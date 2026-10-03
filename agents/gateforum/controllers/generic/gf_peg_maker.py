"""gf_peg_maker — a small state-machine controller that keeps a two-sided book
on a zero-fee stablecoin pair and leans on a crossing limit order whenever
realized turnover falls behind an internal pace target.

States: IDLE -> QUOTING -> (CATCHUP when behind pace) -> QUOTING ... -> HALTED
(fee/drawdown trip, or a supervisor sets `stand_down`).

The quoting side always sizes to whatever of the funding coin is actually
free (not locked in another open order), so the account's own balance is the
only cap — there is no separate clip-size parameter to keep in sync with it.
Catch-up is sized as a fraction of the pace deficit instead of a fixed clip,
and is capped by the resting depth on the side it reaches into so one order
can never walk more than a slice of the book. All catch-up and unwind orders
are priced limits through the touch; this controller never sends an
unpriced market order.

Maker timeout (2-sided, the slowest clock of the four churn desks at
`maker_timeout_s`): either side's resting maker is cancelled and replaced
with a crossing limit, sized the same way as a normal quote (free balance,
capped by resting depth), once it has sat unfilled past that long. Combined
with the deficit-based CATCHUP state this makes the pace target close to a
floor rather than a target.

Capital note (split-book): ~65% / ~$520 of the $800 entry on this Binance desk;
~$280 USDT stays on Gate.io for the debate P&L sleeve. Primary pair USD1-USDC;
fallback USD1-USDT if the primary book is unusable. P&L-sleeve stop ($100 USDT)
is enforced on the GateForum loop, not here. Split-book volume sleeve only.
"""
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Dict, List, Optional

from pydantic import Field

from hummingbot.core.data_type.common import MarketDict, PriceType, TradeType
from hummingbot.strategy_v2.controllers.controller_base import ControllerBase, ControllerConfigBase
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, ExecutorAction, StopExecutorAction

FLOOR_USD = Decimal("6")


class DeskState(str, Enum):
    IDLE = "IDLE"
    QUOTING = "QUOTING"
    CATCHUP = "CATCHUP"
    HALTED = "HALTED"


class GfPegMakerConfig(ControllerConfigBase):
    controller_type: str = "generic"
    controller_name: str = "gf_peg_maker"

    connector_name: str = Field("binance")
    trading_pair: str = Field("USD1-USDC", description="Primary zero-fee stable pair.")
    fallback_pair: str = Field("USD1-USDT", description="Failover if primary book is dead/off-peg.")
    use_pair_fallback: bool = Field(True, json_schema_extra={"is_updatable": True})

    pace_target_usd: Decimal = Field(Decimal("1500000"), json_schema_extra={"is_updatable": True})
    pace_window_s: int = Field(172800)
    pace_deadline_ts: int = Field(0, json_schema_extra={"is_updatable": True})

    deficit_floor_usd: Decimal = Field(
        Decimal("2000"), json_schema_extra={"is_updatable": True},
        description="Catch-up stays off until the pace deficit crosses this.")
    deficit_fraction: Decimal = Field(
        Decimal("0.15"), json_schema_extra={"is_updatable": True},
        description="Fraction of the current deficit sent as one catch-up order (before the depth cap applies).")
    far_side_depth_cap: Decimal = Field(
        Decimal("0.3"), json_schema_extra={"is_updatable": True},
        description="Catch-up never exceeds this share of the resting size on the side it crosses into.")
    reach_ticks: int = Field(2, json_schema_extra={"is_updatable": True})

    step_in_wide_spread: bool = Field(True, json_schema_extra={"is_updatable": True})
    tilt_ceiling_usd: Decimal = Field(Decimal("300"), json_schema_extra={"is_updatable": True})

    maker_timeout_s: float = Field(
        10.0, json_schema_extra={"is_updatable": True},
        description="A resting maker unfilled for this long is cancelled and replaced with a crossing limit sized "
                    "the same way a normal quote would be.")

    fee_ceiling_bp: Decimal = Field(Decimal("0.5"), json_schema_extra={"is_updatable": True})
    drawdown_ceiling_usd: Decimal = Field(Decimal("52"), json_schema_extra={"is_updatable": True})
    peg_lo: Decimal = Field(Decimal("0.9975"), json_schema_extra={"is_updatable": True})
    peg_hi: Decimal = Field(Decimal("1.0025"), json_schema_extra={"is_updatable": True})

    stand_down: bool = Field(False, json_schema_extra={"is_updatable": True},
                             description="Supervisor switch: cancel and hold.")
    unwind: bool = Field(False, json_schema_extra={"is_updatable": True},
                         description="Supervisor switch: cancel, rebalance to unwind_share, then stop.")
    unwind_share: Decimal = Field(Decimal("0.5"), json_schema_extra={"is_updatable": True})

    tick_interval_s: float = Field(1.0)

    def update_markets(self, markets: MarketDict) -> MarketDict:
        pairs = {self.trading_pair}
        fb = getattr(self, "fallback_pair", None)
        if fb:
            pairs.add(fb)
        markets[self.connector_name] = markets.get(self.connector_name, set()) | pairs
        return markets


