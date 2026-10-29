"""Cancel this process's tagged orders once. Foreign orders are left alone.

Paper mode only edits the local state file. Live mode talks to the exchange
and still sends each cancel a single time.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import replace
from pathlib import Path

from entropy_maker.config import Config, ConfigError, load_config, load_env_file
from entropy_maker.identity.cloid import is_ours
from entropy_maker.logging_setup import setup_logging
from entropy_maker.models import Action, SideSlot, halt
from entropy_maker.runtime.engine import load_or_fresh
from entropy_maker.runtime.state_store import save_state

log = logging.getLogger("entropy_maker")


def cancel_ours(config: Config, now_ms: int) -> int:
    if not config.state_file.exists() and config.mode == "paper":
        log.info("no paper state file; nothing to cancel")
        return 0
    try:
        state = load_or_fresh(config, now_ms)
    except Exception as exc:
        log.error("%s", exc)
        return 2
    if config.mode == "paper":
        previous = state.halt_reason
        cleared = replace(
            state,
            bid=SideSlot(),
            ask=SideSlot(),
            uncertain=(),
            halted=False,
            halt_reason=None,
            updated_ms=now_ms,
        )
        save_state(config.state_file, cleared)
        log.info("cleared paper orders (previous halt: %s). No exchange request was sent.", previous)
        return 0
    return _cancel_live(config, state, now_ms)


def _cancel_live(config: Config, state, now_ms: int) -> int:
    if state.mode != "live":
        log.error("refusing to send cancels: state file is not a live session")
        return 2
    try:
        from entropy_maker.exchange.live import LiveSession

        session = LiveSession.open(config)
        orders = session.read_report()["open_orders"]
    except Exception as exc:
        log.error("not canceling: %s", exc)
        return 2
    tagged = [
        order
        for order in orders
        if order.coin == config.coin and order.cloid and is_ours(order.cloid)
    ]
    if not tagged and not state.bid.working() and not state.ask.working():
        log.info("no tagged orders on %s", config.coin)
        save_state(config.state_file, replace(state, updated_ms=now_ms))
        return 0
    if not tagged and (state.bid.working() or state.ask.working()):
        log.error("state has working orders but the exchange listed none; not clearing and not retrying")
        save_state(config.state_file, replace(halt(state, "cancel_unconfirmed"), updated_ms=now_ms))
        return 2
    sent: set[str] = set()
    for order in tagged:
        assert order.cloid is not None
        if order.cloid.lower() in sent:
            continue
        sent.add(order.cloid.lower())
        action = Action(kind="cancel", side=order.side, cloid=order.cloid, purpose="risk")
        ack = session.send(action)
        log.info("cancel %s -> %s", order.cloid, ack.kind)
        if ack.kind != "canceled":
            state = halt(state, "cancel_unconfirmed")
            save_state(config.state_file, replace(state, updated_ms=now_ms))
            log.error("cancel was not confirmed; not retrying")
            return 2
    unseen = [
        slot.cloid
        for slot in (state.bid, state.ask)
        if slot.working() and slot.cloid and slot.cloid.lower() not in sent
    ]
    if unseen:
        log.error("working cloids were not on the open-order read; not clearing")
        save_state(config.state_file, replace(halt(state, "cancel_unconfirmed"), updated_ms=now_ms))
        return 2
    cleared = replace(
        state,
        bid=SideSlot(),
        ask=SideSlot(),
        uncertain=(),
        halted=False,
        halt_reason=None,
        updated_ms=now_ms,
    )
    save_state(config.state_file, cleared)
    log.info("canceled %s tagged order(s)", len(sent))
    return 0


def describe(state) -> str:
    lines = [
        f"mode={state.mode} coin={state.coin} halted={state.halted} reason={state.halt_reason}",
        f"position={state.position} cash={state.cash} uncertain={len(state.uncertain)}",
        f"bid={_slot(state.bid)}",
        f"ask={_slot(state.ask)}",
    ]
    return "\n".join(lines)


def _slot(slot: SideSlot) -> str:
    if slot.phase == "empty":
        return "empty"
    return (
        f"{slot.phase} cloid={slot.cloid} px={slot.px} remaining={slot.remaining} "
        f"reduce_only={slot.reduce_only}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cancel entropy-maker orders once")
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args(argv)
    if args.env_file:
        try:
            load_env_file(args.env_file)
        except ConfigError as exc:
            print(exc, file=sys.stderr)
            return 2
    try:
        config = load_config()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    setup_logging(config.log_level)
    return cancel_ours(config, int(time.time() * 1000))


if __name__ == "__main__":
    raise SystemExit(main())
