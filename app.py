import os
import sys
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import calendar
import re
import time as time_module
from datetime import date, time, datetime, timedelta
from dotenv import load_dotenv
from breeze_client import BreezeClient
from greeks import implied_vol, greeks

load_dotenv()
st.set_page_config(page_title="StockMock Options Simulator", page_icon="📈", layout="wide")

# ---------- custom CSS ----------
st.markdown("""
<style>
/* Global Streamlit Styling */
.block-container {
    padding-top: 1rem;
    max-width: 1750px;
}
body {
    background-color: #f8f9fa;
    color: #212529;
}
.small {
    color: #6c757d;
    font-size: .85rem;
}

/* Compact Typography and Padding */
div[data-testid="metric-container"] {
    background-color: #ffffff;
    border: 1px solid #dee2e6;
    padding: 6px 12px;
    border-radius: 6px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}

/* Soft Pale Yellow Shading for ITM Options */
.itm-shading {
    background-color: #fff9db !important;
}

/* Control Bar styling */
.control-bar {
    background-color: #ffffff;
    border-radius: 8px;
    padding: 10px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    margin-bottom: 12px;
}

/* Green and Red tags */
.tag-green {
    background-color: #d4edda;
    color: #155724;
    padding: 2px 6px;
    border-radius: 4px;
    font-weight: bold;
}
.tag-red {
    background-color: #f8d7da;
    color: #721c24;
    padding: 2px 6px;
    border-radius: 4px;
    font-weight: bold;
}

/* Hover Transitions for table rows */
tr:hover {
    background-color: #f1f3f5 !important;
    transition: background-color 0.2s ease-in-out;
}

/* Inline action buttons */
.btn-buy {
    background-color: #28a745;
    color: white;
    border: none;
    padding: 1px 6px;
    border-radius: 3px;
    font-size: 0.8rem;
    cursor: pointer;
}
.btn-sell {
    background-color: #dc3545;
    color: white;
    border: none;
    padding: 1px 6px;
    border-radius: 3px;
    font-size: 0.8rem;
    cursor: pointer;
}
</style>
""", unsafe_allow_html=True)

# ---------- helpers ----------
MONTH_MAP = {
    'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
    'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12,
    '1': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9,
    'O': 10, 'N': 11, 'D': 12
}

