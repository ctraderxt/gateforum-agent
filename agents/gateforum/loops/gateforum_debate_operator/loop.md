---
name: GateForum Debate Operator
description: >-
  Runs the research & decision loop - refresh data, convene the analysis council,
  then open and manage directional perp positions from verdicts that clear the
  confidence floor. Competition-tuned: 2x leverage, modest sizing, drawdown-scaling
  instead of a hard stop. Volume is handled by a separate dedicated desk, so this
  loop no longer force-closes positions on a timer — only stop_loss/trailing_stop
  (or a genuine analysis reversal) close a position. Patient P&L sleeve on $280 of the $800 split-book (Binance volume sleeve holds $520).
agent_key: null
skills: []
default_config:
  frequency_sec: 900
  execution_mode: loop
  tick_timeout_sec: 1500
  total_amount_quote: 280
  risk_limits:
    max_position_size_quote: 120
    max_open_executors: 3
    max_drawdown_pct: 36
    # Absolute P&L-sleeve stop = $100 USDT (~36% of $280). Hard flatten, split-book sleeve stop.
    pnl_stop_loss_usd: 100
    max_leverage: 2
    require_triple_barrier: true
    require_trailing_stop: true
  volume_arm_usd: 520
  pnl_arm_usd: 280
  race_envelope_usd: 800
  volume_controller: gf_peg_maker
  volume_pair: USD1-USDC
  volume_pair_fallback: USD1-USDT
default_trading_context: 'Trade XRP-USDT, XAG-USDT and CL-USDT on gate_io_perpetual. P&L sleeve $280 USDT (stop $100). Volume sleeve $520 is gf_peg_maker on Binance USD1-USDC (fallback USD1-USDT) — not this loop. Size via _gateforum_alloc.'
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
Goal in the 48h race: **protect P&L on the ~$280 directional sleeve** — volume is
produced by the separate stable desk, not by timer-closing losers here.

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

## Drawdown response + hard sleeve stop

**Hard stop:** if P&L-sleeve NAV is down **$100 USDT** from session entry NAV, flatten
all Gate.io positions this tick (`KILL_PORTFOLIO` / stop executors) and do not re-arm
without an operator. Basis is the **$280 P&L sleeve**, not the $800 tape budget.
Code: `routines/_gateforum_alloc.portfolio_stop_usd` · config `pnl_stop_loss_usd: 100`.

**Before that ceiling**, drawdown still **scales size down** so the bot is not dead:

| Drawdown | Position size | Time limit | Behavior |
|---|---|---|---|
| **0–4%** | normal (12% of P&L-arm balance) | 48h ceiling | trade verdicts at full size |
| **4–8%** | half size (6% of balance) | 48h ceiling | trade verdicts at half size |
| **>10%** (and loss still **<$100**) | quarter size | 48h ceiling | keep trading tiny; hard stop only at **−$100 USDT** |

At >10% drawdown: still open small positions on the highest-confidence verdict, sized
down, and let stop_loss / trailing_stop close them naturally — do not force an early
time-based exit. The point is to stay capital-protective, not to manufacture activity.
Never increase size while in drawdown. If the risk gate refuses an entry, journal it
and keep the tick alive — do not treat it as a stop.

**Enforcement note:** the risk gate (strategy `risk_limits`) enforces at the platform
level — max 3 executors, max $120 TOTAL open exposure across all positions (not per-position — size within that against the 8/12/16% tiers on the ~$280 P&L sleeve), 10% drawdown pause, max 2x leverage, and
every position MUST carry a full triple barrier (stop_loss ≤10%, take_profit ≤50%,
time_limit 60s–48h, valid trailing stop). The LLM cannot open an unprotected or
over-leveraged position even if it tries; a blocked create is refused by the engine,
not by the playbook.

## Volume sleeve (handled elsewhere)

## Split-book capital ($800)

| Sleeve | USD | Venue | Component |
|---|---|---|---|
| **Volume** | **$520 (65%)** | Binance USD1-USDC → USD1-USDT | `gf_peg_maker` |
| **P&L** | **$280 (35%)** | Gate.io perps XRP/XAG/CL | this loop |
| **P&L stop** | **$100 USDT** | sleeve NAV | `pnl_stop_loss_usd` |
| **Total** | **$800** | two venues | split-book |

Volume is generated by the Binance stable desk, not by this strategy. **This loop does not force-close positions for volume anymore.** A
position stays open until its own barrier closes it (stop_loss, take_profit, or
trailing_stop) or a genuine analysis-driven reason fires (reversal, conviction
collapse — see Position management below). Do not re-introduce a timer-based close;
if a position is sitting flat or mildly negative, that is normal — let the barrier
do its job.

## Position management

**5 — Manage what is open.** Positions carry their own triple barrier, so intervene only
on analysis-driven grounds (these are allowed exits alongside SL/TP/trailing):
- **Council decision changed / Reversal** — the council flips direction on an open pair
  with confidence ≥75%: close it, then re-enter the other way next tick. Do not flip and
  re-open in one tick.
- **Conviction collapse** — verdict moves to HOLD and confidence drops below 50%: close.
- Otherwise leave the barrier to do its job. Do not micromanage. No timer-based closes.

**6 — Open new positions.** Only where `Actionable = YES` (BUY/SELL, confidence ≥65%).
Rank by confidence, respect **max 3 concurrent positions**, skip pairs already open.

**"Pairs already open" = exchange-level truth, NOT just your own executor list.**
Use `get_portfolio_overview`'s perp positions (it reports the account's REAL open
perpetual positions across ALL sessions/controllers, including positions a
previous session left running — e.g. after a bot restart, the old session's XRP
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
(Rationale: XRP/XAG/CL carried offsetting remnant legs that blocked every new
entry even though they net to ~$0 — noise, not risk.)

