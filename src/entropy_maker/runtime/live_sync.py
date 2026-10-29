"""One read-only pass over the cloids this process is waiting on.

Order of reads: cloid status, open orders, historical orders, then fills.
Fills are fetched so a timeout cannot skip them. A fill by itself does not
prove the order is gone; an unproven cloid still halts.
"""

from __future__ import annotations

import time

from entropy_maker.config import Config
from entropy_maker.models import RuntimeState
from entropy_maker.runtime.reconcile import live_report_from, reconcile_live

_PENDING = {"pending_place", "pending_cancel", "uncertain"}


def sync_pending(session, state: RuntimeState, config: Config) -> RuntimeState:
    watched: set[str] = set()
    statuses: dict[str, str] = {}
    placed: list[int] = []
    for slot in (state.bid, state.ask):
        if slot.cloid and slot.phase in _PENDING:
            watched.add(slot.cloid.lower())
            verdict, _order = session.status_for(slot.cloid)
            statuses[slot.cloid.lower()] = verdict
            if slot.placed_ms is not None:
                placed.append(slot.placed_ms)
    bundle = session.read_report()
    start_ms = min(placed) if placed else int(time.time() * 1000) - 60_000
    session.fills_since(start_ms)
    report = live_report_from(bundle["open_orders"], statuses, bundle["historical"], watched)
    return reconcile_live(state, report, confirm_polls=config.reconcile_polls)
