import csv
import dataclasses
import json
import os
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, jsonify, render_template_string, request, Response

from bot.config import Config
from bot.exchange import build_exchange
from bot.live import LiveTrader

app = Flask(__name__)
_base_cfg = Config.load("config.yaml")


def _build_account(name: str, testnet: bool, api_key: str, api_secret: str) -> dict:
    """One trading account (demo or live) - its own exchange connection, its own
    on-disk state files, completely independent of the other account. If the keys
    for it aren't set, it's simply left unconfigured rather than failing startup -
    the dashboard shows a plain "not configured" state for that account instead."""
    if not api_key or not api_secret:
        return {"name": name, "configured": False, "reason": "API key/secret not set"}
    cfg = dataclasses.replace(_base_cfg, testnet=testnet, api_key=api_key, api_secret=api_secret)
    try:
        exchange = build_exchange(cfg)
    except Exception as e:
        return {"name": name, "configured": False, "reason": f"could not connect: {e}"}
    return {
        "name": name,
        "configured": True,
        "reason": "",
        "cfg": cfg,
        "exchange": exchange,
        "status_path": f"status_{name}.json",
        "log_path": f"trades_log_{name}.csv",
        "reset_marker_path": f"history_reset_at_{name}.json",
    }


# two fully independent accounts, switchable from the dashboard: demo (Binance Demo
# Trading, paper money) and live (real Binance futures, real money). Live only comes
# up if BINANCE_API_KEY_LIVE/BINANCE_API_SECRET_LIVE are actually set - until then it
# just shows as unconfigured, same as any other missing account.
ACCOUNTS = {
    "demo": _build_account("demo", True, os.getenv("BINANCE_API_KEY", ""), os.getenv("BINANCE_API_SECRET", "")),
    "live": _build_account("live", False, os.getenv("BINANCE_API_KEY_LIVE", ""), os.getenv("BINANCE_API_SECRET_LIVE", "")),
}


def _get_account(name: str):
    return ACCOUNTS.get(name) or ACCOUNTS["demo"]


def _history_cutoff(reset_marker_path: str) -> str:
    """ISO timestamp set by /api/reset-history - history/stats before this point are
    hidden from the dashboard. Doesn't touch the exchange's own records (there's no
    way to erase those, and no need to) - this just draws a line for what the site
    counts as "since we started this test run"."""
    if os.path.exists(reset_marker_path):
        try:
            with open(reset_marker_path) as f:
                return json.load(f).get("since", "")
        except Exception:
            pass
    return ""

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


_bot_threads_started = False


