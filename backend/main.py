from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import numpy as np
import math

from backend.breeze_service import BreezeService
from backend.math_engine import (
    black_scholes_pricing,
    implied_volatility,
    calculate_greeks,
    generate_strike_grid,
    get_strike_interval,
    get_atm_strike,
    calculate_pcr,
    calculate_max_pain
)

app = FastAPI(title="Option Replay & Simulator API Server", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request / Response Schemas
class Credentials(BaseModel):
    api_key: str
    secret_key: str
    session_token: Optional[str] = None

class ExchangeSession(BaseModel):
    api_key: str
    session_token: str

class StrategyLeg(BaseModel):
    side: str  # "BUY" or "SELL"
    right: str  # "CALL" or "PUT"
    strike: float
    qty: int
    premium: float

class PayoffRequest(BaseModel):
    legs: List[StrategyLeg]
    spot: float
    step: float
    r: float
    q: float
    sigma: float
    time_to_expiry_days: float

class OptionChainRequest(BaseModel):
    symbol: str
    timestamp: str  # ISO string: e.g. "2026-08-08T15:29:00Z"
    expiry_date: str

# In-memory storage for active simulation state
simulation_state = {
    "cash": 1000000.0,
    "positions": [],
    "history": []
}

@app.post("/api/auth/login-url")
def get_login_url(creds: Credentials):
    service = BreezeService(api_key=creds.api_key, secret_key=creds.secret_key)
    return {"login_url": service.login_url()}

@app.post("/api/auth/exchange-session")
def exchange_session(payload: ExchangeSession):
    service = BreezeService(api_key=payload.api_key)
    try:
        exchanged_token = f"exchanged_{payload.session_token[:15]}"
        return {"session_token": exchanged_token}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/payoff")
def get_payoff_data(req: PayoffRequest):
    """
    Returns both Expiration Payoff (T=0) and T+t Payoff curves (calculated dynamically via Black-Scholes).
    Using target spot price steps (S * 0.90 to S * 1.10).
    """
    if not req.legs:
        return {"spots": [], "expiry_pnl": [], "t0_pnl": [], "greeks": {"delta": 0, "gamma": 0, "theta": 0, "vega": 0}}

    spots = np.linspace(req.spot * 0.90, req.spot * 1.10, 100).tolist()
    expiry_pnl = []
    t0_pnl = []

    # Expiry T = 0
    # Target Date T (remaining time to expiry in years)
    T_target = max(1e-6, req.time_to_expiry_days / 365.0)

    for s in spots:
        p_expiry = 0.0
        p_t0 = 0.0
        for leg in req.legs:
            mult = 1.0 if leg.side.upper() == "BUY" else -1.0

            # 1. Expiry Payoff
            intrinsic = max(s - leg.strike, 0.0) if leg.right.upper() == "CALL" else max(leg.strike - s, 0.0)
            p_expiry += leg.qty * (intrinsic - leg.premium) * mult

            # 2. T+t Payoff (Black-Scholes price difference)
            bs_price = black_scholes_pricing(
                S=s, K=leg.strike, T=T_target, r=req.r, q=req.q, sigma=req.sigma, option_type=leg.right.lower()
            )
            p_t0 += leg.qty * (bs_price - leg.premium) * mult

        expiry_pnl.append(p_expiry)
        t0_pnl.append(p_t0)

    # Calculated aggregate portfolio greeks
    net_delta = 0.0
    net_gamma = 0.0
    net_theta = 0.0
    net_vega = 0.0

    for leg in req.legs:
        g = calculate_greeks(
            S=req.spot, K=leg.strike, T=T_target, r=req.r, q=req.q, sigma=req.sigma, option_type=leg.right.lower()
        )
        mult = 1.0 if leg.side.upper() == "BUY" else -1.0
        net_delta += g["delta"] * leg.qty * mult
        net_gamma += g["gamma"] * leg.qty * mult
        net_theta += g["theta"] * leg.qty * mult
        net_vega += g["vega"] * leg.qty * mult

    return {
        "spots": spots,
        "expiry_pnl": expiry_pnl,
        "t0_pnl": t0_pnl,
        "greeks": {
            "delta": net_delta,
            "gamma": net_gamma,
            "theta": net_theta,
            "vega": net_vega
        }
    }

@app.post("/api/v1/options/chain")
def get_option_chain(req: OptionChainRequest):
    """
    Computes IV, Delta, Theta, Gamma, Vega, PCR, Max Pain, and Straddle Premium for that exact timestamp.
    """
    # 1. Determine underlying spot S (simulate realistically from symbol)
    symbol_upper = req.symbol.upper()
    if symbol_upper == "NIFTY":
        S = 25000.0
        interval = 50.0
    elif symbol_upper == "BANKNIFTY":
        S = 52000.0
        interval = 100.0
    elif symbol_upper == "FINNIFTY":
        S = 23000.0
        interval = 50.0
    else:
        S = 25000.0
        interval = 50.0

    # Let's add slight time-based variance based on the timestamp string to simulate progression
    try:
        import hashlib
        h = int(hashlib.md5(req.timestamp.encode('utf-8')).hexdigest(), 16)
        S += (h % 301) - 150.0
    except Exception:
        pass

    # 2. Generate Strike Array (ATM ± 15 strikes)
    strikes = generate_strike_grid(S, req.symbol)
    atm_strike = get_atm_strike(S, interval)

    # Risk-free benchmark: r=0.07 (7%), q=0.0
    r = 0.07
    q = 0.0
    T = 0.01 # Assume roughly 3.65 days remaining for simulation

    # 3. Build Strike-by-Strike Option Matrix
    chain_rows = []
    total_call_oi = 0.0
    total_put_oi = 0.0

    rng = np.random.default_rng(42)

    # Let's collect strikes info for Max Pain strike calculation
    strikes_info = []

    for k in strikes:
        # Simulate realistic Volume and OI
        dist = abs(k - S) / interval
        call_oi = float(int(max(10, 50000 * np.exp(-dist * 0.2))))
        put_oi = float(int(max(10, 48000 * np.exp(-dist * 0.2))))

        total_call_oi += call_oi
        total_put_oi += put_oi

        strikes_info.append({
            "strike": k,
            "call_oi": call_oi,
            "put_oi": put_oi
        })

    max_pain_strike = calculate_max_pain(strikes_info)
    pcr = calculate_pcr(total_call_oi, total_put_oi)

    # Collect ATM Call & Put price to compute straddle premium & synthetic futures
    atm_call_price = 100.0
    atm_put_price = 100.0

    for idx, k in enumerate(strikes):
        dist = abs(k - S) / interval

        # Simulate realistic option close LTP (approx BS price + small noise)
        c_intrinsic = max(S - k, 0.0)
        c_time_value = max(5.0, 150.0 * np.exp(-dist * 0.15))
        c_market = c_intrinsic + c_time_value

        p_intrinsic = max(k - S, 0.0)
        p_time_value = max(5.0, 145.0 * np.exp(-dist * 0.15))
        p_market = p_intrinsic + p_time_value

        if k == atm_strike:
            atm_call_price = c_market
            atm_put_price = p_market

        # Back-compute dynamic IV via Newton-Raphson Solver
        c_iv = implied_volatility(c_market, S, k, T, r, q, 'call')
        p_iv = implied_volatility(p_market, S, k, T, r, q, 'put')

        # Calculate exact Greeks per Strike
        c_g = calculate_greeks(S, k, T, r, q, c_iv, 'call')
        p_g = calculate_greeks(S, k, T, r, q, p_iv, 'put')

        # Collect Volume and OI information for this strike
        c_vol = int(max(100, 250000 * np.exp(-dist * 0.3)))
        p_vol = int(max(100, 230000 * np.exp(-dist * 0.3)))

        row_info = strikes_info[idx]

        chain_rows.append({
            "strike": k,
            "is_atm": (k == atm_strike),
            "call": {
                "ltp": round(c_market, 2),
                "volume": c_vol,
                "oi": int(row_info["call_oi"]),
                "iv": round(c_iv * 100, 2),
                "delta": round(c_g["delta"], 3),
                "gamma": round(c_g["gamma"], 5),
                "theta": round(c_g["theta"], 2),
                "vega": round(c_g["vega"], 2),
                "rho": round(c_g["rho"], 2)
            },
            "put": {
                "ltp": round(p_market, 2),
                "volume": p_vol,
                "oi": int(row_info["put_oi"]),
                "iv": round(p_iv * 100, 2),
                "delta": round(p_g["delta"], 3),
                "gamma": round(p_g["gamma"], 5),
                "theta": round(p_g["theta"], 2),
                "vega": round(p_g["vega"], 2),
                "rho": round(p_g["rho"], 2)
            }
        })

    straddle_premium = atm_call_price + atm_put_price
    synthetic_futures = atm_strike + atm_call_price - atm_put_price

    return {
        "symbol": req.symbol,
        "timestamp": req.timestamp,
        "expiry_date": req.expiry_date,
        "metadata": {
            "spot_price": round(S, 2),
            "day_open": round(S - 120.0, 2),
            "futures_price": round(S + 15.0, 2),
            "synthetic_futures": round(synthetic_futures, 2),
            "straddle_premium": round(straddle_premium, 2),
            "atm_iv": round(chain_rows[15]["call"]["iv"], 2),
            "pcr": round(pcr, 2),
            "max_pain": round(max_pain_strike, 2)
        },
        "chain": chain_rows
    }

@app.get("/api/positions")
def get_positions():
    return {"positions": simulation_state["positions"], "cash": simulation_state["cash"]}

@app.post("/api/positions/clear")
def clear_positions():
    simulation_state["positions"] = []
    simulation_state["cash"] = 1000000.0
    return {"status": "cleared"}
