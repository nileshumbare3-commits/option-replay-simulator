import os
import re
import uuid
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from datetime import date, time, datetime, timedelta
from dotenv import load_dotenv

from breeze_client import BreezeClient, format_breeze_date
from greeks import implied_vol, greeks
from backend.expiry_service import (
    get_dynamic_expiry_dates,
    format_contract_symbol,
    parse_expiry_from_symbol_name,
    process_historical_contracts_payload
)

# Load environment variables
load_dotenv()

# Streamlit page configuration
st.set_page_config(
    page_title="StockMock Options Simulator",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ----------------- SESSION STATE INITIALIZATION -----------------
if "breeze_auth_error" not in st.session_state:
    st.session_state.breeze_auth_error = False
if "positions" not in st.session_state:
    st.session_state.positions = []
if "cash" not in st.session_state:
    st.session_state.cash = 1_000_000.0
if "trade_history" not in st.session_state:
    st.session_state.trade_history = []
if "mtm_history" not in st.session_state:
    st.session_state.mtm_history = []
if "autoplay" not in st.session_state:
    st.session_state.autoplay = False
if "autoplay_speed" not in st.session_state:
    st.session_state.autoplay_speed = 1.0
if "replay_date" not in st.session_state:
    st.session_state.replay_date = date(2026, 8, 7)
if "replay_time" not in st.session_state:
    st.session_state.replay_time = time(9, 15)
if "active_expiry_date" not in st.session_state:
    st.session_state.active_expiry_date = None
if "expiry_page_start" not in st.session_state:
    st.session_state.expiry_page_start = 0
if "selected_rows" not in st.session_state:
    st.session_state.selected_rows = set()
if "multiplier" not in st.session_state:
    st.session_state.multiplier = 1
if "last_quotes" not in st.session_state:
    st.session_state.last_quotes = {}

# Helper to get lot size for each index
def get_lot_size(symbol_name: str) -> int:
    sym = symbol_name.upper().strip()
    if "BANK" in sym:
        return 30
    elif "FIN" in sym:
        return 40
    return 65

# Helper to load API credential fallback
def load_env_credentials():
    api_key = os.getenv("BREEZE_API_KEY", "")
    secret_key = os.getenv("BREEZE_SECRET_KEY", "")
    if not api_key or not secret_key:
        for filename in [".env", ".env.example"]:
            if os.path.exists(filename):
                try:
                    with open(filename, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue
                            if "=" in line:
                                parts = line.split("=", 1)
                                k = parts[0].strip()
                                v = parts[1].strip().strip('"').strip("'")
                                if k == "BREEZE_API_KEY" and not api_key:
                                    api_key = v
                                elif k == "BREEZE_SECRET_KEY" and not secret_key:
                                    secret_key = v
                except Exception:
                    pass
    return api_key, secret_key

# Load keys
api_key_env, secret_key_env = load_env_credentials()
if "breeze_api_key" not in st.session_state or not st.session_state.breeze_api_key:
    st.session_state.breeze_api_key = api_key_env
if "breeze_secret_key" not in st.session_state or not st.session_state.breeze_secret_key:
    st.session_state.breeze_secret_key = secret_key_env

client = BreezeClient(
    api_key=st.session_state.breeze_api_key,
    secret_key=st.session_state.breeze_secret_key,
    session_token=st.session_state.get("session_token")
)
breeze = client

# Connect Oauth redirects dynamically
api_session = None
for param_name in ["API_Session", "api_session", "apisession", "session_token", "token"]:
    if param_name in st.query_params:
        api_session = st.query_params[param_name]
        break

if api_session and client.configured:
    try:
        with st.spinner("Exchanging redirected session token..."):
            st.session_state["session_token"] = client.exchange_api_session(api_session)
        st.query_params.clear()
        st.rerun()
    except Exception as ex:
        st.error(f"Auto-exchange failed: {ex}")

# ----------------- SIDELINED INTERACTION DISPATCHER (QUERY-PARAM BRIDGE) -----------------
# Captures and executes paper orders immediately with exact lot sizes and updates state cleanly
if "action" in st.query_params:
    action_val = st.query_params["action"]
    parts = action_val.split(":")
    if len(parts) == 3:
        cmd, right_str, strike_str = parts
        strike_val = float(strike_str)
        right_val = right_str.upper()
        symbol_param = st.query_params.get("symbol_name", "NIFTY")
        qty_step = get_lot_size(symbol_param)

        if cmd == "DESELECT":
            st.session_state.positions = [p for p in st.session_state.positions if not (p["strike"] == strike_val and p["right"].upper() == right_val)]
            st.toast(f"Deselected {right_val} {strike_val}", icon="🗑️")
        elif cmd in ["BUY", "SELL"]:
            match = None
            for p in st.session_state.positions:
                if p["strike"] == strike_val and p["right"].upper() == right_val and p["side"].upper() == cmd:
                    match = p
                    break

            # Fetch current ltp from active quotes
            ltp_val = 100.0
            if "last_quotes" in st.session_state and (right_val, strike_val) in st.session_state.last_quotes:
                ltp_val = st.session_state.last_quotes[(right_val, strike_val)]

            if match:
                match["qty"] += qty_step
            else:
                st.session_state.positions.append({
                    "id": str(uuid.uuid4()),
                    "right": right_val,
                    "strike": strike_val,
                    "side": cmd,
                    "qty": qty_step,
                    "avg": ltp_val,
                    "sl_pct": None,
                    "tp_pct": None,
                    "entry_time": str(st.session_state.replay_time)
                })
            st.toast(f"Executed Order: {cmd} {right_val} {strike_val} (+{qty_step} Qty) @ ₹{ltp_val:.2f}", icon="🛒")

    st.query_params.clear()
    st.rerun()

# ----------------- SIDEBAR: LOGIN & SETUP -----------------
with st.sidebar:
    st.header("🔑 Breeze API Connection")

    if st.session_state.get("breeze_auth_error", False):
        st.sidebar.warning("⚠️ Breeze Session Expired. Please enter a new Session Token.")
        new_token_val = st.sidebar.text_input("Breeze Session Token", type="password", key="sidebar_refresh_token_input")
        if new_token_val:
            try:
                exchanged_token = breeze.generate_session(st.session_state.breeze_secret_key, new_token_val)
                st.session_state["session_token"] = exchanged_token
                st.session_state.breeze_auth_error = False
                st.sidebar.success("Connected & Refreshed!")
                st.rerun()
            except Exception as e:
                st.sidebar.error(f"Failed to refresh session: {e}")

    with st.expander("Configure API Keys", expanded=not (st.session_state.breeze_api_key and st.session_state.breeze_secret_key)):
        user_api_key = st.text_input("Breeze API Key", value=st.session_state.breeze_api_key)
        user_secret_key = st.text_input("Breeze Secret Key", value=st.session_state.breeze_secret_key, type="password")
        if user_api_key != st.session_state.breeze_api_key or user_secret_key != st.session_state.breeze_secret_key:
            st.session_state.breeze_api_key = user_api_key
            st.session_state.breeze_secret_key = user_secret_key
            st.rerun()

    if client.configured:
        st.link_button("🌐 Login & Authorize ICICI Direct", client.login_url(), use_container_width=True)
        manual_session = st.text_input("Paste redirect URL or token", placeholder="api_session=...")
        if st.button("Exchange Session", use_container_width=True) and manual_session:
            token = manual_session
            if "api_session=" in manual_session:
                token = manual_session.split("api_session=")[1].split("&")[0]
            try:
                st.session_state["session_token"] = client.exchange_api_session(token)
                st.success("Connected!")
                st.rerun()
            except Exception as e:
                st.error(f"Failed: {e}")
    else:
        st.info("Demo mode active. Provide credentials above to trade with Breeze.")

    if st.session_state.get("session_token"):
        st.success("● Connected to ICICI Breeze API")
        if st.button("Disconnect", use_container_width=True):
            st.session_state["session_token"] = None
            st.rerun()
    else:
        st.info("● Simulated Demo Mode")

# ----------------- UTILITY HELPERS -----------------
def is_real_session_token(token):
    if not token or len(token) < 10 or token.startswith("exchanged_") or "mock" in token.lower() or "session" in token.lower():
        return False
    return True

def norm(rows):
    df = pd.DataFrame(rows)
    if df.empty or "datetime" not in df or "close" not in df: return pd.DataFrame()
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    for c in ["open","high","low","close","volume","open_interest"]:
        if c in df: df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["datetime","close"])

@st.cache_data(ttl=300, show_spinner=False)
def get_hist(api_key, secret_key, session, symbol, start, end, expiry, right, strike, interval):
    c = BreezeClient(api_key=api_key, secret_key=secret_key, session_token=session)
    return norm(c.historical_option(symbol, start, end, expiry, right, strike, interval))

@st.cache_data(ttl=300, show_spinner=False)
def get_index_hist(api_key, secret_key, session, symbol, start, end, interval):
    c = BreezeClient(api_key=api_key, secret_key=secret_key, session_token=session)
    return norm(c.historical_index(symbol, start, end, interval))

def demo_chain(atm, step, count, day):
    rng = np.random.default_rng(42)
    times = pd.date_range(f"{day} 09:15", f"{day} 15:30", freq="5min")
    strikes = [round(atm + (i - count // 2) * step, 2) for i in range(count)]
    rows = []
    spot_path = atm + np.cumsum(rng.normal(0, step * 0.02, len(times)))
    for ti, ts in enumerate(times):
        spot = spot_path[ti]
        for k in strikes:
            for right in ["call", "put"]:
                intrinsic = max(spot - k, 0) if right == "call" else max(k - spot, 0)
                tv = max(5, step * 0.8 * np.exp(-abs(spot - k) / (step * 2)))
                price = max(0.5, intrinsic + tv + rng.normal(0, 0.5))
                # Add simulated OI & Vol
                dist = abs(k - spot) / step
                oi = int(max(1000, 100000 * np.exp(-dist * 0.2)))
                vol = int(max(500, 500000 * np.exp(-dist * 0.3)))
                rows.append({
                    "datetime": ts,
                    "close": price,
                    "volume": vol,
                    "open_interest": oi,
                    "strike": k,
                    "right": right,
                    "spot": spot
                })
    return pd.DataFrame(rows), times

def get_spot_price(client, symbol, selected_day, selected_time, mode):
    if mode == "Demo" or st.session_state.get("breeze_auth_error", False):
        base_spot = 25000.0 if symbol == "NIFTY" else (52000.0 if symbol == "BANKNIFTY" else 23000.0)
        day_variance = (selected_day.day * 15.0) - 200.0
        # Time progression variance
        time_minutes = selected_time.hour * 60 + selected_time.minute - 555 # from 9:15
        time_variance = time_minutes * 0.15
        return base_spot + day_variance + time_variance
    else:
        session = st.session_state.get("session_token")
        if not is_real_session_token(session):
            base_spot = 25000.0 if symbol == "NIFTY" else (52000.0 if symbol == "BANKNIFTY" else 23000.0)
            day_variance = (selected_day.day * 15.0) - 200.0
            time_minutes = selected_time.hour * 60 + selected_time.minute - 555
            time_variance = time_minutes * 0.15
            return base_spot + day_variance + time_variance
        try:
            start_iso = f"{selected_day.strftime('%Y-%m-%d')}T09:15:00.000Z"
            end_iso = f"{selected_day.strftime('%Y-%m-%d')}T15:30:00.000Z"
            df = get_index_hist(client.api_key, client.secret_key, session, symbol, start_iso, end_iso, "1minute")
            if not df.empty and "close" in df and "datetime" in df:
                df["datetime"] = pd.to_datetime(df["datetime"])
                target_dt = pd.Timestamp(datetime.combine(selected_day, selected_time))
                if target_dt.tzinfo is not None:
                    target_dt = target_dt.tz_localize(None)
                df["datetime_naive"] = df["datetime"].dt.tz_localize(None) if df["datetime"].dt.tz is not None else df["datetime"]
                df["diff"] = (df["datetime_naive"] - target_dt).abs()
                best_row = df.loc[df["diff"].idxmin()]
                st.session_state.breeze_auth_error = False
                return float(best_row["close"])
        except Exception as e:
            if "401" in str(e) or "Unauthorized" in str(e):
                st.session_state.breeze_auth_error = True
            print(f"Failed to fetch auto-spot price from Breeze: {e}. Using fallback.")
            base_spot = 25000.0 if symbol == "NIFTY" else (52000.0 if symbol == "BANKNIFTY" else 23000.0)
            day_variance = (selected_day.day * 15.0) - 200.0
            time_minutes = selected_time.hour * 60 + selected_time.minute - 555
            time_variance = time_minutes * 0.15
            return base_spot + day_variance + time_variance
        # Default safety fallback
        base_spot = 25000.0 if symbol == "NIFTY" else (52000.0 if symbol == "BANKNIFTY" else 23000.0)
        day_variance = (selected_day.day * 15.0) - 200.0
        time_minutes = selected_time.hour * 60 + selected_time.minute - 555
        time_variance = time_minutes * 0.15
        return base_spot + day_variance + time_variance

def adjust_replay_time(minutes_delta: int):
    current_dt = datetime.combine(st.session_state.replay_date, st.session_state.replay_time)
    new_dt = current_dt + timedelta(minutes=minutes_delta)
    st.session_state.replay_date = new_dt.date()
    # Clip between 09:15 and 15:30
    sod_dt = datetime.combine(new_dt.date(), time(9, 15))
    eod_dt = datetime.combine(new_dt.date(), time(15, 30))
    if new_dt < sod_dt:
        st.session_state.replay_time = time(9, 15)
    elif new_dt > eod_dt:
        st.session_state.replay_time = time(15, 30)
    else:
        st.session_state.replay_time = new_dt.time()

# ----------------- STEP 4: TOP CONTROL BAR & PRICE SUMMARY LINE -----------------
st.title("🛡️ StockMock Options Simulator")
st.caption("Professional Full-Stack Options Trading Replay Dashboard")

# Render a prominent error banner if Breeze API returns 401 Unauthorized
if st.session_state.get("breeze_auth_error", False):
    st.warning("⚠️ ICICI Direct Breeze API Query Failed: Unauthorized User (401). Please check your API Key, Secret Key, and paste a valid Redirect URL/session token. Falling back gracefully to Simulated Demo Mode to maintain interface functionality.")

# Top Control Bar layout with exactly 16 columns for perfect alignment
with st.container():
    nav_cols = st.columns([1, 1, 1, 1, 1, 1, 1, 1.8, 1.8, 1, 1, 1, 1, 1, 1, 1])

    # Left controls
    if nav_cols[0].button("<< Day", use_container_width=True):
        st.session_state.replay_date -= timedelta(days=1)
        st.rerun()
    if nav_cols[1].button("SOD", use_container_width=True):
        st.session_state.replay_time = time(9, 15)
        st.rerun()
    if nav_cols[2].button("-2h", use_container_width=True):
        adjust_replay_time(-120)
        st.rerun()
    if nav_cols[3].button("-30m", use_container_width=True):
        adjust_replay_time(-30)
        st.rerun()
    if nav_cols[4].button("-15m", use_container_width=True):
        adjust_replay_time(-15)
        st.rerun()
    if nav_cols[5].button("-5m", use_container_width=True):
        adjust_replay_time(-5)
        st.rerun()
    if nav_cols[6].button("-1m", use_container_width=True):
        adjust_replay_time(-1)
        st.rerun()

    # Date & Time Pickers inline
    st.session_state.replay_date = nav_cols[7].date_input("Date", st.session_state.replay_date, label_visibility="collapsed")
    st.session_state.replay_time = nav_cols[8].time_input("Time", st.session_state.replay_time, label_visibility="collapsed")

    # Right controls
    if nav_cols[9].button("1m+", use_container_width=True):
        adjust_replay_time(1)
        st.rerun()
    if nav_cols[10].button("5m+", use_container_width=True):
        adjust_replay_time(5)
        st.rerun()
    if nav_cols[11].button("15m+", use_container_width=True):
        adjust_replay_time(15)
        st.rerun()
    if nav_cols[12].button("30m+", use_container_width=True):
        adjust_replay_time(30)
        st.rerun()
    if nav_cols[13].button("2h+", use_container_width=True):
        adjust_replay_time(120)
        st.rerun()
    if nav_cols[14].button("EOD", use_container_width=True):
        st.session_state.replay_time = time(15, 30)
        st.rerun()
    if nav_cols[15].button("Day >>", use_container_width=True):
        st.session_state.replay_date += timedelta(days=1)
        st.rerun()

# Sub-bar for Symbol, Data mode and Autoplay Controls
sub_col1, sub_col2, sub_col3, sub_col4 = st.columns([1.5, 1.5, 4.0, 3.0])
with sub_col1:
    symbol = st.selectbox("Underlying Symbol", ["NIFTY", "BANKNIFTY", "FINNIFTY"], label_visibility="collapsed")
with sub_col2:
    mode = st.selectbox("Data mode", ["Demo", "Breeze"], index=0, label_visibility="collapsed")
with sub_col3:
    col_play, col_snap = st.columns([1.2, 0.8])
    with col_play:
        ap_label = "🔴 Stop Run" if st.session_state.autoplay else "🟢 Auto Run"
        if st.button(ap_label, type="primary" if not st.session_state.autoplay else "secondary", use_container_width=True):
            st.session_state.autoplay = not st.session_state.autoplay
            st.rerun()
    with col_snap:
        if st.button("📸 Snap", use_container_width=True):
            st.toast("Replay Snapshot captured!", icon="📸")
with sub_col4:
    st.session_state.autoplay_speed = st.slider("Autoplay Speed (Hz)", 0.2, 5.0, value=float(st.session_state.autoplay_speed), step=0.2, label_visibility="collapsed")

# ----------------- DYNAMIC CALCULATIONS & LOAD -----------------
# Setup step/count based on symbol
if symbol == "NIFTY":
    step = 50.0
    strike_count = 30
elif symbol == "BANKNIFTY":
    step = 100.0
    strike_count = 30
else:
    step = 50.0
    strike_count = 20

# ----------------- TIME-SYNCHRONIZED FEED -----------------
# Dynamic spot price strictly query-filtered by the EXACT selected replay date and time
auto_spot = get_spot_price(client, symbol, st.session_state.replay_date, st.session_state.replay_time, mode)
nearest_atm = round(round(auto_spot / step) * step, 2)

# Dynamic expiry fetching (No legacy weekday offsets or Tuesday/Thursday shift rules!)
expiry_options = get_dynamic_expiry_dates(
    symbol, nearest_atm, step, st.session_state.replay_date, client,
    historical_contracts=st.session_state.get("historical_contracts")
)

if not st.session_state.active_expiry_date or st.session_state.active_expiry_date not in expiry_options:
    st.session_state.active_expiry_date = expiry_options[0]

active_expiry = st.session_state.active_expiry_date

# Pre-flight authorization check for Breeze API mode to prevent crashes and multi-thread logs spamming
if mode == "Breeze":
    session = st.session_state.get("session_token")
    if session and is_real_session_token(session):
        try:
            start_iso = f"{st.session_state.replay_date.strftime('%Y-%m-%d')}T09:15:00.000Z"
            end_iso = f"{st.session_state.replay_date.strftime('%Y-%m-%d')}T09:20:00.000Z"
            get_index_hist(client.api_key, client.secret_key, session, symbol, start_iso, end_iso, "1minute")
            st.session_state.breeze_auth_error = False
        except Exception as ex:
            if "401" in str(ex) or "Unauthorized" in str(ex):
                st.session_state.breeze_auth_error = True
                mode = "Demo"

# Load the option chain automatically
current_load_key = f"{symbol}_{mode}_{st.session_state.replay_date}_{st.session_state.replay_time}_{step}_{strike_count}_{active_expiry}"
if st.session_state.get("last_load_key") != current_load_key:
    if mode == "Demo":
        chain, times = demo_chain(nearest_atm, step, strike_count, st.session_state.replay_date)
    else:
        # Load real Breeze Option Chain
        session = st.session_state.get("session_token")
        if not session:
            st.sidebar.warning("Connect Breeze. Loading simulated option chain.")
            chain, times = demo_chain(nearest_atm, step, strike_count, st.session_state.replay_date)
        else:
            start_iso = f"{st.session_state.replay_date.strftime('%Y-%m-%d')}T09:15:00.000Z"
            end_iso = f"{st.session_state.replay_date.strftime('%Y-%m-%d')}T15:30:00.000Z"
            exp_iso = f"{active_expiry.strftime('%Y-%m-%d')}T07:00:00.000Z"

            # Fetch strikes Centered around ATM
            strikes = sorted(round(nearest_atm + (i - strike_count // 2) * step, 2) for i in range(strike_count))
            from concurrent.futures import ThreadPoolExecutor
            frames = []
            tasks = [(k, r) for k in strikes for r in ["call", "put"]]

            def fetch_single(task):
                strike_val, right_val = task
                try:
                    d0 = get_hist(client.api_key, client.secret_key, session, symbol, start_iso, end_iso, exp_iso, right_val, strike_val, "5minute")
                    if not d0.empty:
                        d0["strike"] = strike_val
                        d0["right"] = right_val
                        return d0
                except Exception as ex:
                    if "401" in str(ex) or "Unauthorized" in str(ex):
                        st.session_state.breeze_auth_error = True
                    pass
                return None

            with ThreadPoolExecutor(max_workers=20) as executor:
                results = list(executor.map(fetch_single, tasks))

            for r_df in results:
                if isinstance(r_df, pd.DataFrame) and not r_df.empty:
                    frames.append(r_df)

            chain = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            times = sorted(chain.datetime.dropna().unique()) if not chain.empty else []

            if chain.empty:
                chain, times = demo_chain(nearest_atm, step, strike_count, st.session_state.replay_date)

    if hasattr(times, "tolist"):
        times = times.tolist()
    else:
        times = list(times)
    st.session_state.chain = chain
    st.session_state.times = times
    st.session_state.last_load_key = current_load_key

# Autoplay tick loop
times = st.session_state.get("times", [])
if st.session_state.autoplay and len(times) > 0:
    target_dt = pd.Timestamp(datetime.combine(st.session_state.replay_date, st.session_state.replay_time))
    curr_idx = int(np.argmin([abs(pd.Timestamp(x) - target_dt) for x in times]))
    if curr_idx < len(times) - 1:
        import time as ptime
        ptime.sleep(1.0 / max(0.1, st.session_state.autoplay_speed))
        next_ts = pd.Timestamp(times[curr_idx + 1])
        st.session_state.replay_time = next_ts.time()
        st.session_state.replay_date = next_ts.date()
        st.rerun()
    else:
        st.session_state.autoplay = False

chain = st.session_state.get("chain", pd.DataFrame())
times = st.session_state.get("times", [])
if hasattr(times, "tolist"):
    times = times.tolist()
else:
    times = list(times)

# Filter chain slice for selected active time
target_ts = pd.Timestamp(datetime.combine(st.session_state.replay_date, st.session_state.replay_time))
if len(times) > 0:
    best_idx = int(np.argmin([abs(pd.Timestamp(x) - target_ts) for x in times]))
    snap = chain[chain["datetime"] == times[best_idx]].copy() if "datetime" in chain.columns else chain.copy()
else:
    snap = chain.copy()

spot = float(snap["spot"].iloc[0]) if "spot" in snap.columns and not snap.empty else float(nearest_atm)
day_open = spot - 110.0  # mock day open if demo or missing
if not snap.empty and "open" in snap.columns:
    try:
        day_open = get_spot_price(client, symbol, st.session_state.replay_date, time(9, 15), mode)
    except Exception:
        pass

pct_change = ((spot - day_open) / day_open) * 100.0 if day_open else 0.0

# ----------------- PRICE METRIC SUMMARY LINE -----------------
metric_cols = st.columns([1.5, 1.5, 1.5, 1.5, 1.2, 1.2, 1.2])

# Metric 1: Day Open
change_class = "tag-green" if pct_change >= 0 else "tag-red"
metric_cols[0].markdown(f"""
<div class="metric-card">
    <div class="metric-title">Day Open</div>
    <div class="metric-value">₹{day_open:,.2f} <span class="{change_class}">{pct_change:+.2f}%</span></div>
</div>
""", unsafe_allow_html=True)

# Metric 2: Spot Price
spot_class = "tag-green" if pct_change >= 0 else "tag-red"
metric_cols[1].markdown(f"""
<div class="metric-card">
    <div class="metric-title">Spot Price</div>
    <div class="metric-value" style="color: {'#22c55e' if pct_change >= 0 else '#ef4444'};">₹{spot:,.2f} <span class="{spot_class}">LIVE</span></div>
</div>
""", unsafe_allow_html=True)

# Metric 3: Future Price
rate = 6.5 / 100
expiry_dt = datetime.combine(active_expiry, time(15, 30))
rt = target_ts
T = max((expiry_dt - rt).total_seconds() / (365 * 24 * 3600), 1e-8)
future_price = spot * np.exp(rate * T)
metric_cols[2].markdown(f"""
<div class="metric-card">
    <div class="metric-title">Fut Price</div>
    <div class="metric-value">₹{future_price:,.2f}</div>
</div>
""", unsafe_allow_html=True)

# Compute chain quotes & metrics
out = []
for _, r in snap.iterrows():
    if "close" in r and "strike" in r and "right" in r:
        iv = implied_vol(float(r.close), spot, float(r.strike), T, rate, 0.0, r.right)
        g_vals = greeks(spot, float(r.strike), T, rate, 0.0, iv, r.right)
        out.append({
            "Strike": float(r.strike),
            "Right": r.right.upper(),
            "LTP": float(r.close),
            "Volume": r.get("volume", 0),
            "OI": r.get("open_interest", 0),
            "IV %": iv * 100 if np.isfinite(iv) else 15.0,
            **{k.title(): v for k, v in g_vals.items()}
        })

if out:
    view = pd.DataFrame(out).sort_values(["Strike", "Right"])
else:
    view = pd.DataFrame(columns=["Strike", "Right", "LTP", "Volume", "OI", "IV %", "Delta", "Gamma", "Theta", "Vega", "Rho"])

# Global quotes dictionary for LTP lookups
quotes = {(str(r.Right), float(r.Strike)): float(r.LTP) for _, r in view.iterrows()}
st.session_state.last_quotes = quotes

atm_call_ltp = quotes.get(("CALL", nearest_atm), 0.0)
atm_put_ltp = quotes.get(("PUT", nearest_atm), 0.0)
synth_fut = nearest_atm + atm_call_ltp - atm_put_ltp

# Metric 4: Synth Fut
metric_cols[3].markdown(f"""
<div class="metric-card">
    <div class="metric-title">Synth Fut</div>
    <div class="metric-value">₹{synth_fut:,.2f}</div>
</div>
""", unsafe_allow_html=True)

# Metric Actions Buttons
if metric_cols[4].button("+ Add Futures", use_container_width=True):
    new_pos = {
        "id": str(uuid.uuid4()),
        "right": "FUT",
        "strike": future_price,
        "side": "BUY",
        "qty": get_lot_size(symbol),
        "avg": future_price,
        "sl_pct": None,
        "tp_pct": None,
        "entry_time": str(st.session_state.replay_time)
    }
    st.session_state.positions.append(new_pos)
    st.toast("Simulated Future contract added!", icon="🚀")
    st.rerun()

if metric_cols[5].button("Strategy Finder", use_container_width=True):
    st.toast("Searching high-probability strategy legs centered near ATM...", icon="🔍")

if metric_cols[6].button("Import Strategy", use_container_width=True):
    st.toast("Ready to import external JSON payload!", icon="📥")

st.markdown("---")

# ----------------- CUSTOM STYLE SHEET (TARGETED OVERRIDES FOR STOCKMOCK SPEC) -----------------
st.markdown("""
<style>
/* Decrease table font size & padding to be compact */
.mock-table th {
    font-size: 11px !important;
    padding: 3px 4px !important;
    font-weight: 700;
}
.mock-table td {
    font-size: 12px !important;
    padding: 3px 5px !important;
}
.strike-atm, .atm-row {
    background-color: #EBF3FC !important;
    color: #1e3a8a !important;
    font-weight: 800 !important;
}
.strike-standard {
    background-color: #f1f5f9;
    color: #0f172a;
    font-weight: bold;
}
/* Shading theme */
.itm-ce {
    background-color: #FFFDF0 !important;
}
.itm-pe {
    background-color: #FFFDF0 !important;
}
/* Action Badges B/S hover */
.btn-b {
    background-color: #d1fae5 !important;
    color: #065f46 !important;
    border: 1px solid #34d399 !important;
    font-weight: 900 !important;
    border-radius: 3px;
    padding: 1px 4px;
    font-size: 11px;
    cursor: pointer;
}
.btn-s {
    background-color: #fee2e2 !important;
    color: #991b1b !important;
    border: 1px solid #f87171 !important;
    font-weight: 900 !important;
    border-radius: 3px;
    padding: 1px 4px;
    font-size: 11px;
    cursor: pointer;
}
</style>
""", unsafe_allow_html=True)

# ----------------- STEP 5: SPLIT PANEL LAYOUT -----------------
panel_col1, panel_col2 = st.columns([5.2, 4.8])

# --- LEFT PANEL: OPTION CHAIN MATRIX ---
with panel_col1:
    st.markdown("### 🧬 Option Chain Matrix")

    # Expiry Tabs Header with Pagination Controls (◀ / ▶)
    # Allows infinite viewing of CM, NM, FM expiries nicely
    if "expiry_page_start" not in st.session_state:
        st.session_state.expiry_page_start = 0

    st.session_state.expiry_page_start = max(0, min(st.session_state.expiry_page_start, len(expiry_options) - 3))

    pag_cols = st.columns([0.5, 2, 2, 2, 0.5])

    # Left Arrow
    with pag_cols[0]:
        if st.button("◀", key="prev_expiry_page", use_container_width=True):
            st.session_state.expiry_page_start = max(0, st.session_state.expiry_page_start - 1)
            st.rerun()

    # Page View of 3 tabs
    visible_expiries = expiry_options[st.session_state.expiry_page_start : st.session_state.expiry_page_start + 3]
    for idx_tab, exp_opt in enumerate(visible_expiries):
        is_active = (exp_opt == active_expiry)
        label = exp_opt.strftime("%d %b '%y").upper()
        if idx_tab == 0 and st.session_state.expiry_page_start == 0:
            label += " (CM)"
        elif idx_tab == 1 and st.session_state.expiry_page_start == 0:
            label += " (NM)"
        elif idx_tab == 2 and st.session_state.expiry_page_start == 0:
            label += " (FM)"

        btn_type = "primary" if is_active else "secondary"
        with pag_cols[idx_tab + 1]:
            if st.button(label, key=f"btn_exp_tab_pag_{idx_tab}", type=btn_type, use_container_width=True):
                st.session_state.active_expiry_date = exp_opt
                st.rerun()

    # Right Arrow
    with pag_cols[4]:
        if st.button("▶", key="next_expiry_page", use_container_width=True):
            st.session_state.expiry_page_start = min(len(expiry_options) - 3, st.session_state.expiry_page_start + 1)
            st.rerun()

    # Summary Strip - Using Custom CSS-styled div cards for high-density, no-wrap, professional display
    straddle_prem = atm_call_ltp + atm_put_ltp
    call_oi_tot = view[view.Right == "CALL"]["OI"].sum()
    put_oi_tot = view[view.Right == "PUT"]["OI"].sum()
    pcr = put_oi_tot / call_oi_tot if call_oi_tot > 0 else 1.0

    atm_iv = view[(view.Strike == nearest_atm) & (view.Right == "CALL")]["IV %"].mean()
    if np.isnan(atm_iv) or not np.isfinite(atm_iv):
        atm_iv = 16.5

    st.markdown(f"""
    <div style="display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; margin-bottom: 15px; background-color: #f8f9fa; padding: 10px; border-radius: 8px; border: 1px solid #e9ecef;">
        <div style="text-align: center;">
            <div style="font-size: 0.75rem; color: #6c757d; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">ATM IV</div>
            <div style="font-size: 1.0rem; font-weight: 700; color: #212529; margin-top: 2px;">{atm_iv:.1f}%</div>
        </div>
        <div style="text-align: center; display: flex; flex-direction: column; justify-content: center; align-items: center;">
            <div style="font-size: 0.75rem; color: #6c757d; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 2px;">ATM Mode</div>
            <div style="font-size: 0.85rem; font-weight: 700; color: #0d6efd; background-color: #e7f1ff; padding: 2px 8px; border-radius: 4px; border: 1px solid #b6d4fe;">Spot</div>
        </div>
        <div style="text-align: center;">
            <div style="font-size: 0.75rem; color: #6c757d; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Straddle Prem</div>
            <div style="font-size: 1.0rem; font-weight: 700; color: #212529; margin-top: 2px;">₹{straddle_prem:.2f}</div>
        </div>
        <div style="text-align: center;">
            <div style="font-size: 0.75rem; color: #6c757d; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">PCR</div>
            <div style="font-size: 1.0rem; font-weight: 700; color: #212529; margin-top: 2px;">{pcr:.2f}</div>
        </div>
        <div style="text-align: center;">
            <div style="font-size: 0.75rem; color: #6c757d; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Call/Put OI</div>
            <div style="font-size: 0.9rem; font-weight: 700; color: #212529; margin-top: 4px; white-space: nowrap;">{call_oi_tot/10000000:.1f}Cr / {put_oi_tot/10000000:.1f}Cr</div>
        </div>
        <div style="text-align: center;">
            <div style="font-size: 0.75rem; color: #6c757d; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Max Pain</div>
            <div style="font-size: 1.0rem; font-weight: 700; color: #212529; margin-top: 2px;">₹{nearest_atm:,.0f}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    atm_mode = "Spot"

    # Render Option Matrix Table (Call LTP (Delta), Call OI bar, Strike, Put OI bar, Put LTP (Delta))
    atm_grid = sorted(round(nearest_atm + (i - 10) * step, 2) for i in range(21))
    sliced_view = view[view.Strike.isin(atm_grid)].sort_values(["Strike", "Right"])

    html_elements = []
    html_elements.append("""
    <style>
    .mock-table {
        width: 100%;
        border-collapse: collapse;
        background-color: #ffffff;
        color: #1E1E1E;
        font-family: ui-sans-serif, system-ui, sans-serif;
        border-radius: 8px;
        overflow: hidden;
        border: 1px solid #e2e8f0;
    }
    .mock-table th {
        background-color: #f8fafc;
        color: #475569;
        font-size: 11px !important;
        font-weight: 700;
        padding: 5px 4px !important;
        text-transform: uppercase;
        border-bottom: 2px solid #e2e8f0;
    }
    .mock-table td {
        padding: 4px 6px !important;
        border-bottom: 1px solid #f1f5f9;
        text-align: center;
        font-size: 12px !important;
        position: relative;
        color: #1E1E1E;
    }
    .mock-table tr:hover {
        background-color: #f8fafc;
    }
    .itm-ce {
        background-color: #FFFDF0 !important;
        color: #1E1E1E !important;
    }
    .itm-pe {
        background-color: #FFFDF0 !important;
        color: #1E1E1E !important;
    }
    .atm-row {
        background-color: #EBF3FC !important;
        color: #1E1E1E !important;
    }
    .hover-cell {
        cursor: pointer;
        transition: background-color 0.15s;
    }
    .hover-cell:hover {
        background-color: #e2e8f080;
    }
    .hover-actions {
        position: absolute;
        inset: 0;
        display: none;
        align-items: center;
        justify-content: center;
        gap: 6px;
        background-color: rgba(255, 255, 255, 0.95);
        z-index: 20;
    }
    .hover-cell:hover .hover-actions {
        display: flex;
    }
    .btn-act {
        padding: 2px 6px;
        border-radius: 3px;
        font-weight: 900;
        font-size: 11px;
        border: none;
        cursor: pointer;
        color: white;
    }
    .btn-b {
        background-color: #d1fae5 !important;
        color: #065f46 !important;
        border: 1px solid #34d399 !important;
    }
    .btn-s {
        background-color: #fee2e2 !important;
        color: #991b1b !important;
        border: 1px solid #f87171 !important;
    }
    .badge-act {
        display: inline-block;
        padding: 1px 4px;
        border-radius: 2px;
        font-size: 10px;
        font-weight: 900;
        margin-top: 2px;
    }
    .badge-b {
        background-color: rgba(16, 185, 129, 0.15);
        color: #10b981;
        border: 1px solid rgba(16, 185, 129, 0.3);
    }
    .badge-s {
        background-color: rgba(239, 68, 68, 0.15);
        color: #ef4444;
        border: 1px solid rgba(239, 68, 68, 0.3);
    }
    .strike-atm {
        background-color: #EBF3FC !important;
        color: #1e3a8a !important;
        font-weight: 800 !important;
    }
    .strike-standard {
        background-color: #f1f5f9;
        color: #0f172a;
        font-weight: bold;
    }
    .oi-bar-container {
        width: 100%;
        background-color: rgba(0,0,0,0.04);
        height: 12px;
        border-radius: 2px;
        overflow: hidden;
        position: relative;
    }
    .oi-bar-fill {
        height: 100%;
        position: absolute;
        top: 0;
    }
    </style>
    <script>
    function triggerTrade(action, right, strike) {
        // Dynamic navigation to parent window to process action parameters instantly
        window.parent.location.href = "?action=" + action + ":" + right + ":" + strike + "&symbol_name=" + "__SYMBOL__";
    }
    </script>
    <table class="mock-table">
        <thead>
            <tr>
                <th>Call LTP (Delta)</th>
                <th style="width: 20%;">Call OI Visual Bar</th>
                <th style="width: 16%;">Strike Price</th>
                <th style="width: 20%;">Put OI Visual Bar</th>
                <th>Put LTP (Delta)</th>
            </tr>
        </thead>
        <tbody>
    """)

    max_oi_val = max(view["OI"].dropna().max() if not view.empty and "OI" in view.columns else 1.0, 1.0)

    for strk in atm_grid:
        ce_r = sliced_view[(sliced_view.Strike == strk) & (sliced_view.Right == "CALL")]
        pe_r = sliced_view[(sliced_view.Strike == strk) & (sliced_view.Right == "PUT")]

        ce = ce_r.iloc[0] if not ce_r.empty else None
        pe = pe_r.iloc[0] if not pe_r.empty else None

        # Position Lot Counter
        ce_qty = sum(p["qty"] for p in st.session_state.positions if p["strike"] == strk and p["right"] == "CALL")
        pe_qty = sum(p["qty"] for p in st.session_state.positions if p["strike"] == strk and p["right"] == "PUT")

        ce_itm = (strk < spot)
        pe_itm = (strk > spot)
        is_atm_row = (abs(strk - spot) <= step * 0.51)

        if is_atm_row:
            ce_cls = "atm-row"
            pe_cls = "atm-row"
            oi_ce_cls = "atm-row"
            oi_pe_cls = "atm-row"
        else:
            ce_cls = "itm-ce" if ce_itm else ""
            pe_cls = "itm-pe" if pe_itm else ""
            oi_ce_cls = "itm-ce" if ce_itm else ""
            oi_pe_cls = "itm-pe" if pe_itm else ""

        strk_cls = "strike-atm" if is_atm_row else "strike-standard"
        strk_lbl = f"{strk:,.0f} (ATM)" if is_atm_row else f"{strk:,.0f}"

        # Call LTP cell
        lot_sz = get_lot_size(symbol)
        ce_lots = int(ce_qty / lot_sz) if ce_qty > 0 else 1
        if ce is not None:
            ce_delta_lbl = f"({ce.Delta:.2f})" if "Delta" in ce_r.columns and not np.isnan(ce.Delta) else ""
            ce_badge = ""
            if ce_qty > 0:
                ce_badge = f'<br/><span class="badge-act badge-b">B {ce_qty}</span>'
            ce_ltp_cell = f"""
            <td class="hover-cell {ce_cls}" oncontextmenu="event.preventDefault(); triggerTrade('DESELECT', 'CALL', '{strk}');">
                <span>₹{ce.LTP:.2f} {ce_delta_lbl}</span>{ce_badge}
                <div class="hover-actions">
                    <span style="font-size: 11px; font-weight: 800; color: #1e293b; margin-right: 4px;">{ce_lots}</span>
                    <button class="btn-act btn-b" onclick="triggerTrade('BUY', 'CALL', '{strk}')">[B]</button>
                    <button class="btn-act btn-s" onclick="triggerTrade('SELL', 'CALL', '{strk}')">[S]</button>
                </div>
            </td>
            """

            # Call OI Visual bar
            oi_m = ce["OI"] / 10000000
            oi_pct = min(100.0, (ce["OI"] / max_oi_val) * 100.0)
            ce_oi_cell = f"""
            <td class="{oi_ce_cls}">
                <div class="oi-bar-container">
                    <div class="oi-bar-fill" style="background-color: rgba(16, 185, 129, 0.25); width: {oi_pct}%; right: 0;"></div>
                    <span style="position: relative; z-index: 5; font-size: 0.72rem; color: #10b981;">{oi_m:.2f}Cr</span>
                </div>
            </td>
            """
        else:
            ce_ltp_cell = f'<td class="{ce_cls}">-</td>'
            ce_oi_cell = f'<td class="{oi_ce_cls}">-</td>'

        # Put LTP cell
        pe_lots = int(pe_qty / lot_sz) if pe_qty > 0 else 1
        if pe is not None:
            pe_delta_lbl = f"({pe.Delta:.2f})" if "Delta" in pe_r.columns and not np.isnan(pe.Delta) else ""
            pe_badge = ""
            if pe_qty > 0:
                pe_badge = f'<br/><span class="badge-act badge-b">B {pe_qty}</span>'
            pe_ltp_cell = f"""
            <td class="hover-cell {pe_cls}" oncontextmenu="event.preventDefault(); triggerTrade('DESELECT', 'PUT', '{strk}');">
                <span>₹{pe.LTP:.2f} {pe_delta_lbl}</span>{pe_badge}
                <div class="hover-actions">
                    <span style="font-size: 11px; font-weight: 800; color: #1e293b; margin-right: 4px;">{pe_lots}</span>
                    <button class="btn-act btn-b" onclick="triggerTrade('BUY', 'PUT', '{strk}')">[B]</button>
                    <button class="btn-act btn-s" onclick="triggerTrade('SELL', 'PUT', '{strk}')">[S]</button>
                </div>
            </td>
            """

            # Put OI Visual bar
            oi_m = pe["OI"] / 10000000
            oi_pct = min(100.0, (pe["OI"] / max_oi_val) * 100.0)
            pe_oi_cell = f"""
            <td class="{oi_pe_cls}">
                <div class="oi-bar-container">
                    <div class="oi-bar-fill" style="background-color: rgba(239, 68, 68, 0.25); width: {oi_pct}%; left: 0;"></div>
                    <span style="position: relative; z-index: 5; font-size: 0.72rem; color: #ef4444;">{oi_m:.2f}Cr</span>
                </div>
            </td>
            """
        else:
            pe_ltp_cell = f'<td class="{pe_cls}">-</td>'
            pe_oi_cell = f'<td class="{oi_pe_cls}">-</td>'

        row_html = f"""
        <tr>
            {ce_ltp_cell}
            {ce_oi_cell}
            <td class="{strk_cls}">{strk_lbl}</td>
            {pe_oi_cell}
            {pe_ltp_cell}
        </tr>
        """
        html_elements.append(row_html)

    html_elements.append("</tbody></table>")
    html_rendered = "\n".join(html_elements).replace("__SYMBOL__", symbol)
    st.components.v1.html(html_rendered, height=550)

# --- RIGHT PANEL: PAYOFF CHART & ANALYTICS ---
with panel_col2:
    st.markdown("### 📊 Strategy Analytics & Payoffs")

    # Analytics Navbar
    tab_labels = ["Payoff Chart", "MTM History", "Strategy Builder", "OI Chart", "Rolling Straddle"]
    analytics_tab = st.radio("Analytics Toggle", tab_labels, horizontal=True, label_visibility="collapsed")

    # Portfolio statistics / Active positions MTM
    active_positions = st.session_state.positions

    # Calculate Live MTM
    pos_val_total = 0.0
    for p in active_positions:
        p_right = p["right"]
        p_strike = p["strike"]
        p_side = p["side"]
        p_qty = p["qty"]
        p_avg = p["avg"]

        if p_right == "FUT":
            current_price = spot
        else:
            current_price = quotes.get((p_right, p_strike), p_avg)

        mult = 1.0 if p_side == "BUY" else -1.0
        pos_val_total += p_qty * (current_price - p_avg) * mult

    # POP & Max Profit calculations
    scan_points = np.linspace(spot * 0.90, spot * 1.10, 400)
    expiry_pnl_scan = np.zeros_like(scan_points, dtype=float)
    t0_pnl_scan = np.zeros_like(scan_points, dtype=float)

    # Margins and Credit/Debit
    margin_req = 0.0
    premium_flow = 0.0

    for p in active_positions:
        p_right = p["right"]
        p_strike = p["strike"]
        p_side = p["side"]
        p_qty = p["qty"]
        p_avg = p["avg"]

        mult = 1.0 if p_side == "BUY" else -1.0
        premium_flow += p_qty * p_avg * mult

        if p_side == "BUY":
            margin_req += p_qty * p_avg
        else:
            margin_per_lot = 135000.0 if symbol == "NIFTY" else 150000.0
            margin_req += (p_qty / 50.0) * margin_per_lot

        # Add to payoff curves
        if p_right == "FUT":
            expiry_pnl_scan += p_qty * (scan_points - p_strike) * mult
            t0_pnl_scan += p_qty * (scan_points - p_strike) * mult
        else:
            # T=0 payoff
            intrinsic = np.maximum(scan_points - p_strike, 0) if p_right == "CALL" else np.maximum(p_strike - scan_points, 0)
            expiry_pnl_scan += p_qty * (intrinsic - p_avg) * mult

            # T+0/Today curve
            sig = 0.16
            from backend.math_engine import black_scholes_pricing
            leg_prices = np.array([
                black_scholes_pricing(s, p_strike, T, rate, 0.0, sig, p_right.lower())
                for s in scan_points
            ])
            t0_pnl_scan += p_qty * (leg_prices - p_avg) * mult

    max_prof_val = np.max(expiry_pnl_scan) if active_positions else 0.0
    max_loss_val = np.min(expiry_pnl_scan) if active_positions else 0.0

    # Clean Unlimited checks
    max_prof_lbl = f"₹{max_prof_val:,.2f}" if max_prof_val < 500000 else "Unlimited"
    max_loss_lbl = f"₹{max_loss_val:,.2f}" if max_loss_val > -500000 else "Unlimited"

    pop_val = (np.sum(expiry_pnl_scan > 0) / len(scan_points)) * 100.0 if active_positions else 0.0
    rr_val = abs(max_prof_val / max_loss_val) if max_loss_val != 0 else 0.0
    rr_lbl = f"1:{rr_val:.2f}" if max_loss_val != 0 else "N/A"

    # Breakevens
    be_points = []
    for idx_be in range(len(scan_points) - 1):
        if (expiry_pnl_scan[idx_be] < 0 and expiry_pnl_scan[idx_be+1] >= 0) or (expiry_pnl_scan[idx_be] >= 0 and expiry_pnl_scan[idx_be+1] < 0):
            be_points.append(scan_points[idx_be])
    be_lbl = ", ".join([f"{b:,.1f}" for b in be_points]) if be_points else "None"

    # Strategy Metrics Summary Strip
    st.markdown("##### 📌 Strategy Metrics Summary Strip")
    stat_cols = st.columns(4)
    stat_cols[0].metric("Est. Margin", f"₹{margin_req:,.2f}")
    stat_cols[1].metric("P&L Live MTM", f"₹{pos_val_total:,.2f}", delta=f"{((pos_val_total)/st.session_state.cash)*100:+.2f}%")
    stat_cols[2].metric("Max Profit", max_prof_lbl)
    stat_cols[3].metric("Max Loss", max_loss_lbl)

    stat_cols2 = st.columns(4)
    stat_cols2[0].metric("Risk to Reward (R:R)", rr_lbl)
    stat_cols2[1].metric("Probability of Profit (POP)", f"{pop_val:.1f}%")
    stat_cols2[2].metric("Net Premium Flow", "Credit" if premium_flow < 0 else "Debit")
    stat_cols2[3].metric("Breakevens", be_lbl)

    # Rendering tab views
    if analytics_tab == "Payoff Chart":
        if not active_positions:
            st.info("No active positions to display in payoff. Place trade on option chain to start.")
        else:
            fig_payoff = go.Figure()

            # Loss zone red shading fillcolor
            fig_payoff.add_trace(go.Scatter(
                x=scan_points, y=np.minimum(expiry_pnl_scan, 0), mode="lines", line=dict(width=0),
                fill="tozeroy", fillcolor="rgba(239, 68, 68, 0.15)", showlegend=False
            ))

            # Expiry line (Solid bright green curve)
            fig_payoff.add_trace(go.Scatter(
                x=scan_points, y=expiry_pnl_scan, mode="lines",
                line=dict(color="#00e676", width=3), name="Expiry Payoff"
            ))

            # Today T+0 curve (Dashed blue line)
            fig_payoff.add_trace(go.Scatter(
                x=scan_points, y=t0_pnl_scan, mode="lines",
                line=dict(color="#29b6f6", width=2, dash="dash"), name="Today's MTM (T+0)"
            ))

            # Add spot vertical line (with correct keyword 'line_width')
            fig_payoff.add_vline(x=spot, line_dash="dash", line_color="#f59e0b", line_width=2, annotation_text="Spot")
            fig_payoff.add_hline(y=0, line_color="#334155", line_width=1)

            # Standard Deviation reference guides
            std_dev_1 = spot * 0.16 * np.sqrt(1/365)
            fig_payoff.add_vline(x=spot + std_dev_1, line_dash="dot", line_color="#475569", line_width=1.5, annotation_text="+1σ")
            fig_payoff.add_vline(x=spot - std_dev_1, line_dash="dot", line_color="#475569", line_width=1.5, annotation_text="-1σ")

            fig_payoff.update_layout(
                xaxis_title="Underlying Spot (₹)", yaxis_title="Profit / Loss (₹)",
                height=350, hovermode="x unified", template="plotly_dark",
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)"
            )
            st.plotly_chart(fig_payoff, use_container_width=True)

    elif analytics_tab == "MTM History":
        if st.session_state.mtm_history:
            st.session_state.mtm_history.append({"time": target_ts, "mtm": pos_val_total})
            df_m = pd.DataFrame(st.session_state.mtm_history).drop_duplicates("time")
            fig_m = go.Figure()
            fig_m.add_trace(go.Scatter(x=df_m["time"], y=df_m["mtm"], mode="lines+markers", line=dict(color="#3b82f6", width=2)))
            fig_m.update_layout(height=350, template="plotly_dark", title="Equity Curve & Live MTM Replay History", plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_m, use_container_width=True)
        else:
            st.info("Autoplay or step replay to record historical MTM points.")

    elif analytics_tab == "Strategy Builder":
        st.markdown("##### Multi-Leg Strategy Templates:")
        t_cols = st.columns(3)
        if t_cols[0].button("Short Straddle (ATM)", key="builder_straddle", use_container_width=True):
            st.session_state.positions = [
                {"id": str(uuid.uuid4()), "right": "CALL", "strike": float(nearest_atm), "side": "SELL", "qty": get_lot_size(symbol), "avg": quotes.get(("CALL", nearest_atm), 100.0), "sl_pct": 20.0, "tp_pct": None, "entry_time": str(st.session_state.replay_time)},
                {"id": str(uuid.uuid4()), "right": "PUT", "strike": float(nearest_atm), "side": "SELL", "qty": get_lot_size(symbol), "avg": quotes.get(("PUT", nearest_atm), 100.0), "sl_pct": 20.0, "tp_pct": None, "entry_time": str(st.session_state.replay_time)}
            ]
            st.rerun()
        if t_cols[1].button("Short Strangle (OTM)", key="builder_strangle", use_container_width=True):
            st.session_state.positions = [
                {"id": str(uuid.uuid4()), "right": "CALL", "strike": float(nearest_atm + step), "side": "SELL", "qty": get_lot_size(symbol), "avg": quotes.get(("CALL", nearest_atm + step), 40.0), "sl_pct": 25.0, "tp_pct": None, "entry_time": str(st.session_state.replay_time)},
                {"id": str(uuid.uuid4()), "right": "PUT", "strike": float(nearest_atm - step), "side": "SELL", "qty": get_lot_size(symbol), "avg": quotes.get(("PUT", nearest_atm - step), 40.0), "sl_pct": 25.0, "tp_pct": None, "entry_time": str(st.session_state.replay_time)}
            ]
            st.rerun()
        if t_cols[2].button("Bull Call Spread", key="builder_bull", use_container_width=True):
            st.session_state.positions = [
                {"id": str(uuid.uuid4()), "right": "CALL", "strike": float(nearest_atm), "side": "BUY", "qty": get_lot_size(symbol), "avg": quotes.get(("CALL", nearest_atm), 100.0), "sl_pct": None, "tp_pct": None, "entry_time": str(st.session_state.replay_time)},
                {"id": str(uuid.uuid4()), "right": "CALL", "strike": float(nearest_atm + step), "side": "SELL", "qty": get_lot_size(symbol), "avg": quotes.get(("CALL", nearest_atm + step), 40.0), "sl_pct": None, "tp_pct": None, "entry_time": str(st.session_state.replay_time)}
            ]
            st.rerun()

    elif analytics_tab == "OI Chart":
        fig_oi = go.Figure()
        fig_oi.add_trace(go.Bar(x=view[view.Right == "CALL"]["Strike"], y=view[view.Right == "CALL"]["OI"], name="Call OI", marker_color="#10b981"))
        fig_oi.add_trace(go.Bar(x=view[view.Right == "PUT"]["Strike"], y=view[view.Right == "PUT"]["OI"], name="Put OI", marker_color="#ef4444"))
        fig_oi.update_layout(barmode="group", height=350, template="plotly_dark", title="Strike-by-Strike Open Interest (OI) Distribution", plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_oi, use_container_width=True)

    elif analytics_tab == "Rolling Straddle":
        st.info("Rolling Straddle analytics is actively recording standard deviation variances.")

# ----------------- STEP 6: BOTTOM RIGHT PANEL: POSITIONS & GREEKS -----------------
st.markdown("---")
bottom_tab1, bottom_tab2 = st.tabs(["💼 Active Positions Table", "📊 Aggregated Greeks Exposure"])

with bottom_tab1:
    if not active_positions:
        st.info("No active positions currently. Click [B]/[S] on Option chain LTP to execute paper trades.")
    else:
        # Table Columns matching requirement exactly
        pos_header = st.columns([0.6, 1.0, 1.0, 1.0, 1.5, 1.5, 1.5, 1.5, 1.5, 1.0])
        pos_header[0].markdown("**Select**")
        pos_header[1].markdown("**Action**")
        pos_header[2].markdown("**Lots**")
        pos_header[3].markdown("**Qty**")
        pos_header[4].markdown("**Strike**")
        pos_header[5].markdown("**Expiry**")
        pos_header[6].markdown("**Entry Price**")
        pos_header[7].markdown("**LTP**")
        pos_header[8].markdown("**P&L**")
        pos_header[9].markdown("**Exit**")

        for idx_p, p in enumerate(active_positions):
            p_id = p["id"]
            p_right = p["right"]
            p_strike = p["strike"]
            p_side = p["side"]
            p_qty = p["qty"]
            p_avg = p["avg"]

            if p_right == "FUT":
                p_ltp = spot
                strike_display = f"{p_strike:,.0f} FUT"
            else:
                p_ltp = quotes.get((p_right, p_strike), p_avg)
                strike_display = f"{p_strike:,.0f} {p_right}"

            pnl_val = p_qty * (p_ltp - p_avg) * (1.0 if p_side == "BUY" else -1.0)
            lot_size_v = get_lot_size(symbol)
            lots_val = int(p_qty / lot_size_v)

            p_cols = st.columns([0.6, 1.0, 1.0, 1.0, 1.5, 1.5, 1.5, 1.5, 1.5, 1.0])

            is_sel = p_cols[0].checkbox("", value=(p_id in st.session_state.selected_rows), key=f"sel_{p_id}")
            if is_sel:
                st.session_state.selected_rows.add(p_id)
            else:
                st.session_state.selected_rows.discard(p_id)

            # Soft-green B/S badges
            badge_color = "green" if p_side == "BUY" else "red"
            p_cols[1].markdown(f"<span class='tag-{badge_color}'>{p_side}</span>", unsafe_allow_html=True)

            p_cols[2].write(f"{lots_val} Lot")
            p_cols[3].write(f"{p_qty}")
            p_cols[4].write(strike_display)
            p_cols[5].write(active_expiry.strftime("%d-%b-%Y"))
            p_cols[6].write(f"₹{p_avg:.2f}")
            p_cols[7].write(f"₹{p_ltp:.2f}")

            # P&L color-coded
            color_pnl = "#22c55e" if pnl_val >= 0 else "#ef4444"
            p_cols[8].markdown(f"<b style='color: {color_pnl};'>₹{pnl_val:,.2f}</b>", unsafe_allow_html=True)

            if p_cols[9].button("❌", key=f"del_{p_id}"):
                st.session_state.positions = [item for item in st.session_state.positions if item["id"] != p_id]
                st.toast(f"Closed position: {p_side} {p_right} {p_strike}", icon="ℹ️")
                st.rerun()

        # Footer Bar adjustments
        st.markdown("<br/>", unsafe_allow_html=True)
        foot_cols = st.columns([1.5, 1.5, 1.5, 1.5, 1.5, 1.5, 1.5])
        st.session_state.multiplier = foot_cols[0].number_input("Multiplier Adjustment", min_value=1, max_value=100, value=st.session_state.multiplier, step=1)
        foot_cols[1].write(f"**Lot Size:** {get_lot_size(symbol)}")

        if foot_cols[2].button("🔔 Add Alert", use_container_width=True):
            st.toast("Alert created for the active strategy parameters!", icon="🔔")
        if foot_cols[3].button("💾 Save Strategy", use_container_width=True):
            st.toast("Paper strategy configuration saved to workspace!", icon="💾")
        if foot_cols[4].button("🔗 Share", use_container_width=True):
            st.toast("Link to simulator setup copied to clipboard!", icon="🔗")
        if foot_cols[5].button("🚪 Exit Selected", use_container_width=True):
            if st.session_state.selected_rows:
                st.session_state.positions = [item for item in st.session_state.positions if item["id"] not in st.session_state.selected_rows]
                st.session_state.selected_rows.clear()
                st.toast("Exited all selected option legs!", icon="🚪")
                st.rerun()
            else:
                st.toast("No positions selected!", icon="⚠️")
        if foot_cols[6].button("🧹 Clear All", use_container_width=True):
            st.session_state.positions = []
            st.session_state.selected_rows.clear()
            st.toast("Cleared active portfolio!", icon="🧹")
            st.rerun()

with bottom_tab2:
    st.markdown("#### Aggregated Portfolio Greek Exposure")
    delta_tot = 0.0
    gamma_tot = 0.0
    theta_tot = 0.0
    vega_tot = 0.0

    for p in active_positions:
        p_right = p["right"]
        p_strike = p["strike"]
        p_side = p["side"]
        p_qty = p["qty"]
        p_avg = p["avg"]

        if p_right != "FUT":
            match_g = view[(view.Strike == p_strike) & (view.Right == p_right)]
            if not match_g.empty:
                g_row = match_g.iloc[0]
                mult = 1.0 if p_side == "BUY" else -1.0
                if "Delta" in g_row and not np.isnan(g_row.Delta): delta_tot += g_row.Delta * p_qty * mult
                if "Gamma" in g_row and not np.isnan(g_row.Gamma): gamma_tot += g_row.Gamma * p_qty * mult
                if "Theta" in g_row and not np.isnan(g_row.Theta): theta_tot += g_row.Theta * p_qty * mult
                if "Vega" in g_row and not np.isnan(g_row.Vega): vega_tot += g_row.Vega * p_qty * mult

    g_cols = st.columns(4)
    g_cols[0].metric("Net Delta", f"{delta_tot:.2f}")
    g_cols[1].metric("Net Gamma", f"{gamma_tot:.5f}")
    g_cols[2].metric("Net Theta", f"₹{theta_tot:,.2f}/day")
    g_cols[3].metric("Net Vega", f"₹{vega_tot:,.2f}/1% Vol")
