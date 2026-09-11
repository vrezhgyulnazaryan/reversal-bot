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
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>reversal_bot</title>
<style>
  :root {
    --bg: #08090d;
    --bg-soft: #0d0f16;
    --card: #12141c;
    --card-hover: #161923;
    --border: #1f2330;
    --text: #eef0f5;
    --muted: #7c8296;
    --faint: #4b5165;
    --green: #34d399;
    --green-soft: rgba(52, 211, 153, .12);
    --red: #fb7185;
    --red-soft: rgba(251, 113, 133, .12);
    --accent: #6ea8fe;
    --accent-soft: rgba(110, 168, 254, .12);
    --amber: #fbbf24;
    --radius: 14px;
  }
  * { box-sizing: border-box; }
  html, body { max-width: 100%; overflow-x: hidden; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    background: radial-gradient(ellipse 1200px 600px at 50% -10%, #131726 0%, var(--bg) 55%);
    background-attachment: fixed;
    color: var(--text); margin: 0; padding: 28px 24px 60px;
    -webkit-font-smoothing: antialiased;
  }
  .wrap { max-width: 1080px; margin: 0 auto; }

  header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 26px; flex-wrap: wrap; gap: 10px; }
  .brand { display: flex; align-items: center; gap: 10px; }
  .brand .logo {
    width: 34px; height: 34px; border-radius: 10px; display: flex; align-items: center; justify-content: center;
    background: linear-gradient(135deg, var(--accent), #a78bfa); font-size: 16px; flex-shrink: 0;
  }
  .brand h1 { font-size: 18px; font-weight: 650; margin: 0; letter-spacing: -.01em; }
  .brand .modebadge { font-size: 10.5px; font-weight: 700; letter-spacing: .06em; color: var(--amber);
    background: rgba(251,191,36,.12); border: 1px solid rgba(251,191,36,.25); border-radius: 6px; padding: 3px 7px; margin-left: 2px; }
  .live { display: flex; align-items: center; gap: 7px; font-size: 12.5px; color: var(--muted); }
  .live .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--green); box-shadow: 0 0 0 0 rgba(52,211,153,.6);
    animation: pulse 2s infinite; }
  @keyframes pulse {
    0% { box-shadow: 0 0 0 0 rgba(52,211,153,.55); }
    70% { box-shadow: 0 0 0 7px rgba(52,211,153,0); }
    100% { box-shadow: 0 0 0 0 rgba(52,211,153,0); }
  }

  .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 20px; }
  .stat { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 18px; min-width: 0; }
  .stat .label { font-size: 11.5px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; color: var(--faint); margin-bottom: 8px; }
  .stat .value { font-size: 22px; font-weight: 680; letter-spacing: -.01em; word-break: break-word; font-variant-numeric: tabular-nums; }
  .stat .sub { font-size: 12px; color: var(--muted); margin-top: 3px; }

  .panel {
    background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 18px 18px 8px; margin-bottom: 16px;
  }
  .panel-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
  .panel-head h2 { font-size: 13.5px; font-weight: 650; margin: 0; display: flex; align-items: center; gap: 7px; }
  .panel-head .count { font-size: 11.5px; color: var(--faint); font-weight: 600; }
  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start; }

  .green { color: var(--green); } .red { color: var(--red); } .muted { color: var(--muted); }

  .pill { display: inline-flex; align-items: center; gap: 3px; padding: 2px 9px; border-radius: 999px; font-size: 11px; font-weight: 700; letter-spacing: .02em; }
  .pill.long { background: var(--green-soft); color: var(--green); }
  .pill.short { background: var(--red-soft); color: var(--red); }
  .pill.entry { background: var(--accent-soft); color: var(--accent); }
  .pill.exit { background: rgba(124,130,150,.15); color: var(--muted); }
  .pill.trail_stop { background: rgba(251,191,36,.12); color: var(--amber); }

  .poslist { display: flex; flex-direction: column; gap: 8px; padding-bottom: 10px; }
  .posrow {
    display: flex; align-items: center; justify-content: space-between; gap: 10px;
    background: var(--bg-soft); border: 1px solid var(--border); border-radius: 10px; padding: 11px 13px;
  }
  .posrow .left { display: flex; align-items: center; gap: 9px; min-width: 0; }
  .posrow .sym { font-weight: 650; font-size: 14px; }
  .posrow .meta { font-size: 11.5px; color: var(--muted); margin-top: 2px; font-variant-numeric: tabular-nums; }
  .posrow .right { text-align: right; flex-shrink: 0; }
  .posrow .pnl { font-weight: 700; font-size: 14.5px; font-variant-numeric: tabular-nums; }
  .posrow .pnl.stale { font-size: 11.5px; font-weight: 600; color: var(--amber); }

  .moverlist { display: flex; flex-direction: column; gap: 3px; padding-bottom: 10px; }
  .moverrow { display: flex; align-items: center; gap: 10px; padding: 7px 2px; }
  .moverrow .sym { font-size: 13px; font-weight: 600; width: 92px; flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .moverrow .bar-track { flex: 1; height: 6px; border-radius: 4px; background: rgba(124,130,150,.12); overflow: hidden; }
  .moverrow .bar { height: 100%; border-radius: 4px; }
  .moverrow .bar.up { background: var(--green); }
  .moverrow .bar.down { background: var(--red); }
  .moverrow .pct { font-size: 12.5px; font-weight: 650; width: 60px; text-align: right; flex-shrink: 0; font-variant-numeric: tabular-nums; }

  .table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; margin: 0 -18px; padding: 0 18px; }
  table { width: 100%; min-width: 520px; border-collapse: collapse; font-size: 12.5px; }
  th, td { text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; }
  th { color: var(--faint); font-weight: 650; font-size: 10.5px; text-transform: uppercase; letter-spacing: .05em; }
  tbody tr:hover { background: rgba(255,255,255,.015); }
  tbody tr:last-child td { border-bottom: none; }
  td.sym { font-weight: 600; }
  td.reason { color: var(--muted); max-width: 220px; overflow: hidden; text-overflow: ellipsis; }
  td.time { color: var(--faint); font-variant-numeric: tabular-nums; }

  .empty { color: var(--faint); font-size: 13px; text-align: center; padding: 22px 0; }
  .empty .ico { font-size: 22px; display: block; margin-bottom: 6px; opacity: .5; }

  @media (max-width: 700px) {
    body { padding: 16px 12px 40px; }
    .stats { grid-template-columns: 1fr 1fr; gap: 8px; }
    .stat { padding: 12px 13px; border-radius: 11px; }
    .stat .value { font-size: 18px; }
    .two-col { grid-template-columns: 1fr; }
    .panel { padding: 14px 14px 6px; border-radius: 11px; }
    .brand h1 { font-size: 16px; }
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="brand">
      <div class="logo">🤖</div>
      <div>
        <h1>reversal_bot <span class="modebadge" id="mode">...</span></h1>
      </div>
    </div>
    <div class="live"><span class="dot"></span><span id="lastUpdate">connecting…</span></div>
  </header>

  <div class="stats">
    <div class="stat">
      <div class="label">Equity</div>
      <div class="value" id="equity">-</div>
    </div>
    <div class="stat">
      <div class="label">Open</div>
      <div class="value" id="posCount">-</div>
    </div>
    <div class="stat">
      <div class="label">Win rate</div>
      <div class="value" id="winRate">-</div>
      <div class="sub" id="winRateSub"></div>
    </div>
    <div class="stat">
      <div class="label">Realized P&amp;L</div>
      <div class="value" id="totalPnl">-</div>
    </div>
  </div>

  <div class="two-col">
    <div class="panel">
      <div class="panel-head"><h2>📌 Open positions</h2><span class="count" id="posCountLabel"></span></div>
      <div class="poslist" id="posList"></div>
      <div class="empty" id="positionsEmpty"><span class="ico">💤</span>No open positions</div>
    </div>
    <div class="panel">
      <div class="panel-head"><h2>📡 Last scan</h2><span class="count" id="scanCountLabel"></span></div>
      <div class="moverlist" id="moverList"></div>
      <div class="empty" id="moversEmpty" style="display:none"><span class="ico">📭</span>No data yet</div>
    </div>
  </div>

  <div class="panel">
    <div class="panel-head"><h2>📜 Trade history</h2><span class="count">last 20</span></div>
    <div class="table-wrap">
      <table id="historyTable"><thead><tr><th>Time</th><th>Event</th><th>Coin</th><th>Side</th><th>Entry</th><th>Stop</th><th>TP</th><th>PnL</th><th>Reason</th></tr></thead><tbody></tbody></table>
    </div>
    <div class="empty" id="historyEmpty" style="display:none"><span class="ico">🗒️</span>No trades yet</div>
  </div>
</div>

<script>
function coinName(sym) { return (sym || '').split('/')[0]; }

function fmtNum(v, maxDp) {
  if (v === null || v === undefined || v === '') return '-';
  const n = parseFloat(v);
  if (isNaN(n)) return v;
  const dp = Math.abs(n) < 1 ? Math.min(6, maxDp ?? 6) : Math.min(4, maxDp ?? 4);
  return n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: dp });
}

