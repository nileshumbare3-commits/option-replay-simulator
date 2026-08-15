import re
import calendar
from datetime import date, datetime, timedelta, timezone

# Common Indian Exchange Fixed Holidays (Month, Day)
EXCHANGE_FIXED_HOLIDAYS = {
    (1, 26),   # Republic Day
    (5, 1),    # Maharashtra Day
    (8, 15),   # Independence Day
    (10, 2),   # Gandhi Jayanti
    (12, 25),  # Christmas
}

# Month mapping for weekly and monthly option contracts
WEEKLY_MONTH_MAP = {
    '1': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6,
    '7': 7, '8': 8, '9': 9, 'O': 10, 'N': 11, 'D': 12
}

MONTHLY_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
}

WEEKLY_MONTH_MAP_REV = {v: k for k, v in WEEKLY_MONTH_MAP.items()}
MONTHLY_MAP_REV = {v: k for k, v in MONTHLY_MAP.items()}

def is_exchange_holiday(d: date) -> bool:
    """
    Checks whether a given date is a weekend or standard exchange holiday.
    """
    if d.weekday() in (5, 6): # Saturday or Sunday
        return True
    if (d.month, d.day) in EXCHANGE_FIXED_HOLIDAYS:
        return True
    return False

def adjust_for_holiday(d: date) -> date:
    """
    If the date falls on an exchange holiday or weekend, adjust to the preceding business day.
    """
    curr = d
    while is_exchange_holiday(curr):
        curr -= timedelta(days=1)
    return curr

def normalize_date(val) -> date:
    """
    Parses timestamps, ISO strings, or date objects into a standard YYYY-MM-DD date.
    Handles IST/UTC timezone offsets cleanly.
    """
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, (int, float)):
        # Unix timestamp
        return datetime.fromtimestamp(val, tz=timezone.utc).date()
    if isinstance(val, str):
        clean_str = val.strip()
        # ISO format parser
        try:
            # Try fromisoformat first (handles +05:30, Z, etc.)
            dt = datetime.fromisoformat(clean_str.replace("Z", "+00:00"))
            return dt.date()
        except Exception:
            pass

        # Try simple string formats
        for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%Y/%m/%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(clean_str, fmt).date()
            except Exception:
                pass

    raise ValueError(f"Unable to normalize date value: {val}")

def fallback_calculate_expiry(underlying: str, req_date: date) -> date:
    """
    Fallback rule when contract expiry date is missing:
    Calculates standard contract cycle expiry (e.g., Nifty=Thursday, BankNifty=Wednesday, FinNifty=Tuesday)
    and applies holiday adjustments.
    """
    u_upper = underlying.upper()
    target_weekday = 3 # Default Thursday (NIFTY)
    if "BANK" in u_upper:
        target_weekday = 2 # Wednesday
    elif "FIN" in u_upper:
        target_weekday = 1 # Tuesday

    # Find the target weekday in the current or next week
    days_ahead = target_weekday - req_date.weekday()
    if days_ahead < 0:
        days_ahead += 7
    exp = req_date + timedelta(days=days_ahead)
    return adjust_for_holiday(exp)

def parse_expiry_from_symbol_name(symbol_name: str, sibling_weekly_dates=None) -> date:
    """
    Extract the expiry date directly from contract symbol format:
    - Weekly Format: NIFTY2682524500CE -> Extract 26 (Year 2026), 8 (Month Aug), 25 (Day 25) -> 2026-08-25
    - Monthly Format: NIFTY26AUG24500CE -> Extract 26 (Year 2026), AUG (Month 08).
    """
    symbol = symbol_name.upper().strip()

    # 1. Weekly Format Example: [SYMBOL][YY][M/O/N/D][DD][STRIKE][CE/PE]
    weekly_match = re.search(r'(\d{2})([1-9OND])(\d{2})\d+(CE|PE)$', symbol)
    if weekly_match:
        yy_str, month_code, day_str = weekly_match.groups()[:3]
        year = 2000 + int(yy_str)
        month = WEEKLY_MONTH_MAP[month_code]
        day = int(day_str)
        return adjust_for_holiday(date(year, month, day))

    # 2. Monthly Format Example: [SYMBOL][YY][MMM][STRIKE][CE/PE]
    monthly_match = re.search(r'(\d{2})([A-Z]{3})\d+(CE|PE)$', symbol)
    if monthly_match:
        yy_str, month_str = monthly_match.groups()[:2]
        year = 2000 + int(yy_str)
        month = MONTHLY_MAP.get(month_str)
        if month:
            if sibling_weekly_dates:
                siblings = [d for d in sibling_weekly_dates if d.year == year and d.month == month]
                if siblings:
                    return max(siblings)
            _, last_day = calendar.monthrange(year, month)
            return adjust_for_holiday(date(year, month, last_day))

    raise ValueError(f"Unable to parse expiry date from symbol: {symbol_name}")

