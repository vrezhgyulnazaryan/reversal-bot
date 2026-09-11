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
    # skip symbols with less than this much 24h quote volume - avoids the thinnest,
    # most easily-manipulated listings. 0 disables the filter.
    min_quote_volume_usd: float = 0.0


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
    # a price level's resting size must be at least this many times the local median
    # to count as a "wall" (support/resistance). 0 disables wall-based stop refinement.
    wall_multiplier: float = 0.0
    min_wall_usd: float = 5000.0
    wall_scan_depth: int = 500


@dataclass
class RiskConfig:
    risk_per_trade_pct: float
    max_risk_per_trade_pct: float
    stop_atr_mult: float
    take_profit_r_multiple: float
    max_leverage: int
    max_concurrent_positions: int
    daily_loss_limit_pct: float
    # once a position's unrealized profit reaches this many USD, start trailing its
    # stop-loss behind the best price seen, trail_atr_mult ATRs back, instead of
    # waiting for take-profit or the original stop. Re-evaluated every cycle and only
    # ever tightened, never loosened. 0 (default) disables this.
    profit_lock_trigger_usd: float = 0.0
    trail_atr_mult: float = 1.5
    # trailing distance never exceeds this % of price, even if trail_atr_mult * ATR
    # would be wider - caps how much profit a sudden volatility spike can give back
    # on an explosively volatile symbol. 0 disables the cap.
    trail_max_pct: float = 0.0
    # once profit reaches trail_tighten_at_multiple x profit_lock_trigger_usd, the
    # trailing multiplier is halved (tighter buffer) to lock in more as gains grow -
    # let the trade breathe early, squeeze harder once it's deep in profit
    trail_tighten_at_multiple: float = 2.0
    # every trade uses at least this leverage (never below it, still capped at max_leverage)
    min_leverage: int = 1
    # stop-loss is never placed further than this % away from entry, even if the
    # ATR-based distance would be wider (0 disables the cap)
    max_stop_pct: float = 0.0
    # this % of total equity is always kept free (unused as margin) so a strong new
    # signal always has room to enter - leverage is increased toward max_leverage first
    # to make room before the position size is ever reduced
    margin_reserve_pct: float = 15.0
    # skip a new entry if its recent price-return correlation with any already-open
    # position is at or above this (avoids stacking the same risk under different
    # tickers). 0 disables the check.
    max_correlation_with_open: float = 0.0
    # skip a new entry if the symbol's funding rate is this costly against the
    # intended side (positive funding costs longs, negative funding costs shorts).
    # 0 disables the check.
    max_adverse_funding_rate: float = 0.0

    def __post_init__(self):
        if self.risk_per_trade_pct > self.max_risk_per_trade_pct:
            raise ValueError(
                f"risk_per_trade_pct ({self.risk_per_trade_pct}) exceeds "
                f"max_risk_per_trade_pct ({self.max_risk_per_trade_pct}) - refusing to start"
            )
        if self.min_leverage > self.max_leverage:
            raise ValueError(
                f"min_leverage ({self.min_leverage}) exceeds max_leverage ({self.max_leverage}) - refusing to start"
            )
        if self.profit_lock_trigger_usd > 0 and self.trail_atr_mult <= 0:
            raise ValueError("trail_atr_mult must be positive when profit_lock_trigger_usd is set")


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
