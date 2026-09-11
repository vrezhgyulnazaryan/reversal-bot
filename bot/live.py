import csv
import json
import os
import time
from datetime import datetime, timezone

from .config import Config
from .exchange import build_exchange
from .data import fetch_ohlcv_df, scan_movers, fetch_orderbook_imbalance
from .strategy import compute_signal
from .risk import position_size, DailyLossGuard
from . import indicators as ind

LOG_PATH = "trades_log.csv"
STATUS_PATH = "status.json"
LOG_FIELDS = [
    "time", "event", "symbol", "side", "entry", "stop", "take_profit",
    "qty", "leverage", "reason", "order_id", "exit_price", "pnl",
]


def _log_row(row: dict):
    exists = os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in LOG_FIELDS})


class LiveTrader:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.exchange = build_exchange(cfg)
        balance = self.exchange.fetch_balance()
        equity = balance["total"].get("USDT", 0.0)
        if equity <= 0:
            raise RuntimeError("USDT futures balance is 0 - fund the account (or the testnet faucet) before running")
        self.guard = DailyLossGuard(equity, cfg.risk.daily_loss_limit_pct)
        self.open_symbols = set()
        self.open_trades = {}  # symbol -> dict(entry metadata) for trades this bot opened
        print(f"[init] mode={'TESTNET' if cfg.testnet else 'LIVE'} equity={equity:.2f} USDT", flush=True)

    def _equity(self) -> float:
        return self.exchange.fetch_balance()["total"].get("USDT", 0.0)

    def _available_margin(self) -> float:
        return self.exchange.fetch_balance()["free"].get("USDT", 0.0)

    def _open_positions(self):
        positions = self.exchange.fetch_positions()
        return [p for p in positions if float(p.get("contracts") or 0) != 0]

    def _handle_closed_positions(self, currently_open: set):
        closed = set(self.open_symbols) - currently_open
        for symbol in closed:
            meta = self.open_trades.pop(symbol, None)
            pnl = self._realized_pnl_since(symbol, meta["entry_time"] if meta else None)
            self.guard.record_trade_pnl(pnl)
            print(f"[closed] {symbol} realized_pnl={pnl:.2f} USDT", flush=True)
            _log_row({
                "time": datetime.now(timezone.utc).isoformat(),
                "event": "exit",
                "symbol": symbol,
                "pnl": round(pnl, 4),
                **({"side": meta["side"], "entry": meta["entry"]} if meta else {}),
            })

    def _adopt_untracked_positions(self, positions: list):
        """A position already open when this process started (or that survived a
        Render redeploy) isn't in self.open_trades - trailing-stop/profit-lock would
        silently never engage for it, since that logic only looks at tracked trades.
        Reconstruct enough metadata from the exchange to adopt it; if it genuinely has
        no stop-loss order at all, place a fallback one immediately instead of leaving
        it naked."""
        for p in positions:
            symbol = p["symbol"]
            if symbol in self.open_trades:
                continue
            side = p.get("side")
            qty = abs(float(p.get("contracts") or 0))
            entry = float(p.get("entryPrice") or 0)
            if not side or qty <= 0 or entry <= 0:
                continue

            try:
                market_id = self.exchange.market(symbol)["id"]
                algo_orders = [
                    o for o in self.exchange.fapiPrivateGetOpenAlgoOrders() if o.get("symbol") == market_id
                ]
            except Exception as e:
                print(f"[warn] could not fetch algo orders while adopting {symbol}: {e}", flush=True)
                continue

            stop_order = next((o for o in algo_orders if o.get("orderType") == "STOP_MARKET"), None)

            if stop_order is None:
                mark = float(p.get("markPrice") or 0) or entry
                pct = self.cfg.risk.max_stop_pct or 5.0
                fallback_stop = mark * (1 - pct / 100) if side == "long" else mark * (1 + pct / 100)
                try:
                    opposite = "sell" if side == "long" else "buy"
                    stop_algo_id = self._place_reduce_only_stop(symbol, opposite, qty, "STOP_MARKET", fallback_stop)
                    current_stop = fallback_stop
                    print(f"[adopt] {symbol} had NO stop-loss - placed fallback at {fallback_stop:.6f}", flush=True)
                except Exception as e:
                    print(f"[error] could not place fallback stop for naked position {symbol}: {e}", flush=True)
                    continue
            else:
                stop_algo_id = str(stop_order.get("algoId"))
                current_stop = float(stop_order.get("triggerPrice"))

            self.open_trades[symbol] = {
                "entry_time": None,
                "side": side,
                "entry": entry,
                "qty": qty,
                "stop_algo_id": stop_algo_id,
                "current_stop": current_stop,
                "trailing_active": False,
            }
            print(f"[adopt] now tracking pre-existing position {symbol} side={side} "
                  f"entry={entry} stop={current_stop}", flush=True)

    def _sweep_orphaned_algo_orders(self, positions: list):
        # When a position closes via one bracket order (stop or take-profit) triggering,
        # the *other* one is left sitting open (they aren't a real OCO pair on this
        # exchange), and a process restart loses track of open_trades entirely. Left
        # alone these accumulate and eventually hit Binance's per-symbol
        # MAX_NUM_ALGO_ORDERS cap, which breaks bracket-order placement on that symbol -
        # so every cycle, cancel any algo order whose symbol has no open position at all.
        try:
            open_market_ids = {self.exchange.market(p["symbol"])["id"] for p in positions}
            for o in self.exchange.fapiPrivateGetOpenAlgoOrders():
                if o.get("symbol") not in open_market_ids:
                    self.exchange.fapiPrivateDeleteAlgoOrder(
                        {"symbol": o.get("symbol"), "algoId": o.get("algoId")}
                    )
                    print(f"[cleanup] cancelled orphaned algo order {o.get('symbol')} "
                          f"{o.get('orderType')}", flush=True)
        except Exception as e:
            print(f"[warn] orphaned algo order sweep failed: {e}", flush=True)

    def _realized_pnl_since(self, symbol: str, entry_time) -> float:
        try:
            trades = self.exchange.fetch_my_trades(symbol, limit=20)
        except Exception as e:
            print(f"[warn] could not fetch trades for {symbol}: {e}", flush=True)
            return 0.0
        total = 0.0
        for t in trades:
            if entry_time is not None and t["timestamp"] is not None and t["timestamp"] < entry_time:
                continue
            total += float(t.get("info", {}).get("realizedPnl", 0) or 0)
        return total

    def _write_status(self, now, equity, movers, note=""):
        try:
            with open(STATUS_PATH, "w") as f:
                json.dump({
                    "time": now,
                    "equity": equity,
                    "open_positions": sorted(self.open_symbols),
                    "movers": movers,
                    "note": note,
                    "guard_can_trade": self.guard.can_trade(),
                }, f)
        except Exception:
            pass

    def step(self):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        if not self.guard.can_trade():
            print(f"[{now}] [guard] daily loss limit reached - no new entries until reset", flush=True)
            self._write_status(now, self._equity(), [], "daily loss limit reached")
            return

        positions = self._open_positions()
        currently_open = {p["symbol"] for p in positions}
        self._handle_closed_positions(currently_open)
        self.open_symbols = currently_open
        equity = self._equity()

        print(f"[{now}] equity={equity:.2f} USDT open_positions={sorted(self.open_symbols) or 'none'}", flush=True)

        self._adopt_untracked_positions(positions)
        self._sweep_orphaned_algo_orders(positions)

        self._check_trailing_stops(positions)

        if len(self.open_symbols) >= self.cfg.risk.max_concurrent_positions:
            print(f"[{now}] max concurrent positions reached, skipping scan", flush=True)
            self._write_status(now, equity, [], "max concurrent positions reached")
            return

        movers = scan_movers(
            self.exchange,
            self.cfg.scan.quote_currency,
            self.cfg.scan.top_n_movers,
            self.cfg.scan.min_abs_move_pct,
            self.cfg.scan.min_quote_volume_usd,
        )
        print(f"[{now}] scanned {len(movers)} movers: "
              f"{[(s, round(p, 2)) for s, p in movers[:10]]}", flush=True)
        self._write_status(now, equity, movers)

        for symbol, pct in movers:
            if symbol in self.open_symbols:
                continue
            if len(self.open_symbols) >= self.cfg.risk.max_concurrent_positions:
                break
            try:
                self._evaluate_symbol(symbol)
            except Exception as e:
                print(f"[error] {symbol}: {e}", flush=True)

    def _place_reduce_only_stop(self, symbol: str, side: str, qty: float, order_type: str, trigger_price: float) -> str:
        # Binance migrated conditional orders (STOP_MARKET/TAKE_PROFIT_MARKET/etc) off
        # POST /fapi/v1/order onto a dedicated Algo Order endpoint (2025-12-09) - the
        # old endpoint now rejects these types with error -4120.
        market = self.exchange.market(symbol)
        response = self.exchange.fapiPrivatePostAlgoOrder({
            "algoType": "CONDITIONAL",
            "symbol": market["id"],
            "side": side.upper(),
            "type": order_type,
            "triggerPrice": self.exchange.price_to_precision(symbol, trigger_price),
            "quantity": self.exchange.amount_to_precision(symbol, qty),
            "reduceOnly": "true",
        })
        return str(response.get("algoId"))

    def _check_trailing_stops(self, positions: list):
        """Once a position's profit reaches profit_lock_trigger_usd, trail its stop
        trail_atr_mult ATRs behind the current price every cycle - tightened only,
        never loosened. ATR-based instead of a fixed dollar amount so the buffer scales
        with each symbol's actual volatility (a fixed-dollar lock sat too close to price
        on a volatile symbol and got stopped out by ordinary noise before the move
        continued toward the original take-profit)."""
        trigger = self.cfg.risk.profit_lock_trigger_usd
        base_trail_mult = self.cfg.risk.trail_atr_mult
        if trigger <= 0:
            return

        positions_by_symbol = {p["symbol"]: p for p in positions}
        for symbol, meta in self.open_trades.items():
            if "stop_algo_id" not in meta:
                continue
            pos = positions_by_symbol.get(symbol)
            if pos is None:
                continue

            # Demo Trading has occasionally returned markPrice=0/notional=0 for a
            # genuinely open position (a stale/broken feed for that symbol) - trusting
            # unrealizedPnl computed off that would be trusting garbage, so skip this
            # cycle rather than act on it.
            mark_price = float(pos.get("markPrice") or 0)
            if mark_price <= 0:
                print(f"[warn] {symbol} markPrice is {mark_price}, skipping trailing-stop check "
                      f"this cycle (looks like a stale/broken price feed)", flush=True)
                continue

            upnl = float(pos.get("unrealizedPnl") or 0)
            if not meta.get("trailing_active") and upnl < trigger:
                continue

            try:
                df = fetch_ohlcv_df(self.exchange, symbol, self.cfg.timeframe, self.cfg.lookback_bars)
                atr_val = ind.atr(df["high"], df["low"], df["close"], self.cfg.signal.atr_period).iloc[-1]
            except Exception as e:
                print(f"[warn] could not compute ATR for trailing stop on {symbol}: {e}", flush=True)
                continue

            qty = meta["qty"]

            # let it breathe early, squeeze harder once deep in profit
            trail_mult = base_trail_mult
            if self.cfg.risk.trail_tighten_at_multiple > 0 and upnl >= trigger * self.cfg.risk.trail_tighten_at_multiple:
                trail_mult = base_trail_mult / 2

            distance = trail_mult * atr_val
            if self.cfg.risk.trail_max_pct > 0:
                # cap the buffer as a % of price - on an explosively volatile symbol,
                # trail_mult * ATR alone can stay wide enough to give back most of a
                # move even after "tightening" once
                distance = min(distance, mark_price * (self.cfg.risk.trail_max_pct / 100))

            if meta["side"] == "long":
                candidate_stop = mark_price - distance
                improved = candidate_stop > meta["current_stop"]
            else:
                candidate_stop = mark_price + distance
                improved = candidate_stop < meta["current_stop"]

            if not improved:
                continue

            try:
                # place the new stop BEFORE cancelling the old one - if this fails, the
                # position is still protected by the original stop instead of sitting
                # naked until the next cycle
                opposite = "sell" if meta["side"] == "long" else "buy"
                new_stop_id = self._place_reduce_only_stop(symbol, opposite, qty, "STOP_MARKET", candidate_stop)
            except Exception as e:
                print(f"[error] trailing stop update failed for {symbol}, original stop left in place: {e}", flush=True)
                continue

            try:
                self.exchange.cancel_order(meta["stop_algo_id"], symbol, params={"trigger": True})
            except Exception as e:
                print(f"[warn] could not cancel old stop for {symbol} after trailing "
                      f"(position now has two stops, which is safe but redundant): {e}", flush=True)

            meta["stop_algo_id"] = new_stop_id
            meta["current_stop"] = candidate_stop
            meta["trailing_active"] = True
            print(f"[trail-stop] {symbol} uPnL={upnl:.2f} -> moved stop to {candidate_stop:.6f} "
                  f"({trail_mult}x ATR behind mark={mark_price:.6f})", flush=True)
            _log_row({
                "time": datetime.now(timezone.utc).isoformat(),
                "event": "trail_stop",
                "symbol": symbol,
                "side": meta["side"],
                "stop": candidate_stop,
            })

    def _too_correlated_with_open(self, symbol: str, df) -> bool:
        """Reject a new entry that would just be stacking the same risk under a
        different ticker - e.g. several similarly-moving meme coins at once defeats
        the point of max_concurrent_positions diversification."""
        threshold = self.cfg.risk.max_correlation_with_open
        if threshold <= 0 or not self.open_symbols:
            return False
        candidate_returns = df["close"].pct_change().dropna()
        for open_symbol in self.open_symbols:
            try:
                open_df = fetch_ohlcv_df(self.exchange, open_symbol, self.cfg.timeframe, len(df))
            except Exception as e:
                print(f"[warn] could not fetch {open_symbol} for correlation check: {e}", flush=True)
                continue
            open_returns = open_df["close"].pct_change().dropna()
            n = min(len(candidate_returns), len(open_returns))
            if n < 10:
                continue
            corr = candidate_returns.tail(n).reset_index(drop=True).corr(
                open_returns.tail(n).reset_index(drop=True)
            )
            if corr is not None and abs(corr) >= threshold:
                print(f"[skip] {symbol} correlation {corr:.2f} with open {open_symbol} "
                      f">= {threshold}, skipping entry", flush=True)
                return True
        return False

    def _funding_rate_blocks_entry(self, symbol: str, side: str) -> bool:
        threshold = self.cfg.risk.max_adverse_funding_rate
        if threshold <= 0:
            return False
        try:
            fr = self.exchange.fetch_funding_rate(symbol).get("fundingRate")
        except Exception as e:
            print(f"[warn] could not fetch funding rate for {symbol}: {e}", flush=True)
            return False
        if fr is None:
            return False
        # positive funding costs longs (they pay shorts); negative funding costs shorts
        if side == "long" and fr > threshold:
            print(f"[skip] {symbol} funding rate {fr:.5f} too costly for long (> {threshold})", flush=True)
            return True
        if side == "short" and fr < -threshold:
            print(f"[skip] {symbol} funding rate {fr:.5f} too costly for short (< -{threshold})", flush=True)
            return True
        return False

    def _evaluate_symbol(self, symbol: str):
        df = fetch_ohlcv_df(self.exchange, symbol, self.cfg.timeframe, self.cfg.lookback_bars)
        imbalance = fetch_orderbook_imbalance(
            self.exchange,
            symbol,
            self.cfg.signal.orderbook_depth_levels,
            self.cfg.signal.orderbook_price_range_pct,
        )
        sig = compute_signal(
            df, self.cfg.signal, self.cfg.risk.stop_atr_mult, self.cfg.risk.take_profit_r_multiple, imbalance,
            self.cfg.risk.max_stop_pct,
        )
        if sig is None:
            return

        if self._too_correlated_with_open(symbol, df):
            return
        if self._funding_rate_blocks_entry(symbol, sig.side):
            return

        equity = self._equity()
        available_margin = self._available_margin()
        sizing = position_size(equity, sig.entry, sig.stop, self.cfg.risk, available_margin)
        if sizing.qty <= 0:
            return

        print(f"[signal] {symbol} {sig.side} entry={sig.entry:.6f} stop={sig.stop:.6f} tp={sig.take_profit:.6f} "
              f"qty={sizing.qty:.6f} lev={sizing.leverage}x reason={sig.reason}", flush=True)

        try:
            self.exchange.set_margin_mode("cross", symbol)
        except Exception:
            pass  # already cross, or exchange doesn't allow changing it mid-position - harmless either way
        self.exchange.set_leverage(sizing.leverage, symbol)
        side = "buy" if sig.side == "long" else "sell"
        opposite = "sell" if sig.side == "long" else "buy"

        entry_order = self.exchange.create_order(symbol, self.cfg.execution.order_type, side, sizing.qty)

        try:
            stop_algo_id = self._place_reduce_only_stop(symbol, opposite, sizing.qty, "STOP_MARKET", sig.stop)
            self._place_reduce_only_stop(symbol, opposite, sizing.qty, "TAKE_PROFIT_MARKET", sig.take_profit)
        except Exception as e:
            # the position is now open with no protective orders - closing it immediately
            # is safer than leaving it unguarded until the next poll cycle
            print(f"[error] bracket order placement failed for {symbol}, closing position: {e}", flush=True)
            self.exchange.create_order(symbol, "market", opposite, sizing.qty, params={"reduceOnly": True})
            return

        self.open_symbols.add(symbol)
        entry_time = entry_order.get("timestamp") or int(time.time() * 1000)
        self.open_trades[symbol] = {
            "entry_time": entry_time,
            "side": sig.side,
            "entry": sig.entry,
            "qty": sizing.qty,
            "stop_algo_id": stop_algo_id,
            "current_stop": sig.stop,
            "trailing_active": False,
        }
        _log_row({
            "time": datetime.now(timezone.utc).isoformat(),
            "event": "entry",
            "symbol": symbol,
            "side": sig.side,
            "entry": sig.entry,
            "stop": sig.stop,
            "take_profit": sig.take_profit,
            "qty": sizing.qty,
            "leverage": sizing.leverage,
            "reason": sig.reason,
            "order_id": entry_order.get("id"),
        })

    def run_forever(self):
        while True:
            self.step()
            time.sleep(self.cfg.execution.poll_interval_sec)
