# ICICI Direct Breeze Option Replay Simulator

A Streamlit paper-trading simulator for NSE options using ICICI Direct Breeze historical derivatives data.

## Features
- ICICI Direct/Breeze login redirect and API session-token exchange
- 20 configurable strikes around ATM, with CALL and PUT
- Historical option replay with previous/next bar controls
- Black-Scholes implied volatility and Delta, Gamma, Theta, Vega, Rho
- Paper orders, cash, positions and mark-to-market
- Server-side API secrets only

## Run locally
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
streamlit run app.py
```

Set `BREEZE_API_KEY`, `BREEZE_SECRET_KEY`, and `BREEZE_REDIRECT_URL` in `.env`.

The Breeze login redirect supplies `API_Session`; the API key/secret are generated in the Breeze developer portal and should not be exposed to the browser.

## Disclaimer
This project is a paper-trading/replay tool. It does not place live orders.
