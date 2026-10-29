"""Command line. The default path is paper and does not load a signing key."""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from pathlib import Path

from entropy_maker.config import Config, ConfigError, load_config, load_env_file, require_quote_sizes
from entropy_maker.exchange.info_client import InfoClient, InfoError
from entropy_maker.logging_setup import setup_logging
from entropy_maker.market.rounding import quantize_size
from entropy_maker.ops.preflight import run_preflight
from entropy_maker.ops.watchdog import cancel_ours, describe
from entropy_maker.runtime.engine import EngineError, load_or_fresh, run_loop
from entropy_maker.runtime.paper import paper_ack
from entropy_maker.runtime.state_store import StateError, load_state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="entropy-maker",
        description="Post-only Entropy/Hyperliquid maker. Paper mode is the default.",
    )
    parser.add_argument("--env-file", type=Path, help="optional KEY=VALUE file; existing env wins")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight", help="read the public book and market rules; send nothing")
    sub.add_parser("once", help="one paper or live quote cycle")
    sub.add_parser("run", help="quote until SIGINT or SIGTERM")
    sub.add_parser("flatten", help="reduce-only post-only order for the current position")
    sub.add_parser("status", help="print the state file")
    sub.add_parser("reconcile", help="adopt proven order state; do not place")
    sub.add_parser("cancel-ours", help="cancel tagged orders once, or clear the paper state file")
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
    try:
        if args.command == "preflight":
            return run_preflight(config)
        if args.command == "status":
            return _status(config)
        if args.command == "cancel-ours":
            return cancel_ours(config, _now_ms())
        if args.command == "reconcile":
            return _reconcile(config)
        if args.command == "once":
            return _run(config, cycles=1, flatten=False)
        if args.command == "run":
            return _run(config, cycles=None, flatten=False)
        if args.command == "flatten":
            return _run(config, cycles=4, flatten=True)
    except (ConfigError, StateError, EngineError, InfoError) as exc:
        print(exc, file=sys.stderr)
        return 2
    parser.error(f"unknown command {args.command}")
    return 2


def _run(config: Config, *, cycles: int | None, flatten: bool) -> int:
    _require_ready(config)
    info = InfoClient(config.info_url)
    spec = info.market(config.coin)
    assert config.order_size is not None
    size = quantize_size(config.order_size, spec.sz_decimals)
    if size * spec.mark_px < config.min_notional_usd:
        raise ConfigError("order notional at the mark is below ENTROPY_MIN_NOTIONAL_USD")
    stop = threading.Event()

    def _handle(signum, _frame) -> None:
        stop.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)
    snapshot = _snapshot(config, info)
    session = None
    if config.mode == "live":
        from entropy_maker.exchange.live import LiveSession

        session = LiveSession.open(config)

    def send(action, book, state):
        if session is None:
            return paper_ack(action, book, state.position)
        return session.send(action)

    def reconcile(state):
        from entropy_maker.runtime.live_sync import sync_pending

        return sync_pending(session, state, config)

    def account():
        return session.read_account()

    def opens():
        return session.read_report()["open_orders"]

    state = run_loop(
        config,
        spec,
        cycles=cycles,
        snapshot=snapshot,
        send=send,
        now_ms=_now_ms,
        sleep=lambda seconds: _sleep(seconds, stop),
        stop=stop.is_set,
        reconcile=reconcile if session is not None else None,
        account=account if session is not None else None,
        open_orders=opens if session is not None else None,
        flatten=flatten,
    )
    print(describe(state))
    return 2 if state.halted else 0


def _require_ready(config: Config) -> None:
    require_quote_sizes(config)


def _snapshot(config: Config, info: InfoClient):
    if config.book_source == "rest":

        def snapshot():
            return info.book(config.coin)

        return snapshot
    from entropy_maker.exchange.ws_book import WsBookFeed

    feed = WsBookFeed(config.info_url, config.coin)
    return feed.snapshot


def _status(config: Config) -> int:
    if not config.state_file.exists():
        print(f"no state file at {config.state_file}")
        return 0
    state = load_state(config.state_file)
    print(describe(state))
    return 2 if state.halted else 0


def _reconcile(config: Config) -> int:
    if not config.state_file.exists():
        print(f"no state file at {config.state_file}")
        return 0
    state = load_or_fresh(config, _now_ms())
    if config.mode != "live":
        from entropy_maker.runtime.state_store import save_state

        save_state(config.state_file, state)
        print(describe(state))
        return 2 if state.halted else 0
    if config.mode == "live":
        from entropy_maker.exchange.live import LiveSession
        from entropy_maker.runtime.live_sync import sync_pending

        session = LiveSession.open(config)
        position, available = session.read_account()
        state = sync_pending(session, state, config)
        from dataclasses import replace

        state = replace(state, position=position, cash=available, seen_account=True, updated_ms=_now_ms())
        from entropy_maker.runtime.state_store import save_state

        save_state(config.state_file, state)
    print(describe(state))
    if state.halted:
        print("halt remains until a later reconcile can prove every watched cloid", file=sys.stderr)
        return 2
    return 0


def _sleep(seconds: float, stop: threading.Event) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if stop.is_set():
            return
        time.sleep(min(0.2, deadline - time.monotonic()))


def _now_ms() -> int:
    return int(time.time() * 1000)


if __name__ == "__main__":
    raise SystemExit(main())
