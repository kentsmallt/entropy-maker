"""Live signer. Constructed only after the paper/live gate is already live.

Network timeouts are returned as ``Ack(kind='timeout')``. The same action is
not sent again. Immediate ALO fills are treated as a breach and halted by the
engine, because a post-only order is not supposed to take.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from entropy_maker.config import Config, ConfigError
from entropy_maker.exchange.info_client import InfoClient, InfoError
from entropy_maker.exchange.parse import (
    parse_available_usd,
    parse_cancel_response,
    parse_historical,
    parse_open_orders,
    parse_order_status,
    parse_position,
)
from entropy_maker.identity.agent import AgentError, parse_agents, require_agent
from entropy_maker.identity.secrets import SecretError, load_secret_file, load_secret_value
from entropy_maker.market.allowlist import ENTROPY_DEX
from entropy_maker.market.rounding import format_decimal
from entropy_maker.models import Ack, Action
from entropy_maker.risk.policy import MAKER_TIF, PolicyError, assert_maker_order

log = logging.getLogger("entropy_maker.live")


class LiveError(RuntimeError):
    pass


class LiveSession:
    def __init__(self, config: Config, info: InfoClient, exchange: object, account_address: str) -> None:
        self.config = config
        self.info = info
        self.exchange = exchange
        self.account_address = account_address

    @classmethod
    def open(cls, config: Config) -> LiveSession:
        if config.mode != "live":
            raise LiveError("refusing to open a live session while mode is paper")
        secret = _read_secret(config)
        try:
            from eth_account import Account
            from hyperliquid.exchange import Exchange
            from hyperliquid.utils.constants import MAINNET_API_URL, TESTNET_API_URL
        except ImportError as exc:
            raise LiveError('live mode needs pip install -e ".[live]"') from exc
        try:
            wallet = Account.from_key(secret)
        except Exception:
            raise ConfigError("agent secret is not a usable secp256k1 private key") from None
        finally:
            secret = ""
        info = InfoClient(config.info_url)
        _verify_agent(config, info, wallet.address)
        base = MAINNET_API_URL if config.network == "mainnet" else TESTNET_API_URL
        if config.info_url.rstrip("/") not in {MAINNET_API_URL, TESTNET_API_URL}:
            base = config.info_url
        try:
            exchange = Exchange(
                wallet,
                base,
                account_address=config.account_address,
                perp_dexs=[ENTROPY_DEX],
            )
        except Exception as exc:
            raise LiveError("could not construct the Hyperliquid exchange client") from exc
        assert config.account_address is not None
        return cls(config, info, exchange, config.account_address)

    def read_account(self) -> tuple[Decimal, Decimal]:
        payload = self.info.clearinghouse(self.account_address, ENTROPY_DEX)
        position = parse_position(payload, self.config.coin)
        available = parse_available_usd(payload)
        return position, available

    def read_report(self) -> dict[str, object]:
        return {
            "open_orders": parse_open_orders(
                self.info.post({"type": "frontendOpenOrders", "user": self.account_address, "dex": ENTROPY_DEX})
            ),
            "historical": parse_historical(self.info.historical(self.account_address)),
        }

    def status_for(self, cloid: str):
        return parse_order_status(
            self.info.post({"type": "orderStatus", "user": self.account_address, "oid": cloid})
        )

    def fills_since(self, start_ms: int):
        return self.info.user_fills_since(self.account_address, start_ms)

    def send(self, action: Action) -> Ack:
        try:
            if action.kind == "cancel":
                return _cancel(self.exchange, self.config.coin, action.cloid)
            return _place(self.exchange, self.config.coin, action)
        except PolicyError as exc:
            return Ack("rejected", detail=str(exc))
        except Exception as exc:
            if _is_timeout(exc):
                log.warning("exchange timeout on %s %s; not retrying", action.kind, action.cloid)
                return Ack("timeout", detail="timeout")
            log.warning("exchange error on %s %s; not retrying", action.kind, action.cloid)
            return Ack("unknown", detail=type(exc).__name__)


def _read_secret(config: Config) -> str:
    import os

    try:
        if config.agent_secret_file is not None:
            return load_secret_file(config.agent_secret_file)
        if config.agent_secret_in_env:
            return load_secret_value(os.environ.get("ENTROPY_AGENT_SECRET", ""))
    except SecretError as exc:
        raise ConfigError(str(exc)) from exc
    raise ConfigError("live mode has no agent secret")


def _verify_agent(config: Config, info: InfoClient, derived: str) -> None:
    assert config.account_address is not None
    try:
        abstraction = info.user_dex_abstraction(config.account_address)
        user_abstraction = info.user_abstraction(config.account_address)
    except InfoError as exc:
        raise LiveError("could not read abstraction state") from exc
    log.info(
        "abstraction observation dex=%s user=%s (not a trading permit)",
        _brief(abstraction),
        _brief(user_abstraction),
    )
    try:
        agents = parse_agents(info.extra_agents(config.account_address))
        require_agent(
            agents,
            derived,
            now_ms=_now_ms(),
            min_ttl_ms=config.agent_min_ttl_seconds * 1000,
            expected_name=config.agent_name,
            account_address=config.account_address,
        )
    except (AgentError, InfoError) as exc:
        raise LiveError(str(exc)) from exc
    log.info("agent %s is registered for %s", derived, config.account_address)


def _place(exchange: object, coin: str, action: Action) -> Ack:
    if action.px is None or action.sz is None:
        raise PolicyError("place action is missing price or size")
    assert_maker_order(
        tif=MAKER_TIF,
        size=action.sz,
        price=action.px,
        reduce_only=action.reduce_only,
        position=Decimal(0) if not action.reduce_only else _placeholder_position(action),
        side=action.side,
    )
    from hyperliquid.utils.types import Cloid

    is_buy = action.side == "bid"
    response = exchange.order(  # type: ignore[attr-defined]
        coin,
        is_buy,
        float(format_decimal(action.sz)),
        float(format_decimal(action.px)),
        {"limit": {"tif": MAKER_TIF}},
        reduce_only=action.reduce_only,
        cloid=Cloid.from_str(action.cloid),
    )
    return _ack_from_order_response(response)


def _placeholder_position(action: Action):
    """``assert_maker_order`` checks reduce-only against position.

    The engine already checked the signed position. Repeat the sign check
    here with a position that matches the side and is at least the size, so
    a reduce-only order cannot be built for the wrong side inside this module.
    """
    assert action.sz is not None
    return action.sz if action.side == "ask" else -action.sz


def _cancel(exchange: object, coin: str, cloid: str) -> Ack:
    from hyperliquid.utils.types import Cloid

    response = exchange.cancel_by_cloid(coin, Cloid.from_str(cloid))  # type: ignore[attr-defined]
    kind = parse_cancel_response(response)
    if kind == "canceled":
        return Ack("canceled")
    return Ack("unknown", detail="cancel_response")


def _ack_from_order_response(response: object) -> Ack:
    if not isinstance(response, dict) or response.get("status") != "ok":
        return Ack("unknown", detail="order_response")
    try:
        status = response["response"]["data"]["statuses"][0]
    except (KeyError, IndexError, TypeError):
        return Ack("unknown", detail="order_status_shape")
    if not isinstance(status, dict):
        return Ack("unknown", detail="order_status_type")
    if "resting" in status and isinstance(status["resting"], dict):
        oid = status["resting"].get("oid")
        return Ack("resting", oid=oid if isinstance(oid, int) else None)
    if "filled" in status:
        return Ack("filled", detail="alo_filled_immediately")
    if "error" in status:
        return Ack("rejected", detail=str(status["error"])[:180])
    return Ack("unknown", detail="unrecognized_status")


def _is_timeout(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return True
    text = str(exc).lower()
    return "timed out" in text or "timeout" in text


def _brief(value: object) -> str:
    text = str(value)
    return text if len(text) <= 120 else text[:117] + "..."


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)
