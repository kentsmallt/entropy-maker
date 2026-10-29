"""Hard policy: one account, post-only, no self-match, no fake size.

There is no wash mode, no second account, and no order type other than ALO.
"""

from __future__ import annotations

from decimal import Decimal

from entropy_maker.models import Side

MAKER_TIF = "Alo"


class PolicyError(RuntimeError):
    pass


def assert_maker_order(
    *,
    tif: str,
    size: Decimal,
    price: Decimal,
    reduce_only: bool,
    position: Decimal,
    side: Side,
) -> None:
    if tif != MAKER_TIF:
        raise PolicyError(f"refusing tif {tif}; only {MAKER_TIF} is allowed")
    if size <= 0 or price <= 0:
        raise PolicyError("size and price must be positive")
    if reduce_only:
        _assert_reduce_only(size, position, side)


def _assert_reduce_only(size: Decimal, position: Decimal, side: Side) -> None:
    if side == "ask" and position <= 0:
        raise PolicyError("reduce-only ask requires a long position")
    if side == "bid" and position >= 0:
        raise PolicyError("reduce-only bid requires a short position")
    if size > abs(position):
        raise PolicyError("reduce-only size would be able to flip the position")


def would_take(side: Side, price: Decimal, best_bid: Decimal | None, best_ask: Decimal | None) -> bool:
    """True when a limit at ``price`` would match immediately."""
    if side == "bid":
        return best_ask is not None and price >= best_ask
    return best_bid is not None and price <= best_bid


def crosses_self(bid: Decimal | None, ask: Decimal | None) -> bool:
    if bid is None or ask is None:
        return False
    return bid >= ask
