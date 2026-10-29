from decimal import Decimal
from pathlib import Path

import pytest

from entropy_maker.config import ConfigError, load_config, load_env_file
from entropy_maker.exchange.parse import parse_cancel_response, parse_order_status
from entropy_maker.identity.agent import AgentError, AgentRecord, require_agent
from entropy_maker.identity.cloid import is_ours, new_cloid
from entropy_maker.identity.secrets import SecretError, load_secret_file
from entropy_maker.models import OpenOrder
from entropy_maker.redact import redact
from entropy_maker.risk.gates import classify_open_orders
from entropy_maker.risk.policy import PolicyError, assert_maker_order, crosses_self


def test_only_alo_orders_are_accepted():
    with pytest.raises(PolicyError):
        assert_maker_order(
            tif="Ioc",
            size=Decimal("1"),
            price=Decimal("10"),
            reduce_only=False,
            position=Decimal("0"),
            side="bid",
        )
    with pytest.raises(PolicyError):
        assert_maker_order(
            tif="Alo",
            size=Decimal("2"),
            price=Decimal("10"),
            reduce_only=True,
            position=Decimal("1"),
            side="ask",
        )
    assert_maker_order(
        tif="Alo",
        size=Decimal("1"),
        price=Decimal("10"),
        reduce_only=True,
        position=Decimal("1"),
        side="ask",
    )
    assert crosses_self(Decimal("10"), Decimal("10")) is True
    assert crosses_self(Decimal("10"), Decimal("11")) is False


def test_cloid_tag_marks_only_our_orders():
    ours = new_cloid()
    assert is_ours(ours)
    foreign = "0x" + "ab" * 16
    assert is_ours(foreign) is False
    orders = (
        OpenOrder("io:ANTH", 1, ours, "bid", Decimal("1"), Decimal("1")),
        OpenOrder("io:ANTH", 2, foreign, "ask", Decimal("2"), Decimal("1")),
        OpenOrder("io:ANTH", 3, None, "ask", Decimal("3"), Decimal("1")),
        OpenOrder("io:SNDK", 4, None, "bid", Decimal("4"), Decimal("1")),
    )
    foreign_orders, orphans = classify_open_orders(orders, "io:ANTH", {ours.lower()}, set(), is_ours)
    assert [item.oid for item in foreign_orders] == [2, 3]
    assert orphans == ()


def test_live_flags_fail_closed():
    assert load_config({}).mode == "paper"
    live = load_config(
        {
            "ENTROPY_DRY_RUN": "0",
            "ENTROPY_LIVE": "1",
            "ENTROPY_LIVE_CONFIRM": "io:ANTH",
            "ENTROPY_ACCOUNT_ADDRESS": "0x" + "11" * 20,
            "ENTROPY_AGENT_SECRET_FILE": "agent.key",
        }
    )
    assert live.mode == "live"
    with pytest.raises(ConfigError):
        load_config({"ENTROPY_DRY_RUN": "0"})
    with pytest.raises(ConfigError):
        load_config(
            {
                "ENTROPY_DRY_RUN": "0",
                "ENTROPY_LIVE": "1",
                "ENTROPY_LIVE_CONFIRM": "io:SNDK",
                "ENTROPY_ACCOUNT_ADDRESS": "0x" + "11" * 20,
                "ENTROPY_AGENT_SECRET_FILE": "agent.key",
            }
        )
    with pytest.raises(ConfigError):
        load_config(
            {
                "ENTROPY_DRY_RUN": "1",
                "ENTROPY_LIVE": "1",
                "ENTROPY_LIVE_CONFIRM": "io:ANTH",
                "ENTROPY_ACCOUNT_ADDRESS": "0x" + "11" * 20,
                "ENTROPY_AGENT_SECRET_FILE": "agent.key",
            }
        )


def test_secret_file_must_be_mode_0600(tmp_path: Path):
    path = tmp_path / "agent.key"
    path.write_text("0x" + "ab" * 32 + "\n", encoding="utf-8")
    path.chmod(0o644)
    with pytest.raises(SecretError):
        load_secret_file(path)
    path.chmod(0o600)
    assert load_secret_file(path).startswith("0xab")


def test_env_file_cannot_carry_a_private_key(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text("ENTROPY_AGENT_SECRET=0x" + "cd" * 32 + "\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_env_file(path)


def test_logs_redact_private_keys_and_keep_cloids():
    key = "0x" + "ab" * 32
    cloid = "0x" + "454d4d31" + "ef" * 12
    text = redact(f"key={key} cloid={cloid}")
    assert key not in text
    assert "REDACTED" in text
    assert cloid in text


def test_agent_must_match_and_not_be_the_master():
    account = "0x" + "11" * 20
    derived = "0x" + "22" * 20
    agents = (AgentRecord("maker", derived, 2_000_000),)
    require_agent(
        agents,
        derived,
        now_ms=1_000_000,
        min_ttl_ms=10,
        expected_name="maker",
        account_address=account,
    )
    with pytest.raises(AgentError):
        require_agent(
            agents,
            derived,
            now_ms=1_999_999,
            min_ttl_ms=10,
            expected_name="maker",
            account_address=account,
        )
    with pytest.raises(AgentError):
        require_agent(
            agents,
            derived,
            now_ms=1_000_000,
            min_ttl_ms=10,
            expected_name="other",
            account_address=account,
        )
    with pytest.raises(AgentError):
        require_agent(
            (AgentRecord(None, account, 9_000_000),),
            account,
            now_ms=1_000_000,
            min_ttl_ms=10,
            expected_name=None,
            account_address=account,
        )


def test_cancel_response_does_not_treat_an_error_as_done():
    assert parse_cancel_response({"status": "ok", "response": {"data": {"statuses": ["success"]}}}) == "canceled"
    assert (
        parse_cancel_response(
            {"status": "ok", "response": {"data": {"statuses": [{"error": "Order was never placed, already canceled, or filled."}]}}}
        )
        == "canceled"
    )
    assert parse_cancel_response({"status": "ok", "response": {"data": {"statuses": [{"error": "rate limited"}]}}}) == "unknown"
    assert parse_cancel_response({"status": "err"}) == "unknown"


def test_self_trade_status_is_distinct_from_a_normal_cancel():
    verdict, _order = parse_order_status(
        {"status": "order", "order": {"status": "selfTradeCanceled", "order": {"coin": "io:ANTH", "side": "B", "limitPx": "1", "sz": "1"}}}
    )
    assert verdict == "self_trade"
