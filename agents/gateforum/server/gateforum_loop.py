"""GateForum display-loop worker — ensure the trading session is live.

Used by the gateforum-loop systemd timer. The trading session itself produces
all research verdicts (fresh every 15-min tick); the display page (:8600) polls
the research server for the stored transcripts, so this loop does NOT re-run the
research (that duplicated LLM cost and collided with the session's cycles via the
server's per-pair rate limiter, producing 429/stale verdicts).

This worker's job is purely operational: make sure the TRADING session is
running (start it via the condor web API if not) — the self-bootstrap that makes
the whole stack come up on boot with no manual instructions.

Run:  /home/carlito/projects/condor/.venv/bin/python gateforum_loop.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

CONDOR_WEB_HOST = os.environ.get("WEB_BIND_HOST", "127.0.0.1")
CONDOR_WEB = f"http://{CONDOR_WEB_HOST}:8099"
AGENT = "gateforum"
STRATEGY = "gateforum_debate_operator"
CHAT_ID = 5587715073

# The session start payload — MINIMAL SIZE MODE. Never loosen these.
SESSION_CONFIG = {
    "execution_mode": "loop",
    "frequency_sec": 900,
    "max_ticks": 0,  # 0 = unlimited (run until stopped); competition length
    "total_amount_quote": 50,
    "restart_on_boot": True,
    "trading_context": (
        "Trade BTC-USDT, XAU-USDT and CL-USDT on gate_io_perpetual. "
        "MINIMAL SIZE MODE: max 3 open positions, max $8 notional per position "
        "(total_amount_quote REQUIRED), leverage 1x only. "
        "MUST-OPEN: open EVERY pair whose verdict is actionable at >=65% confidence "
        "(ranked by confidence) until 3 positions are open; never defer an actionable "
        "pair because another has higher confidence. "
        "SKIP-PAIR: use get_portfolio_overview perp positions (exchange truth, ALL "
        "sessions) to detect pairs already open - never open a 2nd on an open pair. "
        "DUST RULE: judge pairs by NET notional (SHORT negative + LONG positive x "
        "entry); |net| < $1 = dust, ignore entirely; only |net| >= $1 blocks. " 
        "VOLUME CADENCE: every 30 minutes (every 2nd tick), close positions in "
        "negative P&L, then that pair is in a 15-minute COOLDOWN (1 cycle) - no "
        "re-entry until it elapses, then re-evaluate via a fresh >=65% verdict; "
        "keep winners running. Capital preservation first."
    ),
    "risk_limits": {
        "max_position_size_quote": 24,
        "max_open_executors": 3,
        "max_drawdown_pct": 8,
        "max_leverage": 1,
        "require_triple_barrier": True,
        "require_trailing_stop": True,
    },
}


class Ctx:
    _chat_id = CHAT_ID


def _condor_jwt() -> str:
    """Mint a short-lived condor web JWT (same pattern as gateforum_public)."""
    try:
        from jose import jwt as jose_jwt
    except Exception as exc:  # noqa: BLE001
        print(f"jose import failed: {exc}", flush=True)
        return ""
    secret = os.getenv("WEB_JWT_SECRET")
    if not secret:
        try:
            import yaml

            cfg = yaml.safe_load(Path("/home/carlito/projects/condor/config.yml").read_text())
            secret = (cfg.get("web_jwt_secret") or "").strip() or None
        except Exception as exc:  # noqa: BLE001
            print(f"config.yml secret read failed: {exc}", flush=True)
            secret = None
    if not secret:
        print("no WEB_JWT_SECRET available", flush=True)
        return ""
    payload = {
        "sub": str(CHAT_ID),
        "username": "admin",
        "first_name": "Carlito",
        "role": "admin",
        "exp": int(time.time()) + 3600,
    }
    return jose_jwt.encode(payload, secret, algorithm="HS256")


async def ensure_session_running() -> bool:
    """Start the trading session if it is not already running. True if running/started."""
    import httpx

    jwt = _condor_jwt()
    if not jwt:
        print("ensure_session: no JWT — cannot check session state", flush=True)
        return False
    headers = {"Authorization": f"Bearer {jwt}"}
    url = f"{CONDOR_WEB}/api/v1/agents/{AGENT}/strategies/{STRATEGY}"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url, headers=headers)
            if r.status_code == 200:
                status = r.json().get("status", "")
                if status == "running":
                    print("ensure_session: already running", flush=True)
                    return True
            # Not running (stopped/idle/404) -> start it.
            start_url = f"{url}/start"
            payload = {"config": SESSION_CONFIG, "chat_id": CHAT_ID}
            r2 = await c.post(start_url, headers={**headers, "Content-Type": "application/json"}, json=payload, timeout=60)
            if r2.status_code == 200:
                body = r2.json()
                print(
                    f"ensure_session: STARTED session #{body.get('session_num')} "
                    f"({body.get('agent_id')})",
                    flush=True,
                )
                return True
            print(f"ensure_session: start failed {r2.status_code}: {r2.text[:200]}", flush=True)
            return False
    except Exception as exc:  # noqa: BLE001
        print(f"ensure_session: error {type(exc).__name__}: {exc}", flush=True)
        return False


async def main() -> None:
    # No research runs here: the trading session produces all verdicts and the page
    # polls the server for them. This loop only keeps the session alive.
    ok = await ensure_session_running()
    if not ok:
        print("gateforum_loop: session check/start failed — see log", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
