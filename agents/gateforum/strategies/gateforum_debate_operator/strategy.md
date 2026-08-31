---
name: GateForum Debate Operator
description: >-
  Runs the research & decision loop - refresh data, convene the analysis council,
  then open and manage directional perp positions from verdicts that clear the
  confidence floor. Competition-tuned: 2x leverage, modest sizing, drawdown-scaling
  instead of a hard stop, and a 30-min loser-close cadence for volume.
agent_key: null
skills: []
default_config:
  frequency_sec: 900
  execution_mode: loop
  total_amount_quote: 800
  risk_limits:
    max_position_size_quote: 96
    max_open_executors: 3
    max_drawdown_pct: 8
    max_leverage: 2
    require_triple_barrier: true
    require_trailing_stop: true
default_trading_context: 'Trade BTC-USDT, XAU-USDT and CL-USDT on gate_io_perpetual'
created_by: 0
created_at: '2026-08-15T00:00:00+00:00'
---

# GateForum Debate Operator

> **Identity, edge and risk philosophy live in the agent file (AGENT.md).** This
> playbook is the exact procedure for every tick — thresholds, sizing, call shapes,
> exits. Follow it precisely: the risk gate enforces the limits, so your job is to
> read the verdicts and size honestly within them.

Each tick: refresh the data, convene the research & analysis council, act only on
verdicts that earned it.
Goal in the 48h race: **protect the $800 while producing real volume** — the judges
score Volume + P&L, and the capital left at the end is what the participant keeps.

Read `connector_name` from `[CURRENT CONFIG]` or `trading_context`. Default is
`gate_io_perpetual`. Never hardcode a venue in a routine.

## Tick sequence

**1 — Server.** `manage_routines(action="run", routine="gateforum_init")`.
If it reports the server is not healthy, **stop the tick**. Journal the reason and do
not trade. Never trade on stale verdicts.

**2 — Data.** `manage_routines(action="run", routine="gateforum_data")`.
Fetches candles and fundamentals for all three pairs.

**3 — Analysis.** `manage_routines(action="run", routine="gateforum_research")`.
Runs the 12-role pipeline (research → adversarial analysis → risk assessment) and
returns a verdict table: pair, verdict, direction, confidence, actionable.

**WAIT for the verdict — do not poll-and-give-up.** The routine budget is 300s and
the debate takes ~3-4 min, so the blocking `action="run"` returns the fresh verdict
in one call. If it reports `Status=stale` or times out anyway, hold (no trade on
stale data) and journal it — but do not switch to `run_async` + `get_instance`
polling; the blocking run is the intended path now.

**Risk gate = CONSENSUS, not a lone veto.** The risk judge's HOLD only stands when
**at least 2 of the 3 risk analysts also lean HOLD** (a genuine risk consensus).
A lone risk-judge veto is overruled: the research judge's ruling prevails and the
disagreement only lowers confidence. Rationale: an absolute first-word veto let
one conservative reply kill the verdict even when the research judge and two risk
analysts agreed — it vetoed most ticks and starved the floor of volume. The
floor's job is to trade ≥65% verdicts; the risk layer exists to *size* them, and
only a real analyst consensus may block one.

**4 — Portfolio.** `get_portfolio_overview(connector=<connector_name>)` for balance and
open positions. Compute current **drawdown % = (peak_balance − balance) / peak_balance**.

## Drawdown response (NOT a hard stop)

The engine's `max_drawdown_pct` risk gate blocks NEW entries when drawdown crosses the
threshold. We do not want a dead bot that just sits there — that reads as a stop, not
a strategy. Instead, treat **8% drawdown as the "reduce" trigger**:

| Drawdown | Position size | Time limit | Behavior |
|---|---|---|---|
| **0–4%** | normal (12% of balance) | 1h barrier | trade verdicts at full size |
| **4–8%** | half size (6% of balance) | 1h barrier | trade verdicts at half size |
| **>8%** | quarter size (3% of balance) | **10–15 min** quick round-trip | keep trading, tiny size, fast close |

At >8% drawdown: still open small positions on the highest-confidence verdict and let
the short time limit close them quickly. The point is to stay active and recover volume
without risking meaningful capital. Never increase size while in drawdown. If the risk
gate refuses an entry, journal it and keep the tick alive — do not treat it as a stop.

**Enforcement note:** the risk gate (strategy `risk_limits`) enforces at the platform
level — max 3 executors, max $96 position, 8% drawdown pause, max 2x leverage, and
every position MUST carry a full triple barrier (stop_loss ≤10%, take_profit ≤50%,
time_limit 60s–48h, valid trailing stop). The LLM cannot open an unprotected or
over-leveraged position even if it tries; a blocked create is refused by the engine,
not by the playbook.

