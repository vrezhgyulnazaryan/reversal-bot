import argparse
import json
from bot.config import Config
from bot.exchange import build_exchange
from bot.data import fetch_ohlcv_df
from bot.backtest import run_backtest


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--symbols", nargs="+", required=True, help="e.g. BTC/USDT:USDT ETH/USDT:USDT")
    p.add_argument("--bars", type=int, default=1500, help="historical bars per symbol")
    p.add_argument("--equity", type=float, default=110.0)
    args = p.parse_args()

    cfg = Config.load(args.config)
    exchange = build_exchange(cfg)

    symbol_dfs = {}
    for symbol in args.symbols:
        print(f"fetching {symbol} ...")
        symbol_dfs[symbol] = fetch_ohlcv_df(exchange, symbol, cfg.timeframe, args.bars)

    result = run_backtest(symbol_dfs, cfg, starting_equity=args.equity)
    print(json.dumps(result.summary(), indent=2))

    for t in result.trades[-20:]:
        print(f"{t.entry_time} {t.symbol} {t.side} entry={t.entry:.6f} exit={t.exit_price:.6f} "
              f"pnl={t.pnl:.2f} ({t.exit_reason})")


if __name__ == "__main__":
    main()
