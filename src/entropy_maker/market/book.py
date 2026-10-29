"""L2 book parsing and staleness."""

from __future__ import annotations

from decimal import Decimal

from entropy_maker.market.rounding import decimal_from
from entropy_maker.models import Book, BookLevel


class BookError(ValueError):
    pass


def parse_book(payload: object, coin: str) -> Book:
    if not isinstance(payload, dict):
        raise BookError("l2Book response is not an object")
    if payload.get("coin") not in {None, coin}:
        raise BookError(f"l2Book coin is {payload.get('coin')}, expected {coin}")
    levels = payload.get("levels")
    if not isinstance(levels, list) or len(levels) != 2:
        raise BookError("l2Book levels are missing")
    try:
        time_ms = int(payload["time"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BookError("l2Book time is missing") from exc
    return Book(
        coin=coin,
        time_ms=time_ms,
        bids=_levels(levels[0]),
        asks=_levels(levels[1]),
    )


def _levels(raw: object) -> tuple[BookLevel, ...]:
    if not isinstance(raw, list):
        raise BookError("book side is not a list")
    parsed: list[BookLevel] = []
    for item in raw:
        if not isinstance(item, dict):
            raise BookError("book level is not an object")
        try:
            px = decimal_from(item["px"])
            sz = decimal_from(item["sz"])
        except (KeyError, ValueError) as exc:
            raise BookError("book level is missing px or sz") from exc
        if px <= 0 or sz <= 0:
            continue
        parsed.append(BookLevel(px=px, sz=sz))
    return tuple(parsed)


def book_is_stale(now_ms: int, book: Book | None, received_ms: int | None, max_age_ms: int) -> bool:
    if book is None or received_ms is None:
        return True
    if now_ms - received_ms > max_age_ms:
        return True
    skew = now_ms - book.time_ms
    if abs(skew) > max_age_ms:
        return True
    return False


def deviation_bps(left: Decimal, right: Decimal) -> Decimal:
    if right == 0:
        return Decimal("999999")
    return (abs(left - right) / abs(right)) * Decimal(10000)
