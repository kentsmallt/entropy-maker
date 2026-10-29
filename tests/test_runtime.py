from dataclasses import replace
from decimal import Decimal

from entropy_maker.models import Ack, OpenOrder, fresh_state
from entropy_maker.ops.watchdog import cancel_ours
from entropy_maker.runtime.apply import apply_result
from entropy_maker.runtime.engine import run_loop
from entropy_maker.runtime.flatten import plan_flatten
from entropy_maker.runtime.paper import apply_paper_fills, paper_ack
from entropy_maker.runtime.planner import View, propose
from entropy_maker.runtime.reconcile import LiveReport, reconcile_live
from entropy_maker.runtime.state_store import load_state, save_state
from tests.conftest import book, cloids, make_config, spec

NOW = 1_700_000_000_000


def _view(snapshot, *, now_ms=NOW, foreign=False, shutdown=False, equity=Decimal("10000")):
    return View(
        now_ms=now_ms,
        book=snapshot,
        book_rx_ms=now_ms,
        spec=spec(),
        foreign=foreign,
        shutdown=shutdown,
        equity_usd=equity,
    )


def test_planner_places_once_and_respects_min_rest(tmp_path):
    config = make_config(tmp_path)
    state = fresh_state("io:ANTH", "paper", Decimal("10000"), NOW)
    state, actions = propose(state, _view(book(time_ms=NOW)), config, cloids())
    assert [action.kind for action in actions] == ["place", "place"]
    assert {action.side for action in actions} == {"bid", "ask"}
    assert all(action.reduce_only is False for action in actions)
    for action in actions:
        state = apply_result(state, action, Ack("resting"), confirm_polls=5)
    assert state.bid.phase == "resting"
    assert state.ask.phase == "resting"
    assert state.bid.px < state.ask.px

    later = _view(book(bid="100.2", ask="101.2", time_ms=NOW + 1000), now_ms=NOW + 1000)
    state, again = propose(state, later, config, cloids())
    assert again == []


def test_partial_replace_uses_remaining_size(tmp_path):
    config = make_config(tmp_path, ENTROPY_MIN_REST_SECONDS="0", ENTROPY_REPRICE_BPS="1")
    state = fresh_state("io:ANTH", "paper", Decimal("10000"), NOW)
    state, actions = propose(state, _view(book(time_ms=NOW)), config, cloids())
    for action in actions:
        state = apply_result(state, action, Ack("resting"), confirm_polls=5)
    state = state.with_slot("bid", replace(state.bid, remaining=Decimal("0.40")))
    moved = _view(book(bid="100.4", ask="101.4", time_ms=NOW + 5000), now_ms=NOW + 5000)
    state, cancels = propose(state, moved, config, cloids())
    bid_cancel = next(action for action in cancels if action.side == "bid")
    assert bid_cancel.kind == "cancel"
    state = apply_result(state, bid_cancel, Ack("canceled"), confirm_polls=5)
    assert state.bid.phase == "empty"
    assert state.bid.replace_sz == Decimal("0.40")
    state, places = propose(state, moved, config, cloids())
    bid_place = next(action for action in places if action.side == "bid")
    assert bid_place.sz == Decimal("0.40")


def test_foreign_order_pulls_quotes_and_places_nothing(tmp_path):
    config = make_config(tmp_path, ENTROPY_MIN_REST_SECONDS="0")
    state = fresh_state("io:ANTH", "paper", Decimal("10000"), NOW)
    state, actions = propose(state, _view(book(time_ms=NOW)), config, cloids())
    for action in actions:
        state = apply_result(state, action, Ack("resting"), confirm_polls=5)
    state, pulled = propose(state, _view(book(time_ms=NOW), foreign=True), config, cloids())
    assert pulled
    assert all(action.kind == "cancel" and action.purpose == "risk" for action in pulled)


def test_stale_book_does_not_place(tmp_path):
    config = make_config(tmp_path, ENTROPY_MAX_BOOK_AGE_SECONDS="3")
    state = fresh_state("io:ANTH", "paper", Decimal("10000"), NOW)
    stale = book(time_ms=NOW - 60_000)
    state, actions = propose(state, _view(stale), config, cloids())
    assert actions == []


def test_paper_fill_only_when_the_market_trades_through(tmp_path):
    config = make_config(tmp_path)
    state = fresh_state("io:ANTH", "paper", Decimal("10000"), NOW)
    state, actions = propose(state, _view(book(time_ms=NOW)), config, cloids())
    for action in actions:
        state = apply_result(state, action, Ack("resting", oid=7), confirm_polls=5)
    bid_px = state.bid.px
    untouched = apply_paper_fills(state, book(bid="100", ask="101", time_ms=NOW), NOW)
    assert untouched.position == 0
    assert untouched.bid.phase == "resting"
    traded = book(bid="99", ask=str(bid_px), time_ms=NOW)
    filled = apply_paper_fills(state, traded, NOW)
    assert filled.position == state.bid.remaining
    assert filled.bid.phase == "empty"
    assert filled.recent_fills[0].side == "bid"
    rejected = paper_ack(actions[0], book(time_ms=NOW), Decimal("0"))
    # The original bid was inside the spread, so it would rest.
    assert rejected.kind == "resting"


