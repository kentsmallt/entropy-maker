from decimal import Decimal

import pytest

from entropy_maker.market.allowlist import MarketError, assert_allowed
from entropy_maker.market.book import parse_book
from entropy_maker.market.meta import parse_market
from entropy_maker.market.rounding import quantize_price, quantize_size
from entropy_maker.quoting.quotes import _maker_quote, _separate, compute_quotes
from entropy_maker.risk.inventory import side_allowed
from tests.conftest import book, spec


def test_price_ticks_follow_significant_figures():
    assert quantize_price(Decimal("1234.56"), 0, "bid") == Decimal("1234.5")
    assert quantize_price(Decimal("1234.56"), 0, "ask") == Decimal("1234.6")
    assert quantize_price(Decimal("0.0012345"), 0, "bid") == Decimal("0.001234")
    assert quantize_price(Decimal("0.0012345"), 0, "ask") == Decimal("0.001235")
    assert quantize_price(Decimal("1.01141"), 1, "bid") == Decimal("1.0114")
    assert quantize_price(Decimal("1.01141"), 1, "ask") == Decimal("1.0115")
    assert quantize_price(Decimal("100001.2"), 2, "bid") == Decimal("100001")
    assert quantize_price(Decimal("100001.2"), 2, "ask") == Decimal("100002")


def test_size_rounds_down():
    assert quantize_size(Decimal("1.239"), 2) == Decimal("1.23")
    with pytest.raises(ValueError):
        quantize_size(Decimal("0.001"), 2)


def test_allowlist_and_live_meta_rules():
    assert assert_allowed("io:ANTH") == "io:ANTH"
    with pytest.raises(MarketError):
        assert_allowed("BTC")
    with pytest.raises(MarketError):
        assert_allowed("io:NOT_A_MARKET")
    payload = [
        {"universe": [{"name": "io:ANTH", "szDecimals": 3, "maxLeverage": 15, "onlyIsolated": True}]},
        [{"markPx": "1840.4"}],
    ]
    market = parse_market(payload, "io:ANTH")
    assert market.sz_decimals == 3
    assert market.mark_px == Decimal("1840.4")
    payload[0]["universe"][0]["isDelisted"] = True
    with pytest.raises(MarketError):
        parse_market(payload, "io:ANTH")
    payload[0]["universe"][0]["isDelisted"] = False
    payload[0]["universe"][0]["onlyIsolated"] = False
    with pytest.raises(MarketError):
        parse_market(payload, "io:ANTH")


def test_book_parser_keeps_both_sides():
    parsed = parse_book(
        {"coin": "io:ANTH", "time": 10, "levels": [[{"px": "1.5", "sz": "2", "n": 1}], [{"px": "1.6", "sz": "3", "n": 1}]]},
        "io:ANTH",
    )
    assert parsed.best_bid == Decimal("1.5")
    assert parsed.best_ask == Decimal("1.6")
    assert parsed.mid == Decimal("1.55")


def test_quotes_stay_post_only_and_do_not_cross_each_other():
    quotes = compute_quotes(
        book=book(),
        spec=spec(),
        position=Decimal("0"),
        bid_size=Decimal("1"),
        ask_size=Decimal("1"),
        max_position=Decimal("5"),
        half_spread_bps=Decimal("8"),
        skew_bps=Decimal("10"),
        min_notional=Decimal("1"),
    )
    bid, ask = quotes
    assert bid is not None and ask is not None
    assert bid.px < ask.px
    assert bid.px < Decimal("101")
    assert ask.px > Decimal("100")


def test_inventory_drops_the_side_that_would_increase_risk():
    assert side_allowed(Decimal("4.5"), Decimal("1"), Decimal("5"), "bid") is False
    assert side_allowed(Decimal("4.5"), Decimal("1"), Decimal("5"), "ask") is True
    bid, ask = compute_quotes(
        book=book(),
        spec=spec(),
        position=Decimal("4.5"),
        bid_size=Decimal("1"),
        ask_size=Decimal("1"),
        max_position=Decimal("5"),
        half_spread_bps=Decimal("8"),
        skew_bps=Decimal("10"),
        min_notional=Decimal("1"),
    )
    assert bid is None
    assert ask is not None


def test_price_through_the_touch_is_stepped_back_not_flipped_to_a_taker():
    quote = _maker_quote(
        "bid",
        Decimal("101"),
        Decimal("1"),
        spec(),
        book(),
        Decimal("0"),
        Decimal("5"),
        Decimal("1"),
    )
    assert quote is not None
    assert quote.px < Decimal("101")


def test_locked_quotes_are_separated_or_dropped():
    from entropy_maker.models import Quote

    bid, ask = _separate(
        Quote("bid", Decimal("100.50"), Decimal("1")),
        Quote("ask", Decimal("100.50"), Decimal("1")),
        spec(),
        book(),
    )
    assert bid is not None and ask is not None
    assert bid.px < ask.px
