import itertools
import json
from bot.config import Config
from bot.exchange import build_exchange
from bot.data import fetch_ohlcv_df
from bot.backtest import run_backtest

cfg = Config.load("config.yaml")
exchange = build_exchange(cfg)

SYMBOLS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "BNB/USDT:USDT", "XRP/USDT:USDT", "DOGE/USDT:USDT"]
TIMEFRAMES = ["5m", "15m"]

data_cache = {}
for tf in TIMEFRAMES:
    for sym in SYMBOLS:
        print(f"fetching {sym} {tf} ...")
        data_cache[(tf, sym)] = fetch_ohlcv_df(exchange, sym, tf, 2500)

rsi_pairs = [(70, 30), (75, 25), (80, 20)]
stop_mults = [1.0, 1.5, 2.0]
tp_mults = [1.0, 1.5, 2.0]

results = []
for tf, (ob, os_), stop_m, tp_m in itertools.product(TIMEFRAMES, rsi_pairs, stop_mults, tp_mults):
    cfg.timeframe = tf
    cfg.signal.rsi_overbought = ob
    cfg.signal.rsi_oversold = os_
    cfg.risk.stop_atr_mult = stop_m
    cfg.risk.take_profit_r_multiple = tp_m

    symbol_dfs = {sym: data_cache[(tf, sym)] for sym in SYMBOLS}
    res = run_backtest(symbol_dfs, cfg, starting_equity=110.0)
    summ = res.summary()
    summ.update({"timeframe": tf, "rsi_ob": ob, "rsi_os": os_, "stop_atr_mult": stop_m, "tp_r_mult": tp_m})
    results.append(summ)
    print(summ)

results = [r for r in results if r.get("trades", 0) >= 15]
results.sort(key=lambda r: (r["profit_factor"], -r["max_drawdown_pct"]), reverse=True)

print("\n=== TOP 10 (min 15 trades) ===")
for r in results[:10]:
    print(json.dumps(r))