def test_timeout_is_not_retried_and_unknown_cloid_halts():
    state = fresh_state("io:ANTH", "paper", Decimal("10000"), NOW)
    from entropy_maker.models import Action

    place = Action("place", "bid", "0x454d4d31" + "0" * 24, Decimal("100"), Decimal("1"))
    state = state.with_slot(
        "bid",
        state.bid.__class__(phase="pending_place", cloid=place.cloid, px=place.px, remaining=place.sz, orig_sz=place.sz),
    )
    waiting = apply_result(state, place, Ack("timeout"), confirm_polls=5)
    assert waiting.bid.phase == "pending_place"
    assert waiting.halted is False
    report = LiveReport(open_orders=(), statuses={place.cloid: "unknown"}, historical={})
    halted = reconcile_live(waiting, report, confirm_polls=5)
    assert halted.halted is True
    assert halted.halt_reason == "cloid_unproven"

    proven = LiveReport(
        open_orders=(OpenOrder("io:ANTH", 9, place.cloid, "bid", Decimal("100"), Decimal("1")),),
        statuses={place.cloid: "open"},
        historical={},
    )
    resting = reconcile_live(waiting, proven, confirm_polls=5)
    assert resting.halted is False
    assert resting.bid.phase == "resting"
    assert resting.bid.oid == 9


def test_reconcile_reads_fills_before_it_will_halt(tmp_path):
    from entropy_maker.models import SideSlot
    from entropy_maker.runtime.live_sync import sync_pending

    cloid = "0x454d4d31" + "a" * 24
    slot = SideSlot(phase="pending_place", cloid=cloid, px=Decimal("1"), remaining=Decimal("1"), placed_ms=NOW)
    state = fresh_state("io:ANTH", "paper", Decimal("1"), NOW).with_slot("bid", slot)

    class FakeSession:
        def __init__(self):
            self.calls: list[str] = []

        def status_for(self, _cloid):
            self.calls.append("status")
            return "unknown", None

        def read_report(self):
            self.calls.append("open-and-history")
            return {"open_orders": (), "historical": ()}

        def fills_since(self, _start_ms):
            self.calls.append("fills")
            return []

    session = FakeSession()
    halted = sync_pending(session, state, make_config(tmp_path))
    assert session.calls == ["status", "open-and-history", "fills"]
    assert halted.halted is True
    assert halted.halt_reason == "cloid_unproven"


def test_unconfirmed_cancel_is_not_sent_again():
    from entropy_maker.models import SideSlot

    cloid = "0x454d4d31" + "1" * 24
    slot = SideSlot(phase="pending_cancel", cloid=cloid, px=Decimal("100"), remaining=Decimal("1"), cancel_sent=True)
    state = fresh_state("io:ANTH", "paper", Decimal("10000"), NOW).with_slot("bid", slot)
    report = LiveReport(
        open_orders=(OpenOrder("io:ANTH", 3, cloid, "bid", Decimal("100"), Decimal("1")),),
        statuses={cloid: "open"},
        historical={},
    )
    once = reconcile_live(state, report, confirm_polls=2)
    assert once.halted is False
    assert once.bid.cancel_sent is True
    twice = reconcile_live(once, report, confirm_polls=2)
    assert twice.halted is True
    assert twice.halt_reason == "cancel_unconfirmed"


def test_flatten_is_reduce_only_and_cannot_flip(tmp_path):
    config = make_config(tmp_path)
    state = fresh_state("io:ANTH", "paper", Decimal("10000"), NOW)
    state = replace(state, position=Decimal("2"))
    resting_quote = state.bid.__class__(
        phase="resting",
        cloid="0x454d4d31" + "2" * 24,
        px=Decimal("100"),
        remaining=Decimal("1"),
        orig_sz=Decimal("1"),
    )
    state = state.with_slot("bid", resting_quote)
    view = _view(book(time_ms=NOW))
    state, cancels = plan_flatten(state, view, config, cloids())
    assert len(cancels) == 1
    assert cancels[0].kind == "cancel"
    state = apply_result(state, cancels[0], Ack("canceled"), confirm_polls=5)
    state, places = plan_flatten(state, view, config, cloids())
    assert len(places) == 1
    assert places[0].reduce_only is True
    assert places[0].side == "ask"
    assert places[0].sz == Decimal("2")
    assert places[0].sz <= abs(state.position)


def test_state_roundtrip_and_paper_loop(tmp_path):
    config = make_config(tmp_path, ENTROPY_MIN_REST_SECONDS="100")
    sent = []

    def snapshot():
        return book(time_ms=NOW)

    def send(action, _book, _state):
        sent.append(action)
        return Ack("resting", oid=len(sent))

    state = run_loop(
        config,
        spec(),
        cycles=2,
        snapshot=snapshot,
        send=send,
        now_ms=lambda: NOW,
        sleep=lambda _seconds: None,
        stop=lambda: False,
    )
    assert state.mode == "paper"
    assert state.bid.phase == "resting"
    assert state.ask.phase == "resting"
    assert len(sent) == 2
    loaded = load_state(config.state_file)
    assert loaded.bid.cloid == state.bid.cloid
    save_state(config.state_file, loaded)
    assert cancel_ours(config, NOW) == 0
    cleared = load_state(config.state_file)
    assert cleared.bid.phase == "empty"
    assert cleared.ask.phase == "empty"
    assert cleared.halted is False


def test_shutdown_cancels_without_a_new_order(tmp_path):
    config = make_config(tmp_path)
    calls = {"n": 0}

    def snapshot():
        calls["n"] += 1
        return book(time_ms=NOW)

    def send(action, _book, _state):
        return Ack("resting" if action.kind == "place" else "canceled")

    flags = {"stop": False}

    def stop():
        return flags["stop"]

    def sleep(_seconds):
        flags["stop"] = True

    state = run_loop(
        config,
        spec(),
        cycles=None,
        snapshot=snapshot,
        send=send,
        now_ms=lambda: NOW,
        sleep=sleep,
        stop=stop,
    )
    assert state.bid.phase == "empty"
    assert state.ask.phase == "empty"
    assert state.halted is False
