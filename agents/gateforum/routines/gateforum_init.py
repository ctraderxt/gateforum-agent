"""Start and health-check the GateForum research server.

Idempotent: if a healthy server is already listening it returns immediately.
Otherwise it launches `gateforum_server.py` in its own process group so the server
outlives the tick that started it, then polls /health until ready.
"""

import asyncio
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

CATEGORY = "Monitoring"


class Config(BaseModel):
    """Ensure the GateForum research server is running and healthy."""

    server_url: str = Field(
        default="http://127.0.0.1:8500", description="GateForum research server base URL"
    )
    server_script: str = Field(
        default="agents/gateforum/server/gateforum_server.py",
        description="Path to the research server, relative to the repo root",
    )
    health_check_timeout: int = Field(
        default=90, description="Seconds to wait for the server to report ready"
    )
    autostart: bool = Field(
        default=True, description="Launch the server if it is not already running"
    )


async def _health(session: aiohttp.ClientSession, url: str) -> dict | None:
    try:
        async with session.get(f"{url}/health", timeout=8) as resp:
            if resp.status != 200:
                return None
            return await resp.json()
    except Exception:  # noqa: BLE001 - "not up yet" is the normal path here
        return None


def _repo_root() -> Path:
    # routines/ -> gateforum/ -> agents/ -> repo root
    return Path(__file__).resolve().parents[3]


def _launch(script: Path) -> str:
    log_path = script.parent / "gateforum_server.log"
    with open(log_path, "ab") as log:
        subprocess.Popen(
            [sys.executable, str(script)],
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=str(_repo_root()),
            start_new_session=True,  # survives the tick process
            env=os.environ.copy(),
        )
    return str(log_path)


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE):
    """Return once the research server is healthy, or report why it is not."""
    started = False
    log_path = ""

    async with aiohttp.ClientSession() as session:
        health = await _health(session, config.server_url)

        if health is None and config.autostart:
            script = _repo_root() / config.server_script
            if not script.exists():
                return (
                    f"GateForum server script not found at {script}. "
                    "Install the research server before launching the agent."
                )
            try:
                log_path = _launch(script)
                started = True
            except Exception as exc:  # noqa: BLE001
                return f"Failed to launch GateForum server: {type(exc).__name__}: {exc}"

            deadline = asyncio.get_event_loop().time() + config.health_check_timeout
            while asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(3)
                health = await _health(session, config.server_url)
                if health:
                    break

    if health is None:
        return (
            f"GateForum server did not become healthy within {config.health_check_timeout}s "
            f"at {config.server_url}. Check {log_path or 'the server log'}. "
            "Do not trade this tick."
        )

    rows = [
        {"Field": "Status", "Value": str(health.get("status", "ready"))},
        {"Field": "Endpoint", "Value": config.server_url},
        {"Field": "LLM Provider", "Value": str(health.get("llm_provider", "n/a"))},
        {"Field": "Graph Loaded", "Value": str(health.get("graph_loaded", "n/a"))},
        {"Field": "Research Cycles", "Value": str(health.get("research_cycles", 0))},
        {"Field": "Uptime (s)", "Value": str(health.get("uptime_seconds", 0))},
        {"Field": "Launched This Tick", "Value": "yes" if started else "no"},
    ]
    columns = ["Field", "Value"]

    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder("GateForum — Server Health")
        builder.source("routine", "gateforum_init")
        builder.tags(["gateforum", "infrastructure"])
        builder.kpi("Status", str(health.get("status", "ready")).upper())
        builder.kpi("Provider", str(health.get("llm_provider", "n/a")))
        builder.kpi("Research Cycles", str(health.get("research_cycles", 0)))
        builder.kpi("Checked", datetime.now(timezone.utc).strftime("%H:%M:%S UTC"))
        builder.section("01 / RESEARCH SERVER", "Runtime backing the multi-agent research.")
        builder.table(rows, columns)
        builder.manual_order()
        await builder.save()
    except Exception as exc:  # noqa: BLE001
        logger.warning("gateforum_init: report generation failed: %s", exc)

    from routines.base import RoutineResult

    verb = "launched and healthy" if started else "already healthy"
    return RoutineResult(
        text=f"GateForum research server {verb} at {config.server_url}.",
        table_data=rows,
        table_columns=columns,
    )
