import json, os, logging, urllib.parse
from datetime import date, time, datetime
from typing import Optional
import requests
import pyotp

logger = logging.getLogger(__name__)

# Selenium imports for session automation
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
except ImportError:
    webdriver = None

def generate_breeze_session_token(api_key: str, username: str, password: str, totp_secret: str) -> Optional[str]:
    """
    Automates login to ICICI Direct Breeze portal using Selenium and pyotp.
    Returns fresh API_Session token valid for 24 hours.
    """
    if not webdriver:
        logger.error("Selenium is not installed.")
        return None

    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")

    driver = None
    session_token = None

    try:
        driver = webdriver.Chrome(options=chrome_options)
        login_url = f"https://api.icicidirect.com/apiuser/login?api_key={urllib.parse.quote(api_key)}"
        driver.get(login_url)
        wait = WebDriverWait(driver, 15)

        # 1. Input Credentials
        user_field = wait.until(EC.presence_of_element_located((By.ID, "txtUserId")))
        pass_field = driver.find_element(By.ID, "txtPassword")

        user_field.send_keys(username)
        pass_field.send_keys(password)
        driver.find_element(By.ID, "btnLogin").click()

        # 2. Enter TOTP / 2FA Code
        totp = pyotp.TOTP(totp_secret)
        otp_field = wait.until(EC.presence_of_element_located((By.ID, "txtOtp")))
        otp_field.send_keys(totp.now())
        driver.find_element(By.ID, "btnSubmitOtp").click()

        # 3. Extract API_Session from redirected URL parameter
        wait.until(EC.url_contains("API_Session="))
        current_url = driver.current_url
        parsed_url = urllib.parse.urlparse(current_url)
        query_params = urllib.parse.parse_qs(parsed_url.query)

        session_token = query_params.get("API_Session", [None])[0]
        logger.info(f"Breeze Session Token generated successfully: {session_token}")

    except Exception as e:
        logger.error(f"Failed to generate Breeze session via Selenium: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    return session_token

def fetch_simulation_spot_price(
    breeze_client,
    stock_code: str,
    simulation_date: str,
    fallback_spot: float = 24000.0
) -> float:
    """
    Attempts to fetch spot price from Breeze historical charts.
    Falls back gracefully on 401 Unauthorized or API failure.
    """
    if not breeze_client or not getattr(breeze_client, "session_token", None):
        logger.info(f"Breeze client inactive. Using fallback spot: {fallback_spot}")
        return fallback_spot

    try:
        from_time = f"{simulation_date}T09:15:00.000Z"
        to_time = f"{simulation_date}T15:30:00.000Z"

        res = breeze_client.historical_index(
            stock_code=stock_code,
            from_date=from_time,
            to_date=to_time
        )
        if res and isinstance(res, list) and len(res) > 0:
            return float(res[-1].get("close", fallback_spot))

    except Exception as e:
        logger.warning(f"Breeze Session 401/API Error: {e}. Switching to fallback spot price.")

    return fallback_spot

def format_breeze_date(date_obj, target_type):
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
                    logger.warning("Breeze API returned 401 Unauthorized status.")
                    raise requests.exceptions.HTTPError("401 Client Error: Unauthorized User from Breeze API", response=fake_resp)
        except Exception as e:
            if isinstance(e, requests.exceptions.HTTPError):
                raise e

    def generate_session(self, api_secret=None, session_token=None):
        if api_secret:
            self.secret_key = api_secret
        if session_token:
            clean_token = session_token
            if "api_session=" in session_token:
                clean_token = session_token.split("api_session=")[1].split("&")[0]
            try:
                self.session_token = self.exchange_api_session(clean_token)
            except Exception:
                self.session_token = clean_token
        return self.session_token

    def login_url(self):
        return f'https://api.icicidirect.com/apiuser/login?api_key={urllib.parse.quote(self.api_key or "")}'

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

        try:
            r = requests.get(self.HISTORICAL_URL, params=params,
                             headers={'X-SessionToken': self.session_token, 'X-apikey': self.api_key}, timeout=30)
            self._check_response(r)
            return r.json().get('Success', [])
        except requests.exceptions.HTTPError as http_err:
            if "401" in str(http_err):
                logger.warning("Breeze Session Expired (401 Unauthorized) while fetching historical option.")
                try:
                    r = requests.get(self.HISTORICAL_URL, params=params,
                                     headers={'X-SessionToken': self.session_token, 'X-apikey': self.api_key}, timeout=30)
                    self._check_response(r)
                    return r.json().get('Success', [])
                except Exception as ex:
                    logger.error(f"Retry failed for historical option: {ex}")
                    raise http_err
            raise http_err

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

        try:
            r = requests.get(self.HISTORICAL_URL, params=params,
                             headers={'X-SessionToken': self.session_token, 'X-apikey': self.api_key}, timeout=30)
            self._check_response(r)
            return r.json().get('Success', [])
        except requests.exceptions.HTTPError as http_err:
            if "401" in str(http_err):
                logger.warning("Breeze Session Expired (401 Unauthorized) while fetching historical index spot price.")
                try:
                    r = requests.get(self.HISTORICAL_URL, params=params,
                                     headers={'X-SessionToken': self.session_token, 'X-apikey': self.api_key}, timeout=30)
                    self._check_response(r)
                    return r.json().get('Success', [])
                except Exception as ex:
                    logger.error(f"Retry failed for historical index spot price: {ex}")
                    raise http_err
            raise http_err

    def get_option_chain_quotes(self, stock_code, expiry_date=None, right=None, strike_price=None):
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

        url = 'https://breezeapi.icicidirect.com/api/v2/optionchain'
        try:
            r = requests.get(url, params=params, headers={'X-SessionToken': self.session_token, 'X-apikey': self.api_key}, timeout=30)
            if r.status_code == 200:
                return r.json().get('Success', [])
        except Exception:
            pass
        return []
