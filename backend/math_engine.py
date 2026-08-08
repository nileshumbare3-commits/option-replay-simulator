import math
from scipy.stats import norm

def black_scholes_pricing(S: float, K: float, T: float, r: float, q: float, sigma: float, option_type: str) -> float:
    """
    S: Spot Price
    K: Strike Price
    T: Time to Expiration (in years)
    r: Risk-free rate (e.g. 0.065 for 6.5%)
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

def implied_volatility(price: float, S: float, K: float, T: float, r: float, q: float, option_type: str) -> float:
    """
    Calculates Implied Volatility using Newton-Raphson or Bisection solver.
    """
    # Intrinsic value floor
    intrinsic = max(S - K, 0.0) if option_type.lower() == 'call' else max(K - S, 0.0)
    if price <= intrinsic:
        return 0.01

    # Bisection search bounds
    low_vol = 0.0001
    high_vol = 5.0

    # Check bounds
    p_low = black_scholes_pricing(S, K, T, r, q, low_vol, option_type)
    if p_low >= price:
        return low_vol

    p_high = black_scholes_pricing(S, K, T, r, q, high_vol, option_type)
    if p_high <= price:
        return high_vol

    # Perform bisection search
    for _ in range(100):
        mid_vol = (low_vol + high_vol) / 2.0
        p_mid = black_scholes_pricing(S, K, T, r, q, mid_vol, option_type)

        if abs(p_mid - price) < 1e-5:
            return mid_vol

        if p_mid < price:
            low_vol = mid_vol
        else:
            high_vol = mid_vol

    return (low_vol + high_vol) / 2.0

def calculate_greeks(S: float, K: float, T: float, r: float, q: float, sigma: float, option_type: str) -> dict:
    """
    Calculates Delta, Gamma, Theta, Vega, and Rho.
    """
    if T <= 1e-6:
        # Expiry greeks
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
