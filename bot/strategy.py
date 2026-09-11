from dataclasses import dataclass
from typing import Optional
import pandas as pd

from .config import SignalConfig
from . import indicators as ind


@dataclass
class Signal:
    side: str  # "long" or "short"
    entry: float
    stop: float
    take_profit: float
    reason: str
    strategy: str = "mean_reversion"


def _levels(
    entry: float, side: str, atr_val: float, mean_ref: float, cfg: SignalConfig,
    stop_atr_mult: float, tp_r_mult: float, max_stop_pct: float,
):
    max_risk = entry * (max_stop_pct / 100) if max_stop_pct > 0 else float("inf")
    if side == "long":
        stop = entry - stop_atr_mult * atr_val
        risk = min(entry - stop, max_risk)
        stop = entry - risk
        take_profit = max(mean_ref, entry + tp_r_mult * risk)
    else:
        stop = entry + stop_atr_mult * atr_val
        risk = min(stop - entry, max_risk)
        stop = entry + risk
        take_profit = min(mean_ref, entry - tp_r_mult * risk)
    return stop, take_profit


def compute_signal(
    df: pd.DataFrame,
    cfg: SignalConfig,
    stop_atr_mult: float,
    tp_r_mult: float,
    orderbook_imbalance: Optional[float] = None,
    max_stop_pct: float = 0.0,
) -> Optional[Signal]:
    """Fade-the-extreme reversal signal.

    Long: price poked below lower Bollinger band, RSI oversold, then the latest
    candle closes back above the lower band (rejection) -> bet on reversion to the mean.
    Short: mirror image on the upside.

    orderbook_imbalance, when provided (live mode only - not available historically),
    must confirm the direction: >min_ratio for long, <1/min_ratio for short.
    """
    if len(df) < max(cfg.bb_period, cfg.rsi_period, cfg.atr_period) + 2:
        return None

    close = df["close"]
    r = ind.rsi(close, cfg.rsi_period)
    mid, upper, lower = ind.bollinger_bands(close, cfg.bb_period, cfg.bb_std)
    a = ind.atr(df["high"], df["low"], close, cfg.atr_period)

    prev = -2
    last = -1

    poked_below = df["low"].iloc[prev] < lower.iloc[prev]
    rejected_up = close.iloc[last] > lower.iloc[last] and r.iloc[prev] < cfg.rsi_oversold
    if poked_below and rejected_up:
        if orderbook_imbalance is not None and orderbook_imbalance < cfg.min_imbalance_ratio:
            pass
        else:
            entry = close.iloc[last]
            stop, tp = _levels(entry, "long", a.iloc[last], mid.iloc[last], cfg, stop_atr_mult, tp_r_mult, max_stop_pct)
            if stop < entry:
                return Signal("long", entry, stop, tp, "oversold rejection off lower band")

    poked_above = df["high"].iloc[prev] > upper.iloc[prev]
    rejected_down = close.iloc[last] < upper.iloc[last] and r.iloc[prev] > cfg.rsi_overbought
    if poked_above and rejected_down:
        max_imb = 1 / cfg.min_imbalance_ratio
        if orderbook_imbalance is not None and orderbook_imbalance > max_imb:
            pass
        else:
            entry = close.iloc[last]
            stop, tp = _levels(entry, "short", a.iloc[last], mid.iloc[last], cfg, stop_atr_mult, tp_r_mult, max_stop_pct)
            if stop > entry:
                return Signal("short", entry, stop, tp, "overbought rejection off upper band")

    return None
