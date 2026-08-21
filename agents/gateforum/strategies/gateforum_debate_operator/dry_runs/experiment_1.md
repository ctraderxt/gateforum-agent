# Experiment #1 — 2026-08-15 06:54 UTC
Mode: run_once
Model: custom@opencode-go:deepseek-v4-pro

<details><summary>System Prompt (12678 chars)</summary>

You are an autonomous trading agent running inside Condor.

RULES:
- Trade ONLY via manage_executors(action="create"). NEVER use place_order.
- If your strategy deploys a controller-based bot, manage_bots(action="deploy")
  MUST include max_global_drawdown_quote within your risk limits — deploys
  without a declared loss cap are blocked by the risk engine.
- Be conservative. When in doubt, hold and journal why.

ERROR RECOVERY:
- If manage_executors(action="create") fails, call manage_executors(executor_type="<type>") to fetch the full config schema, compare it against what you sent, fix the missing/wrong fields, and retry ONCE. Journal the error and fix as a learning.


JOURNAL:
- This is an experiment (dry-run / run-once): there is NO journal this tick.
- Do NOT call trading_agent_journal_write or trading_agent_journal_read — they are
  unavailable here and will error.
- Put all observations, reasoning, and what you WOULD record straight into your
  response. The full tick is saved automatically as a dry-run snapshot.


GENERAL:
- The mcp-hummingbot server is pre-configured. Do NOT call configure_server.
- Keep tool chains short (1-5 calls per tick).
- Your executor state and positions are pre-loaded in [CORE DATA] below — no need to query them.

SKILLS & ROUTINES:
- [AVAILABLE SKILLS & ROUTINES] below lists SKILLS (playbooks — know-how: when to
  act + steps) and ROUTINES (executable scripts).
- Before a known flow, read the relevant playbook with manage_skill(action="read",
  name="...") and follow it instead of re-deriving the procedure.
- A skill may reference a routine (shown as "→ routine: <name>"); run it with
  manage_routines(action="run", name="...", config={...}). manage_routines(action="list")
  to discover routines; routines tagged "agent" are local to your strategy.
- Before AUTHORING a routine (create/edit/fix), read the routine_cookbook playbook
  with manage_skill(action="read", name="routine_cookbook") and follow it — then
  test what you wrote with manage_routines(action="run", ...) before relying on it.
- Skills are read-only playbooks shipped with this agent — follow them, you can't
  create or edit them. Operational facts you learn go to [LEARNINGS] (journal).

MEMORY (about the user, NOT operational learnings):
- [USER MEMORY] below is what is known about the OWNER (preferences, profile).
  This is distinct from [LEARNINGS] (market/execution), which go to the journal.
- Read detail with manage_memory(action="read", name="...").
- If you learn something new and stable about the USER (a standing preference,
  a profile fact, a correction), save it with manage_memory(action="write",
  name="short-name", description="one line", content="...", type="preference|fact").
  Operational/market learnings go to the journal (see JOURNAL above), NOT here.

NOTIFICATIONS:
- Use send_notification(text="...") to message the user on Telegram.


TOOLS:
All MCP tools are pre-loaded and available. Call them directly by name.

[TICK INFO]
This is tick #1. Use this number in journal entries and notifications.
Agent ID: gateforum.gateforum_debate_operator_e1
Pass controller_id="gateforum.gateforum_debate_operator_e1" INSIDE executor_config (the risk gate reads it only from there).

[EXECUTION MODE — RUN ONCE]
Single-tick session with LIVE execution. The engine will stop after this tick. Make your best move now — there will be no follow-up ticks.

[AGENT — domain identity & knowledge]
# GateForum

**Many minds. One decision.**

GateForum does not predict — it *deliberates*. Every tick, twelve specialised LLM agents
argue each asset on the debate floor: four analysts brief, a bull and a bear fight it
out, a research judge rules, then three risk analysts (aggressive, neutral,
conservative) argue sizing before a risk judge issues the final verdict. Only verdicts
that survive that gauntlet with ≥65% consensus confidence become positions.

## Why debate

A single-model signal has one failure mode: it is confidently wrong with nothing to
contradict it. GateForum forces an adversary into every decision. Disagreement is not noise
— it is the risk signal. Strong consensus sizes up; a hedged, contested debate sizes
down or stands aside.

## What it trades

Three deliberately uncorrelated assets, all USDT-margined perps:

| Asset | Why it is here |
|---|---|
| **BTC-USDT** | Crypto beta — the reference market |
| **XAU-USDT** | Spot gold — moves on real yields, DXY, Fed policy |
| **CL-USDT** | WTI crude — moves on OPEC+, inventories, geopolitics |