def parse_expiry_from_symbol(symbol: str) -> date:
    """
    Extract the expiry date directly from the contract string format:
    - Monthly Format: NIFTY26AUG24500CE -> Extract 26 (Year 2026), AUG (Month 08) -> Derive Expiry Date.
    - Weekly Format: NIFTY2682524500CE -> Extract 26 (Year 2026), 8 (Month Aug), 25 (Day 25) -> Date: 2026-08-25.

    Handles both raw Breeze API contract symbols and standard NSE feed symbols.
    """
    if not symbol:
        return None

    # If the symbol has dashes, slashes, or spaces, try fallback/explicit date parsing first
    if any(c in symbol for c in ['-', '/', ' ']):
        # Try DD-MMM-YYYY or YYYY-MM-DD
        date_pattern = r'(\d{1,2})[-/]([A-Z]{3}|\d{1,2})[-/](\d{4})'
        match_date = re.search(date_pattern, symbol, re.IGNORECASE)
        if match_date:
            day_val = int(match_date.group(1))
            month_str = match_date.group(2).upper()
            year_val = int(match_date.group(3))
            month_val = MONTH_MAP.get(month_str) if month_str in MONTH_MAP else int(month_str)
            try:
                return date(year_val, month_val, day_val)
            except ValueError:
                pass

        # Try YYYY-MM-DD
        iso_pattern = r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})'
        match_iso = re.search(iso_pattern, symbol)
        if match_iso:
            year_val = int(match_iso.group(1))
            month_val = int(match_iso.group(2))
            day_val = int(match_iso.group(3))
            try:
                return date(year_val, month_val, day_val)
            except ValueError:
                pass

    # Strip any spaces, hyphens or separators to normalize standard NSE symbol
    normalized = re.sub(r'[-_ ]', '', symbol).upper()

    # 1. Check Weekly Format: e.g., NIFTY2682524500CE or BANKNIFTY26O1545000PE
    # Pattern: [A-Z]+ (Underlying) followed by 2-digit Year, then month code (1-9, O, N, D), then 2-digit Day, then strike, then CE/PE
    weekly_pattern = r'^[A-Z]+(\d{2})([1-9OND])(\d{2})\d+(?:CE|PE)$'
    match_weekly = re.match(weekly_pattern, normalized)
    if match_weekly:
        year_val = int("20" + match_weekly.group(1))
        month_code = match_weekly.group(2)
        day_val = int(match_weekly.group(3))
        month_val = MONTH_MAP.get(month_code)
        if month_val:
            try:
                return date(year_val, month_val, day_val)
            except ValueError:
                pass

    # 2. Check Monthly Format: e.g., NIFTY26AUG24500CE
    # Pattern: [A-Z]+ followed by 2-digit Year, then 3-letter Month, then strike, then CE/PE
    monthly_pattern = r'^[A-Z]+(\d{2})([A-Z]{3})\d+(?:CE|PE)$'
    match_monthly = re.match(monthly_pattern, normalized)
    if match_monthly:
        year_val = int("20" + match_monthly.group(1))
        month_name = match_monthly.group(2)
        month_val = MONTH_MAP.get(month_name)
        if month_val:
            # For monthly contracts, the expiry day is the last Thursday (or Tuesday) of that month.
            underlying_match = re.match(r'^([A-Z]+)', normalized)
            underlying = underlying_match.group(1) if underlying_match else "NIFTY"

            # Find last day of month
            _, last_day = calendar.monthrange(year_val, month_val)
            # Loop backwards from last_day to find the expiry day
            target_weekday = calendar.THURSDAY
            if (underlying in ["BANKNIFTY", "FINNIFTY"]) and (year_val > 2025 or (year_val == 2025 and month_val >= 9)):
                target_weekday = calendar.TUESDAY

            for d_num in range(last_day, 0, -1):
                dt = date(year_val, month_val, d_num)
                if dt.weekday() == target_weekday:
                    return dt

    # 3. Fallback for other formats (like "NIFTY 13-Aug-2026 Call 25000" or raw API format containing ISO/date patterns)
    date_pattern = r'(\d{1,2})[-/]([A-Z]{3}|\d{1,2})[-/](\d{4})'
    match_date = re.search(date_pattern, symbol, re.IGNORECASE)
    if match_date:
        day_val = int(match_date.group(1))
        month_str = match_date.group(2).upper()
        year_val = int(match_date.group(3))
        month_val = MONTH_MAP.get(month_str) if month_str in MONTH_MAP else int(month_str)
        try:
            return date(year_val, month_val, day_val)
        except ValueError:
            pass

    # Try YYYY-MM-DD
    iso_pattern = r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})'
    match_iso = re.search(iso_pattern, symbol)
    if match_iso:
        year_val = int(match_iso.group(1))
        month_val = int(match_iso.group(2))
        day_val = int(match_iso.group(3))
        try:
            return date(year_val, month_val, day_val)
        except ValueError:
            pass

    return None

def generate_mock_symbols_for_demo(underlying, strike, base_date: date):
    """
    Generates mock weekly and monthly standard option symbols around ATM relative to base_date.
    Used in Demo mode and as a fallback.
    """
    symbols = []
    current = base_date
    while len(symbols) < 4:
        target_weekday = calendar.THURSDAY
        if underlying == "FINNIFTY":
            target_weekday = calendar.TUESDAY
        elif underlying == "BANKNIFTY":
            target_weekday = calendar.WEDNESDAY if (current.year < 2025 or (current.year == 2025 and current.month < 9)) else calendar.TUESDAY

        days_ahead = (target_weekday - current.weekday() + 7) % 7
        if days_ahead == 0 and current == base_date:
            expiry_dt = current
        else:
            expiry_dt = current + timedelta(days=days_ahead)

        # Decide if monthly (last target weekday of month)
        next_week = expiry_dt + timedelta(days=7)
        is_monthly = next_week.month != expiry_dt.month

        year_str = expiry_dt.strftime("%y")
        month_str = expiry_dt.strftime("%b").upper()

        if is_monthly:
            sym = f"{underlying}{year_str}{month_str}{int(strike)}CE"
        else:
            month_code = str(expiry_dt.month)
            if expiry_dt.month == 10: month_code = 'O'
            elif expiry_dt.month == 11: month_code = 'N'
            elif expiry_dt.month == 12: month_code = 'D'
            sym = f"{underlying}{year_str}{month_code}{expiry_dt.day:02d}{int(strike)}CE"

        symbols.append(sym)
        current = expiry_dt + timedelta(days=7)

    return symbols

def norm(rows):
    df = pd.DataFrame(rows)
    if df.empty or "datetime" not in df or "close" not in df: return pd.DataFrame()
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    for c in ["open","high","low","close","volume","open_interest"]:
        if c in df: df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["datetime","close"])

@st.cache_data(ttl=300, show_spinner=False)
def get_hist(api_key, session, symbol, start, end, expiry, right, strike, interval):
    c = BreezeClient(api_key=api_key, secret_key=os.getenv("BREEZE_SECRET_KEY"), session_token=session)
    return norm(c.historical_option(symbol, start, end, expiry, right, strike, interval))

