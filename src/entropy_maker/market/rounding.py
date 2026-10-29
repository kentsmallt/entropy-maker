"""Hyperliquid perp price and size ticks.

Perps accept at most 5 significant figures, and at most ``6 - szDecimals``
decimal places. Integer prices are always legal. Bids are rounded down and
asks up so a tick step cannot turn a maker price into a taker price.
Sizes are rounded down so a quote cannot grow past the configured size.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Literal

Side = Literal["bid", "ask"]

PERP_MAX_DECIMALS = 6


def decimal_places_for_price(px: Decimal, sz_decimals: int) -> int:
    if px <= 0:
        raise ValueError("price must be positive")
    if not 0 <= sz_decimals <= PERP_MAX_DECIMALS:
        raise ValueError(f"szDecimals {sz_decimals} is outside 0..{PERP_MAX_DECIMALS}")
    max_decimals = PERP_MAX_DECIMALS - sz_decimals
    # 5 significant figures. ``adjusted()`` is the exponent of the leading digit.
    sig_decimals = 4 - int(px.adjusted())
    return max(0, min(max_decimals, sig_decimals))


def _quantum(places: int) -> Decimal:
    if places <= 0:
        return Decimal(1)
    return Decimal(1).scaleb(-places)


def quantize_price(px: Decimal, sz_decimals: int, side: Side) -> Decimal:
    if px <= 0:
        raise ValueError("price must be positive")
    places = decimal_places_for_price(px, sz_decimals)
    rounding = ROUND_DOWN if side == "bid" else ROUND_UP
    rounded = px.quantize(_quantum(places), rounding=rounding)
    if rounded <= 0:
        raise ValueError("rounded price is not positive")
    places_after = decimal_places_for_price(rounded, sz_decimals)
    if places_after != places:
        rounded = rounded.quantize(_quantum(places_after), rounding=rounding)
    if rounded <= 0:
        raise ValueError("rounded price is not positive")
    return rounded


def quantize_size(sz: Decimal, sz_decimals: int) -> Decimal:
    if sz <= 0:
        raise ValueError("size must be positive")
    if not 0 <= sz_decimals <= PERP_MAX_DECIMALS:
        raise ValueError(f"szDecimals {sz_decimals} is outside 0..{PERP_MAX_DECIMALS}")
    quantum = Decimal(1) if sz_decimals == 0 else Decimal(1).scaleb(-sz_decimals)
    rounded = sz.quantize(quantum, rounding=ROUND_DOWN)
    if rounded <= 0:
        raise ValueError("size rounds to zero")
    return rounded


def tick_size(px: Decimal, sz_decimals: int) -> Decimal:
    return _quantum(decimal_places_for_price(px, sz_decimals))


def format_decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def decimal_from(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or value is None:
        raise ValueError("missing decimal")
    return Decimal(str(value))