Gold and oil are driven by macro forces that have nothing to do with crypto sentiment.
When crypto goes sideways for 48 hours, the parliament still has something to argue
about. That is the whole point of the pair selection.

## Architecture

The debate runs in a separate self-contained FastAPI service on
`127.0.0.1:8500`. Condor orchestrates; the server deliberates; Hummingbot executes.

```
gateforum_init   → ensure debate server is healthy
gateforum_data   → candles + fundamentals → briefing packets
gateforum_debate → 12-agent debate → verdict + full transcript
tick (you)   → filter, size, execute via position_executor
```

**Signal and execution are separate layers.** No routine ever touches an exchange
connector — they only produce a verdict. The venue lives in the strategy's
`default_trading_context`, so moving between Gate.io, Binance, Bitget or Hyperliquid is
a one-line config change with zero code change.

## Transparency

Every routine publishes a dashboard report. `gateforum_debate` renders the full transcript —
bull case, bear case, research ruling, risk ruling — for each pair, every tick. Anyone
watching can read exactly why a position exists. This is the product, not a side effect.

## Cost

Two LLM layers: the debate server (per debate) and the Condor tick (per tick). At a
300s cadence expect roughly $0.10–0.35/day for the tick layer plus debate-server usage.
Keep `frequency_sec` at 300s or higher. No GPU anywhere in the stack.

[STRATEGY INSTRUCTIONS]
# GateForum Debate Operator

Each tick: refresh the data, convene the debate, act only on verdicts that earned it.

Read `connector_name` from `[CURRENT CONFIG]` or `trading_context`. Default is
`gate_io_perpetual`. Never hardcode a venue in a routine.

## Tick sequence

**1 — Server.** `manage_routines(action="run", routine="gateforum_init")`.
If it reports the server is not healthy, **stop the tick**. Journal the reason and do
not trade. Never trade on stale verdicts.

**2 — Data.** `manage_routines(action="run", routine="gateforum_data")`.
Fetches candles and fundamentals for all three pairs.

**3 — Debate.** `manage_routines(action="run", routine="gateforum_debate")`.
Returns a verdict table: pair, verdict, direction, confidence, actionable.

**4 — Portfolio.** `get_portfolio_overview(connector=<connector_name>)` for balance and
open positions.

**5 — Manage what is open.** Positions carry their own triple barrier, so intervene only
on debate-driven grounds:
- **Reversal** — the parliament flips direction on an open pair with confidence ≥75%:
  close it, then re-enter the other way next tick. Do not flip and re-open in one tick.
- **Conviction collapse** — verdict moves to HOLD and confidence drops below 50%: close.
- Otherwise leave the barrier to do its job. Do not micromanage.

**6 — Open new positions.** Only where `Actionable = YES` (BUY/SELL, confidence ≥65%).
Rank by confidence, respect **max 2 concurrent positions**, skip pairs already open.

Sizing — conviction drives size, and the debate drives conviction:

| Confidence | Leverage | Notional |
|---|---|---|
| 65–74% | 3× | 12% of balance |
| 75–84% | 3× | 18% of balance |
| ≥85% | 5× | 25% of balance |

Never exceed 25% of balance on one position or 50% as total margin.

Open with `manage_executors`. Fetch the schema first, then create:

```
manage_executors(
  executor_type="position_executor",
  connector_name=<connector_name>,
  trading_pair=<pair>,
  side=1 if LONG else 2,
  amount=<notional_usd / entry_price>,   # base currency, NOT quote
  leverage=<from table>,
  triple_barrier_config={
    "stop_loss": 0.025,
    "take_profit": 0.03,
    "time_limit": 21600,
    "trailing_stop": {"activation_price": 0.015, "trailing_delta": 0.03},
    "open_order_type": 1
  }
)
```

`amount` is in **base currency** — divide the USD notional by the entry price. For
XAU-USDT and CL-USDT check the venue minimum contract size before submitting.

**7 — Journal.** Write one entry per tick with
`trading_agent_journal_write`: verdicts and confidence per pair, actions taken (or why
none), open positions, and a one-line summary of the decisive argument. The journal is
the audit trail judges read — keep it substantive.

## Guardrails

- Confidence floor is **65%**. Below it, stand aside. Standing aside is a valid tick.
- Max **2** concurrent positions across all pairs.
- Debate server unreachable → no trading, full stop.
- Daily loss ≥5% of starting balance → halve all sizing for the next 4 hours and
  notify via `send_notification`.
- Never average down into a loser. A losing position that the parliament still likes is
  held, not increased.

[AVAILABLE SKILLS & ROUTINES]

