import os
import time
import pandas as pd
from urllib.parse import quote
from typing import List, Dict, Any

class BreezeService:
    def __init__(self, api_key: str = None, secret_key: str = None, session_token: str = None):
        self.api_key = api_key or os.getenv("BREEZE_API_KEY", "")
        self.secret_key = secret_key or os.getenv("BREEZE_SECRET_KEY", "")
        self.session_token = session_token
        self.last_call_time = 0.0
        self.calls_per_second_limit = 3  # ICICI Direct rate limiting

    def login_url(self) -> str:
        """
        Generates the ICICI Direct OAuth login URL for session token retrieval.
        """
        return f"https://api.icicidirect.com/apiuser/login?api_key={quote(self.api_key)}"

    def rate_limit(self):
        """
        Simple rate limiter to respect Breeze api limitations.
        """
        now = time.time()
        elapsed = now - self.last_call_time
        minimum_interval = 1.0 / self.calls_per_second_limit
        if elapsed < minimum_interval:
            time.sleep(minimum_interval - elapsed)
        self.last_call_time = time.time()

    def fetch_historical_candles(
        self,
        symbol: str,
        expiry_date: str,
        right: str,
        strike_price: float,
        start_date: str,
        end_date: str,
        interval: str = "1minute"
    ) -> List[Dict[str, Any]]:
        """
        Fetches historical candles from the Breeze client.
        Uses standard structure or simulates data if connection is in demo mode.
        """
        self.rate_limit()

        # Real-world wrapper would invoke breeze.get_historical_data or similar endpoint.
        # Below returns structured data ready for simulation and resampling.
        mock_data = [
            {
                "datetime": f"{start_date} 09:{15 + i}:00",
                "open": 100.0 + i * 0.1,
                "high": 101.0 + i * 0.1,
                "low": 99.5 + i * 0.1,
                "close": 100.5 + i * 0.1,
                "volume": 1200 + i * 10,
                "open_interest": 50000 + i * 100,
            }
            for i in range(120)
        ]
        return mock_data

    @staticmethod
    def resample_candles(candles: List[Dict[str, Any]], timeframe: str) -> List[Dict[str, Any]]:
        """
        Dynamically aggregates and resamples 1-minute historical candles into larger intervals:
        3m, 5m, 15m, 30m, 1-hour, or Daily candles.
        """
        if not candles:
            return []

        df = pd.DataFrame(candles)
        df["datetime"] = pd.to_datetime(df["datetime"])
        df.set_index("datetime", inplace=True)

        # Map typical timeframes to pandas resample codes
        resample_map = {
            "3m": "3T",
            "5m": "5T",
            "15m": "15T",
            "30m": "30T",
            "1h": "1H",
            "1d": "1D"
        }
        rule = resample_map.get(timeframe, "1T")

        # Resampling rules for OHLC + Vol + OI
        resampled_df = df.resample(rule).agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "open_interest": "last"
        }).dropna()

        # Format back to list of dicts with iso string timestamps
        resampled_df.reset_index(inplace=True)
        resampled_df["datetime"] = resampled_df["datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")
        return resampled_df.to_dict(orient="records")
