"""One quote cycle: read, reconcile, plan, send each action at most once."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal

from entropy_maker.config import Config
from entropy_maker.identity.cloid import is_ours
from entropy_maker.models import Ack, Action, Book, MarketSpec, OpenOrder, RuntimeState, fresh_state, halt
from entropy_maker.risk.gates import classify_open_orders
from entropy_maker.runtime.apply import apply_result
from entropy_maker.runtime.flatten import plan_flatten
from entropy_maker.runtime.paper import apply_paper_fills, paper_equity
from entropy_maker.runtime.planner import View, propose
from entropy_maker.runtime.reconcile import normalize_paper
from entropy_maker.runtime.state_store import StateError, load_state, save_state

log = logging.getLogger("entropy_maker")

Send = Callable[[Action, Book | None, RuntimeState], Ack]
Snapshot = Callable[[], Book]
Reconcile = Callable[[RuntimeState], RuntimeState]
Account = Callable[[], tuple[Decimal, Decimal]]
OpenOrders = Callable[[], tuple[OpenOrder, ...]]


class EngineError(RuntimeError):
    pass


def load_or_fresh(config: Config, now_ms: int) -> RuntimeState:
    path = config.state_file
    if path.exists():
        state = load_state(path)
        if state.coin != config.coin:
            raise StateError(
                f"state file coin is {state.coin} but ENTROPY_COIN is {config.coin}; "
                "refusing to mix markets"
            )
        if state.mode != config.mode:
            raise StateError(
                f"state file mode is {state.mode} but this process is {config.mode}; "
                "refusing to mix paper and live state"
            )
        if config.mode == "paper":
            state = normalize_paper(state)
        return state
    cash = config.paper_equity_usd if config.mode == "paper" else Decimal("0")
    return fresh_state(config.coin, config.mode, cash, now_ms)  # type: ignore[arg-type]


def run_loop(
    config: Config,
    spec: MarketSpec,
    *,
    cycles: int | None,
    snapshot: Snapshot,
    send: Send,
    now_ms: Callable[[], int],
    sleep: Callable[[float], None],
    stop: Callable[[], bool],
    reconcile: Reconcile | None = None,
    account: Account | None = None,
    open_orders: OpenOrders | None = None,
    flatten: bool = False,
    new_cloid: Callable[[], str] | None = None,
) -> RuntimeState:
    if new_cloid is None:
        from entropy_maker.identity.cloid import new_cloid as _new

        new_cloid = _new
    state = load_or_fresh(config, now_ms())
    save_state(config.state_file, state)
    if state.halted:
        log.error("halted before start: %s", state.halt_reason)
        return state
    completed = 0
    while cycles is None or completed < cycles:
        state, finished = _cycle(
            state,
            config,
            spec,
            snapshot=snapshot,
            send=send,
            now_ms=now_ms,
            stop=stop,
            reconcile=reconcile,
            account=account,
            open_orders=open_orders,
            flatten=flatten,
            new_cloid=new_cloid,
        )
        save_state(config.state_file, state)
        completed += 1
        if finished or state.halted:
            break
        if cycles is not None and completed >= cycles:
            break
        sleep(config.refresh_seconds_float)
    _log_state(state)
    return state


def _cycle(
    state: RuntimeState,
    config: Config,
    spec: MarketSpec,
    *,
    snapshot: Snapshot,
    send: Send,
    now_ms: Callable[[], int],
    stop: Callable[[], bool],
    reconcile: Reconcile | None,
    account: Account | None,
    open_orders: OpenOrders | None,
    flatten: bool,
    new_cloid: Callable[[], str],
) -> tuple[RuntimeState, bool]:
    stamp = now_ms()
    book: Book | None = None
    try:
        book = snapshot()
        state = replace(state, book_errors=0, book_rx_ms=stamp)
    except Exception:
        state = replace(state, book_errors=state.book_errors + 1, book_rx_ms=None)
        log.warning("book read failed (%s/%s)", state.book_errors, config.book_error_limit)

    if state.mode == "paper" and book is not None and not state.halted:
        state = apply_paper_fills(state, book, stamp)
        if state.halted:
            return state, True

    foreign = False
    equity: Decimal | None
    if state.mode == "live":
        try:
            if account is None or reconcile is None or open_orders is None:
                raise EngineError("live mode is missing account readers")
            position, available = account()
            state = _note_position(state, position, config.order_size or Decimal(0))
            if state.halted:
                return state, True
            state = reconcile(state)
            if state.halted:
                return state, True
            orders = open_orders()
            foreign_list, orphans = classify_open_orders(
                orders,
                config.coin,
                state.working_cloids(),
                state.working_oids(),
                is_ours,
            )
            if orphans:
                return halt(state, "orphan_tagged_order"), True
            foreign = bool(foreign_list)
            equity = available
            state = replace(state, read_errors=0)
        except EngineError:
            raise
        except Exception:
            state = replace(state, read_errors=state.read_errors + 1)
            log.warning("account read failed (%s); no orders sent", state.read_errors)
            if state.read_errors >= config.book_error_limit:
                return halt(state, "read_failed"), True
            return state, False
    else:
        equity = paper_equity(state, spec.mark_px, book)

    shutdown = stop() or state.book_errors >= config.book_error_limit
    view = View(
        now_ms=stamp,
        book=book,
        book_rx_ms=state.book_rx_ms,
        spec=spec,
        foreign=foreign,
        shutdown=shutdown,
        equity_usd=equity,
    )
    if flatten:
        state, actions = plan_flatten(state, view, config, new_cloid)
    else:
        state, actions = propose(state, view, config, new_cloid)
    for action in actions:
        ack = send(action, book, state)
        log.info(
            "%s %s %s px=%s sz=%s reduce_only=%s -> %s",
            state.mode,
            action.kind,
            action.side,
            action.px,
            action.sz,
            action.reduce_only,
            ack.kind,
        )
        state = apply_result(state, action, ack, confirm_polls=config.reconcile_polls)
        if state.halted:
            return state, True
    if state.book_errors >= config.book_error_limit and not _working(state):
        return halt(state, "book_unavailable"), True
    if stop() and not actions and not _working(state):
        return state, True
    if flatten and not actions and state.position == 0 and not _working(state):
        return state, True
    return state, False


def _note_position(state: RuntimeState, position: Decimal, order_size: Decimal) -> RuntimeState:
    if state.seen_account and abs(position - state.position) > order_size:
        return halt(replace(state, position=position, seen_account=True), "position_jump")
    return replace(state, position=position, seen_account=True)


def _working(state: RuntimeState) -> bool:
    return state.bid.working() or state.ask.working()


def _log_state(state: RuntimeState) -> None:
    if state.halted:
        log.error("halted: %s", state.halt_reason)
        return
    log.info(
        "mode=%s coin=%s position=%s bid=%s ask=%s",
        state.mode,
        state.coin,
        state.position,
        _slot_text(state.bid),
        _slot_text(state.ask),
    )


def _slot_text(slot) -> str:
    if slot.phase == "empty":
        return "empty"
    return f"{slot.phase} {slot.px} x {slot.remaining}"
