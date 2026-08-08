import os
import numpy as np, pandas as pd, plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from breeze_client import BreezeClient
from greeks import implied_vol, greeks

load_dotenv()
st.set_page_config(page_title='Breeze Option Replay',layout='wide')

def norm(rows):
    df=pd.DataFrame(rows)
    if df.empty or 'datetime' not in df or 'close' not in df: return pd.DataFrame()
    df['datetime']=pd.to_datetime(df['datetime'],errors='coerce')
    for c in ['open','high','low','close','volume','open_interest']:
        if c in df: df[c]=pd.to_numeric(df[c],errors='coerce')
    return df.dropna(subset=['datetime','close'])

@st.cache_data(ttl=300,show_spinner=False)
def get_hist(api_key,session,symbol,start,end,expiry,right,strike,interval):
    c=BreezeClient(api_key=api_key,secret_key=os.getenv('BREEZE_SECRET_KEY'),session_token=session)
    return norm(c.historical_option(symbol,start,end,expiry,right,strike,interval))

st.title('📈 ICICI Direct Breeze — Option Replay Simulator')
st.caption('Historical paper trading only — no live orders.')

client=BreezeClient()
with st.sidebar:
    st.header('Breeze login')
    if client.configured:
        st.link_button('Login with ICICI Direct',client.login_url())
        st.caption('After login, Breeze redirects here with API_Session.')
    else:
        st.warning('Set BREEZE_API_KEY and BREEZE_SECRET_KEY in .env or deployment secrets.')

api_session=st.query_params.get('API_Session') or st.query_params.get('api_session')
if api_session and client.configured:
    try:
        st.session_state['session_token']=client.exchange_api_session(api_session)
        st.query_params.clear(); st.success('Breeze session connected.')
    except Exception as e: st.error(str(e))

session=st.session_state.get('session_token')
if not session:
    st.info('Connect Breeze first.'); st.stop()

with st.sidebar:
    st.header('Replay')
    symbol=st.text_input('Underlying','NIFTY').upper()
    expiry=st.text_input('Expiry ISO','2026-08-13T06:00:00.000Z')
    start=st.text_input('Start ISO','2026-08-07T09:15:00.000Z')
    end=st.text_input('End ISO','2026-08-07T15:30:00.000Z')
    interval=st.selectbox('Interval',['1minute','5minute','30minute','1day'])
    atm=st.number_input('ATM strike',25000.0,step=50.0)
    step=st.number_input('Strike step',50.0,min_value=0.05,step=50.0)
    count=st.slider('Strike count',4,20,20)
    rate=st.number_input('Risk-free %',6.5,step=0.25)/100
    div=st.number_input('Dividend yield %',0.0,step=0.25)/100

strikes=sorted(round(atm+(i-count//2)*step,2) for i in range(count))
if st.button('Load historical chain',type='primary'):
    frames=[]
    with st.spinner('Downloading/caching option history...'):
        for k in strikes:
            for right in ['call','put']:
                try:
                    d=get_hist(client.api_key,session,symbol,start,end,expiry,right,k,interval)
                    if not d.empty:
                        d['strike']=k; d['right']=right; frames.append(d)
                except Exception as e:
                    st.warning(f'{right.upper()} {k}: {e}')
    st.session_state.chain=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()
    st.session_state.idx=0

chain=st.session_state.get('chain',pd.DataFrame())
if chain.empty:
    st.warning('Load data to begin. Check Breeze expiry/date/symbol values.'); st.stop()

times=sorted(chain.datetime.dropna().unique()); idx=min(st.session_state.get('idx',0),len(times)-1)
c1,c2,c3=st.columns(3)
if c1.button('⏮ Previous'): st.session_state.idx=max(0,idx-1); st.rerun()
if c2.button('Next ⏭'): st.session_state.idx=min(len(times)-1,idx+1); st.rerun()
c3.metric('Replay time',pd.Timestamp(times[idx]).strftime('%Y-%m-%d %H:%M'))

spot=st.number_input('Underlying spot',float(atm),step=float(step))
expiry_dt=pd.to_datetime(expiry,utc=True,errors='coerce'); rt=pd.Timestamp(times[idx])
rt=rt.tz_localize('UTC') if rt.tzinfo is None else rt.tz_convert('UTC')
T=max((expiry_dt-rt).total_seconds()/(365*24*3600),1e-8) if pd.notna(expiry_dt) else 1e-8

out=[]
snap=chain[chain.datetime==times[idx]]
for _,r in snap.iterrows():
    iv=implied_vol(float(r.close),spot,float(r.strike),T,rate,div,r.right)
    g=greeks(spot,float(r.strike),T,rate,div,iv,r.right)
    out.append({'Strike':r.strike,'Right':r.right.upper(),'LTP':r.close,'Volume':r.get('volume',np.nan),'OI':r.get('open_interest',np.nan),'IV %':iv*100 if np.isfinite(iv) else np.nan,**{k.title():v for k,v in g.items()}})
view=pd.DataFrame(out).sort_values(['Strike','Right'])
st.dataframe(view,use_container_width=True,hide_index=True)

st.subheader('ATM replay chart')
near=min(strikes,key=lambda x:abs(x-spot)); plot=chain[chain.strike==near]
fig=go.Figure()
for right in ['call','put']:
    p=plot[plot.right==right].sort_values('datetime')
    fig.add_trace(go.Scatter(x=p.datetime,y=p.close,mode='lines',name=right.upper()))
fig.add_vline(x=pd.Timestamp(times[idx]),line_dash='dash')
st.plotly_chart(fig,use_container_width=True)

st.header('Paper trading')
a,b,c,d=st.columns(4)
side=a.selectbox('Side',['BUY','SELL']); right=b.selectbox('Right',['CALL','PUT']); strike=c.selectbox('Strike',strikes); qty=d.number_input('Qty',1,100000,50,step=50)
row=view[(view.Strike==strike)&(view.Right==right)]; price=float(row.iloc[0].LTP) if not row.empty else 0
if st.button('Execute paper order'):
    pos=st.session_state.setdefault('positions',{}); key=(right,float(strike)); signed=qty if side=='BUY' else -qty
    pos[key]=pos.get(key,0)+signed; st.session_state.cash=st.session_state.get('cash',1_000_000.0)-signed*price
    st.success(f'{side} {qty} {right} {strike} @ ₹{price:,.2f}')

st.metric('Paper cash',f"₹{st.session_state.get('cash',1_000_000.0):,.2f}")
rows=[]
for (r,k),q in st.session_state.get('positions',{}).items():
    x=view[(view.Strike==k)&(view.Right==r)]; l=float(x.iloc[0].LTP) if not x.empty else np.nan
    rows.append({'Right':r,'Strike':k,'Qty':q,'LTP':l,'Market Value':q*l if np.isfinite(l) else np.nan})
if rows: st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
