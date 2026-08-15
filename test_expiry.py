import pytest
from datetime import date
from backend.expiry_service import (
    parse_expiry_from_symbol_name,
    generate_mock_symbols_for_demo,
    get_dynamic_expiry_dates,
    format_contract_symbol,
    process_historical_contracts_payload,
    normalize_date,
    adjust_for_holiday,
    is_exchange_holiday
)

def test_normalize_date():
    assert normalize_date("2026-08-27T15:30:00+05:30") == date(2026, 8, 27)
    assert normalize_date("2026-08-27") == date(2026, 8, 27)
    assert normalize_date(date(2026, 8, 27)) == date(2026, 8, 27)

def test_holiday_adjustment():
    # Aug 15, 2026 is Independence Day (exchange holiday)
    aug_15 = date(2026, 8, 15)
    assert is_exchange_holiday(aug_15) is True
    adj = adjust_for_holiday(aug_15)
    assert adj < aug_15
    assert is_exchange_holiday(adj) is False

def test_process_historical_contracts_payload_exact_spec():
    payload = {
      "underlying": "NIFTY",
      "request_date": "2026-08-15",
      "historical_contracts": [
        {
          "symbol": "NIFTY26AUGFUT",
          "strike": None,
          "option_type": None,
          "limit_expiry": "2026-08-27T15:30:00+05:30",
          "status": "ACTIVE"
        },
        {
          "symbol": "NIFTY26AUG24000CE",
          "strike": 24000,
          "option_type": "CE",
          "expiry": "2026-08-27",
          "ltp": 150.25,
          "oi": 125000,
          "iv": 18.5
        },
        {
          "symbol": "NIFTY26AUG24000PE",
          "strike": 24000,
          "option_type": "PE",
          "expiry": "2026-08-27",
          "ltp": 120.10,
          "oi": 110000,
          "iv": 19.0
        }
      ]
    }

    res = process_historical_contracts_payload(payload)
    assert res["status"] == "SUCCESS"
    assert res["underlying"] == "NIFTY"
    assert res["target_limit_expiry"] == "2026-08-27"
    assert res["is_historical_contract"] is True
    assert len(res["option_chain"]) == 1

    row = res["option_chain"][0]
    assert row["strike_price"] == 24000
    assert row["call"]["symbol"] == "NIFTY26AUG24000CE"
    assert row["call"]["ltp"] == 150.25
    assert row["call"]["oi"] == 125000
    assert row["put"]["symbol"] == "NIFTY26AUG24000PE"
    assert row["put"]["ltp"] == 120.10
    assert row["put"]["oi"] == 110000

def test_process_historical_contracts_payload_expired_rollover():
    payload = {
      "underlying": "NIFTY",
      "request_date": "2026-09-01", # After August contract
      "historical_contracts": [
        {
          "symbol": "NIFTY26AUG24000CE",
          "strike": 24000,
          "option_type": "CE",
          "expiry": "2026-08-27",
          "ltp": 150.25
        }
      ]
    }

    res = process_historical_contracts_payload(payload)
    assert res["status"] == "SUCCESS"
    assert res["target_limit_expiry"] == "2026-08-27"
    assert "Rolled over" in res.get("warning", "")

def test_parse_expiry_from_symbol_name_weekly():
    d = parse_expiry_from_symbol_name("NIFTY2682524500CE")
    assert d == date(2026, 8, 25)

def test_parse_expiry_from_symbol_name_monthly():
    d = parse_expiry_from_symbol_name("NIFTY26AUG24500CE")
    assert d == date(2026, 8, 31)

def test_generate_mock_symbols_for_demo():
    symbols = generate_mock_symbols_for_demo("NIFTY", 24000, 50, date(2026, 8, 1))
    assert len(symbols) > 0

def test_get_dynamic_expiry_dates():
    dates = get_dynamic_expiry_dates("NIFTY", 24000, 50, date(2026, 8, 1))
    assert len(dates) == 4
    assert dates[0] >= date(2026, 8, 1)

def test_format_contract_symbol():
    sym_monthly = format_contract_symbol("NIFTY", date(2026, 8, 27), 24500, "CE")
    assert "AUG" in sym_monthly
