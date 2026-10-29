"""Small JSON client for the public Hyperliquid ``/info`` endpoint."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from entropy_maker.exchange.parse import (
    parse_available_usd,
    parse_historical,
    parse_open_orders,
    parse_order_status,
    parse_position,
)
from entropy_maker.market.book import parse_book
from entropy_maker.market.meta import parse_market
from entropy_maker.models import Book, MarketSpec, OpenOrder


class InfoError(RuntimeError):
    pass


class InfoClient:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def post(self, payload: dict[str, Any]) -> Any:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/info",
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "entropy-maker/0.1"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
        except urllib.error.URLError as exc:
            raise InfoError(f"info request failed: {exc.reason if hasattr(exc, 'reason') else exc}") from exc
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise InfoError("info response was not JSON") from exc

    def market(self, coin: str) -> MarketSpec:
        dex = coin.split(":", 1)[0]
        return parse_market(self.post({"type": "metaAndAssetCtxs", "dex": dex}), coin)

    def book(self, coin: str) -> Book:
        return parse_book(self.post({"type": "l2Book", "coin": coin}), coin)

    def clearinghouse(self, user: str, dex: str) -> Any:
        return self.post({"type": "clearinghouseState", "user": user, "dex": dex})

    def position(self, user: str, dex: str, coin: str):
        return parse_position(self.clearinghouse(user, dex), coin)

    def available_usd(self, user: str, dex: str):
        return parse_available_usd(self.clearinghouse(user, dex))

    def open_orders(self, user: str, dex: str) -> tuple[OpenOrder, ...]:
        return parse_open_orders(self.post({"type": "frontendOpenOrders", "user": user, "dex": dex}))

    def order_status(self, user: str, cloid: str):
        return parse_order_status(self.post({"type": "orderStatus", "user": user, "oid": cloid}))

    def historical(self, user: str):
        return parse_historical(self.post({"type": "historicalOrders", "user": user}))

    def user_fills_since(self, user: str, start_ms: int):
        return self.post(
            {"type": "userFillsByTime", "user": user, "startTime": start_ms, "aggregateByTime": False}
        )

    def extra_agents(self, user: str) -> Any:
        return self.post({"type": "extraAgents", "user": user})

    def user_dex_abstraction(self, user: str) -> Any:
        return self.post({"type": "userDexAbstraction", "user": user})

    def user_abstraction(self, user: str) -> Any:
        return self.post({"type": "userAbstraction", "user": user})
