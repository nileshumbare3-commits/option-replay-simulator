from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    else:
        raise TypeError(f"Unsupported date value: {type(value)!r}")

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_breeze_date(date_obj: Any, target_type: str) -> str:
    """Normalize Python dates/datetimes (or ISO strings) to Breeze formats.

    ISO_HISTORICAL / REST_EXPIRY: YYYY-MM-DDTHH:mm:ss.000Z
    FEED_EXCHANGE: DD-MMM-YYYY
    DISPLAY_FORMAT: DD-MMM-YYYY
    """
    target = target_type.upper()
    dt = _as_utc(date_obj)

    if target in {"ISO_HISTORICAL", "REST_EXPIRY"}:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    if target in {"FEED_EXCHANGE", "DISPLAY_FORMAT"}:
        return f"{dt.day:02d}-{MONTHS[dt.month - 1]}-{dt.year:04d}"
    raise ValueError(f"Unknown Breeze date target_type: {target_type!r}")


def normalize_right(right: str, websocket: bool = False) -> str:
    value = str(right).strip().lower()
    if value not in {"call", "put"}:
        raise ValueError("right must be 'call' or 'put'")
    return value.capitalize() if websocket else value


def format_strike_price(strike_price: Any) -> str:
    """Return a numeric strike without accidental '.0' suffixes."""
    if isinstance(strike_price, bool):
        raise TypeError("strike_price must be numeric, not bool")
    try:
        value = Decimal(str(strike_price).strip())
    except Exception as exc:
        raise ValueError(f"Invalid strike_price: {strike_price!r}") from exc
    if not value.is_finite():
        raise ValueError("strike_price must be finite")
    if value == value.to_integral_value():
        return str(value.quantize(Decimal("1")))
    return format(value.normalize(), "f")


def parse_contract_expiry(value: Any) -> datetime:
    """Parse Breeze/master expiry values into a timezone-aware UTC datetime."""
    if isinstance(value, (datetime, date)):
        return _as_utc(value)
    text = str(value).strip()
    if not text:
        raise ValueError("empty expiry date")
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return _as_utc(candidate)
        except ValueError:
            pass
    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Unsupported expiry date: {value!r}")