## Volume cadence (30-min: close losers, 15-min cooldown, re-evaluate)

Judges see Volume, and a single held position registers only one open + one close.
At 15-min ticks, use the **30-minute mark (every 2nd tick)** as the loser-close
cadence:

- **Close any open position that is in NEGATIVE unrealized P&L** at the 30-min mark.
  Realizing a small loss deliberately: (1) adds a close to the volume ledger,
  (2) stops a loser from running toward its 1h time limit, (3) frees the slot for a
  fresh verdict. This is active risk management — **cutting losers, running
  winners** — not churn.
- **Do NOT close winners at the 30-min mark** — their take-profit / trailing barrier
  does the work. Only losers get force-closed.
- **After a loser-close, the pair enters a 15-MINUTE COOLDOWN (1 cycle at the
  15-min cadence).** Do NOT re-enter that pair during the cooldown — even if a fresh
  verdict is actionable, even if it says the same direction. Closing a loser and
  instantly re-opening the same pair (same or opposite side) reads as churn/wash
  trading, not management. Journal the cooldown deadline (close time + 15 min) so
  you can check it on the next tick.
- **After the cooldown elapses, evaluate the pair again via the normal trade-session
  flow** — the MUST-OPEN rule applies to it like any other pair. Re-entry must be
  driven by a fresh verdict ≥65%, never by "we just closed, open again".
- **Cost check:** at ~$8 notional a close+reopen costs ~2 × 0.05% ≈ **$0.008** —
  negligible against the ~$16 of volume it registers. At race scale ($96 positions)
  ≈ $0.10 per round trip for ~$192 of volume — worth it. Never churn faster than
  every 30 minutes; the cost is only acceptable at this frequency.

If the analysis is ambiguous at the 30-min mark (no actionable verdict), the loser
close still happens (volume + risk) and the pair enters the 15-min cooldown — do
not force a re-entry against the analysis or during the cooldown.

## Position management

**5 — Manage what is open.** Positions carry their own triple barrier, so intervene only
on analysis-driven grounds:
- **Reversal** — the council flips direction on an open pair with confidence ≥75%:
  close it, then re-enter the other way next tick. Do not flip and re-open in one tick.
- **Conviction collapse** — verdict moves to HOLD and confidence drops below 50%: close.
- Otherwise leave the barrier to do its job. Do not micromanage.

**6 — Open new positions.** Only where `Actionable = YES` (BUY/SELL, confidence ≥65%).
Rank by confidence, respect **max 3 concurrent positions**, skip pairs already open.

**"Pairs already open" = exchange-level truth, NOT just your own executor list.**
Use `get_portfolio_overview`'s perp positions (it reports the account's REAL open
perpetual positions across ALL sessions/controllers, including positions a
previous session left running — e.g. after a bot restart, the old session's BTC
SHORT is still on the exchange). If a pair has ANY open perp position, do not
open a second one on it — manage the existing position instead. Your own
executor list (filtered by controller_id) is a subset; the exchange view is the
complete picture.

**DUST / OFFSET IGNORE RULE:** judge each pair by its **NET position**, not its
raw legs. `get_portfolio_overview` reports every open leg separately, and prior
test runs left OFFSETTING legs (LONG + SHORT on the same pair) that net to ~$0.
For each pair, sum the signed amounts (`SHORT` negative, `LONG` positive) ×
entry price → the pair's net notional. A pair is "open" only if |net notional|
≥ **$1.00**. Pairs whose legs cancel out (|net| < $1) are dust: they do NOT
count as open for the skip-pair check, the 3-position cap, or drawdown math.
(Rationale: BTC/CL/XAU carried offsetting remnant legs that blocked every new
entry even though they net to ~$0 — noise, not risk.)

**MUST-OPEN RULE (non-negotiable):** open a position on **EVERY** pair that is
`Actionable = YES` (confidence ≥65%), up to **max 3 concurrent positions**, ranked
by confidence. Do not defer or skip a ≥65% verdict because a higher-confidence
pair exists — every actionable pair gets a slot (highest confidence first, then
the next, until 3 slots are used or no actionable pairs remain). Standing aside
when an actionable verdict exists is a failure — the confidence floor exists
precisely so that ≥65% means "trade". Only skip if the risk gate refuses the
create (journal it and try the next actionable pair), the pair is in cooldown, or
the server is unhealthy. Do not wait "for confirmation" — the analysis IS the
confirmation.

Sizing — conviction drives size, drawdown scales it down:

| Confidence | Leverage | Notional (normal mode) |
|---|---|---|
| 65–74% | 2× | 8% of balance |
| 75–84% | 2× | 12% of balance |
| ≥85% | 2× | 16% of balance |