function timeAgo(raw) {
  if (!raw) return '-';
  const iso = raw.includes('T') || raw.includes('+') ? raw : raw.replace(' ', 'T') + 'Z';
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 5) return 'just now';
  if (diff < 60) return Math.floor(diff) + 's ago';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

async function refresh() {
  let data;
  try {
    const res = await fetch('/api/status');
    data = await res.json();
  } catch (e) {
    document.getElementById('lastUpdate').textContent = 'connection lost';
    return;
  }

  document.getElementById('mode').textContent = data.mode;
  document.getElementById('lastUpdate').textContent = 'updated ' + timeAgo(data.scan_time);
  document.getElementById('equity').textContent = fmtNum(data.equity, 2) + ' USDT';
  document.getElementById('posCount').textContent = data.positions.length;

  const wr = data.stats.closed > 0 ? (data.stats.wins / data.stats.closed * 100).toFixed(1) + '%' : '-';
  document.getElementById('winRate').textContent = wr;
  document.getElementById('winRateSub').textContent = data.stats.closed ? `${data.stats.wins}/${data.stats.closed} trades` : 'no trades yet';

  const pnlEl = document.getElementById('totalPnl');
  pnlEl.textContent = (data.stats.total_pnl >= 0 ? '+' : '') + fmtNum(data.stats.total_pnl, 2) + ' USDT';
  pnlEl.className = 'value ' + (data.stats.total_pnl >= 0 ? 'green' : 'red');

  // --- open positions ---
  const posList = document.getElementById('posList');
  posList.innerHTML = '';
  document.getElementById('positionsEmpty').style.display = data.positions.length ? 'none' : 'block';
  document.getElementById('posCountLabel').textContent = data.positions.length ? data.positions.length + ' open' : '';
  for (const p of data.positions) {
    const row = document.createElement('div');
    row.className = 'posrow';
    const pnlHtml = p.stale
      ? `<div class="pnl stale">⚠ stale feed</div>`
      : `<div class="pnl ${p.upnl >= 0 ? 'green' : 'red'}">${p.upnl >= 0 ? '+' : ''}${fmtNum(p.upnl, 2)}</div>`;
    row.innerHTML = `
      <div class="left">
        <span class="pill ${p.side || ''}">${(p.side || '?').toUpperCase()}</span>
        <div>
          <div class="sym">${coinName(p.symbol)}</div>
          <div class="meta">${fmtNum(p.qty, 2)} @ ${fmtNum(p.entry)}</div>
        </div>
      </div>
      <div class="right">${pnlHtml}<div class="meta">mark ${fmtNum(p.mark)}</div></div>`;
    posList.appendChild(row);
  }

  // --- last scan movers ---
  const moverList = document.getElementById('moverList');
  moverList.innerHTML = '';
  document.getElementById('moversEmpty').style.display = data.movers.length ? 'none' : 'block';
  document.getElementById('scanCountLabel').textContent = data.movers.length ? data.movers.length + ' coins' : '';
  const maxAbs = Math.max(1, ...data.movers.map(m => Math.abs(m[1])));
  for (const m of data.movers) {
    const row = document.createElement('div');
    row.className = 'moverrow';
    const up = m[1] >= 0;
    const width = Math.min(100, Math.abs(m[1]) / maxAbs * 100);
    row.innerHTML = `
      <div class="sym">${coinName(m[0])}</div>
      <div class="bar-track"><div class="bar ${up ? 'up' : 'down'}" style="width:${width}%"></div></div>
      <div class="pct ${up ? 'green' : 'red'}">${up ? '+' : ''}${m[1].toFixed(1)}%</div>`;
    moverList.appendChild(row);
  }

  // --- trade history ---
  const histBody = document.querySelector('#historyTable tbody');
  histBody.innerHTML = '';
  document.getElementById('historyEmpty').style.display = data.history.length ? 'none' : 'block';
  document.querySelector('#historyTable').parentElement.style.display = data.history.length ? 'block' : 'none';
  const eventIcons = { entry: '↗', exit: '↘', trail_stop: '🔒' };
  for (const h of data.history.slice().reverse()) {
    const tr = document.createElement('tr');
    const pnlClass = h.pnl ? (parseFloat(h.pnl) >= 0 ? 'green' : 'red') : '';
    const pnlText = h.pnl ? (parseFloat(h.pnl) >= 0 ? '+' : '') + fmtNum(h.pnl, 2) : '-';
    tr.innerHTML = `
      <td class="time">${timeAgo(h.time)}</td>
      <td><span class="pill ${h.event}">${eventIcons[h.event] || ''} ${h.event}</span></td>
      <td class="sym">${coinName(h.symbol)}</td>
      <td>${h.side ? `<span class="pill ${h.side}">${h.side}</span>` : '-'}</td>
      <td>${fmtNum(h.entry)}</td>
      <td>${fmtNum(h.stop)}</td>
      <td>${fmtNum(h.take_profit)}</td>
      <td class="${pnlClass}">${pnlText}</td>
      <td class="reason">${h.reason || '-'}</td>`;
    histBody.appendChild(tr);
  }
}
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


@app.route("/health")
def health():
    # deliberately no auth - this is what an external uptime pinger hits every few
    # minutes to stop Render's free tier from spinning the service down (and with it,
    # the trading thread) after 15 minutes of no HTTP traffic
    return "ok"


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
        # Demo Trading has occasionally returned markPrice=0 for a genuinely open
        # position (stale/broken feed for that symbol) - the unrealizedPnl computed
        # off that is garbage, so flag it instead of showing a misleading number.
        stale = float(p.get("markPrice") or 0) <= 0
        positions.append({
            "symbol": p["symbol"],
            "side": p.get("side"),
            "qty": p.get("contracts"),
            "entry": p.get("entryPrice"),
            "mark": p.get("markPrice"),
            "upnl": None if stale else round(float(p.get("unrealizedPnl") or 0), 2),
            "stale": stale,
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