class GfPegMakerController(ControllerBase):

    def __init__(self, config: GfPegMakerConfig, *args, **kwargs):
        self.config = config
        kwargs.setdefault("update_interval", float(config.tick_interval_s))
        super().__init__(config, *args, **kwargs)
        self._t_start: Optional[float] = None
        self._pace_anchor = None
        self._fill_seen: Dict[str, Decimal] = {}
        self._fee_seen: Dict[str, Decimal] = {}
        self._value_at_start: Optional[Decimal] = None
        self._halt_cause: Optional[str] = None
        self._state = DeskState.IDLE
        self._warned_off_peg = False
        self._cross_due: Dict[TradeType, bool] = {TradeType.BUY: False, TradeType.SELL: False}

    def update_config(self, new_config):
        keys = [k for k, f in type(self.config).model_fields.items() if (f.json_schema_extra or {}).get("is_updatable")]
        before = {k: getattr(self.config, k) for k in keys}
        super().update_config(new_config)
        diffs = [f"{k}:{before[k]}->{getattr(self.config, k)}" for k in keys if before[k] != getattr(self.config, k)]
        if diffs:
            self.logger().info(f"[{self.config.id}] " + " ".join(diffs))

    def _coins(self):
        return self.config.trading_pair.split("-")

    def _book_alive(self) -> bool:
        try:
            bid = self._px(PriceType.BestBid)
            ask = self._px(PriceType.BestAsk)
            return bid > 0 and ask > 0 and ask >= bid
        except Exception:
            return False

    def _ensure_pair(self) -> None:
        """Prefer USD1-USDC; fall over to USD1-USDT when primary cannot trade."""
        c = self.config
        if not getattr(c, "use_pair_fallback", True):
            return
        fb = (getattr(c, "fallback_pair", None) or "").strip()
        if not fb or fb == c.trading_pair:
            return
        if self._book_alive():
            mid = self._px(PriceType.MidPrice)
            if c.peg_lo <= mid <= c.peg_hi:
                return
        prev = c.trading_pair
        c.trading_pair = fb
        self.logger().warning(f"[{c.id}] pair failover {prev} -> {fb}")

    def _balances(self, available_only: bool):
        base, quote = self._coins()
        conn = self.market_data_provider.get_connector(self.config.connector_name)
        reader = (getattr(conn, "get_available_balance", None) or conn.get_balance) if available_only else conn.get_balance
        return Decimal(str(reader(base))), Decimal(str(reader(quote)))

    def _tick_size(self) -> Decimal:
        try:
            r = self.market_data_provider.get_trading_rules(self.config.connector_name, self.config.trading_pair)
            return Decimal(str(r.min_price_increment))
        except Exception:
            return Decimal("0")

    def _px(self, kind) -> Decimal:
        return Decimal(str(self.market_data_provider.get_price_by_type(
            self.config.connector_name, self.config.trading_pair, kind)))

    def _resting_depth(self):
        try:
            ob = self.market_data_provider.get_order_book(self.config.connector_name, self.config.trading_pair)
            bid, ask = next(ob.bid_entries(), None), next(ob.ask_entries(), None)
            return None if bid is None or ask is None else (Decimal(str(bid.amount)), Decimal(str(ask.amount)))
        except Exception:
            return None

    def _turnover(self) -> Decimal:
        for ex in self.executors_info:
            v = Decimal(str(ex.filled_amount_quote or 0))
            if v > self._fill_seen.get(ex.id, Decimal("0")):
                self._fill_seen[ex.id] = v
            f = Decimal(str(getattr(ex, "cum_fees_quote", 0) or 0))
            if f > self._fee_seen.get(ex.id, Decimal("0")):
                self._fee_seen[ex.id] = f
        return sum(self._fill_seen.values(), Decimal("0"))

    def _fees(self) -> Decimal:
        return sum(self._fee_seen.values(), Decimal("0"))

    def _pace_line(self, now, deadline, turnover, idle) -> Decimal:
        tgt = self.config.pace_target_usd

        def at(anchor, t):
            t0, v0, g, dl = anchor
            f = min(1.0, max(0.0, (t - t0) / max(1.0, dl - t0)))
            return v0 + (g - v0) * Decimal(str(f))

        if self._pace_anchor is None:
            self._pace_anchor = (self._t_start, Decimal("0"), tgt, deadline)
        elif (self._pace_anchor[2], self._pace_anchor[3]) != (tgt, deadline) or idle:
            now_v = at(self._pace_anchor, now)
            self._pace_anchor = (now, min(now_v, turnover) if idle else now_v, tgt, deadline)
        return at(self._pace_anchor, now)

    async def update_processed_data(self):
        self._ensure_pair()
        now = self.market_data_provider.time()
        if self._t_start is None:
            self._t_start = now
        deadline = self.config.pace_deadline_ts or (self._t_start + self.config.pace_window_s)
        mid = self._px(PriceType.MidPrice)
        on_peg = self.config.peg_lo <= mid <= self.config.peg_hi
        total_base, total_quote = self._balances(available_only=False)
        turnover = self._turnover()
        idle = self.config.stand_down or self.config.unwind or not on_peg
        self.processed_data = {
            "now": now, "deadline": deadline, "mid": mid, "base": total_base, "quote": total_quote,
            "on_peg": on_peg, "turnover": turnover, "paced": self._pace_line(now, deadline, turnover, idle),
        }

    @staticmethod
    def _to_units(usd: Decimal, px: Decimal) -> Decimal:
        return Decimal("0") if px <= 0 else (usd / px).quantize(Decimal("1"), rounding=ROUND_DOWN)

    def _place(self, side: TradeType, amount: Decimal, price: Optional[Decimal], strat: ExecutionStrategy):
        cfg = OrderExecutorConfig(timestamp=self.market_data_provider.time(), connector_name=self.config.connector_name,
                                  trading_pair=self.config.trading_pair, side=side, amount=amount, price=price,
                                  execution_strategy=strat, controller_id=self.config.id)
        return CreateExecutorAction(executor_config=cfg, controller_id=self.config.id)

    def determine_executor_actions(self) -> List[ExecutorAction]:
        pd = self.processed_data
        if not pd:
            return []
        c = self.config
        active = [ex for ex in self.executors_info if ex.is_active]
        value = pd["base"] * pd["mid"] + pd["quote"]
        if self._value_at_start is None:
            self._value_at_start = value

        turnover, fees = pd["turnover"], self._fees()
        if self._halt_cause is None:
            if turnover > Decimal("1200") and (fees / turnover * 10000) > c.fee_ceiling_bp:
                self._halt_cause = "fee_ceiling"
            elif self._value_at_start - value > c.drawdown_ceiling_usd:
                self._halt_cause = "drawdown_ceiling"
        if self._halt_cause or not pd["on_peg"] or c.stand_down:
            self._state = DeskState.HALTED if self._halt_cause else self._state
            if not pd["on_peg"] and not self._warned_off_peg:
                self.logger().warning(f"[{c.id}] off peg at {pd['mid']}")
                self._warned_off_peg = True
            if pd["on_peg"]:
                self._warned_off_peg = False
            return [StopExecutorAction(controller_id=c.id, executor_id=ex.id) for ex in active]
        self._warned_off_peg = False

        makers: Dict[TradeType, list] = {TradeType.BUY: [], TradeType.SELL: []}
        takers: list = []
        for ex in active:
            (makers[ex.config.side] if getattr(ex.config, "execution_strategy", None) == ExecutionStrategy.LIMIT_MAKER
             else takers).append(ex)

        if c.unwind:
            self._state = DeskState.HALTED
            if makers[TradeType.BUY] or makers[TradeType.SELL]:
                return [StopExecutorAction(controller_id=c.id, executor_id=ex.id)
                       for ex in makers[TradeType.BUY] + makers[TradeType.SELL]]
            if takers:
                return []
            total = pd["base"] * pd["mid"] + pd["quote"]
            gap = pd["base"] * pd["mid"] - total * c.unwind_share
            if abs(gap) < FLOOR_USD:
                return []
            tick = self._tick_size()
            if gap > 0:
                px = self._px(PriceType.BestBid) - tick * c.reach_ticks
                return [self._place(TradeType.SELL, self._to_units(gap, px), px, ExecutionStrategy.LIMIT)]
            px = self._px(PriceType.BestAsk) + tick * c.reach_ticks
            return [self._place(TradeType.BUY, self._to_units(-gap, px), px, ExecutionStrategy.LIMIT)]

        deficit = pd["paced"] - pd["turnover"]
        base_val, quote_val = pd["base"] * pd["mid"], pd["quote"]
        imbalance = base_val - (base_val + quote_val) / 2
        bid, ask = self._px(PriceType.BestBid), self._px(PriceType.BestAsk)
        now = pd["now"]

        timed_out = {
            side: [ex for ex in makers[side] if now - float(ex.config.timestamp) >= c.maker_timeout_s]
            for side in (TradeType.BUY, TradeType.SELL)
        }
        timeout_stops = [StopExecutorAction(controller_id=c.id, executor_id=ex.id)
                          for side in (TradeType.BUY, TradeType.SELL) for ex in timed_out[side]]
        if timeout_stops:
            for side in (TradeType.BUY, TradeType.SELL):
                if timed_out[side]:
                    self._cross_due[side] = True
            return timeout_stops

        if deficit > c.deficit_floor_usd and not takers:
            self._state = DeskState.CATCHUP
            side = TradeType.SELL if imbalance >= 0 else TradeType.BUY
            if makers[side]:
                return [StopExecutorAction(controller_id=c.id, executor_id=ex.id) for ex in makers[side]]
            tick = self._tick_size()
            px = (bid - tick * c.reach_ticks) if side == TradeType.SELL else (ask + tick * c.reach_ticks)
            size_usd = deficit * c.deficit_fraction
            depth = self._resting_depth()
            if depth is not None:
                far = depth[0] if side == TradeType.SELL else depth[1]
                size_usd = min(size_usd, far * pd["mid"] * c.far_side_depth_cap)
            avail = base_val if side == TradeType.SELL else quote_val
            size_usd = min(size_usd, avail)
            if size_usd >= FLOOR_USD:
                return [self._place(side, self._to_units(size_usd, px), px, ExecutionStrategy.LIMIT)]

        self._state = DeskState.QUOTING
        touch = {TradeType.BUY: bid, TradeType.SELL: ask}
        if c.step_in_wide_spread:
            tick = self._tick_size()
            if tick > 0:
                width = ((ask - bid) / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                if width >= 3:
                    touch = {TradeType.BUY: bid + tick, TradeType.SELL: ask - tick}
                elif width == 2:
                    touch = ({TradeType.BUY: bid, TradeType.SELL: ask - tick} if base_val >= quote_val
                            else {TradeType.BUY: bid + tick, TradeType.SELL: ask})

        allow = {TradeType.BUY: imbalance <= c.tilt_ceiling_usd, TradeType.SELL: imbalance >= -c.tilt_ceiling_usd}
        actions: List[ExecutorAction] = []
        reach_tick = self._tick_size()
        reach_depth = self._resting_depth()
        for side in (TradeType.BUY, TradeType.SELL):
            if not makers[side] and self._cross_due.get(side) and not takers:
                self._cross_due[side] = False
                px = (bid - reach_tick * c.reach_ticks) if side == TradeType.SELL else (ask + reach_tick * c.reach_ticks)
                free_base, free_quote = self._balances(available_only=True)
                spend_usd = free_quote if side == TradeType.BUY else free_base * pd["mid"]
                if reach_depth is not None:
                    far = reach_depth[0] if side == TradeType.SELL else reach_depth[1]
                    spend_usd = min(spend_usd, far * pd["mid"] * c.far_side_depth_cap)
                if spend_usd >= FLOOR_USD:
                    actions.append(self._place(side, self._to_units(spend_usd, px), px, ExecutionStrategy.LIMIT))
                continue
            stale = [ex for ex in makers[side] if Decimal(str(ex.config.price)) != touch[side]]
            actions += [StopExecutorAction(controller_id=c.id, executor_id=ex.id) for ex in stale]
            if makers[side] and not stale:
                continue
            if not allow[side]:
                continue
            free_base, free_quote = self._balances(available_only=True)
            spend_usd = free_quote if side == TradeType.BUY else free_base * pd["mid"]
            if spend_usd < FLOOR_USD:
                continue
            actions.append(self._place(side, self._to_units(spend_usd, touch[side]), touch[side],
                                       ExecutionStrategy.LIMIT_MAKER))
        return actions

    def to_format_status(self) -> List[str]:
        pd = self.processed_data or {}
        return [f"=== {self.config.id} state={self._state.value} ===",
                f"  turnover={pd.get('turnover', 0):.0f} paced={pd.get('paced', 0):.0f}"]