def parse_expiry_from_contract(contract_symbol: str) -> date:
    return parse_expiry_from_symbol_name(contract_symbol)

def process_historical_contracts_payload(payload: dict) -> dict:
    """
    Processes historical contract datasets/JSON payload to determine limit expiries,
    handle roll-overs and contract expirations, and return the filtered option chain matrix.
    """
    underlying = payload.get("underlying", "NIFTY").upper()
    req_date_raw = payload.get("request_date")
    if not req_date_raw:
        req_date = date.today()
    else:
        req_date = normalize_date(req_date_raw)

    contracts = payload.get("historical_contracts", [])
    if not contracts:
        # Fallback if no contracts provided
        fallback_exp = fallback_calculate_expiry(underlying, req_date)
        return {
            "status": "SUCCESS",
            "underlying": underlying,
            "target_limit_expiry": fallback_exp.isoformat(),
            "is_historical_contract": False,
            "warning": "No historical contracts provided. Calculated default limit expiry.",
            "option_chain": []
        }

    # Step 1 & 2: Parse contract expiries and determine active vs expired status
    parsed_contracts = []
    contract_expiries = set()

    for c in contracts:
        # Check target fields for expiry date
        raw_exp = None
        for key in ("limit_expiry", "expiry", "expiry_date", "expiration_timestamp", "last_trade_date"):
            if key in c and c[key] is not None:
                raw_exp = c[key]
                break

        if raw_exp is not None:
            try:
                exp_date = normalize_date(raw_exp)
            except Exception:
                exp_date = fallback_calculate_expiry(underlying, req_date)
        else:
            # Fallback to symbol parsing or specification calculation
            sym = c.get("symbol", "")
            try:
                exp_date = parse_expiry_from_symbol_name(sym)
            except Exception:
                exp_date = fallback_calculate_expiry(underlying, req_date)

        exp_date = adjust_for_holiday(exp_date)
        contract_expiries.add(exp_date)

        is_expired = (req_date > exp_date)
        parsed_c = dict(c)
        parsed_c["normalized_expiry"] = exp_date
        parsed_c["status"] = "EXPIRED" if is_expired else "ACTIVE"
        parsed_contracts.append(parsed_c)

    sorted_expiries = sorted(list(contract_expiries))

    # Determine target limit expiry date
    future_or_current_expiries = [e for e in sorted_expiries if e >= req_date]

    if future_or_current_expiries:
        target_limit_expiry = min(future_or_current_expiries)
        is_historical = True
        warning = None
    else:
        # All contracts expired relative to req_date -> Retrieve closest prior expiry date (roll over)
        if sorted_expiries:
            target_limit_expiry = max(sorted_expiries)
            is_historical = True
            warning = f"Requested date {req_date} is after all historical contracts. Rolled over to last valid expiry {target_limit_expiry}."
        else:
            target_limit_expiry = fallback_calculate_expiry(underlying, req_date)
            is_historical = False
            warning = f"Out-of-Range Request: {req_date} falls outside historical contract availability window."

    # Check if request date is way prior to all available contracts
    if sorted_expiries and req_date < min(sorted_expiries) - timedelta(days=365):
        warning = f"Out-of-Range Request: Requested date {req_date} is prior to historical contract availability window."

    # Step 3: Option Chain Filtering & Grouping
    # Filter contracts matching target_limit_expiry
    matching_contracts = [c for c in parsed_contracts if c["normalized_expiry"] == target_limit_expiry and c.get("strike") is not None]

    strikes_map = {}
    for c in matching_contracts:
        strike_val = float(c.get("strike"))
        opt_type = str(c.get("option_type", "")).upper()
        if not opt_type:
            # Attempt to deduce option type from symbol
            sym = str(c.get("symbol", "")).upper()
            if sym.endswith("CE") or "CALL" in sym:
                opt_type = "CE"
            elif sym.endswith("PE") or "PUT" in sym:
                opt_type = "PE"

        if strike_val not in strikes_map:
            strikes_map[strike_val] = {"call": None, "put": None}

        metric_dict = {
            "symbol": c.get("symbol"),
            "ltp": float(c.get("ltp", 0.0) or 0.0),
            "oi": int(c.get("oi", 0) or 0),
            "iv": float(c.get("iv", 0.0) or 0.0),
            "volume": int(c.get("volume", 0) or 0)
        }

        if opt_type == "CE" or opt_type == "CALL":
            strikes_map[strike_val]["call"] = metric_dict
        elif opt_type == "PE" or opt_type == "PUT":
            strikes_map[strike_val]["put"] = metric_dict

    sorted_strikes = sorted(strikes_map.keys())
    option_chain = []

    for s in sorted_strikes:
        c_info = strikes_map[s]["call"] or {"symbol": None, "ltp": 0.0, "oi": 0, "iv": 0.0, "volume": 0}
        p_info = strikes_map[s]["put"] or {"symbol": None, "ltp": 0.0, "oi": 0, "iv": 0.0, "volume": 0}
        option_chain.append({
            "strike_price": int(s) if s.is_integer() else s,
            "call": c_info,
            "put": p_info
        })

    response = {
        "status": "SUCCESS",
        "underlying": underlying,
        "target_limit_expiry": target_limit_expiry.isoformat(),
        "is_historical_contract": is_historical,
        "option_chain": option_chain
    }

    if warning:
        response["warning"] = warning

    return response