**MUST-OPEN RULE (non-negotiable):** open a position on **EVERY** pair that is
`Actionable = YES` (confidence ≥65%), up to **max 3 concurrent positions**, ranked
by confidence. Do not defer or skip a ≥65% verdict because a higher-confidence
pair exists — every actionable pair gets a slot (highest confidence first, then
the next, until 3 slots are used or no actionable pairs remain). Standing aside
when an actionable verdict exists is a failure — the confidence floor exists
precisely so that ≥65% means "trade". Only skip if the risk gate refuses the
create (journal it and try the next actionable pair), the pair already has an
open position (see the exchange-level check above), or the server is unhealthy.
Do not wait "for confirmation" — the analysis IS the confirmation.

Sizing — conviction drives size, drawdown scales it down:

| Confidence | Leverage | Notional (normal mode) |
|---|---|---|
| 65–74% | 2× | 8% of balance |
| 75–84% | 2× | 12% of balance |
| ≥85% | 2× | 16% of balance |

Never exceed 20% of balance on one position or 40% as total margin. Apply the drawdown
multiplier from the table above (half size at 4–8%, quarter size above 10%).

Floor the size to the venue's contract grid before you open. Perps trade in whole
contracts, so `amount` must be a whole multiple of the pair's `quanto_multiplier` and the
resulting notional must clear the venue's `min_notional_size` -- read the trading rules for the
pair once, then round DOWN. A size the exchange cannot represent is silently clipped, which
leaves the executor's `amount` and the real position disagreeing.

Open with `create_position_executor`. Size from the live balance, clear the venue
minimum, and stay under `max_position_size_quote` — the risk gate refuses anything
under- or over-sized:

```
create_position_executor(
  executor_type="position_executor",
  connector_name=<connector_name>,
  trading_pair=<pair>,
  side=1 if LONG else 2,
  amount=<notional_usd / entry_price>,   # REQUIRED - BASE currency, NOT quote
  leverage=2,
  entry_price=<limit price>,             # optional
  stop_loss=0.02,
  take_profit=0.04,
  time_limit=172800,
  trailing_stop_activation_price=0.012,
  trailing_stop_trailing_delta=0.008,
  open_order_type=1
)
```

Call shape notes: the barrier fields are **flat parameters** — the engine rebuilds the
nested `triple_barrier_config` itself, so never send it as an object. `amount` is in
**BASE currency** (quote notional ÷ entry price). `total_amount_quote` is **not** a
position-executor field.

`time_limit=172800` (48h, the platform ceiling) is a backstop only — it should never
be the thing that actually closes a position. Exits are stop_loss, take_profit, or
trailing_stop. The trailing stop activates once a position is up 1.2% and then trails
only 0.8% behind the peak — tighter than the activation distance, so once it engages
it locks in most of the move instead of giving back more than it took to arm.

For drawdown trades, shrink `amount` per the table above — do not shorten `time_limit`.
A smaller position with the same barrier logic is the right response to drawdown, not
a faster forced exit.

`amount` is in **base currency** — divide the USD notional by the entry price. For
XRP-USDT, XAG-USDT and CL-USDT check the venue minimum contract size before submitting.

**7 — Journal.** Write one entry per tick with
`trading_agent_journal_write`: verdicts and confidence per pair, actions taken (or why
none), drawdown %, open positions, and a one-line summary of the decisive argument. The
journal is the audit trail judges read — keep it substantive.

There is no cooldown tracker anymore — it existed only to support the removed
loser-close cadence. A pair stays open or closed purely on its own barrier and
the analysis-driven rules above; there is nothing else to track between ticks.

## Guardrails

- Confidence floor is **65%**. Below it, stand aside.
- Max **3** concurrent positions across all pairs.
- Max leverage **2×** — never 5×, the risk is not worth it.
- Drawdown >10% → quarter size, same barrier logic, never a dead stop.
- Research server unreachable → no trading, full stop.

---

## Cheat sheet (every tick)

| # | Action | Key values |
|---|---|---|
| 1 | `gateforum_init` | server healthy, else **stop the tick** |
| 2 | `gateforum_data` | candles + fundamentals for XRP/XAG/CL |
| 3 | `gateforum_research` | verdicts: pair, direction, confidence, actionable |
| 4 | `get_portfolio_overview` | balance, open positions, drawdown % |
| 5 | Filter | Actionable = YES, confidence ≥65%, skip open pairs, max 3 concurrent — **open EVERY actionable pair** (ranked by confidence) |
| 6 | Size | `_gateforum_alloc.size_order` — 8/12/16% × DD scale on $280 sleeve; lift to venue min if needed; hard stop −$100 USDT |
| 7 | Create | `position_executor`, 2× leverage, `controller_id` INSIDE executor_config, barrier: SL 2% / TP 4% / 48h ceiling / trail activate 1.2%→trail 0.8% |
| 8 | Manage | barrier closes (SL/TP/trailing); flip on reversal ≥75%; close on conviction collapse <50%; no timer-based closes |
| 9 | Journal | verdicts, actions, drawdown %, decisive argument |

A HOLD verdict is valid; a dead bot is not — but standing aside on a pair with no
actionable verdict is not a failure either. No forced trades outside the playbook.

---

## Operations (for operators, not the tick)

This playbook is the *how to trade*. The *how to run* lives in AGENT.md
(**Operations — zero-touch launch and self-healing**): `gateforum_init` brings up
`:8500` + `:8600` on every tick; `gateforum-heal.timer` (15 min) restarts API /
Condor / server / floor / session if anything died. You do not start those by
hand. If the floor is down while the session is running, that is a healer bug.
