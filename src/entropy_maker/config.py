"""Environment configuration. Live mode is an exact flag combination."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from entropy_maker.identity.secrets import SecretError, assert_env_file_has_no_key
from entropy_maker.market.allowlist import MarketError, assert_allowed
from entropy_maker.market.rounding import decimal_from

MAINNET_INFO_URL = "https://api.hyperliquid.xyz"
TESTNET_INFO_URL = "https://api.hyperliquid-testnet.xyz"
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    coin: str
    mode: str
    order_size: Decimal | None
    max_position: Decimal | None
    refresh_seconds: Decimal
    reprice_bps: Decimal
    half_spread_bps: Decimal
    skew_bps: Decimal
    max_book_age_seconds: Decimal
    state_file: Path
    min_notional_usd: Decimal
    min_rest_seconds: Decimal
    agent_min_ttl_seconds: int
    agent_name: str | None
    network: str
    info_url: str
    paper_equity_usd: Decimal
    log_level: str
    account_address: str | None
    agent_secret_file: Path | None
    agent_secret_in_env: bool
    mark_deviation_bps: Decimal
    reconcile_polls: int
    book_error_limit: int
    book_source: str

    @property
    def max_book_age_ms(self) -> int:
        return int(self.max_book_age_seconds * 1000)

    @property
    def min_rest_ms(self) -> int:
        return int(self.min_rest_seconds * 1000)

    @property
    def refresh_seconds_float(self) -> float:
        return float(self.refresh_seconds)


def load_env_file(path: Path) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read env file {path}") from exc
    try:
        assert_env_file_has_no_key(text)
    except SecretError as exc:
        raise ConfigError(str(exc)) from exc
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def load_config(environ: dict[str, str] | None = None) -> Config:
    env = os.environ if environ is None else environ
    coin = env.get("ENTROPY_COIN", "io:ANTH").strip() or "io:ANTH"
    try:
        assert_allowed(coin)
    except MarketError as exc:
        raise ConfigError(str(exc)) from exc
    mode = _mode(env, coin)
    network = env.get("ENTROPY_NETWORK", "mainnet").strip().lower() or "mainnet"
    if network not in {"mainnet", "testnet"}:
        raise ConfigError("ENTROPY_NETWORK must be mainnet or testnet")
    info_url = env.get("ENTROPY_INFO_URL", "").strip()
    if not info_url:
        info_url = MAINNET_INFO_URL if network == "mainnet" else TESTNET_INFO_URL
    account = env.get("ENTROPY_ACCOUNT_ADDRESS", "").strip() or None
    if account is not None and not _ADDRESS_RE.fullmatch(account):
        raise ConfigError("ENTROPY_ACCOUNT_ADDRESS must be a 20-byte hex address")
    secret_file_raw = env.get("ENTROPY_AGENT_SECRET_FILE", "").strip()
    secret_in_env = bool(env.get("ENTROPY_AGENT_SECRET", "").strip())
    if secret_file_raw and secret_in_env:
        raise ConfigError("set only one of ENTROPY_AGENT_SECRET_FILE and ENTROPY_AGENT_SECRET")
    agent_name = env.get("ENTROPY_AGENT_NAME", "").strip() or None
    if mode == "live":
        if not account:
            raise ConfigError("live mode requires ENTROPY_ACCOUNT_ADDRESS")
        if not secret_file_raw and not secret_in_env:
            raise ConfigError("live mode requires ENTROPY_AGENT_SECRET_FILE")
    order_size = _optional_decimal(env, "ENTROPY_ORDER_SIZE")
    max_position = _optional_decimal(env, "ENTROPY_MAX_POSITION")
    if order_size is not None and order_size <= 0:
        raise ConfigError("ENTROPY_ORDER_SIZE must be positive")
    if max_position is not None and max_position <= 0:
        raise ConfigError("ENTROPY_MAX_POSITION must be positive")
    if (
        order_size is not None
        and max_position is not None
        and max_position < order_size
    ):
        raise ConfigError("ENTROPY_MAX_POSITION must be at least ENTROPY_ORDER_SIZE")
    refresh = _decimal(env, "ENTROPY_REFRESH_SECONDS", "2")
    if refresh <= 0:
        raise ConfigError("ENTROPY_REFRESH_SECONDS must be positive")
    reprice = _decimal(env, "ENTROPY_REPRICE_BPS", "5")
    half = _decimal(env, "ENTROPY_HALF_SPREAD_BPS", "8")
    skew = _decimal(env, "ENTROPY_SKEW_BPS", "10")
    if half <= 0:
        raise ConfigError("ENTROPY_HALF_SPREAD_BPS must be positive")
    if reprice < 0 or skew < 0:
        raise ConfigError("bps settings cannot be negative")
    max_age = _decimal(env, "ENTROPY_MAX_BOOK_AGE_SECONDS", "3")
    min_rest = _decimal(env, "ENTROPY_MIN_REST_SECONDS", "5")
    if max_age <= 0 or min_rest < 0:
        raise ConfigError("book age must be positive and min rest cannot be negative")
    min_notional = _decimal(env, "ENTROPY_MIN_NOTIONAL_USD", "10")
    if min_notional <= 0:
        raise ConfigError("ENTROPY_MIN_NOTIONAL_USD must be positive")
    mark_dev = _decimal(env, "ENTROPY_MARK_DEVIATION_BPS", "50")
    if mark_dev < 0:
        raise ConfigError("ENTROPY_MARK_DEVIATION_BPS cannot be negative")
    paper_equity = _decimal(env, "ENTROPY_PAPER_EQUITY_USD", "10000")
    if paper_equity <= 0:
        raise ConfigError("ENTROPY_PAPER_EQUITY_USD must be positive")
    ttl = _int(env, "ENTROPY_AGENT_MIN_TTL_SECONDS", 86400)
    polls = _int(env, "ENTROPY_RECONCILE_POLLS", 5)
    book_errors = _int(env, "ENTROPY_BOOK_ERROR_LIMIT", 5)
    if ttl < 0 or polls < 1 or book_errors < 1:
        raise ConfigError("ttl, reconcile polls, and book error limit are out of range")
    state_file = Path(env.get("ENTROPY_STATE_FILE", ".entropy-maker/state.json")).expanduser()
    log_level = env.get("ENTROPY_LOG_LEVEL", "INFO").strip().upper() or "INFO"
    book_source = env.get("ENTROPY_BOOK_SOURCE", "rest").strip().lower() or "rest"
    if book_source not in {"rest", "ws"}:
        raise ConfigError("ENTROPY_BOOK_SOURCE must be rest or ws")
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ConfigError("ENTROPY_LOG_LEVEL is not a logging level")
    return Config(
        coin=coin,
        mode=mode,
        order_size=order_size,
        max_position=max_position,
        refresh_seconds=refresh,
        reprice_bps=reprice,
        half_spread_bps=half,
        skew_bps=skew,
        max_book_age_seconds=max_age,
        state_file=state_file,
        min_notional_usd=min_notional,
        min_rest_seconds=min_rest,
        agent_min_ttl_seconds=ttl,
        agent_name=agent_name,
        network=network,
        info_url=info_url,
        paper_equity_usd=paper_equity,
        log_level=log_level,
        account_address=account,
        agent_secret_file=Path(secret_file_raw).expanduser() if secret_file_raw else None,
        agent_secret_in_env=secret_in_env,
        mark_deviation_bps=mark_dev,
        reconcile_polls=polls,
        book_error_limit=book_errors,
        book_source=book_source,
    )


def require_quote_sizes(config: Config) -> tuple[Decimal, Decimal]:
    if config.order_size is None or config.max_position is None:
        raise ConfigError("set ENTROPY_ORDER_SIZE and ENTROPY_MAX_POSITION before quoting")
    return config.order_size, config.max_position


def _mode(env: dict[str, str], coin: str) -> str:
    dry_raw = env.get("ENTROPY_DRY_RUN", "1").strip()
    live_raw = env.get("ENTROPY_LIVE", "0").strip()
    confirm = env.get("ENTROPY_LIVE_CONFIRM", "").strip()
    dry_on = _truthy(dry_raw, default=True)
    live_on = _exact_one(live_raw)
    wants_live = (not dry_on) or live_on or bool(confirm)
    if not wants_live:
        return "paper"
    if dry_on and not live_on and not confirm:
        return "paper"
    if (not dry_on) and live_on and confirm == coin:
        return "live"
    raise ConfigError(
        "refusing to start: live trading requires ENTROPY_DRY_RUN=0, "
        f"ENTROPY_LIVE=1, and ENTROPY_LIVE_CONFIRM={coin} together. "
        "Any other combination fails closed."
    )


def _truthy(value: str, default: bool) -> bool:
    if value == "":
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _exact_one(value: str) -> bool:
    return value == "1"


def _optional_decimal(env: dict[str, str], name: str) -> Decimal | None:
    raw = env.get(name, "").strip()
    if not raw:
        return None
    try:
        return decimal_from(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} is not a decimal") from exc


def _decimal(env: dict[str, str], name: str, default: str) -> Decimal:
    raw = env.get(name, "").strip() or default
    try:
        return decimal_from(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} is not a decimal") from exc


def _int(env: dict[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} is not an integer") from exc
