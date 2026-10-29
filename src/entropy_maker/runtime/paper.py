"""Paper fills and paper acknowledgements.

A resting bid fills only when the best ask trades through it. A resting ask
fills only when the best bid trades through it. Joining the touch is not a
fill. Both sides trading through in one update halts instead of assuming a
self-match.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from entropy_maker.models import Ack, Action, Book, FillRecord, RuntimeState, SideSlot
from entropy_maker.risk.policy import PolicyError, assert_maker_order, would_take


def apply_paper_fills(state: RuntimeState, book: Book, now_ms: int) -> RuntimeState:
    # A locked book is handled by the quote gate, which pulls resting orders.
    # Filling both sides here would invent a self-trade.
    if book.is_locked_or_crossed():
        return state
    bid_hit = _traded_through(state.bid, "bid", book)
    ask_hit = _traded_through(state.ask, "ask", book)
    if bid_hit and ask_hit:
        return state
    if bid_hit:
        state = _fill(state, "bid", now_ms)
    elif ask_hit:
        state = _fill(state, "ask", now_ms)
    return state


def paper_equity(state: RuntimeState, mark: Decimal, book: Book | None) -> Decimal:
    mid = book.mid if book is not None and book.mid is not None else mark
    return state.cash + (state.position * mid)


def paper_ack(action: Action, book: Book | None, position: Decimal) -> Ack:
    if action.kind == "cancel":
        return Ack("canceled")
    if action.px is None or action.sz is None:
        return Ack("rejected", detail="missing price or size")
    try:
        assert_maker_order(
            tif="Alo",
            size=action.sz,
            price=action.px,
            reduce_only=action.reduce_only,
            position=position if action.reduce_only else Decimal(0),
            side=action.side,
        )
    except PolicyError as exc:
        return Ack("rejected", detail=str(exc))
    if book is not None and would_take(action.side, action.px, book.best_bid, book.best_ask):
        return Ack("rejected", detail="Post only order would have immediately matched")
    return Ack("resting")


def _traded_through(slot: SideSlot, side: str, book: Book) -> bool:
    if slot.phase != "resting" or slot.px is None or slot.remaining is None or slot.remaining <= 0:
        return False
    if side == "bid":
        return book.best_ask is not None and book.best_ask <= slot.px
    return book.best_bid is not None and book.best_bid >= slot.px


def _fill(state: RuntimeState, side: str, now_ms: int) -> RuntimeState:
    slot = state.slot(side)
    assert slot.px is not None and slot.remaining is not None and slot.cloid is not None
    if side == "bid":
        cash = state.cash - (slot.px * slot.remaining)
        position = state.position + slot.remaining
    else:
        cash = state.cash + (slot.px * slot.remaining)
        position = state.position - slot.remaining
    fill = FillRecord(
        cloid=slot.cloid,
        side=side,  # type: ignore[arg-type]
        px=format(slot.px, "f"),
        sz=format(slot.remaining, "f"),
        time_ms=now_ms,
    )
    fills = (state.recent_fills + (fill,))[-20:]
    return replace(state, cash=cash, position=position, recent_fills=fills).with_slot(side, SideSlot())
