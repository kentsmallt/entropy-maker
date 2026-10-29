"""Reasons to pull quotes. A pulled quote is canceled, never flipped to IOC."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from entropy_maker.identity.cloid import is_ours
from entropy_maker.market.book import book_is_stale, deviation_bps
from entropy_maker.models import Book, MarketSpec, OpenOrder


@dataclass(frozen=True)
class GateResult:
    pull: bool
    reason: str | None = None


def classify_open_orders(
    orders: tuple[OpenOrder, ...] | list[OpenOrder],
    coin: str,
    slot_cloids: set[str],
    slot_oids: set[int],
    ours,
) -> tuple[tuple[OpenOrder, ...], tuple[OpenOrder, ...]]:
    """Split book orders into foreign orders and tagged orphans.

    A tagged orphan is one of our cloids that the state file is not tracking.
    Untagged orders on this coin are foreign and block new quotes.
    """
    foreign: list[OpenOrder] = []
    orphans: list[OpenOrder] = []
    known = {item.lower() for item in slot_cloids}
    for order in orders:
        if order.coin != coin:
            continue
        tracked = (order.cloid is not None and order.cloid.lower() in known) or (
            order.oid is not None and order.oid in slot_oids
        )
        tagged = bool(order.cloid and ours(order.cloid))
        if tagged and not tracked:
            orphans.append(order)
            continue
        if tracked or tagged:
            continue
        foreign.append(order)
    return tuple(foreign), tuple(orphans)


def foreign_orders(
    orders: tuple[OpenOrder, ...] | list[OpenOrder],
    coin: str,
    our_cloids: set[str],
    our_oids: set[int],
) -> tuple[OpenOrder, ...]:
    foreign, _orphans = classify_open_orders(orders, coin, our_cloids, our_oids, is_ours)
    return foreign


def evaluate_book_gates(
    *,
    now_ms: int,
    book: Book | None,
    received_ms: int | None,
    spec: MarketSpec,
    max_age_ms: int,
    mark_deviation_bps: Decimal,
    foreign: bool,
    shutdown: bool,
    equity_usd: Decimal | None,
    min_notional: Decimal,
) -> GateResult:
    if shutdown:
        return GateResult(True, "shutdown")
    if foreign:
        return GateResult(True, "foreign_order")
    if book is None or book_is_stale(now_ms, book, received_ms, max_age_ms):
        return GateResult(True, "stale_book")
    if book.best_bid is None or book.best_ask is None:
        return GateResult(True, "one_sided_book")
    if book.is_locked_or_crossed():
        return GateResult(True, "crossed_book")
    assert book.mid is not None
    if mark_deviation_bps > 0 and deviation_bps(book.mid, spec.mark_px) > mark_deviation_bps:
        return GateResult(True, "mark_deviation")
    if equity_usd is not None and equity_usd < min_notional:
        return GateResult(True, "equity_below_min_notional")
    return GateResult(False, None)
