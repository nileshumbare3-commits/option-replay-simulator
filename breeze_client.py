import json, os
from urllib.parse import quote
import requests

class BreezeClient:
    CUSTOMER_URL='https://api.icicidirect.com/breezeapi/api/v1/customerdetails'
    HISTORICAL_URL='https://breezeapi.icicidirect.com/api/v2/historicalcharts'

    def __init__(self,api_key=None,secret_key=None,session_token=None):
        self.api_key=api_key or os.getenv('BREEZE_API_KEY')
        self.secret_key=secret_key or os.getenv('BREEZE_SECRET_KEY')
        self.session_token=session_token

    @property
    def configured(self): return bool(self.api_key and self.secret_key)

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
        if not self.api_key or not self.session_token: raise RuntimeError('Complete Breeze login first')
        params={'interval':interval,'from_date':from_date,'to_date':to_date,'stock_code':stock_code,
                'exchange_code':'NFO','product_type':'options','expiry_date':expiry_date,
                'right':right.lower(),'strike_price':str(strike_price)}
        r=requests.get(self.HISTORICAL_URL,params=params,
                       headers={'X-SessionToken':self.session_token,'X-apikey':self.api_key},timeout=30)
        r.raise_for_status()
        return r.json().get('Success',[])

    def get_option_chain_quotes(self, stock_code, strike_price, right="call"):
        if not self.api_key or not self.session_token: raise RuntimeError('Complete Breeze login first')
        import hashlib
        from datetime import datetime, timezone
        url = 'https://api.icicidirect.com/breezeapi/api/v1/optionchain'
        time_stamp = datetime.now(timezone.utc).isoformat()[:19] + '.000Z'
        payload_dict = {
            "stock_code": stock_code,
            "exchange_code": "NFO",
            "product_type": "options",
            "right": right.lower(),
            "strike_price": str(strike_price),
            "expiry_date": ""
        }
        payload = json.dumps(payload_dict, separators=(',', ':'))
        checksum_input = time_stamp + payload + self.secret_key
        checksum = hashlib.sha256(checksum_input.encode("utf-8")).hexdigest()
        headers = {
            'Content-Type': 'application/json',
            'X-Checksum': 'token ' + checksum,
            'X-Timestamp': time_stamp,
            'X-AppKey': self.api_key,
            'X-SessionToken': self.session_token
        }
        r = requests.get(url, headers=headers, data=payload, timeout=30)
        r.raise_for_status()
        return r.json().get('Success', [])
