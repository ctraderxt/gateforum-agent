"""GateForum split-book alloc + size unstick."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parents[1] / "routines" / "_gateforum_alloc.py"
    spec = importlib.util.spec_from_file_location("gf_alloc", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load()


def test_split_sums_800():
    s = M.allocation_split()
    assert s["volume_arm_usd"] + s["pnl_arm_usd"] == 800
    assert s["volume_arm_usd"] == 520
    assert s["pnl_arm_usd"] == 280
    assert s["pnl_stop_loss_usd"] == 100
    assert s["volume_pair"] == "USD1-USDT"
    assert s["volume_pair_fallback"] == "FDUSD-USDT"
    assert "split-book" in s["organizer_blurb"].lower()
    assert "split-book" in s["organizer_blurb"].lower()


def test_pnl_stop_100_usdt():
    hit = M.portfolio_stop_usd(entry_nav_usd=280, current_nav_usd=180, stop_usd=100)
    assert hit["action"] == "KILL_PORTFOLIO"
    ok = M.portfolio_stop_usd(entry_nav_usd=280, current_nav_usd=200, stop_usd=100)
    assert ok["action"] == "HOLD"


def test_size_placeable_xrp_like():
    # 12% of 280 = 33.6 → placeable
    o = M.size_order(
        free_balance_usd=280, confidence=80, entry_price=0.60,
        quanto_multiplier=1.0, min_notional_usd=5,
    )
    assert o["placeable"] is True
    assert o["amount_base"] > 0
    assert o["notional_usd"] <= 120


def test_size_lifts_to_venue_min_instead_of_stuck():
    # Tiny conviction slice would round under min — lift when balance allows.
    o = M.base_amount_from_notional(
        notional_usd=9.0,
        entry_price=2500.0,  # CL-like
        quanto_multiplier=0.01,
        min_notional_usd=25.0,
        free_balance_usd=280.0,
        max_notional_usd=120.0,
    )
    assert o["placeable"] is True
    assert o["lifted_to_min"] is True
    assert o["notional_usd"] + 1e-6 >= 25.0


def test_size_refuses_when_min_exceeds_balance():
    o = M.base_amount_from_notional(
        notional_usd=10.0,
        entry_price=2500.0,
        quanto_multiplier=0.01,
        min_notional_usd=50.0,
        free_balance_usd=40.0,
        max_notional_usd=120.0,
    )
    assert o["placeable"] is False


def test_dd_scale_and_conf_floor():
    assert M.conf_size_pct(64) == 0.0
    assert M.conf_size_pct(70) == 0.08
    assert M.drawdown_scale(5) == 0.5
    cold = M.target_notional_usd(free_balance_usd=280, confidence=60)
    assert cold["placeable"] is False


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("OK", name)
    print("ALL PASS")
