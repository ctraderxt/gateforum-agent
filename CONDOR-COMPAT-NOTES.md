# Condor compatibility notes

Fixes applied to make this entry run on a **current Condor + Hummingbot API** install, with the
findings from a live run on a small Gate.io USDT-perp account (two `position_executor` legs opened,
managed by the triple barrier, then stopped and flattened cleanly). Every change below was verified
against the installed platform rather than a docstring.

Nothing here changes the entry's design: the venue stays `gate_io_perpetual`, the council keeps its
12 roles and the 65% floor, and the triple barrier is untouched.

## Fixed

**Could not trade at all**

1. `routines/gateforum_research.py` — **the three pair requests are now serialised.** The research
   server rate-limits on a single global timestamp, so the previous `asyncio.gather` fan-out had its
   siblings rejected with `429`; the routine then set `last_error = "rate_limited"` and broke, so two
   of three verdicts were discarded *even when the server already held good ones*. A module-level
   `asyncio.Lock()` now serialises the POSTs.
2. `routines/gateforum_research.py` — **verdicts the server already produced are now used.** The
   routine previously demanded a *fresh blocking* council on every tick, so anything already stored
   was invisible (`report_id=null`) and the tick held forever. It now consults `GET /sessions` first
   and returns the newest verdict **per pair** (backwards scan — the server opens a new session per
   pair, so three pairs are usually three sessions) inside a 1500 s freshness window, falling back to
   a blocking council only when nothing is fresh.
3. `AGENT.md` — `manage_executors` no longer exists on current Condor (it was called 12×). Replaced
   with `create_position_executor` / `list_executors` / `stop_executor` / `get_executor`.
4. `strategies/gateforum_debate_operator/strategy.md` — **the create call shape was wrong.** The
   playbook claimed `create_position_executor` *requires* `total_amount_quote`; the executor schema
   has no such field. The real shape is `trading_pair* · connector_name* · side* · amount*` (BASE
   currency, required) plus **flat** barrier parameters — the engine rebuilds the nested
   `triple_barrier_config` itself, so sending it as an object is wrong.
5. `server/gateforum_config.yaml` — the LLM transport defaulted to the `claude-cli` provider, which
   shells out to a `claude` binary that is not present on a stock Condor, so `POST /research`
   answered 500. Default is now the OpenAI-compatible path (OpenRouter) and any compatible endpoint
   works: set `llm.provider`, `llm.model`, `llm.backend_url` and `llm.api_key_env`.
6. `server/gateforum_public.py` — **the page's JWT subject was a name, not an id.** Condor decodes
   `int(payload["sub"])`, so `"sub": "gateforum-admin"` raised `ValueError` and *every* call the
   floor page made returned 500. The subject is now the numeric `ADMIN_USER_ID` (environment, falling
   back to the Condor repo `.env`).
7. `server/gateforum_heal.py` — same string subject (the healer would have reintroduced the 500s),
   now fixed the same way, plus `server_name` dropped and `tick_timeout_sec` aligned to the chain
   below.

**Correctness / money**

8. `strategies/gateforum_debate_operator/strategy.md` — the sizing section now says to **floor the
   computed size to the venue's contract grid**. Gate perps trade in whole contracts, and a size that
   is not a multiple of `quanto_multiplier` gets clipped by the exchange, leaving the executor's
   configured `amount` and the real position disagreeing (observed live: configured 0.00015 BTC
   against a 0.0001-quantum contract, filled as 1 contract).
9. `strategies/gateforum_debate_operator/strategy.md` — `tick_timeout_sec: 1500` added to
   `default_config`. The ordering that works is **routine timeout < host blocking budget < tick
   timeout**; without it, a host that hands a blocking routine off to the background leaves the tick
   with no verdict at all.
10. `routines/gateforum_research.py` — `research_timeout_seconds` 300 → 900, so the routine's own
    timeout fires *before* the host's hand-off and reports a clean timeout the agent can act on.
11. `server/gateforum_config.yaml` — `rate_limit_seconds` 180 → 30. One council cycle measures ~187 s,
    so a 180 s limiter adds a three-minute queue per pair and makes a three-pair cycle ≈ 10–12 min.
12. `tests/validate_agent.py` — crashed on current Condor with `ImportError: cannot import name
    '_slugify'`; modern Condor exports it as `slugify`. Now imports either. Prints ALL CHECKS PASSED.
13. `AGENT.md` — `agent_key` is no longer pinned to `claude-acp:sonnet` (that path needs an
    `claude-agent-acp` bridge that is not installed on a stock Condor; `strategy.md` already used
    `null`). It now inherits the host's default agent — set an explicit `provider:model` key here if
    you want to pin one. `server_name: GateForum-Agent` was also removed: with `server_required: true`
    a name that does not exist on the host pins the agent to a phantom server and breaks its trading
    tools.
14. `strategies/…/learnings.md` and `dry_runs/experiment_1.md` — these are historical records, so they
    were **not rewritten**; each has a one-line header noting that `manage_executors` no longer exists
    and naming the current tools.

## Still to do

1. **Make the rate limiter genuinely per-pair on the server.** The client-side lock fixes the symptom;
   the server's single `LAST_RESEARCH_AT` (with a config comment that claims "per pair") is the cause.
2. **The `max_position_size_quote` cap behaves as a total-book cap, not a per-ticket cap.** Observed
   live: with the first leg at roughly a tenth of the cap, a second and third leg were refused with
   `Would exceed position limit`. On a small balance this means a three-pair council can never express
   all three verdicts — decide whether that is intended and say so in the docs, or make the gate
   per-ticket.
3. **Fix the documented budget arithmetic.** "The debate takes ~3-4 min" is true *per pair*; with the
   queue the three-pair cycle is ~10–12 min (measured: one council ≈ 187 s). The README's claim that a
   300 s budget returns a fresh verdict in one call cannot hold, and the host-side contract (routine
   timeout < host blocking budget < tick timeout) should be stated explicitly.
4. **`gateforum_heal.py`'s `SESSION_CONFIG` risk values drift from `strategy.md`'s `default_config`**
   (it carries a much smaller envelope than the strategy declares). Pick one as canonical — as it
   stands, running the healer silently rewrites the session to different risk limits.
5. **`gateforum_heal.py` now needs `ADMIN_USER_ID`** (environment or the Condor repo `.env`) to mint a
   valid admin JWT; without it the subject falls back to `"0"`.
6. **Position mode: the README assumes hedge mode** (`reduce_only` closes), while the account used for
   testing is `ONEWAY`. A `reduce_only` close can be rejected in one-way mode, and the every-second-tick
   loser-close path is exactly such a close. Decide whether to require hedge mode or make the close path
   mode-aware, and document it. Gate only allows the switch while the account is flat.
7. **Sizing is LLM-driven prose.** Item 8 above is an instruction, not code; a small helper that floors
   to `quanto_multiplier` and checks `min_notional_size` against the venue's trading rules would be
   strictly better than asking the model to remember it.
