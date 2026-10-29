"""Reviewed Entropy HIP-3 coins.

Adding a coin is a code change. Delisted names are intentionally absent.
The public `meta` response is still checked at runtime, so a coin that was
later delisted is refused even if it remains in this set.
"""

from __future__ import annotations

# Non-delisted `io:` markets observed on the public info API on 2026-10-29.
REVIEWED_IO_COINS: frozenset[str] = frozenset(
    {
        "io:ANTH",
        "io:DRAM",
        "io:EWY",
        "io:GPRO",
        "io:IONQ",
        "io:NBIS",
        "io:NVDA",
        "io:OAI",
        "io:PRL",
        "io:SNDK",
        "io:TCNT",
        "io:XIAOMI",
    }
)

ENTROPY_DEX = "io"


class MarketError(ValueError):
    pass


def dex_of(coin: str) -> str:
    if ":" not in coin:
        raise MarketError(f"{coin} is not an Entropy HIP-3 coin (expected io:NAME)")
    return coin.split(":", 1)[0]


def assert_allowed(coin: str) -> str:
    dex = dex_of(coin)
    if dex != ENTROPY_DEX:
        raise MarketError(f"{coin} is not on the Entropy dex")
    if coin not in REVIEWED_IO_COINS:
        raise MarketError(
            f"{coin} is not in the reviewed allowlist. "
            "Refusing to trade an unreviewed market."
        )
    return coin
