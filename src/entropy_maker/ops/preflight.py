"""Read-only startup check. This command never places or cancels an order."""

from __future__ import annotations

import logging
from decimal import Decimal

from entropy_maker.config import Config
from entropy_maker.exchange.info_client import InfoClient, InfoError
from entropy_maker.market.book import deviation_bps
from entropy_maker.market.rounding import format_decimal, quantize_size
from entropy_maker.models import Book, MarketSpec

log = logging.getLogger("entropy_maker")


def run_preflight(config: Config, client: InfoClient | None = None) -> int:
    info = client or InfoClient(config.info_url)
    try:
        spec = info.market(config.coin)
        book = info.book(config.coin)
    except (InfoError, ValueError) as exc:
        log.error("preflight failed: %s", exc)
        return 2
    _print_market(config, spec, book)
    if config.order_size is not None:
        try:
            size = quantize_size(config.order_size, spec.sz_decimals)
        except ValueError as exc:
            log.error("order size: %s", exc)
            return 2
        log.info(
            "order size %s notional %s USD at mark (min %s)",
            format_decimal(size),
            format_decimal(size * spec.mark_px),
            format_decimal(config.min_notional_usd),
        )
        if size * spec.mark_px < config.min_notional_usd:
            log.error("order notional is below the minimum")
            return 2
    if config.mode == "live":
        return _live_read(config, info, spec)
    log.info("paper mode: agent key was not loaded and no orders were sent")
    return 0


def _print_market(config: Config, spec: MarketSpec, book: Book) -> None:
    spread = None
    if book.best_bid is not None and book.best_ask is not None and book.mid:
        spread = deviation_bps(book.best_ask, book.best_bid)
    log.info(
        "coin=%s mode=%s szDecimals=%s maxLeverage=%s isolated=%s mark=%s",
        spec.coin,
        config.mode,
        spec.sz_decimals,
        spec.max_leverage,
        spec.only_isolated,
        format_decimal(spec.mark_px),
    )
    log.info(
        "bbo bid=%s ask=%s spread_bps=%s book_time_ms=%s",
        _fmt(book.best_bid),
        _fmt(book.best_ask),
        _fmt(spread),
        book.time_ms,
    )


def _live_read(config: Config, info: InfoClient, spec: MarketSpec) -> int:
    del spec
    try:
        from entropy_maker.exchange.live import LiveSession

        session = LiveSession.open(config)
        position, available = session.read_account()
    except Exception as exc:
        log.error("live preflight failed: %s", exc)
        return 2
    log.info(
        "account position=%s available_usd=%s; no orders were sent",
        format_decimal(position),
        format_decimal(available),
    )
    return 0


def _fmt(value: Decimal | None) -> str:
    if value is None:
        return "-"
    return format_decimal(value)
