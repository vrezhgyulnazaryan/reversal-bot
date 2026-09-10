from dataclasses import dataclass
from .config import RiskConfig


@dataclass
class Sizing:
    qty: float
    leverage: int
    notional: float
    risk_amount: float


def position_size(
    equity: float, entry: float, stop: float, cfg: RiskConfig, available_margin: float = None
) -> Sizing:
    """available_margin is the exchange's free/available balance (equity minus margin
    already locked in other open positions). Required if you can have more than one
    concurrent position, otherwise a new trade can be sized against total equity and
    then rejected by the exchange for insufficient margin."""
    if available_margin is None:
        available_margin = equity

    risk_pct = min(cfg.risk_per_trade_pct, cfg.max_risk_per_trade_pct)
    risk_amount = equity * (risk_pct / 100)
    stop_distance = abs(entry - stop)
    if stop_distance <= 0:
        raise ValueError("stop_distance must be positive")

    qty = risk_amount / stop_distance
    notional = qty * entry

    required_leverage = max(1, round(notional / equity))
    leverage = min(required_leverage, cfg.max_leverage)

    max_notional_by_equity = equity * cfg.max_leverage
    # leave a small buffer below the exchange's actual free margin for fees/slippage
    max_notional_by_margin = available_margin * leverage * 0.95
    max_notional = min(max_notional_by_equity, max_notional_by_margin)

    if notional > max_notional:
        qty = max_notional / entry
        notional = qty * entry

    return Sizing(qty=qty, leverage=leverage, notional=notional, risk_amount=risk_amount)


class DailyLossGuard:
    """Halts new entries once realized losses for the day breach the configured limit."""

    def __init__(self, starting_equity: float, daily_loss_limit_pct: float):
        self.starting_equity = starting_equity
        self.limit_pct = daily_loss_limit_pct
        self.realized_pnl = 0.0

    def record_trade_pnl(self, pnl: float) -> None:
        self.realized_pnl += pnl

    def can_trade(self) -> bool:
        loss_pct = -self.realized_pnl / self.starting_equity * 100
        return loss_pct < self.limit_pct

    def reset_for_new_day(self, current_equity: float) -> None:
        self.starting_equity = current_equity
        self.realized_pnl = 0.0
