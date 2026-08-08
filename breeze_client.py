from __future__ import annotations

import io
import json
import logging
import os
import time
import zipfile
from datetime import datetime, timezone
from urllib.parse import quote

import pandas as pd
import requests

from breeze_dates import (
    format_breeze_date,
    format_strike_price,
    normalize_right,
    parse_contract_expiry,
)

logger = logging.getLogger(__name__)


class BreezeClient:
    CUSTOMER_URL = "https://api.icicidirect.com/breezeapi/api/v1/customerdetails"
    HISTORICAL_URL = "https://breezeapi.icicidirect.com/api/v2/historicalcharts"
    OPTION_CHAIN_URL = "https://api.icicidirect.com/breezeapi/api/v1/OptionChain"
    SECURITY_MASTER_URL = "https://directlink.icicidirect.com/NewSecurityMaster/SecurityMaster.zip"
    STOCK_SCRIPT_CSV_URL = "https://traderweb.icicidirect.com/Content/File/txtFile/ScripFile/StockScriptNew.csv"

    def __init__(self, api_key=None, secret_key=None, session_token=None, max_retries=3):
        self.api_key = api_key or os.getenv("BREEZE_API_KEY")
        self.secret_key = secret_key or os.getenv("BREEZE_SECRET_KEY")
        self.session_token = session_token
        self.max_retries = max(0, int(max_retries))
        self.timeout = 30

    @property
    def configured(self):
        return bool(self.api_key and self.secret_key)

    def login_url(self):
        return f"https://api.icicidirect.com/apiuser/login?api_key={quote(self.api_key)}"

    def _headers(self):
        return {"X-SessionToken": self.session_token, "X-apikey": self.api_key}

    def _request(self, method, url, *, params=None, headers=None, timeout=None):
        """GET wrapper with bounded retries and exact query logging on empty Success."""
        request = requests.Request(method, url, params=params, headers=headers).prepare()
        last_exc = None
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.Session().send(request, timeout=timeout or self.timeout)
                response.raise_for_status()
                payload = response.json()
                if payload.get("Status") == 200 and payload.get("Success") == []:
                    logger.warning("Breeze returned empty Success. Exact query: %s", request.url)
                return payload
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                retryable = status in {429, 500, 502, 503, 504} or isinstance(exc, (requests.Timeout, requests.ConnectionError, ValueError))
                if attempt >= self.max_retries or not retryable:
                    raise
                delay = min(2 ** attempt, 8)
                logger.warning("Breeze request failed (attempt %d/%d, status=%s): %s; retrying in %ss", attempt + 1, self.max_retries + 1, status, exc, delay)
                time.sleep(delay)
        raise last_exc or RuntimeError("Breeze request failed")

    def exchange_api_session(self, api_session):
        payload = json.dumps({"SessionToken": api_session, "AppKey": self.api_key})
        r = requests.get(self.CUSTOMER_URL, headers={"Content-Type": "application/json"}, data=payload, timeout=self.timeout)
        r.raise_for_status()
        token = (r.json().get("Success") or {}).get("session_token")
        if not token:
            raise RuntimeError("No session_token returned by Breeze")
        self.session_token = token
        return token

    def historical_option(self, stock_code, from_date, to_date, expiry_date, right, strike_price, interval="1minute"):
        if not self.api_key or not self.session_token:
            raise RuntimeError("Complete Breeze login first")
        params = {
            "interval": interval,
            "from_date": format_breeze_date(from_date, "ISO_HISTORICAL"),
            "to_date": format_breeze_date(to_date, "ISO_HISTORICAL"),
            "stock_code": stock_code,
            "exchange_code": "NFO",
            "product_type": "options",
            "expiry_date": format_breeze_date(expiry_date, "REST_EXPIRY"),
            "right": normalize_right(right),
            "strike_price": format_strike_price(strike_price),
        }
        payload = self._request("GET", self.HISTORICAL_URL, params=params, headers=self._headers())
        return payload.get("Success", [])

    def get_option_chain_quotes(self, stock_code, expiry_date, right="call"):
        if not self.api_key or not self.session_token:
            raise RuntimeError("Complete Breeze login first")
        params = {
            "stock_code": stock_code,
            "exchange_code": "NFO",
            "product_type": "options",
            "right": normalize_right(right),
            "expiry_date": format_breeze_date(expiry_date, "REST_EXPIRY"),
        }
        payload = self._request("GET", self.OPTION_CHAIN_URL, params=params, headers=self._headers())
        return payload.get("Success", [])

    @staticmethod
    def build_feed_params(stock_code, expiry_date, strike_price, right, interval=None, exchange_code="NFO", product_type="options"):
        """Normalize parameters for Breeze subscribe_feeds-style helpers."""
        params = {
            "exchange_code": exchange_code,
            "stock_code": stock_code,
            "expiry_date": format_breeze_date(expiry_date, "FEED_EXCHANGE"),
            "strike_price": format_strike_price(strike_price),
            "right": normalize_right(right, websocket=True),
            "product_type": product_type,
        }
        if interval:
            params["interval"] = interval
        return params

    @staticmethod
    def _find_column(columns, names):
        normalized = {str(c).strip().lower().replace(" ", "_"): c for c in columns}
        for name in names:
            if name in normalized:
                return normalized[name]
        return None

    def _parse_security_master(self, raw_bytes, stock_code):
        frames = []
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as archive:
            for name in archive.namelist():
                if not name.lower().endswith((".csv", ".txt")):
                    continue
                with archive.open(name) as fh:
                    for sep in [",", "|", "\t"]:
                        fh.seek(0)
                        try:
                            df = pd.read_csv(fh, sep=sep, dtype=str, low_memory=False)
                        except Exception:
                            continue
                        if len(df.columns) > 1:
                            frames.append(df)
                            break
        return self._extract_expiries(frames, stock_code)

    def _extract_expiries(self, frames, stock_code):
        expiries = set()
        target = str(stock_code).strip().upper()
        for df in frames:
            exchange_col = self._find_column(df.columns, {"exchange_code", "exchange", "exch"})
            stock_col = self._find_column(df.columns, {"stock_code", "stockcode", "symbol", "short_name", "sc"})
            expiry_col = self._find_column(df.columns, {"expiry_date", "expiry", "expirydate", "exp_date"})
            if not expiry_col:
                continue
            work = df
            if exchange_col:
                work = work[work[exchange_col].astype(str).str.upper().eq("NFO")]
            if stock_col:
                stock_values = work[stock_col].astype(str).str.upper().str.strip()
                work = work[stock_values.eq(target) | stock_values.str.contains(target, regex=False, na=False)]
            for value in work[expiry_col].dropna().tolist():
                try:
                    expiries.add(parse_contract_expiry(value).date())
                except ValueError:
                    logger.debug("Ignoring unparseable security-master expiry: %r", value)
        return sorted(expiries)

    def expiry_calendar(self, stock_code):
        """Read actual NFO contract expiries from the broker's daily Security Master."""
        if not self.api_key or not self.session_token:
            raise RuntimeError("Complete Breeze login first")
        try:
            r = requests.get(self.SECURITY_MASTER_URL, timeout=self.timeout)
            r.raise_for_status()
            expiries = self._parse_security_master(r.content, stock_code)
            if expiries:
                return expiries
            logger.warning("Security Master contained no expiries for %s", stock_code)
        except Exception as exc:
            logger.warning("Security Master sync failed for %s: %s", stock_code, exc)

        try:
            r = requests.get(self.STOCK_SCRIPT_CSV_URL, timeout=self.timeout)
            r.raise_for_status()
            df = pd.read_csv(io.BytesIO(r.content), dtype=str, low_memory=False)
            expiries = self._extract_expiries([df], stock_code)
            if expiries:
                return expiries
        except Exception as exc:
            logger.warning("StockScriptNew.csv sync failed for %s: %s", stock_code, exc)

        raise RuntimeError(f"Unable to sync official Breeze expiries for {stock_code}")
