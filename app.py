import os
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from datetime import date, time, datetime, timedelta
from dotenv import load_dotenv
from breeze_client import BreezeClient
from greeks import implied_vol, greeks

load_dotenv()
st.set_page_config(page_title="Breeze Option Replay", page_icon="📈", layout="wide")

# ---------- helpers ----------
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
        intrinsic=np.maximum(spots-leg["strike"],0) if leg["right"]=="CALL" else np.maximum(leg["strike"]-spots,0)
        per=intrinsic-leg["premium"] if leg["side"]=="BUY" else leg["premium"]-intrinsic
        total += leg["qty"]*per
    return total

def move_index(times, idx, delta):
    if not times: return idx
    return max(0,min(len(times)-1,idx+delta))

def current_quotes(view):
    return {(str(r.Right),float(r.Strike)):float(r.LTP) for _,r in view.iterrows()}

def mark_portfolio(view):
    quotes=current_quotes(view); total=0.0; rows=[]
    for key,p in st.session_state.positions.items():
        r,k=key; qty=p["qty"]; ltp=quotes.get((r,k),np.nan)
        mv=qty*ltp if np.isfinite(ltp) else np.nan
        unreal=((ltp-p["avg"])*qty) if np.isfinite(ltp) else np.nan
        rows.append({"Right":r,"Strike":k,"Qty":qty,"Avg":p["avg"],"LTP":ltp,"Unrealized P&L":unreal})
        if np.isfinite(mv): total += mv
    return pd.DataFrame(rows),total

# ---------- state ----------
for key,default in [("positions",{}),("cash",1_000_000.0),("trade_history",[]),("mtm_history",[]),("strategy_legs",[])]:
    if key not in st.session_state: st.session_state[key]=default

st.markdown("""
<style>
.block-container{padding-top:1rem;max-width:1750px}.small{color:#8b949e;font-size:.85rem}
</style>
""",unsafe_allow_html=True)
st.title("📈 Breeze Option Replay")
st.caption("Historical paper-trading simulator • every paper fill is replay-time stamped • no live orders")

client=BreezeClient()

with st.sidebar:
    st.header("🔐 Connection")
    if client.configured: st.link_button("Connect ICICI Direct",client.login_url(),use_container_width=True)
    else: st.info("Demo mode works without credentials.")
    api_session=st.query_params.get("API_Session") or st.query_params.get("api_session")
    if api_session and client.configured:
        try:
            st.session_state["session_token"]=client.exchange_api_session(api_session); st.query_params.clear()
        except Exception as ex: st.error(str(ex))
    st.success("● Breeze connected" if st.session_state.get("session_token") else "● Demo / Paper")
    st.header("📅 Market setup")
    atm=st.number_input("ATM strike",1000.0,step=50.0,value=25000.0)
    strike_count=st.slider("Strikes",4,20,20)
    expiry_date=st.date_input("Option expiry",date(2026,8,13))
    expiry_time=st.time_input("Expiry time",time(15,30))
    rate=st.number_input("Risk-free %",6.5,step=.25)/100
    div=st.number_input("Dividend %",0.0,step=.25)/100

tab_replay, tab_extractor = st.tabs(["📈 Option Replay Simulator", "📅 Historical Data Extractor"])

