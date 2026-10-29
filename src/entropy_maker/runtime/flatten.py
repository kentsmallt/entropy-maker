"""Reduce-only ALO flatten. Never an IOC, and never larger than the position."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from entropy_maker.config import Config
from entropy_maker.market.rounding import quantize_size
from entropy_maker.models import Action, RuntimeState, Side, SideSlot, halt
from entropy_maker.quoting.quotes import join_touch
from entropy_maker.runtime.planner import View


def plan_flatten(
    state: RuntimeState,
    view: View,
    config: Config,
    new_cloid: Callable[[], str],
) -> tuple[RuntimeState, list[Action]]:
    if state.halted or state.uncertain:
        return state, []
    if view.book is None or view.shutdown:
        return state, []
    actions: list[Action] = []
    state, pending = _cancel_quotes(state, actions)
    if pending or actions:
        return state, actions
    if state.position == 0:
        return state, []
    side: Side = "ask" if state.position > 0 else "bid"
    slot = state.slot(side)
    if slot.phase != "empty":
        return state, []
    try:
        size = quantize_size(abs(state.position), view.spec.sz_decimals)
    except ValueError:
        return halt(state, "position_dust"), []
    if size > abs(state.position):
        return halt(state, "flatten_size"), []
    quote = join_touch(side, size, view.spec, view.book)
    if quote is None:
        return state, []
    if quote.sz > abs(state.position):
        return halt(state, "flatten_size"), []
    cloid = new_cloid()
    placed = SideSlot(
        phase="pending_place",
        cloid=cloid,
        px=quote.px,
        orig_sz=quote.sz,
        remaining=quote.sz,
        placed_ms=view.now_ms,
        reduce_only=True,
    )
    actions.append(
        Action(
            kind="place",
            side=side,
            cloid=cloid,
            px=quote.px,
            sz=quote.sz,
            reduce_only=True,
            purpose="flatten",
        )
    )
    return state.with_slot(side, placed), actions


def _cancel_quotes(state: RuntimeState, actions: list[Action]) -> tuple[RuntimeState, bool]:
    pending = False
    for side in ("bid", "ask"):
        slot = state.slot(side)
        if slot.phase in {"pending_place", "pending_cancel"}:
            pending = True
            continue
        if slot.phase == "resting" and slot.cloid and not slot.cancel_sent:
            if state.position == 0 or not slot.reduce_only:
                actions.append(Action(kind="cancel", side=side, cloid=slot.cloid, purpose="risk"))
                state = state.with_slot(
                    side,
                    replace(slot, phase="pending_cancel", cancel_sent=True, replace_sz=None),
                )
    return state, pending
