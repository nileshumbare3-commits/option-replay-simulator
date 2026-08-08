from math import exp, log, sqrt
import numpy as np
from scipy.stats import norm

def bs_price(S,K,T,r,q,sigma,right):
    if T <= 0:
        return max(S-K,0) if right=='call' else max(K-S,0)
    d1=(log(S/K)+(r-q+0.5*sigma*sigma)*T)/(sigma*sqrt(T))
    d2=d1-sigma*sqrt(T)
    if right=='call':
        return S*exp(-q*T)*norm.cdf(d1)-K*exp(-r*T)*norm.cdf(d2)
    return K*exp(-r*T)*norm.cdf(-d2)-S*exp(-q*T)*norm.cdf(-d1)

def implied_vol(price,S,K,T,r,q,right):
    if not all(np.isfinite(x) for x in [price,S,K,T]) or min(price,S,K,T)<=0: return np.nan
    intrinsic=max(S-K,0) if right=='call' else max(K-S,0)
    if price < intrinsic*0.999: return np.nan
    lo,hi=1e-5,5.0
    for _ in range(80):
        mid=(lo+hi)/2
        if bs_price(S,K,T,r,q,mid,right)>price: hi=mid
        else: lo=mid
    return (lo+hi)/2

def greeks(S,K,T,r,q,sigma,right):
    if not np.isfinite(sigma) or T<=0:
        return dict(delta=np.nan,gamma=np.nan,theta=np.nan,vega=np.nan,rho=np.nan)
    d1=(log(S/K)+(r-q+0.5*sigma*sigma)*T)/(sigma*sqrt(T)); d2=d1-sigma*sqrt(T)
    pdf=norm.pdf(d1)
    gamma=exp(-q*T)*pdf/(S*sigma*sqrt(T)); vega=S*exp(-q*T)*pdf*sqrt(T)/100
    if right=='call':
        delta=exp(-q*T)*norm.cdf(d1)
        theta=(-S*exp(-q*T)*pdf*sigma/(2*sqrt(T))-r*K*exp(-r*T)*norm.cdf(d2)+q*S*exp(-q*T)*norm.cdf(d1))/365
        rho=K*T*exp(-r*T)*norm.cdf(d2)/100
    else:
        delta=exp(-q*T)*(norm.cdf(d1)-1)
        theta=(-S*exp(-q*T)*pdf*sigma/(2*sqrt(T))+r*K*exp(-r*T)*norm.cdf(-d2)-q*S*exp(-q*T)*norm.cdf(-d1))/365
        rho=-K*T*exp(-r*T)*norm.cdf(-d2)/100
    return dict(delta=delta,gamma=gamma,theta=theta,vega=vega,rho=rho)
