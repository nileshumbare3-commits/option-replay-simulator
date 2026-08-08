import os
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from breeze_client import BreezeClient
from greeks import implied_vol, greeks

load_dotenv()
st.set_page_config(page_title="Breeze Option Replay", page_icon="📈", layout="wide")

# ---------- helpers ----------
def norm(rows):
    df = pd.DataFrame(rows)
    if df.empty or "datetime" not in df or "close" not in df:
        return pd.DataFrame()
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    for c in ["open", "high", "low", "close", "volume", "open_interest"]:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["datetime", "close"])

@st.cache_data(ttl=300, show_spinner=False)
def get_hist(api_key, session, symbol, start, end, expiry, right, strike, interval):
    c = BreezeClient(api_key=api_key, secret_key=os.getenv("BREEZE_SECRET_KEY"), session_token=session)
    return norm(c.historical_option(symbol, start, end, expiry, right, strike, interval))

def demo_chain(atm, step, count):
    rng = np.random.default_rng(7)
    times = pd.date_range("2026-08-07 09:15", "2026-08-07 15:30", freq="5min")
    strikes = [round(atm + (i - count // 2) * step, 2) for i in range(count)]
    rows = []
    spot_path = atm + np.cumsum(rng.normal(0, step * .025, len(times)))
    for ti, ts in enumerate(times):
        spot = spot_path[ti]
        for k in strikes:
            m = max(abs(spot - k), step * .1)
            for right in ["call", "put"]:
                intrinsic = max(spot-k, 0) if right == "call" else max(k-spot, 0)
                time_value = max(5, step * .8 * np.exp(-m / (step * 2)))
                noise = rng.normal(0, .8)
                price = max(.5, intrinsic + time_value + noise)
                rows.append({"datetime": ts, "close": price, "volume": int(rng.integers(500, 15000)),
                             "open_interest": int(rng.integers(5000, 90000)), "strike": k, "right": right})
    return pd.DataFrame(rows), times

def payoff(legs, spots):
    total = np.zeros_like(spots, dtype=float)
    for leg in legs:
        qty = leg["qty"]
        strike = leg["strike"]
        premium = leg["premium"]
        right = leg["right"]
        side = leg["side"]
        intrinsic = np.maximum(spots-strike, 0) if right == "CALL" else np.maximum(strike-spots, 0)
        per_unit = intrinsic - premium if side == "BUY" else premium - intrinsic
        total += qty * per_unit
    return total

# ---------- session ----------
if "demo" not in st.session_state:
    st.session_state.demo = True
if "positions" not in st.session_state:
    st.session_state.positions = {}
if "cash" not in st.session_state:
    st.session_state.cash = 1_000_000.0
if "strategy_legs" not in st.session_state:
    st.session_state.strategy_legs = []

st.markdown("""
<style>
.block-container {padding-top: 1rem; max-width: 1700px;}
.metric-card {padding: 12px 16px; border: 1px solid rgba(128,128,128,.25); border-radius: 12px;}
.small-muted {color:#8b949e; font-size:.85rem;}
</style>
""", unsafe_allow_html=True)

st.title("📈 Breeze Option Replay")
st.caption("Historical paper-trading simulator • No live orders")

# ---------- top controls ----------
with st.container(border=True):
    a,b,c,d,e,f = st.columns([1.2,1.2,1.3,1.5,1.3,1.2])
    symbol = a.selectbox("Underlying", ["NIFTY", "BANKNIFTY", "FINNIFTY"], index=0)
    mode = b.selectbox("Data mode", ["Demo", "Breeze"])
    expiry = c.text_input("Expiry", "2026-08-13T06:00:00.000Z")
    interval = d.selectbox("Interval", ["1minute", "5minute", "15minute", "30minute", "1day"], index=1)
    atm = e.number_input("ATM", 1000.0, step=50.0, value=25000.0)
    step = f.number_input("Strike step", 5.0, step=50.0, value=50.0)

client = BreezeClient()
with st.sidebar:
    st.header("🔐 Connection")
    if client.configured:
        st.link_button("Connect ICICI Direct", client.login_url(), use_container_width=True)
    else:
        st.info("Breeze credentials are not configured. Demo mode is available now.")
    api_session = st.query_params.get("API_Session") or st.query_params.get("api_session")
    if api_session and client.configured:
        try:
            st.session_state["session_token"] = client.exchange_api_session(api_session)
            st.query_params.clear()
        except Exception as ex:
            st.error(str(ex))
    if st.session_state.get("session_token"):
        st.success("● Breeze connected")
    else:
        st.warning("● Paper / Demo")

with st.sidebar:
    st.header("⏱ Replay")
    start = st.text_input("Start", "2026-08-07T09:15:00.000Z")
    end = st.text_input("End", "2026-08-07T15:30:00.000Z")
    strike_count = st.slider("Strikes", 4, 20, 20)
    rate = st.number_input("Risk-free %", 6.5, step=.25) / 100
    div = st.number_input("Dividend %", 0.0, step=.25) / 100
    if st.button("🔄 Load / Generate Chain", type="primary", use_container_width=True):
        if mode == "Demo":
            chain, times = demo_chain(atm, step, strike_count)
        else:
            session = st.session_state.get("session_token")
            if not session:
                st.error("Connect Breeze first.")
                chain, times = pd.DataFrame(), []
            else:
                frames = []
                strikes = sorted(round(atm + (i-strike_count//2)*step, 2) for i in range(strike_count))
                for k in strikes:
                    for right in ["call", "put"]:
                        try:
                            d0 = get_hist(client.api_key, session, symbol, start, end, expiry, right, k, interval)
                            if not d0.empty:
                                d0["strike"] = k; d0["right"] = right; frames.append(d0)
                        except Exception as ex:
                            st.warning(f"{right.upper()} {k}: {ex}")
                chain = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
                times = sorted(chain.datetime.dropna().unique()) if not chain.empty else []
        st.session_state.chain = chain
        st.session_state.times = times
        st.session_state.idx = 0

chain = st.session_state.get("chain", pd.DataFrame())
times = st.session_state.get("times", [])
if chain.empty:
    st.info("👈 Choose Demo mode and click **Load / Generate Chain** to explore the simulator without API credentials.")
    st.stop()

idx = min(st.session_state.get("idx", 0), len(times)-1)
with st.container(border=True):
    p1,p2,p3,p4,p5 = st.columns([1,1,1,1,2])
    if p1.button("⏮", use_container_width=True): st.session_state.idx = max(0, idx-1); st.rerun()
    if p2.button("▶ Next", use_container_width=True): st.session_state.idx = min(len(times)-1, idx+1); st.rerun()
    if p3.button("⏪ -5", use_container_width=True): st.session_state.idx = max(0, idx-5); st.rerun()
    if p4.button("⏩ +5", use_container_width=True): st.session_state.idx = min(len(times)-1, idx+5); st.rerun()
    p5.metric("Replay time", pd.Timestamp(times[idx]).strftime("%d %b %Y  %H:%M"))

snap = chain[chain.datetime == times[idx]].copy()
spot = st.number_input("Underlying spot", value=float(atm), step=float(step))
expiry_dt = pd.to_datetime(expiry, utc=True, errors="coerce")
rt = pd.Timestamp(times[idx]); rt = rt.tz_localize("UTC") if rt.tzinfo is None else rt.tz_convert("UTC")
T = max((expiry_dt-rt).total_seconds()/(365*24*3600), 1e-8) if pd.notna(expiry_dt) else 1e-8

out=[]
for _, r in snap.iterrows():
    iv = implied_vol(float(r.close), spot, float(r.strike), T, rate, div, r.right)
    g = greeks(spot, float(r.strike), T, rate, div, iv, r.right)
    out.append({"Strike":r.strike,"Right":r.right.upper(),"LTP":r.close,"Volume":r.get("volume",np.nan),"OI":r.get("open_interest",np.nan),"IV %":iv*100 if np.isfinite(iv) else np.nan, **{k.title():v for k,v in g.items()}})
view=pd.DataFrame(out).sort_values(["Strike","Right"])

# ---------- option chain + click-to-ticket ----------
st.subheader("Option Chain")
left, right = st.columns([5, 2])
with left:
    st.dataframe(view, use_container_width=True, hide_index=True, height=520,
                 column_config={"LTP":st.column_config.NumberColumn(format="₹%.2f"), "IV %":st.column_config.NumberColumn(format="%.2f")})
with right:
    st.markdown("### 🎯 Order Ticket")
    selected_right = st.selectbox("Option", ["CALL", "PUT"])
    selected_strike = st.selectbox("Strike", sorted(view.Strike.unique()), index=len(sorted(view.Strike.unique()))//2)
    selected_side = st.radio("Action", ["BUY", "SELL"], horizontal=True)
    selected_qty = st.number_input("Quantity", 1, 100000, 50, step=50)
    rr = view[(view.Strike == selected_strike) & (view.Right == selected_right)]
    selected_price = float(rr.iloc[0].LTP) if not rr.empty else 0
    st.metric("Market price", f"₹{selected_price:,.2f}")
    if st.button(f"{selected_side} {selected_right}", type="primary", use_container_width=True):
        key=(selected_right,float(selected_strike)); signed=selected_qty if selected_side=="BUY" else -selected_qty
        st.session_state.positions[key]=st.session_state.positions.get(key,0)+signed
        st.session_state.cash -= signed*selected_price
        st.success(f"Paper order: {selected_side} {selected_qty} {selected_right} {selected_strike}")

# ---------- chart ----------
near=min(view.Strike.unique(), key=lambda x: abs(x-spot))
plot=chain[chain.strike==near]
fig=go.Figure()
for right_name in ["call","put"]:
    p=plot[plot.right==right_name].sort_values("datetime")
    fig.add_trace(go.Scatter(x=p.datetime,y=p.close,mode="lines",name=right_name.upper()))
fig.add_vline(x=pd.Timestamp(times[idx]), line_dash="dash")
fig.update_layout(height=360, margin=dict(l=10,r=10,t=30,b=10), title=f"ATM option replay • {near}")
st.plotly_chart(fig, use_container_width=True)

# ---------- strategy builder ----------
st.subheader("🧩 Strategy Builder")
with st.container(border=True):
    s1,s2,s3,s4,s5,s6 = st.columns([1.2,1.2,1.4,1.2,1.2,1.2])
    leg_side=s1.selectbox("Side", ["BUY","SELL"], key="leg_side")
    leg_right=s2.selectbox("Type", ["CALL","PUT"], key="leg_right")
    leg_strike=s3.selectbox("Strike", sorted(view.Strike.unique()), key="leg_strike")
    leg_qty=s4.number_input("Qty", 1, 100000, 50, step=50, key="leg_qty")
    leg_row=view[(view.Strike==leg_strike)&(view.Right==leg_right)]
    leg_premium=float(leg_row.iloc[0].LTP) if not leg_row.empty else 0
    s5.metric("Premium", f"₹{leg_premium:.2f}")
    if s6.button("+ Add leg", use_container_width=True):
        st.session_state.strategy_legs.append({"side":leg_side,"right":leg_right,"strike":float(leg_strike),"qty":int(leg_qty),"premium":leg_premium})

if st.session_state.strategy_legs:
    legs_df=pd.DataFrame(st.session_state.strategy_legs)
    st.dataframe(legs_df, use_container_width=True, hide_index=True)
    ca,cb=st.columns([1,1])
    if ca.button("🗑 Clear strategy"): st.session_state.strategy_legs=[]; st.rerun()
    spots=np.linspace(max(1, spot-step*20), spot+step*20, 161)
    pnl=payoff(st.session_state.strategy_legs, spots)
    fig2=go.Figure(go.Scatter(x=spots,y=pnl,mode="lines",fill="tozeroy",name="P&L"))
    fig2.add_vline(x=spot,line_dash="dash")
    fig2.add_hline(y=0,line_dash="dot")
    fig2.update_layout(height=400, title="Expiry Payoff / P&L", xaxis_title="Underlying at expiry", yaxis_title="P&L (₹)")
    st.plotly_chart(fig2, use_container_width=True)
    max_profit=float(np.max(pnl)); max_loss=float(np.min(pnl))
    m1,m2,m3=st.columns(3); m1.metric("Max profit",f"₹{max_profit:,.0f}"); m2.metric("Max loss",f"₹{max_loss:,.0f}"); m3.metric("Breakeven approx",f"{spots[np.argmin(np.abs(pnl))]:,.0f}")

# ---------- portfolio ----------
st.subheader("💼 Paper Portfolio")
positions=[]
for (r,k),q in st.session_state.positions.items():
    x=view[(view.Strike==k)&(view.Right==r)]
    l=float(x.iloc[0].LTP) if not x.empty else np.nan
    positions.append({"Right":r,"Strike":k,"Qty":q,"LTP":l,"Market Value":q*l if np.isfinite(l) else np.nan})
portfolio=pd.DataFrame(positions)
if portfolio.empty:
    st.info("No paper positions yet.")
else:
    st.dataframe(portfolio,use_container_width=True,hide_index=True)
    unreal=float(portfolio["Market Value"].sum())
    c1,c2,c3=st.columns(3); c1.metric("Cash",f"₹{st.session_state.cash:,.2f}"); c2.metric("Position value",f"₹{unreal:,.2f}"); c3.metric("Net liquidation",f"₹{st.session_state.cash+unreal:,.2f}")
