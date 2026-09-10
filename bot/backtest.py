from dataclasses import dataclass, field
from typing import List, Dict
import pandas as pd

from .config import Config
from . import indicators as ind
from .strategy import compute_signal
from .risk import position_size, DailyLossGuard


@dataclass
class Trade:
    symbol: str
    side: str
    entry_time: pd.Timestamp
    entry: float
    stop: float
    take_profit: float
    qty: float = 0.0
    exit_time: pd.Timestamp = None
    exit_price: float = None
    pnl: float = 0.0
    exit_reason: str = ""


@dataclass
class BacktestResult:
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    starting_equity: float = 0.0
    ending_equity: float = 0.0

    def summary(self) -> Dict:
        n = len(self.trades)
        if n == 0:
            return {"trades": 0}
        wins = [t for t in self.trades if t.pnl > 0]
        losses = [t for t in self.trades if t.pnl <= 0]
        gross_profit = sum(t.pnl for t in wins)
        gross_loss = -sum(t.pnl for t in losses)
        peak = self.starting_equity
        max_dd = 0.0
        for e in self.equity_curve:
            peak = max(peak, e)
            dd = (peak - e) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
        return {
            "trades": n,
            "win_rate_pct": round(len(wins) / n * 100, 2),
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf"),
            "total_return_pct": round((self.ending_equity - self.starting_equity) / self.starting_equity * 100, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "starting_equity": round(self.starting_equity, 2),
            "ending_equity": round(self.ending_equity, 2),
        }


def run_backtest(symbol_dfs: Dict[str, pd.DataFrame], cfg: Config, starting_equity: float, taker_fee_pct: float = 0.04) -> BacktestResult:
    """NOTE: order-book imbalance confirmation is NOT available on historical data,
    so backtest signals rely on price/RSI/Bollinger/ATR only. Real trading additionally
    requires order-book confirmation, so live results should be more selective (fewer,
    hopefully cleaner trades) than this backtest - never the other way around.
    """
    equity = starting_equity
    guard = DailyLossGuard(starting_equity, cfg.risk.daily_loss_limit_pct)
    open_trades: Dict[str, Trade] = {}
    result = BacktestResult(starting_equity=starting_equity)

    warmup = max(cfg.signal.bb_period, cfg.signal.rsi_period, cfg.signal.atr_period) + 2

    all_timestamps = sorted(set(ts for df in symbol_dfs.values() for ts in df["ts"]))

    for ts in all_timestamps:
        for symbol, trade in list(open_trades.items()):
            df = symbol_dfs[symbol]
            row = df[df["ts"] == ts]
            if row.empty:
                continue
            bar = row.iloc[0]
            hit_stop = bar["low"] <= trade.stop if trade.side == "long" else bar["high"] >= trade.stop
            hit_tp = bar["high"] >= trade.take_profit if trade.side == "long" else bar["low"] <= trade.take_profit
            if hit_stop or hit_tp:
                exit_price = trade.stop if hit_stop else trade.take_profit
                direction = 1 if trade.side == "long" else -1
                qty = trade.qty
                gross_pnl = direction * (exit_price - trade.entry) * qty
                fees = (trade.entry + exit_price) * qty * (taker_fee_pct / 100)
                pnl = gross_pnl - fees
                trade.exit_time, trade.exit_price = ts, exit_price
                trade.pnl = pnl
                trade.exit_reason = "stop" if hit_stop else "take_profit"
                equity += pnl
                guard.record_trade_pnl(pnl)
                result.trades.append(trade)
                del open_trades[symbol]

        if not guard.can_trade():
            result.equity_curve.append(equity)
            continue

        if len(open_trades) < cfg.risk.max_concurrent_positions:
            for symbol, df in symbol_dfs.items():
                if symbol in open_trades:
                    continue
                window = df[df["ts"] <= ts]
                if len(window) < warmup:
                    continue
                sig = compute_signal(
                    window, cfg.signal, cfg.risk.stop_atr_mult, cfg.risk.take_profit_r_multiple, orderbook_imbalance=None
                )
                if sig is None:
                    continue
                sizing = position_size(equity, sig.entry, sig.stop, cfg.risk)
                trade = Trade(symbol, sig.side, ts, sig.entry, sig.stop, sig.take_profit)
                trade.qty = sizing.qty
                open_trades[symbol] = trade
                if len(open_trades) >= cfg.risk.max_concurrent_positions:
                    break

        result.equity_curve.append(equity)

    result.ending_equity = equity
    return result
