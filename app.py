import os
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from breeze_client import BreezeClient
from greeks import implied_vol, greeks

load_dotenv()
st.set_page_config(page_title="Breeze Option Simulator", page_icon="📊", layout="wide", initial_sidebar_state="expanded")

# ---------- Theme ----------
st.markdown("""
<style>
.stApp { background:#0b1220; color:#e5e7eb; }
[data-testid="stSidebar"] { background:#101827; }
.block-container { padding-top:1rem; max-width:1500px; }
.card { background:#111c2e; border:1px solid #24334a; border-radius:12px; padding:14px 16px; }
.small { color:#94a3b8; font-size:12px; }
.big { font-size:25px; font-weight:700; }
.buy { color:#22c55e; font-weight:700; }
.sell { color:#ef4444; font-weight:700; }
.atm { background:#1d2a3d; border-radius:5px; }
</style>
""", unsafe_allow_html=True)

# ---------- Helpers ----------
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

def demo_chain(symbol, atm, step, count):
    rng = np.random.default_rng(42)
    start = pd.Timestamp("2026-08-07 09:15", tz="UTC")
    times = pd.date_range(start, periods=76, freq="5min")
    spot_path = atm + np.cumsum(rng.normal(0, step * 0.06, len(times)))
    strikes = [round(atm + (i - count // 2) * step, 2) for i in range(count)]
    rows = []
    expiry = pd.Timestamp("2026-08-13 10:00", tz="UTC")
    for t, spot in zip(times, spot_path):
        T = max((expiry - t).total_seconds() / (365 * 86400), 1e-5)
        for k in strikes:
            m = (spot-k)/max(spot,1)
            base = max(abs(spot-k)*0.55, 8) * (0.85 + 0.35*np.exp(-abs(m)*35))
            for right in ["call", "put"]:
                intrinsic = max(spot-k,0) if right=="call" else max(k-spot,0)
                ltp = intrinsic + base + rng.normal(0, 1.2)
                rows.append({"datetime":t,"close":max(0.5,ltp),"volume":int(rng.integers(1000,25000)),"open_interest":int(rng.integers(5000,150000)),"strike":k,"right":right,"spot":spot})
    return pd.DataFrame(rows)

def make_greeks(chain, spot, expiry, now, rate, div):
    out=[]
    expiry_dt=pd.Timestamp(expiry, tz="UTC") if not pd.Timestamp(expiry).tzinfo else pd.Timestamp(expiry)
    T=max((expiry_dt-now).total_seconds()/(365*86400),1e-8)
    for _, r in chain.iterrows():
        iv=implied_vol(float(r.close),spot,float(r.strike),T,rate,div,r.right)
        g=greeks(spot,float(r.strike),T,rate,div,iv,r.right)
        out.append({"Strike":r.strike,"Right":r.right.upper(),"LTP":r.close,"OI":r.get("open_interest",np.nan),"Volume":r.get("volume",np.nan),"IV %":iv*100 if np.isfinite(iv) else np.nan,**{k.title():v for k,v in g.items()}})
    return pd.DataFrame(out)

# ---------- Session ----------
client=BreezeClient()
api_session=st.query_params.get("API_Session") or st.query_params.get("api_session")
if api_session and client.configured:
    try:
        st.session_state["session_token"] = client.exchange_api_session(api_session)
        st.query_params.clear()
    except Exception as e:
        st.error(f"Breeze login failed: {e}")
session=st.session_state.get("session_token")

# ---------- Sidebar ----------
with st.sidebar:
    st.markdown("## 📊 Option Replay")
    mode=st.radio("Data source", ["Demo Mode", "ICICI Breeze"], horizontal=True)
    st.divider()
    st.subheader("Market")
    symbol=st.selectbox("Underlying", ["NIFTY", "BANKNIFTY", "FINNIFTY"])
    atm=st.number_input("ATM strike", value=25000.0 if symbol=="NIFTY" else 55000.0, step=50.0)
    step=st.number_input("Strike interval", value=50.0, min_value=0.05, step=50.0)
    count=st.slider("Strikes", 10, 20, 20)
    rate=st.number_input("Risk-free rate %", value=6.5, step=0.25)/100
    div=st.number_input("Dividend yield %", value=0.0, step=0.25)/100
    st.divider()
    if mode=="ICICI Breeze":
        st.subheader("🔐 Breeze connection")
        if client.configured:
            st.link_button("Connect ICICI Direct", client.login_url(), use_container_width=True)
            st.caption("API secrets stay in .env/server secrets.")
            st.success("Connected" if session else "Not connected")
        else:
            st.warning("Add BREEZE_API_KEY and BREEZE_SECRET_KEY to .env")
    st.divider()
    st.caption("Paper trading only • No live orders")

# ---------- Header ----------
st.markdown("# 📈 Breeze Option Simulator")
status="🟢 Demo market" if mode=="Demo Mode" else ("🟢 Breeze connected" if session else "🟠 Breeze login required")
st.markdown(f"<span class='small'>{status} &nbsp; • &nbsp; Historical replay &nbsp; • &nbsp; Paper trading</span>", unsafe_allow_html=True)

# ---------- Data controls ----------
if mode=="ICICI Breeze":
    c1,c2,c3,c4=st.columns(4)
    expiry=c1.text_input("Expiry ISO", "2026-08-13T06:00:00.000Z")
    start=c2.text_input("Start ISO", "2026-08-07T09:15:00.000Z")
    end=c3.text_input("End ISO", "2026-08-07T15:30:00.000Z")
    interval=c4.selectbox("Interval", ["1minute","5minute","30minute","1day"], index=1)
    if st.button("⬇️ Load historical data", type="primary", use_container_width=True):
        if not session:
            st.error("Connect ICICI Direct first.")
        else:
            frames=[]
            with st.spinner("Loading historical option chain..."):
                strikes=sorted(round(atm+(i-count//2)*step,2) for i in range(count))
                for k in strikes:
                    for right in ["call","put"]:
                        try:
                            d=get_hist(client.api_key,session,symbol,start,end,expiry,right,k,interval)
                            if not d.empty:
                                d["strike"]=k; d["right"]=right; frames.append(d)
                        except Exception as e:
                            st.warning(f"{right.upper()} {k}: {e}")
            st.session_state.chain=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()
            st.session_state.idx=0
            st.session_state.expiry=expiry
else:
    expiry="2026-08-13T10:00:00Z"
    if "chain" not in st.session_state or st.session_state.get("demo_key")!=(symbol,atm,step,count):
        st.session_state.chain=demo_chain(symbol,atm,step,count)
        st.session_state.demo_key=(symbol,atm,step,count)
        st.session_state.idx=0
        st.session_state.expiry=expiry

chain=st.session_state.get("chain",pd.DataFrame())
if chain.empty:
    st.info("Choose Demo Mode to explore the interface immediately, or connect Breeze and load historical data.")
    st.stop()

# ---------- Replay toolbar ----------
times=sorted(chain.datetime.dropna().unique())
idx=min(st.session_state.get("idx",0),len(times)-1)
now=pd.Timestamp(times[idx]);

b1,b2,b3,b4,b5,b6=st.columns([1,1,1.5,1.2,1.2,1.5])
if b1.button("⏮", use_container_width=True): st.session_state.idx=max(0,idx-1); st.rerun()
if b2.button("▶ Next", use_container_width=True): st.session_state.idx=min(len(times)-1,idx+1); st.rerun()
b3.markdown(f"**Replay:** `{now.strftime('%d %b %Y  %H:%M')}`")
speed=b4.selectbox("Speed", ["0.5×","1×","2×","5×"], index=1, label_visibility="collapsed")
spot_default=float(chain.loc[chain.datetime==now,"spot"].iloc[0]) if "spot" in chain.columns else float(atm)
spot=b5.number_input("Spot", value=round(spot_default,2), step=float(step), label_visibility="collapsed")
if b6.button("↺ Reset replay", use_container_width=True): st.session_state.idx=0; st.rerun()

view=make_greeks(chain[chain.datetime==now],spot,st.session_state.get("expiry",expiry),now,rate,div)
near=min(view.Strike,key=lambda x:abs(x-spot))

# ---------- KPI row ----------
ce,pe,ivm=st.columns(3)
atm_rows=view[view.Strike==near]
call_ltp=float(atm_rows[atm_rows.Right=="CALL"].LTP.iloc[0]) if not atm_rows[atm_rows.Right=="CALL"].empty else 0
put_ltp=float(atm_rows[atm_rows.Right=="PUT"].LTP.iloc[0]) if not atm_rows[atm_rows.Right=="PUT"].empty else 0
ce.metric(f"ATM {near} CE", f"₹{call_ltp:,.2f}")
pe.metric(f"ATM {near} PE", f"₹{put_ltp:,.2f}")
ivm.metric("ATM IV", f"{float(atm_rows['IV %'].mean()):.2f}%" if not atm_rows.empty else "—")

# ---------- Option chain ----------
st.subheader("Option Chain")
st.caption("Click an option by selecting it in the paper-order ticket below. ATM strike is highlighted conceptually by the Spot/Strike relationship.")
left=view[view.Right=="CALL"].set_index("Strike")
right=view[view.Right=="PUT"].set_index("Strike")
chain_ui=pd.DataFrame(index=sorted(view.Strike.unique()))
for col,src,name in [("Call LTP",left,"LTP"),("Call IV %",left,"IV %"),("Δ CE",left,"Delta"),("Γ CE",left,"Gamma"),("Θ CE",left,"Theta"),("CE OI",left,"OI")]: chain_ui[col]=src[name]
chain_ui["STRIKE"]=chain_ui.index
for col,src,name in [("PE OI",right,"OI"),("Θ PE",right,"Theta"),("Γ PE",right,"Gamma"),("Δ PE",right,"Delta"),("Put IV %",right,"IV %"),("Put LTP",right,"LTP")]: chain_ui[col]=src[name]
cols=["Call LTP","Call IV %","Δ CE","Γ CE","Θ CE","CE OI","STRIKE","PE OI","Θ PE","Γ PE","Δ PE","Put IV %","Put LTP"]
st.dataframe(chain_ui[cols].round(2), use_container_width=True, height=520)

# ---------- Chart ----------
st.subheader("Replay Chart")
chart_data=chain[chain.strike==near].sort_values("datetime")
fig=go.Figure()
for r in ["call","put"]:
    p=chart_data[chart_data.right==r]
    fig.add_trace(go.Scatter(x=p.datetime,y=p.close,mode="lines",name=r.upper()))
fig.add_vline(x=now,line_dash="dash",annotation_text="Replay")
fig.update_layout(height=360,margin=dict(l=10,r=10,t=10,b=10),template="plotly_dark",paper_bgcolor="#111c2e",plot_bgcolor="#111c2e",legend=dict(orientation="h"))
st.plotly_chart(fig,use_container_width=True)

# ---------- Paper trading ----------
st.subheader("🛒 Paper Order")
o1,o2,o3,o4,o5=st.columns([1,1,1.3,1.2,1])
side=o1.selectbox("Side",["BUY","SELL"]); right_sel=o2.selectbox("Option",["CALL","PUT"]); strike=o3.selectbox("Strike",sorted(view.Strike.unique()),index=list(sorted(view.Strike.unique())).index(near)); qty=o4.number_input("Quantity",1,100000,50,step=50); o5.markdown(f"**LTP**\n\n₹{float(view[(view.Strike==strike)&(view.Right==right_sel)].LTP.iloc[0]):,.2f}")
price=float(view[(view.Strike==strike)&(view.Right==right_sel)].LTP.iloc[0])
if st.button(f"{('🟢' if side=='BUY' else '🔴')} {side} {right_sel} {strike}", type="primary", use_container_width=True):
    positions=st.session_state.setdefault("positions",{})
    key=(right_sel,float(strike)); signed=qty if side=="BUY" else -qty
    positions[key]=positions.get(key,0)+signed
    st.session_state.cash=st.session_state.get("cash",1_000_000.0)-signed*price
    st.success(f"Paper order filled: {side} {qty} {right_sel} {strike} @ ₹{price:,.2f}")

# ---------- Portfolio ----------
st.subheader("💰 Portfolio")
cash=st.session_state.get("cash",1_000_000.0); rows=[]; unreal=0
for (r,k),q in st.session_state.get("positions",{}).items():
    x=view[(view.Strike==k)&(view.Right==r)]
    l=float(x.LTP.iloc[0]) if not x.empty else np.nan
    mv=q*l if np.isfinite(l) else 0
    unreal+=mv
    rows.append({"Option":f"NIFTY {int(k)} {r}","Qty":q,"LTP":l,"Market Value":mv})
m1,m2,m3=st.columns(3); m1.metric("Available Cash",f"₹{cash:,.2f}"); m2.metric("Position Value",f"₹{unreal:,.2f}"); m3.metric("Portfolio Value",f"₹{cash+unreal:,.2f}")
if rows: st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
else: st.info("No paper positions yet.")

st.caption("Demo Mode uses synthetic data for UI exploration. Breeze mode uses your authenticated historical data. This application does not place live orders.")
