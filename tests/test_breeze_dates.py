from datetime import date, datetime, timezone

from breeze_dates import format_breeze_date, format_strike_price, normalize_right


def test_historical_date_has_utc_milliseconds():
    value = datetime(2026, 7, 1, 9, 15, tzinfo=timezone.utc)
    assert format_breeze_date(value, "ISO_HISTORICAL") == "2026-07-01T09:15:00.000Z"


def test_date_converts_to_utc():
    assert format_breeze_date(date(2026, 7, 28), "REST_EXPIRY") == "2026-07-28T00:00:00.000Z"


def test_feed_exchange_month_is_capitalized():
    assert format_breeze_date(date(2026, 7, 28), "FEED_EXCHANGE") == "28-Jul-2026"


def test_strike_removes_float_suffix():
    assert format_strike_price(24500.0) == "24500"
    assert format_strike_price("24500.0") == "24500"
    assert format_strike_price("24500.50") == "24500.5"


def test_right_normalization():
    assert normalize_right("CALL") == "call"
    assert normalize_right("put") == "put"
    assert normalize_right("call", websocket=True) == "Call"
    assert normalize_right("PUT", websocket=True) == "Put"
