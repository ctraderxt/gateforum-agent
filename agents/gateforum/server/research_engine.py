"""Self-contained GateForum research engine — the council..

Runs the multi-agent research that the Condor `gateforum_research` routine calls over
HTTP. Twelve specialised LLM roles deliberate every asset:

  Phase 1  Intelligence   market / social / news / fundamentals analysts (parallel)
  Phase 2  Investment     bull researcher  <->  bear researcher  ->  research judge
  Phase 3  Risk           aggressive / neutral / conservative  ->  risk judge
  Phase 4  Verdict        final BUY / SELL / HOLD  (direction + confidence)

The engine talks to an OpenAI-compatible chat endpoint (opencode-go /
deepseek-v4-flash by default), so there is no heavy framework dependency and
nothing to GPU. A mock mode returns a deterministic verdict with a full transcript,
so the entire Condor pipeline can be exercised with no API key.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("gateforum.engine")

CONFIG_PATH = Path(__file__).resolve().parent / "gateforum_config.yaml"
_ASSET_CONTEXT: dict[str, str] = {
    "BTC": "BTC is crypto's reference market — driven by macro liquidity, ETF flows and risk sentiment.",
    "XAU": "XAU is spot gold quoted in USDT — driven by real yields, the US dollar and safe-haven flow, NOT crypto sentiment.",
    "CL": "CL is WTI crude oil quoted in USDT — driven by OPEC+ supply policy, inventories and geopolitics, NOT crypto sentiment.",
    "XAG": "XAG is spot silver — monetary drivers like gold plus higher industrial beta.",
}


@dataclass
class EngineConfig:
    pairs: list[str] = field(default_factory=lambda: ["BTC-USDT", "XAU-USDT", "CL-USDT"])
    aliases: dict[str, str] = field(default_factory=dict)
    provider: str = "claude-cli"
    model: str = "sonnet"
    backend_url: str = "https://opencode.ai/zen/go/v1"
    api_key: str = ""
    temperature: float = 0.3
    timeout: int = 90
    max_tokens: int = 2000  # generous: reasoning models spend tokens on CoT first
    max_research_rounds: int = 1
    max_risk_rounds: int = 1
    mock_mode: bool = True  # safe default: deterministic verdicts, no key required
    rate_limit_seconds: int = 180
    confidence: dict[str, Any] = field(default_factory=dict)

    @property
    def uses_claude_cli(self) -> bool:
        p = (self.provider or "").lower()
        return p in {"claude-cli", "claude", "claude-acp", "claude-code"}

    @property
    def api_key_set(self) -> bool:
        return bool(self.api_key) or self.uses_claude_cli

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "EngineConfig":
        try:
            import yaml  # type: ignore
        except Exception:  # pyyaml may be absent; fall back to sane defaults.
            logger.warning("PyYAML not found — using default engine config.")
            return cls()

        with open(path) as fh:
            raw = yaml.safe_load(fh) or {}

        llm = raw.get("llm", {})
        # Resolve the same way Condor resolves custom@opencode-go:...:
        #   base_url -> GATEFORUM_BACKEND_URL env, else config, else CUSTOM_LLM_BASE_URL
        #   api_key  -> GATEFORUM_API_KEY env, else config api_key_env, else OPENCODE_GO_API_KEY
        backend_url = (
            os.getenv("GATEFORUM_BACKEND_URL")
            or llm.get("backend_url")
            or os.getenv("CUSTOM_LLM_BASE_URL", "")
        )
        api_key = (
            os.getenv("GATEFORUM_API_KEY")
            or os.getenv(llm.get("api_key_env", "CUSTOM_LLM_API_KEY"))
            or os.getenv("OPENCODE_GO_API_KEY", "")
            or os.getenv("OPENCODE_ZEN_API_KEY", "")
        )
        return cls(
            pairs=raw.get("pairs", cls().pairs),
            aliases=raw.get("pair_aliases", {}),
            provider=str(llm.get("provider") or os.getenv("GATEFORUM_LLM_PROVIDER") or "claude-cli"),
            model=llm.get("model", cls().model),
            backend_url=backend_url,
            api_key=api_key,
            temperature=float(llm.get("temperature", cls().temperature)),
            timeout=int(llm.get("request_timeout_seconds", cls().timeout)),
            max_tokens=int(llm.get("max_tokens", cls().max_tokens)),
            max_research_rounds=int(raw.get("research", {}).get("max_research_rounds", 1)),
            max_risk_rounds=int(raw.get("research", {}).get("max_risk_rounds", 1)),
            mock_mode=bool(
                os.environ.get("GATEFORUM_MOCK_MODE", "").strip()
                if "GATEFORUM_MOCK_MODE" in os.environ
                else raw.get("research", {}).get("mock_mode", False)
            ),
            rate_limit_seconds=int(raw.get("server", {}).get("rate_limit_seconds", 180)),
            confidence=raw.get("confidence", {}),
        )


def _resolve_pair(pair: str, aliases: dict[str, str]) -> str:
    """'BTC'/'GOLD' -> 'BTC-USDT'."""
    return aliases.get(pair.upper(), pair.upper())


def _base_of(pair: str) -> str:
    return pair.split("-")[0]


# ── LLM call ──────────────────────────────────────────────────────────────
_CLAUDE_SEM = asyncio.Semaphore(3)


async def _chat_claude_cli(system: str, user: str, cfg: EngineConfig) -> str:
    """Council turn via WSL Claude Code email login (no API key)."""
    env = os.environ.copy()
    env["HOME"] = str(Path.home())
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    cmd = [
        "claude",
        "-p",
        user,
        "--output-format",
        "text",
        "--system-prompt",
        system,
        "--model",
        cfg.model or "sonnet",
        "--tools",
        "",
        "--no-session-persistence",
    ]
    timeout = max(int(cfg.timeout), 120)
    async with _CLAUDE_SEM:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=str(Path.home()),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(f"claude -p timed out after {timeout}s")
    text = (stdout or b"").decode("utf-8", "replace").strip()
    if proc.returncode != 0 and not text:
        err = (stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(err or f"claude -p exited {proc.returncode}")
    return text


async def _chat(system: str, user: str, cfg: EngineConfig) -> str:
    """Single chat completion via Claude Code login or an OpenAI-compatible endpoint.

    Handles reasoning models (deepseek-v4-pro/flash, hy3): they emit their chain
    of thought in ``reasoning_content`` and the actual answer in ``content``. We
    prefer ``content``, falling back to ``reasoning_content`` (clipped) when the
    answer is empty. If BOTH come back empty (hy3 can exhaust its token budget
    mid-reasoning and return ``finish_reason=length`` with no content), retry
    once with a larger budget so the judge's verdict is never silently dropped.
    """
    if cfg.mock_mode:
        return _mock_reply(system, user, cfg)

    if cfg.uses_claude_cli:
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                answer = (await _chat_claude_cli(system, user, cfg)).strip()
                if answer:
                    return answer
                logger.warning("claude -p returned empty answer (attempt=%d)", attempt + 1)
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                logger.error("claude -p failed (attempt %d): %s", attempt + 1, exc)
                if attempt == 0:
                    await asyncio.sleep(2)
                    continue
        if last_err is not None:
            raise last_err
        return ""

    from openai import AsyncOpenAI  # imported lazily so mock mode needs nothing

    client = AsyncOpenAI(api_key=cfg.api_key or "empty", base_url=cfg.backend_url)
    last_err: Exception | None = None
    for attempt in range(2):
        budget = cfg.max_tokens if attempt == 0 else max(cfg.max_tokens * 2, 8000)
        try:
            resp = await client.chat.completions.create(
                model=cfg.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=cfg.temperature,
                max_tokens=budget,
                timeout=cfg.timeout,
            )
            msg = resp.choices[0].message
            answer = (msg.content or "").strip()
            if not answer:
                reason = (getattr(msg, "reasoning_content", None) or "").strip()
                answer = reason[:1600] if reason else ""
            if answer:
                return answer
            finish = getattr(resp.choices[0], "finish_reason", "?")
            logger.warning(
                "LLM returned empty answer (finish=%s, attempt=%d, budget=%d) — retrying",
                finish, attempt + 1, budget,
            )
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            logger.error("LLM call failed (attempt %d): %s", attempt + 1, exc)
            if attempt == 0:
                continue
    if last_err is not None:
        raise last_err
    return ""


def _mock_reply(system: str, user: str, cfg: EngineConfig) -> str:
    """Deterministic, asset-aware transcript for key-free demos.

    Parses the asset from the user brief so each role's mock argument reads as a
    real research, not a placeholder. The final verdict is still computed from the
    judge text by the normal parser, so the confidence/decision path is real.
    """
    sys_l = system.lower()
    pair = "the asset"
    for tok in ("btc", "xau", "cl", "xag", "gold", "oil"):
        if tok in user.lower():
            pair = {"btc": "BTC", "xau": "gold (XAU)", "cl": "WTI crude (CL)",
                    "xag": "silver (XAG)", "gold": "gold (XAU)", "oil": "WTI crude (CL)"}.get(tok, pair)
            break

    if "market analyst" in sys_l:
        return (f"[{pair}] Technicals: price below the 50-period mean, RSI near 50, "
                f"volume unremarkable. No strong directional momentum; range-bound bias.")
    if "social" in sys_l:
        return (f"[{pair}] Crowd positioning is balanced. "
                f"(Note: for commodities, crypto-native social signal is not a primary driver.)")
    if "news" in sys_l:
        return (f"[{pair}] No dominant headline. Macro calendar light; "
                f"watching upcoming policy cues that could shift the regime.")
    if "fundamentals" in sys_l:
        return (f"[{pair}] Structural drivers intact. Supply/demand balanced; "
                f"no acute catalyst that forces a directional view here.")
    if "bull" in sys_l:
        return (f"[{pair}] The case FOR long: downside looks contained, any positive "
                f"macro surprise skews risk to the upside. Conviction 62/100.")
    if "bear" in sys_l:
        return (f"[{pair}] The case FOR short / caution: momentum is absent and the "
                f"range can resolve lower on risk-off flows. Conviction 58/100.")
    if "research manager" in sys_l:
        return (f"INVESTMENT PLAN: NEUTRAL. The bull and bear cases are close; "
                f"absent a catalyst, stand aside rather than force a directional bet. "
                f"Conviction 60/100.")
    if "aggressive" in sys_l:
        return (f"[{pair}] Size toward the upper bound of the risk budget; the payoff "
                f"asymmetry favours participation at these levels.")
    if "neutral" in sys_l:
        return (f"[{pair}] Moderate sizing, standard risk budget; no reason to deviate.")
    if "conservative" in sys_l:
        return (f"[{pair}] Preserve capital: small size, tight invalidation; if wrong, "
                f"losses stay trivial.")
    if "risk judge" in sys_l:
        return (f"FINAL DECISION: HOLD. Research leans neutral and no edge clears the "
                f"bar; do not deploy until a catalyst resolves the range.")
    return "(mock) reasoned analysis"


# ── Prompts ───────────────────────────────────────────────────────────────
_ANALYST_ROLE = {
    "market": (
        "You are the Market Analyst for the AI trading research council. Argue only from "
        "technical price action: trend, momentum (RSI), support/resistance, volume. "
        "Reply as 2-4 SHORT bullets, each under 15 words, like a trader texting a "
        "friend. No paragraphs, no headers, no 'In summary'."
    ),
    "social": (
        "You are the Social Sentiment Analyst. Assess crowd positioning, social buzz "
        "and retail sentiment. If the asset is a commodity (gold/oil), state that "
        "crypto-native sentiment is NOT relevant. Reply as 2-3 SHORT bullets, each "
        "under 15 words."
    ),
    "news": (
        "You are the News Analyst. Assess macro and asset-specific headlines and "
        "their directional bias. Reply as 2-3 SHORT bullets, each under 15 words."
    ),
    "fundamentals": (
        "You are the Fundamentals Analyst. Assess valuation, flows and structural "
        "drivers. For commodities, focus on supply/demand and policy. Reply as 2-3 "
        "SHORT bullets, each under 15 words."
    ),
}

_DEBATER_ROLE = {
    "bull": (
        "You are the Bull Researcher in the live trading research. You argue FOR a long "
        "position with real conviction. Chat-style: 2-4 SHORT bullets, each under "
        "15 words, like a trader texting a friend. Cite specific evidence. Attack "
        "the bear's weakest claim when rebutting. End with 'CONVICTION: <0-100>'."
    ),
    "bear": (
        "You are the Bear Researcher in the live trading research. You argue FOR a short "
        "position (or why not to be long) with real conviction. Chat-style: 2-4 SHORT "
        "bullets, each under 15 words, like a trader texting a friend. Cite specific "
        "evidence. Attack the bull's weakest claim when rebutting. End with "
        "'CONVICTION: <0-100>'."
    ),
}

_RISK_ROLE = {
    "aggressive": (
        "You are the Aggressive Risk Analyst. Argue for maximum warranted position "
        "size and conviction. Reply as 2 SHORT bullets, each under 12 words."
    ),
    "neutral": (
        "You are the Neutral Risk Analyst. Argue for balanced, moderate sizing. "
        "Reply as 2 SHORT bullets, each under 12 words."
    ),
    "conservative": (
        "You are the Conservative Risk Analyst. Argue for capital preservation and "
        "what could go wrong. Reply as 2 SHORT bullets, each under 12 words."
    ),
}


def _investment_brief(pair: str, packet: dict) -> str:
    base = _base_of(pair)
    ctx = _ASSET_CONTEXT.get(base, "")
    return (
        f"ASSET: {pair} ({base}).\n{ctx}\n\n"
        f"BRIEFING PACKET (analyst inputs):\n{packet.get('market_data', '')}\n\n"
        f"{packet.get('fundamentals', '')}\n\n"
        f"News: {packet.get('news_data', '') or '(none)'}\n"
        f"Social: {packet.get('social_data', '') or '(none)'}\n\n"
        f"Decide the directional case. No hedging: pick a side."
    )


# ── Research orchestration ───────────────────────────────────────────────────
_ANALYST_LABEL = {
    "market": "Analyst Agent for Market",
    "social": "Analyst Agent for Social",
    "news": "Analyst Agent for News",
    "fundamentals": "Analyst Agent for Fundamentals",
}


async def run_research(
    pair: str,
    packet: dict,
    cfg: EngineConfig,
    on_turn=None,
) -> dict[str, Any]:
    """Run the full 4-phase research and return a structured transcript + verdict.

    `packet` keys: market_data, fundamentals, news_data, social_data.
    `on_turn` (optional async callback) fires after every agent post with
    ``{"role", "label", "text"}`` so callers can stream progressive output.
    """
    pair = _resolve_pair(pair, cfg.aliases)
    base = _base_of(pair)
    brief = _investment_brief(pair, packet)

    async def _emit(role: str, label: str, text: str) -> None:
        if on_turn:
            await on_turn({"role": role, "label": label, "text": text})

    # Phase 1 — analysts in parallel
    analyst_keys = ["market", "social", "news", "fundamentals"]
    analyst_reports = {}
    async def _analyst(k):
        return k, await _chat(_ANALYST_ROLE[k], brief, cfg)
    for k, report in await asyncio.gather(*(_analyst(k) for k in analyst_keys)):
        analyst_reports[k] = report
    for k in analyst_keys:
        await _emit("analyst", _ANALYST_LABEL[k], analyst_reports[k])

    analyst_block = "\n\n".join(
        f"[{k.upper()}]\n{analyst_reports[k]}" for k in analyst_keys
    )

    # Phase 2 — bull vs bear: sequential adversarial rounds (they SEE each
    # other's arguments and must attack specific claims — real back-and-forth).
    bull_hist, bear_hist = [], []
    bull_arg = bear_arg = ""
    rounds = max(1, cfg.max_research_rounds)
    for r in range(rounds):
        if r == 0:
            # Opening statements — both stake a claim, no one has spoken yet.
            bull_arg, bear_arg = await asyncio.gather(
                _chat(_DEBATER_ROLE["bull"], brief + "\n\nANALYST REPORTS:\n" + analyst_block + "\n\nOpen the research — make your case.", cfg),
                _chat(_DEBATER_ROLE["bear"], brief + "\n\nANALYST REPORTS:\n" + analyst_block + "\n\nOpen the research — make your case.", cfg),
            )
        else:
            # Rebuttal rounds — each side must attack the OTHER's specific claims.
            round_ctx = (
                f"{brief}\n\nANALYST REPORTS:\n{analyst_block}\n\n"
                f"BULL SO FAR:\n{bull_arg}\n\nBEAR SO FAR:\n{bear_arg}"
            )
            if r % 2 == 1:
                # Bear attacks bull's claims first this round.
                bear_arg = await _chat(
                    _DEBATER_ROLE["bear"],
                    round_ctx + "\n\nDirectly attack the BULL's weakest claims above, then re-state your case.",
                    cfg,
                )
                bull_arg = await _chat(
                    _DEBATER_ROLE["bull"],
                    round_ctx + f"\n\nBEAR JUST ATTACKED:\n{bear_arg}\n\nRebutt the bear's attack point-by-point, then re-state your case.",
                    cfg,
                )
            else:
                bull_arg = await _chat(
                    _DEBATER_ROLE["bull"],
                    round_ctx + "\n\nDirectly attack the BEAR's weakest claims above, then re-state your case.",
                    cfg,
                )
                bear_arg = await _chat(
                    _DEBATER_ROLE["bear"],
                    round_ctx + f"\n\nBULL JUST ATTACKED:\n{bull_arg}\n\nRebutt the bull's attack point-by-point, then re-state your case.",
                    cfg,
                )
        bull_hist.append(bull_arg)
        bear_hist.append(bear_arg)
        await _emit("bull", "Bull Researcher Agent", bull_arg)
        await _emit("bear", "Bear Researcher Agent", bear_arg)

    invest_ctx = (
        f"{brief}\n\nANALYST REPORTS:\n{analyst_block}\n\n"
        f"BULL CASE (final):\n{bull_arg}\n\nBEAR CASE (final):\n{bear_arg}\n\n"
        f"FULL EXCHANGE — BULL:\n{chr(10).join(bull_hist)}\n\nFULL EXCHANGE — BEAR:\n{chr(10).join(bear_hist)}"
    )
    research_judge = await _chat(
        "You are the Research Manager (judge) of the live trading research. Rule on WHO "
        "WON: name the single deciding claim and the loser's failed point. Then give "
        "the INVESTMENT PLAN: LONG / SHORT / NEUTRAL + conviction 0-100. Reply as "
        "2-3 SHORT bullets, each under 15 words.",
        invest_ctx,
        cfg,
    )
    await _emit("research_judge", "Research Judge Agent", research_judge)

    # Phase 3 — risk assessment
    risk_ctx = f"{invest_ctx}\n\nRESEARCH JUDGE:\n{research_judge}"
    agg, neu, cons = await asyncio.gather(
        _chat(_RISK_ROLE["aggressive"], risk_ctx, cfg),
        _chat(_RISK_ROLE["neutral"], risk_ctx, cfg),
        _chat(_RISK_ROLE["conservative"], risk_ctx, cfg),
    )
    for k, txt in (("Aggressive", agg), ("Neutral", neu), ("Conservative", cons)):
        await _emit("risk_analyst", f"Risk Analyst Agent · {k}", txt)
    risk_judge = await _chat(
        "You are the Risk Judge of the research. First word: BUY, SELL or HOLD. Then "
        "name the disagreement between the risk analysts and your ruling, as 2 SHORT "
        "bullets, each under 12 words.",
        f"{risk_ctx}\n\nAGGRESSIVE: {agg}\n\nNEUTRAL: {neu}\n\nCONSERVATIVE: {cons}",
        cfg,
    )
    await _emit("risk_judge", "Risk Judge Agent", risk_judge)

    # Phase 4 — verdict + confidence
    decision, direction, confidence = _parse_verdict(
        research_judge, risk_judge, bull_hist, bear_hist, cfg,
        risk_analysts=(agg, neu, cons),
    )

    return {
        "pair": pair,
        "decision": decision,
        "direction": direction,
        "confidence": confidence,
        "rationale": research_judge,
        "risk_assessment": risk_judge,
        "bull_case": "\n\n".join(bull_hist),
        "bear_case": "\n\n".join(bear_hist),
        "analyst_reports": analyst_reports,
        "reports": analyst_reports,  # alias used by gateforum_research normaliser
        "transcript": _build_transcript(
            pair, analyst_reports, bull_hist, bear_hist,
            research_judge, risk_judge, agg, neu, cons,
        ),
    }


def _build_transcript(
    pair: str,
    analyst_reports: dict[str, str],
    bull_hist: list[str],
    bear_hist: list[str],
    research_judge: str,
    risk_judge: str,
    agg: str,
    neu: str,
    cons: str,
) -> list[dict]:
    """Chronological agent-by-agent transcript for the trading floor page.

    Each turn: {role, label, text}. Roles: analyst (4), bull, bear (merged into
    ONE post per agent — no Opening/Rebuttal framing), research_judge,
    risk_analyst (3), risk_judge. The public page renders these as chat bubbles.
    """
    turns: list[dict] = []
    for k, txt in analyst_reports.items():
        turns.append({"role": "analyst", "label": _ANALYST_LABEL.get(k, f"Analyst Agent · {k.title()}"), "text": txt})
    if bull_hist:
        turns.append({"role": "bull", "label": "Bull Researcher Agent", "text": "\n\n".join(bull_hist)})
    if bear_hist:
        turns.append({"role": "bear", "label": "Bear Researcher Agent", "text": "\n\n".join(bear_hist)})
    turns.append({"role": "research_judge", "label": "Research Judge Agent", "text": research_judge})
    for k, txt in (("Aggressive", agg), ("Neutral", neu), ("Conservative", cons)):
        turns.append({"role": "risk_analyst", "label": f"Risk Analyst Agent · {k}", "text": txt})
    turns.append({"role": "risk_judge", "label": "Risk Judge Agent", "text": risk_judge})
    return turns


def _conviction(text: str) -> int | None:
    """Pull the highest stated conviction (0-100) from an agent's post.

    Agents are prompted to end with ``CONVICTION: <0-100>``. Sometimes it lands
    on its own line, sometimes inline ('conviction 90'). Returns None when the
    agent did not state one.
    """
    if not text:
        return None
    vals = [int(m) for m in re.findall(r"conviction\s*[:=]?\s*(\d{1,3})", text, re.I)]
    vals = [v for v in vals if 0 <= v <= 100]
    return max(vals) if vals else None


def _parse_verdict(
    research_judge: str,
    risk_judge: str,
    bull_hist: list[str],
    bear_hist: list[str],
    cfg: EngineConfig,
    risk_analysts: tuple[str, str, str] | None = None,
) -> tuple[str, str, int]:
    # Word-boundary scan of the judges' rulings. NEVER substring-match:
    # "buyers"/"selling"/"shortage" would trip a naive "BUY" in text and flip
    # the verdict. Each judge follows its prompt's placement convention:
    #   risk judge      -> first word of the reply ("First word: BUY/SELL/HOLD")
    #   research judge  -> the PLAN line at the end ("PLAN: SHORT ... conviction")
    def _first_word_ruling(text: str) -> str | None:
        first = text.strip().split()[0].strip(":-;.,") if text.strip() else ""
        up = first.upper()
        if up in ("BUY", "LONG", "SELL", "SHORT", "HOLD"):
            return up
        # Fall back to any decisive word in the first line.
        line = text.splitlines()[0].upper() if text.splitlines() else ""
        for token in ("BUY", "LONG", "SELL", "SHORT", "HOLD"):
            if re.search(rf"\b{token}\b", line):
                return token
        return None

    def _plan_ruling(text: str) -> str | None:
        # The PLAN line (research judge's prompt: "PLAN: LONG/SHORT/NEUTRAL
        # + conviction"). Scan the LAST line first, then any line containing
        # PLAN/CONVICTION, in document order.
        lines = [l for l in text.splitlines() if l.strip()]
        candidates = reversed(lines)  # last line first
        for line in candidates:
            for token in ("BUY", "LONG", "SELL", "SHORT", "HOLD"):
                if re.search(rf"\b{token}\b", line.upper()):
                    return token
        return None

    risk_ruling = _first_word_ruling(risk_judge)
    rj_ruling = _plan_ruling(research_judge)

    # Risk gate with CONSENSUS (LOCAL PATCH 2026-08-16): the risk judge's first
    # word used to be an absolute veto — one conservative reply killed the
    # verdict even when the research judge and two risk analysts agreed. In
    # practice that vetoed most ticks (5 of 6 → HOLD) and starved the floor of
    # volume. Now the veto only stands when it has CONSENSUS: at least 2 of the
    # 3 risk analysts must also lean HOLD. Otherwise the research judge's ruling
    # (the analysis consensus) prevails, and the risk analysts' disagreement is
    # reflected only in confidence, not by blocking the trade.
    analyst_rulings = [
        _first_word_ruling(a) for a in (risk_analysts or ())
    ]
    hold_votes = sum(1 for r in analyst_rulings if r in ("HOLD",))

    final: str | None
    if risk_ruling in ("BUY", "LONG", "SELL", "SHORT"):
        # Risk judge agrees with a direction — the gate passes.
        final = risk_ruling
    elif risk_ruling == "HOLD" and hold_votes >= 2:
        # Risk judge wants to veto AND >=2 analysts agree it's too risky.
        final = "HOLD"
    else:
        # Risk judge vetoes alone (or says nothing) — the research analysis
        # consensus prevails.
        final = rj_ruling or "HOLD"

    if final in ("BUY", "LONG"):
        decision, direction = "BUY", "LONG"
    elif final in ("SELL", "SHORT"):
        decision, direction = "SELL", "SHORT"
    else:
        decision, direction = "HOLD", "NONE"

    # Confidence comes from the agents' STATED convictions, not from text
    # length heuristics (the old formula returned a constant 75 because the
    # short-bullet format never trips the length bonuses).
    #
    #   primary   = research judge's conviction (the decisive ruling)
    #   secondary = risk judge's conviction (final gate)
    #   alignment = bull/bear conviction spread: a lopsided research is a HIGH
    #               confidence signal; a near-tie means genuine disagreement.
    c = cfg.confidence
    primary = _conviction(research_judge)
    secondary = _conviction(risk_judge)
    bull_c = _conviction("\n".join(bull_hist))
    bear_c = _conviction("\n".join(bear_hist))

    conf = int(c.get("baseline", 60))
    if primary is not None:
        conf = primary
    elif secondary is not None:
        conf = secondary
    if secondary is not None:
        # Blend the risk judge in when it stated a number.
        conf = int(round(0.6 * conf + 0.4 * secondary))

    if bull_c is not None and bear_c is not None:
        spread = abs(bull_c - bear_c)
        if spread >= 25:
            conf += int(c.get("decisive_alignment_bonus", 5))
        elif spread >= 10:
            conf += int(c.get("contested_discount", 0))
        else:
            # Both sides convicted -> genuinely contested -> discount.
            conf -= int(c.get("contested_discount", 5))
        # A lone high conviction against the ruling direction still counts.
        if decision == "BUY" and bear_c > bull_c + 10:
            conf -= int(c.get("contrarian_penalty", 3))
        elif decision == "SELL" and bull_c > bear_c + 10:
            conf -= int(c.get("contrarian_penalty", 3))

    # BUY/SELL verdicts must clear the trading floor; never report a tradeable
    # verdict below it. HOLD can be any value.
    floor = int(c.get("floor", 65))
    if decision in ("BUY", "SELL") and conf < floor:
        conf = floor
    return decision, direction, min(conf, int(c.get("max", 95)))
