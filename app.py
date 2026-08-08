import os
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import calendar
from datetime import date, time, datetime, timedelta
from dotenv import load_dotenv
from breeze_client import BreezeClient, format_breeze_date
from greeks import implied_vol, greeks
from backend.expiry_service import get_official_expiry_dates

load_dotenv()
st.set_page_config(page_title="Breeze Option Replay", page_icon="📈", layout="wide")

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

# ---------- helpers ----------
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
    rng=np.random.default_rng(7)
    times=pd.date_range(f"{day} 09:15", f"{day} 15:30", freq="5min")
    strikes=[round(atm+(i-count//2)*step,2) for i in range(count)]
    rows=[]; spot_path=atm+np.cumsum(rng.normal(0,step*.025,len(times)))
    for ti,ts in enumerate(times):
        spot=spot_path[ti]
        for k in strikes:
            for right in ["call","put"]:
                intrinsic=max(spot-k,0) if right=="call" else max(k-spot,0)
                tv=max(5,step*.8*np.exp(-abs(spot-k)/(step*2)))
                price=max(.5,intrinsic+tv+rng.normal(0,.8))
                rows.append({"datetime":ts,"close":price,"volume":int(rng.integers(500,15000)),"open_interest":int(rng.integers(5000,90000)),"strike":k,"right":right,"spot":spot})
    return pd.DataFrame(rows), times

def payoff(legs, spots):
    total=np.zeros_like(spots,dtype=float)
    for leg in legs:
        intrinsic=np.maximum(spots-leg["strike"],0) if leg["right"].upper()=="CALL" else np.maximum(leg["strike"]-spots,0)
        per=intrinsic-leg["premium"] if leg["side"].upper()=="BUY" else leg["premium"]-intrinsic
        total += leg["qty"]*per
    return total

def move_index(times, idx, delta):
    if not times: return idx
    return max(0,min(len(times)-1,idx+delta))

def get_spot_price(client, symbol, selected_day, selected_time, mode):
    if mode == "Demo":
        base_spot = 25000.0 if symbol == "NIFTY" else (52000.0 if symbol == "BANKNIFTY" else 23000.0)
        day_variance = (selected_day.day * 15.0) - 200.0
        return base_spot + day_variance
    else:
        session = st.session_state.get("session_token")
        if not session:
            return 25000.0 if symbol == "NIFTY" else (52000.0 if symbol == "BANKNIFTY" else 23000.0)
        try:
            start_iso = f"{selected_day}T09:15:00.000Z"
            end_iso = f"{selected_day}T15:30:00.000Z"
            df = get_index_hist(client.api_key, client.secret_key, session, symbol, start_iso, end_iso, "1minute")
            if not df.empty and "close" in df and "datetime" in df:
                df["datetime"] = pd.to_datetime(df["datetime"])
                target_dt = pd.Timestamp(datetime.combine(selected_day, selected_time))
                if target_dt.tzinfo is not None:
                    target_dt = target_dt.tz_localize(None)
                df["datetime_naive"] = df["datetime"].dt.tz_localize(None) if df["datetime"].dt.tz is not None else df["datetime"]
                df["diff"] = (df["datetime_naive"] - target_dt).abs()
                best_row = df.loc[df["diff"].idxmin()]
                return float(best_row["close"])
        except Exception as e:
            st.warning(f"Failed to fetch auto-spot price from Breeze: {e}. Using fallback.")
        return 25000.0 if symbol == "NIFTY" else (52000.0 if symbol == "BANKNIFTY" else 23000.0)

def get_strike_position_info(strike, right):
    active_positions = st.session_state.get("positions", [])
    net_qty = 0
    buys = 0
    sells = 0
    for p in active_positions:
        if p["strike"] == float(strike) and p["right"].upper() == right.upper():
            q = p["qty"]
            if p["side"] == "BUY":
                net_qty += q
                buys += q
            else:
                net_qty -= q
                sells += q
    return net_qty, buys, sells

def toggle_chain_trade(strike, right, side, ltp, qty, chain_sl, chain_tp, times, idx):
    match_pos = None
    for p in st.session_state.positions:
        if p["strike"] == float(strike) and p["right"].upper() == right.upper() and p["side"].upper() == side.upper():
            match_pos = p
            break

    if match_pos is not None:
        # Deselect / remove
        st.session_state.positions.remove(match_pos)
        if side.upper() == "BUY":
            st.session_state.cash += match_pos["qty"] * match_pos["avg"]
        else:
            st.session_state.cash -= match_pos["qty"] * match_pos["avg"]

        st.session_state.trade_history.append({
            "time": pd.Timestamp(times[idx]),
            "action": f"TOGGLE_OFF ({side.upper()}_{right.upper()})",
            "right": right.upper(),
            "strike": float(strike),
            "qty": match_pos["qty"],
            "price": float(ltp),
            "realized": 0.0
        })
    else:
        # Select / add
        new_pos = {
            "id": str(uuid.uuid4()),
            "right": right.upper(),
            "strike": float(strike),
            "side": side.upper(),
            "qty": int(qty),
            "avg": float(ltp),
            "sl_pct": float(chain_sl) if chain_sl > 0 else None,
            "tp_pct": float(chain_tp) if chain_tp > 0 else None,
            "entry_time": str(pd.Timestamp(times[idx]))
        }
        st.session_state.positions.append(new_pos)
        if side.upper() == "BUY":
            st.session_state.cash -= int(qty) * float(ltp)
        else:
            st.session_state.cash += int(qty) * float(ltp)

        st.session_state.trade_history.append({
            "time": pd.Timestamp(times[idx]),
            "action": f"{side.upper()} ({right.upper()})",
            "right": right.upper(),
            "strike": float(strike),
            "qty": int(qty),
            "price": float(ltp),
            "realized": 0.0
        })

from backend.math_engine import black_scholes_pricing

def draw_consolidated_payoff_chart(active_legs, spot, step, T, rate, div):
    spots = np.linspace(max(1, spot - step * 10), spot + step * 10, 201)

    # 1. Calculate Expiry P&L (T = 0)
    expiry_pnl = np.zeros_like(spots, dtype=float)
    for leg in active_legs:
        intrinsic = np.maximum(spots - leg["strike"], 0) if leg["right"].upper() == "CALL" else np.maximum(leg["strike"] - spots, 0)
        per = intrinsic - leg["premium"] if leg["side"].upper() == "BUY" else leg["premium"] - intrinsic
        expiry_pnl += leg["qty"] * per

    # 2. Calculate Today's MTM (T = T_target)
    mtm_pnl = np.zeros_like(spots, dtype=float)
    sigma = 0.18  # standard Indian options average IV
    for leg in active_legs:
        mult = 1.0 if leg["side"].upper() == "BUY" else -1.0
        leg_prices = np.array([
            black_scholes_pricing(s, leg["strike"], T, rate, div, sigma, leg["right"].lower())
            for s in spots
        ])
        mtm_pnl += leg["qty"] * (leg_prices - leg["premium"]) * mult

    # Build consolidated plot
    fig = go.Figure()

    # Shading the Profit Zone (above 0)
    profit_shade = np.maximum(expiry_pnl, 0)
    fig.add_trace(go.Scatter(
        x=spots, y=profit_shade,
        mode="lines",
        line=dict(width=0),
        fill="tozeroy",
        fillcolor="rgba(34, 197, 94, 0.2)",
        name="Profit Zone",
        showlegend=False
    ))

    # Shading the Loss Zone (below 0)
    loss_shade = np.minimum(expiry_pnl, 0)
    fig.add_trace(go.Scatter(
        x=spots, y=loss_shade,
        mode="lines",
        line=dict(width=0),
        fill="tozeroy",
        fillcolor="rgba(239, 68, 68, 0.2)",
        name="Loss Zone",
        showlegend=False
    ))

    # Expiry Payoff line (solid)
    fig.add_trace(go.Scatter(
        x=spots, y=expiry_pnl,
        mode="lines",
        line=dict(color="#22c55e", width=3),
        name="Expiry Payoff (T=0)"
    ))

    # Today's MTM curve (dashed)
    fig.add_trace(go.Scatter(
        x=spots, y=mtm_pnl,
        mode="lines",
        line=dict(color="#38bdf8", width=2.5, dash="dash"),
        name="Today's MTM (T+t)"
    ))

    fig.add_vline(x=spot, line_dash="dot", line_color="#f97316", line_width=2, annotation_text="Spot", annotation_position="top left")
    fig.add_hline(y=0, line_dash="solid", line_color="#475569", line_width=1)

    fig.update_layout(
        title="Consolidated Strategy Payoff & Today's MTM Chart (StockMock Style)",
        xaxis_title="Underlying Spot Price (₹)",
        yaxis_title="Profit / Loss (₹)",
        height=450,
        hovermode="x unified",
        template="plotly_dark",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    return fig

import uuid

def current_quotes(view):
    return {(str(r.Right),float(r.Strike)):float(r.LTP) for _,r in view.iterrows()}

def check_risk_triggers(current_time, view):
    quotes = current_quotes(view)
    active_positions = st.session_state.get("positions", [])
    remaining_positions = []
    triggered_any = False

    for p in active_positions:
        r = p["right"]
        k = p["strike"]
        side = p["side"]
        qty = p["qty"]
        avg = p["avg"]
        sl = p.get("sl_pct")
        tp = p.get("tp_pct")

        ltp = quotes.get((r, k), np.nan)
        if not np.isfinite(ltp):
            remaining_positions.append(p)
            continue

        triggered = False
        trigger_reason = ""

        if side == "BUY":
            if sl is not None and sl > 0:
                sl_price = avg * (1.0 - sl / 100.0)
                if ltp <= sl_price:
                    triggered = True
                    trigger_reason = f"SL ({sl}%)"
            if tp is not None and tp > 0:
                tp_price = avg * (1.0 + tp / 100.0)
                if ltp >= tp_price:
                    triggered = True
                    trigger_reason = f"TP ({tp}%)"
        else: # SELL
            if sl is not None and sl > 0:
                sl_price = avg * (1.0 + sl / 100.0)
                if ltp >= sl_price:
                    triggered = True
                    trigger_reason = f"SL ({sl}%)"
            if tp is not None and tp > 0:
                tp_price = avg * (1.0 - tp / 100.0)
                if ltp <= tp_price:
                    triggered = True
                    trigger_reason = f"TP ({tp}%)"

        if triggered:
            realized = 0.0
            if side == "BUY":
                st.session_state.cash += qty * ltp
                realized = (ltp - avg) * qty
            else: # SELL
                st.session_state.cash -= qty * ltp
                realized = (avg - ltp) * qty

            st.session_state.trade_history.append({
                "time": current_time,
                "action": f"AUTO_CLOSE ({trigger_reason})",
                "right": r,
                "strike": k,
                "qty": qty,
                "price": ltp,
                "realized": realized
            })
            triggered_any = True
        else:
            remaining_positions.append(p)

    if triggered_any:
        st.session_state.positions = remaining_positions
        return True
    return False

def mark_portfolio(view):
    quotes = current_quotes(view)
    total_val = 0.0
    rows = []
    active_positions = st.session_state.get("positions", [])
    for idx, p in enumerate(active_positions):
        r = p["right"]
        k = p["strike"]
        qty = p["qty"]
        avg = p["avg"]
        side = p["side"]
        sl = p.get("sl_pct", 0.0)
        tp = p.get("tp_pct", 0.0)

        ltp = quotes.get((r, k), np.nan)

        if np.isfinite(ltp):
            if side == "BUY":
                mv = qty * ltp
                unreal = (ltp - avg) * qty
            else:
                mv = -qty * ltp
                unreal = (avg - ltp) * qty
            total_val += mv
        else:
            unreal = np.nan

        rows.append({
            "idx": idx,
            "id": p.get("id"),
            "Right": r,
            "Strike": k,
            "Side": side,
            "Qty": qty,
            "Avg": avg,
            "LTP": ltp,
            "SL %": sl,
            "TP %": tp,
            "Unrealized P&L": unreal
        })
    return pd.DataFrame(rows), total_val

# ---------- state ----------
if "positions" not in st.session_state or isinstance(st.session_state.positions, dict):
    st.session_state.positions = []
if "cash" not in st.session_state:
    st.session_state.cash = 1_000_000.0
if "trade_history" not in st.session_state:
    st.session_state.trade_history = []
if "mtm_history" not in st.session_state:
    st.session_state.mtm_history = []
if "strategy_legs" not in st.session_state:
    st.session_state.strategy_legs = []
if "autoplay" not in st.session_state:
    st.session_state.autoplay = False
if "autoplay_speed" not in st.session_state:
    st.session_state.autoplay_speed = 1.0

# STREAMLIT INTERACTIVE STYLE OVERLAYS
st.markdown("""
<style>
.block-container{padding-top:1rem;max-width:1750px}.small{color:#8b949e;font-size:.85rem}
div.stButton > button:first-child {
    transition: all 0.2s ease-in-out;
}
div.stButton > button:first-child:hover {
    transform: scale(1.05);
}
</style>
""",unsafe_allow_html=True)
st.title("📈 Breeze Option Replay")
st.caption("Historical paper-trading simulator • every paper fill is replay-time stamped • no live orders")

# 1. API Keys configuration from environment variables, .env, or fallback .env.example
api_key_env, secret_key_env = load_env_credentials()

if "breeze_api_key" not in st.session_state or not st.session_state.breeze_api_key:
    st.session_state.breeze_api_key = api_key_env
if "breeze_secret_key" not in st.session_state or not st.session_state.breeze_secret_key:
    st.session_state.breeze_secret_key = secret_key_env

# Create BreezeClient instance
client = BreezeClient(
    api_key=st.session_state.breeze_api_key,
    secret_key=st.session_state.breeze_secret_key,
    session_token=st.session_state.get("session_token")
)

# ---------- auto-connect & token exchange ----------
# Capture any common parameter names returned by redirect URL: "api_session", "apisession", "API_Session", "session_token", "token"
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

# ---------- controls ----------
with st.container(border=True):
    a,b,c,d,e,f=st.columns([1.1,1.0,1.35,1.15,1.2,1.1])
    symbol=a.selectbox("Underlying",["NIFTY","BANKNIFTY","FINNIFTY"])
    mode=b.selectbox("Data mode",["Demo","Breeze"])
    selected_day=c.date_input("Replay date",date(2026,8,7))
    selected_time=d.time_input("Replay time",time(9,15),step=300)
    interval=e.selectbox("Bar interval",["1minute","5minute","15minute","30minute","1day"],index=1)
    step=f.number_input("Strike step",5.0,step=50.0,value=50.0)

# AutoPlay state setup if missing
if "autoplay" not in st.session_state:
    st.session_state.autoplay = False
if "autoplay_speed" not in st.session_state:
    st.session_state.autoplay_speed = 1.0

with st.sidebar:
    st.header("🔐 Connection")

    with st.expander("🔑 Configure Breeze API Keys", expanded=not (st.session_state.breeze_api_key and st.session_state.breeze_secret_key)):
        user_api_key = st.text_input("Breeze API Key", value=st.session_state.breeze_api_key, placeholder="Enter API Key")
        user_secret_key = st.text_input("Breeze Secret Key", value=st.session_state.breeze_secret_key, type="password", placeholder="Enter Secret Key")
        if user_api_key != st.session_state.breeze_api_key or user_secret_key != st.session_state.breeze_secret_key:
            st.session_state.breeze_api_key = user_api_key
            st.session_state.breeze_secret_key = user_secret_key
            st.rerun()

    if client.configured:
        st.link_button("🌐 Connect/Login ICICI Direct", client.login_url(), use_container_width=True)
    else:
        st.info("Demo mode works without credentials. Enter API credentials above to connect Breeze.")

    # Manual Session Token/Exchange
    if client.configured:
        manual_session = st.text_input("Manual API Session / Redirect URL", placeholder="Paste api_session or redirected URL")
        if st.button("🔌 Exchange Session Token", use_container_width=True):
            if manual_session:
                token_to_exchange = manual_session
                if "api_session=" in manual_session:
                    try:
                        token_to_exchange = manual_session.split("api_session=")[1].split("&")[0]
                    except Exception:
                        pass
                try:
                    with st.spinner("Exchanging manual token..."):
                        session_token = client.exchange_api_session(token_to_exchange)
                        st.session_state["session_token"] = session_token
                    st.success("Session exchanged successfully!")
                    st.rerun()
                except Exception as ex:
                    st.error(f"Exchange failed: {ex}")
            else:
                st.warning("Please enter a token or URL first.")

    # Connection Status
    if st.session_state.get("session_token"):
        st.success("● Connected to Breeze")
        trunc_token = st.session_state["session_token"][:8] + "..." if len(st.session_state["session_token"]) > 10 else st.session_state["session_token"]
        st.caption(f"Session Token: {trunc_token}")
        if st.button("🚪 Disconnect Breeze", use_container_width=True):
            st.session_state["session_token"] = None
            st.rerun()
    else:
        st.warning("● Demo / Paper Mode Active")

# ---------- auto-calculate spot price ----------
current_params_key = f"{symbol}_{mode}_{selected_day}_{selected_time}"
if st.session_state.get("last_params_key") != current_params_key:
    # Trigger auto spot update
    auto_spot = get_spot_price(client, symbol, selected_day, selected_time, mode)
    st.session_state["atm_strike_val"] = auto_spot
    st.session_state["last_params_key"] = current_params_key

if "atm_strike_val" not in st.session_state:
    st.session_state["atm_strike_val"] = 25000.0

# ---------- market parameters & load chain ----------
with st.container(border=True):
    st.markdown("#### 📅 Market Setup & Option Parameters")
    m1, m2, m3, m4, m5, m6 = st.columns([1.2, 1.2, 1.4, 1.2, 1.0, 1.0])

    atm = m1.number_input("ATM strike", 1000.0, step=50.0, value=float(st.session_state["atm_strike_val"]))
    st.session_state["atm_strike_val"] = atm

    strike_count = m2.slider("Strikes", 4, 20, 20)

    # Expiry is a selectbox dynamically calculated directly from the Broker Security Master!
    expiry_options = get_official_expiry_dates(selected_day, symbol, client)

    # Find active expiry (closest on or after selected_day)
    default_expiry_index = 0
    for idx_exp, exp in enumerate(expiry_options):
        if exp >= selected_day:
            default_expiry_index = idx_exp
            break

    expiry_date = m3.selectbox("Option expiry", options=expiry_options, index=default_expiry_index, format_func=lambda d: d.strftime("%d-%b-%Y"))
    expiry_time = m4.time_input("Expiry time", time(15, 30))
    rate = m5.number_input("Risk-free %", 6.5, step=.25) / 100
    div = m6.number_input("Dividend %", 0.0, step=.25) / 100

    if st.button("🔄 Load / Generate Chain", type="primary", use_container_width=True):
        if mode == "Demo":
            chain, times = demo_chain(atm, step, strike_count, selected_day)
        else:
            session = st.session_state.get("session_token")
            frames = []
            strikes = sorted(round(atm + (i - strike_count // 2) * step, 2) for i in range(strike_count))
            if not session:
                st.error("Connect Breeze first.")
                chain, times = pd.DataFrame(), []
            else:
                start_iso = format_breeze_date(selected_day, "ISO_HISTORICAL")
                end_iso = format_breeze_date(selected_day, "ISO_HISTORICAL")
                exp_iso = format_breeze_date(expiry_date, "ISO_EXPIRY")
                for k in strikes:
                    for right in ["call", "put"]:
                        try:
                            d0 = get_hist(client.api_key, client.secret_key, session, symbol, start_iso, end_iso, exp_iso, right, k, interval)
                            if not d0.empty:
                                d0["strike"] = k
                                d0["right"] = right
                                frames.append(d0)
                        except Exception as ex:
                            st.warning(f"{right.upper()} {k}: {ex}")
                chain = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
                times = sorted(chain.datetime.dropna().unique()) if not chain.empty else []
        st.session_state.chain = chain
        st.session_state.times = times
        # select nearest available timestamp to requested time
        target = pd.Timestamp(datetime.combine(selected_day, selected_time))
        if len(times) > 0:
            st.session_state.idx = int(np.argmin([abs(pd.Timestamp(x) - target) for x in times]))
        st.session_state.mtm_history = []

chain=st.session_state.get("chain",pd.DataFrame()); times=st.session_state.get("times",[])
if chain.empty:
    st.info("Choose a date/time and expiry, then click Load / Generate Chain."); st.stop()

# Let's ensure times is not a DatetimeIndex but a plain list/sequence to avoid truth value ambiguity
if hasattr(times, "tolist"):
    times = times.tolist()

idx=min(st.session_state.get("idx",0),len(times)-1)

# Ensure AutoPlay continues running if active
import time as ptime
if st.session_state.autoplay:
    # Trigger next bar if possible
    if idx < len(times) - 1:
        time_to_wait = 1.0 / max(0.1, st.session_state.autoplay_speed)
        ptime.sleep(time_to_wait)
        st.session_state.idx = idx + 1
        st.rerun()
    else:
        st.session_state.autoplay = False

if times:
    idx=min(st.session_state.get("idx",0),len(times)-1)
else:
    idx=0

snap=chain[chain["datetime"]==times[idx]].copy() if ("datetime" in chain.columns and times) else chain.copy()
spot=float(snap["spot"].iloc[0]) if "spot" in snap.columns and not snap.empty else float(atm)
expiry_dt=pd.Timestamp(datetime.combine(expiry_date,expiry_time),tz="UTC")
rt=pd.Timestamp(times[idx]) if times else pd.Timestamp(selected_day)
rt=rt.tz_localize("UTC") if rt.tzinfo is None else rt.tz_convert("UTC")
T=max((expiry_dt-rt).total_seconds()/(365*24*3600),1e-8)

# Calculate quotes & check triggers
out=[]
for _,r in snap.iterrows():
    if "close" in r and "strike" in r and "right" in r:
        iv=implied_vol(float(r.close),spot,float(r.strike),T,rate,div,r.right); g=greeks(spot,float(r.strike),T,rate,div,iv,r.right)
        out.append({"Strike":float(r.strike),"Right":r.right.upper(),"LTP":float(r.close),"Volume":r.get("volume",np.nan),"OI":r.get("open_interest",np.nan),"IV %":iv*100 if np.isfinite(iv) else np.nan,**{k.title():v for k,v in g.items()}})

if out:
    view=pd.DataFrame(out).sort_values(["Strike","Right"])
else:
    view=pd.DataFrame(columns=["Strike","Right","LTP","Volume","OI","IV %","Delta","Gamma","Theta","Vega","Rho"])

# Define quotes for global use (e.g. exit/square-off actions)
quotes = current_quotes(view)

# Risk check
if times:
    triggered = check_risk_triggers(pd.Timestamp(times[idx]), view)
    if triggered:
        st.rerun()

# --- HEADER: Account metrics & AutoPlay controls ---
st.markdown("---")
# Row 1: Key Performance Metrics
portfolio, pos_value = mark_portfolio(view)
current_capital = st.session_state.cash + pos_value
initial_capital = 1_000_000.0
total_pnl = current_capital - initial_capital

# Save MTM to history
if times:
    st.session_state.mtm_history.append({"time":pd.Timestamp(times[idx]),"mtm":total_pnl})
mh=pd.DataFrame(st.session_state.mtm_history).drop_duplicates("time").sort_values("time") if st.session_state.mtm_history else pd.DataFrame(columns=["time", "mtm"])

met1, met2, met3, met4, met5 = st.columns(5)
met1.metric("Capital", f"₹{current_capital:,.2f}")
met2.metric("Portfolio Value", f"₹{pos_value:,.2f}")
met3.metric("Free Cash", f"₹{st.session_state.cash:,.2f}")
met4.metric("Total MTM P&L", f"₹{total_pnl:,.2f}", delta=f"₹{total_pnl:,.2f}")
met5.metric("Current Spot Price", f"₹{spot:,.2f}")

# Row 2: Replay controls & AutoPlay bar
con1, con2 = st.columns([3, 2])
with con1:
    st.markdown("**⏱ Replay Controls**")
    j1,j2,j3,j4,j5,j6,j7 = st.columns(7)
    if j1.button("⏮ 1 bar", key="b1"): st.session_state.idx=move_index(times,idx,-1); st.rerun()
    if j2.button("1 bar ⏭", key="b2"): st.session_state.idx=move_index(times,idx,1); st.rerun()
    if j3.button("⏪ 5m", key="b3"): st.session_state.idx=move_index(times,idx,-1); st.rerun() # demo_chain produces 5m interval
    if j4.button("5m ⏩", key="b4"): st.session_state.idx=move_index(times,idx,1); st.rerun()
    if j5.button("⏪ 30m", key="b5"): st.session_state.idx=move_index(times,idx,-6); st.rerun()
    if j6.button("30m ⏩", key="b6"): st.session_state.idx=move_index(times,idx,6); st.rerun()
    if j7.button("3h ⏩", key="b7"): st.session_state.idx=move_index(times,idx,36); st.rerun()

    q1,q2,q3,q4=st.columns(4)
    if q1.button("⬅ 1 Hour", key="q1", use_container_width=True): st.session_state.idx=move_index(times,idx,-12); st.rerun()
    if q2.button("➡ 1 Hour", key="q2", use_container_width=True): st.session_state.idx=move_index(times,idx,12); st.rerun()
    if q3.button("⬅ 1 Day", key="q3", use_container_width=True): st.session_state.idx=move_index(times,idx,-78); st.rerun()
    if q4.button("➡ 1 Day", key="q4", use_container_width=True): st.session_state.idx=move_index(times,idx,78); st.rerun()

with con2:
    st.markdown("**🔄 AutoPlay Controls**")
    play_col, speed_col = st.columns([1, 1])
    with play_col:
        ap_label = "Stop AutoPlay" if st.session_state.autoplay else "Start AutoPlay"
        if st.button(ap_label, type="primary", use_container_width=True):
            st.session_state.autoplay = not st.session_state.autoplay
            st.rerun()
    with speed_col:
        st.session_state.autoplay_speed = st.slider("Speed (bars/sec)", 0.2, 5.0, value=float(st.session_state.autoplay_speed), step=0.2)

# ---------- TABBED INTERFACE ----------
tab_terminal, tab_positions = st.tabs(["📊 Option Terminal & Strategy Builder", "💼 Active Positions & Analytics"])

# ---------- TAB 1: Option Terminal & Strategy Builder ----------
with tab_terminal:
    # Beautiful top-aligned simulation status card with full formatted datetime
    if times:
        session_ts = pd.Timestamp(times[idx])
        formatted_session_time = session_ts.strftime("%d-%b-%Y %I:%M %p")
        st.markdown(f"""
        <div style='background-color: #0f172a; padding: 15px 25px; border-radius: 8px; border: 1.5px solid #1e293b; margin-bottom: 25px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1);'>
            <div>
                <div style='font-size: 0.8rem; font-weight: bold; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em;'>⏱ ACTIVE REPLAY SESSION</div>
                <div style='font-size: 1.5rem; font-weight: 800; color: #38bdf8;'>{symbol} Option Chain</div>
            </div>
            <div style='text-align: right;'>
                <div style='font-size: 0.8rem; font-weight: bold; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em;'>CURRENT SIMULATION TIMESTAMP</div>
                <div style='font-size: 1.5rem; font-weight: 800; color: #4ade80; font-family: monospace;'>{formatted_session_time}</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    # Option Chain Section
    st.subheader("📈 Interactive Option Chain")
    st.caption("Execute trades instantly. You can set individual Stop-Loss (SL) and Take-Profit (TP) percentages before buying or selling.")

    # Global execution inputs
    chain_exec1, chain_exec2, chain_exec3 = st.columns([1, 1, 1])
    chain_qty = chain_exec1.number_input("Trade Quantity (Lots)", 25, 100000, 50, step=25)
    chain_sl = chain_exec2.number_input("Default Stop Loss % (0 to disable)", 0.0, 100.0, 0.0, step=1.0)
    chain_tp = chain_exec3.number_input("Default Take Profit % (0 to disable)", 0.0, 100.0, 0.0, step=1.0)

    # Option chain grid header
    header=st.columns([1, 1.2, 1.2, 1.2, 1.2, 1.2, 1, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2])
    labels = ["BUY CE", "SELL CE", "CE LTP", "CE IV", "CE Δ", "CE Θ", "Strike", "PE LTP", "PE IV", "PE Δ", "PE Θ", "BUY PE", "SELL PE"]
    for col, label in zip(header, labels):
        col.markdown(f"<div style='text-align: center; font-weight: bold;'>{label}</div>", unsafe_allow_html=True)

    for strike in sorted(view.Strike.unique()):
        ce=view[(view.Strike==strike)&(view.Right=="CALL")]; pe=view[(view.Strike==strike)&(view.Right=="PUT")]
        ce=ce.iloc[0] if not ce.empty else None; pe=pe.iloc[0] if not pe.empty else None

        # Calculate positions
        ce_net, ce_b, ce_s = get_strike_position_info(strike, "CALL")
        pe_net, pe_b, pe_s = get_strike_position_info(strike, "PUT")

        ce_is_itm = (strike < spot)
        pe_is_itm = (strike > spot)

        cols = st.columns([1, 1.2, 1.2, 1.2, 1.2, 1.2, 1, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2])

        # CE buttons (Acting as dynamic Toggle Switches!)
        if ce is not None:
            # BUY CE Toggle Button
            buy_ce_label = "UNBUY" if ce_b > 0 else "BUY"
            if cols[0].button(buy_ce_label, key=f"bce{strike}", use_container_width=True):
                toggle_chain_trade(strike, "CALL", "BUY", ce.LTP, chain_qty, chain_sl, chain_tp, times, idx)
                st.rerun()

            # SELL CE Toggle Button
            sell_ce_label = "UNSELL" if ce_s > 0 else "SELL"
            if cols[1].button(sell_ce_label, key=f"sce{strike}", use_container_width=True):
                toggle_chain_trade(strike, "CALL", "SELL", ce.LTP, chain_qty, chain_sl, chain_tp, times, idx)
                st.rerun()

            # CE LTP cell styling with dynamic In-The-Money highlighting & net quantity display
            if ce_is_itm:
                ce_ltp_html = f"<div style='text-align: center; background-color: #fbbf2415; border: 1px solid #fbbf2440; border-radius: 4px; padding: 2px 5px;'><span style='color: #fbbf24; font-weight: bold;'>₹{ce.LTP:.2f}</span> <span style='font-size: 0.75rem; background-color: #fbbf242c; padding: 2px 4px; border-radius: 3px; color: #fbbf24; font-weight: bold;'>ITM</span></div>"
            else:
                ce_ltp_html = f"<div style='text-align: center; color: #e2e8f0;'>₹{ce.LTP:.2f}</div>"

            # Render Lot Badges: e.g. B (1x), S (2x) directly over that cell
            if ce_net > 0:
                ce_lots = int(ce_net // 50) if ce_net >= 50 else 1
                ce_ltp_html += f"<div style='text-align: center; margin-top: 3px;'><span style='background-color: #22c55e2c; color: #22c55e; border-radius: 4px; padding: 2px 6px; font-weight: bold; font-size: 0.75rem;'>B ({ce_lots}x)</span></div>"
            elif ce_net < 0:
                ce_lots = int(abs(ce_net) // 50) if abs(ce_net) >= 50 else 1
                ce_ltp_html += f"<div style='text-align: center; margin-top: 3px;'><span style='background-color: #ef44442c; color: #ef4444; border-radius: 4px; padding: 2px 6px; font-weight: bold; font-size: 0.75rem;'>S ({ce_lots}x)</span></div>"

            cols[2].markdown(ce_ltp_html, unsafe_allow_html=True)
            cols[3].markdown(f"<div style='text-align: center;'>{ce['IV %']:.1f}%</div>", unsafe_allow_html=True)
            cols[4].markdown(f"<div style='text-align: center;'>{ce.Delta:.2f}</div>", unsafe_allow_html=True)
            cols[5].markdown(f"<div style='text-align: center;'>{ce.Theta:.1f}</div>", unsafe_allow_html=True)
        else:
            for i in range(6): cols[i].write("")

        # Strike Center styled with clean font color and prominent highlight for high contrast readability
        if abs(strike - spot) <= step * 0.51:
            cols[6].markdown(f"<div style='text-align: center; font-weight: bold; background-color: #020617; border: 2.5px solid #22c55e; padding: 2px 5px; border-radius: 4px; color: #38bdf8; font-size: 1.1rem; box-shadow: 0 0 10px #22c55e40;'>{strike:,.0f}</div>", unsafe_allow_html=True)
        else:
            cols[6].markdown(f"<div style='text-align: center; font-weight: bold; background-color: #1e293b; border: 1px solid #475569; padding: 2px 5px; border-radius: 4px; color: #38bdf8; font-size: 1.0rem;'>{strike:,.0f}</div>", unsafe_allow_html=True)

        # PE buttons (Acting as dynamic Toggle Switches!)
        if pe is not None:
            # BUY PE Toggle Button
            buy_pe_label = "UNBUY" if pe_b > 0 else "BUY"
            if cols[11].button(buy_pe_label, key=f"bpe{strike}", use_container_width=True):
                toggle_chain_trade(strike, "PUT", "BUY", pe.LTP, chain_qty, chain_sl, chain_tp, times, idx)
                st.rerun()

            # SELL PE Toggle Button
            sell_pe_label = "UNSELL" if pe_s > 0 else "SELL"
            if cols[12].button(sell_pe_label, key=f"spe{strike}", use_container_width=True):
                toggle_chain_trade(strike, "PUT", "SELL", pe.LTP, chain_qty, chain_sl, chain_tp, times, idx)
                st.rerun()

            # PE LTP cell styling with dynamic In-The-Money highlighting & net quantity display
            if pe_is_itm:
                pe_ltp_html = f"<div style='text-align: center; background-color: #c084fc15; border: 1px solid #c084fc40; border-radius: 4px; padding: 2px 5px;'><span style='color: #c084fc; font-weight: bold;'>₹{pe.LTP:.2f}</span> <span style='font-size: 0.75rem; background-color: #c084fc2c; padding: 2px 4px; border-radius: 3px; color: #c084fc; font-weight: bold;'>ITM</span></div>"
            else:
                pe_ltp_html = f"<div style='text-align: center; color: #e2e8f0;'>₹{pe.LTP:.2f}</div>"

            # Render Lot Badges: e.g. B (1x), S (2x) directly over that cell
            if pe_net > 0:
                pe_lots = int(pe_net // 50) if pe_net >= 50 else 1
                pe_ltp_html += f"<div style='text-align: center; margin-top: 3px;'><span style='background-color: #22c55e2c; color: #22c55e; border-radius: 4px; padding: 2px 6px; font-weight: bold; font-size: 0.75rem;'>B ({pe_lots}x)</span></div>"
            elif pe_net < 0:
                pe_lots = int(abs(pe_net) // 50) if abs(pe_net) >= 50 else 1
                pe_ltp_html += f"<div style='text-align: center; margin-top: 3px;'><span style='background-color: #ef44442c; color: #ef4444; border-radius: 4px; padding: 2px 6px; font-weight: bold; font-size: 0.75rem;'>S ({pe_lots}x)</span></div>"

            cols[7].markdown(pe_ltp_html, unsafe_allow_html=True)
            cols[8].markdown(f"<div style='text-align: center;'>{pe['IV %']:.1f}%</div>", unsafe_allow_html=True)
            cols[9].markdown(f"<div style='text-align: center;'>{pe.Delta:.2f}</div>", unsafe_allow_html=True)
            cols[10].markdown(f"<div style='text-align: center;'>{pe.Theta:.1f}</div>", unsafe_allow_html=True)

    # Strategy Builder Section
    st.markdown("---")
    st.subheader("🧩 Multi-Leg Strategy Builder")
    st.caption("Construct multi-leg strategies manually or import popular template strategies. Customize Stop Loss % and Take Profit % per leg and deploy them together.")

    # Find nearest ATM strike
    unique_strikes = view.Strike.unique() if "Strike" in view.columns else []
    if len(unique_strikes) > 0:
        nearest_atm = min(unique_strikes, key=lambda x: abs(x - spot))
    else:
        nearest_atm = float(atm)

    # Pre-defined strategy templates
    st.markdown("**Templates:**")
    t1, t2, t3, t4, t5 = st.columns(5)

    if t1.button("Short Straddle (ATM)", use_container_width=True):
        st.session_state.strategy_legs = [
            {"side": "SELL", "right": "CALL", "strike": float(nearest_atm), "qty": 50, "sl_pct": 20.0, "tp_pct": None},
            {"side": "SELL", "right": "PUT", "strike": float(nearest_atm), "qty": 50, "sl_pct": 20.0, "tp_pct": None}
        ]
        st.rerun()

    if t2.button("Short Strangle (OTM)", use_container_width=True):
        st.session_state.strategy_legs = [
            {"side": "SELL", "right": "CALL", "strike": float(nearest_atm + step), "qty": 50, "sl_pct": 25.0, "tp_pct": None},
            {"side": "SELL", "right": "PUT", "strike": float(nearest_atm - step), "qty": 50, "sl_pct": 25.0, "tp_pct": None}
        ]
        st.rerun()

    if t3.button("Iron Condor", use_container_width=True):
        st.session_state.strategy_legs = [
            {"side": "BUY", "right": "PUT", "strike": float(nearest_atm - 2 * step), "qty": 50, "sl_pct": None, "tp_pct": None},
            {"side": "SELL", "right": "PUT", "strike": float(nearest_atm - step), "qty": 50, "sl_pct": None, "tp_pct": None},
            {"side": "SELL", "right": "CALL", "strike": float(nearest_atm + step), "qty": 50, "sl_pct": None, "tp_pct": None},
            {"side": "BUY", "right": "CALL", "strike": float(nearest_atm + 2 * step), "qty": 50, "sl_pct": None, "tp_pct": None}
        ]
        st.rerun()

    if t4.button("Bull Call Spread", use_container_width=True):
        st.session_state.strategy_legs = [
            {"side": "BUY", "right": "CALL", "strike": float(nearest_atm), "qty": 50, "sl_pct": None, "tp_pct": None},
            {"side": "SELL", "right": "CALL", "strike": float(nearest_atm + step), "qty": 50, "sl_pct": None, "tp_pct": None}
        ]
        st.rerun()

    if t5.button("Bear Put Spread", use_container_width=True):
        st.session_state.strategy_legs = [
            {"side": "BUY", "right": "PUT", "strike": float(nearest_atm), "qty": 50, "sl_pct": None, "tp_pct": None},
            {"side": "SELL", "right": "PUT", "strike": float(nearest_atm - step), "qty": 50, "sl_pct": None, "tp_pct": None}
        ]
        st.rerun()

    # Manual strategy creation controls
    with st.container(border=True):
        s1, s2, s3, s4, s5, s6, s7 = st.columns([1.2, 1.2, 1.4, 1.2, 1.2, 1.2, 1.2])
        leg_side = s1.selectbox("Side", ["BUY", "SELL"], key="leg_side")
        leg_right = s2.selectbox("Type", ["CALL", "PUT"], key="leg_right")
        leg_strike = s3.selectbox("Strike", sorted(view.Strike.unique()), key="leg_strike")
        leg_qty = s4.number_input("Qty", 25, 100000, 50, step=25, key="leg_qty")
        leg_sl = s5.number_input("SL % (0 = none)", 0.0, 100.0, 0.0, step=1.0, key="leg_sl")
        leg_tp = s6.number_input("TP % (0 = none)", 0.0, 100.0, 0.0, step=1.0, key="leg_tp")

        leg_row = view[(view.Strike==leg_strike)&(view.Right==leg_right)]
        leg_premium = float(leg_row.iloc[0].LTP) if not leg_row.empty else 0.0

        if s7.button("+ Add leg", use_container_width=True):
            st.session_state.strategy_legs.append({
                "side": leg_side,
                "right": leg_right,
                "strike": float(leg_strike),
                "qty": int(leg_qty),
                "premium": leg_premium,
                "sl_pct": float(leg_sl) if leg_sl > 0 else None,
                "tp_pct": float(leg_tp) if leg_tp > 0 else None
            })
            st.rerun()

    # Active/Draft strategy overview
    if st.session_state.strategy_legs:
        draft_legs = []
        for l in st.session_state.strategy_legs:
            l_row = view[(view.Strike==l["strike"])&(view.Right==l["right"])]
            premium = float(l_row.iloc[0].LTP) if not l_row.empty else l.get("premium", 0.0)
            l["premium"] = premium
            draft_legs.append(l)

        st.markdown("**Strategy Draft Legs:**")
        st.dataframe(pd.DataFrame(draft_legs), use_container_width=True, hide_index=True)

        col_exec_st1, col_exec_st2 = st.columns(2)
        if col_exec_st1.button("🗑 Clear Strategy Draft", use_container_width=True):
            st.session_state.strategy_legs = []
            st.rerun()

        if col_exec_st2.button("🚀 Execute Strategy Draft (Deploy all legs)", type="primary", use_container_width=True):
            for leg in draft_legs:
                new_pos = {
                    "id": str(uuid.uuid4()),
                    "right": leg["right"],
                    "strike": float(leg["strike"]),
                    "side": leg["side"],
                    "qty": int(leg["qty"]),
                    "avg": float(leg["premium"]),
                    "sl_pct": leg.get("sl_pct"),
                    "tp_pct": leg.get("tp_pct"),
                    "entry_time": str(pd.Timestamp(times[idx]))
                }
                st.session_state.positions.append(new_pos)
                if leg["side"] == "BUY":
                    st.session_state.cash -= int(leg["qty"]) * float(leg["premium"])
                else:
                    st.session_state.cash += int(leg["qty"]) * float(leg["premium"])

                st.session_state.trade_history.append({
                    "time": pd.Timestamp(times[idx]),
                    "action": f"STRATEGY_DESTRUCTION_{leg['side']} ({leg['right']})",
                    "right": leg["right"],
                    "strike": float(leg["strike"]),
                    "qty": int(leg["qty"]),
                    "price": float(leg["premium"]),
                    "realized": 0.0
                })
            st.session_state.strategy_legs = []
            st.rerun()

# ---------- TAB 2: Positions & Analytics ----------
with tab_positions:
    st.subheader("💼 Active Positions")
    if portfolio.empty:
        st.info("No active positions currently. Head to the Option Terminal to place some trades!")
    else:
        # Prompt selection of "Clear All Positions" to wipe state instantly
        col_clear_1, col_clear_2 = st.columns([1, 4])
        if col_clear_1.button("🚨 Clear All Positions", type="primary", use_container_width=True):
            st.session_state.positions = []
            st.session_state.cash = 1_000_000.0
            st.session_state.trade_history = []
            st.session_state.mtm_history = []
            st.rerun()

        # Active Position Grid
        for i, row in portfolio.iterrows():
            c1, c2, c3, c4, c5, c6, c7, c8 = st.columns([1.5, 1, 1, 1, 1.2, 1, 1, 1])
            c1.markdown(f"**{row.Side} {row.Right} {row.Strike:,.0f}**")

            # Dynamic Lot quantity control inline directly inside the Strategy Builder table
            new_qty = c2.number_input("Qty", 25, 100000, int(row.Qty), step=25, key=f"qty_input_{row.id}")
            if new_qty != int(row.Qty):
                # Update position quantity dynamically
                p_id = row.id
                pos_index = next((index for index, item in enumerate(st.session_state.positions) if item.get("id") == p_id), None)
                if pos_index is not None:
                    # refund cash difference
                    diff_qty = new_qty - int(row.Qty)
                    if row.Side == "BUY":
                        st.session_state.cash -= diff_qty * row.Avg
                    else:
                        st.session_state.cash += diff_qty * row.Avg
                    st.session_state.positions[pos_index]["qty"] = new_qty
                    st.rerun()

            c3.write(f"Avg ₹{row.Avg:.2f}")
            c4.write(f"LTP ₹{row.LTP:.2f}")
            c5.metric("P&L", f"₹{row['Unrealized P&L']:,.2f}")

            # SL / TP Dynamic adjustment inputs
            sl_val = float(row["SL %"]) if (row["SL %"] is not None and np.isfinite(row["SL %"])) else 0.0
            tp_val = float(row["TP %"]) if (row["TP %"] is not None and np.isfinite(row["TP %"])) else 0.0
            new_sl = c6.number_input("SL %", 0.0, 100.0, sl_val, step=1.0, key=f"sl_input_{row.id}")
            new_tp = c7.number_input("TP %", 0.0, 100.0, tp_val, step=1.0, key=f"tp_input_{row.id}")

            p_id = row.id
            pos_index = next((index for index, item in enumerate(st.session_state.positions) if item.get("id") == p_id), None)
            if pos_index is not None:
                orig_sl = st.session_state.positions[pos_index].get("sl_pct")
                orig_tp = st.session_state.positions[pos_index].get("tp_pct")
                updated_sl = new_sl if new_sl > 0 else None
                updated_tp = new_tp if new_tp > 0 else None
                if orig_sl != updated_sl or orig_tp != updated_tp:
                    st.session_state.positions[pos_index]["sl_pct"] = updated_sl
                    st.session_state.positions[pos_index]["tp_pct"] = updated_tp

            if c8.button("Exit Leg", key=f"exit_{row.id}", use_container_width=True):
                p_id = row.id
                pos = next((item for item in st.session_state.positions if item.get("id") == p_id), None)
                if pos:
                    r = pos["right"]
                    k = pos["strike"]
                    side = pos["side"]
                    qty = pos["qty"]
                    avg = pos["avg"]

                    ltp = quotes.get((r, k), np.nan)
                    if np.isfinite(ltp):
                        realized = 0.0
                        if side == "BUY":
                            st.session_state.cash += qty * ltp
                            realized = (ltp - avg) * qty
                        else:
                            st.session_state.cash -= qty * ltp
                            realized = (avg - ltp) * qty

                        st.session_state.trade_history.append({
                            "time": pd.Timestamp(times[idx]),
                            "action": f"MANUAL_CLOSE_{side}",
                            "right": r,
                            "strike": k,
                            "qty": qty,
                            "price": ltp,
                            "realized": realized
                        })
                    st.session_state.positions = [item for item in st.session_state.positions if item.get("id") != p_id]
                    st.rerun()

    # Portfolio Greeks Section
    st.markdown("---")
    st.subheader("📊 Portfolio Greeks")
    st.caption("Aggregated Greek exposure of all active positions combined.")

    net_delta = 0.0
    net_gamma = 0.0
    net_theta = 0.0
    net_vega = 0.0

    for i, row in portfolio.iterrows():
        match_view = view[(view.Strike == row.Strike) & (view.Right == row.Right)]
        if not match_view.empty:
            g_row = match_view.iloc[0]
            mult = 1.0 if row.Side == "BUY" else -1.0
            qty = row.Qty

            if np.isfinite(g_row.Delta): net_delta += g_row.Delta * qty * mult
            if np.isfinite(g_row.Gamma): net_gamma += g_row.Gamma * qty * mult
            if np.isfinite(g_row.Theta): net_theta += g_row.Theta * qty * mult
            if np.isfinite(g_row.Vega): net_vega += g_row.Vega * qty * mult

    g1, g2, g3, g4 = st.columns(4)
    g1.metric("Delta (Directional)", f"{net_delta:.2f}")
    g2.metric("Gamma (Acceleration)", f"{net_gamma:.4f}")
    g3.metric("Theta (Time Decay)", f"₹{net_theta:,.2f}/day")
    g4.metric("Vega (Volatility)", f"₹{net_vega:,.2f}/1% Vol")

    # Interactive charts
    st.markdown("---")

    if not portfolio.empty:
        active_legs = []
        for i, row in portfolio.iterrows():
            active_legs.append({
                "side": row.Side,
                "right": row.Right,
                "strike": float(row.Strike),
                "qty": int(row.Qty),
                "premium": float(row.Avg)
            })
        # Plotly Consolidated Strategy Payoff Curve
        fig = draw_consolidated_payoff_chart(active_legs, spot, step, T, rate, div)
        st.plotly_chart(fig, use_container_width=True)

    # Execution trade history
    st.markdown("---")
    st.subheader("📜 Execution History")
    if st.session_state.trade_history:
        st.dataframe(pd.DataFrame(st.session_state.trade_history).sort_values("time", ascending=False), use_container_width=True, hide_index=True)
    else:
        st.info("No trades executed yet.")
