"""Prove whether a cloid is open or gone. Ambiguity halts the process.

Read order for a live cloid: order status, open orders, historical orders.
Fills are consulted and do not, by themselves, clear an unknown cloid.
A cancel that is still open after several successful reads becomes uncertain
and is not sent again.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from entropy_maker.models import OpenOrder, RuntimeState, SideSlot, halt


@dataclass(frozen=True)
class LiveReport:
    open_orders: tuple[OpenOrder, ...]
    statuses: dict[str, str]
    historical: dict[str, str]
    self_trade: bool = False


def live_report_from(
    open_orders: tuple[OpenOrder, ...],
    statuses: dict[str, str],
    historical_rows: tuple[tuple[str, str, OpenOrder | None], ...],
    watched_cloids: set[str],
) -> LiveReport:
    historical: dict[str, str] = {}
    self_trade = any(verdict == "self_trade" for verdict in statuses.values())
    for cloid, verdict, _order in historical_rows:
        if not cloid:
            continue
        key = cloid.lower()
        historical[key] = verdict
        if key in watched_cloids and verdict == "self_trade":
            self_trade = True
    return LiveReport(
        open_orders=open_orders,
        statuses={key.lower(): value for key, value in statuses.items()},
        historical=historical,
        self_trade=self_trade,
    )


def reconcile_live(state: RuntimeState, report: LiveReport, *, confirm_polls: int) -> RuntimeState:
    if report.self_trade:
        return halt(state, "self_trade_detected")
    for side in ("bid", "ask"):
        slot = state.slot(side)
        if slot.phase not in {"pending_place", "pending_cancel", "uncertain"} or not slot.cloid:
            continue
        verdict = _verdict(slot.cloid, report)
        if verdict == "self_trade":
            return halt(state.with_slot(side, replace(slot, phase="uncertain")), "self_trade_detected")
        if verdict == "open":
            found = _find_open(slot.cloid, report.open_orders)
            if slot.phase == "pending_cancel":
                polls = slot.cancel_polls + 1
                if polls >= confirm_polls:
                    uncertain = replace(slot, phase="uncertain", cancel_polls=polls)
                    return halt(state.with_slot(side, uncertain), "cancel_unconfirmed")
                updated = _resting_from_open(slot, found, polls)
                updated = replace(updated, phase="pending_cancel", cancel_sent=True)
                state = state.with_slot(side, updated)
                continue
            state = state.with_slot(side, _resting_from_open(slot, found, 0))
            continue
        if verdict == "gone":
            state = state.with_slot(side, SideSlot(replace_sz=slot.replace_sz if slot.phase == "pending_cancel" else None))
            continue
        uncertain = replace(slot, phase="uncertain")
        return halt(state.with_slot(side, uncertain), "cloid_unproven")
    if state.halted:
        return state
    return replace(state, read_errors=0)


def normalize_paper(state: RuntimeState) -> RuntimeState:
    """Paper orders live in the state file, so an uncommitted pending place is gone."""
    if state.halted:
        return state
    for side in ("bid", "ask"):
        slot = state.slot(side)
        if slot.phase == "pending_place":
            state = state.with_slot(side, SideSlot())
        elif slot.phase == "pending_cancel":
            state = state.with_slot(side, SideSlot(replace_sz=slot.replace_sz))
        elif slot.phase == "uncertain":
            return halt(state, state.halt_reason or "uncertain")
    return state


def _verdict(cloid: str, report: LiveReport) -> str:
    key = cloid.lower()
    status = report.statuses.get(key)
    if status in {"open", "gone", "self_trade"}:
        return status
    for order in report.open_orders:
        if order.cloid and order.cloid.lower() == key:
            return "open"
    historical = report.historical.get(key)
    if historical in {"open", "gone", "self_trade"}:
        return historical
    return "unknown"


def _find_open(cloid: str, orders: tuple[OpenOrder, ...]) -> OpenOrder | None:
    for order in orders:
        if order.cloid and order.cloid.lower() == cloid.lower():
            return order
    return None


def _resting_from_open(slot: SideSlot, found: OpenOrder | None, polls: int) -> SideSlot:
    if found is None:
        return replace(slot, phase="resting", cancel_polls=polls, cancel_sent=False)
    return replace(
        slot,
        phase="resting",
        px=found.px,
        remaining=found.sz,
        oid=found.oid if found.oid is not None else slot.oid,
        cancel_polls=polls,
        cancel_sent=False,
    )
