import pytest
import requests
from datetime import date, datetime
from breeze_client import format_breeze_date, BreezeClient, fetch_simulation_spot_price

def test_format_breeze_date():
    dt = datetime(2026, 8, 25, 9, 15, 0)
    formatted = format_breeze_date(dt, "ISO_HISTORICAL")
    assert formatted == "2026-08-25T09:15:00.000Z"

    d = date(2026, 8, 25)
    formatted_exp = format_breeze_date(d, "ISO_EXPIRY")
    assert formatted_exp == "2026-08-25T06:00:00.000Z"

def test_breeze_client_session_assignment():
    client = BreezeClient(api_key="my_key", secret_key="my_secret")
    assert client.configured is True

    token = client.generate_session(session_token="123456")
    assert token == "123456"
    assert client.session_token == "123456"

    token2 = client.generate_session(session_token="https://localhost:8501/?api_session=abcdefg&state=ok")
    assert token2 == "abcdefg"
    assert client.session_token == "abcdefg"

def test_breeze_client_check_response_401():
    client = BreezeClient(api_key="my_key", secret_key="my_secret", session_token="123456")

    class MockResponse:
        def __init__(self, status_code, json_data, content=b""):
            self.status_code = status_code
            self.json_data = json_data
            self.content = content
        def json(self):
            return self.json_data
        def raise_for_status(self):
            pass

    r = MockResponse(200, {"Status": 401, "Error": "Unauthorized User"})
    with pytest.raises(requests.exceptions.HTTPError) as exc_info:
        client._check_response(r)
    assert "401 Client Error: Unauthorized User" in str(exc_info.value)

def test_fetch_simulation_spot_price_fallback():
    # When client is None or inactive
    spot = fetch_simulation_spot_price(None, "NIFTY", "2026-08-11", fallback_spot=24500.0)
    assert spot == 24500.0
