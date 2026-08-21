# GateForum — Builders Cup Video Script (~5 min)

**Format:** 10 slides · ~25-35s each · conversational, not read verbatim.
**Goal:** memorable + entertaining — judges vote from this video.
**Pacing tip:** pause after the punchlines (marked 🎯). Smile on the personal-story lines.

---

## SLIDE 00 — About Me (~30s)

"I'm a Hummingbot community member since 2022, and like a lot of you, I mostly run Pure Market Making — my coding skills are limited, I'll be honest. 🎯 But over the past year I've been vibe-coding trading strategies — throwing ideas at the wall and seeing what sticks. I built several. Some were fine. None of them made me feel like I'd unlocked something… until this one. When I watched 12 AI agents research, argue, and decide before *every single trade* — I knew the potential here is actually wild. And honestly? This is just scratching the surface."

---

## SLIDE 01 — About GateForum (~25s)

"Meet GateForum — a multi-agent research and decision trader on Gate.com perpetuals. Twelve AI agents. Research → analysis → verdict. Every 15 minutes, a fresh decision. 🎯 It's like having an entire trading desk — analysts, researchers, risk managers — that never sleeps."

---

## SLIDE 02 — The Research Floor (~40s)

"Here's how it works. Four analysts gather intelligence — market, news, social, fundamentals. Then the bull and the bear go head to head, and a research judge picks a winner. Three risk analysts — aggressive, neutral, conservative — assess the trade. And the risk judge rules: buy, sell, or hold.

But here's the part I love: 🎯 no single agent can block a trade. A HOLD verdict needs at least two of three risk analysts to agree. So one overly-cautious personality can't hijack the whole council. It's democracy, with a stop-loss."

---

## SLIDE 03 — Live Research Floor (~25s)

"And this is the floor — live, in real time. Every research cycle, every data point, every ruling — published every 15 minutes. 🎯 You can literally watch the council think. No black box, no mystery. Judges can read exactly *why* the bot decided what it decided. Total transparency."

---

## SLIDE 04 — Strategy Stack (~30s)

"Under the hood: Condor is the brain — the AI agent that runs the loop and makes the calls. Condor routines handle all the data plumbing. A self-contained research server runs the 12-agent council. And Hummingbot executes on Gate.com — with a platform-enforced safety net: 2% stop loss, 4% take profit, a 2-hour time limit, trailing stop. 🎯 The agent literally *cannot* open an unprotected position. Even an AI can't be reckless."

---

## SLIDE 05 — The 12 Agents (~25s)

"Meet the council. Four analysts, bull and bear, a research judge, three risk analysts — and the risk judge, the final gate. Twelve different perspectives on every single trade. 🎯 One model can hallucinate — a council catches errors. That's the whole idea: don't trust a single mind with your money."

---

## SLIDE 06 — Pair Selection (~40s)

"Now — the pairs. Quick story. My original plan? Ten pairs. Ten different markets. Maximum diversification, right? 🎯 But when I started testing… the LLM cost and the time to research ten pairs was just too long, and too expensive. By the time the council finished, the verdicts were already stale. So I cut it down to three — BTC, gold, and oil. Crypto, precious metals, energy. Three markets that don't move together — when crypto chops, commodities still move. Quality over quantity: three pairs the council can research deeply, every single cycle."

---

## SLIDE 07 — Risk Framework (~30s)

"Risk is where most strategies die. So GateForum layers it three ways. Research consensus — a 65% confidence floor, and a hold needs two of three analysts. Position sizing — max three positions, capped notional, capped leverage. And every position carries a platform-enforced exit: stop loss, take profit, time limit, trailing. Plus it self-heals — a watchdog monitors the whole stack for 48 hours straight, restarts services, even closes orphaned positions. 🎯 It runs the entire competition with no human in the loop."

---

## SLIDE 08 — Why This Wins (~25s)

"Why will this stand out? A council, not a single mind. Full transparency — the reasoning is right there for the judges to read. Risk triangulation — a committee, not a formula. Multi-source intelligence — price, news, social, fundamentals. And three uncorrelated markets so there's always something moving. 🎯 This is a strategy you can read, understand, and trust."

---

## SLIDE 09 — Closing (~20s)

"Twelve agents. Research → analysis → verdict. GateForum — the whole is greater than the sum of its parts. 🎯 And honestly? This is just scratching the surface. Thank you."

---

## Delivery tips for the video

1. **Energy:** smile on the personal-story lines (vibe-coding, "potential is wild") — that's the hook.
2. **The 🎯 moments:** pause half a beat after each — they're the memorable lines.
3. **Slide 06 (pairs):** the "ten pairs → three pairs" story is your most *human* moment — tell it like you're explaining a mistake you learned from, not a fact.
4. **Slide 02 (council):** "It's democracy, with a stop-loss" is the line people will remember — land it.
5. **Under 5 min:** trim slides 04/07 if needed (the most technical) — never trim 00, 06, or 09.
6. **End on a smile:** "just scratching the surface" + thank you = open, confident close.
