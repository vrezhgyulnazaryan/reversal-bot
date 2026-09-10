import ccxt
from .config import Config

# Binance retired the old GitHub-login testnet.binancefuture.com UI in favor of
# "Demo Trading" inside a regular Binance.com account (API host demo-fapi.binance.com).
# ccxt's set_sandbox_mode() still points at the old, deprecated host - enable_demo_trading()
# is the current, correct way to point the client at Demo Trading.


def build_exchange(cfg: Config) -> ccxt.Exchange:
    klass = getattr(ccxt, cfg.exchange)
    exchange = klass(
        {
            "apiKey": cfg.api_key,
            "secret": cfg.api_secret,
            "enableRateLimit": True,
            "options": {
                "defaultType": "future",
                "fetchOpenOrders": {"warnWithoutSymbol": False},
            },
        }
    )
    if cfg.testnet:
        exchange.enable_demo_trading(True)
    exchange.load_markets()
    return exchange
