"""Apply a single gateway acknowledgement. Timeouts do not create a retry."""

from __future__ import annotations

from dataclasses import replace

from entropy_maker.models import Ack, Action, RuntimeState, SideSlot, halt


def apply_result(state: RuntimeState, action: Action, ack: Ack, *, confirm_polls: int) -> RuntimeState:
    slot = state.slot(action.side)
    if slot.cloid != action.cloid:
        return halt(state, "ack_cloid_mismatch")
    if action.kind == "place":
        return _apply_place(state, action, ack, slot)
    return _apply_cancel(state, action, ack, slot, confirm_polls)


def _apply_place(state: RuntimeState, action: Action, ack: Ack, slot: SideSlot) -> RuntimeState:
    if ack.kind == "resting":
        updated = replace(slot, phase="resting", oid=ack.oid if ack.oid is not None else slot.oid)
        return state.with_slot(action.side, updated)
    if ack.kind == "rejected":
        return state.with_slot(action.side, SideSlot())
    if ack.kind == "filled":
        uncertain = replace(slot, phase="uncertain")
        return halt(state.with_slot(action.side, uncertain), "alo_filled_immediately")
    # Timeout or an unreadable ack: leave the pending place in place and reconcile.
    # The order is not sent again.
    return state


def _apply_cancel(
    state: RuntimeState,
    action: Action,
    ack: Ack,
    slot: SideSlot,
    confirm_polls: int,
) -> RuntimeState:
    del confirm_polls
    if ack.kind == "canceled":
        return state.with_slot(action.side, SideSlot(replace_sz=slot.replace_sz))
    # Rejected, timeout, or unknown: do not send the cancel again. Reads decide.
    return state.with_slot(action.side, replace(slot, phase="pending_cancel", cancel_sent=True))
