import os
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import calendar
import uuid
from datetime import date, time, datetime, timedelta
from dotenv import load_dotenv
from breeze_client import BreezeClient, format_breeze_date
from greeks import implied_vol, greeks
from backend.expiry_service import get_official_expiry_dates, format_contract_symbol, parse_expiry_from_contract

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
        if not is_real_session_token(session):
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
            print(f"Failed to fetch auto-spot price from Breeze: {e}. Using fallback.")
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

    fig.add_vline(x=spot, line_dash="dot", line_color="#f97316", width=2, annotation_text="Spot", annotation_position="top left")
    fig.add_hline(y=0, line_dash="solid", line_color="#475569", width=1)

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
div[data-testid="stTextInput"]:has(input[placeholder="HiddenTradeSignalInput"]) {
    display: none !important;
}
</style>
""",unsafe_allow_html=True)

# PostMessage Cross-Origin Communications Listener:
# Allows custom HTML iframe components to securely notify and update the main parent Streamlit context
st.markdown("""
<script>
window.addEventListener("message", (event) => {
    const actionStr = event.data;
    if (typeof actionStr === "string" && (actionStr.startsWith("BUY:") || actionStr.startsWith("SELL:") || actionStr.startsWith("DESELECT:"))) {
        const input = window.parent.document.querySelector('input[placeholder="HiddenTradeSignalInput"]');
        if (input) {
            input.value = actionStr;
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
        }
    }
});
</script>
""", unsafe_allow_html=True)

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

    strike_count = m2.slider("Strikes", 4, 40, 40)

    expiry_options = get_official_expiry_dates(selected_day, symbol, client)

    # Define a highly robust, type-safe date formatting helper
    def safe_format_date(d):
        if hasattr(d, "strftime"):
            return d.strftime("%d-%b-%Y")
        if isinstance(d, str):
            try:
                parsed = datetime.strptime(d.split("T")[0], "%Y-%m-%d")
                return parsed.strftime("%d-%b-%Y")
            except Exception:
                return str(d)
        return str(d)

    # State preservation logic
    if "preserved_expiry" not in st.session_state:
        st.session_state.preserved_expiry = None

    current_day_str = selected_day.strftime("%Y-%m-%d") if hasattr(selected_day, "strftime") else str(selected_day)
    if (st.session_state.get("last_expiry_selected_day") != current_day_str or
        st.session_state.get("last_expiry_symbol") != symbol):
        st.session_state.preserved_expiry = None
        st.session_state.last_expiry_selected_day = current_day_str
        st.session_state.last_expiry_symbol = symbol

    expiry_date = selected_day  # safe fallback value

    if not expiry_options:
        m3.warning("⚠️ No contract expiries found.")
    else:
        # Code readability & performance: use next() with fallback
        default_expiry = None
        if st.session_state.preserved_expiry in expiry_options:
            default_expiry = st.session_state.preserved_expiry
        else:
            default_expiry = next((exp for exp in expiry_options if exp >= selected_day), None)
            if default_expiry is None:
                default_expiry = expiry_options[0]

        default_expiry_index = expiry_options.index(default_expiry) if default_expiry in expiry_options else 0

        selected_exp = m3.selectbox(
            "Option expiry",
            options=expiry_options,
            index=default_expiry_index,
            format_func=safe_format_date,
            key="expiry_selectbox_key"
        )

        if selected_exp:
            expiry_date = selected_exp
            st.session_state.preserved_expiry = selected_exp

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
                start_iso = f"{selected_day.strftime('%Y-%m-%d')}T09:15:00.000Z"
                end_iso = f"{selected_day.strftime('%Y-%m-%d')}T15:30:00.000Z"
                exp_iso = f"{expiry_date.strftime('%Y-%m-%d')}T07:00:00.000Z"

                unauthorized = False
                for k in strikes:
                    if unauthorized:
                        break
                    for right in ["call", "put"]:
                        try:
                            d0 = get_hist(
                                client.api_key,
                                client.secret_key,
                                session,
                                symbol,
                                start_iso,
                                end_iso,
                                exp_iso,
                                right,
                                k,
                                interval
                            )
                            if not d0.empty:
                                d0["strike"] = k
                                d0["right"] = right
                                frames.append(d0)
                        except Exception as ex:
                            if "401" in str(ex) or "Unauthorized" in str(ex) or "unauthorized" in str(ex).lower():
                                unauthorized = True
                                st.error("❌ **Breeze Session Unauthorized (401)**: Your session token is invalid or expired. Please click 'Connect/Login ICICI Direct' on the sidebar to get a new session token, or switch 'Data mode' to 'Demo'.")
                                break
                            else:
                                st.warning(f"{right.upper()} {k}: {ex}")

                chain = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
                times = sorted(chain.datetime.dropna().unique()) if not chain.empty else []

                if chain.empty:
                    if unauthorized:
                        st.info("💡 Gracefully loaded high-fidelity simulated Option Chain in **Demo mode** so you can continue simulating!")
                    else:
                        st.toast("⚠️ Breeze API returned empty chain for this contract date. Gracefully loading un-cached high-fidelity simulated Option Chain.", icon="💡")
                    chain, times = demo_chain(atm, step, strike_count, selected_day)
                    st.session_state["breeze_fallback_active"] = True
                else:
                    st.session_state["breeze_fallback_active"] = False

        st.session_state.chain = chain
        st.session_state.times = times

        target = pd.Timestamp(datetime.combine(selected_day, selected_time))
        if len(times) > 0:
            st.session_state.idx = int(np.argmin([abs(pd.Timestamp(x) - target) for x in times]))
        st.session_state.mtm_history = []

chain=st.session_state.get("chain",pd.DataFrame()); times=st.session_state.get("times",[])
if chain.empty:
    st.info("Choose a date/time and expiry, then click Load / Generate Chain."); st.stop()

if st.session_state.get("breeze_fallback_active", False):
    st.info("💡 **Replay Engine Status**: Real ICICI Securities historical options database was empty for this specific date/expiry. Un-cached simulated Option Chain loaded so you can continuously trade and replay.")

if hasattr(times, "tolist"):
    times = times.tolist()

idx=min(st.session_state.get("idx",0),len(times)-1)

# Ensure AutoPlay continues running if active
import time as ptime
if st.session_state.autoplay:
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

# Calculate dynamic Future Price using cost of carry math
future_price = spot * np.exp(rate * T)

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

# Define quotes for global use
quotes = current_quotes(view)

# Risk check
if times:
    triggered = check_risk_triggers(pd.Timestamp(times[idx]), view)
    if triggered:
        st.rerun()

# --- HEADER: Account metrics & AutoPlay controls ---
st.markdown("---")
portfolio, pos_value = mark_portfolio(view)
current_capital = st.session_state.cash + pos_value
initial_capital = 1_000_000.0
total_pnl = current_capital - initial_capital

# Save MTM to history
if times:
    st.session_state.mtm_history.append({"time":pd.Timestamp(times[idx]),"mtm":total_pnl})
mh=pd.DataFrame(st.session_state.mtm_history).drop_duplicates("time").sort_values("time") if st.session_state.mtm_history else pd.DataFrame(columns=["time", "mtm"])

met1, met2, met3, met4, met5, met6 = st.columns(6)
met1.metric("Capital", f"₹{current_capital:,.2f}")
met2.metric("Portfolio Value", f"₹{pos_value:,.2f}")
met3.metric("Free Cash", f"₹{st.session_state.cash:,.2f}")
met4.metric("Total MTM P&L", f"₹{total_pnl:,.2f}", delta=f"₹{total_pnl:,.2f}")
met5.metric("Spot Price", f"₹{spot:,.2f}")
met6.metric("Future Price", f"₹{future_price:,.2f}")

# Hidden action dispatcher text input for JS communication
action_input = st.text_input("HiddenTradeSignalInput", key="action_input", placeholder="HiddenTradeSignalInput", label_visibility="collapsed")

if action_input:
    # Clear input first and parse immediately
    action = action_input
    parts = action.split(":")
    if len(parts) == 3:
        cmd, right_str, strike_str = parts
        strike_val = float(strike_str)
        right_val = right_str.upper()

        if cmd == "DESELECT":
            # Refund cash cleanly
            for p in st.session_state.positions:
                if p["strike"] == strike_val and p["right"].upper() == right_val:
                    if p["side"] == "BUY":
                        st.session_state.cash += p["qty"] * p["avg"]
                    else:
                        st.session_state.cash -= p["qty"] * p["avg"]
            st.session_state.positions = [p for p in st.session_state.positions if not (p["strike"] == strike_val and p["right"].upper() == right_val)]

            st.session_state.trade_history.append({
                "time": pd.Timestamp(times[idx]) if times else pd.Timestamp(selected_day),
                "action": f"DESELECT ({right_val})",
                "right": right_val,
                "strike": strike_val,
                "qty": 0,
                "price": 0.0,
                "realized": 0.0
            })
            st.rerun()

        elif cmd in ["BUY", "SELL"]:
            # Find matching active position to accumulate lots
            match_pos = None
            for p in st.session_state.positions:
                if p["strike"] == strike_val and p["right"].upper() == right_val and p["side"].upper() == cmd:
                    match_pos = p
                    break

            # Find price of that option in view
            ltp_val = 0.0
            for _, r_view in view.iterrows():
                if r_view.Strike == strike_val and r_view.Right == right_val:
                    ltp_val = float(r_view.LTP)
                    break

            if match_pos:
                # Accumulate quantity (+1 lot = 50 qty)
                match_pos["qty"] += 50
                if cmd == "BUY":
                    st.session_state.cash -= 50 * ltp_val
                else:
                    st.session_state.cash += 50 * ltp_val
            else:
                # Add new position (1 lot = 50 qty)
                new_pos = {
                    "id": str(uuid.uuid4()),
                    "right": right_val,
                    "strike": strike_val,
                    "side": cmd,
                    "qty": 50,
                    "avg": ltp_val,
                    "sl_pct": None,
                    "tp_pct": None,
                    "entry_time": str(pd.Timestamp(times[idx])) if times else str(selected_day)
                }
                st.session_state.positions.append(new_pos)
                if cmd == "BUY":
                    st.session_state.cash -= 50 * ltp_val
                else:
                    st.session_state.cash += 50 * ltp_val

            st.session_state.trade_history.append({
                "time": pd.Timestamp(times[idx]) if times else pd.Timestamp(selected_day),
                "action": f"{cmd} ({right_val})",
                "right": right_val,
                "strike": strike_val,
                "qty": 50,
                "price": ltp_val,
                "realized": 0.0
            })
            st.rerun()

# Row 2: Replay controls & AutoPlay bar
con1, con2 = st.columns([3, 2])
with con1:
    st.markdown("**⏱ Replay Controls**")
    j1,j2,j3,j4,j5,j6,j7 = st.columns(7)
    if j1.button("⏮ 1 bar", key="b1"): st.session_state.idx=move_index(times,idx,-1); st.rerun()
    if j2.button("1 bar ⏭", key="b2"): st.session_state.idx=move_index(times,idx,1); st.rerun()
    if j3.button("⏪ 5m", key="b3"): st.session_state.idx=move_index(times,idx,-1); st.rerun()
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
    st.subheader("📈 Premium Option Chain (StockMock Style)")
    st.caption("No fixed buttons. Hover CE LTP, PE LTP or Strike to display Buy (B) and Sell (S) controls. Left-click to accumulate lots (+50 qty per click). Right-click any cell to deselect option.")

    # 1. Calculate nearest ATM strike dynamically as the replay moves through spot prices
    nearest_atm = round(round(spot / step) * step, 2)

    # 2. Slice exactly 21 strikes centered around the active ATM strike
    active_strikes = sorted(round(nearest_atm + (i - 10) * step, 2) for i in range(21))

    # Filter the Option Chain view based on these 21 strikes
    sliced_view = view[view.Strike.isin(active_strikes)].sort_values(["Strike", "Right"])

    # CSS stylesheet and javascript communication payload injected inline
    html_elements = []
    html_elements.append("""
    <style>
    .mock-table {
        width: 100%;
        border-collapse: collapse;
        background-color: #0b0f19;
        color: #e2e8f0;
        font-family: ui-sans-serif, system-ui, sans-serif;
        border-radius: 8px;
        overflow: hidden;
    }
    .mock-table th {
        background-color: #0f172a;
        color: #94a3b8;
        font-size: 0.75rem;
        font-weight: 700;
        padding: 12px 8px;
        text-transform: uppercase;
        border-bottom: 2px solid #1e293b;
    }
    .mock-table td {
        padding: 10px 8px;
        border-bottom: 1px solid #1e293b;
        text-align: center;
        font-size: 0.85rem;
        position: relative;
    }
    .mock-table tr:hover {
        background-color: #1e293b40;
    }
    .itm-ce {
        background-color: #fbbf240d !important;
    }
    .itm-pe {
        background-color: #c084fc0d !important;
    }
    .hover-cell {
        cursor: pointer;
        transition: background-color 0.2s;
    }
    .hover-cell:hover {
        background-color: #1e293b80;
    }
    .hover-actions {
        position: absolute;
        inset: 0;
        display: none;
        align-items: center;
        justify-content: center;
        gap: 6px;
        background-color: rgba(15, 23, 42, 0.95);
        z-index: 20;
    }
    .hover-cell:hover .hover-actions {
        display: flex;
    }
    .btn-act {
        padding: 4px 10px;
        border-radius: 4px;
        font-weight: bold;
        font-size: 0.75rem;
        border: none;
        cursor: pointer;
        color: white;
        transition: transform 0.1s;
    }
    .btn-act:active {
        transform: scale(0.95);
    }
    .btn-b {
        background-color: #2563eb;
    }
    .btn-s {
        background-color: #dc2626;
    }
    .badge-act {
        display: inline-block;
        padding: 2px 6px;
        border-radius: 4px;
        font-size: 0.7rem;
        font-weight: bold;
        margin-top: 4px;
    }
    .badge-b {
        background-color: rgba(34, 197, 94, 0.2);
        color: #22c55e;
        border: 1px solid rgba(34, 197, 94, 0.4);
    }
    .badge-s {
        background-color: rgba(239, 68, 68, 0.2);
        color: #ef4444;
        border: 1px solid rgba(239, 68, 68, 0.4);
    }
    .strike-atm {
        background-color: #020617;
        border: 2px solid #22c55e !important;
        color: #38bdf8 !important;
        font-weight: 800;
        font-size: 1.05rem;
    }
    .strike-standard {
        background-color: #1e293b;
        color: #38bdf8;
        font-weight: bold;
    }
    </style>
    <script>
    function dispatchAction(actionStr) {
        window.parent.postMessage(actionStr, "*");
    }
    </script>
    """)

    html_elements.append("""
    <table class="mock-table">
        <thead>
            <tr>
                <th>CE IV</th>
                <th>CE Delta</th>
                <th>CE LTP (Hover to trade)</th>
                <th style="width: 14%;">Strike</th>
                <th>PE LTP (Hover to trade)</th>
                <th>PE Delta</th>
                <th>PE IV</th>
            </tr>
        </thead>
        <tbody>
    """)

    for strike in active_strikes:
        ce_row = sliced_view[(sliced_view.Strike == strike) & (sliced_view.Right == "CALL")]
        pe_row = sliced_view[(sliced_view.Strike == strike) & (sliced_view.Right == "PUT")]

        ce = ce_row.iloc[0] if not ce_row.empty else None
        pe = pe_row.iloc[0] if not pe_row.empty else None

        ce_net, ce_b, ce_s = get_strike_position_info(strike, "CALL")
        pe_net, pe_b, pe_s = get_strike_position_info(strike, "PUT")

        ce_is_itm = (strike < spot)
        pe_is_itm = (strike > spot)

        ce_class = "itm-ce" if ce_is_itm else ""
        pe_class = "itm-pe" if pe_is_itm else ""

        strike_class = "strike-atm" if abs(strike - spot) <= step * 0.51 else "strike-standard"

        # Build Call LTP Cell
        ce_ltp_html = ""
        if ce is not None:
            ce_ltp_val = f"₹{ce.LTP:.2f}"
            ce_badge_html = ""
            if ce_net > 0:
                ce_badge_html = f'<br/><span class="badge-act badge-b">B ({int(ce_net // 50)}x)</span>'
            elif ce_net < 0:
                ce_badge_html = f'<br/><span class="badge-act badge-s">S ({int(abs(ce_net) // 50)}x)</span>'

            ce_ltp_html = f"""
            <td class="hover-cell {ce_class}" oncontextmenu="event.preventDefault(); dispatchAction('DESELECT:CALL:{strike}');">
                <span>{ce_ltp_val}</span>{ce_badge_html}
                <div class="hover-actions">
                    <button class="btn-act btn-b" onclick="dispatchAction('BUY:CALL:{strike}')">B</button>
                    <button class="btn-act btn-s" onclick="dispatchAction('SELL:CALL:{strike}')">S</button>
                </div>
            </td>
            """
        else:
            ce_ltp_html = f"<td>-</td>"

        # Build Put LTP Cell
        pe_ltp_html = ""
        if pe is not None:
            pe_ltp_val = f"₹{pe.LTP:.2f}"
            pe_badge_html = ""
            if pe_net > 0:
                pe_badge_html = f'<br/><span class="badge-act badge-b">B ({int(pe_net // 50)}x)</span>'
            elif pe_net < 0:
                pe_badge_html = f'<br/><span class="badge-act badge-s">S ({int(abs(pe_net) // 50)}x)</span>'

            pe_ltp_html = f"""
            <td class="hover-cell {pe_class}" oncontextmenu="event.preventDefault(); dispatchAction('DESELECT:PUT:{strike}');">
                <span>{pe_ltp_val}</span>{pe_badge_html}
                <div class="hover-actions">
                    <button class="btn-act btn-b" onclick="dispatchAction('BUY:PUT:{strike}')">B</button>
                    <button class="btn-act btn-s" onclick="dispatchAction('SELL:PUT:{strike}')">S</button>
                </div>
            </td>
            """
        else:
            pe_ltp_html = f"<td>-</td>"

        # Build Strike cell with double hover triggers (buying CALL on left side, PUT on right side)
        strike_cell_html = f"""
        <td class="{strike_class} hover-cell">
            <span>{strike:,.0f}</span>
            <div class="hover-actions">
                <button class="btn-act btn-b" style="background-color: #059669;" onclick="dispatchAction('BUY:CALL:{strike}')">B CE</button>
                <button class="btn-act btn-b" style="background-color: #7c3aed;" onclick="dispatchAction('BUY:PUT:{strike}')">B PE</button>
            </div>
        </td>
        """

        ce_iv_html = f"<td>{ce['IV %']:.1f}%</td>" if ce is not None else "<td>-</td>"
        ce_delta_html = f"<td>{ce.Delta:.2f}</td>" if ce is not None else "<td>-</td>"

        pe_iv_html = f"<td>{pe['IV %']:.1f}%</td>" if pe is not None else "<td>-</td>"
        pe_delta_html = f"<td>{pe.Delta:.2f}</td>" if pe is not None else "<td>-</td>"

        row_html = f"""
        <tr>
            {ce_iv_html}
            {ce_delta_html}
            {ce_ltp_html}
            {strike_cell_html}
            {pe_ltp_html}
            {pe_delta_html}
            {pe_iv_html}
        </tr>
        """
        html_elements.append(row_html)

    html_elements.append("""
        </tbody>
    </table>
    """)

    # Render option chain beautifully
    st.components.v1.html("\n".join(html_elements), height=720, scrolling=True)

    # Strategy Builder Section
    st.markdown("---")
    st.subheader("🧩 Multi-Leg Strategy Builder")
    st.caption("Construct multi-leg strategies manually or import popular template strategies. Customize Stop Loss % and Take Profit % per leg and deploy them together.")

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
            try:
                l["Symbol"] = format_contract_symbol(symbol, expiry_date, l["strike"], l["right"])
            except Exception:
                l["Symbol"] = ""
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
        col_clear_1, col_clear_2 = st.columns([1, 4])
        if col_clear_1.button("🚨 Clear All Positions", type="primary", use_container_width=True):
            st.session_state.positions = []
            st.session_state.cash = 1_000_000.0
            st.session_state.trade_history = []
            st.session_state.mtm_history = []
            st.rerun()

        for i, row in portfolio.iterrows():
            c1, c2, c3, c4, c5, c6, c7, c8 = st.columns([1.5, 1, 1, 1, 1.2, 1, 1, 1])
            try:
                pos_sym = format_contract_symbol(symbol, expiry_date, row.Strike, row.Right)
                symbol_display = f" ({pos_sym})"
            except Exception:
                symbol_display = ""
            c1.markdown(f"**{row.Side} {row.Right} {row.Strike:,.0f}**<br/><span style='font-size:0.75rem; color:#8b949e;'>{symbol_display}</span>", unsafe_allow_html=True)

            new_qty = c2.number_input("Qty", 25, 100000, int(row.Qty), step=25, key=f"qty_input_{row.id}")
            if new_qty != int(row.Qty):
                p_id = row.id
                pos_index = next((index for index, item in enumerate(st.session_state.positions) if item.get("id") == p_id), None)
                if pos_index is not None:
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
        fig = draw_consolidated_payoff_chart(active_legs, spot, step, T, rate, div)
        st.plotly_chart(fig, use_container_width=True)

    # Execution trade history
    st.markdown("---")
    st.subheader("📜 Execution History")
    if st.session_state.trade_history:
        st.dataframe(pd.DataFrame(st.session_state.trade_history).sort_values("time", ascending=False), use_container_width=True, hide_index=True)
    else:
        st.info("No trades executed yet.")
