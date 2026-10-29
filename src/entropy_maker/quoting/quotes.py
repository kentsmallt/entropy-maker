"""Build one bid and one ask that rest on the book.

A price that would take is stepped one tick away from the touch. If it still
would take, that side is dropped. The bid is kept strictly below the ask.
Nothing in this module emits IOC, GTC, or a second order on the same side.
"""

from __future__ import annotations

from decimal import Decimal

from entropy_maker.market.rounding import quantize_price, quantize_size, tick_size
from entropy_maker.models import Book, MarketSpec, Quote, Side
from entropy_maker.risk.inventory import clamp_skew_bps, side_allowed
from entropy_maker.risk.policy import would_take


def compute_quotes(
    *,
    book: Book,
    spec: MarketSpec,
    position: Decimal,
    bid_size: Decimal,
    ask_size: Decimal,
    max_position: Decimal,
    half_spread_bps: Decimal,
    skew_bps: Decimal,
    min_notional: Decimal,
) -> tuple[Quote | None, Quote | None]:
    if book.mid is None or book.best_bid is None or book.best_ask is None:
        return None, None
    if book.is_locked_or_crossed():
        return None, None
    skew = clamp_skew_bps(position, max_position, skew_bps, half_spread_bps)
    half = half_spread_bps / Decimal(10000)
    skew_frac = skew / Decimal(10000)
    raw_bid = book.mid * (Decimal(1) - half - skew_frac)
    raw_ask = book.mid * (Decimal(1) + half - skew_frac)
    bid = _maker_quote(
        "bid",
        raw_bid,
        bid_size,
        spec,
        book,
        position,
        max_position,
        min_notional,
    )
    ask = _maker_quote(
        "ask",
        raw_ask,
        ask_size,
        spec,
        book,
        position,
        max_position,
        min_notional,
    )
    if bid is not None and ask is not None and bid.px >= ask.px:
        bid, ask = _separate(bid, ask, spec, book)
    return bid, ask


def join_touch(side: Side, size: Decimal, spec: MarketSpec, book: Book) -> Quote | None:
    """Post at the current touch without crossing. Used for reduce-only flatten."""
    if book.best_bid is None or book.best_ask is None or book.is_locked_or_crossed():
        return None
    raw = book.best_bid if side == "bid" else book.best_ask
    try:
        px = quantize_price(raw, spec.sz_decimals, side)
        sz = quantize_size(size, spec.sz_decimals)
    except ValueError:
        return None
    if would_take(side, px, book.best_bid, book.best_ask):
        return None
    return Quote(side=side, px=px, sz=sz)


def _maker_quote(
    side: Side,
    raw: Decimal,
    size: Decimal,
    spec: MarketSpec,
    book: Book,
    position: Decimal,
    max_position: Decimal,
    min_notional: Decimal,
) -> Quote | None:
    if size <= 0 or not side_allowed(position, size, max_position, side):
        return None
    try:
        px = quantize_price(raw, spec.sz_decimals, side)
        sz = quantize_size(size, spec.sz_decimals)
    except ValueError:
        return None
    if would_take(side, px, book.best_bid, book.best_ask):
        stepped = _step_away(px, spec.sz_decimals, side)
        if stepped is None or would_take(side, stepped, book.best_bid, book.best_ask):
            return None
        px = stepped
    if sz * px < min_notional:
        return None
    return Quote(side=side, px=px, sz=sz)


def _step_away(px: Decimal, sz_decimals: int, side: Side) -> Decimal | None:
    tick = tick_size(px, sz_decimals)
    nxt = px - tick if side == "bid" else px + tick
    try:
        return quantize_price(nxt, sz_decimals, side)
    except ValueError:
        return None


def _separate(
    bid: Quote,
    ask: Quote,
    spec: MarketSpec,
    book: Book,
) -> tuple[Quote | None, Quote | None]:
    bid_px = _step_away(bid.px, spec.sz_decimals, "bid")
    ask_px = _step_away(ask.px, spec.sz_decimals, "ask")
    if bid_px is None or ask_px is None or bid_px >= ask_px:
        return None, None
    if would_take("bid", bid_px, book.best_bid, book.best_ask):
        return None, None
    if would_take("ask", ask_px, book.best_bid, book.best_ask):
        return None, None
    return Quote("bid", bid_px, bid.sz), Quote("ask", ask_px, ask.sz)
