# GateForum — organizer capital ($800)

GateForum is a **12-role research council** on Gate.io USDT perps with a separate Binance stable volume sleeve. **Split-book**: capital does not auto-move between venues.

| Sleeve | Share | USD | Venue | Component |
|---|---|---|---|---|
| **Volume** | 65% | **520** | Binance spot | `gf_peg_maker` |
| **P&L** | 35% | **280** | Gate.io USDT-M | `gateforum_debate_operator` |
| **Total** | 100% | **800** | two venues | — |

## Volume pair

1. **Primary:** `USD1-USDC`
2. **Fallback:** `USD1-USDT` if primary book is missing, halted, or off-peg

Sample: `conf/gf_peg_maker.sample.yml`.

## P&L stop

| Rule | Value |
|---|---|
| Sleeve stop | **$100 USDT** absolute mark loss on the Gate.io book |
| Basis | P&L sleeve NAV (not 10% of $800 volume+PnL blended unless you choose) |
| Code | `pnl_stop_loss_usd: 100` · `portfolio_stop_usd` |

## Order sizing

`routines/_gateforum_alloc.size_order` — conviction % × drawdown scale, round to quanto, **lift to venue min** when free balance allows so the desk does not sit unable to place.