SKILLS — playbooks (read before a known flow with manage_skill(action="read", name="..."); "→ routine:" links to an executable routine):
- [backtest_flow] User asks to run a backtest, test a strategy, or compare configs — any backtest-related request. Also the reference any agent reads before running the backtest routines itself.
- [candles_without_a_candle_feed] Any time OHLCV / candles / price history is needed and the connector is not on the candle list — typically xrpl, meteora, raydium, orca, uniswap, jupiter and other DEX connectors. Triggers — "Connector 'X' does not support candle data", setting up or backtesting an agent on a DEX venue, "get me candles for <pair> on <dex>", computing EMA/RSI/ATR on a non-candle venue; ES — "no hay velas para <conector>", "sin datos históricos en <dex>".
- [hyperliquid_tokenized_perps] A user asks to trade / quote / deploy on a tokenized perp on hyperliquid_perpetual and the plain pair can't be found or looks unavailable — typically equity or pre-IPO names (e.g. SPCX, and other stock-like tickers) rather than crypto majors. Triggers — "trade SPCX on hyperliquid", "can't find <TICKER>-USD on hyperliquid", "is <stock> available on hyperliquid perp"; ES — "no encuentro <TICKER>-USD en hyperliquid", "puedo operar <acción> en hyperliquid perp".
- [routine_cookbook] Before implementing or debugging ANY routine. Read this first, then pull the specific companion file(s) for what your routine actually does (data, async, reports, continuous, charts).
- [self_improve] After any feedback moment in a conversation — a correction, a stated preference, a missed pattern, a better flow discovered. Do NOT defer to end of session.

ROUTINES — executable analysis scripts:
Call via: manage_routines(action="run", name="<name>", agent="gateforum", config={...})

  - archived_analyzer: Analyze archived bot databases: list, summarize, or deep-dive into historical bot performance (shared)
  - backtest_chart: Run backtest and generate interactive chart with entry/exit markers and PnL curve. (shared)
  - backtest_compare: Compare saved backtests with overlaid PnL curves and a ranked metrics table. (shared)
  - gateforum_data: Fetch candles and fundamentals for GateForum's debate pairs.
  - gateforum_debate: Run the GateForum debate pipeline for each configured pair.
  - gateforum_init: Ensure the GateForum debate server is running and healthy.

[SESSION CONTEXT]
The user provided the following natural language context for this trading session. Use this to guide your market selection, risk appetite, and trading style:

Trade BTC-USDT, XAU-USDT and CL-USDT on gate_io_perpetual. MINIMAL TEST: position size ~$10 max, leverage 1x, at most 1 position. Prefer the highest-confidence verdict.

[CURRENT CONFIG]
These are the ACTIVE values for this session. If the strategy instructions mention different defaults, IGNORE them and use these values instead.
total_amount_quote: 50
model_base_url: 
max_ticks: 1
bot_name: 
bot_mode: auto
canvas_enabled: True
canvas_nudge_ticks: 12
canvas_band_usd: 25.0
min_order_amount_quote: 5

[RISK STATE]
Position Size: $0.00 / $10.00 limit
Open Executors: 0 / 1 limit
Drawdown: 0.0% / 5.0% limit
Status: ACTIVE

[CORE DATA - executors]
Active Executors: none running (agent: gateforum.gateforum_debate_operator_e1)
  Realized: $+0.00 | Unrealized: $+0.00 | Total PnL: $+0.00 | Volume: $0

[CORE DATA - positions]
Positions Summary [agent: gateforum.gateforum_debate_operator_e1]: no open positions

</details>

## Executor State
Active Executors: none running (agent: gateforum.gateforum_debate_operator_e1)
  Realized: $+0.00 | Unrealized: $+0.00 | Total PnL: $+0.00 | Volume: $0

## Risk State
- Position Size: $0.00 / $10.00 limit
- Open Executors: 1 / 1 limit
- Drawdown: 0.0% / 5.0% limit
- Status: ACTIVE

## Agent Response
Tick #1 complete. Summary of what happened and why:

## Tick Sequence
1. **Server** — `gateforum_init`: healthy at `127.0.0.1:8500`, graph loaded, 4 debates run. ✅
2. **Data** — `gateforum_data`: 3/3 pairs refreshed from gate_io (4h candles).
   - BTC-USDT: $63,059, −0.79%, RSI 39, **downtrend**
   - XAU-USDT: $4,378, +8.18%, RSI 42, **uptrend**
   - CL-USDT: $81.32, −4.23%, RSI 47, **uptrend**
3. **Debate** — `gateforum_debate`: all three pairs actionable at **80% confidence** (tie).

