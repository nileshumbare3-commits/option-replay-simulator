import pytest
from datetime import date
from backend.expiry_service import (
    parse_expiry_from_symbol_name,
    generate_mock_symbols_for_demo,
    get_dynamic_expiry_dates,
    format_contract_symbol
)

def test_parse_expiry_from_symbol_name_weekly():
    # Weekly Format Example: NIFTY2682524500CE -> Extract 26 (Year 2026), 8 (Month Aug), 25 (Day 25)
    d = parse_expiry_from_symbol_name("NIFTY2682524500CE")
    assert d == date(2026, 8, 25)

    # Weekly with letters: BANKNIFTY26O1552000PE (O = Oct)
    d2 = parse_expiry_from_symbol_name("BANKNIFTY26O1552000PE")
    assert d2 == date(2026, 10, 15)

def test_parse_expiry_from_symbol_name_monthly():
    # Monthly Format Example: NIFTY26AUG24500CE -> Extract 26 (Year 2026), AUG (Month 08)
    # Without siblings, defaults to end of month (Aug 31)
    d = parse_expiry_from_symbol_name("NIFTY26AUG24500CE")
    assert d == date(2026, 8, 31)

    # With siblings, uses the maximum of weekly siblings
    siblings = [date(2026, 8, 11), date(2026, 8, 18), date(2026, 8, 25)]
    d_with_siblings = parse_expiry_from_symbol_name("NIFTY26AUG24500CE", sibling_weekly_dates=siblings)
    assert d_with_siblings == date(2026, 8, 25)

def test_generate_mock_symbols_for_demo():
    symbols = generate_mock_symbols_for_demo("NIFTY", 24000, 50, date(2026, 8, 1))
    assert len(symbols) > 0
    # First few should contain weekly formats
    assert any("CE" in s for s in symbols)

def test_get_dynamic_expiry_dates():
    # Tests clean deduction and deduplication
    dates = get_dynamic_expiry_dates("NIFTY", 24000, 50, date(2026, 8, 1))
    assert len(dates) == 4
    assert dates[0] >= date(2026, 8, 1)

def test_format_contract_symbol():
    # Monthly formatting
    sym_monthly = format_contract_symbol("NIFTY", date(2026, 8, 27), 24500, "CE")
    assert "AUG" in sym_monthly

    # Weekly formatting
    sym_weekly = format_contract_symbol("NIFTY", date(2026, 8, 11), 24500, "CE")
    # Weekly format NIFTY2681124500CE
    assert "NIFTY2681124500CE" in sym_weekly
