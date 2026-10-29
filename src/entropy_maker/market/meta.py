"""Parse ``metaAndAssetCtxs`` and refuse markets this toolkit will not quote."""

from __future__ import annotations

from decimal import Decimal

from entropy_maker.market.allowlist import MarketError, assert_allowed
from entropy_maker.market.rounding import decimal_from
from entropy_maker.models import MarketSpec


def parse_market(payload: object, coin: str) -> MarketSpec:
    assert_allowed(coin)
    if not isinstance(payload, list) or len(payload) < 2:
        raise MarketError("metaAndAssetCtxs response is not [meta, contexts]")
    meta, contexts = payload[0], payload[1]
    if not isinstance(meta, dict) or not isinstance(contexts, list):
        raise MarketError("metaAndAssetCtxs shape is unexpected")
    universe = meta.get("universe")
    if not isinstance(universe, list):
        raise MarketError("meta.universe is missing")
    found = None
    ctx = None
    for index, asset in enumerate(universe):
        if isinstance(asset, dict) and asset.get("name") == coin:
            found = asset
            ctx = contexts[index] if index < len(contexts) else None
            break
    if found is None or not isinstance(ctx, dict):
        raise MarketError(f"{coin} is not in the live {coin.split(':', 1)[0]} universe")
    if found.get("isDelisted") is True:
        raise MarketError(f"{coin} is delisted")
    if found.get("onlyIsolated") is not True:
        raise MarketError(f"{coin} is not isolated-only; refusing to quote it")
    try:
        sz_decimals = int(found["szDecimals"])
        max_leverage = int(found["maxLeverage"])
        mark = decimal_from(ctx.get("markPx"))
    except (KeyError, TypeError, ValueError) as exc:
        raise MarketError(f"{coin} metadata is incomplete") from exc
    if mark <= 0:
        raise MarketError(f"{coin} mark price is not positive")
    if max_leverage < 1:
        raise MarketError(f"{coin} maxLeverage is not usable")
    if not 0 <= sz_decimals <= 6:
        raise MarketError(f"{coin} szDecimals {sz_decimals} is outside 0..6")
    return MarketSpec(
        coin=coin,
        sz_decimals=sz_decimals,
        max_leverage=max_leverage,
        only_isolated=True,
        is_delisted=False,
        mark_px=mark,
    )


def notional(size: Decimal, price: Decimal) -> Decimal:
    return size * price
