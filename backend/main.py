from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import numpy as np

from backend.breeze_service import BreezeService
from backend.math_engine import black_scholes_pricing, implied_volatility, calculate_greeks

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
        # In actual integration, this uses the real breeze client token exchange
        exchanged_token = f"exchanged_{payload.session_token[:15]}"
        return {"session_token": exchanged_token}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/payoff")
def get_payoff_data(req: PayoffRequest):
    """
    Returns both Expiration Payoff and T+0 / Target Date Payoff curves (calculated via Black-Scholes).
    """
    if not req.legs:
        return {"spots": [], "expiry_pnl": [], "t0_pnl": []}

    spots = np.linspace(max(1.0, req.spot - req.step * 8), req.spot + req.step * 8, 100).tolist()
    expiry_pnl = []
    t0_pnl = []

    # Expiry T
    T_expiry = 0.0
    # Target Date T (remaining time to expiry in years)
    T_t0 = max(1e-6, req.time_to_expiry_days / 365.0)

    for s in spots:
        p_expiry = 0.0
        p_t0 = 0.0
        for leg in req.legs:
            mult = 1.0 if leg.side.upper() == "BUY" else -1.0

            # 1. Expiry Payoff
            intrinsic = max(s - leg.strike, 0.0) if leg.right.upper() == "CALL" else max(leg.strike - s, 0.0)
            p_expiry += leg.qty * (intrinsic - leg.premium) * mult

            # 2. T+0 Payoff (Black-Scholes price difference)
            bs_price = black_scholes_pricing(
                S=s, K=leg.strike, T=T_t0, r=req.r, q=req.q, sigma=req.sigma, option_type=leg.right.lower()
            )
            p_t0 += leg.qty * (bs_price - leg.premium) * mult

        expiry_pnl.append(p_expiry)
        t0_pnl.append(p_t0)

    # Calculated metrics
    net_delta = 0.0
    net_gamma = 0.0
    net_theta = 0.0
    net_vega = 0.0

    for leg in req.legs:
        greeks = calculate_greeks(
            S=req.spot, K=leg.strike, T=T_t0, r=req.r, q=req.q, sigma=req.sigma, option_type=leg.right.lower()
        )
        mult = 1.0 if leg.side.upper() == "BUY" else -1.0
        net_delta += greeks["delta"] * leg.qty * mult
        net_gamma += greeks["gamma"] * leg.qty * mult
        net_theta += greeks["theta"] * leg.qty * mult
        net_vega += greeks["vega"] * leg.qty * mult

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

@app.get("/api/positions")
def get_positions():
    return {"positions": simulation_state["positions"], "cash": simulation_state["cash"]}

@app.post("/api/positions/clear")
def clear_positions():
    simulation_state["positions"] = []
    simulation_state["cash"] = 1000000.0
    return {"status": "cleared"}
