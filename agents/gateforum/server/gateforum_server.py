"""GateForum research server — FastAPI wrapper around the self-contained research engine.

Endpoints (contract consumed by the Condor `gateforum_research` / `gateforum_init` routines):
  GET  /health      -> {status, graph_loaded, pairs_tracked, research_cycles, uptime_seconds, llm_provider}
  POST /research    -> {pair, decision, direction, confidence, rationale, risk_assessment,
                         bull_case, bear_case, reports, timestamp, request_id}
  GET  /history/{pair}?limit=20
  GET  /            -> metadata

Run:
  python agents/gateforum/server/gateforum_server.py --host 127.0.0.1 --port 8500
Set GATEFORUM_API_KEY (or leave CUSTOM_LLM_API_KEY/OPENCODE_GO_API_KEY set in the env) for live LLM research; leave mock_mode:true
in gateforum_config.yaml (or --mock) to exercise the full pipeline without a key.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from research_engine import EngineConfig, run_research

logger = logging.getLogger("gateforum")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [GATEFORUM] %(levelname)s %(message)s",
)

CONFIG = EngineConfig.load()
SESSION_START = datetime.now(timezone.utc)
DECISION_HISTORY: dict[str, list[dict]] = {}

# ── Session tracking ────────────────────────────────────────────────────
# A "session" is one cycle of the research floor. The server bumps the session
# id when a new /research arrives after a quiet gap (SESSION_GAP_SECONDS), so a
# loop that researches BTC/XAU/CL in quick succession shares one session id and
# the next loop starts a fresh one. Per-pair active flags let the public page
# show "Research in Progress".
# The counter + last-activity are persisted so a server restart continues
# numbering instead of resetting to 1 (which would merge post-restart research
# into an old session id).
SESSION_STATE_FILE = Path(__file__).resolve().parent / "session_state.json"
CURRENT_SESSION = 1
LAST_RESEARCH_AT = 0.0
SESSION_GAP_SECONDS = 120.0
ACTIVE_RESEARCH: set[str] = set()
# Progressive stream: pair -> turns posted so far while a research cycle is running.
ACTIVE_TRANSCRIPTS: dict[str, list[dict]] = {}


def _load_session_state() -> None:
    global CURRENT_SESSION, LAST_RESEARCH_AT
    try:
        if SESSION_STATE_FILE.exists():
            data = json.loads(SESSION_STATE_FILE.read_text())
            CURRENT_SESSION = int(data.get("current_session", 1) or 1)
            LAST_RESEARCH_AT = float(data.get("last_research_at", 0.0) or 0.0)
    except Exception:
        pass


def _save_session_state() -> None:
    try:
        SESSION_STATE_FILE.write_text(
            json.dumps({"current_session": CURRENT_SESSION, "last_research_at": LAST_RESEARCH_AT})
        )
    except Exception:
        pass


_load_session_state()

app = FastAPI(title="GateForum — Multi-Agent Research & Decision Trader", version="2.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)


# ── Models ────────────────────────────────────────────────────────────────
class ResearchRequest(BaseModel):
    pair: str
    trading_pair: str | None = None
    market_data: str = ""
    news_data: str = ""
    social_data: str = ""
    fundamentals_data: str = ""


class ResearchResponse(BaseModel):
    pair: str
    decision: str
    direction: str
    confidence: int
    rationale: str
    risk_assessment: str
    bull_case: str
    bear_case: str
    reports: dict[str, str]
    transcript: list[dict] = []
    session_id: int = 1
    timestamp: str
    request_id: str


# ── Helpers ──────────────────────────────────────────────────────────────
def _track(pair: str, record: dict) -> None:
    DECISION_HISTORY.setdefault(pair, []).append(record)
    if len(DECISION_HISTORY[pair]) > 500:
        DECISION_HISTORY[pair] = DECISION_HISTORY[pair][-500:]


# ── Routes ───────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    total = sum(len(v) for v in DECISION_HISTORY.values())
    return {
        "status": "ready",
        "graph_loaded": True,
        "pairs_tracked": CONFIG.pairs,
        "research_cycles": total,
        "uptime_seconds": (datetime.now(timezone.utc) - SESSION_START).total_seconds(),
        "llm_provider": CONFIG.model if not CONFIG.mock_mode else f"{CONFIG.model} (mock)",
        "current_session": CURRENT_SESSION,
        "active_research": sorted(ACTIVE_RESEARCH),
    }


@app.get("/sessions")
async def sessions():
    """All research cycles grouped by session id, renumbered consecutively by start time."""
    grouped: dict[int, list[dict]] = {}
    for pair, records in DECISION_HISTORY.items():
        for rec in records:
            sid = rec.get("session_id", 1)
            grouped.setdefault(sid, []).append(rec)
    ordered = sorted(grouped, key=lambda sid: min(r.get("timestamp", "") for r in grouped[sid]))
    out = []
    for n, sid in enumerate(ordered, start=1):
        out.append(
            {
                "session_id": n,  # renumbered 1..N so the UI never shows gaps
                "server_session": sid,
                "started_at": min(r.get("timestamp", "") for r in grouped[sid]),
                "research": grouped[sid],
            }
        )
    return {"current_session": out[-1]["session_id"] if out else 1, "sessions": out}


@app.post("/research", response_model=ResearchResponse)
async def research(req: ResearchRequest):
    pair = req.trading_pair or req.pair
    if not pair:
        raise HTTPException(status_code=400, detail="pair is required")

    # Validate the asset is on our research list (by base symbol or full pair).
    base = pair.split("-")[0].upper()
    known = {p.split("-")[0].upper() for p in CONFIG.pairs}
    if base not in known and pair.upper() not in {p.upper() for p in CONFIG.pairs}:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported pair: {pair}. Supported: {CONFIG.pairs}",
        )

    if not CONFIG.mock_mode and not CONFIG.api_key_set:
        raise HTTPException(
            status_code=503,
            detail="No LLM API key configured (set GATEFORUM_API_KEY, CUSTOM_LLM_API_KEY or OPENCODE_GO_API_KEY). Enable mock_mode to test without one.",
        )

    packet = {
        "market_data": req.market_data,
        "fundamentals": req.fundamentals_data,
        "news_data": req.news_data,
        "social_data": req.social_data,
    }

    # Session bookkeeping: a fresh burst after a quiet gap starts a new session.
    global CURRENT_SESSION, LAST_RESEARCH_AT
    now = time.time()
    if LAST_RESEARCH_AT and now - LAST_RESEARCH_AT > SESSION_GAP_SECONDS:
        CURRENT_SESSION += 1
    LAST_RESEARCH_AT = now
    _save_session_state()
    ACTIVE_RESEARCH.add(pair)
    ACTIVE_TRANSCRIPTS[pair] = []

    async def _on_turn(turn: dict) -> None:
        ACTIVE_TRANSCRIPTS.setdefault(pair, []).append(turn)

    try:
        result = await run_research(pair, packet, CONFIG, on_turn=_on_turn)
    except Exception as exc:  # noqa: BLE001
        ACTIVE_RESEARCH.discard(pair)
        ACTIVE_TRANSCRIPTS.pop(pair, None)
        logger.exception("Research failed for %s", pair)
        raise HTTPException(status_code=500, detail=f"Research failed: {exc}")
    finally:
        ACTIVE_RESEARCH.discard(pair)

    result["timestamp"] = datetime.now(timezone.utc).isoformat()
    result["request_id"] = hashlib.sha256(
        f"{pair}:{result['timestamp']}".encode()
    ).hexdigest()[:12]
    result["session_id"] = CURRENT_SESSION

    _track(pair, result)
    # The live stream is replaced by the authoritative stored transcript.
    ACTIVE_TRANSCRIPTS.pop(pair, None)
    return ResearchResponse(**{k: result[k] for k in ResearchResponse.model_fields})


@app.get("/active/{pair}")
async def active_transcript(pair: str):
    """Progressive transcript for a research cycle currently running, if any."""
    return {
        "pair": pair,
        "running": pair in ACTIVE_RESEARCH,
        "turns": ACTIVE_TRANSCRIPTS.get(pair, []),
    }


@app.get("/history/{pair}")
async def history(pair: str, limit: int = 20):
    return {"pair": pair, "decisions": DECISION_HISTORY.get(pair, [])[-limit:], "count": len(DECISION_HISTORY.get(pair, []))}


@app.get("/")
async def root():
    return {
        "name": "GateForum Server",
        "version": "2.0.0",
        "framework": "self-contained research engine (OpenAI-compatible)",
        "mock_mode": CONFIG.mock_mode,
        "endpoints": ["/health", "/research", "/history/{pair}"],
    }


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="GateForum Research Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8500)
    parser.add_argument("--mock", action="store_true", help="Force mock mode (no LLM key needed)")
    args = parser.parse_args()

    if args.mock:
        CONFIG.mock_mode = True

    if CONFIG.mock_mode:
        logger.warning("MOCK MODE: research returns deterministic verdicts (no real LLM).")
    elif not CONFIG.api_key_set:
        logger.warning("No GATEFORUM_API_KEY / CUSTOM_LLM_API_KEY set — live research will fail. Use --mock to test.")

    logger.info("Starting GateForum Server on %s:%d (model=%s)", args.host, args.port, CONFIG.model)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