with tab_replay:
    # ---------- controls ----------
    with st.container(border=True):
        a,b,c,d,e,f=st.columns([1.1,1.0,1.35,1.15,1.2,1.1])
        symbol=a.selectbox("Underlying",["NIFTY","BANKNIFTY","FINNIFTY"])
        mode=b.selectbox("Data mode",["Demo","Breeze"])
        selected_day=c.date_input("Replay date",date(2026,8,7))
        selected_time=d.time_input("Replay time",time(9,15),step=300)
        interval=e.selectbox("Bar interval",["1minute","5minute","15minute","30minute","1day"],index=1)
        step=f.number_input("Strike step",5.0,step=50.0,value=50.0)

    if st.button("🔄 Load / Generate Chain",type="primary",use_container_width=True):
        if mode=="Demo":
            chain,times=demo_chain(atm,step,strike_count,selected_day)
        else:
            session=st.session_state.get("session_token")
            frames=[]; strikes=sorted(round(atm+(i-strike_count//2)*step,2) for i in range(strike_count))
            if not session: st.error("Connect Breeze first."); chain,times=pd.DataFrame(),[]
            else:
                start_iso=f"{selected_day}T09:15:00.000Z"; end_iso=f"{selected_day}T15:30:00.000Z"; exp_iso=f"{expiry_date}T{expiry_time.strftime('%H:%M:%S')}.000Z"
                for k in strikes:
                    for right in ["call","put"]:
                        try:
                            d0=get_hist(client.api_key,session,symbol,start_iso,end_iso,exp_iso,right,k,interval)
                            if not d0.empty: d0["strike"]=k; d0["right"]=right; frames.append(d0)
                        except Exception as ex: st.warning(f"{right.upper()} {k}: {ex}")
                chain=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame(); times=sorted(chain.datetime.dropna().unique()) if not chain.empty else []
        st.session_state.chain=chain; st.session_state.times=times
        target=pd.Timestamp(datetime.combine(selected_day,selected_time))
        if times: st.session_state.idx=int(np.argmin([abs(pd.Timestamp(x)-target) for x in times]))
        st.session_state.mtm_history=[]

    chain=st.session_state.get("chain",pd.DataFrame()); times=st.session_state.get("times",[])
    if chain.empty:
        st.info("Choose a date/time and expiry, then click Load / Generate Chain.")
    else:
        idx=min(st.session_state.get("idx",0),len(times)-1)

        # ---------- replay jumps ----------
        with st.container(border=True):
            j1,j2,j3,j4,j5,j6,j7=st.columns(7)
            if j1.button("⏮ 1m"): st.session_state.idx=move_index(times,idx,-max(1,1)); st.rerun()
            if j2.button("1m ⏭"): st.session_state.idx=move_index(times,idx,1); st.rerun()
            if j3.button("⏪ 5m"): st.session_state.idx=move_index(times,idx,-1); st.rerun()
            if j4.button("5m ⏩"): st.session_state.idx=move_index(times,idx,1); st.rerun()
            if j5.button("⏪ 30m"): st.session_state.idx=move_index(times,idx,-6); st.rerun()
            if j6.button("30m ⏩"): st.session_state.idx=move_index(times,idx,6); st.rerun()
            if j7.button("3h ⏩"): st.session_state.idx=move_index(times,idx,36); st.rerun()

        with st.container(border=True):
            q1,q2,q3,q4=st.columns(4)
            if q1.button("⬅ 1 hour",use_container_width=True): st.session_state.idx=move_index(times,idx,-12); st.rerun()
            if q2.button("➡ 1 hour",use_container_width=True): st.session_state.idx=move_index(times,idx,12); st.rerun()
            if q3.button("⬅ 1 day",use_container_width=True): st.session_state.idx=move_index(times,idx,-78); st.rerun()
            if q4.button("➡ 1 day",use_container_width=True): st.session_state.idx=move_index(times,idx,78); st.rerun()

        idx=min(st.session_state.idx,len(times)-1); snap=chain[chain.datetime==times[idx]].copy()
        spot=float(snap["spot"].iloc[0]) if "spot" in snap.columns and not snap.empty else float(atm)
        expiry_dt=pd.Timestamp(datetime.combine(expiry_date,expiry_time),tz="UTC")
        rt=pd.Timestamp(times[idx]); rt=rt.tz_localize("UTC") if rt.tzinfo is None else rt.tz_convert("UTC")
        T=max((expiry_dt-rt).total_seconds()/(365*24*3600),1e-8)

        # ---------- chain with direct BUY/SELL buttons ----------
        out=[]
        for _,r in snap.iterrows():
            iv=implied_vol(float(r.close),spot,float(r.strike),T,rate,div,r.right); g=greeks(spot,float(r.strike),T,rate,div,iv,r.right)
            out.append({"Strike":float(r.strike),"Right":r.right.upper(),"LTP":float(r.close),"Volume":r.get("volume",np.nan),"OI":r.get("open_interest",np.nan),"IV %":iv*100 if np.isfinite(iv) else np.nan,**{k.title():v for k,v in g.items()}})
        view=pd.DataFrame(out).sort_values(["Strike","Right"])

        st.subheader("Option Chain • direct paper execution")
        header=st.columns([.8,1,1,1,1,1,1,1,1,1,1])
        for col,label in zip(header,["Strike","CE LTP","CE IV","CE Δ","CE Γ","CE Θ","PE LTP","PE IV","PE Δ","PE Γ","PE Θ"]): col.markdown(f"**{label}**")
        for strike in sorted(view.Strike.unique()):
            ce=view[(view.Strike==strike)&(view.Right=="CALL")]; pe=view[(view.Strike==strike)&(view.Right=="PUT")]
            ce=ce.iloc[0] if not ce.empty else None; pe=pe.iloc[0] if not pe.empty else None
            cols=st.columns([.8,1,1,1,1,1,1,1,1,1,1])
            cols[0].markdown(f"**{strike:,.0f}**")
            if ce is not None:
                cols[1].markdown(f"₹{ce.LTP:.2f}")
                cols[2].markdown(f"{ce['IV %']:.1f}%")
                cols[3].markdown(f"{ce.Delta:.3f}"); cols[4].markdown(f"{ce.Gamma:.4f}"); cols[5].markdown(f"{ce.Theta:.3f}")
            if pe is not None:
                cols[6].markdown(f"₹{pe.LTP:.2f}"); cols[7].markdown(f"{pe['IV %']:.1f}%"); cols[8].markdown(f"{pe.Delta:.3f}"); cols[9].markdown(f"{pe.Gamma:.4f}"); cols[10].markdown(f"{pe.Theta:.3f}")
            bcols=st.columns([.8,1,1,1,1,1,1,1,1,1,1])
            bcols[0].caption("paper")
            if ce is not None:
                if bcols[1].button("BUY CE",key=f"bce{strike}"): st.session_state.order_request=("BUY","CALL",float(strike),float(ce.LTP)); st.rerun()
                if bcols[2].button("SELL CE",key=f"sce{strike}"): st.session_state.order_request=("SELL","CALL",float(strike),float(ce.LTP)); st.rerun()
            if pe is not None:
                if bcols[6].button("BUY PE",key=f"bpe{strike}"): st.session_state.order_request=("BUY","PUT",float(strike),float(pe.LTP)); st.rerun()
                if bcols[7].button("SELL PE",key=f"spe{strike}"): st.session_state.order_request=("SELL","PUT",float(strike),float(pe.LTP)); st.rerun()

        # ---------- execution ticket ----------
        if st.session_state.get("order_request"):
            side,right,strike,price=st.session_state.order_request
            with st.container(border=True):
                st.markdown(f"### 🛒 Confirm {side} {right} {strike:,.0f}")
                qty=st.number_input("Quantity",1,100000,50,step=50,key="confirm_qty")
                if st.button(f"Confirm {side} {right} @ ₹{price:.2f}",type="primary"):
                    signed=qty if side=="BUY" else -qty; key=(right,float(strike)); old=st.session_state.positions.get(key)
                    if old and np.sign(old["qty"]) != np.sign(signed) and abs(signed) >= abs(old["qty"]):
                        close_qty=abs(old["qty"]); realized=(price-old["avg"])*close_qty*(1 if old["qty"]>0 else -1)
                        remaining=old["qty"]+signed; st.session_state.cash-=signed*price
                        st.session_state.trade_history.append({"time":pd.Timestamp(times[idx]),"action":"CLOSE","right":right,"strike":strike,"qty":close_qty,"price":price,"realized":realized})
                        if remaining==0: del st.session_state.positions[key]
                        else: st.session_state.positions[key]={"qty":remaining,"avg":price}
                    else:
                        st.session_state.cash-=signed*price
                        if old and np.sign(old["qty"])==np.sign(signed):
                            new_qty=old["qty"]+signed; old["avg"]=(abs(old["qty"])*old["avg"]+abs(signed)*price)/abs(new_qty); old["qty"]=new_qty
                        else: st.session_state.positions[key]={"qty":signed,"avg":price}
                        st.session_state.trade_history.append({"time":pd.Timestamp(times[idx]),"action":side,"right":right,"strike":strike,"qty":qty,"price":price,"realized":0.0})
                    st.session_state.order_request=None; st.rerun()

        # ---------- MTM snapshot ----------
        portfolio,pos_value=mark_portfolio(view)
        mtm=pos_value + st.session_state.cash - 1_000_000.0
        st.session_state.mtm_history.append({"time":pd.Timestamp(times[idx]),"mtm":mtm})
        mh=pd.DataFrame(st.session_state.mtm_history).drop_duplicates("time").sort_values("time")

        m1,m2,m3,m4=st.columns(4)
        m1.metric("Replay time",pd.Timestamp(times[idx]).strftime("%d %b %Y %H:%M")); m2.metric("MTM P&L",f"₹{mtm:,.2f}"); m3.metric("Cash",f"₹{st.session_state.cash:,.2f}"); m4.metric("Open positions",str(len(st.session_state.positions)))

        if not mh.empty:
            mtm_fig=go.Figure(go.Scatter(x=mh.time,y=mh.mtm,mode="lines+markers",name="MTM P&L",fill="tozeroy"))
            mtm_fig.add_hline(y=0,line_dash="dot"); mtm_fig.update_layout(height=330,title="MTM fluctuation over replay time",xaxis_title="Replay time",yaxis_title="P&L (₹)")
            st.plotly_chart(mtm_fig,use_container_width=True)

        # ---------- position management ----------
        st.subheader("💼 Open Positions — close anytime")
        if portfolio.empty:
            st.info("No open positions.")
        else:
            for i,row in portfolio.iterrows():
                c1,c2,c3,c4,c5,c6=st.columns([1,1,1,1,1.2,1])
                c1.write(f"**{row.Right} {row.Strike:,.0f}**"); c2.write(f"Qty {int(row.Qty)}"); c3.write(f"Avg ₹{row.Avg:.2f}"); c4.write(f"LTP ₹{row.LTP:.2f}"); c5.metric("MTM",f"₹{row['Unrealized P&L']:,.2f}")
                if c6.button("Close",key=f"close_{row.Right}_{row.Strike}"):
                    qty=int(abs(row.Qty)); side="SELL" if row.Qty>0 else "BUY"; price=float(row.LTP); signed=qty if side=="BUY" else -qty
                    realized=(price-row.Avg)*qty*(1 if row.Qty>0 else -1)
                    st.session_state.cash-=signed*price
                    st.session_state.trade_history.append({"time":pd.Timestamp(times[idx]),"action":"CLOSE","right":row.Right,"strike":row.Strike,"qty":qty,"price":price,"realized":realized})
                    del st.session_state.positions[(row.Right,float(row.Strike))]
                    st.rerun()

        # ---------- history ----------
        st.subheader("📜 Execution history")
        if st.session_state.trade_history: st.dataframe(pd.DataFrame(st.session_state.trade_history).sort_values("time",ascending=False),use_container_width=True,hide_index=True)

        # ---------- charts ----------
        near=min(view.Strike.unique(),key=lambda x:abs(x-spot)); plot=chain[chain.strike==near]
        fig=go.Figure()
        for rn in ["call","put"]:
            p=plot[plot.right==rn].sort_values("datetime"); fig.add_trace(go.Scatter(x=p.datetime,y=p.close,mode="lines",name=rn.upper()))
        fig.add_vline(x=pd.Timestamp(times[idx]),line_dash="dash"); fig.update_layout(height=340,title=f"ATM option replay • {near}")
        st.plotly_chart(fig,use_container_width=True)

        # ---------- strategy builder ----------
        st.subheader("🧩 Strategy Builder")
        with st.container(border=True):
            s1,s2,s3,s4,s5,s6=st.columns([1.2,1.2,1.4,1.2,1.2,1.2])
            leg_side=s1.selectbox("Side",["BUY","SELL"],key="leg_side"); leg_right=s2.selectbox("Type",["CALL","PUT"],key="leg_right"); leg_strike=s3.selectbox("Strike",sorted(view.Strike.unique()),key="leg_strike"); leg_qty=s4.number_input("Qty",1,100000,50,step=50,key="leg_qty")
            leg_row=view[(view.Strike==leg_strike)&(view.Right==leg_right)]; leg_premium=float(leg_row.iloc[0].LTP) if not leg_row.empty else 0
            s5.metric("Premium",f"₹{leg_premium:.2f}")
            if s6.button("+ Add leg"): st.session_state.strategy_legs.append({"side":leg_side,"right":leg_right,"strike":float(leg_strike),"qty":int(leg_qty),"premium":leg_premium})
        if st.session_state.strategy_legs:
            st.dataframe(pd.DataFrame(st.session_state.strategy_legs),use_container_width=True,hide_index=True)
            if st.button("🗑 Clear strategy"): st.session_state.strategy_legs=[]; st.rerun()
            spots=np.linspace(max(1,spot-step*20),spot+step*20,161); pnl=payoff(st.session_state.strategy_legs,spots)
            fig2=go.Figure(go.Scatter(x=spots,y=pnl,mode="lines",fill="tozeroy",name="P&L")); fig2.add_vline(x=spot,line_dash="dash"); fig2.add_hline(y=0,line_dash="dot"); fig2.update_layout(height=380,title="Expiry payoff / P&L",xaxis_title="Underlying at expiry",yaxis_title="P&L (₹)"); st.plotly_chart(fig2,use_container_width=True)

with tab_extractor:
    st.header("📅 Year-wise Historical Data Extractor")
    st.markdown("Fetch high-resolution historical data (e.g., **1-minute bars**) from the ICICI Breeze API, automatically handle request limits via date chunking, and group results year-by-year.")

    session = st.session_state.get("session_token")
    if not session:
        st.warning("⚠️ ICICI Direct Breeze is not connected. Please use the **🔐 Connection** section in the sidebar to authenticate.")
    else:
        st.success("✅ Connected to Breeze API. Ready to fetch data.")

    with st.form("extractor_form"):
        col1, col2, col3 = st.columns(3)
        ext_symbol = col1.text_input("Stock Code / Underlying", value="NIFTY")
        ext_exchange = col2.selectbox("Exchange Code", ["NFO", "NSE", "BFO", "BSE"])
        ext_product = col3.selectbox("Product Type", ["options", "futures", "cash"])

        col4, col5, col6 = st.columns(3)
        ext_interval = col4.selectbox("Bar Interval", ["1minute", "5minute", "30minute", "1day"], index=0)
        ext_start_date = col5.date_input("Start Date", date.today() - timedelta(days=30))
        ext_end_date = col6.date_input("End Date", date.today())

        st.markdown("##### Options / Futures Details (Only required for FNO/Options/Futures)")
        col7, col8, col9 = st.columns(3)
        ext_expiry = col7.date_input("Expiry Date (for options/futures)", value=date.today())
        ext_expiry_time = col7.time_input("Expiry Time", value=time(15, 30))
        ext_right = col8.selectbox("Option Right", ["None", "Call", "Put"])
        ext_strike = col9.number_input("Strike Price", value=0.0, step=50.0)

        ext_chunk_days = st.slider("Chunk Size (Days per request)", min_value=1, max_value=30, value=14, help="Splits large requests into smaller windows to avoid API query limit constraints.")

        submit_btn = st.form_submit_button("📥 Fetch and Generate Year-wise CSVs", type="primary", use_container_width=True)

    if submit_btn:
        if not session:
            st.error("Please connect your Breeze API first via the sidebar!")
        else:
            with st.spinner("Fetching data from Breeze API... This can take some time due to chunking/rate-limiting."):
                from fetch_historical import download_historical_data

                right_val = None if ext_right == "None" else ext_right.lower()
                strike_val = None if ext_product != "options" or ext_strike == 0.0 else ext_strike

                if ext_product in ("options", "futures"):
                    expiry_iso = f"{ext_expiry}T{ext_expiry_time.strftime('%H:%M:%S')}.000Z"
                else:
                    expiry_iso = None

                year_wise_dfs = download_historical_data(
                    api_key=client.api_key,
                    session_token=session,
                    stock_code=ext_symbol,
                    exchange_code=ext_exchange,
                    product_type=ext_product,
                    start_date=ext_start_date.strftime('%Y-%m-%d'),
                    end_date=ext_end_date.strftime('%Y-%m-%d'),
                    interval=ext_interval,
                    expiry_date=expiry_iso,
                    right=right_val,
                    strike_price=strike_val,
                    chunk_days=ext_chunk_days
                )

                if not year_wise_dfs:
                    st.error("No historical data was found or fetched. Please verify your parameters or connection.")
                else:
                    st.success(f"Successfully retrieved data! Grouped into {len(year_wise_dfs)} year(s).")

                    out_dir = "downloaded_data"
                    os.makedirs(out_dir, exist_ok=True)

                    for year, df_year in year_wise_dfs.items():
                        st.subheader(f"📅 Year {year} Data")
                        st.dataframe(df_year.head(100), use_container_width=True)

                        csv_data = df_year.to_csv(index=False).encode('utf-8')
                        filename = f"{ext_symbol}_{ext_product}_{year}.csv"

                        local_path = os.path.join(out_dir, filename)
                        df_year.to_csv(local_path, index=False)

                        col_dl, col_path = st.columns([1, 2])
                        col_dl.download_button(
                            label=f"⬇️ Download {filename}",
                            data=csv_data,
                            file_name=filename,
                            mime='text/csv',
                            key=f"dl_{year}_{filename}",
                            use_container_width=True
                        )
                        col_path.info(f"Saved locally to `{local_path}`")
