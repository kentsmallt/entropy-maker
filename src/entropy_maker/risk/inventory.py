"""Inventory limits. Near the cap, only the reducing side stays up."""

from __future__ import annotations

from decimal import Decimal

from entropy_maker.models import Side


def side_allowed(position: Decimal, order_size: Decimal, max_position: Decimal, side: Side) -> bool:
    if order_size <= 0 or max_position <= 0:
        return False
    if side == "bid":
        return position + order_size <= max_position
    return position - order_size >= -max_position


def clamp_skew_bps(position: Decimal, max_position: Decimal, skew_bps: Decimal, half_spread_bps: Decimal) -> Decimal:
    if max_position <= 0:
        return Decimal(0)
    raw = (position / max_position) * skew_bps
    limit = half_spread_bps * Decimal("0.9")
    if raw > limit:
        return limit
    if raw < -limit:
        return -limit
    return raw
