import itertools
import json
from bot.config import Config
from bot.exchange import build_exchange
from bot.data import fetch_ohlcv_df
from bot.backtest import run_backtest

cfg = Config.load("config.yaml")
exchange = build_exchange(cfg)

# majors for stability + the actual alt/meme coins this bot has been live-trading,
# so the sweep reflects the kind of symbols it actually picks, not just BTC/ETH
SYMBOLS = [
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
    "KOMA/USDT:USDT", "BULLA/USDT:USDT", "PUFFER/USDT:USDT", "VTHO/USDT:USDT",
    "RAYSOL/USDT:USDT", "AKE/USDT:USDT", "ZEC/USDT:USDT", "TRIA/USDT:USDT",
    "BEAT/USDT:USDT", "USELESS/USDT:USDT", "VELVET/USDT:USDT", "IDOL/USDT:USDT",
    "RVN/USDT:USDT",
]
TIMEFRAMES = ["5m", "15m"]

data_cache = {}
for tf in TIMEFRAMES:
    for sym in SYMBOLS:
        print(f"fetching {sym} {tf} ...")
        try:
            data_cache[(tf, sym)] = fetch_ohlcv_df(exchange, sym, tf, 2500)
        except Exception as e:
            print(f"  skipped ({e})")

rsi_pairs = [(70, 30), (75, 25), (80, 20)]
stop_mults = [1.0, 1.5, 2.0]
tp_mults = [1.0, 1.5, 2.0]
max_stop_pcts = [5.0, 10.0, 0.0]  # 0 = uncapped

results = []
for tf, (ob, os_), stop_m, tp_m, cap in itertools.product(
    TIMEFRAMES, rsi_pairs, stop_mults, tp_mults, max_stop_pcts
):
    cfg.timeframe = tf
    cfg.signal.rsi_overbought = ob
    cfg.signal.rsi_oversold = os_
    cfg.risk.stop_atr_mult = stop_m
    cfg.risk.take_profit_r_multiple = tp_m
    cfg.risk.max_stop_pct = cap

    symbol_dfs = {sym: data_cache[(tf, sym)] for sym in SYMBOLS if (tf, sym) in data_cache}
    res = run_backtest(symbol_dfs, cfg, starting_equity=110.0)
    summ = res.summary()
    summ.update({
        "timeframe": tf, "rsi_ob": ob, "rsi_os": os_,
        "stop_atr_mult": stop_m, "tp_r_mult": tp_m, "max_stop_pct": cap,
    })
    results.append(summ)

results = [r for r in results if r.get("trades", 0) >= 20]
results.sort(key=lambda r: (r["profit_factor"], -r["max_drawdown_pct"]), reverse=True)

print(f"\n=== ran {len(results)} combos with >=20 trades ===")
print("\n=== TOP 15 (min 20 trades) ===")
for r in results[:15]:
    print(json.dumps(r))

print("\n=== current config.yaml values for comparison ===")
current = Config.load("config.yaml")
print(f"rsi=({current.signal.rsi_overbought},{current.signal.rsi_oversold}) "
      f"stop_atr_mult={current.risk.stop_atr_mult} tp_r_mult={current.risk.take_profit_r_multiple} "
      f"max_stop_pct={current.risk.max_stop_pct} timeframe={current.timeframe}")
