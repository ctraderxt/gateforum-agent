"""Start and health-check the GateForum research server + public floor.

Idempotent: if a healthy server is already listening it returns immediately.
Otherwise it launches `gateforum_server.py` (and the public page on :8600) in
its own process group so they outlive the tick that started them, then polls
/health until ready. Starting a GateForum session is enough — no extra process.
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
    public_url: str = Field(
        default="http://127.0.0.1:8600",
        description="Public research-floor page (auto-started with the server)",
    )
    public_script: str = Field(
        default="agents/gateforum/server/gateforum_public.py",
        description="Path to the public floor, relative to the repo root",
    )
    public_host: str = Field(default="127.0.0.1")
    public_port: int = Field(default=8600)


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


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and val:
                out[key] = val
    except OSError:
        pass
    return out


def _server_env() -> dict[str, str]:
    """Env for the research server. Pulls LLM keys from Condor + Hermes dotenv."""
    env = os.environ.copy()
    env["HOME"] = str(Path.home())
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    home = Path.home()
    candidates = [
        _repo_root() / ".env",
        home / ".env",
        home.parent / ".env",
        home / ".hermes" / ".env",
        home / "condor" / ".env",
    ]
    for path in candidates:
        if path.is_file():
            for k, v in _parse_env_file(path).items():
                env.setdefault(k, v)
    zen = env.get("OPENCODE_ZEN_API_KEY") or env.get("OPENCODE_GO_API_KEY") or ""
    if zen:
        env.setdefault("CUSTOM_LLM_API_KEY", zen)
        env.setdefault("OPENCODE_GO_API_KEY", zen)
        env.setdefault("GATEFORUM_API_KEY", zen)
    return env


def _stop_listener(port: int = 8500) -> None:
    """Drop a stale research server so we can relaunch with keys."""
    try:
        subprocess.run(
            ["fuser", "-k", f"{port}/tcp"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
    except Exception:  # noqa: BLE001
        pass


def _launch(script: Path, log_name: str = "gateforum_server.log", extra_args: list[str] | None = None) -> str:
    log_path = script.parent / log_name
    cmd = [sys.executable, str(script)] + (extra_args or [])
    with open(log_path, "ab") as log:
        subprocess.Popen(
            cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=str(_repo_root()),
            start_new_session=True,  # survives the tick process
            env=_server_env(),
        )
    return str(log_path)


async def _ensure_public(session: aiohttp.ClientSession, config: Config) -> str:
    """Bring up the judge-facing floor on :8600. Never blocks trading."""
    health = await _health(session, config.public_url)
    if health is not None:
        return f"already up at {config.public_url}"
    script = _repo_root() / config.public_script
    if not script.exists():
        return f"script missing ({script})"
    try:
        _launch(
            script,
            log_name="gateforum_public.log",
            extra_args=["--host", config.public_host, "--port", str(config.public_port)],
        )
    except Exception as exc:  # noqa: BLE001
        return f"launch failed: {type(exc).__name__}: {exc}"
    deadline = asyncio.get_event_loop().time() + 20
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(1)
        if await _health(session, config.public_url):
            return f"launched at {config.public_url}"
    return f"launched but not answering yet ({config.public_url})"


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE):
    """Return once the research server is healthy, or report why it is not."""
    started = False
    log_path = ""

    async with aiohttp.ClientSession() as session:
        health = await _health(session, config.server_url)

        needs_launch = health is None or (
            config.autostart and health.get("llm_ready") is not True
        )
        if needs_launch and config.autostart:
            if health is not None:
                _stop_listener(8500)
                await asyncio.sleep(1)
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

        public_note = "skipped"
        if health is not None and config.autostart:
            try:
                public_note = await _ensure_public(session, config)
            except Exception as exc:  # noqa: BLE001
                public_note = f"error: {type(exc).__name__}: {exc}"

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
        {"Field": "LLM Ready", "Value": "yes" if health.get("llm_ready", True) else "no"},
        {"Field": "Graph Loaded", "Value": str(health.get("graph_loaded", "n/a"))},
        {"Field": "Research Cycles", "Value": str(health.get("research_cycles", 0))},
        {"Field": "Uptime (s)", "Value": str(health.get("uptime_seconds", 0))},
        {"Field": "Launched This Tick", "Value": "yes" if started else "no"},
        {"Field": "Research Floor", "Value": public_note},
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
        text=(
            f"GateForum research server {verb} at {config.server_url}. "
            f"Research floor: {public_note}."
        ),
        table_data=rows,
        table_columns=columns,
    )
