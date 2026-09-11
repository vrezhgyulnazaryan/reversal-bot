import csv
import json
import os
import threading
from datetime import datetime, timezone
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
    --bg: #07080c;
    --bg-soft: #0d0f16;
    --card: #12141c;
    --border: #1e212c;
    --text: #eef0f5;
    --muted: #7c8296;
    --faint: #4b5165;
    --green: #34d399;
    --green-soft: rgba(52, 211, 153, .12);
    --red: #fb7185;
    --red-soft: rgba(251, 113, 133, .12);
    --accent: #6ea8fe;
    --accent2: #a78bfa;
    --accent-soft: rgba(110, 168, 254, .12);
    --amber: #fbbf24;
    --radius: 16px;
    --sidebar-w: 210px;
  }
  * { box-sizing: border-box; }
  html, body { max-width: 100%; overflow-x: hidden; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    background: var(--bg); color: var(--text); margin: 0;
    -webkit-font-smoothing: antialiased;
  }
  .glow {
    position: fixed; inset: 0; z-index: 0; pointer-events: none;
    background:
      radial-gradient(680px 420px at 14% -6%, rgba(110,168,254,.16), transparent 60%),
      radial-gradient(620px 380px at 88% 4%, rgba(167,139,250,.13), transparent 60%);
  }

  .shell { position: relative; z-index: 1; display: flex; min-height: 100vh; }

  /* ---------- sidebar ---------- */
  .sidebar {
    width: var(--sidebar-w); flex-shrink: 0; padding: 22px 14px;
    border-right: 1px solid var(--border); background: rgba(13,15,22,.6);
    display: flex; flex-direction: column; position: sticky; top: 0; height: 100vh;
  }
  .brand { display: flex; align-items: center; gap: 10px; padding: 0 6px 22px; }
  .brand .logo {
    width: 32px; height: 32px; border-radius: 9px; display: flex; align-items: center; justify-content: center;
    background: linear-gradient(135deg, var(--accent), var(--accent2)); font-size: 15px; flex-shrink: 0;
    box-shadow: 0 0 20px rgba(110,168,254,.35);
  }
  .brand .name { font-size: 15.5px; font-weight: 700; letter-spacing: -.01em; }
  .navgroup { display: flex; flex-direction: column; gap: 3px; }
  .navbtn {
    display: flex; align-items: center; gap: 10px; width: 100%; text-align: left;
    background: transparent; border: none; color: var(--muted); font-size: 13.5px; font-weight: 600;
    padding: 10px 12px; border-radius: 10px; cursor: pointer; font-family: inherit;
    transition: background .12s, color .12s;
  }
  .navbtn .ic { font-size: 15px; width: 18px; text-align: center; flex-shrink: 0; }
  .navbtn:hover { background: rgba(255,255,255,.04); color: var(--text); }
  .navbtn.active { background: var(--accent-soft); color: var(--accent); }
  .sidebar-foot { margin-top: auto; padding: 12px 8px 4px; border-top: 1px solid var(--border); }
  .live { display: flex; align-items: center; gap: 7px; font-size: 11.5px; color: var(--muted); margin-bottom: 8px; }
  .live .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--green); flex-shrink: 0;
    box-shadow: 0 0 0 0 rgba(52,211,153,.6); animation: pulse 2s infinite; }
  @keyframes pulse {
    0% { box-shadow: 0 0 0 0 rgba(52,211,153,.55); } 70% { box-shadow: 0 0 0 7px rgba(52,211,153,0); }
    100% { box-shadow: 0 0 0 0 rgba(52,211,153,0); }
  }
  .modebadge { display: inline-block; font-size: 10px; font-weight: 700; letter-spacing: .06em; color: var(--amber);
    background: rgba(251,191,36,.12); border: 1px solid rgba(251,191,36,.25); border-radius: 6px; padding: 3px 7px; }

  /* ---------- main ---------- */
  main { flex: 1; min-width: 0; padding: 24px 26px 90px; }
  h1.pagetitle { font-size: 19px; font-weight: 700; margin: 0 0 18px; letter-spacing: -.01em; }

  .stats { display: grid; grid-template-columns: 1.3fr 1fr 1fr 1fr 1fr; gap: 12px; margin-bottom: 18px; }
  .stat {
    background: linear-gradient(180deg, var(--card), var(--bg-soft)); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 15px 17px; min-width: 0; position: relative; overflow: hidden;
  }
  .stat .label { font-size: 11px; font-weight: 650; text-transform: uppercase; letter-spacing: .06em; color: var(--faint); margin-bottom: 7px; }
  .stat .value { font-size: 21px; font-weight: 700; letter-spacing: -.01em; word-break: break-word; font-variant-numeric: tabular-nums; }
  .stat .sub { font-size: 11.5px; color: var(--muted); margin-top: 3px; }
  .stat.equity { display: flex; align-items: flex-end; justify-content: space-between; gap: 10px; }
  .stat.equity svg { flex-shrink: 0; }
  .stat.winrate { display: flex; align-items: center; gap: 12px; }
  .stat.winrate .ring-wrap { position: relative; width: 52px; height: 52px; flex-shrink: 0; }
  .stat.winrate .ring-wrap .pct { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    font-size: 12px; font-weight: 700; }

  .green { color: var(--green); } .red { color: var(--red); } .muted { color: var(--muted); }

  .pill { display: inline-flex; align-items: center; gap: 3px; padding: 2px 9px; border-radius: 999px; font-size: 11px; font-weight: 700; letter-spacing: .02em; white-space: nowrap; }
  .pill.long { background: var(--green-soft); color: var(--green); }
  .pill.short { background: var(--red-soft); color: var(--red); }
  .pill.entry { background: var(--accent-soft); color: var(--accent); }
  .pill.exit { background: rgba(124,130,150,.15); color: var(--muted); }
  .pill.trail_stop { background: rgba(251,191,36,.12); color: var(--amber); }

  .avatar {
    width: 30px; height: 30px; border-radius: 9px; flex-shrink: 0; display: flex; align-items: center; justify-content: center;
    font-size: 11.5px; font-weight: 750; color: #0b0c10;
  }

  .panel { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 18px 18px 8px; margin-bottom: 16px; }
  .panel-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
  .panel-head h2 { font-size: 13.5px; font-weight: 700; margin: 0; display: flex; align-items: center; gap: 7px; }
  .panel-head .count { font-size: 11.5px; color: var(--faint); font-weight: 600; }
  .panel-head a.viewall { font-size: 11.5px; color: var(--accent); text-decoration: none; font-weight: 650; cursor: pointer; }
  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start; }

  .poslist { display: flex; flex-direction: column; gap: 8px; padding-bottom: 10px; }
  .posrow { display: flex; align-items: center; justify-content: space-between; gap: 10px;
    background: var(--bg-soft); border: 1px solid var(--border); border-radius: 12px; padding: 10px 13px; }
  .posrow .left { display: flex; align-items: center; gap: 10px; min-width: 0; }
  .posrow .sym { font-weight: 700; font-size: 14px; }
  .posrow .meta { font-size: 11px; color: var(--muted); margin-top: 1px; font-variant-numeric: tabular-nums; }
  .posrow .right { text-align: right; flex-shrink: 0; }
  .posrow .pnl { font-weight: 750; font-size: 14.5px; font-variant-numeric: tabular-nums; }
  .posrow .pnl.stale { font-size: 11.5px; font-weight: 600; color: var(--amber); }

  .moverlist { display: flex; flex-direction: column; gap: 2px; padding-bottom: 10px; }
  .moverrow { display: flex; align-items: center; gap: 10px; padding: 6px 2px; }
  .moverrow .sym { font-size: 12.5px; font-weight: 650; width: 84px; flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .moverrow .bar-track { flex: 1; height: 6px; border-radius: 4px; background: rgba(124,130,150,.12); overflow: hidden; }
  .moverrow .bar { height: 100%; border-radius: 4px; }
  .moverrow .bar.up { background: linear-gradient(90deg, #10b981, var(--green)); }
  .moverrow .bar.down { background: linear-gradient(90deg, #e11d48, var(--red)); }
  .moverrow .pct { font-size: 12px; font-weight: 700; width: 58px; text-align: right; flex-shrink: 0; font-variant-numeric: tabular-nums; }

  .table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; margin: 0 -18px; padding: 0 18px; }
  table { width: 100%; min-width: 560px; border-collapse: collapse; font-size: 12.5px; }
  th, td { text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; }
  th { color: var(--faint); font-weight: 650; font-size: 10.5px; text-transform: uppercase; letter-spacing: .05em; }
  tbody tr:hover { background: rgba(255,255,255,.015); }
  tbody tr:last-child td { border-bottom: none; }
  td.coin { display: flex; align-items: center; gap: 8px; font-weight: 650; border-bottom: 1px solid var(--border); }
  td.reason { color: var(--muted); max-width: 200px; overflow: hidden; text-overflow: ellipsis; }
  td.time { color: var(--faint); font-variant-numeric: tabular-nums; }

  .empty { color: var(--faint); font-size: 13px; text-align: center; padding: 26px 0; }
  .empty .ico { font-size: 24px; display: block; margin-bottom: 6px; opacity: .5; }

  .page { display: none; }
  .page.active { display: block; }

  .bottomnav { display: none; }

  @media (max-width: 900px) {
    .sidebar { display: none; }
    main { padding: 16px 14px 84px; }
    .bottomnav {
      display: flex; position: fixed; bottom: 0; left: 0; right: 0; z-index: 5;
      background: rgba(10,11,16,.92); backdrop-filter: blur(10px); border-top: 1px solid var(--border);
      padding: 8px 6px calc(8px + env(safe-area-inset-bottom));
    }
    .bottomnav button {
      flex: 1; background: none; border: none; color: var(--faint); font-family: inherit;
      display: flex; flex-direction: column; align-items: center; gap: 3px; font-size: 10px; font-weight: 650;
      padding: 4px 0; cursor: pointer;
    }
    .bottomnav button .ic { font-size: 18px; }
    .bottomnav button.active { color: var(--accent); }
    .stats { grid-template-columns: 1fr 1fr; gap: 8px; }
    .stat { padding: 12px 13px; border-radius: 12px; }
    .stat .value { font-size: 17px; }
    .stat.equity { grid-column: 1 / -1; }
    .stat.equity .value { font-size: 21px; }
    .two-col { grid-template-columns: 1fr; }
    .panel { padding: 14px 14px 6px; border-radius: 13px; }
    h1.pagetitle { font-size: 17px; }
  }
</style>
</head>
<body>
<div class="glow"></div>
<div class="shell">
  <aside class="sidebar">
    <div class="brand"><div class="logo">🤖</div><div class="name">reversal_bot</div></div>
    <div class="navgroup">
      <button class="navbtn active" data-page="overview"><span class="ic">🏠</span>Overview</button>
      <button class="navbtn" data-page="positions"><span class="ic">📌</span>Positions</button>
      <button class="navbtn" data-page="scan"><span class="ic">📡</span>Market Scan</button>
      <button class="navbtn" data-page="history"><span class="ic">📜</span>History</button>
    </div>
    <div class="sidebar-foot">
      <div class="live"><span class="dot"></span><span id="lastUpdate">connecting…</span></div>
      <span class="modebadge" id="mode">...</span>
    </div>
  </aside>

  <main>
    <div class="stats">
      <div class="stat equity">
        <div><div class="label">Equity</div><div class="value" id="equity">-</div></div>
        <svg id="sparkline" width="96" height="34" viewBox="0 0 96 34"></svg>
      </div>
      <div class="stat"><div class="label">Open</div><div class="value" id="posCount">-</div></div>
      <div class="stat winrate">
        <div class="ring-wrap">
          <svg width="52" height="52" viewBox="0 0 52 52">
            <circle cx="26" cy="26" r="21" fill="none" stroke="rgba(124,130,150,.15)" stroke-width="6"/>
            <circle id="wrRing" cx="26" cy="26" r="21" fill="none" stroke="#34d399" stroke-width="6"
              stroke-dasharray="132" stroke-dashoffset="132" stroke-linecap="round" transform="rotate(-90 26 26)"/>
          </svg>
          <div class="pct" id="winRate">-</div>
        </div>
        <div><div class="label">Win rate</div><div class="sub" id="winRateSub">-</div></div>
      </div>
      <div class="stat"><div class="label">Realized P&amp;L</div><div class="value" id="totalPnl">-</div></div>
      <div class="stat"><div class="label">Today</div><div class="value" id="todayPnl">-</div></div>
    </div>

    <section id="page-overview" class="page active">
      <div class="two-col">
        <div class="panel">
          <div class="panel-head"><h2>📌 Open positions</h2><a class="viewall" data-goto="positions">View all</a></div>
          <div class="poslist" id="posListPreview"></div>
          <div class="empty" id="positionsEmptyPreview"><span class="ico">💤</span>No open positions</div>
        </div>
        <div class="panel">
          <div class="panel-head"><h2>📡 Last scan</h2><a class="viewall" data-goto="scan">View all</a></div>
          <div class="moverlist" id="moverListPreview"></div>
          <div class="empty" id="moversEmptyPreview" style="display:none"><span class="ico">📭</span>No data yet</div>
        </div>
      </div>
      <div class="panel">
        <div class="panel-head"><h2>📜 Recent activity</h2><a class="viewall" data-goto="history">View all</a></div>
        <div class="table-wrap"><table id="historyTablePreview"><thead><tr><th>Time</th><th>Event</th><th>Coin</th><th>Side</th><th>PnL</th></tr></thead><tbody></tbody></table></div>
        <div class="empty" id="historyEmptyPreview" style="display:none"><span class="ico">🗒️</span>No trades yet</div>
      </div>
    </section>

    <section id="page-positions" class="page">
      <h1 class="pagetitle">Open Positions</h1>
      <div class="panel">
        <div class="poslist" id="posListFull"></div>
        <div class="empty" id="positionsEmptyFull"><span class="ico">💤</span>No open positions right now</div>
      </div>
    </section>

    <section id="page-scan" class="page">
      <h1 class="pagetitle">Market Scan</h1>
      <div class="panel">
        <div class="panel-head"><h2>Coins with the biggest recent move</h2><span class="count" id="scanCountLabel"></span></div>
        <div class="moverlist" id="moverListFull"></div>
        <div class="empty" id="moversEmptyFull" style="display:none"><span class="ico">📭</span>No data yet</div>
      </div>
    </section>

    <section id="page-history" class="page">
      <h1 class="pagetitle">Trade History</h1>
      <div class="panel">
        <div class="table-wrap">
          <table id="historyTableFull"><thead><tr><th>Time</th><th>Event</th><th>Coin</th><th>Side</th><th>Entry</th><th>Stop</th><th>TP</th><th>PnL</th><th>Reason</th></tr></thead><tbody></tbody></table>
        </div>
        <div class="empty" id="historyEmptyFull" style="display:none"><span class="ico">🗒️</span>No trades yet</div>
      </div>
    </section>
  </main>
</div>

<nav class="bottomnav">
  <button class="active" data-page="overview"><span class="ic">🏠</span>Overview</button>
  <button data-page="positions"><span class="ic">📌</span>Positions</button>
  <button data-page="scan"><span class="ic">📡</span>Scan</button>
  <button data-page="history"><span class="ic">📜</span>History</button>
</nav>

<script>
function coinName(sym) { return (sym || '').split('/')[0]; }

const PALETTE = ['#6ea8fe','#a78bfa','#34d399','#fbbf24','#fb7185','#38bdf8','#f472b6','#4ade80','#fb923c','#c084fc'];
function avatarColor(sym) {
  let h = 0;
  for (let i = 0; i < sym.length; i++) h = (h * 31 + sym.charCodeAt(i)) >>> 0;
  return PALETTE[h % PALETTE.length];
}
function avatarHtml(sym) {
  const name = coinName(sym);
  const letters = name.replace(/[^A-Za-z0-9]/g, '').slice(0, 2).toUpperCase() || '?';
  return `<div class="avatar" style="background:${avatarColor(name)}">${letters}</div>`;
}

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

// ---------- page switching (sidebar + bottom nav share the same data-page buttons) ----------
function gotoPage(name) {
  document.querySelectorAll('.page').forEach(el => el.classList.toggle('active', el.id === 'page-' + name));
  document.querySelectorAll('.navbtn, .bottomnav button').forEach(el => el.classList.toggle('active', el.dataset.page === name));
}
document.querySelectorAll('.navbtn, .bottomnav button, [data-goto]').forEach(el => {
  el.addEventListener('click', () => gotoPage(el.dataset.page || el.dataset.goto));
});

// ---------- render helpers (each used for both the overview preview and the full page) ----------
function renderPositions(container, positions) {
  container.innerHTML = '';
  for (const p of positions) {
    const row = document.createElement('div');
    row.className = 'posrow';
    const pnlHtml = p.stale
      ? `<div class="pnl stale">⚠ stale feed</div>`
      : `<div class="pnl ${p.upnl >= 0 ? 'green' : 'red'}">${p.upnl >= 0 ? '+' : ''}${fmtNum(p.upnl, 2)}</div>`;
    row.innerHTML = `
      <div class="left">
        ${avatarHtml(p.symbol)}
        <div>
          <div class="sym">${coinName(p.symbol)} <span class="pill ${p.side || ''}" style="margin-left:4px">${(p.side || '?').toUpperCase()}</span></div>
          <div class="meta">${fmtNum(p.qty, 2)} @ ${fmtNum(p.entry)}</div>
        </div>
      </div>
      <div class="right">${pnlHtml}<div class="meta">mark ${fmtNum(p.mark)}</div></div>`;
    container.appendChild(row);
  }
}

function renderMovers(container, movers) {
  container.innerHTML = '';
  const maxAbs = Math.max(1, ...movers.map(m => Math.abs(m[1])));
  for (const m of movers) {
    const row = document.createElement('div');
    row.className = 'moverrow';
    const up = m[1] >= 0;
    const width = Math.min(100, Math.abs(m[1]) / maxAbs * 100);
    row.innerHTML = `
      <div class="sym">${coinName(m[0])}</div>
      <div class="bar-track"><div class="bar ${up ? 'up' : 'down'}" style="width:${width}%"></div></div>
      <div class="pct ${up ? 'green' : 'red'}">${up ? '+' : ''}${m[1].toFixed(1)}%</div>`;
    container.appendChild(row);
  }
}

const EVENT_ICONS = { entry: '↗', exit: '↘', trail_stop: '🔒' };

function renderHistoryFull(tbody, rows) {
  tbody.innerHTML = '';
  for (const h of rows) {
    const tr = document.createElement('tr');
    const pnlClass = h.pnl ? (parseFloat(h.pnl) >= 0 ? 'green' : 'red') : '';
    const pnlText = h.pnl ? (parseFloat(h.pnl) >= 0 ? '+' : '') + fmtNum(h.pnl, 2) : '-';
    tr.innerHTML = `
      <td class="time">${timeAgo(h.time)}</td>
      <td><span class="pill ${h.event}">${EVENT_ICONS[h.event] || ''} ${h.event}</span></td>
      <td class="coin">${avatarHtml(h.symbol)}${coinName(h.symbol)}</td>
      <td>${h.side ? `<span class="pill ${h.side}">${h.side}</span>` : '-'}</td>
      <td>${fmtNum(h.entry)}</td>
      <td>${fmtNum(h.stop)}</td>
      <td>${fmtNum(h.take_profit)}</td>
      <td class="${pnlClass}">${pnlText}</td>
      <td class="reason">${h.reason || '-'}</td>`;
    tbody.appendChild(tr);
  }
}

function renderHistoryPreview(tbody, rows) {
  tbody.innerHTML = '';
  for (const h of rows.slice(0, 6)) {
    const tr = document.createElement('tr');
    const pnlClass = h.pnl ? (parseFloat(h.pnl) >= 0 ? 'green' : 'red') : '';
    const pnlText = h.pnl ? (parseFloat(h.pnl) >= 0 ? '+' : '') + fmtNum(h.pnl, 2) : '-';
    tr.innerHTML = `
      <td class="time">${timeAgo(h.time)}</td>
      <td><span class="pill ${h.event}">${EVENT_ICONS[h.event] || ''} ${h.event}</span></td>
      <td class="coin">${avatarHtml(h.symbol)}${coinName(h.symbol)}</td>
      <td>${h.side ? `<span class="pill ${h.side}">${h.side}</span>` : '-'}</td>
      <td class="${pnlClass}">${pnlText}</td>`;
    tbody.appendChild(tr);
  }
}

function drawSparkline(history, equityNow) {
  const svg = document.getElementById('sparkline');
  const closed = history.filter(h => h.event === 'exit' && h.pnl).slice().reverse();
  if (closed.length < 2) { svg.innerHTML = ''; return; }
  let running = equityNow - closed.reduce((s, h) => s + parseFloat(h.pnl), 0);
  const points = [running];
  for (const h of closed) { running += parseFloat(h.pnl); points.push(running); }
  const min = Math.min(...points), max = Math.max(...points);
  const range = (max - min) || 1;
  const w = 96, h = 34, pad = 3;
  const step = (w - pad * 2) / (points.length - 1);
  const coords = points.map((v, i) => [pad + i * step, h - pad - (v - min) / range * (h - pad * 2)]);
  const line = coords.map(c => c.join(',')).join(' ');
  const up = points[points.length - 1] >= points[0];
  const color = up ? '#34d399' : '#fb7185';
  const areaPath = `M${coords[0][0]},${h} L` + line + ` L${coords[coords.length - 1][0]},${h} Z`;
  svg.innerHTML = `
    <path d="${areaPath}" fill="${color}" opacity="0.12"></path>
    <polyline points="${line}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></polyline>`;
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

  const wr = data.stats.closed > 0 ? (data.stats.wins / data.stats.closed * 100) : 0;
  document.getElementById('winRate').textContent = data.stats.closed ? wr.toFixed(0) + '%' : '-';
  document.getElementById('winRateSub').textContent = data.stats.closed ? `${data.stats.wins}/${data.stats.closed} trades` : 'no trades yet';
  const ring = document.getElementById('wrRing');
  const circumference = 132;
  ring.setAttribute('stroke-dashoffset', String(circumference - (wr / 100) * circumference));
  ring.setAttribute('stroke', wr >= 50 ? '#34d399' : '#fb7185');

  const pnlEl = document.getElementById('totalPnl');
  pnlEl.textContent = (data.stats.total_pnl >= 0 ? '+' : '') + fmtNum(data.stats.total_pnl, 2) + ' USDT';
  pnlEl.className = 'value ' + (data.stats.total_pnl >= 0 ? 'green' : 'red');

  const todayEl = document.getElementById('todayPnl');
  todayEl.textContent = (data.stats.today_pnl >= 0 ? '+' : '') + fmtNum(data.stats.today_pnl, 2) + ' USDT';
  todayEl.className = 'value ' + (data.stats.today_pnl >= 0 ? 'green' : 'red');

  drawSparkline(data.history, data.equity);

  // positions: overview preview (top 3) + full page
  renderPositions(document.getElementById('posListPreview'), data.positions.slice(0, 3));
  renderPositions(document.getElementById('posListFull'), data.positions);
  document.getElementById('positionsEmptyPreview').style.display = data.positions.length ? 'none' : 'block';
  document.getElementById('positionsEmptyFull').style.display = data.positions.length ? 'none' : 'block';

  // movers: overview preview (top 8) + full page
  renderMovers(document.getElementById('moverListPreview'), data.movers.slice(0, 8));
  renderMovers(document.getElementById('moverListFull'), data.movers);
  document.getElementById('moversEmptyPreview').style.display = data.movers.length ? 'none' : 'block';
  document.getElementById('moversEmptyFull').style.display = data.movers.length ? 'none' : 'block';
  document.getElementById('scanCountLabel').textContent = data.movers.length ? data.movers.length + ' coins scanned' : '';

  // history: overview preview (compact, 6 rows) + full page
  const histDesc = data.history.slice().reverse();
  renderHistoryPreview(document.querySelector('#historyTablePreview tbody'), histDesc);
  renderHistoryFull(document.querySelector('#historyTableFull tbody'), histDesc);
  document.getElementById('historyEmptyPreview').style.display = data.history.length ? 'none' : 'block';
  document.getElementById('historyEmptyFull').style.display = data.history.length ? 'none' : 'block';
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


def fetch_exchange_closed_trades(limit=200):
    """Realized PnL per closed trade, sourced from the exchange itself rather than the
    local trades_log.csv. Render's free tier disk isn't guaranteed to survive a
    redeploy, which was silently wiping the trade log and its PnL/history - the
    exchange's own income record persists regardless of what happens to this process."""
    try:
        income = exchange.fapiPrivateGetIncome({"incomeType": "REALIZED_PNL", "limit": limit})
    except Exception as e:
        print(f"[warn] could not fetch income history: {e}", flush=True)
        return []

    income.sort(key=lambda x: int(x["time"]))
    market_by_id = {m["id"]: m["symbol"] for m in exchange.markets.values()}

    grouped = []
    for item in income:
        t = int(item["time"])
        pnl = float(item["income"])
        sid = item["symbol"]
        # Binance can split one close into several partial-fill income entries -
        # collapse ones for the same symbol within 5s into a single trade row
        if grouped and grouped[-1]["symbol_id"] == sid and t - grouped[-1]["time"] < 5000:
            grouped[-1]["pnl"] += pnl
            grouped[-1]["time"] = t
        else:
            grouped.append({"symbol_id": sid, "pnl": pnl, "time": t})

    rows = []
    for g in grouped:
        rows.append({
            "time": datetime.fromtimestamp(g["time"] / 1000, tz=timezone.utc).isoformat(),
            "event": "exit",
            "symbol": market_by_id.get(g["symbol_id"], g["symbol_id"]),
            "side": "", "entry": "", "stop": "", "take_profit": "",
            "pnl": round(g["pnl"], 4),
            "reason": "",
        })
    return rows


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

    # entry/trail_stop events only come from the local log (no clean exchange
    # equivalent) - exits come from the exchange's own income record so they survive
    # this process restarting or Render wiping the disk on redeploy
    local_rows = []
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, newline="") as f:
            local_rows = [r for r in csv.DictReader(f) if r.get("event") in ("entry", "trail_stop")]

    exit_rows = fetch_exchange_closed_trades()
    history = sorted(local_rows + exit_rows, key=lambda r: r["time"])

    closed_pnls = [r["pnl"] for r in exit_rows]
    today = datetime.now(timezone.utc).date().isoformat()
    today_pnl = sum(r["pnl"] for r in exit_rows if r["time"][:10] == today)
    stats = {
        "closed": len(closed_pnls),
        "wins": len([p for p in closed_pnls if p > 0]),
        "total_pnl": sum(closed_pnls),
        "today_pnl": today_pnl,
    }

    return jsonify({
        "mode": "TESTNET/DEMO" if cfg.testnet else "LIVE",
        "equity": equity,
        "positions": positions,
        "movers": movers,
        "scan_time": scan_time,
        "history": history[-30:],
        "stats": stats,
    })


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    host = "0.0.0.0" if os.getenv("PORT") else "127.0.0.1"
    print(f"Dashboard running at http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
