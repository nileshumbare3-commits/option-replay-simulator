import pytest
from datetime import date
from app import parse_expiry_from_symbol, generate_mock_symbols_for_demo

def test_parse_expiry_monthly():
    # Monthly format: NIFTY26AUG24500CE -> Extract 26 (Year 2026), AUG (Month 08) -> Derive Last Thursday (27-Aug-2026)
    dt = parse_expiry_from_symbol("NIFTY26AUG24500CE")
    assert dt == date(2026, 8, 27)

def test_parse_expiry_weekly():
    # Weekly format: NIFTY2682524500CE -> Extract 26 (Year 2026), 8 (Month Aug), 25 (Day 25) -> Date: 2026-08-25
    dt = parse_expiry_from_symbol("NIFTY2682524500CE")
    assert dt == date(2026, 8, 25)

def test_parse_expiry_weekly_month_codes():
    # Weekly format with O (October): NIFTY26O1545000PE -> Extract 26, Oct, 15
    dt = parse_expiry_from_symbol("NIFTY26O1545000PE")
    assert dt == date(2026, 10, 15)

    # Weekly format with N (November): NIFTY26N0545000PE
    dt = parse_expiry_from_symbol("NIFTY26N0545000PE")
    assert dt == date(2026, 11, 5)

    # Weekly format with D (December): NIFTY26D1045000PE
    dt = parse_expiry_from_symbol("NIFTY26D1045000PE")
    assert dt == date(2026, 12, 10)

def test_parse_expiry_different_underlying():
    # BANKNIFTY pre-Sept 2025 monthly: expires on last Thursday
    dt = parse_expiry_from_symbol("BANKNIFTY25AUG24500CE")
    assert dt == date(2025, 8, 28)

    # BANKNIFTY post-Sept 2025 monthly: expires on last Tuesday (e.g. Sept 2025 -> last Tuesday is 30-Sep-2025)
    dt = parse_expiry_from_symbol("BANKNIFTY25SEP24500CE")
    assert dt == date(2025, 9, 30)

def test_parse_expiry_fallback_formats():
    # Dash format
    assert parse_expiry_from_symbol("NIFTY-13-Aug-2026-25000-CE") == date(2026, 8, 13)
    # ISO date format
    assert parse_expiry_from_symbol("NIFTY-2026-08-13-25000-CE") == date(2026, 8, 13)

def test_generate_mock_symbols():
    # Let's test that mock generation produces symbols that can be parsed back to correct dates
    symbols = generate_mock_symbols_for_demo("NIFTY", 25000, date(2026, 8, 7))
    assert len(symbols) == 4

    parsed_dates = [parse_expiry_from_symbol(sym) for sym in symbols]
    # Expected expiries starting from 7-Aug-2026: 13-Aug, 20-Aug, 27-Aug (Monthly), 3-Sep
    assert parsed_dates[0] == date(2026, 8, 13)
    assert parsed_dates[1] == date(2026, 8, 20)
    assert parsed_dates[2] == date(2026, 8, 27)
    assert parsed_dates[3] == date(2026, 9, 3)
