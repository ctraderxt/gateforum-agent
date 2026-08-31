#!/usr/bin/env python3
"""GateForum stack healer — no LLM, no Telegram spam.

Silent (exit 0, empty stdout) when the stack is healthy.
Prints what it repaired when something was down.

Checks / repairs, in order:
  1. Hummingbot API (docker compose)
  2. Condor web dashboard (:8088) — HOME set so Claude login works
  3. Research server (:8500, claude-cli / sonnet)
  4. Public research floor (:8600)
  5. GateForum trading session (loop)
  6. Orphan Gate perps (open position, no RUNNING executor) — close them

Designed for a systemd user timer every 15 minutes and for organizer testing
on this WSL box. Does not require the Hermes gateway.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REAL_HOME = Path.home()
CONDOR_ROOT = REAL_HOME / "condor"
HB_API_ROOT = REAL_HOME / "hummingbot-api"
VENV_PY = CONDOR_ROOT / ".venv" / "bin" / "python"
UV = REAL_HOME / ".local" / "bin" / "uv"
AGENT = "gateforum"
STRATEGY = "gateforum_debate_operator"
ADMIN_SUB = "gateforum-admin"
WEB_PORT = 8088
LOG = Path("/tmp/gateforum-heal.log")

SESSION_CONFIG = {
    "execution_mode": "loop",
    "frequency_sec": 900,
    "tick_timeout_sec": 600,
    "max_ticks": 0,
    "total_amount_quote": 40,
    "server_name": "GateForum-Agent",
    "trading_context": "Trade BTC-USDT, XAU-USDT and CL-USDT on gate_io_perpetual",
    "risk_limits": {
        "max_position_size_quote": 48,
        "max_open_executors": 3,
        "max_drawdown_pct": 8,
        "max_leverage": 2,
        "require_triple_barrier": True,
        "require_trailing_stop": True,
    },
}


def _log(msg: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg + "\n"
    try:
        LOG.open("a").write(line)
    except OSError:
        pass


def _load_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore

        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


def _cfg() -> dict:
    return _load_yaml(CONDOR_ROOT / "config.yml")


def _condor_env() -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(REAL_HOME)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    env["WEB_HOST"] = "127.0.0.1"
    env.setdefault("WEB_PORT", str(WEB_PORT))
    env["PATH"] = (
        f"{REAL_HOME / '.local' / 'bin'}:{CONDOR_ROOT / '.venv' / 'bin'}:"
        + env.get("PATH", "")
    )
    return env


def _http(method: str, url: str, headers: dict | None = None, body=None, timeout: int = 12):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                parsed = json.loads(raw.decode()) if raw else None
            except Exception:
                parsed = raw.decode(errors="replace")[:400]
            return r.status, parsed
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")[:400]
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        return e.code, parsed
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def _up(url: str, timeout: int = 4) -> bool:
    st, _ = _http("GET", url, timeout=timeout)
    return 200 <= int(st) < 500 and st != 0


def _jwt() -> str:
    cfg = _cfg()
    secret = (cfg.get("web_jwt_secret") or "").strip()
    if not secret:
        return ""
    try:
        from jose import jwt as jose_jwt  # type: ignore
    except Exception:
        return ""
    return jose_jwt.encode(
        {
            "sub": ADMIN_SUB,
            "username": "admin",
            "first_name": "GateForum",
            "role": "admin",
            "exp": int(time.time()) + 3600,
        },
        secret,
        algorithm="HS256",
    )


def _server_name(cfg: dict) -> str:
    servers = cfg.get("servers") or {}
    if "GateForum-Agent" in servers:
        return "GateForum-Agent"
    if servers:
        return next(iter(servers))
    return "GateForum-Agent"


def _hb_auth(cfg: dict) -> str:
    import base64

    name = _server_name(cfg)
    srv = (cfg.get("servers") or {}).get(name) or {}
    user = str(srv.get("username") or "condor_api")
    pw = str(srv.get("password") or "")
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


def _spawn(cmd: list[str], cwd: Path, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        subprocess.Popen(
            cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=str(cwd),
            start_new_session=True,
            env=_condor_env(),
        )


def _wait(url: str, seconds: int = 40) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if _up(url):
            return True
        time.sleep(1.5)
    return False


def ensure_hb_api(fixed: list[str]) -> None:
    if _up("http://127.0.0.1:8000/"):
        return
    _log("hb-api down — docker compose up -d")
    subprocess.run(
        ["docker", "compose", "up", "-d"],
        cwd=str(HB_API_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )
    if _wait("http://127.0.0.1:8000/", 60):
        fixed.append("hummingbot-api")
    else:
        fixed.append("hummingbot-api:still-down")


def ensure_condor(fixed: list[str]) -> None:
    if _up(f"http://127.0.0.1:{WEB_PORT}/"):
        return
    _log("condor :8088 down — starting with HOME set")
    cmd = [str(UV), "run", "python", "main.py"] if UV.is_file() else [str(VENV_PY), "main.py"]
    _spawn(cmd, CONDOR_ROOT, Path("/tmp/gateforum-condor.log"))
    if _wait(f"http://127.0.0.1:{WEB_PORT}/", 45):
        fixed.append("condor")
    else:
        fixed.append("condor:still-down")


def ensure_research(fixed: list[str]) -> None:
    st, body = _http("GET", "http://127.0.0.1:8500/health", timeout=5)
    healthy = st == 200 and isinstance(body, dict) and body.get("status") == "ready"
    provider = str((body or {}).get("llm_provider") or "") if isinstance(body, dict) else ""
    # hy3 / opencode-go is the dead monthly-quota path — bounce onto claude-cli.
    wrong = healthy and provider and "sonnet" not in provider.lower() and "claude" not in provider.lower()
    if healthy and not wrong:
        return
    if st != 0:
        subprocess.run(
            ["fuser", "-k", "8500/tcp"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
        time.sleep(1)
    script = CONDOR_ROOT / "agents/gateforum/server/gateforum_server.py"
    _spawn(
        [str(VENV_PY), str(script), "--host", "127.0.0.1", "--port", "8500"],
        CONDOR_ROOT,
        CONDOR_ROOT / "agents/gateforum/server/gateforum_server.log",
    )
    if _wait("http://127.0.0.1:8500/health", 20):
        fixed.append("research-server")
    else:
        fixed.append("research-server:still-down")


def ensure_floor(fixed: list[str]) -> None:
    if _up("http://127.0.0.1:8600/health"):
        return
    script = CONDOR_ROOT / "agents/gateforum/server/gateforum_public.py"
    _spawn(
        [str(VENV_PY), str(script), "--host", "127.0.0.1", "--port", "8600"],
        CONDOR_ROOT,
        CONDOR_ROOT / "agents/gateforum/server/gateforum_public.log",
    )
    if _wait("http://127.0.0.1:8600/health", 15):
        fixed.append("research-floor")
    else:
        fixed.append("research-floor:still-down")


def ensure_session(fixed: list[str]) -> None:
    jwt = _jwt()
    if not jwt:
        fixed.append("session:no-jwt")
        return
    headers = {"Authorization": f"Bearer {jwt}"}
    url = f"http://127.0.0.1:{WEB_PORT}/api/v1/agents/{AGENT}/strategies/{STRATEGY}"
    st, body = _http("GET", url, headers=headers, timeout=10)
    status = (body or {}).get("status") if isinstance(body, dict) else ""
    if status == "running":
        return
    st2, body2 = _http(
        "POST",
        url + "/start",
        headers=headers,
        body={"config": SESSION_CONFIG, "chat_id": ADMIN_SUB},
        timeout=30,
    )
    if st2 == 200:
        fixed.append("session")
        _log(f"started session {body2}")
    else:
        fixed.append(f"session:start-failed:{st2}")


def close_orphans(fixed: list[str]) -> None:
    """Close Gate perps that have no RUNNING executor managing them."""
    cfg = _cfg()
    auth = _hb_auth(cfg)
    headers = {"Authorization": auth}
    st, pos = _http(
        "POST",
        "http://127.0.0.1:8000/trading/positions",
        headers=headers,
        body={"account_name": "master_account", "connector_name": "gate_io_perpetual"},
        timeout=15,
    )
    if st != 200 or not isinstance(pos, dict):
        return
    rows = pos.get("data") or []
    open_pairs = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        pair = r.get("trading_pair")
        amt = float(r.get("amount") or 0)
        px = float(r.get("entry_price") or 0)
        if pair and abs(amt * px) >= 1.0:
            open_pairs.add(pair)
    if not open_pairs:
        return
    st, ex = _http(
        "POST",
        "http://127.0.0.1:8000/executors/search",
        headers=headers,
        body={"status": "RUNNING"},
        timeout=15,
    )
    managed: set[str] = set()
    data = (ex or {}).get("data") if isinstance(ex, dict) else ex
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            cfg_e = item.get("config") or {}
            pair = item.get("trading_pair") or cfg_e.get("trading_pair")
            conn = item.get("connector_name") or cfg_e.get("connector_name")
            if conn == "gate_io_perpetual" and pair:
                managed.add(pair)
    orphans = sorted(open_pairs - managed)
    for pair in orphans:
        st_s, found = _http(
            "POST",
            "http://127.0.0.1:8000/executors/search",
            headers=headers,
            body={"trading_pairs": [pair]},
            timeout=15,
        )
        closed = False
        found_data = (found or {}).get("data") if isinstance(found, dict) else []
        if isinstance(found_data, list):
            for item in found_data:
                eid = (item or {}).get("id") or (item or {}).get("executor_id")
                if not eid:
                    continue
                st_stop, _ = _http(
                    "POST",
                    f"http://127.0.0.1:8000/executors/{eid}/stop",
                    headers=headers,
                    body={"close_position": True},
                    timeout=30,
                )
                if st_stop in (200, 201):
                    closed = True
                    break
        if closed:
            fixed.append(f"orphan-closed:{pair}")
            _log(f"closed orphan {pair}")
        else:
            fixed.append(f"orphan-open:{pair}")
            _log(f"orphan still open {pair} (no executor to stop)")


def main() -> int:
    os.environ["HOME"] = str(REAL_HOME)
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
    fixed: list[str] = []
    try:
        ensure_hb_api(fixed)
        ensure_condor(fixed)
        ensure_research(fixed)
        ensure_floor(fixed)
        ensure_session(fixed)
        close_orphans(fixed)
    except Exception as exc:  # noqa: BLE001
        msg = f"heal-crash: {type(exc).__name__}: {exc}"
        _log(msg)
        print(msg)
        return 1
    if fixed:
        msg = "GateForum healed: " + ", ".join(fixed)
        _log(msg)
        print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
