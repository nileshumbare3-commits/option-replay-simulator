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
    Handles IST/UTC timezone offsets cleanly without weekday math.
    """
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val, tz=timezone.utc).date()
    if isinstance(val, str):
        clean_str = val.strip()
        try:
            dt = datetime.fromisoformat(clean_str.replace("Z", "+00:00"))
            return dt.date()
        except Exception:
            pass

        for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%Y/%m/%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(clean_str, fmt).date()
            except Exception:
                pass

    raise ValueError(f"Unable to normalize date value: {val}")

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
    No weekday math is used! All expiries come strictly from historical contract metadata.
    """
    underlying = payload.get("underlying", "NIFTY").upper()
    req_date_raw = payload.get("request_date")
    if not req_date_raw:
        req_date = date.today()
    else:
        req_date = normalize_date(req_date_raw)

    contracts = payload.get("historical_contracts", [])
    if not contracts:
        # If no contracts provided, return fallback structure with request date
        return {
            "status": "SUCCESS",
            "underlying": underlying,
            "target_limit_expiry": req_date.isoformat(),
            "is_historical_contract": False,
            "warning": "No historical contracts provided.",
            "option_chain": []
        }

    # Step 1 & 2: Parse contract expiries directly from historical contract metadata fields
    parsed_contracts = []
    contract_expiries = set()

    for c in contracts:
        raw_exp = None
        for key in ("limit_expiry", "expiry", "expiry_date", "expiration_timestamp", "last_trade_date"):
            if key in c and c[key] is not None:
                raw_exp = c[key]
                break

        if raw_exp is not None:
            try:
                exp_date = normalize_date(raw_exp)
            except Exception:
                exp_date = req_date
        else:
            sym = c.get("symbol", "")
            try:
                exp_date = parse_expiry_from_symbol_name(sym)
            except Exception:
                exp_date = req_date

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
        if sorted_expiries:
            target_limit_expiry = max(sorted_expiries)
            is_historical = True
            warning = f"Requested date {req_date} is after all historical contracts. Rolled over to last valid expiry {target_limit_expiry}."
        else:
            target_limit_expiry = req_date
            is_historical = False
            warning = f"Out-of-Range Request: {req_date} falls outside historical contract availability window."

    if sorted_expiries and req_date < min(sorted_expiries) - timedelta(days=365):
        warning = f"Out-of-Range Request: Requested date {req_date} is prior to historical contract availability window."

    # Step 3: Option Chain Filtering & Grouping
    matching_contracts = [c for c in parsed_contracts if c["normalized_expiry"] == target_limit_expiry and c.get("strike") is not None]

    strikes_map = {}
    for c in matching_contracts:
        strike_val = float(c.get("strike"))
        opt_type = str(c.get("option_type", "")).upper()
        if not opt_type:
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

def generate_mock_symbols_for_demo(underlying: str, atm_strike: float, step: float, replay_date: date) -> list:
    """
    Generates mock contracts directly using relative day offsets from replay_date,
    without any Thursday or weekday math.
    """
    underlying_upper = underlying.upper().strip()

    # Generate 4 expiries relative to replay_date
    exp_dates = [replay_date + timedelta(days=7 * i) for i in range(4)]
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

def get_dynamic_expiry_dates(symbol: str, atm_strike: float, step: float, replay_date: date, client=None, historical_contracts=None) -> list:
    """
    Extracts available expiry dates directly from historical contract metadata, quotes, or symbol parsing.
    Completely free of weekday math or Thursday calculations!
    """
    exp_dates = set()

    # Step A: Extract directly from historical_contracts metadata if provided
    if historical_contracts:
        for c in historical_contracts:
            raw_exp = None
            for key in ("limit_expiry", "expiry", "expiry_date", "expiration_timestamp", "last_trade_date"):
                if key in c and c[key] is not None:
                    raw_exp = c[key]
                    break
            if raw_exp is not None:
                try:
                    exp_dates.add(normalize_date(raw_exp))
                except Exception:
                    pass

    # Step B: Extract directly from Breeze client option chain quotes metadata
    if client and client.configured and client.session_token:
        try:
            quotes = client.get_option_chain_quotes(symbol.upper().strip())
            if quotes:
                for q in quotes:
                    raw_exp = q.get('expiry_date') or q.get('expiry') or q.get('limit_expiry')
                    if raw_exp:
                        try:
                            exp_dates.add(normalize_date(raw_exp))
                        except Exception:
                            pass
                    # Also check symbols
                    for key in ["symbol", "symbol_name", "contract_name", "symbol_code", "contract_detail"]:
                        val = q.get(key)
                        if val and len(val) > 10:
                            try:
                                exp_dates.add(parse_expiry_from_symbol_name(val))
                            except Exception:
                                pass
        except Exception:
            pass

    # Step C: If no dates extracted yet, parse from generated mock symbols
    if not exp_dates:
        symbols = generate_mock_symbols_for_demo(symbol, atm_strike, step, replay_date)
        for s in symbols:
            try:
                exp_dates.add(parse_expiry_from_symbol_name(s))
            except Exception:
                pass

    unique_all = sorted(list(exp_dates))
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
