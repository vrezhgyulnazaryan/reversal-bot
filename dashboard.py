import csv
import json
import os
import threading
from functools import wraps

from flask import Flask, jsonify, render_template_string, request, Response

from bot.config import Config
from bot.exchange import build_exchange
from bot.live import LiveTrader

app = Flask(__name__)
cfg = Config.load("config.yaml")
exchange = build_exchange(cfg)

STATUS_PATH = "status.json"
LOG_PATH = "trades_log.csv"

DASHBOARD_USER = os.getenv("DASHBOARD_USER", "")
DASHBOARD_PASS = os.getenv("DASHBOARD_PASS", "")


def requires_auth(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not DASHBOARD_USER:  # auth disabled for local-only use (no env vars set)
            return f(*args, **kwargs)
        auth = request.authorization
        if not auth or auth.username != DASHBOARD_USER or auth.password != DASHBOARD_PASS:
            return Response(
                "Auth required", 401, {"WWW-Authenticate": 'Basic realm="reversal_bot"'}
            )
        return f(*args, **kwargs)
    return wrapped


_bot_thread_started = False


def start_bot_thread_once():
    global _bot_thread_started
    if _bot_thread_started:
        return
    _bot_thread_started = True
    trader = LiveTrader(cfg)
    t = threading.Thread(target=trader.run_forever, daemon=True, name="reversal-bot")
    t.start()
    print("[dashboard] bot trading thread started", flush=True)


# Off by default locally (start_bot.bat runs the bot as its own process there).
# Render sets RUN_BOT_IN_PROCESS=true so one combined web service does both jobs -
# its free tier only runs a Web Service, not a separate always-on background worker.
if os.getenv("RUN_BOT_IN_PROCESS", "false").lower() == "true":
    start_bot_thread_once()

PAGE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>reversal_bot dashboard</title>
<style>
  body { font-family: system-ui, sans-serif; background: #0b0e14; color: #e6e6e6; margin: 0; padding: 24px; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .sub { color: #8a8f98; font-size: 13px; margin-bottom: 24px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .card { background: #161a23; border: 1px solid #262b36; border-radius: 10px; padding: 16px; }
  .card h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .05em; color: #8a8f98; margin: 0 0 8px; }
  .big { font-size: 28px; font-weight: 600; }
  .green { color: #3ecf8e; }
  .red { color: #f0555c; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #262b36; }
  th { color: #8a8f98; font-weight: 500; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; }
  .pill.long { background: #123d2c; color: #3ecf8e; }
  .pill.short { background: #3d1618; color: #f0555c; }
  .empty { color: #565d6b; font-style: italic; }
  .badge { display:inline-block; padding: 3px 10px; border-radius: 6px; background:#2a2f3a; font-size:12px; margin-left:8px; }
</style>
</head>
<body>
  <h1>reversal_bot <span class="badge" id="mode">...</span></h1>
  <div class="sub">Auto-refreshes every 5s. Last update: <span id="lastUpdate">-</span></div>

  <div class="grid">
    <div class="card">
      <h2>Equity</h2>
      <div class="big" id="equity">-</div>
    </div>
    <div class="card">
      <h2>Open positions</h2>
      <div class="big" id="posCount">-</div>
    </div>
    <div class="card">
      <h2>Closed trades win rate</h2>
      <div class="big" id="winRate">-</div>
    </div>
    <div class="card">
      <h2>Total realized PnL</h2>
      <div class="big" id="totalPnl">-</div>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <h2>Open positions</h2>
      <table id="positionsTable"><thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Entry</th><th>Mark</th><th>uPnL</th></tr></thead><tbody></tbody></table>
      <div class="empty" id="positionsEmpty">No open positions</div>
    </div>
    <div class="card">
      <h2>Last scan (movers)</h2>
      <table id="moversTable"><thead><tr><th>Symbol</th><th>Move %</th></tr></thead><tbody></tbody></table>
    </div>
  </div>

  <div class="card">
    <h2>Trade history (last 20)</h2>
    <table id="historyTable"><thead><tr><th>Time</th><th>Event</th><th>Symbol</th><th>Side</th><th>Entry</th><th>Stop</th><th>TP</th><th>PnL</th><th>Reason</th></tr></thead><tbody></tbody></table>
    <div class="empty" id="historyEmpty">No trades yet</div>
  </div>

<script>
async function refresh() {
  const res = await fetch('/api/status');
  const data = await res.json();

  document.getElementById('mode').textContent = data.mode;
  document.getElementById('lastUpdate').textContent = data.scan_time || '-';
  document.getElementById('equity').textContent = data.equity.toFixed(2) + ' USDT';
  document.getElementById('posCount').textContent = data.positions.length;

  const wr = data.stats.closed > 0 ? (data.stats.wins / data.stats.closed * 100).toFixed(1) + '%' : '-';
  document.getElementById('winRate').textContent = wr + (data.stats.closed ? ` (${data.stats.wins}/${data.stats.closed})` : '');

  const pnlEl = document.getElementById('totalPnl');
  pnlEl.textContent = data.stats.total_pnl.toFixed(2) + ' USDT';
  pnlEl.className = 'big ' + (data.stats.total_pnl >= 0 ? 'green' : 'red');

  const posBody = document.querySelector('#positionsTable tbody');
  posBody.innerHTML = '';
  document.getElementById('positionsEmpty').style.display = data.positions.length ? 'none' : 'block';
  for (const p of data.positions) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${p.symbol}</td><td><span class="pill ${p.side}">${p.side}</span></td>` +
      `<td>${p.qty}</td><td>${p.entry}</td><td>${p.mark}</td>` +
      `<td class="${p.upnl >= 0 ? 'green' : 'red'}">${p.upnl}</td>`;
    posBody.appendChild(tr);
  }

  const movBody = document.querySelector('#moversTable tbody');
  movBody.innerHTML = '';
  for (const m of data.movers) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${m[0]}</td><td class="${m[1] >= 0 ? 'green' : 'red'}">${m[1].toFixed(2)}%</td>`;
    movBody.appendChild(tr);
  }

  const histBody = document.querySelector('#historyTable tbody');
  histBody.innerHTML = '';
  document.getElementById('historyEmpty').style.display = data.history.length ? 'none' : 'block';
  for (const h of data.history.slice().reverse()) {
    const tr = document.createElement('tr');
    const pnlClass = h.pnl ? (parseFloat(h.pnl) >= 0 ? 'green' : 'red') : '';
    tr.innerHTML = `<td>${h.time}</td><td>${h.event}</td><td>${h.symbol}</td>` +
      `<td>${h.side || ''}</td><td>${h.entry || ''}</td><td>${h.stop || ''}</td>` +
      `<td>${h.take_profit || ''}</td><td class="${pnlClass}">${h.pnl || ''}</td><td>${h.reason || ''}</td>`;
    histBody.appendChild(tr);
  }
}
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


@app.route("/")
@requires_auth
def index():
    return render_template_string(PAGE)


@app.route("/api/status")
@requires_auth
def api_status():
    balance = exchange.fetch_balance()
    equity = balance["total"].get("USDT", 0.0)

    positions = []
    for p in exchange.fetch_positions():
        if float(p.get("contracts") or 0) == 0:
            continue
        positions.append({
            "symbol": p["symbol"],
            "side": p.get("side"),
            "qty": p.get("contracts"),
            "entry": p.get("entryPrice"),
            "mark": p.get("markPrice"),
            "upnl": round(float(p.get("unrealizedPnl") or 0), 2),
        })

    scan_time, movers = None, []
    if os.path.exists(STATUS_PATH):
        with open(STATUS_PATH) as f:
            status = json.load(f)
        scan_time = status.get("time")
        movers = status.get("movers", [])

    history = []
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, newline="") as f:
            history = list(csv.DictReader(f))

    closed_pnls = [float(r["pnl"]) for r in history if r.get("event") == "exit" and r.get("pnl")]
    stats = {
        "closed": len(closed_pnls),
        "wins": len([p for p in closed_pnls if p > 0]),
        "total_pnl": sum(closed_pnls),
    }

    return jsonify({
        "mode": "TESTNET/DEMO" if cfg.testnet else "LIVE",
        "equity": equity,
        "positions": positions,
        "movers": movers,
        "scan_time": scan_time,
        "history": history[-20:],
        "stats": stats,
    })


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    host = "0.0.0.0" if os.getenv("PORT") else "127.0.0.1"
    print(f"Dashboard running at http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
