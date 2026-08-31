# GateForum — Video Script (~5 min)

**Deck:** `gateforum-deck.html`  
**Length:** 4–6 minutes. Talk over the slides. Don’t read the cards.  
**Voice:** first person, like you’re explaining it to another Hummingbot user. Simple words. Council energy — not a nature documentary.

The story lives **in this video**. Point. Don’t recite.

---

## Slide 00 — About Me  (~30s)

**On screen:** “I am Dan…” Point the first teal line, then Condor.

Hi everyone.  
Good morning,  
good noon, or  
good night.

I am Dan.
a.k.a ctraderxt in Discord.  
I am a Hummingbot community member  
since 2022.

I mostly run  
Pure Market Making before  
due to limited coding skills.
I am into infrastructure,
system admin stuff.

Happy to see Hummingbot  
embracing AI with Condor.  
It gives me some superpowers too.  
Now I can build  
AI trading agents  
without being 
a Python expert.

GateForum is that agent.

---

## Slide 01 — About GateForum  (~25s)

**On screen:** title + 12 Agents · Research → Analysis → Decision.  
Point the teal line. Don’t recite 3 / 12 / 15m / Gate.com.

Meet GateForum.  
A multi-agent research  
and decision trader  
on Gate.com 
perpetuals.

Twelve AI agents.  
They do Research →  
Analysis →  
Decision.

Every fifteen minutes,  
a fresh decision.

It’s like having  
an entire trading team —  
analysts,  
researchers,  
risk managers —  
that never sleeps.

Inspired by  
TauricResearch 
TradingAgents.  
This is the crypto  
and Condor version.

---

## Slide 02 — 12 Agents  (~25s)

**On screen:** twelve cards. Finger left row (analysts), then amber (bull / bear / judge), then risk, then teal (judge + trader). Don’t read every card.

Meet the council.

Four analysts.  
Bull and bear.  
A research judge.

Three risk analysts.  
The risk judge  
as the final gate.  
And a trader  
who only executes.

Twelve different 
perspectives  
on every  
single  
trade.

One model can 
hallucinate.  

A council 
catches errors.

That’s the whole idea:  
don’t trust a single mind  
with your money.

---

## Slide 03 — The Research Floor  (~40s)

**On screen:** four phases. Point Phase 1 → 2 → 3 → 4. Land the last line.

Here’s how they decide.

Phase 1 for Research.
Four analysts  
gather intelligence —  
market,  
news,  
social,  
fundamentals.

Phase 2 for Analysis.
The bull  
and the bear  
go head to head.  
A research judge  
picks a winner.

Phase 3 for Risk Assessment
Three risk analysts —  
aggressive,  
neutral,  
conservative —  
assess the trade.  
The risk judge rules:  
buy,  
sell,  
or hold.

The part I love:  
no single agent can block a trade.  
A HOLD needs at least two  
of three risk analysts.  

One overly-cautious personality  
cannot hijack the council.

It is democracy,  
with a stop-loss.

---

## Slide 04 — Live Research Floor  (~25s)

**On screen:** the floor screenshot. Point the page. Don’t read the transcript.

To see the twelve agents in action,  
I built this Research Floor webpage.

This is live.  
Every research cycle,  
every data point,  
every ruling —  
published every fifteen minutes.

You can watch the council think.  
No black box.  
Anyone can read  
exactly *why*  
the bot decided  
what it decided.

Total transparency.

---

## Slide 05 — Strategy Stack  (~30s)

**On screen:** four layers. Point brain → routines → council server → Hummingbot.

This is the Strategy Stack.

Condor is the brain.  
The AI that runs the loop  
and makes the calls.

Routines do the plumbing.  
Init. 
Data. 
Research.

A self-contained  
research server  
runs the twelve-agent council.

Hummingbot executes  
on Gate.com  
with a platform-enforced safety net —  
two percent stop,  
four percent take profit,  
one-hour time limit,  
trailing stop.

The agent cannot  
open an 
unprotected position.

---

## Slide 06 — Pair Selection  (~40s)

**On screen:** BTC / XAU / CL. Finger each card. Then the bottom line.

For the Pair Selection.  
Quick story.

My original plan?  
Ten pairs.  
Ten different markets.  
Maximum diversification, right?

