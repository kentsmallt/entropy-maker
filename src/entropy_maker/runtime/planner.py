"""Decide the next single place or cancel for each side.

Pending actions are never re-sent. A replace of a partial uses the remaining
size, and a price change inside the minimum rest time is ignored so quotes
are not flickered.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal

from entropy_maker.config import Config
from entropy_maker.models import Action, Book, MarketSpec, RuntimeState, Side, SideSlot
from entropy_maker.quoting.quotes import compute_quotes
from entropy_maker.risk.gates import GateResult, evaluate_book_gates


@dataclass(frozen=True)
class View:
    now_ms: int
    book: Book | None
    book_rx_ms: int | None
    spec: MarketSpec
    foreign: bool
    shutdown: bool
    equity_usd: Decimal | None


def propose(
    state: RuntimeState,
    view: View,
    config: Config,
    new_cloid: Callable[[], str],
) -> tuple[RuntimeState, list[Action]]:
    if state.halted or state.uncertain:
        return state, []
    if config.order_size is None or config.max_position is None:
        return state, []
    gate = evaluate_book_gates(
        now_ms=view.now_ms,
        book=view.book,
        received_ms=view.book_rx_ms,
        spec=view.spec,
        max_age_ms=config.max_book_age_ms,
        mark_deviation_bps=config.mark_deviation_bps,
        foreign=view.foreign,
        shutdown=view.shutdown,
        equity_usd=view.equity_usd,
        min_notional=config.min_notional_usd,
    )
    if gate.pull or view.book is None:
        return _pull(state, gate)
    bid_size = _desired_size(state.bid, config.order_size)
    ask_size = _desired_size(state.ask, config.order_size)
    bid, ask = compute_quotes(
        book=view.book,
        spec=view.spec,
        position=state.position,
        bid_size=bid_size,
        ask_size=ask_size,
        max_position=config.max_position,
        half_spread_bps=config.half_spread_bps,
        skew_bps=config.skew_bps,
        min_notional=config.min_notional_usd,
    )
    actions: list[Action] = []
    state = _sync_side(state, "bid", bid, view, config, new_cloid, actions)
    state = _sync_side(state, "ask", ask, view, config, new_cloid, actions)
    return replace(state, book_rx_ms=view.book_rx_ms, updated_ms=view.now_ms), actions


def _desired_size(slot: SideSlot, full: Decimal) -> Decimal:
    if slot.phase == "empty" and slot.replace_sz is not None:
        return slot.replace_sz
    if slot.phase == "resting" and slot.remaining is not None:
        return slot.remaining
    return full


def _pull(state: RuntimeState, gate: GateResult) -> tuple[RuntimeState, list[Action]]:
    actions: list[Action] = []
    for side in ("bid", "ask"):
        slot = state.slot(side)
        if slot.phase in {"resting", "pending_place"} and slot.cloid and not slot.cancel_sent:
            actions.append(
                Action(
                    kind="cancel",
                    side=side,
                    cloid=slot.cloid,
                    purpose="risk",
                )
            )
            state = state.with_slot(
                side,
                replace(slot, phase="pending_cancel", cancel_sent=True, replace_sz=None),
            )
    return state, actions


def _sync_side(
    state: RuntimeState,
    side: Side,
    quote,
    view: View,
    config: Config,
    new_cloid: Callable[[], str],
    actions: list[Action],
) -> RuntimeState:
    slot = state.slot(side)
    if slot.phase in {"pending_place", "pending_cancel", "uncertain"}:
        return state
    if slot.phase == "empty":
        if quote is None:
            if slot.replace_sz is not None:
                return state.with_slot(side, SideSlot())
            return state
        cloid = new_cloid()
        placed = SideSlot(
            phase="pending_place",
            cloid=cloid,
            px=quote.px,
            orig_sz=quote.sz,
            remaining=quote.sz,
            placed_ms=view.now_ms,
            reduce_only=False,
        )
        actions.append(
            Action(
                kind="place",
                side=side,
                cloid=cloid,
                px=quote.px,
                sz=quote.sz,
                reduce_only=False,
                purpose="quote",
            )
        )
        return state.with_slot(side, placed)
    if slot.phase != "resting" or slot.cloid is None or slot.px is None:
        return state
    if quote is None:
        return _begin_cancel(state, side, slot, keep_size=False, actions=actions)
    if _should_replace(slot, quote.px, view.now_ms, config):
        return _begin_cancel(state, side, slot, keep_size=True, actions=actions)
    return state


def _begin_cancel(
    state: RuntimeState,
    side: Side,
    slot: SideSlot,
    keep_size: bool,
    actions: list[Action],
) -> RuntimeState:
    if slot.cancel_sent or slot.cloid is None:
        return state
    replace_sz = slot.remaining if keep_size else None
    actions.append(Action(kind="cancel", side=side, cloid=slot.cloid, purpose="quote"))
    return state.with_slot(
        side,
        replace(slot, phase="pending_cancel", cancel_sent=True, replace_sz=replace_sz),
    )


def _should_replace(slot: SideSlot, new_px: Decimal, now_ms: int, config: Config) -> bool:
    if slot.placed_ms is None or slot.px is None or slot.px == 0:
        return False
    if now_ms - slot.placed_ms < config.min_rest_ms:
        return False
    moved = abs(new_px - slot.px) / slot.px * Decimal(10000)
    return moved >= config.reprice_bps
