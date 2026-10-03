"""GateForum split-book capital ($800 Cup entry).

Volume sleeve = Binance gf_peg_maker (USD1-USDC, fallback USD1-USDT).
P&L sleeve   = Gate.io perps debate operator (this agent).

GateForum is **split-book**: two funded sleeves, no auto-transfer between them.
"""
from __future__ import annotations

RACE_ENVELOPE_USD = 800.0
VOLUME_ARM_USD = 520.0   # 65% — Binance stable desk
PNL_ARM_USD = 280.0      # 35% — Gate.io directional council
VOLUME_ARM_PCT = VOLUME_ARM_USD / RACE_ENVELOPE_USD
PNL_ARM_PCT = PNL_ARM_USD / RACE_ENVELOPE_USD

# Absolute P&L-sleeve stop (USDT mark loss vs session entry NAV).
PNL_STOP_LOSS_USD = 100.0

VOLUME_CONTROLLER = "gf_peg_maker"
VOLUME_PAIR = "USD1-USDC"
VOLUME_PAIR_FALLBACK = "USD1-USDT"
PNL_LOOP = "gateforum_debate_operator"

# Default conviction → notional % of *P&L sleeve free balance* (before DD scale).
CONF_SIZE_PCT = (
    (65.0, 74.999, 0.08),
    (75.0, 84.999, 0.12),
    (85.0, 100.0, 0.16),
)

# Drawdown scale on the P&L sleeve (still trade; shrink size). Hard stop is $100.
DD_SCALE = (
    (0.0, 4.0, 1.0),
    (4.0, 10.0, 0.5),
    (10.0, 100.0, 0.25),
)

MAX_OPEN = 3
MAX_LEVERAGE = 2
# Soft per-ticket cap so 3 names cannot each demand 16% blindly into the risk gate.
MAX_TICKET_USD = 120.0
# Never open dust the venue will reject; never refuse a clearable min forever.
ABSOLUTE_MIN_NOTIONAL_USD = 8.0

ORGANIZER_BLURB = (
    f"Fund ${VOLUME_ARM_USD:.0f} on Binance {VOLUME_PAIR} ({VOLUME_CONTROLLER}; "
    f"fallback {VOLUME_PAIR_FALLBACK}) and ${PNL_ARM_USD:.0f} USDT on Gate.io perps "
    f"({PNL_LOOP}). Total ${RACE_ENVELOPE_USD:.0f}. P&L-sleeve stop "
    f"${PNL_STOP_LOSS_USD:.0f} USDT. Split-book capital design."
)


def allocation_split() -> dict:
    return {
        "total_envelope_usd": RACE_ENVELOPE_USD,
        "volume_arm_usd": VOLUME_ARM_USD,
        "pnl_arm_usd": PNL_ARM_USD,
        "volume_arm_pct": VOLUME_ARM_PCT,
        "pnl_arm_pct": PNL_ARM_PCT,
        "pnl_stop_loss_usd": PNL_STOP_LOSS_USD,
        "volume_controller": VOLUME_CONTROLLER,
        "volume_pair": VOLUME_PAIR,
        "volume_pair_fallback": VOLUME_PAIR_FALLBACK,
        "pnl_loop": PNL_LOOP,
        "max_ticket_usd": MAX_TICKET_USD,
        "max_open": MAX_OPEN,
        "max_leverage": MAX_LEVERAGE,
        "organizer_blurb": ORGANIZER_BLURB,
    }


def conf_size_pct(confidence: float) -> float:
    c = float(confidence)
    for lo, hi, pct in CONF_SIZE_PCT:
        if lo <= c <= hi:
            return pct
    return 0.0


def drawdown_scale(drawdown_pct: float) -> float:
    d = max(0.0, float(drawdown_pct))
    for lo, hi, scale in DD_SCALE:
        if lo <= d < hi or (hi >= 100 and d >= lo):
            return scale
    return 0.25


def portfolio_stop_usd(
    *,
    entry_nav_usd: float,
    current_nav_usd: float,
    stop_usd: float = PNL_STOP_LOSS_USD,
    enabled: bool = True,
) -> dict:
    """Absolute USDT stop on the Gate.io P&L sleeve NAV."""
    limit = float(stop_usd)
    if not enabled or limit <= 0 or entry_nav_usd <= 0:
        return {
            "action": "HOLD",
            "loss_usd": 0.0,
            "stop_usd": limit,
            "reason": "portfolio stop off or no entry NAV",
        }
    loss = float(entry_nav_usd) - float(current_nav_usd)
    if loss + 1e-9 >= limit:
        return {
            "action": "KILL_PORTFOLIO",
            "loss_usd": round(loss, 4),
            "stop_usd": limit,
            "reason": f"P&L-sleeve mark loss ${loss:.2f} >= stop ${limit:.2f} USDT",
        }
    return {
        "action": "HOLD",
        "loss_usd": round(loss, 4),
        "stop_usd": limit,
        "reason": "within P&L-sleeve dollar stop",
    }


