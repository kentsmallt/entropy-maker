from __future__ import annotations

from decimal import Decimal

from entropy_maker.config import load_config
from entropy_maker.models import Book, BookLevel, MarketSpec


def make_config(tmp_path, **overrides: str):
    env = {
        "ENTROPY_STATE_FILE": str(tmp_path / "state.json"),
        "ENTROPY_ORDER_SIZE": "1",
        "ENTROPY_MAX_POSITION": "5",
        "ENTROPY_MIN_NOTIONAL_USD": "1",
        "ENTROPY_PAPER_EQUITY_USD": "10000",
        "ENTROPY_MIN_REST_SECONDS": "100",
        "ENTROPY_HALF_SPREAD_BPS": "8",
        "ENTROPY_REPRICE_BPS": "5",
        "ENTROPY_MARK_DEVIATION_BPS": "50",
    }
    env.update(overrides)
    return load_config(env)


def spec(mark: str = "100.5") -> MarketSpec:
    return MarketSpec(
        coin="io:ANTH",
        sz_decimals=2,
        max_leverage=15,
        only_isolated=True,
        is_delisted=False,
        mark_px=Decimal(mark),
    )


def book(bid: str = "100", ask: str = "101", *, time_ms: int = 1_700_000_000_000) -> Book:
    return Book(
        coin="io:ANTH",
        time_ms=time_ms,
        bids=(BookLevel(Decimal(bid), Decimal("2")),),
        asks=(BookLevel(Decimal(ask), Decimal("2")),),
    )


def cloids():
    counter = {"n": 0}

    def _next() -> str:
        counter["n"] += 1
        return "0x454d4d31" + f"{counter['n']:024x}"

    return _next
