import math
from scipy.stats import norm

def black_scholes_pricing(S: float, K: float, T: float, r: float, q: float, sigma: float, option_type: str) -> float:
    """
    S: Spot Price
    K: Strike Price
    T: Time to Expiration (in years)
    r: Risk-free rate (e.g. 0.07 for 7%)
    q: Dividend yield
    sigma: Implied Volatility
    option_type: 'call' or 'put'
    """
    if T <= 1e-6:
        if option_type.lower() == 'call':
            return max(S - K, 0.0)
        else:
            return max(K - S, 0.0)

    if sigma <= 1e-4:
        sigma = 1e-4

    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    if option_type.lower() == 'call':
        price = S * math.exp(-q * T) * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    else:
        price = K * math.exp(-r * T) * norm.cdf(-d2) - S * math.exp(-q * T) * norm.cdf(-d1)

    return max(0.0, price)

def vega_greeks(S: float, K: float, T: float, r: float, q: float, sigma: float) -> float:
    if T <= 1e-6:
        return 0.0
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    pdf_d1 = norm.pdf(d1)
    vega = S * sqrt_T * pdf_d1 * math.exp(-q * T)
    return vega

def implied_volatility(price: float, S: float, K: float, T: float, r: float, q: float, option_type: str) -> float:
    """
    Calculates Implied Volatility using Newton-Raphson solver.
    Convergence criteria: |BS_Price(IV) - Market_Price| < 0.001 within 20 iterations.
    Falls back to historic volatility (20%, i.e., 0.20) if solver fails to converge.
    """
    intrinsic = max(S - K, 0.0) if option_type.lower() == 'call' else max(K - S, 0.0)
    if price <= intrinsic:
        return 0.20

    # Starting guess: 20%
    sigma = 0.20
    for _ in range(20):
        p_val = black_scholes_pricing(S, K, T, r, q, sigma, option_type)
        diff = p_val - price
        if abs(diff) < 0.001:
            return sigma
        veg = vega_greeks(S, K, T, r, q, sigma)
        if abs(veg) < 1e-4:
            return 0.20
        sigma = sigma - diff / veg
        if sigma <= 0.001 or sigma > 5.0:
            return 0.20
    return sigma

def calculate_greeks(S: float, K: float, T: float, r: float, q: float, sigma: float, option_type: str) -> dict:
    """
    Calculates Delta, Gamma, Theta, Vega, and Rho.
    """
    if T <= 1e-6:
        if option_type.lower() == 'call':
            delta = 1.0 if S > K else 0.0
        else:
            delta = -1.0 if S < K else 0.0
        return {
            "delta": delta,
            "gamma": 0.0,
            "theta": 0.0,
            "vega": 0.0,
            "rho": 0.0
        }

    if sigma <= 1e-4:
        sigma = 1e-4

    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T

    pdf_d1 = norm.pdf(d1)
    cdf_d1 = norm.cdf(d1)
    cdf_d2 = norm.cdf(d2)

    if option_type.lower() == 'call':
        delta = math.exp(-q * T) * cdf_d1
        theta = (- (S * sigma * math.exp(-q * T) * pdf_d1) / (2 * sqrt_T)
                 + q * S * math.exp(-q * T) * cdf_d1
                 - r * K * math.exp(-r * T) * cdf_d2)
        rho = K * T * math.exp(-r * T) * cdf_d2
    else:
        delta = -math.exp(-q * T) * norm.cdf(-d1)
        theta = (- (S * sigma * math.exp(-q * T) * pdf_d1) / (2 * sqrt_T)
                 - q * S * math.exp(-q * T) * norm.cdf(-d1)
                 + r * K * math.exp(-r * T) * norm.cdf(-d2))
        rho = -K * T * math.exp(-r * T) * norm.cdf(-d2)

    gamma = (pdf_d1 * math.exp(-q * T)) / (S * sigma * sqrt_T)
    vega = S * sqrt_T * pdf_d1 * math.exp(-q * T)

    return {
        "delta": delta,
        "gamma": gamma,
        "theta": theta / 365.0,  # daily theta decay
        "vega": vega / 100.0,     # change per 1% vol change
        "rho": rho / 100.0        # change per 1% rate change
    }

def get_strike_interval(symbol: str) -> float:
    sym = symbol.upper()
    if sym == "NIFTY":
        return 50.0
    elif sym == "BANKNIFTY":
        return 100.0
    elif sym == "FINNIFTY":
        return 50.0
    else:
        return 50.0

def get_atm_strike(S: float, I: float) -> float:
    return round(S / I) * I

def generate_strike_grid(S: float, symbol: str) -> list:
    I = get_strike_interval(symbol)
    atm = get_atm_strike(S, I)
    return [round(atm + (i - 15) * I, 2) for i in range(31)]

def calculate_pcr(call_oi: float, put_oi: float) -> float:
    if call_oi <= 0:
        return 0.0
    return put_oi / call_oi

def calculate_max_pain(strikes_info: list) -> float:
    """
    strikes_info is a list of dicts: [{"strike": float, "call_oi": float, "put_oi": float}]
    """
    if not strikes_info:
        return 0.0
    best_strike = strikes_info[0]["strike"]
    min_pain = float("inf")

    strikes = [item["strike"] for item in strikes_info]

    for s_target in strikes:
        current_pain = 0.0
        for item in strikes_info:
            strike = item["strike"]
            c_oi = item.get("call_oi", 0.0)
            p_oi = item.get("put_oi", 0.0)

            # Loss for call option buyers
            current_pain += max(s_target - strike, 0.0) * c_oi
            # Loss for put option buyers
            current_pain += max(strike - s_target, 0.0) * p_oi

        if current_pain < min_pain:
            min_pain = current_pain
            best_strike = s_target

    return best_strike