def get_nearest_weekday(start_date: date, target_weekday: int) -> date:
    days_ahead = target_weekday - start_date.weekday()
    if days_ahead < 0:
        days_ahead += 7
    return start_date + timedelta(days=days_ahead)

def generate_mock_symbols_for_demo(underlying: str, atm_strike: float, step: float, replay_date: date) -> list:
    underlying_upper = underlying.upper().strip()
    target_weekday = 3
    if "BANK" in underlying_upper:
        target_weekday = 2
    elif "FIN" in underlying_upper:
        target_weekday = 1

    first_expiry = get_nearest_weekday(replay_date, target_weekday)
    exp_dates = [first_expiry + timedelta(days=7 * i) for i in range(4)]
    strikes = [round(atm_strike + (i - 10) * step, 2) for i in range(21)]
    symbols = []

    for idx, exp_date in enumerate(exp_dates):
        exp_date_adj = adjust_for_holiday(exp_date)
        yy = exp_date_adj.strftime("%y")
        month_val = exp_date_adj.month
        is_monthly = (idx == 3)

        month_code = WEEKLY_MONTH_MAP_REV.get(month_val, str(month_val))
        day_str = f"{exp_date_adj.day:02d}"

        for strike in strikes:
            strike_str = str(int(strike))
            for right in ["CE", "PE"]:
                sym_weekly = f"{underlying_upper}{yy}{month_code}{day_str}{strike_str}{right}"
                symbols.append(sym_weekly)
                if is_monthly:
                    month_str = MONTHLY_MAP_REV.get(month_val, "AUG")
                    sym_monthly = f"{underlying_upper}{yy}{month_str}{strike_str}{right}"
                    symbols.append(sym_monthly)
    return symbols

def get_dynamic_expiry_dates(symbol: str, atm_strike: float, step: float, replay_date: date, client=None) -> list:
    symbols = []
    symbol_upper = symbol.upper().strip()

    if client and client.configured and client.session_token:
        try:
            quotes = client.get_option_chain_quotes(symbol_upper)
            if quotes:
                for q in quotes:
                    for key in ["symbol", "symbol_name", "contract_name", "symbol_code", "contract_detail"]:
                        val = q.get(key)
                        if val and len(val) > 10:
                            symbols.append(val)
        except Exception:
            pass

    if not symbols:
        symbols = generate_mock_symbols_for_demo(symbol_upper, atm_strike, step, replay_date)

    weekly_dates = []
    monthly_contracts = []

    for s in symbols:
        try:
            if re.search(r'(\d{2})([1-9OND])(\d{2})\d+(CE|PE)$', s.upper().strip()):
                weekly_dates.append(parse_expiry_from_symbol_name(s))
            else:
                monthly_contracts.append(s)
        except Exception:
            pass

    unique_weekly = sorted(list(set(weekly_dates)))
    all_dates = list(unique_weekly)

    for s in monthly_contracts:
        try:
            d = parse_expiry_from_symbol_name(s, sibling_weekly_dates=unique_weekly)
            all_dates.append(d)
        except Exception:
            pass

    unique_all = sorted(list(set(all_dates)))
    filtered_dates = [d for d in unique_all if d >= replay_date]
    if not filtered_dates:
        filtered_dates = unique_all if unique_all else [replay_date]

    return filtered_dates

def format_contract_symbol(underlying_symbol: str, expiry_date_val: date, strike: float, right: str, available_expiries=None) -> str:
    yy = expiry_date_val.strftime("%y")
    strike_str = str(int(float(strike)))
    right_upper = right.upper()
    right_code = "CE" if right_upper in ["CALL", "CE"] else "PE"

    is_monthly = False
    if available_expiries:
        sibling_expiries = [d for d in available_expiries if d.year == expiry_date_val.year and d.month == expiry_date_val.month]
        if sibling_expiries and expiry_date_val == max(sibling_expiries):
            is_monthly = True
    else:
        if expiry_date_val.day >= 25:
            is_monthly = True

    if is_monthly:
        month_str = expiry_date_val.strftime("%b").upper()
        return f"{underlying_symbol.upper()}{yy}{month_str}{strike_str}{right_code}"
    else:
        month_val = expiry_date_val.month
        inv_map = {v: k for k, v in WEEKLY_MONTH_MAP.items()}
        m_code = inv_map.get(month_val, str(month_val))
        dd_str = f"{expiry_date_val.day:02d}"
        return f"{underlying_symbol.upper()}{yy}{m_code}{dd_str}{strike_str}{right_code}"