## Decision
All three verdicts tied at 80%, so I applied the tie-break on data alignment and venue simplicity:

- **BTC-USDT SHORT** — the verdict direction (SHORT) agrees with the price structure (downtrend, RSI 39). Cleanest trend-following alignment.
- XAU LONG chases an +8.18% already-extended rally; CL LONG is a dip-buy against a −4.23% period move. Both are plausible but less clean than BTC's trend-verdict agreement.
- BTC-USDT also avoids XAU/CL minimum contract-size uncertainty for a ~$10 test.

## Execution
Per session override (MINIMAL TEST), I ignored the standard conviction/sizing table and used:

- **Pair:** BTC-USDT on `gate_io_perpetual`
- **Side:** 2 (SHORT)
- **Amount:** 0.0001586 BTC ≈ **$10.00 notional**
- **Leverage:** 1×
- **Barriers:** SL 2.5% / TP 3% / 6h time limit / trailing stop (activation +1.5%, delta 3%) / market entry
- **`controller_id`** included in executor_config as required by the risk gate

✅ Executor created: `EQRGPgZrzfudHk21F1PW7yNCnUjaauh67uw2HL68maB6`

## Risk posture
- 1 of 1 allowed open positions; 0 of 1 previously open.
- ~$10 notional vs $10 size limit; well under drawdown and total-margin guardrails.
- No intervention needed on existing positions (none open).

## Journal (dry-run note)
Would record: all three pairs 80% confidence; selected BTC-USDT SHORT on trend-verdict alignment; single-position minimal test respected; remaining capacity held in reserve. No errors to log — schema was fetched before creation, so no recovery path was needed.

## Tool Calls (6)

### 1. manage_routines (completed)
**Input:**
```json
{
  "action": "run",
  "name": "gateforum_init",
  "agent": "gateforum"
}
```
**Output:**
```
{'name': 'gateforum_init', 'instance_id': '8a3a49b2', 'status': 'completed', 'report_id': 'f66873', 'result': {'text': 'GateForum debate server already healthy at http://127.0.0.1:8500.', 'table_data': [{'Field': 'Status', 'Value': 'ready'}, {'Field': 'Endpoint', 'Value': 'http://127.0.0.1:8500'}, {'Field': 'LLM Provider', 'Value': 'hy3'}, {'Field': 'Graph Loaded', 'Value': 'True'}, {'Field': 'Debates Run', 'Value': '4'}, {'Field': 'Uptime (s)', 'Value': '267.981209'}, {'Field': 'Launched This Tick', 'Value': 'no'}], 'table_columns': ['Field', 'Value'], 'chart_image': None, 'sections': None}}
```

### 2. manage_routines (completed)
**Input:**
```json
{
  "action": "run",
  "agent": "gateforum",
  "name": "gateforum_data"
}
```
**Output:**
```
{'name': 'gateforum_data', 'instance_id': 'ce11c151', 'status': 'completed', 'report_id': 'bdec82', 'result': {'text': 'GateForum data refreshed from gate_io: 3/3 pairs with candles (4h).', 'table_data': [{'Pair': 'BTC-USDT', 'Price': '63,059.0000', 'Period Δ': '-0.79%', 'RSI': '39', 'Trend': 'DOWNTREND', 'Candles': 100, 'Fundamentals': 'coingecko'}, {'Pair': 'XAU-USDT', 'Price': '4,378.1300', 'Period Δ': '+8.18%', 'RSI': '42', 'Trend': 'UPTREND', 'Candles': 100, 'Fundamentals': 'macro'}, {'Pair': 'CL-USDT', 'Price': '81.3200', 'Period Δ': '-4.23%', 'RSI': '47', 'Trend': 'UPTREND', 'Candles': 100, 'Fundamentals': 'macro'}], 'table_columns': ['Pair', 'Price', 'Period Δ', 'RSI', 'Trend', 'Candles', 'Fundamentals'], 'chart_image': None, 'sections': None}}
```

### 3. manage_routines (completed)
**Input:**
```json
{
  "action": "run",
  "agent": "gateforum",
  "name": "gateforum_debate"
}
```
**Output:**
```
{'name': 'gateforum_debate', 'instance_id': '06e9b129', 'status': 'completed', 'report_id': '0248ef', 'result': {'text': 'GateForum debated 3 pairs — 3 actionable at ≥65% confidence.', 'table_data': [{'Pair': 'BTC-USDT', 'Verdict': 'SELL', 'Direction': 'SHORT', 'Confidence': '80%', 'Actionable': 'YES', 'Status': 'ok'}, {'Pair': 'XAU-USDT', 'Verdict': 'BUY', 'Direction': 'LONG', 'Confidence': '80%', 'Actionable': 'YES', 'Status': 'ok'}, {'Pair': 'CL-USDT', 'Verdict': 'BUY', 'Direction': 'LONG', 'Confidence': '80%', 'Actionable': 'YES', 'Status': 'ok'}], 'table_columns': ['Pair', 'Verdict', 'Direction', 'Confidence', 'Actionable', 'Status'], 'chart_image': None, 'sections': None}}
```