def target_notional_usd(
    *,
    free_balance_usd: float,
    confidence: float,
    drawdown_pct: float = 0.0,
    open_notional_usd: float = 0.0,
    max_ticket_usd: float = MAX_TICKET_USD,
    book_usd: float = PNL_ARM_USD,
) -> dict:
    """Conviction × DD scale → quote notional, capped by ticket and remaining book room.

    Returns placeable=False when confidence below floor or no free room — caller SIT.
    """
    bal = max(float(free_balance_usd), 0.0)
    pct = conf_size_pct(confidence)
    if pct <= 0 or bal <= 0:
        return {
            "notional_usd": 0.0,
            "pct": 0.0,
            "dd_scale": drawdown_scale(drawdown_pct),
            "placeable": False,
            "reason": "confidence below 65 or empty balance",
        }
    scale = drawdown_scale(drawdown_pct)
    raw = bal * pct * scale
    # Remaining headroom under soft ticket + sleeve (open names share the book).
    room_ticket = max(max_ticket_usd, 0.0)
    room_book = max(float(book_usd) - max(float(open_notional_usd), 0.0), 0.0)
    notional = min(raw, room_ticket, room_book, bal)
    return {
        "notional_usd": round(notional, 4),
        "pct": pct,
        "dd_scale": scale,
        "placeable": notional + 1e-9 >= ABSOLUTE_MIN_NOTIONAL_USD,
        "reason": (
            f"conf pct {pct:.0%} × dd×{scale} → ${notional:.2f} "
            f"(cap ticket ${room_ticket:.0f} / book room ${room_book:.0f})"
        ),
    }


def base_amount_from_notional(
    *,
    notional_usd: float,
    entry_price: float,
    quanto_multiplier: float = 1.0,
    min_notional_usd: float = 0.0,
    min_base_amount: float = 0.0,
    max_notional_usd: float = MAX_TICKET_USD,
    free_balance_usd: float | None = None,
) -> dict:
    """Convert quote notional → whole-contract base amount the venue can accept.

    Unstick rules:
    - Round DOWN to quanto grid.
    - If rounded notional < venue min but free balance and ticket cap can fund the
      min, **lift to min** (one clearable contract) instead of permanent skip.
    - If even the min cannot fit free balance / caps → placeable=False with reason.
    """
    px = float(entry_price)
    q = max(float(quanto_multiplier or 1.0), 1e-12)
    want = max(float(notional_usd), 0.0)
    if px <= 0 or want <= 0:
        return {
            "amount_base": 0.0,
            "notional_usd": 0.0,
            "placeable": False,
            "lifted_to_min": False,
            "reason": "bad price or zero notional",
        }

    def contracts_for(n_usd: float) -> float:
        raw_base = n_usd / px
        # whole multiples of quanto
        n_contracts = int(raw_base / q)
        return n_contracts * q

    amount = contracts_for(want)
    notion = amount * px
    min_n = max(float(min_notional_usd or 0.0), ABSOLUTE_MIN_NOTIONAL_USD)
    min_b = max(float(min_base_amount or 0.0), 0.0)
    # Venue may quote min in base
    if min_b > 0:
        min_n = max(min_n, min_b * px)

    lifted = False
    bal = None if free_balance_usd is None else max(float(free_balance_usd), 0.0)
    cap = min(float(max_notional_usd), bal if bal is not None else float(max_notional_usd))

    if amount <= 0 or notion + 1e-9 < min_n:
        # Try lift to minimum clearable size
        need = min_n
        if need <= cap + 1e-9 and (bal is None or need <= bal + 1e-9):
            amount = contracts_for(need)
            # ensure at least one quanto if still zero
            if amount <= 0:
                amount = q
            notion = amount * px
            # if still short due to grid, add one contract
            if notion + 1e-9 < min_n and (bal is None or (amount + q) * px <= bal + 1e-9):
                if (amount + q) * px <= cap + 1e-9:
                    amount = amount + q
                    notion = amount * px
            lifted = True
        else:
            return {
                "amount_base": 0.0,
                "notional_usd": 0.0,
                "placeable": False,
                "lifted_to_min": False,
                "reason": (
                    f"need min notional ${min_n:.2f} but cap/balance only "
                    f"${cap:.2f} — cannot place"
                ),
            }

    if notion > cap + 1e-6:
        amount = contracts_for(cap)
        notion = amount * px
        if notion + 1e-9 < min_n:
            return {
                "amount_base": 0.0,
                "notional_usd": 0.0,
                "placeable": False,
                "lifted_to_min": False,
                "reason": f"after cap ${cap:.2f} still under venue min ${min_n:.2f}",
            }

    return {
        "amount_base": round(amount, 10),
        "notional_usd": round(notion, 4),
        "placeable": amount > 0 and notion + 1e-9 >= min_n,
        "lifted_to_min": lifted,
        "reason": (
            f"base {amount} (~${notion:.2f})"
            + (" lifted to venue min" if lifted else "")
        ),
    }


def size_order(
    *,
    free_balance_usd: float,
    confidence: float,
    entry_price: float,
    drawdown_pct: float = 0.0,
    open_notional_usd: float = 0.0,
    quanto_multiplier: float = 1.0,
    min_notional_usd: float = 0.0,
    min_base_amount: float = 0.0,
) -> dict:
    """One-call path: confidence → notional → venue base amount."""
    tgt = target_notional_usd(
        free_balance_usd=free_balance_usd,
        confidence=confidence,
        drawdown_pct=drawdown_pct,
        open_notional_usd=open_notional_usd,
    )
    if not tgt["placeable"]:
        return {**tgt, "amount_base": 0.0, "lifted_to_min": False}
    base = base_amount_from_notional(
        notional_usd=tgt["notional_usd"],
        entry_price=entry_price,
        quanto_multiplier=quanto_multiplier,
        min_notional_usd=min_notional_usd,
        min_base_amount=min_base_amount,
        free_balance_usd=free_balance_usd,
    )
    return {
        "notional_usd": base["notional_usd"],
        "amount_base": base["amount_base"],
        "pct": tgt["pct"],
        "dd_scale": tgt["dd_scale"],
        "placeable": base["placeable"],
        "lifted_to_min": base["lifted_to_min"],
        "reason": f"{tgt['reason']} | {base['reason']}",
    }
