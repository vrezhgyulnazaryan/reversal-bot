# reversal_bot

Fade-the-extreme reversal bot for Binance USDT-M futures. Scans symbols that moved a
lot, checks RSI/Bollinger/ATR for overextension, confirms with live order-book
imbalance, and trades the reversion back to the mean with a hard stop-loss and
take-profit placed on the exchange itself.

## Honest status

Initial backtest on real historical data (BTC/ETH/SOL, 5m candles, ~7 days, starting
config): 51 trades, 49% win rate, profit factor 1.05, +3% return, 11.7% max drawdown.
Roughly break-even before real-world slippage and funding costs.

Ran a parameter sweep (`sweep.py`) after that: 54 combinations of RSI thresholds,
stop-loss width, and take-profit width, across 6 symbols and 2 timeframes (5m/15m,
~8 days of data each). Findings:

- **15m timeframe was uniformly unprofitable** across every parameter combination tested.
- **Tighter RSI filters (75/25, 80/20) were mostly unprofitable** and cut trade count too
  low to mean much (as few as 7-19 trades).
- Only the original loose 70/30 RSI filter on 5m candles showed consistent positive
  results, in the profit-factor 1.1-1.4 range depending on stop/target width.
- The single best row in the sweep (+34.86% return, PF 1.37) is **not** trustworthy on
  its own - it's the best of 54 combinations tested on the *same* ~8-day window, which
  is a classic overfitting trap (test enough variations and one will look great by luck
  alone). The current config instead uses the more moderate, neighborhood-consistent
  settings (`stop_atr_mult: 1.5`, `take_profit_r_multiple: 1.5`, PF ~1.14, +15.9%) since
  nearby parameter values gave similar results rather than one lucky spike.

Treat all of this as a working framework to iterate on, not a finished edge. Order-book
confirmation isn't available in backtests (no historical L2 data), so live trading is
more selective than what the backtest shows - it should filter out some losing trades,
not add more. Before trusting this with real money, forward-test it on testnet for at
least 1-2 weeks of live market conditions the sweep never saw, not just historical
replay of the same week.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in API keys (see below)
```

Get **testnet** API keys at https://testnet.binancefuture.com (fake funds, real
exchange mechanics) - this is where you test before risking real money.

If/when you use real keys: create them with **trading permission only, withdrawal
permission OFF**. Never give a bot withdrawal rights.

## Backtest (no API key needed for historical data)

```bash
python run_backtest.py --symbols "BTC/USDT:USDT" "ETH/USDT:USDT" --bars 2000 --equity 110
```

## Run on testnet (config.yaml has `testnet: true` by default)

```bash
python run_live.py
```

## Going live (real money) - deliberately not the default

`config.yaml` must have `testnet: false` AND you must pass
`--i-understand-the-risk`, or it refuses to start:

```bash
python run_live.py --i-understand-the-risk
```

## Risk controls already built in (edit in config.yaml, don't remove)

- `risk_per_trade_pct` capped by `max_risk_per_trade_pct` - the bot raises an error at
  startup if you set the former above the latter.
- `min_leverage` / `max_leverage` bound the leverage used per trade. Note leverage on
  its own doesn't change dollar risk when a stop-loss is in place - it only changes how
  much margin a position uses and how close liquidation sits. Risk per trade is still
  governed by `risk_per_trade_pct` and the stop-loss distance.
- `max_stop_pct` caps how far the ATR-based stop-loss can sit from entry, tightening it
  on symbols volatile enough that the raw ATR distance would exceed it.
- `profit_lock_trigger_usd` / `trail_atr_mult` trail the stop `trail_atr_mult` ATRs
  behind price once profit crosses the trigger - re-evaluated and only tightened every
  cycle, so the buffer scales with each symbol's volatility instead of a fixed amount.
- `daily_loss_limit_pct` halts new entries for the day once hit.
- `max_concurrent_positions` caps how many trades are open at once.
- Stop-loss and take-profit are placed as real `STOP_MARKET` / `TAKE_PROFIT_MARKET`
  reduce-only orders on the exchange immediately after entry, so a position stays
  protected even if the bot process dies.

## What this will not do

It will not turn $110 into meaningful money in a week reliably. Nothing can promise
that - anyone claiming otherwise is selling something. Use this to learn, iterate on
the strategy with more backtesting across more symbols/timeframes, and only risk what
you can lose.
