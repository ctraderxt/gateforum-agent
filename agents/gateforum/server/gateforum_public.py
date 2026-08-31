"""GateForum — public live research & decision page (no auth).

Serves the real-time decision floor for judges and visitors. Reads the latest
research/analysis transcripts from the GateForum server (port 8500) and renders
them as a chat-style page with auto-refresh.

Run:
  python gateforum_public.py --host 0.0.0.0 --port 8600

No authentication: this page is intentionally public — it is the Agent
Builders Cup judge-vote asset. It exposes ONLY research transcripts (arguments,
verdicts), never API keys, balances, or positions.
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger("gateforum-public")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [PUBLIC] %(levelname)s %(message)s")

DEBATE_SERVER = "http://127.0.0.1:8500"
# Condor web (for activity strip). Prefer live env; this WSL box is :8088.
import os as _os
from pathlib import Path as _Path

_WEB_HOST = (
    _os.environ.get("WEB_BIND_HOST")
    or _os.environ.get("WEB_HOST")
    or "127.0.0.1"
)
_WEB_PORT = _os.environ.get("WEB_PORT") or "8088"
CONDOR_WEB = f"http://{_WEB_HOST}:{_WEB_PORT}"
PAIRS = ["BTC-USDT", "XAU-USDT", "CL-USDT"]
REFRESH_MS = 5_000

app = FastAPI(title="GateForum — Public Research & Decision Floor", version="1.3.0")


def _condor_jwt() -> str:
    """Mint a short-lived condor web JWT for server-side stats fetch (local only).

    Self-contained (no ``import condor`` — this app runs from the server dir):
    uses the same secret the condor web server signs with (WEB_JWT_SECRET env or
    the persisted config.yml ``web_jwt_secret``) and the same payload shape.
    """
    import os
    import time
    from pathlib import Path

    try:
        from jose import jwt as jose_jwt
    except Exception as exc:  # noqa: BLE001
        logger.warning("jose import failed: %s", exc)
        return ""

    secret = os.getenv("WEB_JWT_SECRET")
    if not secret:
        try:
            import yaml
            roots = [
                _Path(__file__).resolve().parents[3] / "config.yml",
                _Path.home() / "condor" / "config.yml",
            ]
            secret = None
            for cfg_path in roots:
                if cfg_path.is_file():
                    cfg = yaml.safe_load(cfg_path.read_text()) or {}
                    secret = (cfg.get("web_jwt_secret") or "").strip() or None
                    if secret:
                        break
        except Exception as exc:  # noqa: BLE001
            logger.warning("config.yml secret read failed: %s", exc)
            secret = None
    if not secret:
        logger.warning("no WEB_JWT_SECRET available")
        return ""
    payload = {
        "sub": "gateforum-admin",
        "username": "admin",
        "first_name": "GateForum",
        "role": "admin",
        "exp": int(time.time()) + 3600,
    }
    return jose_jwt.encode(payload, secret, algorithm="HS256")


async def _fetch_json(path: str) -> "dict | list | None":
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(f"{DEBATE_SERVER}{path}")
            r.raise_for_status()
            return r.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch failed for %s: %s", path, exc)
        return None


async def _fetch_condor(path: str) -> "dict | list | None":
    """Fetch from the condor web API with a local JWT (server-side, no exposure)."""
    jwt = _condor_jwt()
    if not jwt:
        return None
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(
                f"{CONDOR_WEB}{path}",
                headers={"Authorization": f"Bearer {jwt}"},
            )
            r.raise_for_status()
            return r.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("condor fetch failed for %s: %s", path, exc)
        return None


async def _condor_server() -> str:
    """Live Condor server name (this demo is GateForum-Agent, not 'local')."""
    pinned = (_os.environ.get("GATEFORUM_SERVER_NAME") or "").strip()
    if pinned:
        return pinned
    servers = await _fetch_condor("/api/v1/servers")
    if isinstance(servers, list) and servers:
        for s in servers:
            if isinstance(s, dict) and s.get("is_default"):
                name = str(s.get("name") or "").strip()
                if name:
                    return name
        name = str((servers[0] or {}).get("name") or "").strip()
        if name:
            return name
    return "GateForum-Agent"


@app.get("/health")
async def health():
    """Used by gateforum_init to know the floor is up. No secrets."""
    return {"status": "ok", "debate_server": DEBATE_SERVER, "condor_web": CONDOR_WEB}


@app.get("/api/research")
async def api_research():
    """Sessions, live turns, overall stats ($+%), and per-asset trading stats."""
    health = await _fetch_json("/health") or {}
    sess = await _fetch_json("/sessions") or {"sessions": []}
    active = list(health.get("active_research", [])) if isinstance(health, dict) else []
    live: dict[str, list[dict]] = {}
    for pair in active:
        data = await _fetch_json(f"/active/{pair}")
        data = data if isinstance(data, dict) else {}
        live[pair] = data.get("turns", [])

    # Overall + per-asset trading stats, scoped to GateForum's own venue.
    from urllib.parse import quote as _quote

    server = await _condor_server()
    s_path = _quote(server, safe="")
    stats = await _fetch_condor(f"/api/v1/servers/{s_path}/executors/summary")
    stats = stats if isinstance(stats, dict) else {}
    execs = await _fetch_condor(f"/api/v1/servers/{s_path}/executors?limit=100")
    execs = execs if isinstance(execs, list) else []
    portfolio = await _fetch_condor(f"/api/v1/servers/{s_path}/portfolio")
    portfolio = portfolio if isinstance(portfolio, dict) else {}

    # Gate.io perpetual balance only (the % P&L base). Fall back to account total.
    total_value = 0.0
    for c in portfolio.get("connectors", []):
        if c.get("connector") == "gate_io_perpetual":
            total_value += float(c.get("total_usd") or 0.0)
    if total_value <= 0:
        total_value = float(portfolio.get("total_usd") or 0.0)

    # Aggregate per trading pair — gate_io_perpetual executors only.
    gate_execs = [e for e in execs if e.get("connector") == "gate_io_perpetual"]
    per_asset: dict[str, dict] = {}
    for e in gate_execs:
        pair = e.get("trading_pair") or "?"
        a = per_asset.setdefault(pair, {"pnl": 0.0, "volume": 0.0, "positions": 0, "open": 0})
        a["pnl"] += float(e.get("pnl") or 0.0)
        a["volume"] += float(e.get("volume") or 0.0)
        a["positions"] += 1
        if (e.get("status") or "") in ("running", "active", "OPEN"):
            a["open"] += 1

    # Overall P&L/volume from GateForum's own executors (not the all-connector summary).
    own_pnl = round(sum(a["pnl"] for a in per_asset.values()), 8)
    own_vol = round(sum(a["volume"] for a in per_asset.values()), 8)
    own_count = sum(a["positions"] for a in per_asset.values())

    return JSONResponse(
        {
            "current_session": sess.get("current_session", 1),
            "active_research": active,
            "live_transcripts": live,
            "sessions": sess.get("sessions", []),
            "stats": {"pnl": own_pnl, "volume": own_vol, "count": own_count, "period": "all"},
            "total_value": total_value,
            "per_asset": per_asset,
        }
    )


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(PAGE)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>GateForum — Live</title>
<style>
  :root { --bg:#080c18; --surface:#0f1525; --surface2:#161e34; --border:#1c2541;
          --text:#e8e6e3; --muted:#7b8ba4; --green:#22c55e; --red:#ef4444; --blue:#d4a845;
          --gold:#f5c542; }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { background:var(--bg); color:var(--text); font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif; min-height:100vh; }
  header { position:sticky; top:0; z-index:10; background:rgba(8,12,24,.92); backdrop-filter:blur(8px);
           border-bottom:1px solid var(--border); padding:14px 24px; display:flex; align-items:center; justify-content:center; gap:16px; position:relative; }
  header h1 { font-size:18px; font-weight:700; letter-spacing:.5px; }
  header .live { color:var(--green); font-size:12px; display:flex; align-items:center; gap:6px; position:absolute; right:24px; }
  header .live .dot { width:8px; height:8px; border-radius:50%; background:var(--green); animation:pulse 1.6s infinite; }
  @keyframes pulse { 50% { opacity:.25; } }
  main { max-width:1200px; margin:0 auto; padding:24px 16px 80px; }

  .session-head { display:flex; align-items:center; gap:12px; margin-bottom:18px; flex-wrap:wrap; }
  .session-head h2 { font-size:16px; font-weight:700; }
  .session-head .pill { font-size:11px; padding:3px 10px; border-radius:999px; border:1px solid var(--border); color:var(--muted); }
  .session-head .pill.running { color:var(--green); border-color:var(--green); }
  .session-head .pill.waiting { color:var(--gold); border-color:var(--gold); }
  .session-head .fundinfo { margin-left:auto; display:flex; align-items:center; gap:16px; font-size:13px; }
  .session-head .fundinfo .fv { color:var(--text); font-weight:600; }
  .session-head .fundinfo .fv .val { font-weight:800; }
  .session-head .fundinfo .opnl { color:var(--muted); font-weight:600; }
  .session-head .fundinfo .opnl .val { font-weight:800; }
  .session-head .fundinfo .opnl .val.pos { color:var(--green); }
  .session-head .fundinfo .opnl .val.neg { color:var(--red); }

  .story { font-size:14px; line-height:1.45; color:var(--text); background:linear-gradient(135deg, rgba(46,204,113,.08), rgba(64,156,255,.06));
           border:1px solid var(--border); border-left:3px solid var(--green); border-radius:10px; padding:10px 14px; margin-bottom:10px; }
  .story .sv-long { color:var(--green); font-weight:700; }
  .story .sv-short { color:var(--red); font-weight:700; }
  .story .sv-hold { color:var(--muted); font-weight:600; }
  .story b { color:var(--gold); }
  .activity { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:18px; }
  .activity .chip { font-size:11px; padding:4px 10px; border-radius:999px; border:1px solid var(--border); color:var(--muted); background:var(--surface2); }

  .columns { display:grid; grid-template-columns:repeat(3,1fr); gap:14px; align-items:start; }
  @media (max-width:900px) { .columns { grid-template-columns:1fr; } }
  .col { background:var(--surface); border:1px solid var(--border); border-radius:14px; overflow:hidden; display:flex; flex-direction:column; }
  .col .card { padding:14px 16px; border-bottom:1px solid var(--border); background:var(--surface2); }
  .col .card .pair { font-weight:700; font-size:15px; text-align:center; }
  .col .card .verdict { font-size:13px; margin-top:4px; text-align:center; }
  .col .card .status { font-size:11px; margin-top:8px; padding:3px 10px; border-radius:999px; display:block; text-align:center; width:fit-content; margin-left:auto; margin-right:auto; }
  .col .card .status.running { color:var(--green); border:1px solid var(--green); animation:pulse 1.6s infinite; }
  .col .card .status.done { color:var(--muted); border:1px solid var(--border); }
  .col .card .status.waiting { color:var(--gold); border:1px solid var(--gold); }
  .col .card .statsgrid { margin-top:12px; border-top:1px dashed var(--border); padding-top:10px; display:grid; grid-template-columns:1fr auto; gap:4px 14px; align-items:baseline; font-size:12px; }
  .col .card .statsgrid .l { color:var(--muted); white-space:nowrap; }
  .col .card .statsgrid .r { font-weight:700; text-align:right; white-space:nowrap; }
  .col .card .statsgrid .r.pos { color:var(--green); } .col .card .statsgrid .r.neg { color:var(--red); }
  .feed { padding:12px; display:flex; flex-direction:column; gap:8px; }
  .bubble { background:var(--surface2); border:1px solid var(--border); border-radius:10px; padding:8px 12px; }
  .bubble .who { font-size:10px; text-transform:uppercase; letter-spacing:.7px; color:var(--muted); margin-bottom:4px; }
  .bubble.bull { border-left:3px solid var(--green); } .bubble.bull .who { color:var(--green); }
  .bubble.bear { border-left:3px solid var(--red); } .bubble.bear .who { color:var(--red); }
  .bubble.judge { border-left:3px solid var(--gold); } .bubble.judge .who { color:var(--gold); }
  .bubble.analyst { border-left:3px solid var(--blue); } .bubble.analyst .who { color:var(--blue); }
  .bubble p { white-space:pre-wrap; font-size:12.5px; line-height:1.5; margin:0; }

  .accordion { margin-top:12px; border-top:1px dashed var(--border); padding-top:10px; }
  .accordion details { background:var(--surface); border:1px solid var(--border); border-radius:10px; margin-bottom:8px; overflow:hidden; }
  .accordion summary { cursor:pointer; padding:9px 12px; font-size:12px; font-weight:600; list-style:none; display:flex; align-items:center; gap:8px; }
  .accordion summary::-webkit-details-marker { display:none; }
  .accordion summary .chev { color:var(--muted); transition:transform .15s; font-size:10px; }
  .accordion details[open] summary .chev { transform:rotate(90deg); }
  .accordion summary .meta { color:var(--muted); font-weight:400; font-size:11px; margin-left:auto; }
  .accordion .inner { padding:0 12px 12px; }
  .empty { color:var(--muted); text-align:center; padding:60px 0; }
  footer { position:fixed; bottom:0; width:100%; text-align:center; color:var(--muted); font-size:11px;
           padding:8px; background:rgba(8,12,24,.9); border-top:1px solid var(--border); }
</style>
</head>
<body>
<header>
  <h1>⚖️ GateForum</h1>
  <span class="live"><span class="dot"></span>LIVE · 12-agent research &amp; decision</span>
</header>
<main>
  <div id="app"></div>
</main>
<footer>GateForum — multi-agent trading research &amp; decision · Agent Builders Cup · auto-refresh every 30s</footer>
<script>
const ESC = s => s.replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmtTime = ts => { try { return new Date(ts).toLocaleString(); } catch { return ''; } };
const ORDER = ['BTC-USDT', 'XAU-USDT', 'CL-USDT'];

async function load() {
  try {
    const res = await fetch('/api/research');
    const data = await res.json();
    render(data);
  } catch (e) { console.error(e); }
}

function statusFor(pair, active) {
  if (active.includes(pair)) return { label: '⚡ Session in Progress…', cls: 'running' };
  return { label: '✓ Decision Made', cls: 'done' };
}

function bubbles(turns) {
  return (turns || []).map(x => {
    let cls = 'bubble analyst';
    if (x.role === 'bull') cls = 'bubble bull';
    else if (x.role === 'bear') cls = 'bubble bear';
    else if (x.role === 'research_judge' || x.role === 'risk_judge') cls = 'bubble judge';
    const text = (x.text || '').trim() || (x.role === 'risk_judge' ? 'FINAL DECISION: HOLD — no ruling text received.' : '—');
    return `<div class="${cls}"><div class="who">${ESC(x.label || x.role)}</div><p>${ESC(text)}</p></div>`;
  }).join('');
}

function colHTML(pair, history, active, liveTurns, aStats) {
  // history = all research cycles for this pair across sessions, chronological.
  const latest = history.length ? history[history.length - 1] : null;
  // Group older cycles by session (one accordion row per session).
  const bySess = new Map();
  history.slice(0, -1).forEach(d => {
    const sid = d.session_id || '?';
    if (!bySess.has(sid)) bySess.set(sid, []);
    bySess.get(sid).push(d);
  });
  const older = Array.from(bySess.entries()).reverse(); // newest-oldest session
  const st = statusFor(pair, active);
  const isLive = active.includes(pair);
  const c = latest ? (latest.confidence || 0) : 0;
  const feedTurns = isLive && liveTurns.length ? liveTurns : (latest ? latest.transcript : []);
  const acc = older.length ? `<div class="accordion">
      ${older.map(([sid, cycles]) => `<details>
        <summary><span class="chev">▸</span> Trade Session #${sid}
          <span class="meta">${cycles.length} research cycle${cycles.length > 1 ? 's' : ''} · ${fmtTime(cycles[0].timestamp)}</span></summary>
        <div class="inner"><div class="feed">${bubbles(cycles.flatMap(d => d.transcript))}</div></div>
      </details>`).join('')}
    </div>` : '';

  // Per-asset trading stats: P&L / Position / Volume.
  const ap = aStats || {};
  const apnl = typeof ap.pnl === 'number' ? ap.pnl : null;
  const avol = typeof ap.volume === 'number' ? ap.volume : null;
  const apos = (typeof ap.open === 'number' ? ap.open : (typeof ap.positions === 'number' ? ap.positions : 0));
  const fmt2 = n => (n === null || n === undefined) ? '—' : ((n >= 0 ? '+' : '') + n.toFixed(2));
  const pnlCls = apnl === null ? '' : (apnl >= 0 ? 'pos' : 'neg');

  return `<div class="col">
    <div class="card">
      <div class="pair">${ESC(pair)}</div>
      <div class="verdict">${latest ? ESC(latest.decision || 'HOLD') + ' · ' + ESC(latest.direction || 'NONE') : 'Awaiting first session'}</div>
      <span class="status ${st.cls}">${st.label}</span>
      <div class="statsgrid">
        <span class="l">${latest ? c + '% (confidence)' : '— (confidence)'}</span>
        <span class="r ${pnlCls}">P&nbsp;&amp;&nbsp;L&nbsp;&nbsp;${fmt2(apnl)}</span>
        <span class="l">${latest ? '✓ Decision Made' : '⏳ Awaiting Decision'}</span>
        <span class="r">Position&nbsp;&nbsp;${apos}</span>
        <span class="l">${latest ? fmtTime(latest.timestamp) : ''}</span>
        <span class="r">Vol&nbsp;&nbsp;${fmt2(avol)}</span>
      </div>
    </div>
    <div class="feed">${feedTurns.length ? bubbles(feedTurns) : '<div class="empty" style="padding:30px 0">Waiting for the first session.</div>'}
      ${isLive ? '<div class="bubble judge"><div class="who">Agents thinking…</div><p style="opacity:.6">▍</p></div>' : ''}
    </div>
    ${acc}
  </div>`;
}

function pnlText(pnl, total) {
  // "$-0.07 (-0.1%)" style: $ amount + % of portfolio value
  if (pnl === null || pnl === undefined) return null;
  const usd = (pnl >= 0 ? '+' : '') + pnl.toFixed(2);
  let pct = null;
  if (total && total > 0) {
    const p = (pnl / total) * 100;
    pct = (p >= 0 ? '+' : '') + p.toFixed(1) + '%';
  }
  return { usd, pct };
}

function render(data) {
  const app = document.getElementById('app');
  const sessions = data.sessions || [];
  const active = data.active_research || [];
  const live = data.live_transcripts || {};
  const perAssetStats = data.per_asset || {};
  const current = sessions.length ? sessions[sessions.length - 1] : null;
  const curId = current ? current.session_id : (data.current_session || 1);

  // Overall P&L for the session header line ($ + %).
  const s = data.stats || {};
  const oPnl = typeof s.pnl === 'number' ? s.pnl : null;
  const oTotal = typeof data.total_value === 'number' ? data.total_value : 0;
  const opt = pnlText(oPnl, oTotal);
  const pnlCls = oPnl === null ? '' : (oPnl >= 0 ? 'pos' : 'neg');
  const fundHtml = `<div class="fundinfo">
      <span class="fv">Fund Value: <span class="val">$${oTotal.toFixed(2)}</span></span>
      <span class="opnl">Overall P&amp;L: <span class="val ${pnlCls}">${opt ? opt.usd : '—'}${opt && opt.pct ? ' (' + opt.pct + ')' : ''}</span></span>
    </div>`;

  // Per-asset history across ALL sessions (latest decision persists).
  const perAsset = {};
  ORDER.forEach(p => perAsset[p] = []);
  sessions.forEach(sess => (sess.research || []).forEach(d => {
    if (perAsset[d.pair]) perAsset[d.pair].push({...d, session_id: sess.session_id});
  }));
  ORDER.forEach(p => perAsset[p].sort((a, b) => (a.timestamp < b.timestamp ? -1 : 1)));

  const done = (current ? current.research || [] : []).length;
  const sessionPill = active.length ? 'running' : (done ? 'done' : 'waiting');
  const sessionLabel = active.length ? 'Trade Session in Progress' : (done ? `Complete · ${done} decisions` : 'Awaiting');

  // ── Story header: ALL council rulings in plain words ──────────────────
  // List every pair's verdict with confidence; mark pairs that actually hold
  // an open position (perAssetStats[pair].open > 0). Judges see the full
  // decision table, not just the single highest-confidence verdict.
  let story = 'The council is preparing its first research cycle.';
  if (current && (current.research || []).length) {
    const ds = (current.research || []).slice().sort((a, b) => (b.confidence || 0) - (a.confidence || 0));
    const parts = ds.map(d => {
      const dec = (d.decision || '').toUpperCase();
      const dir = (d.direction || '').toLowerCase();
      const side = dec === 'HOLD' ? 'HOLD' : (dir === 'long' ? 'LONG' : 'SHORT');
      const pair = ESC(d.pair);
      const conf = d.confidence || 0;
      const open = (perAssetStats[d.pair] && perAssetStats[d.pair].open > 0);
      if (dec === 'HOLD') return `<span class="sv-hold">${pair} HOLD ${conf}%</span>`;
      return `<span class="sv-${dir === 'short' ? 'short' : 'long'}">${pair} ${side} ${conf}%${open ? ' <b>· OPEN</b>' : ''}</span>`;
    });
    story = `Council rulings — ${parts.join(' · ')}.`;
  }
  const storyHtml = `<div class="story">🎙️ ${story}</div>`;

  // ── Live activity strip: proof the floor is moving ────────────────────
  const allDecisions = sessions.reduce((n, s) => n + (s.research || []).length, 0);
  const posTotal = Object.values(perAssetStats).reduce((n, a) => n + (typeof a.positions === 'number' ? a.positions : 0), 0);
  const lastTs = sessions.length ? (sessions[sessions.length - 1].started_at || '') : '';
  const vol = typeof s.volume === 'number' ? s.volume : null;
  const actHtml = `<div class="activity">
      <span class="chip">📊 ${sessions.length} session${sessions.length === 1 ? '' : 's'}</span>
      <span class="chip">🧠 ${allDecisions} decisions</span>
      <span class="chip">🧾 ${posTotal} position${posTotal === 1 ? '' : 's'}</span>
      <span class="chip">💧 Vol ${vol === null ? '—' : '$' + vol.toFixed(2)}</span>
      <span class="chip">🕐 last cycle ${lastTs ? fmtTime(lastTs) : '—'}</span>
    </div>`;

  let html = `<div class="session-head">
      <h2>🗣️ Trade Session #${curId}</h2>
      <span class="pill ${sessionPill}">${sessionLabel}</span>
      <span class="pill">${current ? fmtTime(current.started_at) : ''}</span>
      ${fundHtml}
    </div>
    ${storyHtml}
    ${actHtml}
    <div class="columns">
      ${ORDER.map(p => colHTML(p, perAsset[p], active, live[p] || [], perAssetStats[p])).join('')}
    </div>`;
  app.innerHTML = html;
}

load();
setInterval(load, 5000);
</script>
</body>
</html>
"""


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="GateForum public research page")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8600)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