Never exceed 20% of balance on one position or 40% as total margin. Apply the drawdown
multiplier from the table above (half size at 4–8%, quarter size above 8%).

Open with `manage_executors`. Fetch the schema first, then create — the risk gate
REQUIRES `total_amount_quote` (quote notional) on every position; a base-only
`amount` is refused because it cannot be risk-checked against the position cap:

```
manage_executors(
  executor_type="position_executor",
  connector_name=<connector_name>,
  trading_pair=<pair>,
  side=1 if LONG else 2,
  total_amount_quote=<notional_usd>,       # REQUIRED — quote notional ($)
  amount=<notional_usd / entry_price>,     # base currency, NOT quote
  leverage=2,
  triple_barrier_config={
    "stop_loss": 0.02,
    "take_profit": 0.04,
    "time_limit": 3600,
    "trailing_stop": {"activation_price": 0.01, "trailing_delta": 0.02},
    "open_order_type": 1
  }
)
```

For drawdown or duty trades, shrink `amount` per the tables and shorten `time_limit` to
600–900 seconds so the position round-trips in minutes.

`amount` is in **base currency** — divide the USD notional by the entry price. For
XAU-USDT and CL-USDT check the venue minimum contract size before submitting.

**7 — Journal.** Write one entry per tick with
`trading_agent_journal_write`: verdicts and confidence per pair, actions taken (or why
none), drawdown %, open positions, and a one-line summary of the decisive argument. The
journal is the audit trail judges read — keep it substantive.

**Cooldown tracker (mandatory field):** every journal entry MUST include a
`Cooldowns:` line listing each pair's cooldown state. This is how you remember the
loser-close cooldown across ticks — do not rely on memory:

```
Cooldowns: CL-USDT until 21:30 (closed 21:00 @ -$0.31) · BTC-USDT none · XAU-USDT none
```

- When you close a losing position at the 30-min mark, **record the deadline**
  (close time + 15 min) on that pair in the Cooldowns line.
- On the next tick, **check the Cooldowns line first**: any pair whose deadline has
  passed is re-evaluated normally (MUST-OPEN applies); any pair still in cooldown is
  skipped for new entries, whatever the verdict says.
- Remove the pair from the line once the cooldown has elapsed.
- If you had no cooldowns, still write `Cooldowns: none` so the audit trail shows
  you checked.

## Guardrails

- Confidence floor is **65%**. Below it, stand aside (except the 30-min loser-close cadence).
- Max **3** concurrent positions across all pairs.
- Max leverage **2×** — never 5×, the risk is not worth the volume.
- Drawdown >8% → quarter size + quick close, never a dead stop.
- Research server unreachable → no trading, full stop.

---

## Cheat sheet (every tick)

| # | Action | Key values |
|---|---|---|
| 1 | `gateforum_init` | server healthy, else **stop the tick** |
| 2 | `gateforum_data` | candles + fundamentals for BTC/XAU/CL |
| 3 | `gateforum_research` | verdicts: pair, direction, confidence, actionable |
| 4 | `get_portfolio_overview` | balance, open positions, drawdown % |
| 5 | Filter | Actionable = YES, confidence ≥65%, skip open pairs, max 3 concurrent — **open EVERY actionable pair** (ranked by confidence) |
| 6 | Size | 8/12/16% by confidence (65-74/75-84/≥85); ×1.0 (≤4% DD), ×0.5 (4-8%), ×0.25 (>8%) |
| 7 | Create | `position_executor`, 2× leverage, `controller_id` INSIDE executor_config, barrier: SL 2% / TP 4% / 1h / trail 1→2% |
| 8 | Manage | barrier closes; flip on reversal ≥75%; close on conviction collapse <50% |
| 9 | Volume cadence | every 2nd tick (30-min): close NEGATIVE positions → 15-min pair cooldown (1 cycle) → then re-evaluate via trade session — never churn faster than 30 min |
| 10 | Journal | verdicts, actions, drawdown %, decisive argument, **Cooldowns: line (mandatory)** |

If the analysis is ambiguous at the 30-min mark, the loser-close still registers
volume and the slot stays free — a HOLD verdict is valid; a dead bot is not. No
forced trades outside the playbook.

---

## Operations (for operators, not the tick)

This playbook is the *how to trade*. The *how to run* lives in AGENT.md
(**Operations — zero-touch launch and self-healing**): `gateforum_init` brings up
`:8500` + `:8600` on every tick; `gateforum-heal.timer` (15 min) restarts API /
Condor / server / floor / session if anything died. You do not start those by
hand. If the floor is down while the session is running, that is a healer bug.
