import json, os
from urllib.parse import quote
from datetime import date, time, datetime
import requests

def format_breeze_date(date_obj, target_type):
    """
    Format standard Date/Datetime objects to match Breeze API specifications exactly:
    - ISO_HISTORICAL: "YYYY-MM-DDTHH:mm:ss.000Z" (Millisecond precision ISO 8601 UTC)
    - ISO_EXPIRY: "YYYY-MM-DDT06:00:00.000Z" (Option contracts expiry)
    - FEED_EXCHANGE: "DD-MMM-YYYY" (Short exchange string)
    - DISPLAY_FORMAT: "DD-b-YYYY"
    """
    if isinstance(date_obj, str):
        clean_str = date_obj.replace("Z", "")
        if "T" in clean_str:
            try:
                date_obj = datetime.strptime(clean_str.split(".")[0], "%Y-%m-%dT%H:%M:%S")
            except Exception:
                try:
                    date_obj = datetime.strptime(clean_str.split("T")[0], "%Y-%m-%d").date()
                except Exception:
                    pass
        else:
            try:
                date_obj = datetime.strptime(clean_str, "%Y-%m-%d").date()
            except Exception:
                pass

    if target_type == "ISO_HISTORICAL" or target_type == "ISO_EXPIRY":
        if isinstance(date_obj, datetime):
            if target_type == "ISO_EXPIRY":
                return date_obj.replace(hour=6, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            return date_obj.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        elif isinstance(date_obj, date):
            t = time(6, 0) if target_type == "ISO_EXPIRY" else time(9, 15)
            return datetime.combine(date_obj, t).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        else:
            return str(date_obj)

    elif target_type == "FEED_EXCHANGE":
        return date_obj.strftime("%d-%b-%Y")

    elif target_type == "DISPLAY_FORMAT":
        return date_obj.strftime("%d-%b-%Y")

    return str(date_obj)

class BreezeClient:
    CUSTOMER_URL='https://api.icicidirect.com/breezeapi/api/v1/customerdetails'
    HISTORICAL_URL='https://breezeapi.icicidirect.com/api/v2/historicalcharts'

    def __init__(self,api_key=None,secret_key=None,session_token=None):
        self.api_key=api_key or os.getenv('BREEZE_API_KEY')
        self.secret_key=secret_key or os.getenv('BREEZE_SECRET_KEY')
        self.session_token=session_token

    @property
    def configured(self): return bool(self.api_key and self.secret_key)

    def _check_response(self, r):
        if r.status_code != 200:
            r.raise_for_status()
        try:
            resp_json = r.json()
            if isinstance(resp_json, dict):
                status_val = resp_json.get("Status")
                error_msg = str(resp_json.get("Error", ""))
                if status_val == 401 or "Unauthorized" in error_msg:
                    fake_resp = requests.Response()
                    fake_resp.status_code = 401
                    fake_resp._content = r.content
                    raise requests.exceptions.HTTPError("401 Client Error: Unauthorized User from Breeze API", response=fake_resp)
        except Exception as e:
            if isinstance(e, requests.exceptions.HTTPError):
                raise e

    def generate_session(self, api_secret=None, session_token=None):
        """
        Generates/updates Breeze API session keys.
        """
        if api_secret:
            self.secret_key = api_secret
        if session_token:
            # Handle standard raw or redirect formats seamlessly
            clean_token = session_token
            if "api_session=" in session_token:
                clean_token = session_token.split("api_session=")[1].split("&")[0]
            try:
                # If it's a redirect token, try exchanging it
                self.session_token = self.exchange_api_session(clean_token)
            except Exception:
                # Direct assignment fallback if exchange fails or token is already exchanged
                self.session_token = clean_token
        return self.session_token

    def login_url(self):
        return f'https://api.icicidirect.com/apiuser/login?api_key={quote(self.api_key)}'

    def exchange_api_session(self,api_session):
        payload=json.dumps({'SessionToken':api_session,'AppKey':self.api_key})
        r=requests.get(self.CUSTOMER_URL,headers={'Content-Type':'application/json'},data=payload,timeout=30)
        r.raise_for_status()
        token=(r.json().get('Success') or {}).get('session_token')
        if not token: raise RuntimeError('No session_token returned by Breeze')
        self.session_token=token
        return token

    def historical_option(self,stock_code,from_date,to_date,expiry_date,right,strike_price,interval='1minute'):
        """
        Historical option REST endpoint.
        - from_date & to_date must be "YYYY-MM-DDTHH:mm:ss.000Z"
        - expiry_date must be "YYYY-MM-DDTHH:mm:ss.000Z"
        - strike_price is integer string (e.g. "24500")
        - right is strictly lowercase ("call" or "put")
        """
        if not self.api_key or not self.session_token: raise RuntimeError('Complete Breeze login first')

        # Format string sanitation according to the guidelines
        f_from = format_breeze_date(from_date, "ISO_HISTORICAL")
        f_to = format_breeze_date(to_date, "ISO_HISTORICAL")
        f_exp = format_breeze_date(expiry_date, "ISO_EXPIRY")
        f_strike = str(int(float(strike_price)))
        f_right = right.lower()

        params = {
            'interval': interval,
            'from_date': f_from,
            'to_date': f_to,
            'stock_code': stock_code,
            'exchange_code': 'NFO',
            'product_type': 'options',
            'expiry_date': f_exp,
            'right': f_right,
            'strike_price': f_strike
        }

        r = requests.get(self.HISTORICAL_URL, params=params,
                         headers={'X-SessionToken': self.session_token, 'X-apikey': self.api_key}, timeout=30)

        self._check_response(r)
        return r.json().get('Success', [])

    def historical_index(self,stock_code,from_date,to_date,interval='1minute'):
        if not self.api_key or not self.session_token: raise RuntimeError('Complete Breeze login first')

        f_from = format_breeze_date(from_date, "ISO_HISTORICAL")
        f_to = format_breeze_date(to_date, "ISO_HISTORICAL")

        params = {
            'interval': interval,
            'from_date': f_from,
            'to_date': f_to,
            'stock_code': stock_code,
            'exchange_code': 'NSE',
            'product_type': 'cash'
        }
        r = requests.get(self.HISTORICAL_URL, params=params,
                         headers={'X-SessionToken': self.session_token, 'X-apikey': self.api_key}, timeout=30)
        self._check_response(r)
        return r.json().get('Success', [])

    def get_option_chain_quotes(self, stock_code, expiry_date=None, right=None, strike_price=None):
        """
        Retrieves real available contract expiries & details from Breeze Security master / active quotes.
        """
        if not self.api_key or not self.session_token: raise RuntimeError('Complete Breeze login first')

        params = {
            'stock_code': stock_code,
            'exchange_code': 'NFO',
            'product_type': 'options'
        }
        if expiry_date:
            params['expiry_date'] = format_breeze_date(expiry_date, "ISO_EXPIRY")
        if right:
            params['right'] = right.lower()
        if strike_price:
            params['strike_price'] = str(int(float(strike_price)))

        # In real Breeze, this URL is option chain quotes endpoint
        url = 'https://breezeapi.icicidirect.com/api/v2/optionchain'
        try:
            r = requests.get(url, params=params, headers={'X-SessionToken': self.session_token, 'X-apikey': self.api_key}, timeout=30)
            if r.status_code == 200:
                return r.json().get('Success', [])
        except Exception:
            pass
        return []
