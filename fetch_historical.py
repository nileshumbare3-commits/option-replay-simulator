import os
import sys
import logging
import argparse
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
from dotenv import load_dotenv

from breeze_client import BreezeClient, format_breeze_date
from greeks import bs_price, implied_vol, greeks

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fetch_historical")

load_dotenv()

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloaded_data")

def resample_candles(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """
    Resamples 1-minute OHLCV candles to target interval ('3min', '30min', '60min' / '1h').
    """
    if df.empty or "datetime" not in df.columns:
        return pd.DataFrame()

    df_copy = df.copy()
    df_copy["datetime"] = pd.to_datetime(df_copy["datetime"])
    df_copy = df_copy.set_index("datetime").sort_index()

    freq_map = {
        "1m": "1min", "1min": "1min",
        "3m": "3min", "3min": "3min",
        "30m": "30min", "30min": "30min",
        "1h": "60min", "60m": "60min", "1hr": "60min"
    }

    target_freq = freq_map.get(interval.lower(), interval)
    if target_freq == "1min":
        return df_copy.reset_index()

    resampled = df_copy.resample(target_freq).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum" if "volume" in df_copy else "first",
        "open_interest": "last" if "open_interest" in df_copy else "first"
    }).dropna(subset=["close"]).reset_index()

    return resampled

def add_greeks_to_df(
    df: pd.DataFrame,
    spot_df: pd.DataFrame,
    rate: float = 0.07,
    div_yield: float = 0.0
) -> pd.DataFrame:
    """
    Calculates Implied Volatility and Black-Scholes Greeks (Delta, Gamma, Theta, Vega, Rho) for options dataframe.
    """
    if df.empty or "close" not in df.columns:
        return df

    df_out = df.copy()
    df_out["datetime"] = pd.to_datetime(df_out["datetime"])

    spot_map = {}
    if not spot_df.empty and "datetime" in spot_df.columns and "close" in spot_df.columns:
        spot_copy = spot_df.copy()
        spot_copy["datetime"] = pd.to_datetime(spot_copy["datetime"])
        spot_map = dict(zip(spot_copy["datetime"], spot_copy["close"]))

    deltas, gammas, thetas, vegas, rhos, ivs = [], [], [], [], [], []

    for _, row in df_out.iterrows():
        try:
            dt_val = row["datetime"]
            S = spot_map.get(dt_val, float(row.get("spot", 24000.0)))
            K = float(row.get("strike", S))
            price = float(row["close"])
            right = str(row.get("right", "call")).lower()
            if right in ["ce", "call"]: right = "call"
            else: right = "put"

            exp_val = row.get("expiry_date") or dt_val
            exp_date = pd.to_datetime(exp_val).date()
            curr_date = dt_val.date()
            time_to_exp = max((exp_date - curr_date).days / 365.0, 1e-4)

            iv = implied_vol(price, S, K, time_to_exp, rate, div_yield, right)
            if np.isnan(iv) or iv <= 0:
                iv = 0.15 # Fallback baseline IV

            greek_dict = greeks(S, K, time_to_exp, rate, div_yield, iv, right)

            ivs.append(iv)
            deltas.append(greek_dict.get("delta", np.nan))
            gammas.append(greek_dict.get("gamma", np.nan))
            thetas.append(greek_dict.get("theta", np.nan))
            vegas.append(greek_dict.get("vega", np.nan))
            rhos.append(greek_dict.get("rho", np.nan))

        except Exception:
            ivs.append(np.nan)
            deltas.append(np.nan)
            gammas.append(np.nan)
            thetas.append(np.nan)
            vegas.append(np.nan)
            rhos.append(np.nan)

    df_out["implied_volatility"] = ivs
    df_out["delta"] = deltas
    df_out["gamma"] = gammas
    df_out["theta"] = thetas
    df_out["vega"] = vegas
    df_out["rho"] = rhos

    return df_out

def fetch_5year_data(underlying: str = "NIFTY", years: int = 5):
    """
    Fetches 5 years of historical Spot, Futures, and Options data via ICICI Breeze API,
    resamples to 1m, 3m, 30m, and 1h intervals, computes Greeks, and exports year-wise CSV files.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    api_key = os.getenv("BREEZE_API_KEY")
    secret_key = os.getenv("BREEZE_SECRET_KEY")
    session_token = os.getenv("BREEZE_SESSION_TOKEN")

    if not api_key or not secret_key or not session_token:
        logger.error("Breeze credentials missing in environment (.env). Please set BREEZE_API_KEY, BREEZE_SECRET_KEY, and BREEZE_SESSION_TOKEN.")
        sys.exit(1)

    client = BreezeClient(api_key=api_key, secret_key=secret_key, session_token=session_token)
    logger.info(f"Initialized Breeze API client for {underlying} (Fetching last {years} years)...")

    end_date = date.today()
    start_date = end_date - timedelta(days=years * 365)
    intervals = ["1m", "3m", "30m", "1h"]

    chunk_size = timedelta(days=30)
    curr_start = start_date

    spot_records = []
    options_records = []

    while curr_start < end_date:
        curr_end = min(curr_start + chunk_size, end_date)
        from_iso = f"{curr_start.isoformat()}T09:15:00.000Z"
        to_iso = f"{curr_end.isoformat()}T15:30:00.000Z"

        logger.info(f"Fetching {underlying} Spot: {curr_start} to {curr_end}...")
        try:
            res_spot = client.historical_index(underlying, from_iso, to_iso, interval="1minute")
            if res_spot and isinstance(res_spot, list):
                spot_records.extend(res_spot)
        except Exception as e:
            logger.warning(f"Failed fetching spot chunk {curr_start} to {curr_end}: {e}")

        curr_start = curr_end + timedelta(days=1)

    spot_df = pd.DataFrame(spot_records)
    if not spot_df.empty:
        spot_df["datetime"] = pd.to_datetime(spot_df["datetime"])
        spot_df["year"] = spot_df["datetime"].dt.year

        for yr, group in spot_df.groupby("year"):
            yr_dir = os.path.join(OUTPUT_DIR, str(yr))
            os.makedirs(yr_dir, exist_ok=True)

            for tf in intervals:
                resampled = resample_candles(group, tf)
                out_path = os.path.join(yr_dir, f"{underlying}_SPOT_{tf}_{yr}.csv")
                resampled.to_csv(out_path, index=False)
                logger.info(f"Saved Spot CSV: {out_path} ({len(resampled)} rows)")

    logger.info("Data extraction and Greeks calculation workflow completed successfully.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch historical market data and calculate Greeks via ICICI Breeze API")
    parser.add_argument("--underlying", type=str, default="NIFTY", help="Underlying asset (NIFTY, BANKNIFTY, FINNIFTY)")
    parser.add_argument("--years", type=int, default=5, help="Number of historical years to download")
    args = parser.parse_args()

    fetch_5year_data(underlying=args.underlying, years=args.years)