But when I started testing,
the LLM cost  
and the time to research ten pairs  
was too long.  
and too expensive.

By the time the council finished,  
the decisions were already stale.

So I cut it to three —  
BTC,  
gold,  
and oil.

Crypto.  
Precious metals.  
and energy.

Three markets that don’t move together.  
When crypto chops,  
commodities still move.

Quality over quantity:  
three pairs  
the council can research deeply,  
every cycle.

---

## Slide 07 — Risk Framework  (~30s)

**On screen:** three layers + self-heal. Point left, right, then the teal card. Don’t read every percent.

Risk is where most strategies die.  
GateForum layers it three ways.

First — 
Research Consensus.  
Sixty-five percent confidence to enter.  
A hold needs two of three analysts.

Second — 
Position Sizing.  
Max three positions.  
Capped notional.  
Capped leverage.

Third — 
Exit Rules.
Every position carries  
a platform-enforced exit.  
Stop loss. 
Take profit. 
Time limit. 
Trailing stop.

And a bonus -
Self-Healing.  
A watchdog watches the stack  
for forty-eight hours.  
Restarts services.  
Closes orphaned positions.  
It runs the competition  
with no human in the loop.

---

## Slide 08 — Why This Wins  (~25s)

**On screen:** the seven edges. Don’t read them. Pick four, then stop.

Why this stands out.

A council,  
not a single mind.

Full transparency —  
the reasoning is right there.

Risk triangulation —  
a committee,  
not a formula.

Three uncorrelated markets,  
so something is always moving.

A strategy you can read,  
understand,  
and trust.

---

## Slide 09 — Demo  (~20s, then go live)

**On screen:** four centered lines, then the Aristotle quote.  
Point top to bottom. Don’t recap the council. Don’t read the quote twice.

Alright, 
Lets see Gateforum
running in Condor
and Trading in Gate.

We have Condor
running live.
And here is the 
Live Research Floor
webpage running in
localhost at port 86-hundred

Currently the Council has decided
to SHORT at 3 pairs

These 3 panels
are showing the reports of the 
12 Agents like the
Analysts for Market, Social and News.
Bull and Bear Researchers,
and so on.

Below here 
will be listed
the past trade sessions.

Now, lets see the Condor Routines.
1st, The INIT routines
which checks the server health

2nd, the Data routines
which gives us market
intelligence report

and finally, the Research routines
which gives us the decisions
and the detailed reports of the 
agents

Then, lets check the orders
at Gate perpetuals.
Here, we can see the
Short positions of the 3 pairs.

That's it, 
Gateforum is Fully tested.  
Running in Condor.  
Trading at Gate Perpetual  
With the live 
Research Floor webpage.

I will leave with 
my favorite quote
that says:

The whole is greater  
than the sum of its parts.

Alright, 
Let’s go live.

Thank you.

See you soon.

---

## Timing

| Slide | ~s |
|---|---|
| 00 About Me | 30 |
| 01 About GateForum | 25 |
| **02 12 Agents** | **25** |
| 03 Research Floor | 40 |
| 04 Live Floor | 25 |
| 05 Stack | 30 |
| 06 Pairs | 40 |
| 07 Risk | 30 |
| 08 Why | 25 |
| **09 Demo** | **20 + live** |
| **Talk before demo** | **~4:50** |

If you run long, shorten **05** and **07**. Never shorten **00**, **03**, or **06**.

Under four minutes? Keep 00, 01, 02, 03, 06, 09. Skip stack and risk details.

---

## How to say it

- **00:** “I am Dan” first, then 2022. Don’t say 2021. Don’t invent a Discord handle that isn’t on the slide.
- **01:** say **Decision**, not Verdict. Don’t recite the four stats.
- **02:** finger the cards. Don’t name all twelve.
- **03:** “It’s democracy, with a stop-loss” is the line. Land it. Pause.
- **04:** let the screenshot talk. One sentence on live + 15 minutes is enough.
- **06:** ten pairs → three is the human beat. Tell it like a mistake you learned from.
- **09:** point Fully Tested → Condor → Gate.com → Research Floor. Aristotle once. Then demo. Don’t add a new idea after the quote.
- Don’t name other Cup projects. This video is only this desk.
- Don’t promise always profitable.
