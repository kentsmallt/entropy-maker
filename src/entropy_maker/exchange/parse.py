"""Pure parsers for account, order, and fill payloads."""

from __future__ import annotations

from decimal import Decimal

from entropy_maker.identity.cloid import validate_any
from entropy_maker.market.rounding import decimal_from
from entropy_maker.models import OpenOrder, Side

SELF_TRADE_STATUS = "selfTradeCanceled"
_GONE = {
    "filled",
    "canceled",
    "triggered",
    "rejected",
    "marginCanceled",
    "vaultWithdrawalCanceled",
    "openInterestCapCanceled",
    "reduceOnlyCanceled",
    "siblingFilledCanceled",
    "delistedCanceled",
    "liquidatedCanceled",
    "scheduledCancel",
}


class ParseError(ValueError):
    pass


def parse_cancel_response(payload: object) -> str:
    """Return ``canceled`` or ``unknown``. Does not mean the cancel should be retried."""
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return "unknown"
    try:
        statuses = payload["response"]["data"]["statuses"]
    except (KeyError, TypeError):
        return "unknown"
    if not isinstance(statuses, list) or not statuses:
        return "unknown"
    status = statuses[0]
    if status == "success":
        return "canceled"
    if isinstance(status, dict) and "error" in status:
        text = str(status["error"]).lower()
        if "already canceled" in text or "already cancelled" in text or "never placed" in text:
            return "canceled"
        return "unknown"
    return "unknown"


def parse_open_orders(payload: object) -> tuple[OpenOrder, ...]:
    if payload is None:
        return ()
    if not isinstance(payload, list):
        raise ParseError("open orders response is not a list")
    orders: list[OpenOrder] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ParseError("open order is not an object")
        parsed = _order_from_mapping(item)
        if parsed is not None:
            orders.append(parsed)
    return tuple(orders)


def parse_order_status(payload: object) -> tuple[str, OpenOrder | None]:
    """Return ``open``, ``gone``, ``self_trade``, or ``unknown``."""
    if not isinstance(payload, dict):
        return "unknown", None
    status = payload.get("status")
    if status == "unknownOid":
        return "unknown", None
    if status != "order":
        return "unknown", None
    inner = payload.get("order")
    if not isinstance(inner, dict):
        return "unknown", None
    order_status = inner.get("status")
    order = _order_from_mapping(inner.get("order") if isinstance(inner.get("order"), dict) else {})
    if order_status == "open":
        return "open", order
    if order_status == SELF_TRADE_STATUS:
        return "self_trade", order
    if isinstance(order_status, str) and (
        order_status in _GONE or order_status.endswith("Rejected") or order_status.endswith("Canceled")
    ):
        return "gone", order
    return "unknown", order


def parse_historical(payload: object) -> tuple[tuple[str, str, OpenOrder | None], ...]:
    if payload is None:
        return ()
    if not isinstance(payload, list):
        raise ParseError("historicalOrders response is not a list")
    rows: list[tuple[str, str, OpenOrder | None]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        if not isinstance(status, str):
            continue
        order_map = item.get("order") if isinstance(item.get("order"), dict) else {}
        order = _order_from_mapping(order_map)
        verdict = "open" if status == "open" else "self_trade" if status == SELF_TRADE_STATUS else "gone"
        if status not in {"open", SELF_TRADE_STATUS} and not (
            status in _GONE or status.endswith("Rejected") or status.endswith("Canceled")
        ):
            verdict = "unknown"
        cloid = order.cloid if order and order.cloid else ""
        rows.append((cloid, verdict, order))
    return tuple(rows)


def parse_position(payload: object, coin: str) -> Decimal:
    if not isinstance(payload, dict):
        raise ParseError("clearinghouseState is not an object")
    positions = payload.get("assetPositions")
    if positions is None:
        return Decimal(0)
    if not isinstance(positions, list):
        raise ParseError("assetPositions is not a list")
    for item in positions:
        if not isinstance(item, dict):
            continue
        position = item.get("position")
        if isinstance(position, dict) and position.get("coin") == coin:
            try:
                return decimal_from(position.get("szi", "0"))
            except ValueError as exc:
                raise ParseError("position szi is not a decimal") from exc
    return Decimal(0)


def parse_available_usd(payload: object) -> Decimal:
    if not isinstance(payload, dict):
        raise ParseError("clearinghouseState is not an object")
    values: list[Decimal] = []
    withdrawable = payload.get("withdrawable")
    if withdrawable is not None:
        values.append(decimal_from(withdrawable))
    margin = payload.get("marginSummary")
    if isinstance(margin, dict) and margin.get("accountValue") is not None:
        values.append(decimal_from(margin["accountValue"]))
    if not values:
        raise ParseError("no withdrawable or account value on clearinghouseState")
    return min(values)


def _order_from_mapping(item: dict) -> OpenOrder | None:
    coin = item.get("coin")
    if not isinstance(coin, str):
        return None
    side = _side(item.get("side"))
    if side is None:
        return None
    try:
        px = decimal_from(item["limitPx"] if "limitPx" in item else item["px"])
        sz = decimal_from(item["sz"])
    except (KeyError, ValueError):
        return None
    oid = item.get("oid")
    oid_int = int(oid) if isinstance(oid, int) else None
    cloid_raw = item.get("cloid")
    cloid = None
    if isinstance(cloid_raw, str) and cloid_raw:
        try:
            cloid = validate_any(cloid_raw)
        except ValueError:
            cloid = None
    return OpenOrder(coin=coin, oid=oid_int, cloid=cloid, side=side, px=px, sz=sz)


def _side(value: object) -> Side | None:
    if value in {"B", "bid", "buy"}:
        return "bid"
    if value in {"A", "ask", "sell"}:
        return "ask"
    return None