def start_bot_thread_once():
    global _bot_threads_started
    if _bot_threads_started:
        return
    _bot_threads_started = True
    for name, acct in ACCOUNTS.items():
        if not acct["configured"]:
            print(f"[dashboard] {name} account not configured ({acct['reason']}) - skipping", flush=True)
            continue
        # extra, deliberate opt-in for real money beyond just having keys set - mirrors
        # run_live.py's --i-understand-the-risk gate for the CLI entrypoint
        if not acct["cfg"].testnet and os.getenv("ENABLE_LIVE_TRADING", "false").lower() != "true":
            print(f"[dashboard] {name} account has keys but ENABLE_LIVE_TRADING is not 'true' - "
                  f"refusing to trade real money until that's set explicitly", flush=True)
            continue
        trader = LiveTrader(acct["cfg"], account=name)
        t = threading.Thread(target=trader.run_forever, daemon=True, name=f"reversal-bot-{name}")
        t.start()
        print(f"[dashboard] {name} bot trading thread started", flush=True)


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
    --bg: #060707;
    --bg-soft: #0b0d0d;
    --card: #101312;
    --border: #1d2321;
    --text: #f1f3f0;
    --muted: #838d87;
    --faint: #4c5450;
    --green: #2dd4a7;
    --green-soft: rgba(45, 212, 167, .13);
    --red: #fb7185;
    --red-soft: rgba(251, 113, 133, .12);
    --accent: #2dd4a7;
    --accent2: #14b8a6;
    --accent-soft: rgba(45, 212, 167, .13);
    --gold: #f0b93a;
    --gold-soft: rgba(240, 185, 58, .13);
    --amber: #f0b93a;
    --radius: 16px;
    --sidebar-w: 216px;
  }
  * { box-sizing: border-box; }
  html, body { max-width: 100%; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    background: var(--bg); color: var(--text); margin: 0;
    -webkit-font-smoothing: antialiased;
  }
  .glow {
    position: fixed; inset: 0; z-index: 0; pointer-events: none;
    background:
      radial-gradient(680px 420px at 14% -6%, rgba(45,212,167,.14), transparent 60%),
      radial-gradient(620px 380px at 88% 4%, rgba(240,185,58,.10), transparent 60%);
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
    background: linear-gradient(135deg, var(--accent), var(--accent2)); flex-shrink: 0; color: #06110d;
    box-shadow: 0 0 20px rgba(45,212,167,.35);
  }
  .brand .logo svg { width: 17px; height: 17px; }
  .brand .name { font-size: 15.5px; font-weight: 700; letter-spacing: -.01em; }
  .navgroup { display: flex; flex-direction: column; gap: 3px; }
  .navbtn {
    display: flex; align-items: center; gap: 10px; width: 100%; text-align: left;
    background: transparent; border: none; color: var(--muted); font-size: 13.5px; font-weight: 600;
    padding: 10px 12px; border-radius: 10px; cursor: pointer; font-family: inherit;
    transition: background .12s, color .12s;
  }
  .navbtn .ic { width: 18px; height: 18px; flex-shrink: 0; }
  .navbtn .ic svg { width: 100%; height: 100%; }
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
  .modebadge.live { color: var(--red); background: var(--red-soft); border-color: rgba(251,113,133,.35); }

  /* ---------- demo/live account toggle ---------- */
  .acct-toggle { display: flex; gap: 4px; background: rgba(255,255,255,.04); border: 1px solid var(--border);
    border-radius: 10px; padding: 3px; margin: 0 6px 18px; }
  .acct-btn { flex: 1; background: transparent; border: none; color: var(--muted); font-family: inherit;
    font-size: 11.5px; font-weight: 700; padding: 7px 0; border-radius: 7px; cursor: pointer; transition: background .12s, color .12s; }
  .acct-btn.active[data-account="demo"] { background: var(--accent-soft); color: var(--accent); }
  .acct-btn.active[data-account="live"] { background: var(--red-soft); color: var(--red); }

  /* ---------- not-configured state (e.g. live account has no API keys yet) ---------- */
  main.blocked .page { display: none !important; }
  .notconfigured-wrap { display: none; padding: 70px 20px; text-align: center; }
  .notconfigured-wrap.show { display: block; }
  .notconfigured .ico { font-size: 32px; margin-bottom: 14px; }
  .notconfigured h2 { font-size: 17px; margin: 0 0 8px; }
  .notconfigured p { color: var(--muted); font-size: 13px; max-width: 420px; margin: 0 auto; line-height: 1.7; }
  .notconfigured code { background: rgba(255,255,255,.06); padding: 2px 6px; border-radius: 5px; font-size: 12px; }
  .resetbtn {
    display: block; width: 100%; margin-top: 10px; background: transparent; border: 1px solid var(--border);
    color: var(--muted); font-family: inherit; font-size: 11px; font-weight: 650; padding: 7px 0;
    border-radius: 8px; cursor: pointer; transition: border-color .12s, color .12s;
  }
  .resetbtn:hover { border-color: var(--red); color: var(--red); }

  /* ---------- main ---------- */
  main { flex: 1; min-width: 0; padding: 24px 26px 90px; }
  h1.pagetitle { font-size: 19px; font-weight: 700; margin: 0 0 18px; letter-spacing: -.01em; }

  .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 18px; }
  .stat {
    background: linear-gradient(180deg, var(--card), var(--bg-soft)); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 15px 17px; min-width: 0; position: relative; overflow: hidden;
    box-shadow: 0 8px 20px rgba(0,0,0,.2), inset 0 1px 0 rgba(255,255,255,.02);
  }
  .stat .label { font-size: 11px; font-weight: 650; text-transform: uppercase; letter-spacing: .06em; color: var(--faint); margin-bottom: 7px; }
  .stat .value { font-size: 21px; font-weight: 700; letter-spacing: -.01em; word-break: break-word; font-variant-numeric: tabular-nums; }
  .stat .sub { font-size: 11.5px; color: var(--muted); margin-top: 3px; }
  .stat.winrate { display: flex; align-items: center; gap: 12px; }
  .stat.winrate .ring-wrap { position: relative; width: 52px; height: 52px; flex-shrink: 0; }
  .stat.winrate .ring-wrap .pct { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    font-size: 12px; font-weight: 700; }

  /* ---------- hero equity chart ---------- */
  .hero {
    background: linear-gradient(165deg, var(--card), var(--bg-soft)); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 20px 22px 6px; margin-bottom: 16px; position: relative; overflow: hidden;
  }
  .hero-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 6px; }
  .hero .label { font-size: 11.5px; font-weight: 650; text-transform: uppercase; letter-spacing: .06em; color: var(--faint); margin-bottom: 6px; }
  .hero .value { font-size: 32px; font-weight: 750; letter-spacing: -.01em; font-variant-numeric: tabular-nums; }
  .hero .change { display: inline-flex; align-items: center; gap: 4px; font-size: 12.5px; font-weight: 700; padding: 4px 10px;
    border-radius: 999px; margin-top: 8px; }
  .hero .change.green { background: var(--green-soft); }
  .hero .change.red { background: var(--red-soft); }
  .hero-chart-wrap { position: relative; margin: 6px -6px 0; }
  #equityChart { width: 100%; height: 132px; display: block; touch-action: none; }
  .chart-tooltip {
    position: absolute; pointer-events: none; background: #1a1d1a; border: 1px solid var(--border);
    border-radius: 8px; padding: 5px 9px; font-size: 11.5px; font-weight: 650; color: var(--text);
    transform: translate(-50%, -115%); white-space: nowrap; opacity: 0; transition: opacity .1s; z-index: 3;
    box-shadow: 0 6px 18px rgba(0,0,0,.4);
  }

  /* ---------- allocation donut ---------- */
  .donut-card { display: flex; align-items: center; gap: 16px; }
  .donut-wrap { position: relative; width: 92px; height: 92px; flex-shrink: 0; }
  .donut-wrap .center { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; }
  .donut-wrap .center .n { font-size: 15px; font-weight: 750; }
  .donut-wrap .center .l { font-size: 9px; color: var(--faint); text-transform: uppercase; letter-spacing: .04em; }
  .donut-legend { display: flex; flex-direction: column; gap: 6px; font-size: 12px; min-width: 0; }
  .donut-legend .row { display: flex; align-items: center; gap: 7px; }
  .donut-legend .sw { width: 8px; height: 8px; border-radius: 3px; flex-shrink: 0; }
  .donut-legend .name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); font-weight: 600; }
  .donut-legend .amt { font-weight: 700; font-variant-numeric: tabular-nums; }

  /* ---------- daily pnl bars ---------- */
  .barchart { display: flex; align-items: flex-end; gap: 8px; height: 92px; padding: 6px 4px 0; }
  .barcol { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; gap: 6px; height: 100%; }
  .barcol .track { width: 100%; max-width: 26px; flex: 1; display: flex; align-items: flex-end; }
  .barcol .fill { width: 100%; border-radius: 5px 5px 2px 2px; min-height: 3px; transition: height .2s; }
  .barcol .fill.up { background: linear-gradient(180deg, var(--green), #159c7c); }
  .barcol .fill.down { background: linear-gradient(180deg, var(--red), #d94860); }
  .barcol .fill.zero { background: var(--border); }
  .barcol .daylabel { font-size: 10px; color: var(--faint); font-weight: 650; }

  .green { color: var(--green); } .red { color: var(--red); } .muted { color: var(--muted); }

  .pill { display: inline-flex; align-items: center; gap: 3px; padding: 2px 9px; border-radius: 999px; font-size: 11px; font-weight: 700; letter-spacing: .02em; white-space: nowrap; }
  .pill.long { background: var(--green-soft); color: var(--green); }
  .pill.short { background: var(--red-soft); color: var(--red); }
  .pill.entry { background: var(--accent-soft); color: var(--accent); }
  .pill.exit { background: rgba(124,130,150,.15); color: var(--muted); }
  .pill.trail_stop { background: rgba(251,191,36,.12); color: var(--amber); }
  .pill.strategy { background: rgba(255,255,255,.06); color: var(--muted); font-weight: 650; }
  .pill.strategy.ob { background: var(--gold-soft); color: var(--gold); }

  .avatar {
    width: 30px; height: 30px; border-radius: 9px; flex-shrink: 0; display: flex; align-items: center; justify-content: center;
    font-size: 11.5px; font-weight: 750; color: #0b0c10; position: relative; overflow: hidden;
  }
  .avatar .avatar-img {
    position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; padding: 4px;
    box-sizing: border-box; background: #fff;
  }

  .panel {
    background: linear-gradient(180deg, var(--card), var(--bg-soft)); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 18px 18px 8px; margin-bottom: 16px;
    box-shadow: 0 10px 26px rgba(0,0,0,.22), inset 0 1px 0 rgba(255,255,255,.02);
  }
  .panel-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
  .panel-head h2 { font-size: 13.5px; font-weight: 700; margin: 0; display: flex; align-items: center; gap: 7px; }
  .panel-head h2 .ic { width: 15px; height: 15px; flex-shrink: 0; color: var(--muted); }
  .panel-head h2 .ic svg { width: 100%; height: 100%; }
  .panel-head .count { font-size: 11.5px; color: var(--faint); font-weight: 600; }
  .panel-head a.viewall { font-size: 11.5px; color: var(--accent); text-decoration: none; font-weight: 650; cursor: pointer; }
  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start; }

  .poslist { display: flex; flex-direction: column; gap: 8px; padding-bottom: 10px; }
  .posrow { display: flex; align-items: center; justify-content: space-between; gap: 10px;
    background: var(--bg-soft); border: 1px solid var(--border); border-radius: 12px; padding: 10px 13px;
    box-shadow: 0 4px 14px rgba(0,0,0,.18); transition: border-color .12s; }
  .posrow:hover { border-color: rgba(45,212,167,.3); }
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
  td.reason { color: var(--muted); max-width: 320px; overflow: hidden; text-overflow: ellipsis; cursor: help; }
  td.time { color: var(--faint); font-variant-numeric: tabular-nums; }

  .empty { color: var(--faint); font-size: 13px; text-align: center; padding: 26px 0; }
  .empty .ico { width: 30px; height: 30px; margin: 0 auto 8px; opacity: .35; display: block; }

  /* ---------- order book ladder ---------- */
  tr.clickable { cursor: pointer; }
  .moverrow.clickable { cursor: pointer; border-radius: 8px; }
  .moverrow.clickable:hover, tr.clickable:hover { background: rgba(255,255,255,.03); }
  .obladder { font-variant-numeric: tabular-nums; font-size: 12px; }
  .obmid {
    display: flex; align-items: center; justify-content: center; gap: 8px;
    padding: 6px 0; font-size: 14px; font-weight: 750; color: var(--text);
    border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); margin: 1px 0;
  }
  .obrow { position: relative; display: flex; justify-content: space-between; padding: 2.5px 10px; border-radius: 5px; margin-bottom: 1px; overflow: hidden; }
  .obrow .obbar { position: absolute; top: 0; bottom: 0; right: 0; z-index: 0; opacity: .16; }
  .obrow.ask .obbar { background: var(--red); }
  .obrow.bid .obbar { background: var(--green); }
  .obrow .obprice { position: relative; z-index: 1; font-weight: 650; }
  .obrow.ask .obprice { color: var(--red); }
  .obrow.bid .obprice { color: var(--green); }
  .obrow .obqty { position: relative; z-index: 1; color: var(--muted); }
  .obrow.wall { outline: 1px solid var(--amber); background: rgba(240,185,58,.06); }
  .obrow.wall .obqty::after { content: ' WALL'; color: var(--amber); font-weight: 700; font-size: 10px; }
  .oblabel { font-size: 10.5px; color: var(--faint); text-transform: uppercase; letter-spacing: .05em; padding: 2px 10px 4px; }

  /* ---------- strategy-2 scanner (order-book imbalance watchlist) ---------- */
  .obscan-desc { font-size: 12px; color: var(--muted); margin: -4px 0 12px; line-height: 1.5; }
  .watchlist { display: flex; flex-direction: column; }
  .universe-tags { display: flex; flex-wrap: wrap; gap: 6px; padding-bottom: 12px; }
  .universe-tags .tag { font-size: 11px; font-weight: 650; color: var(--muted); background: rgba(255,255,255,.05);
    border: 1px solid var(--border); border-radius: 7px; padding: 4px 9px 4px 4px; display: flex; align-items: center; gap: 5px; }
  .universe-tags .tag .avatar { width: 16px; height: 16px; font-size: 7.5px; border-radius: 5px; }
  .watchrow { display: flex; align-items: center; gap: 10px; background: var(--bg-soft); border: 1px solid var(--border);
    border-radius: 12px; padding: 9px 13px; margin-bottom: 8px; }
  .watchrow .sym { font-weight: 700; font-size: 13px; width: 70px; flex-shrink: 0; }
  .watchrow .progress-wrap { flex: 1; display: flex; align-items: center; gap: 8px; }
  .watchrow .progress-track { flex: 1; height: 6px; border-radius: 4px; background: rgba(124,130,150,.15); overflow: hidden; }
  .watchrow .progress-fill { height: 100%; border-radius: 4px; }
  .watchrow .progress-fill.long { background: linear-gradient(90deg, #10b981, var(--green)); }
  .watchrow .progress-fill.short { background: linear-gradient(90deg, #e11d48, var(--red)); }
  .watchrow .progress-label { font-size: 11px; color: var(--faint); font-weight: 650; width: 60px; text-align: right; flex-shrink: 0; }

  /* ---------- order-book walls grid ---------- */
  .wallsgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 10px; padding-bottom: 10px; }
  .wallcard {
    background: var(--bg-soft); border: 1px solid var(--border); border-radius: 12px; padding: 11px 13px;
    cursor: pointer; transition: border-color .12s; box-shadow: 0 4px 14px rgba(0,0,0,.18);
  }
  .wallcard:hover { border-color: rgba(45,212,167,.3); }
  .wallcard .head { display: flex; align-items: center; gap: 8px; margin-bottom: 9px; }
  .wallcard .head .sym { font-weight: 700; font-size: 13px; }
  .wallcard .head .price { margin-left: auto; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
  .wallcard .row { display: flex; justify-content: space-between; font-size: 11.5px; padding: 3px 0; }
  .wallcard .row .k { color: var(--faint); }
  .wallcard .row .v { font-variant-numeric: tabular-nums; font-weight: 650; }

  /* ---------- history feed (card list, not a dash-heavy table) ---------- */
  .histlist { display: flex; flex-direction: column; gap: 7px; padding-bottom: 10px; }
  .histrow { display: flex; align-items: center; gap: 10px; background: var(--bg-soft); border: 1px solid var(--border);
    border-radius: 12px; padding: 9px 13px; box-shadow: 0 4px 14px rgba(0,0,0,.18); }
  .histrow .left { display: flex; align-items: center; gap: 10px; min-width: 0; flex: 1; }
  .histrow .sym { font-weight: 700; font-size: 13.5px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
  .histrow .meta { font-size: 11px; color: var(--muted); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .histrow .right { text-align: right; flex-shrink: 0; }
  .histrow .pnl { font-weight: 750; font-size: 14px; font-variant-numeric: tabular-nums; }
  .histrow .time { font-size: 10.5px; color: var(--faint); margin-top: 1px; }

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
    .bottomnav button .ic { width: 20px; height: 20px; }
    .bottomnav button .ic svg { width: 100%; height: 100%; }
    .bottomnav button.active { color: var(--accent); }
    .stats { grid-template-columns: 1fr 1fr; gap: 8px; }
    .stat { padding: 12px 13px; border-radius: 12px; }
    .stat .value { font-size: 17px; }
    .two-col { grid-template-columns: 1fr; }
    .panel { padding: 14px 14px 6px; border-radius: 13px; }
    h1.pagetitle { font-size: 17px; }
    .hero { padding: 16px 16px 4px; border-radius: 13px; }
    .hero .value { font-size: 26px; }
    #equityChart { height: 108px; }
    .donut-card { flex-direction: column; align-items: flex-start; }
  }
</style>
</head>
<body>
<div class="glow"></div>
<div class="shell">
  <aside class="sidebar">
    <div class="brand">
      <div class="logo"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="3"/><rect x="9" y="9" width="6" height="6"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3"/></svg></div>
      <div class="name">reversal_bot</div>
    </div>
    <div class="acct-toggle" id="acctToggle">
      <button class="acct-btn active" data-account="demo">Demo</button>
      <button class="acct-btn" data-account="live">Live</button>
    </div>
    <div class="navgroup">
      <button class="navbtn active" data-page="overview"><span class="ic">__IC_HOME__</span>Overview</button>
      <button class="navbtn" data-page="positions"><span class="ic">__IC_POSITIONS__</span>Positions</button>
      <button class="navbtn" data-page="scan"><span class="ic">__IC_SCAN__</span>Market Scan</button>
      <button class="navbtn" data-page="history"><span class="ic">__IC_HISTORY__</span>History</button>
    </div>
    <div class="sidebar-foot">
      <div class="live"><span class="dot"></span><span id="lastUpdate">connecting…</span></div>
      <span class="modebadge" id="mode">...</span>
      <button class="resetbtn" id="resetHistoryBtn" title="Clear stats/history and start counting from now">Reset stats</button>
    </div>
  </aside>

  <main>
    <div class="notconfigured-wrap" id="notConfiguredBanner">
      <div class="notconfigured">
        <div class="ico">🔒</div>
        <h2>This account isn't set up yet</h2>
        <p id="notConfiguredReason">Add <code>BINANCE_API_KEY_LIVE</code> and <code>BINANCE_API_SECRET_LIVE</code> as environment variables and redeploy.</p>
      </div>
    </div>
    <section id="page-overview" class="page active">
      <div class="hero">
        <div class="hero-top">
          <div>
            <div class="label">Equity</div>
            <div class="value" id="equity">-</div>
            <span class="change" id="equityChange">-</span>
          </div>
        </div>
        <div class="hero-chart-wrap">
          <svg id="equityChart" viewBox="0 0 600 132" preserveAspectRatio="none"></svg>
          <div class="chart-tooltip" id="chartTooltip"></div>
        </div>
      </div>

      <div class="stats">
        <div class="stat"><div class="label">Open</div><div class="value" id="posCount">-</div></div>
        <div class="stat winrate">
          <div class="ring-wrap">
            <svg width="52" height="52" viewBox="0 0 52 52">
              <circle cx="26" cy="26" r="21" fill="none" stroke="rgba(124,130,150,.15)" stroke-width="6"/>
              <circle id="wrRing" cx="26" cy="26" r="21" fill="none" stroke="#2dd4a7" stroke-width="6"
                stroke-dasharray="132" stroke-dashoffset="132" stroke-linecap="round" transform="rotate(-90 26 26)"/>
            </svg>
            <div class="pct" id="winRate">-</div>
          </div>
          <div><div class="label">Win rate</div><div class="sub" id="winRateSub">-</div></div>
        </div>
        <div class="stat"><div class="label">Realized P&amp;L</div><div class="value" id="totalPnl">-</div><div class="sub" id="totalPnlSub"></div></div>
        <div class="stat"><div class="label">Today</div><div class="value" id="todayPnl">-</div></div>
      </div>

      <div class="two-col">
        <div class="panel donut-card">
          <div>
            <div class="panel-head" style="margin-bottom:14px"><h2>Margin allocation</h2></div>
            <div class="donut-wrap">
              <svg width="92" height="92" viewBox="0 0 92 92" id="allocDonut"></svg>
              <div class="center"><div class="n" id="allocFreePct">-</div><div class="l">free</div></div>
            </div>
          </div>
          <div class="donut-legend" id="allocLegend"></div>
        </div>
        <div class="panel">
          <div class="panel-head"><h2>Daily P&amp;L (7d)</h2></div>
          <div class="barchart" id="dailyBars"></div>
        </div>
      </div>

      <div class="two-col">
        <div class="panel">
          <div class="panel-head"><h2><span class="ic">__IC_POSITIONS__</span> Open positions</h2><a class="viewall" data-goto="positions">View all</a></div>
          <div class="poslist" id="posListPreview"></div>
          <div class="empty" id="positionsEmptyPreview">__ICO_SLEEP__ No open positions</div>
        </div>
        <div class="panel">
          <div class="panel-head"><h2><span class="ic">__IC_SCAN__</span> Last scan</h2><a class="viewall" data-goto="scan">View all</a></div>
          <div class="moverlist" id="moverListPreview"></div>
          <div class="empty" id="moversEmptyPreview" style="display:none">__ICO_INBOX__ No data yet</div>
        </div>
      </div>
      <div class="panel">
        <div class="panel-head"><h2><span class="ic">__IC_HISTORY__</span> Recent activity</h2><a class="viewall" data-goto="history">View all</a></div>
        <div class="table-wrap"><table id="historyTablePreview"><thead><tr><th>Time</th><th>Event</th><th>Coin</th><th>Side</th><th>PnL</th></tr></thead><tbody></tbody></table></div>
        <div class="empty" id="historyEmptyPreview" style="display:none">__ICO_FILE__ No trades yet</div>
      </div>
    </section>

    <section id="page-positions" class="page">
      <h1 class="pagetitle">Open Positions</h1>
      <div class="panel">
        <div class="poslist" id="posListFull"></div>
        <div class="empty" id="positionsEmptyFull">__ICO_SLEEP__ No open positions right now</div>
      </div>
    </section>

    <section id="page-scan" class="page">
      <h1 class="pagetitle">Market Scan</h1>
      <div class="two-col">
        <div class="panel">
          <div class="panel-head"><h2>Coins with the biggest recent move</h2><span class="count" id="scanCountLabel"></span></div>
          <div class="moverlist" id="moverListFull"></div>
          <div class="empty" id="moversEmptyFull" style="display:none">__ICO_INBOX__ No data yet</div>
          <div class="oblabel" id="scanClickHint" style="display:none">Click a coin to open its order book &rarr;</div>
        </div>
        <div class="panel">
          <div class="panel-head"><h2 id="obTitle">Order Book</h2><span class="count" id="obSubtitle">click a coin</span></div>
          <div class="obladder" id="obLadder">
            <div class="empty" id="obEmpty">__ICO_INBOX__ Click a coin on the left to see its live order book</div>
          </div>
        </div>
      </div>
      <div class="panel">
        <div class="panel-head"><h2>Order-book walls (summary)</h2><span class="count">large resting orders, top movers - click a card to open its ladder</span></div>
        <div class="wallsgrid" id="wallsGrid"></div>
        <div class="empty" id="wallsEmpty" style="display:none">__ICO_INBOX__ No significant walls detected right now</div>
      </div>

      <div class="panel" id="obScanPanel">
        <div class="panel-head"><h2><span class="pill strategy ob">Strategy 2</span> Order-book scanner</h2><span class="count" id="obScanCountLabel"></span></div>
        <div class="obscan-desc">Quiet, liquid coins that haven't made a big move — a separate universe from the movers above. Watches for a strong order-book imbalance that holds for several polls in a row before it counts as a signal.</div>
        <div class="watchlist" id="obWatchList"></div>
        <div class="empty" id="obWatchEmpty">__ICO_INBOX__ No imbalance building right now — scanning quietly</div>
        <div class="oblabel">Universe being scanned</div>
        <div class="universe-tags" id="obUniverseTags"></div>
      </div>
    </section>

    <section id="page-history" class="page">
      <h1 class="pagetitle">Trade History</h1>
      <div class="panel">
        <div class="histlist" id="historyListFull"></div>
        <div class="empty" id="historyEmptyFull" style="display:none">__ICO_FILE__ No trades yet</div>
      </div>
    </section>
  </main>
</div>

<nav class="bottomnav">
  <button class="active" data-page="overview"><span class="ic">__IC_HOME__</span>Overview</button>
  <button data-page="positions"><span class="ic">__IC_POSITIONS__</span>Positions</button>
  <button data-page="scan"><span class="ic">__IC_SCAN__</span>Scan</button>
  <button data-page="history"><span class="ic">__IC_HISTORY__</span>History</button>
</nav>

<script>
function coinName(sym) { return (sym || '').split('/')[0]; }

// ---------- demo/live account switch (persisted per browser via localStorage) ----------
let currentAccount = 'demo';
try { currentAccount = localStorage.getItem('rb_account') || 'demo'; } catch (e) {}

function setAccountButtons() {
  document.querySelectorAll('.acct-btn').forEach(b => b.classList.toggle('active', b.dataset.account === currentAccount));
}
setAccountButtons();
document.querySelectorAll('.acct-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    if (btn.dataset.account === currentAccount) return;
    currentAccount = btn.dataset.account;
    try { localStorage.setItem('rb_account', currentAccount); } catch (e) {}
    setAccountButtons();
    selectedObSymbol = null;
    refresh();
  });
});

const STRATEGY_LABELS = { mean_reversion: 'Mean Reversion', orderbook_imbalance: 'Order Book', unknown: 'Unknown' };
function strategyPill(code) {
  if (!code) return '';
  const label = STRATEGY_LABELS[code] || code;
  const cls = code === 'orderbook_imbalance' ? ' ob' : '';
  return `<span class="pill strategy${cls}" title="Strategy that opened this trade">${label}</span>`;
}

const PALETTE = ['#6ea8fe','#a78bfa','#34d399','#fbbf24','#fb7185','#38bdf8','#f472b6','#4ade80','#fb923c','#c084fc'];
function avatarColor(sym) {
  let h = 0;
  for (let i = 0; i < sym.length; i++) h = (h * 31 + sym.charCodeAt(i)) >>> 0;
  return PALETTE[h % PALETTE.length];
}
// real coin logos from a public icon set, with an automatic fallback to a colored
// letter badge underneath for anything not in that set (most of the exotic/meme
// coins this bot actually trades won't have a real icon available)
function avatarHtml(sym) {
  const name = coinName(sym);
  const letters = name.replace(/[^A-Za-z0-9]/g, '').slice(0, 2).toUpperCase() || '?';
  const iconSlug = name.replace(/[^A-Za-z0-9]/g, '').toLowerCase();
  const iconUrl = `https://cdn.jsdelivr.net/npm/cryptocurrency-icons@0.18.1/svg/color/${iconSlug}.svg`;
  return `<div class="avatar" style="background:${avatarColor(name)}">${letters}<img class="avatar-img" src="${iconUrl}" alt="" onerror="this.remove()"></div>`;
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
          <div class="sym">${coinName(p.symbol)} <span class="pill ${p.side || ''}" style="margin-left:4px">${(p.side || '?').toUpperCase()}</span> ${strategyPill(p.strategy)}</div>
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
    row.className = 'moverrow clickable';
    row.dataset.symbol = m[0];
    const up = m[1] >= 0;
    const width = Math.min(100, Math.abs(m[1]) / maxAbs * 100);
    row.innerHTML = `
      <div class="sym">${coinName(m[0])}</div>
      <div class="bar-track"><div class="bar ${up ? 'up' : 'down'}" style="width:${width}%"></div></div>
      <div class="pct ${up ? 'green' : 'red'}">${up ? '+' : ''}${m[1].toFixed(1)}%</div>`;
    container.appendChild(row);
  }
}

function renderWalls(container, walls) {
  container.innerHTML = '';
  const fmtWall = (wallPrice, price) => {
    if (wallPrice === null || wallPrice === undefined) return '<span class="muted">-</span>';
    const distPct = ((wallPrice - price) / price * 100);
    const cls = distPct >= 0 ? 'green' : 'red';
    return `${fmtNum(wallPrice)} <span class="${cls}">(${distPct >= 0 ? '+' : ''}${distPct.toFixed(1)}%)</span>`;
  };
  for (const w of walls) {
    const card = document.createElement('div');
    card.className = 'wallcard';
    card.dataset.symbol = w.symbol;
    card.innerHTML = `
      <div class="head">${avatarHtml(w.symbol)}<span class="sym">${coinName(w.symbol)}</span><span class="price">${fmtNum(w.price)}</span></div>
      <div class="row"><span class="k">Support</span><span class="v">${fmtWall(w.bid_wall, w.price)}</span></div>
      <div class="row"><span class="k">Resistance</span><span class="v">${fmtWall(w.ask_wall, w.price)}</span></div>`;
    container.appendChild(card);
  }
}

// ---------- order-book ladder (click any coin row to open it) ----------
let selectedObSymbol = null;

function renderOrderbookLadder(data) {
  const wrap = document.getElementById('obLadder');
  if (!data || data.error || (!data.bids.length && !data.asks.length)) {
    wrap.innerHTML = '<div class="empty">__ICO_INBOX__ No order book data available</div>';
    return;
  }
  const allQty = [...data.bids, ...data.asks].map(l => l.qty);
  const maxQty = Math.max(1, ...allQty);

  const rowHtml = (level, side) => {
    const w = Math.min(100, level.qty / maxQty * 100);
    return `<div class="obrow ${side}${level.wall ? ' wall' : ''}">
      <div class="obbar" style="width:${w}%"></div>
      <span class="obprice">${fmtNum(level.price)}</span>
      <span class="obqty">${fmtNum(level.qty, 2)}</span>
    </div>`;
  };

  const asksDesc = data.asks.slice().reverse(); // highest ask at top, closest-to-mid just above mid line
  let html = '<div class="oblabel">Asks (sell orders)</div>';
  html += asksDesc.map(l => rowHtml(l, 'ask')).join('');
  html += `<div class="obmid">${fmtNum(data.mid)}</div>`;
  html += '<div class="oblabel">Bids (buy orders)</div>';
  html += data.bids.map(l => rowHtml(l, 'bid')).join('');
  wrap.innerHTML = html;
}

async function loadOrderbook(symbol) {
  document.getElementById('obTitle').textContent = coinName(symbol) + ' Order Book';
  document.getElementById('obSubtitle').textContent = 'live';
  try {
    const res = await fetch('/api/orderbook?symbol=' + encodeURIComponent(symbol) + '&account=' + currentAccount);
    const data = await res.json();
    renderOrderbookLadder(data);
  } catch (e) {
    document.getElementById('obLadder').innerHTML = '<div class="empty">Connection error</div>';
  }
}

function selectSymbolForOrderbook(symbol) {
  selectedObSymbol = symbol;
  gotoPage('scan');
  loadOrderbook(symbol);
}

document.addEventListener('click', (e) => {
  const row = e.target.closest('[data-symbol]');
  if (row) selectSymbolForOrderbook(row.dataset.symbol);
});

const EVENT_ICONS = { entry: '↗', exit: '↘', trail_stop: '🔒' };

// exit rows from the exchange only ever carry symbol/time/pnl - side/entry/stop/tp/
// reason only show up if this process was still running (and tracking that entry)
// when the position closed. Rather than a table full of "-" for what's usually
// missing, only the fields that actually have data get built into the row at all.
function renderHistoryFull(container, rows) {
  container.innerHTML = '';
  for (const h of rows) {
    const row = document.createElement('div');
    row.className = 'histrow';
    const pnlClass = h.pnl ? (parseFloat(h.pnl) >= 0 ? 'green' : 'red') : 'muted';
    const pnlText = h.pnl ? (parseFloat(h.pnl) >= 0 ? '+' : '') + fmtNum(h.pnl, 2) : '-';

    const metaParts = [];
    if (h.entry) metaParts.push(`${fmtNum(h.entry)} entry`);
    if (h.stop) metaParts.push(`${fmtNum(h.stop)} stop`);
    if (h.take_profit) metaParts.push(`${fmtNum(h.take_profit)} tp`);
    if (h.reason) metaParts.push(h.reason);
    const meta = metaParts.length ? metaParts.join(' · ') : 'closed on the exchange - no local entry data for this one';

    row.innerHTML = `
      <div class="left">
        ${avatarHtml(h.symbol)}
        <div style="min-width:0">
          <div class="sym">${coinName(h.symbol)}
            <span class="pill ${h.event}">${EVENT_ICONS[h.event] || ''} ${h.event}</span>
            ${h.side ? `<span class="pill ${h.side}">${h.side}</span>` : ''}
            ${strategyPill(h.strategy)}
          </div>
          <div class="meta" title="${meta.replace(/"/g, '&quot;')}">${meta}</div>
        </div>
      </div>
      <div class="right">
        <div class="pnl ${pnlClass}">${pnlText}</div>
        <div class="time">${timeAgo(h.time)}</div>
      </div>`;
    container.appendChild(row);
  }
}

function renderObWatchlist(container, emptyEl, watching, universeContainer, universe, countLabel) {
  container.innerHTML = '';
  for (const w of watching) {
    const row = document.createElement('div');
    row.className = 'watchrow';
    const pct = Math.min(100, (w.cycles / w.persist_cycles) * 100);
    row.innerHTML = `
      ${avatarHtml(w.symbol)}
      <div class="sym">${coinName(w.symbol)}</div>
      <span class="pill ${w.direction}">${w.direction.toUpperCase()}</span>
      <div class="progress-wrap">
        <div class="progress-track"><div class="progress-fill ${w.direction}" style="width:${pct}%"></div></div>
        <div class="progress-label">${w.cycles}/${w.persist_cycles} cycles</div>
      </div>`;
    container.appendChild(row);
  }
  emptyEl.style.display = watching.length ? 'none' : 'block';

  universeContainer.innerHTML = universe.slice(0, 30).map(s => `<span class="tag">${avatarHtml(s)}${coinName(s)}</span>`).join('')
    || '<span class="tag muted">scanning...</span>';
  countLabel.textContent = universe.length ? universe.length + ' coins scanned' : '';
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

let equityChartPoints = [];

function drawEquityChart(history, equityNow) {
  const svg = document.getElementById('equityChart');
  const closed = history.filter(h => h.event === 'exit' && h.pnl).slice().reverse();
  const W = 600, H = 132, padX = 4, padY = 10;

  let points;
  if (closed.length < 2) {
    points = [equityNow, equityNow]; // not enough data yet - flat line so the panel isn't blank
  } else {
    let running = equityNow - closed.reduce((s, h) => s + parseFloat(h.pnl), 0);
    points = [running];
    for (const h of closed) { running += parseFloat(h.pnl); points.push(running); }
  }

  const min = Math.min(...points), max = Math.max(...points);
  const range = (max - min) || Math.max(1, Math.abs(points[0]) * 0.02);
  const step = points.length > 1 ? (W - padX * 2) / (points.length - 1) : 0;
  const coords = points.map((v, i) => [padX + i * step, H - padY - (v - min) / range * (H - padY * 2)]);
  equityChartPoints = coords.map((c, i) => ({ x: c[0], y: c[1], value: points[i] }));

  const line = coords.map(c => c.join(',')).join(' ');
  const up = points[points.length - 1] >= points[0];
  const color = up ? '#2dd4a7' : '#fb7185';
  const areaPath = `M${coords[0][0]},${H} L` + line + ` L${coords[coords.length - 1][0]},${H} Z`;

  let grid = '';
  for (let i = 1; i < 4; i++) {
    const y = (H - padY * 2) / 4 * i + padY * 0.6;
    grid += `<line x1="0" y1="${y.toFixed(1)}" x2="${W}" y2="${y.toFixed(1)}" stroke="rgba(255,255,255,.05)" stroke-width="1"/>`;
  }

  svg.innerHTML = `
    <defs><linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="${color}" stop-opacity="0.28"/>
      <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
    </linearGradient></defs>
    ${grid}
    <path d="${areaPath}" fill="url(#areaGrad)"></path>
    <polyline points="${line}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"></polyline>
    <circle cx="${coords[coords.length - 1][0]}" cy="${coords[coords.length - 1][1]}" r="3.5" fill="${color}"></circle>
    <circle id="hoverDot" cx="0" cy="0" r="4" fill="${color}" stroke="#0b0d0d" stroke-width="2" style="opacity:0;pointer-events:none"></circle>`;

  const changeEl = document.getElementById('equityChange');
  const diff = points[points.length - 1] - points[0];
  const diffPct = points[0] !== 0 ? (diff / points[0] * 100) : 0;
  changeEl.textContent = (diff >= 0 ? '+' : '') + diff.toFixed(2) + ' USDT (' + (diffPct >= 0 ? '+' : '') + diffPct.toFixed(2) + '%) since oldest trade shown';
  changeEl.className = 'change ' + (diff >= 0 ? 'green' : 'red');
}

// wired once (not inside refresh) - the SVG contents get replaced each refresh, so this
// re-reads equityChartPoints (updated by drawEquityChart) and re-queries #hoverDot fresh each time
(function initChartHover() {
  const svg = document.getElementById('equityChart');
  const tooltip = document.getElementById('chartTooltip');
  function handleMove(clientX) {
    if (!equityChartPoints.length) return;
    const rect = svg.getBoundingClientRect();
    const relX = (clientX - rect.left) / rect.width * 600;
    let nearest = equityChartPoints[0], minDist = Infinity;
    for (const p of equityChartPoints) {
      const d = Math.abs(p.x - relX);
      if (d < minDist) { minDist = d; nearest = p; }
    }
    const dot = document.getElementById('hoverDot');
    if (dot) { dot.setAttribute('cx', nearest.x); dot.setAttribute('cy', nearest.y); dot.style.opacity = '1'; }
    tooltip.style.opacity = '1';
    tooltip.style.left = (nearest.x / 600 * rect.width) + 'px';
    tooltip.style.top = (nearest.y / 132 * rect.height) + 'px';
    tooltip.textContent = fmtNum(nearest.value, 2) + ' USDT';
  }
  svg.addEventListener('mousemove', e => handleMove(e.clientX));
  svg.addEventListener('mouseleave', () => {
    tooltip.style.opacity = '0';
    const dot = document.getElementById('hoverDot');
    if (dot) dot.style.opacity = '0';
  });
  svg.addEventListener('touchmove', e => { if (e.touches[0]) handleMove(e.touches[0].clientX); }, { passive: true });
})();

function renderAllocationDonut(positions, equity) {
  const svg = document.getElementById('allocDonut');
  const legend = document.getElementById('allocLegend');
  const freeLabel = document.getElementById('allocFreePct');
  const cx = 46, cy = 46, r = 38, strokeW = 13;
  const circumference = 2 * Math.PI * r;

  const totalMargin = positions.reduce((s, p) => s + (p.margin || 0), 0);
  const freeAmt = Math.max(0, equity - totalMargin);
  const freePct = equity > 0 ? (freeAmt / equity * 100) : 100;
  freeLabel.textContent = freePct.toFixed(0) + '%';

  const segments = positions
    .filter(p => (p.margin || 0) > 0)
    .map(p => ({ label: coinName(p.symbol), amt: p.margin, color: avatarColor(coinName(p.symbol)) }));
  segments.push({ label: 'Free', amt: freeAmt, color: 'rgba(255,255,255,.09)' });

  let offset = 0, circles = '';
  for (const seg of segments) {
    const frac = equity > 0 ? seg.amt / equity : 0;
    const len = frac * circumference;
    circles += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${seg.color}" stroke-width="${strokeW}"
      stroke-dasharray="${len.toFixed(2)} ${(circumference - len).toFixed(2)}" stroke-dashoffset="${(-offset).toFixed(2)}"
      transform="rotate(-90 ${cx} ${cy})" stroke-linecap="butt"></circle>`;
    offset += len;
  }
  svg.innerHTML = circles;

  legend.innerHTML = segments.filter(s => s.amt > 0.01).map(s => `
    <div class="row"><span class="sw" style="background:${s.color}"></span>
      <span class="name">${s.label}</span><span class="amt">${fmtNum(s.amt, 0)}</span></div>`).join('')
    || '<div class="row muted">Nothing deployed</div>';
}

function renderDailyBars(dailyPnl) {
  const container = document.getElementById('dailyBars');
  const maxAbs = Math.max(1, ...dailyPnl.map(d => Math.abs(d.pnl)));
  container.innerHTML = dailyPnl.map(d => {
    const heightPct = Math.max(4, Math.abs(d.pnl) / maxAbs * 100);
    const cls = d.pnl > 0 ? 'up' : d.pnl < 0 ? 'down' : 'zero';
    const day = new Date(d.date + 'T00:00:00Z').toLocaleDateString(undefined, { weekday: 'short' });
    const sign = d.pnl >= 0 ? '+' : '';
    return `<div class="barcol" title="${d.date}: ${sign}${d.pnl} USDT">
      <div class="track"><div class="fill ${cls}" style="height:${heightPct}%"></div></div>
      <div class="daylabel">${day}</div>
    </div>`;
  }).join('');
}

async function refresh() {
  let data;
  try {
    const res = await fetch('/api/status?account=' + currentAccount);
    data = await res.json();
  } catch (e) {
    document.getElementById('lastUpdate').textContent = 'connection lost';
    return;
  }

  const main = document.querySelector('main');
  const banner = document.getElementById('notConfiguredBanner');
  if (data.configured === false) {
    main.classList.add('blocked');
    banner.classList.add('show');
    document.getElementById('notConfiguredReason').textContent =
      data.reason || 'Add the API keys for this account as environment variables and redeploy.';
    const modeEl = document.getElementById('mode');
    modeEl.textContent = currentAccount === 'live' ? 'LIVE - NOT SET UP' : 'NOT CONFIGURED';
    modeEl.classList.remove('live');
    document.getElementById('lastUpdate').textContent = 'not connected';
    return;
  }
  main.classList.remove('blocked');
  banner.classList.remove('show');

  const modeEl = document.getElementById('mode');
  modeEl.textContent = data.mode;
  modeEl.classList.toggle('live', currentAccount === 'live');
  document.getElementById('lastUpdate').textContent = 'updated ' + timeAgo(data.scan_time);
  document.getElementById('equity').textContent = fmtNum(data.equity, 2) + ' USDT';
  document.getElementById('posCount').textContent = data.positions.length;

  const wr = data.stats.closed > 0 ? (data.stats.wins / data.stats.closed * 100) : 0;
  document.getElementById('winRate').textContent = data.stats.closed ? wr.toFixed(0) + '%' : '-';
  document.getElementById('winRateSub').textContent = data.stats.closed ? `${data.stats.wins}/${data.stats.closed} trades` : 'no trades yet';
  const ring = document.getElementById('wrRing');
  const circumference = 132;
  ring.setAttribute('stroke-dashoffset', String(circumference - (wr / 100) * circumference));
  ring.setAttribute('stroke', wr >= 50 ? '#2dd4a7' : '#fb7185');

  const pnlEl = document.getElementById('totalPnl');
  pnlEl.textContent = (data.stats.total_pnl >= 0 ? '+' : '') + fmtNum(data.stats.total_pnl, 2) + ' USDT';
  pnlEl.className = 'value ' + (data.stats.total_pnl >= 0 ? 'green' : 'red');
  document.getElementById('totalPnlSub').textContent =
    'net ' + (data.stats.net_pnl >= 0 ? '+' : '') + fmtNum(data.stats.net_pnl, 2) + ' after fees';

  const todayEl = document.getElementById('todayPnl');
  todayEl.textContent = (data.stats.today_pnl >= 0 ? '+' : '') + fmtNum(data.stats.today_pnl, 2) + ' USDT';
  todayEl.className = 'value ' + (data.stats.today_pnl >= 0 ? 'green' : 'red');

  drawEquityChart(data.history, data.equity);
  renderAllocationDonut(data.positions, data.equity);
  renderDailyBars(data.daily_pnl || []);

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
  document.getElementById('scanClickHint').style.display = data.movers.length ? 'block' : 'none';

  // order-book walls
  const walls = data.walls || [];
  renderWalls(document.getElementById('wallsGrid'), walls);
  document.getElementById('wallsEmpty').style.display = walls.length ? 'none' : 'block';
  document.getElementById('wallsGrid').style.display = walls.length ? 'grid' : 'none';

  // strategy 2 (order-book imbalance) - hide the whole panel when it's not running,
  // instead of showing an eternally-empty "scanning..." placeholder
  const obScan = data.orderbook_scan || { enabled: false, universe: [], watching: [] };
  document.getElementById('obScanPanel').style.display = obScan.enabled ? 'block' : 'none';
  if (obScan.enabled) {
    renderObWatchlist(
      document.getElementById('obWatchList'), document.getElementById('obWatchEmpty'),
      obScan.watching || [], document.getElementById('obUniverseTags'), obScan.universe || [],
      document.getElementById('obScanCountLabel')
    );
  }

  // live order-book ladder for whichever coin is selected (default to the top mover)
  if (!selectedObSymbol && data.movers.length) selectedObSymbol = data.movers[0][0];
  if (selectedObSymbol) loadOrderbook(selectedObSymbol);

  // history: overview preview (compact, 6 rows) + full page
  const histDesc = data.history.slice().reverse();
  renderHistoryPreview(document.querySelector('#historyTablePreview tbody'), histDesc);
  renderHistoryFull(document.getElementById('historyListFull'), histDesc);
  document.getElementById('historyEmptyPreview').style.display = data.history.length ? 'none' : 'block';
  document.getElementById('historyEmptyFull').style.display = data.history.length ? 'none' : 'block';
}
document.getElementById('resetHistoryBtn').addEventListener('click', async () => {
  if (!confirm(`Clear stats, equity chart and trade history for the ${currentAccount.toUpperCase()} account and start counting from now? This does not touch Binance itself.`)) return;
  try {
    await fetch('/api/reset-history?account=' + currentAccount, { method: 'POST' });
    refresh();
  } catch (e) {
    alert('Reset failed - connection error');
  }
});

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""

_ICON_SVGS = {
    "__IC_HOME__": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'
        '<polyline points="9 22 9 12 15 12 15 22"/></svg>'
    ),
    "__IC_POSITIONS__": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 2 7 12 12 22 7 12 2"/>'
        '<polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>'
    ),
    "__IC_SCAN__": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="2"/>'
        '<path d="M16.24 7.76a6 6 0 0 1 0 8.49M7.76 16.25a6 6 0 0 1 0-8.49'
        'M19.07 4.93a10 10 0 0 1 0 14.14M4.93 4.93a10 10 0 0 0 0 14.14"/></svg>'
    ),
    "__IC_HISTORY__": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/>'
        '<polyline points="12 6 12 12 16 14"/></svg>'
    ),
    "__ICO_SLEEP__": (
        '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>'
    ),
    "__ICO_INBOX__": (
        '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/>'
        '<path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>'
    ),
    "__ICO_FILE__": (
        '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
        '<polyline points="14 2 14 8 20 8"/></svg>'
    ),
}
for _token, _svg in _ICON_SVGS.items():
    PAGE = PAGE.replace(_token, _svg)


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


def fetch_exchange_closed_trades(exchange, limit=400, since_iso=""):
    """Realized PnL per closed trade plus total fees paid, sourced from the exchange
    itself rather than the local trades_log.csv. Render's free tier disk isn't
    guaranteed to survive a redeploy, which was silently wiping the trade log and its
    PnL/history - the exchange's own income record persists regardless of what happens
    to this process.

    One unfiltered income fetch, bucketed client-side by incomeType: REALIZED_PNL
    entries become trade rows (what "pnl" means everywhere else in the UI - matches
    the take-profit/stop-loss level that triggered), COMMISSION + FUNDING_FEE are
    summed separately into fees_total so trade PnL isn't silently blended with fees.
    """
    try:
        income = exchange.fapiPrivateGetIncome({"limit": limit})
    except Exception as e:
        print(f"[warn] could not fetch income history: {e}", flush=True)
        return [], 0.0

    if since_iso:
        cutoff_ms = int(datetime.fromisoformat(since_iso).timestamp() * 1000)
        income = [item for item in income if int(item["time"]) >= cutoff_ms]

    fees_total = sum(
        float(item["income"]) for item in income
        if item.get("incomeType") in ("COMMISSION", "FUNDING_FEE")
    )

    pnl_income = [item for item in income if item.get("incomeType") == "REALIZED_PNL"]
    pnl_income.sort(key=lambda x: int(x["time"]))
    market_by_id = {m["id"]: m["symbol"] for m in exchange.markets.values()}

    grouped = []
    for item in pnl_income:
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
    return rows, round(fees_total, 4)


@app.route("/api/status")
@requires_auth
def api_status():
    account_name = request.args.get("account", "demo")
    acct = ACCOUNTS.get(account_name)
    if not acct or not acct["configured"]:
        return jsonify({
            "configured": False,
            "account": account_name,
            "reason": (acct or {}).get("reason", "unknown account"),
        })
    exchange = acct["exchange"]
    cfg = acct["cfg"]

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
            "margin": float(p.get("info", {}).get("initialMargin") or 0),
        })

    scan_time, movers, walls, strategy_by_symbol = None, [], [], {}
    orderbook_scan = {"enabled": False, "universe": [], "watching": []}
    if os.path.exists(acct["status_path"]):
        with open(acct["status_path"]) as f:
            status = json.load(f)
        scan_time = status.get("time")
        movers = status.get("movers", [])
        walls = status.get("walls", [])
        strategy_by_symbol = status.get("strategy_by_symbol", {})
        orderbook_scan = status.get("orderbook_scan", orderbook_scan)

    for p in positions:
        p["strategy"] = strategy_by_symbol.get(p["symbol"], "unknown")

    since_iso = _history_cutoff(acct["reset_marker_path"])

    # entry/trail_stop events only come from the local log (no clean exchange
    # equivalent) - exits come from the exchange's own income record so they survive
    # this process restarting or Render wiping the disk on redeploy
    local_rows = []
    if os.path.exists(acct["log_path"]):
        with open(acct["log_path"], newline="") as f:
            local_rows = [
                r for r in csv.DictReader(f)
                if r.get("event") in ("entry", "trail_stop") and (not since_iso or r["time"] >= since_iso)
            ]

    exit_rows, fees_total = fetch_exchange_closed_trades(exchange, since_iso=since_iso)
    history = sorted(local_rows + exit_rows, key=lambda r: r["time"])

    # the exchange only tells us symbol/time/pnl for a close - side/entry/stop/tp/reason
    # only exist in our own entry log, so stitch each exit onto the entry that opened it
    # (walking in time order and matching by symbol) instead of leaving those blank.
    # Only works for entries logged since this process last started - Render's disk
    # isn't guaranteed to survive a redeploy, so older exits can still show blank.
    pending_entry_by_symbol = {}
    for row in history:
        if row["event"] == "entry":
            pending_entry_by_symbol[row["symbol"]] = row
        elif row["event"] == "exit":
            entry = pending_entry_by_symbol.pop(row["symbol"], None)
            if entry:
                row["side"] = entry.get("side", "")
                row["entry"] = entry.get("entry", "")
                row["stop"] = entry.get("stop", "")
                row["take_profit"] = entry.get("take_profit", "")
                row["reason"] = entry.get("reason", "")
                row["strategy"] = entry.get("strategy", "")

    closed_pnls = [r["pnl"] for r in exit_rows]
    today = datetime.now(timezone.utc).date().isoformat()
    today_pnl = sum(r["pnl"] for r in exit_rows if r["time"][:10] == today)
    total_pnl = sum(closed_pnls)
    stats = {
        "closed": len(closed_pnls),
        "wins": len([p for p in closed_pnls if p > 0]),
        "total_pnl": total_pnl,
        "today_pnl": today_pnl,
        "fees_total": fees_total,
        "net_pnl": round(total_pnl + fees_total, 4),
    }

    by_day = defaultdict(float)
    for r in exit_rows:
        by_day[r["time"][:10]] += r["pnl"]
    today_date = datetime.now(timezone.utc).date()
    daily_pnl = [
        {"date": (today_date - timedelta(days=i)).isoformat(),
         "pnl": round(by_day.get((today_date - timedelta(days=i)).isoformat(), 0.0), 2)}
        for i in range(6, -1, -1)
    ]

    return jsonify({
        "configured": True,
        "account": account_name,
        "mode": "TESTNET/DEMO" if cfg.testnet else "LIVE - REAL FUNDS",
        "equity": equity,
        "positions": positions,
        "daily_pnl": daily_pnl,
        "movers": movers,
        "walls": walls,
        "orderbook_scan": orderbook_scan,
        "scan_time": scan_time,
        "history": history[-30:],
        "stats": stats,
        "reset_since": since_iso,
    })


@app.route("/api/reset-history", methods=["POST"])
@requires_auth
def api_reset_history():
    """Draws a line under the dashboard's stats/history/equity-chart - everything
    before this moment stops counting. Doesn't touch the exchange itself (no such
    thing as erasing its records, and no need to): the local trades_log.csv is also
    cleared so old entries don't linger for no reason, but even if it weren't, the
    time cutoff alone is enough to hide everything before it."""
    account_name = request.args.get("account", "demo")
    acct = ACCOUNTS.get(account_name)
    if not acct or not acct["configured"]:
        return jsonify({"ok": False, "reason": (acct or {}).get("reason", "unknown account")}), 400
    now = datetime.now(timezone.utc).isoformat()
    with open(acct["reset_marker_path"], "w") as f:
        json.dump({"since": now}, f)
    if os.path.exists(acct["log_path"]):
        os.remove(acct["log_path"])
    return jsonify({"ok": True, "since": now})


@app.route("/api/orderbook")
@requires_auth
def api_orderbook():
    """Live order-book ladder for one symbol - the actual price/size levels, not just
    the wall-detection summary, so it looks and reads like a real order book."""
    account_name = request.args.get("account", "demo")
    acct = ACCOUNTS.get(account_name)
    if not acct or not acct["configured"]:
        return jsonify({"error": "account not configured"}), 400
    exchange, cfg = acct["exchange"], acct["cfg"]

    symbol = request.args.get("symbol")
    if not symbol:
        return jsonify({"error": "symbol required"}), 400
    try:
        ob = exchange.fetch_order_book(symbol, limit=100)
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    bids = ob.get("bids") or []
    asks = ob.get("asks") or []

    sample = [q for _, q in bids[:50]] + [q for _, q in asks[:50]]
    sample_sorted = sorted(sample)
    median_qty = sample_sorted[len(sample_sorted) // 2] if sample_sorted else 0
    wall_mult = cfg.signal.wall_multiplier or 5.0
    min_wall_usd = cfg.signal.min_wall_usd or 5000.0

    def annotate(levels, n=12):
        out = []
        for price, qty in levels[:n]:
            is_wall = median_qty > 0 and qty >= median_qty * wall_mult and qty * price >= min_wall_usd
            out.append({"price": price, "qty": qty, "wall": is_wall})
        return out

    mid = (bids[0][0] + asks[0][0]) / 2 if bids and asks else None
    return jsonify({
        "symbol": symbol,
        "mid": mid,
        "bids": annotate(bids),
        "asks": annotate(asks),
    })


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    host = "0.0.0.0" if os.getenv("PORT") else "127.0.0.1"
    print(f"Dashboard running at http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