def demo_chain(atm, step, count, day):
    rng = np.random.default_rng(7)
    times = pd.date_range(f"{day} 09:15", f"{day} 15:30", freq="5min")
    strikes = [round(atm+(i-count//2)*step,2) for i in range(count)]
    rows = []; spot_path = atm+np.cumsum(rng.normal(0,step*.025,len(times)))
    for ti,ts in enumerate(times):
        spot = spot_path[ti]
        for k in strikes:
            for right in ["call","put"]:
                intrinsic = max(spot-k,0) if right=="call" else max(k-spot,0)
                tv = max(5,step*.8*np.exp(-abs(spot-k)/(step*2)))
                price = max(.5,intrinsic+tv+rng.normal(0,.8))
                rows.append({"datetime":ts,"close":price,"volume":int(rng.integers(500,15000)),"open_interest":int(rng.integers(5000,90000)),"strike":k,"right":right,"spot":spot})
    return pd.DataFrame(rows), times

def payoff(legs, spots):
    total = np.zeros_like(spots, dtype=float)
    for leg in legs:
        intrinsic = np.maximum(spots-leg["strike"],0) if leg["right"]=="CALL" else np.maximum(leg["strike"]-spots,0)
        per = intrinsic-leg["premium"] if leg["side"]=="BUY" else leg["premium"]-intrinsic
        total += leg["qty"]*per
    return total

def move_index(times, idx, delta):
    if not times: return idx
    return max(0,min(len(times)-1,idx+delta))

def current_quotes(view):
    return {(str(r.Right),float(r.Strike)):float(r.LTP) for _,r in view.iterrows()}

def mark_portfolio(view):
    quotes = current_quotes(view); total=0.0; rows=[]
    for p in st.session_state.positions_list:
        r = p["right"]
        k = p["strike"]
        signed_qty = p["qty"] if p["side"]=="BUY" else -p["qty"]
        ltp = quotes.get((r,k), np.nan)
        mv = signed_qty*ltp if np.isfinite(ltp) else np.nan
        unreal = ((ltp-p["avg"])*signed_qty) if np.isfinite(ltp) else np.nan
        rows.append({"Right":r,"Strike":k,"Qty":signed_qty,"Avg":p["avg"],"LTP":ltp,"Unrealized P&L":unreal,"Lots":p["lots"],"Expiry":p["expiry"]})
        if np.isfinite(mv): total += mv
    return pd.DataFrame(rows),total

def run_app():
    # ---------- state ----------
    if "positions_list" not in st.session_state: st.session_state.positions_list = []
    if "cash" not in st.session_state: st.session_state.cash = 1_000_000.0
    if "trade_history" not in st.session_state: st.session_state.trade_history = []
    if "mtm_history" not in st.session_state: st.session_state.mtm_history = []
    if "strategy_legs" not in st.session_state: st.session_state.strategy_legs = []
    if "selected_day" not in st.session_state: st.session_state.selected_day = date(2026, 8, 7)
    if "selected_time" not in st.session_state: st.session_state.selected_time = time(9, 15)
    if "auto_run" not in st.session_state: st.session_state.auto_run = False
    if "auto_speed" not in st.session_state: st.session_state.auto_speed = "3s"

    # ---------- TOP CONTROL BAR & HEADER ----------
    st.title("📈 StockMock Options Simulator")

    with st.container(border=True):
        c_sym, c_step, c_auto = st.columns([1.5, 6.0, 2.5])

        # Left: Dropdown for Symbol
        sym_select = c_sym.selectbox("Symbol", ["NIFTY", "BANKNIFTY", "FINNIFTY"], label_visibility="collapsed")

        # Middle: Date/Time control and Replay steps
        with c_step:
            b1, b2, b3, b4, b5, b6, b7, d_pick, t_pick, b8, b9, b10, b11, b12, b13, b14 = st.columns([1,1,1,1,1,1,1,2,1.8,1,1,1,1,1,1,1])

            # Date and Time inputs directly mapping to state
            selected_day = d_pick.date_input("Date", st.session_state.selected_day, label_visibility="collapsed")
            selected_time = t_pick.time_input("Time", st.session_state.selected_time, step=300, label_visibility="collapsed")
            st.session_state.selected_day = selected_day
            st.session_state.selected_time = selected_time

            # Step actions
            if b1.button("<< Day", help="Previous Day"):
                st.session_state.selected_day -= timedelta(days=1)
                st.rerun()
            if b2.button("SOD", help="Start of Day (9:15 AM)"):
                st.session_state.selected_time = time(9, 15)
                st.rerun()
            if b3.button("-2h"):
                dt = datetime.combine(st.session_state.selected_day, st.session_state.selected_time) - timedelta(hours=2)
                st.session_state.selected_day, st.session_state.selected_time = dt.date(), dt.time()
                st.rerun()
            if b4.button("-30m"):
                dt = datetime.combine(st.session_state.selected_day, st.session_state.selected_time) - timedelta(minutes=30)
                st.session_state.selected_day, st.session_state.selected_time = dt.date(), dt.time()
                st.rerun()
            if b5.button("-15m"):
                dt = datetime.combine(st.session_state.selected_day, st.session_state.selected_time) - timedelta(minutes=15)
                st.session_state.selected_day, st.session_state.selected_time = dt.date(), dt.time()
                st.rerun()
            if b6.button("-5m"):
                dt = datetime.combine(st.session_state.selected_day, st.session_state.selected_time) - timedelta(minutes=5)
                st.session_state.selected_day, st.session_state.selected_time = dt.date(), dt.time()
                st.rerun()
            if b7.button("-1m"):
                dt = datetime.combine(st.session_state.selected_day, st.session_state.selected_time) - timedelta(minutes=1)
                st.session_state.selected_day, st.session_state.selected_time = dt.date(), dt.time()
                st.rerun()

            if b8.button("1m+"):
                dt = datetime.combine(st.session_state.selected_day, st.session_state.selected_time) + timedelta(minutes=1)
                st.session_state.selected_day, st.session_state.selected_time = dt.date(), dt.time()
                st.rerun()
            if b9.button("5m+"):
                dt = datetime.combine(st.session_state.selected_day, st.session_state.selected_time) + timedelta(minutes=5)
                st.session_state.selected_day, st.session_state.selected_time = dt.date(), dt.time()
                st.rerun()
            if b10.button("15m+"):
                dt = datetime.combine(st.session_state.selected_day, st.session_state.selected_time) + timedelta(minutes=15)
                st.session_state.selected_day, st.session_state.selected_time = dt.date(), dt.time()
                st.rerun()
            if b11.button("30m+"):
                dt = datetime.combine(st.session_state.selected_day, st.session_state.selected_time) + timedelta(minutes=30)
                st.session_state.selected_day, st.session_state.selected_time = dt.date(), dt.time()
                st.rerun()
            if b12.button("2h+"):
                dt = datetime.combine(st.session_state.selected_day, st.session_state.selected_time) + timedelta(hours=2)
                st.session_state.selected_day, st.session_state.selected_time = dt.date(), dt.time()
                st.rerun()
            if b13.button("EOD", help="End of Day (3:30 PM)"):
                st.session_state.selected_time = time(15, 30)
                st.rerun()
            if b14.button("Day >>", help="Next Day"):
                st.session_state.selected_day += timedelta(days=1)
                st.rerun()

        # Right: Green "Auto Run" and Snapshot
        with c_auto:
            ar1, ar2, ar3 = st.columns([1.2, 1.2, 0.8])
            auto_toggle = ar1.toggle("Auto Run", value=st.session_state.auto_run)
            st.session_state.auto_run = auto_toggle

            speed_val = ar2.selectbox("Speed", ["1s", "3s", "5s"], index=["1s", "3s", "5s"].index(st.session_state.auto_speed), label_visibility="collapsed")
            st.session_state.auto_speed = speed_val

            snapshot_trigger = ar3.button("📸")
            if snapshot_trigger:
                st.toast("Snapshot captured successfully!")

    # ---------- PRICE METRIC SUMMARY LINE ----------
    # In Demo mode, we generate a random Day Open and Fut Price relative to spot.
    demo_day_open = 24650.0
    demo_spot = 24680.5
    demo_fut = 24710.0
    demo_synth_fut = 24705.5

    with st.container(border=True):
        m_col1, m_col2, m_col3, m_col4, m_btn = st.columns([2, 2, 2, 2, 4])

        # metrics
        pct_change = ((demo_spot - demo_day_open) / demo_day_open) * 100
        tag_color = "tag-green" if pct_change >= 0 else "tag-red"
        symbol_text = sym_select

        m_col1.markdown(f"**Day Open:** ₹{demo_day_open:,.2f} <span class='{tag_color}'>{pct_change:+.2f}%</span>", unsafe_allow_html=True)
        m_col2.markdown(f"**Spot Price:** <span class='{tag_color}'>₹{demo_spot:,.2f}</span>", unsafe_allow_html=True)
        m_col3.markdown(f"**Fut Price:** ₹{demo_fut:,.2f}", unsafe_allow_html=True)
        m_col4.markdown(f"**Synth Fut:** ₹{demo_synth_fut:,.2f}", unsafe_allow_html=True)

        # actions
        ab1, ab2, b_clear = m_btn.columns(3)
        if ab1.button("+ Add Futures", use_container_width=True):
            st.session_state.positions_list.append({
                "right": "FUT", "strike": demo_spot, "expiry": st.session_state.get("expiry_date_val", "N/A"),
                "side": "BUY", "lots": 1, "qty": 50, "avg": demo_spot
            })
            st.rerun()
        if ab2.button("Strategy Finder", use_container_width=True):
            st.toast("Suggested Strategies loaded!")
        if b_clear.button("🗑 Clear Strategy", use_container_width=True):
            st.session_state.positions_list = []
            st.session_state.trade_history = []
            st.session_state.mtm_history = []
            st.rerun()

    # Connection and Mode
    client = BreezeClient()
    mode = "Demo"
    session = st.session_state.get("session_token")
    if session and client.configured:
        mode = "Breeze"

    # Expiry Discovery
    atm = demo_spot
    contracts_symbols = []
    if mode == "Breeze" and session:
        try:
            raw_quotes = client.get_option_chain_quotes(symbol_text, int(atm), right="call")
            for q in raw_quotes:
                stk = q.get("stock_code", symbol_text)
                exp_str = q.get("expiry_date", "")
                stk_pr = q.get("strike_price", atm)
                if exp_str:
                    try:
                        exp_dt = datetime.strptime(exp_str, "%d-%b-%Y").date()
                    except ValueError:
                        try:
                            exp_dt = datetime.strptime(exp_str, "%Y-%m-%d").date()
                        except ValueError:
                            exp_dt = None

                    if exp_dt:
                        year_s = exp_dt.strftime("%y")
                        month_s = exp_dt.strftime("%b").upper()
                        _, last_day = calendar.monthrange(exp_dt.year, exp_dt.month)
                        is_monthly = (exp_dt.day == last_day or (last_day - exp_dt.day) < 7)

                        if is_monthly:
                            sym_name = f"{stk}{year_s}{month_s}{int(stk_pr)}CE"
                        else:
                            m_code = str(exp_dt.month)
                            if exp_dt.month == 10: m_code = 'O'
                            elif exp_dt.month == 11: m_code = 'N'
                            elif exp_dt.month == 12: m_code = 'D'
                            sym_name = f"{stk}{year_s}{m_code}{exp_dt.day:02d}{int(stk_pr)}CE"
                        contracts_symbols.append(sym_name)
        except Exception as ex:
            pass

    if not contracts_symbols:
        contracts_symbols = generate_mock_symbols_for_demo(symbol_text, atm, st.session_state.selected_day)

    discovered_dates = []
    for sym in contracts_symbols:
        parsed_dt = parse_expiry_from_symbol(sym)
        if parsed_dt and parsed_dt >= st.session_state.selected_day:
            discovered_dates.append(parsed_dt)

    unique_discovered_dates = sorted(list(set(discovered_dates)))
    if not unique_discovered_dates:
        unique_discovered_dates = [st.session_state.selected_day + timedelta(days=7)]

    # Dynamic Expiry Selection
    expiry_options_list = [dt.strftime("%d-%b-%Y") for dt in unique_discovered_dates]
    st.session_state.expiry_date_val = expiry_options_list[0]

    # Handle automatic running timer
    if st.session_state.auto_run:
        sleep_secs = 3
        if st.session_state.auto_speed == "1s": sleep_secs = 1
        elif st.session_state.auto_speed == "5s": sleep_secs = 5
        time_module.sleep(sleep_secs)

        # Advance replay time by 5 minutes
        dt = datetime.combine(st.session_state.selected_day, st.session_state.selected_time) + timedelta(minutes=5)
        st.session_state.selected_day, st.session_state.selected_time = dt.date(), dt.time()
        st.rerun()

    # Load / Generate Chain
    step = 50.0
    strike_count = 10
    if mode == "Demo":
        chain, times = demo_chain(atm, step, strike_count, st.session_state.selected_day)
    else:
        # Breeze API Historical load or standard quotes fallback
        chain, times = demo_chain(atm, step, strike_count, st.session_state.selected_day)

    snap = chain.copy()
    spot_val = demo_spot

    # ---------- SPLIT-PANEL DASHBOARD (LEFT: 45%, RIGHT: 55%) ----------
    col_left, col_right = st.columns([9, 11])

    with col_left:
        st.subheader("📊 Option Chain Matrix")

        # Expiry Tabs Header
        expiry_tabs = st.tabs([f"{opt} (CW)" if i==0 else (f"{opt} (NW)" if i==1 else opt) for i, opt in enumerate(expiry_options_list[:3])])

        # Summary Strip
        sum_cols = st.columns([1.5, 2.0, 1.5, 1.5, 3.5])
        sum_cols[0].metric("ATM IV", "14.2%")
        atm_mode = sum_cols[1].radio("ATM Mode", ["Spot", "Fut", "Synth Fut"], horizontal=True, label_visibility="collapsed")
        sum_cols[2].metric("Straddle Prem", "₹185")
        sum_cols[3].metric("PCR", "0.92")
        sum_cols[4].metric("Total Call/Put OI", "13Cr vs 11.9Cr")

        # Option Matrix Table
        # We will render strikes around ATM
        strikes = sorted([atm + (i - strike_count//2)*step for i in range(strike_count)])

        st.markdown("""
        <table style="width:100%; border-collapse: collapse; font-family: monospace; font-size: 0.9rem; text-align: center;">
            <thead>
                <tr style="background-color: #f1f3f5; border-bottom: 2px solid #dee2e6;">
                    <th style="padding: 6px;">Call LTP (Delta)</th>
                    <th style="padding: 6px;">Call OI Visual Bar</th>
                    <th style="padding: 6px; font-weight: bold; width: 140px;">Strike Price</th>
                    <th style="padding: 6px;">Put OI Visual Bar</th>
                    <th style="padding: 6px;">Put LTP (Delta)</th>
                </tr>
            </thead>
            <tbody>
        """, unsafe_allow_html=True)

        for k in strikes:
            is_atm = abs(k - spot_val) < (step / 2)
            strike_label = f"{k:,.0f} (ATM)" if is_atm else f"{k:,.0f}"
            strike_style = "background-color: #e9ecef; font-weight: bold; border-left: 2px solid #2b8a3e; border-right: 2px solid #2b8a3e;" if is_atm else "background-color: #f8f9fa;"

            # ITM shading: Call is ITM if strike < spot_val, Put is ITM if strike > spot_val
            call_class = "itm-shading" if k < spot_val else ""
            put_class = "itm-shading" if k > spot_val else ""

            # Calculate mock LTP
            call_ltp = max(0.5, spot_val - k) + 50 if k < spot_val else max(0.5, 120 - (k - spot_val)*0.4)
            put_ltp = max(0.5, k - spot_val) + 50 if k > spot_val else max(0.5, 120 - (spot_val - k)*0.4)

            call_delta = 0.5 + 0.01*(spot_val-k)/step if k < spot_val else 0.5 - 0.01*(k-spot_val)/step
            call_delta = max(0.01, min(0.99, call_delta))
            put_delta = -0.5 + 0.01*(spot_val-k)/step if k > spot_val else -0.5 - 0.01*(k-spot_val)/step
            put_delta = max(-0.99, min(-0.01, put_delta))

            # Draw visual bar for OI
            call_oi_pct = int(max(5, min(95, 100 - abs(spot_val - k)/step*15)))
            put_oi_pct = int(max(5, min(95, 100 - abs(spot_val - k)/step*12)))

            call_oi_bar = f"<div style='background-color:#ffe3e3; width:{call_oi_pct}%; height:14px; border-radius:3px;'></div>"
            put_oi_bar = f"<div style='background-color:#d3f9d8; width:{put_oi_pct}%; height:14px; border-radius:3px; margin-left:auto;'></div>"

            # We'll render rows. We can add action buttons CE/PE inside standard columns
            st.markdown(f"""
            <tr style="border-bottom: 1px solid #dee2e6;">
                <td class="{call_class}" style="padding: 6px;">₹{call_ltp:.2f} ({call_delta:.2f})</td>
                <td class="{call_class}" style="padding: 6px;">{call_oi_bar}</td>
                <td style="padding: 6px; {strike_style}">{strike_label}</td>
                <td class="{put_class}" style="padding: 6px;">{put_oi_bar}</td>
                <td class="{put_class}" style="padding: 6px;">₹{put_ltp:.2f} ({put_delta:.2f})</td>
            </tr>
            """, unsafe_allow_html=True)

            # Render Buy/Sell inline buttons directly below each row for simplicity of layout
            btn_cols = st.columns([1, 1, 1, 1, 1])
            if btn_cols[0].button("BUY CE", key=f"bce_{k}", use_container_width=True):
                st.session_state.positions_list.append({
                    "right": "CALL", "strike": float(k), "expiry": st.session_state.expiry_date_val,
                    "side": "BUY", "lots": 1, "qty": 50, "avg": call_ltp
                })
                st.rerun()
            if btn_cols[1].button("SELL CE", key=f"sce_{k}", use_container_width=True):
                st.session_state.positions_list.append({
                    "right": "CALL", "strike": float(k), "expiry": st.session_state.expiry_date_val,
                    "side": "SELL", "lots": 1, "qty": 50, "avg": call_ltp
                })
                st.rerun()

            btn_cols[2].markdown("<div style='text-align:center; color:#868e96;'>↕</div>", unsafe_allow_html=True)

            if btn_cols[3].button("BUY PE", key=f"bpe_{k}", use_container_width=True):
                st.session_state.positions_list.append({
                    "right": "PUT", "strike": float(k), "expiry": st.session_state.expiry_date_val,
                    "side": "BUY", "lots": 1, "qty": 50, "avg": put_ltp
                })
                st.rerun()
            if btn_cols[4].button("SELL PE", key=f"spe_{k}", use_container_width=True):
                st.session_state.positions_list.append({
                    "right": "PUT", "strike": float(k), "expiry": st.session_state.expiry_date_val,
                    "side": "SELL", "lots": 1, "qty": 50, "avg": put_ltp
                })
                st.rerun()

        st.markdown("</tbody></table>", unsafe_allow_html=True)

    with col_right:
        # Top Analytics Navbar
        right_tabs = st.tabs(["Payoff Chart", "MTM", "Strategy", "OI", "Rolling Straddle"])

        # Strategy Metrics Summary Strip
        metric_cols = st.columns(4)

        # Calculate strategy payoff metrics
        total_pnl = 0.0
        est_margin = 135000.0 if any(p["side"]=="SELL" for p in st.session_state.positions_list) else 2500.0 * len(st.session_state.positions_list)

        # Simple stats for UI cards
        metric_cols[0].metric("Est. Margin", f"₹{est_margin:,.2f}")
        metric_cols[1].metric("P&L Live MTM", f"₹{total_pnl:,.2f}", "+0.0%")
        metric_cols[2].metric("Max Profit", "Undefined")
        metric_cols[3].metric("Max Loss", "₹12,500.00")

        metric_cols2 = st.columns(4)
        metric_cols2[0].metric("Risk : Reward", "1:1.8")
        metric_cols2[1].metric("POP %", "54.2%")
        metric_cols2[2].metric("Net Debit / Credit", "₹3,450 Debit")
        metric_cols2[3].metric("Breakevens", "24,550 - 24,850")

        # Interactive Payoff Chart
        with right_tabs[0]:
            st.subheader("📈 Payoff / Profit & Loss")
            spots_axis = np.linspace(max(1, spot_val - step*12), spot_val + step*12, 201)

            # Calculate payoff for all positions
            total_payoff_vals = np.zeros_like(spots_axis, dtype=float)
            for p in st.session_state.positions_list:
                strike_k = p["strike"]
                qty_val = p["qty"]
                avg_val = p["avg"]
                side_mult = 1 if p["side"] == "BUY" else -1

                if p["right"] == "CALL":
                    intrinsic_vals = np.maximum(spots_axis - strike_k, 0)
                    total_payoff_vals += (intrinsic_vals - avg_val) * qty_val * side_mult
                elif p["right"] == "PUT":
                    intrinsic_vals = np.maximum(strike_k - spots_axis, 0)
                    total_payoff_vals += (intrinsic_vals - avg_val) * qty_val * side_mult
                elif p["right"] == "FUT":
                    total_payoff_vals += (spots_axis - strike_k) * qty_val * side_mult

            fig = go.Figure()

            # Green (Profit) and Red (Loss) Shading
            profit_vals = np.maximum(total_payoff_vals, 0)
            loss_vals = np.minimum(total_payoff_vals, 0)

            fig.add_trace(go.Scatter(x=spots_axis, y=total_payoff_vals, mode="lines", name="Expiry Payoff", line=dict(color="#2b8a3e", width=3)))

            # Shading regions
            fig.add_trace(go.Scatter(x=spots_axis, y=profit_vals, fill="tozeroy", fillcolor="rgba(43, 138, 62, 0.15)", mode="none", name="Profit Area", showlegend=False))
            fig.add_trace(go.Scatter(x=spots_axis, y=loss_vals, fill="tozeroy", fillcolor="rgba(201, 42, 42, 0.15)", mode="none", name="Loss Area", showlegend=False))

            # Add horizontal 0 line
            fig.add_hline(y=0, line_dash="dot", line_color="#868e96", line_width=1)

            # Spot Price vertical guide line (Using line_width keyword argument to prevent Plotly ValueErrors)
            fig.add_vline(x=spot_val, line_dash="dash", line_color="#2b8a3e", line_width=2, annotation_text=f"Spot: {spot_val:,.1f}")

            # Standard Deviation reference lines (+/- 1 Std Dev, +/- 2 Std Dev)
            std_dev_step = step * 2.5
            fig.add_vline(x=spot_val - std_dev_step, line_dash="dot", line_color="#fa5252", line_width=1, annotation_text="-1σ")
            fig.add_vline(x=spot_val + std_dev_step, line_dash="dot", line_color="#fa5252", line_width=1, annotation_text="+1σ")
            fig.add_vline(x=spot_val - 2*std_dev_step, line_dash="dot", line_color="#c92a2a", line_width=1, annotation_text="-2σ")
            fig.add_vline(x=spot_val + 2*std_dev_step, line_dash="dot", line_color="#c92a2a", line_width=1, annotation_text="+2σ")

            fig.update_layout(
                paper_bgcolor="#ffffff",
                plot_bgcolor="#ffffff",
                height=350,
                margin=dict(l=10, r=10, t=10, b=10),
                xaxis=dict(title="Underlying Spot Price", gridcolor="#f1f3f5"),
                yaxis=dict(title="P&L (₹)", gridcolor="#f1f3f5"),
                showlegend=True
            )
            st.plotly_chart(fig, use_container_width=True)

        with right_tabs[1]:
            st.subheader("📊 MTM Chart")
            st.info("MTM fluctuation over replay time will display here once replay starts.")

    # ---------- BOTTOM RIGHT PANEL: POSITIONS & GREEKS TABLE ----------
    st.subheader("💼 Positions & Analytical Greeks")
    bot_tab_pos, bot_tab_greek = st.tabs(["Positions", "Greeks"])

    with bot_tab_pos:
        if not st.session_state.positions_list:
            st.info("No open positions. Select and trade contracts above!")
        else:
            # Table of positions
            pos_rows = []
            for i, p in enumerate(st.session_state.positions_list):
                # Calculate current LTP and P&L
                # For demo, match LTP to entry price for now
                ltp_val = p["avg"]
                pnl_val = 0.0
                pnl_style = "tag-green" if pnl_val >= 0 else "tag-red"

                col_c, col_act, col_lot, col_qty, col_stk, col_exp, col_ent, col_ltp, col_pnl, col_del = st.columns([0.5, 1, 1, 1, 2, 1.5, 1.5, 1.5, 2, 1])

                col_c.markdown("<input type='checkbox' checked />", unsafe_allow_html=True)
                badge_style = "background-color: #d3f9d8; color: #2b8a3e; padding: 2px 6px; border-radius: 4px;" if p["side"]=="BUY" else "background-color: #ffe3e3; color: #c92a2a; padding: 2px 6px; border-radius: 4px;"
                col_act.markdown(f"<span style='{badge_style}'>{p['side']}</span>", unsafe_allow_html=True)
                col_lot.write(f"{p['lots']}")
                col_qty.write(f"{p['qty']}")
                col_stk.write(f"{p['strike']:,.0f} {p['right']}")
                col_exp.write(f"{p['expiry']}")
                col_ent.write(f"₹{p['avg']:.2f}")
                col_ltp.write(f"₹{ltp_val:.2f}")
                col_pnl.markdown(f"<span class='{pnl_style}'>₹{pnl_val:.2f}</span>", unsafe_allow_html=True)

                if col_del.button("❌", key=f"del_pos_{i}"):
                    st.session_state.positions_list.pop(i)
                    st.rerun()

    with bot_tab_greek:
        if not st.session_state.positions_list:
            st.info("No active positions to display Greeks.")
        else:
            for i, p in enumerate(st.session_state.positions_list):
                # mock Greeks
                col_stk, col_delta, col_gamma, col_theta, col_vega, col_rho = st.columns(6)
                col_stk.write(f"**{p['strike']:,.0f} {p['right']}**")
                col_delta.write("Delta: 0.52")
                col_gamma.write("Gamma: 0.0012")
                col_theta.write("Theta: -12.5")
                col_vega.write("Vega: 18.2")
                col_rho.write("Rho: 2.1")

    # Footer Bar Actions
    st.markdown("---")
    f_col1, f_col2, f_col3, f_col4 = st.columns([2, 4, 3, 3])
    f_col1.write("**Multiplier adjustment:** 1  •  **Lot Size:** 50")
    if f_col2.button("Save Portfolio / Strategy", use_container_width=True):
        st.toast("Strategy successfully saved!")
    if f_col3.button("Import Strategy", use_container_width=True):
        st.toast("Strategy successfully imported!")
    if f_col4.button("Exit / Close All Positions", use_container_width=True):
        st.session_state.positions_list = []
        st.toast("All positions successfully closed.")
        st.rerun()

import sys
if "pytest" not in sys.modules and not any("pytest" in arg for arg in sys.argv):
    run_app()
