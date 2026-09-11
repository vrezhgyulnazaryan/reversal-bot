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

        self._sweep_orphaned_algo_orders(positions)

        self._check_profit_locks(positions)

        if len(self.open_symbols) >= self.cfg.risk.max_concurrent_positions:
            print(f"[{now}] max concurrent positions reached, skipping scan", flush=True)
            self._write_status(now, equity, [], "max concurrent positions reached")
            return

        movers = scan_movers(
            self.exchange,
            self.cfg.scan.quote_currency,
            self.cfg.scan.top_n_movers,
            self.cfg.scan.min_abs_move_pct,
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

    def _check_profit_locks(self, positions: list):
        trigger = self.cfg.risk.profit_lock_trigger_usd
        lock_amount = self.cfg.risk.profit_lock_amount_usd
        if trigger <= 0:
            return

        positions_by_symbol = {p["symbol"]: p for p in positions}
        for symbol, meta in self.open_trades.items():
            if meta.get("profit_locked") or "stop_algo_id" not in meta:
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
                print(f"[warn] {symbol} markPrice is {mark_price}, skipping profit-lock check "
                      f"this cycle (looks like a stale/broken price feed)", flush=True)
                continue

            upnl = float(pos.get("unrealizedPnl") or 0)
            if upnl < trigger:
                continue

            qty = meta["qty"]
            entry = meta["entry"]
            if meta["side"] == "long":
                new_stop = entry + lock_amount / qty
            else:
                new_stop = entry - lock_amount / qty

            try:
                # place the new stop BEFORE cancelling the old one - if this fails, the
                # position is still protected by the original stop instead of sitting
                # naked until the next cycle
                opposite = "sell" if meta["side"] == "long" else "buy"
                new_stop_id = self._place_reduce_only_stop(symbol, opposite, qty, "STOP_MARKET", new_stop)
            except Exception as e:
                print(f"[error] profit lock failed for {symbol}, original stop left in place: {e}", flush=True)
                continue

            try:
                self.exchange.cancel_order(meta["stop_algo_id"], symbol, params={"trigger": True})
            except Exception as e:
                print(f"[warn] could not cancel old stop for {symbol} after placing new one "
                      f"(position now has two stops, which is safe but redundant): {e}", flush=True)

            meta["stop_algo_id"] = new_stop_id
            meta["profit_locked"] = True
            print(f"[profit-lock] {symbol} uPnL={upnl:.2f} -> moved stop to {new_stop:.6f} "
                  f"(locking ~{lock_amount} USD)", flush=True)
            _log_row({
                "time": datetime.now(timezone.utc).isoformat(),
                "event": "profit_lock",
                "symbol": symbol,
                "side": meta["side"],
                "stop": new_stop,
            })

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
            "profit_locked": False,
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
