import csv
import os
from bot.config import Config
from bot.exchange import build_exchange

LOG_PATH = "trades_log.csv"


def main():
    cfg = Config.load("config.yaml")
    exchange = build_exchange(cfg)

    balance = exchange.fetch_balance()
    equity = balance["total"].get("USDT", 0.0)
    print(f"=== Account ({'TESTNET/DEMO' if cfg.testnet else 'LIVE'}) ===")
    print(f"Equity: {equity:.2f} USDT\n")

    positions = [p for p in exchange.fetch_positions() if float(p.get("contracts") or 0) != 0]
    print(f"=== Open positions ({len(positions)}) ===")
    if not positions:
        print("none")
    for p in positions:
        side = p.get("side")
        contracts = p.get("contracts")
        entry = p.get("entryPrice")
        mark = p.get("markPrice")
        upnl = p.get("unrealizedPnl")
        lev = p.get("leverage")
        print(f"{p['symbol']:<15} {side:<6} qty={contracts} entry={entry} mark={mark} "
              f"uPnL={upnl} lev={lev}x")
    print()

    try:
        open_orders = exchange.fetch_open_orders()
    except Exception as e:
        open_orders = []
        print(f"(could not fetch open orders: {e})")

    try:
        # stop-loss/take-profit orders live on a separate "algo order" system since
        # Binance's 2025-12-09 migration - not returned by fetch_open_orders()
        algo_orders = exchange.fapiPrivateGetOpenAlgoOrders()
    except Exception as e:
        algo_orders = []
        print(f"(could not fetch algo orders: {e})")

    print(f"=== Open orders ({len(open_orders)}) ===")
    for o in open_orders:
        print(f"{o['symbol']:<15} {o['type']:<18} {o['side']:<5} qty={o['amount']} "
              f"stop={o.get('stopPrice')}")

    print(f"=== Open stop/take-profit (algo) orders ({len(algo_orders)}) ===")
    for o in algo_orders:
        print(f"{o.get('symbol'):<15} {o.get('orderType', ''):<18} {o.get('side'):<5} "
              f"qty={o.get('quantity')} trigger={o.get('triggerPrice')}")
    print()

    if not os.path.exists(LOG_PATH):
        print("No trades logged yet (trades_log.csv doesn't exist).")
        return

    with open(LOG_PATH, newline="") as f:
        rows = list(csv.DictReader(f))

    entries = [r for r in rows if r["event"] == "entry"]
    exits = [r for r in rows if r["event"] == "exit"]
    pnls = [float(r["pnl"]) for r in exits if r.get("pnl")]

    print(f"=== Bot trade log: {len(entries)} entries, {len(exits)} closed ===")
    if pnls:
        wins = [p for p in pnls if p > 0]
        print(f"Closed trades win rate: {len(wins)}/{len(pnls)} ({len(wins)/len(pnls)*100:.1f}%)")
        print(f"Total realized PnL (bot-opened trades): {sum(pnls):.2f} USDT")
    print()

    print("=== Last 10 log rows ===")
    for r in rows[-10:]:
        if r["event"] == "entry":
            print(f"{r['time']} ENTRY  {r['symbol']:<15} {r['side']:<5} entry={r['entry']} "
                  f"stop={r['stop']} tp={r['take_profit']} reason={r['reason']}")
        else:
            print(f"{r['time']} EXIT   {r['symbol']:<15} pnl={r['pnl']}")


if __name__ == "__main__":
    main()
