"""Shared value types. Prices and sizes stay as Decimal strings at the edges."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal

Side = Literal["bid", "ask"]
Phase = Literal["empty", "pending_place", "resting", "pending_cancel", "uncertain"]
Mode = Literal["paper", "live"]


@dataclass(frozen=True)
class BookLevel:
    px: Decimal
    sz: Decimal


@dataclass(frozen=True)
class Book:
    coin: str
    time_ms: int
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]

    @property
    def best_bid(self) -> Decimal | None:
        return self.bids[0].px if self.bids else None

    @property
    def best_ask(self) -> Decimal | None:
        return self.asks[0].px if self.asks else None

    @property
    def mid(self) -> Decimal | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / Decimal(2)

    def is_locked_or_crossed(self) -> bool:
        if self.best_bid is None or self.best_ask is None:
            return False
        return self.best_bid >= self.best_ask


@dataclass(frozen=True)
class MarketSpec:
    coin: str
    sz_decimals: int
    max_leverage: int
    only_isolated: bool
    is_delisted: bool
    mark_px: Decimal


@dataclass(frozen=True)
class Quote:
    side: Side
    px: Decimal
    sz: Decimal


@dataclass(frozen=True)
class OpenOrder:
    coin: str
    oid: int | None
    cloid: str | None
    side: Side
    px: Decimal
    sz: Decimal


@dataclass(frozen=True)
class Action:
    kind: Literal["place", "cancel"]
    side: Side
    cloid: str
    px: Decimal | None = None
    sz: Decimal | None = None
    reduce_only: bool = False
    purpose: Literal["quote", "flatten", "risk"] = "quote"


@dataclass(frozen=True)
class Ack:
    kind: Literal["resting", "filled", "rejected", "canceled", "timeout", "unknown"]
    oid: int | None = None
    detail: str = ""


@dataclass(frozen=True)
class SideSlot:
    phase: Phase = "empty"
    cloid: str | None = None
    px: Decimal | None = None
    orig_sz: Decimal | None = None
    remaining: Decimal | None = None
    placed_ms: int | None = None
    cancel_sent: bool = False
    cancel_polls: int = 0
    oid: int | None = None
    reduce_only: bool = False
    replace_sz: Decimal | None = None

    def working(self) -> bool:
        return self.phase in {"pending_place", "resting", "pending_cancel", "uncertain"}


@dataclass(frozen=True)
class FillRecord:
    cloid: str
    side: Side
    px: str
    sz: str
    time_ms: int


@dataclass(frozen=True)
class RuntimeState:
    version: int
    coin: str
    mode: Mode
    position: Decimal
    cash: Decimal
    bid: SideSlot
    ask: SideSlot
    uncertain: tuple[str, ...]
    halted: bool
    halt_reason: str | None
    book_rx_ms: int | None
    updated_ms: int
    seen_account: bool
    read_errors: int
    book_errors: int
    recent_fills: tuple[FillRecord, ...] = ()

    def slot(self, side: Side) -> SideSlot:
        return self.bid if side == "bid" else self.ask

    def with_slot(self, side: Side, slot: SideSlot) -> RuntimeState:
        if side == "bid":
            return replace(self, bid=slot)
        return replace(self, ask=slot)

    def working_cloids(self) -> set[str]:
        found: set[str] = set()
        for slot in (self.bid, self.ask):
            if slot.cloid and slot.working():
                found.add(slot.cloid.lower())
        return found

    def working_oids(self) -> set[int]:
        found: set[int] = set()
        for slot in (self.bid, self.ask):
            if slot.oid is not None and slot.working():
                found.add(slot.oid)
        return found


def fresh_state(coin: str, mode: Mode, cash: Decimal, now_ms: int) -> RuntimeState:
    return RuntimeState(
        version=1,
        coin=coin,
        mode=mode,
        position=Decimal("0"),
        cash=cash,
        bid=SideSlot(),
        ask=SideSlot(),
        uncertain=(),
        halted=False,
        halt_reason=None,
        book_rx_ms=None,
        updated_ms=now_ms,
        seen_account=False,
        read_errors=0,
        book_errors=0,
    )


def halt(state: RuntimeState, reason: str) -> RuntimeState:
    cloids = list(state.uncertain)
    for slot in (state.bid, state.ask):
        if slot.phase == "uncertain" and slot.cloid and slot.cloid not in cloids:
            cloids.append(slot.cloid)
    return replace(state, halted=True, halt_reason=reason, uncertain=tuple(cloids))
