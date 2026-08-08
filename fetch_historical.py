import argparse
import os
import sys
import time
from datetime import datetime, timedelta
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from breeze_client import BreezeClient

def parse_date(d_str):
    """
    Parses a date string in format YYYY-MM-DD, or ISO. Returns a datetime object.
    """
    for fmt in ('%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%fZ', '%Y-%m-%dT%H:%M:%S.%f'):
        try:
            return datetime.strptime(d_str, fmt)
        except ValueError:
            pass
    raise ValueError(f"Could not parse date: {d_str}")

def format_breeze_date(dt):
    """
    Formats datetime to Breeze API's preferred ISO format: YYYY-MM-DDTHH:MM:SS.000Z
    """
    return dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')

def get_date_chunks(start_dt, end_dt, chunk_days=30):
    """
    Splits the date range [start_dt, end_dt] into chunks of maximum length chunk_days.
    Returns list of (chunk_start, chunk_end) tuples.
    """
    chunks = []
    current_start = start_dt
    while current_start < end_dt:
        current_end = current_start + timedelta(days=chunk_days)
        if current_end > end_dt:
            current_end = end_dt
        chunks.append((current_start, current_end))
        current_start = current_end + timedelta(seconds=1)
    return chunks

def download_historical_data(api_key, session_token, stock_code, exchange_code, product_type,
                             start_date, end_date, interval='1minute',
                             expiry_date=None, right=None, strike_price=None,
                             chunk_days=14):
    """
    Downloads historical data from Breeze API by automatically chunking the date range,
    merging chunks, and returning a grouped year-wise Pandas DataFrames dict.
    """
    client = BreezeClient(api_key=api_key, session_token=session_token)

    start_dt = parse_date(start_date)
    end_dt = parse_date(end_date)

    # Generate chunks
    chunks = get_date_chunks(start_dt, end_dt, chunk_days=chunk_days)
    print(f"Total date range split into {len(chunks)} chunk(s).")

    all_data = []
    for i, (c_start, c_end) in enumerate(chunks, 1):
        from_iso = format_breeze_date(c_start)
        to_iso = format_breeze_date(c_end)

        print(f"Fetching chunk {i}/{len(chunks)}: {from_iso} to {to_iso}...")
        try:
            res = client.historical(
                stock_code=stock_code,
                exchange_code=exchange_code,
                product_type=product_type,
                from_date=from_iso,
                to_date=to_iso,
                interval=interval,
                expiry_date=expiry_date,
                right=right,
                strike_price=strike_price
            )
            if res:
                all_data.extend(res)
                print(f"  Retrieved {len(res)} rows.")
            else:
                print("  No data returned for this chunk.")
        except Exception as e:
            print(f"  Error fetching chunk: {e}", file=sys.stderr)

        # Modest rate-limiting delay between requests to be polite to the API
        if i < len(chunks):
            time.sleep(0.5)

    if not all_data:
        print("No historical data was fetched.")
        return {}

    df = pd.DataFrame(all_data)

    # Process datetime and clean data
    if 'datetime' in df.columns:
        df['datetime'] = pd.to_datetime(df['datetime'], errors='coerce')
        df = df.dropna(subset=['datetime'])
        df = df.sort_values('datetime')

        # Numeric conversions
        for col in ['open', 'high', 'low', 'close', 'volume', 'open_interest']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        df['Year'] = df['datetime'].dt.year

        # Group by year and create individual DataFrames
        year_wise_dfs = {}
        for year, group in df.groupby('Year'):
            # clean up temporary 'Year' column if desired or keep it
            clean_group = group.drop(columns=['Year'])
            year_wise_dfs[int(year)] = clean_group

        return year_wise_dfs
    else:
        print("Breeze API response did not contain 'datetime' column.")
        return {}

def main():
    parser = argparse.ArgumentParser(description="Download 1-minute historical data from ICICI Breeze API and save year-wise CSV files.")

    parser.add_argument('--session', type=str, required=True, help="Session token / session key from direct Breeze redirect.")
    parser.add_argument('--stock', type=str, required=True, help="Stock code (e.g. NIFTY, ITC, RELIND).")
    parser.add_argument('--exchange', type=str, default='NFO', help="Exchange code (e.g. NFO, NSE). Default is NFO.")
    parser.add_argument('--product', type=str, default='options', help="Product type (e.g. options, futures, cash). Default is options.")
    parser.add_argument('--start', type=str, required=True, help="Start date (YYYY-MM-DD or ISO format).")
    parser.add_argument('--end', type=str, required=True, help="End date (YYYY-MM-DD or ISO format).")
    parser.add_argument('--interval', type=str, default='1minute', help="Bar interval (e.g. 1minute, 5minute, 1day). Default is 1minute.")
    parser.add_argument('--expiry', type=str, help="Expiry date (ISO format like YYYY-MM-DD or specific format required for options/futures).")
    parser.add_argument('--right', type=str, help="Option right (call, put). Only applicable for options.")
    parser.add_argument('--strike', type=float, help="Strike price. Only applicable for options.")
    parser.add_argument('--chunk-days', type=int, default=14, help="Number of days per request chunk. Default is 14.")
    parser.add_argument('--out-dir', type=str, default=".", help="Directory to save generated year-wise CSV files. Default is current directory.")

    args = parser.parse_args()

    api_key = os.getenv('BREEZE_API_KEY')
    if not api_key:
        print("Error: BREEZE_API_KEY is not set in your environment or .env file.", file=sys.stderr)
        sys.exit(1)

    print(f"Downloading historical {args.interval} data for {args.stock} ({args.exchange}) from {args.start} to {args.end}...")

    year_wise = download_historical_data(
        api_key=api_key,
        session_token=args.session,
        stock_code=args.stock,
        exchange_code=args.exchange,
        product_type=args.product,
        start_date=args.start,
        end_date=args.end,
        interval=args.interval,
        expiry_date=args.expiry,
        right=args.right,
        strike_price=args.strike,
        chunk_days=args.chunk_days
    )

    os.makedirs(args.out_dir, exist_ok=True)

    for year, df_year in year_wise.items():
        filename = f"{args.stock}_{args.product}_{year}.csv"
        filepath = os.path.join(args.out_dir, filename)
        df_year.to_csv(filepath, index=False)
        print(f"Successfully generated year-wise CSV: {filepath}")

if __name__ == '__main__':
    main()
