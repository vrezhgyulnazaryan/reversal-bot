import os
import yaml
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class ScanConfig:
    quote_currency: str
    top_n_movers: int
    move_window_minutes: int
    min_abs_move_pct: float


@dataclass
class SignalConfig:
    rsi_period: int
    rsi_overbought: float
    rsi_oversold: float
    bb_period: int
    bb_std: float
    atr_period: int
    orderbook_depth_levels: int
    orderbook_price_range_pct: float
    min_imbalance_ratio: float


@dataclass
class RiskConfig:
    risk_per_trade_pct: float
    max_risk_per_trade_pct: float
    stop_atr_mult: float
    take_profit_r_multiple: float
    max_leverage: int
    max_concurrent_positions: int
    daily_loss_limit_pct: float
    # once a position's unrealized profit reaches this many USD, move its stop-loss to
    # lock in profit_lock_amount_usd instead of waiting for take-profit or the original
    # stop. 0 (default) disables this.
    profit_lock_trigger_usd: float = 0.0
    profit_lock_amount_usd: float = 0.0

    def __post_init__(self):
        if self.risk_per_trade_pct > self.max_risk_per_trade_pct:
            raise ValueError(
                f"risk_per_trade_pct ({self.risk_per_trade_pct}) exceeds "
                f"max_risk_per_trade_pct ({self.max_risk_per_trade_pct}) - refusing to start"
            )
        if self.profit_lock_trigger_usd > 0 and self.profit_lock_amount_usd >= self.profit_lock_trigger_usd:
            raise ValueError(
                f"profit_lock_amount_usd ({self.profit_lock_amount_usd}) must be less than "
                f"profit_lock_trigger_usd ({self.profit_lock_trigger_usd}) - refusing to start"
            )


@dataclass
class ExecutionConfig:
    order_type: str
    poll_interval_sec: int


@dataclass
class Config:
    exchange: str
    testnet: bool
    timeframe: str
    lookback_bars: int
    scan: ScanConfig
    signal: SignalConfig
    risk: RiskConfig
    execution: ExecutionConfig
    api_key: str = ""
    api_secret: str = ""

    @staticmethod
    def load(path: str = "config.yaml") -> "Config":
        with open(path, "r") as f:
            raw = yaml.safe_load(f)
        return Config(
            exchange=raw["exchange"],
            testnet=raw["testnet"],
            timeframe=raw["timeframe"],
            lookback_bars=raw["lookback_bars"],
            scan=ScanConfig(**raw["scan"]),
            signal=SignalConfig(**raw["signal"]),
            risk=RiskConfig(**raw["risk"]),
            execution=ExecutionConfig(**raw["execution"]),
            api_key=os.getenv("BINANCE_API_KEY", ""),
            api_secret=os.getenv("BINANCE_API_SECRET", ""),
        )