### 4. manage_executors (completed)
**Input:**
```json
{
  "executor_type": "position_executor"
}
```
**Output:**
```
Configuration Schema for position_executor

### Position Executor
Takes directional positions with defined entry, stop-loss, and take-profit levels.

**Use when:**
- You have a clear directional view (bullish/bearish)
- You want automated stop-loss and take-profit management
- You want to define risk/reward ratios upfront

**Avoid when:**
- You want to provide liquidity (use Market Making instead)
- You need complex multi-leg strategies

#### How It Works

- Opens a directional position (long/short) with optional limit entry price
- Manages exit via triple barrier config: stop-loss, take-profit, time limit, trailing stop
- Amount is in **base currency** (NOT quote). e.g., for BTC-USDT, amount=0.01 means 0.01 BTC

**CRITICAL:**
- `amount` is in **base currency** — NOT `total_amount_quote`. To convert from USD: `amount = usd_value / entry_price`
- Always fetch the schema first via progressive disclosure (`manage_executors(executor_type='position_executor')`) before creating

#### Parameter Reference

**Core:**
- `connector_name`: Exchange connector (e.g., 'binance_perpetual')
- `trading_pair`: Trading pair (e.g., 'BTC-USDT')
- `side`: 1 (BUY/LONG) or 2 (SELL/SHORT)
- `amount`: Position size in **base currency** (e.g., 0.01 BTC). To convert from USD: `amount = usd / price`
- `entry_price`: Limit entry price (optional — omit for market entry)
- `leverage`: Leverage multiplier (default: 1)

**Triple Barrier Config (`triple_barrier_config`):**
- `stop_loss`: Stop-loss as decimal (e.g., 0.02 = 2%)
- `take_profit`: Take-profit as decimal (e.g., 0.03 = 3%)
- `time_limit`: Max position duration in seconds (optional)
- `trailing_stop.activation_price`: Price delta to activate trailing stop
- `trailing_stop.trailing_delta`: Trailing distance
- `open_order_type`: 1=MARKET, 2=LIMIT, 3=LIMIT_MAKER
- `take_profit_order_type`: same enum (default: MARKET)
- `stop_loss_order_type`: same enum (default: MARKET)
- `time_limit_order_type`: same enum (default: MARKET)

**Optional:**
- `act
```

### 5. manage_executors (completed)
**Input:**
```json
{
  "action": "create",
  "executor_type": "position_executor",
  "executor_config": {
    "connector_name": "gate_io_perpetual",
    "trading_pair": "BTC-USDT",
    "side": 2,
    "amount": 0.0001586,
    "leverage": 1,
    "controller_id": "gateforum.gateforum_debate_operator_e1",
    "triple_barrier_config": {
      "stop_loss": 0.025,
      "take_profit": 0.03,
      "time_limit": 21600,
      "trailing_stop": {
        "activation_price": 0.015,
        "trailing_delta": 0.03
      },
      "open_order_type": 1
    }
  }
}
```
**Output:**
```
Executor created successfully!

Executor ID: EQRGPgZrzfudHk21F1PW7yNCnUjaauh67uw2HL68maB6
Type: position_executor
Account: master_account

```

### 6. send_notification (completed)
**Input:**
```json
{
  "text": "GateForum tick #1 complete.\n\nDebate verdicts (all 80% confidence):\n\u2022 BTC-USDT \u2014 SHORT \u2705\n\u2022 XAU-USDT \u2014 LONG\n\u2022 CL-USDT \u2014 LONG\n\nAction: Opened 1 position (max 1 for minimal test) \u2014 BTC-USDT SHORT, ~$10 notional, 1x leverage, 2.5% SL / 3% TP / 6h time limit with trailing stop.\nChosen on tie-break: all three verdicts tied at 80%, BTC trend (downtrend, RSI 39) aligns cleanly with the SHORT verdict and avoids venue min-size uncertainty on XAU/CL.\n\nExecutor ID: EQRGPgZrzfudHk21F1PW7yNCnUjaauh67uw2HL68maB6"
}
```
**Output:**
```
{'sent': True}
```


## Stats
Duration: 182.9s
