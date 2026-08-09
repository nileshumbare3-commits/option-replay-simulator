import re
from datetime import date, datetime, timedelta

# Month mapping for weekly option contracts
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

def parse_expiry_from_symbol_name(symbol_name: str, sibling_weekly_dates=None) -> date:
    """
    Extract the expiry date directly from the contract string format:
    - Weekly Format: NIFTY2682524500CE -> Extract 26 (Year 2026), 8 (Month Aug), 25 (Day 25) -> 2026-08-25
    - Monthly Format: NIFTY26AUG24500CE -> Extract 26 (Year 2026), AUG (Month 08).
      If sibling_weekly_dates is provided, find the maximum weekly date of that year and month.
      Otherwise, fall back to the last day of that month.
    """
    symbol = symbol_name.upper().strip()

    # 1. Weekly Format Example: [SYMBOL][YY][M/O/N/D][DD][STRIKE][CE/PE]
    # e.g., NIFTY2682524500CE or BANKNIFTY26O1552000PE
    weekly_match = re.search(r'(\d{2})([1-9OND])(\d{2})\d+(CE|PE)$', symbol)
    if weekly_match:
        yy_str, month_code, day_str = weekly_match.groups()[:3]
        year = 2000 + int(yy_str)
        month = WEEKLY_MONTH_MAP[month_code]
        day = int(day_str)
        return date(year, month, day)

    # 2. Monthly Format Example: [SYMBOL][YY][MMM][STRIKE][CE/PE]
    # e.g., NIFTY26AUG24500CE
    monthly_match = re.search(r'(\d{2})([A-Z]{3})\d+(CE|PE)$', symbol)
    if monthly_match:
        yy_str, month_str = monthly_match.groups()[:2]
        year = 2000 + int(yy_str)
        month = MONTHLY_MAP.get(month_str)
        if month:
            if sibling_weekly_dates:
                # Filter for sibling dates with the same year and month
                siblings = [d for d in sibling_weekly_dates if d.year == year and d.month == month]
                if siblings:
                    return max(siblings)
            # Fallback to the last day of the month (avoiding weekday-based calendar math)
            import calendar
            _, last_day = calendar.monthrange(year, month)
            return date(year, month, last_day)

    raise ValueError(f"Unable to parse expiry date from symbol: {symbol_name}")

def parse_expiry_from_contract(contract_symbol: str) -> date:
    """
    Compatibility wrapper for old codebase imports.
    """
    return parse_expiry_from_symbol_name(contract_symbol)

def generate_mock_symbols_for_demo(underlying: str, atm_strike: float, step: float, replay_date: date) -> list:
    """
    Generates a realistic set of weekly and monthly contract symbols centered around the ATM strike
    for 4 expiries near the replay date.
    """
    # Expiries at 4 offsets: e.g. 5, 12, 19, 26 days from replay_date
    exp_offsets = [5, 12, 19, 26]
    strikes = [round(atm_strike + (i - 10) * step, 2) for i in range(21)]
    symbols = []

    underlying_upper = underlying.upper().strip()

    # We will generate monthly symbol for the last offset, weekly for the first three
    for idx, offset in enumerate(exp_offsets):
        exp_date = replay_date + timedelta(days=offset)
        yy = exp_date.strftime("%y")
        month_val = exp_date.month

        # Determine whether to format as monthly or weekly
        # To make it realistic, the last one can be monthly
        is_monthly = (idx == 3)

        for strike in strikes:
            strike_str = str(int(strike))
            for right in ["CE", "PE"]:
                if is_monthly:
                    # e.g., NIFTY26AUG24500CE
                    month_str = MONTHLY_MAP_REV.get(month_val, "AUG")
                    sym = f"{underlying_upper}{yy}{month_str}{strike_str}{right}"
                else:
                    # e.g., NIFTY2682524500CE
                    month_code = WEEKLY_MONTH_MAP_REV.get(month_val, str(month_val))
                    day_str = f"{exp_date.day:02d}"
                    sym = f"{underlying_upper}{yy}{month_code}{day_str}{strike_str}{right}"
                symbols.append(sym)
    return symbols

def get_dynamic_expiry_dates(symbol: str, atm_strike: float, step: float, replay_date: date, client=None) -> list:
    """
    Dynamically determine available expiry dates by inspecting the active/historical contract symbols
    for strikes near the current Spot Price (ATM), parsing the expiry dates embedded within those contract names,
    and sorting/filtering relative to the trading date.
    """
    symbols = []
    symbol_upper = symbol.upper().strip()

    # Step A: Fetch contracts near ATM (try Breeze if configured/active, or generate mock symbols)
    if client and client.configured and client.session_token:
        try:
            # We fetch options quotes to inspect actual active/historical contract symbols
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

    # Step B: Parse symbol names
    weekly_dates = []
    monthly_contracts = []

    for s in symbols:
        try:
            # check if it is weekly format (has day digits before CE/PE)
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

    # Step C: Deduplicate & Sort
    unique_all = sorted(list(set(all_dates)))

    # Filter for dates >= replay_date
    filtered_dates = [d for d in unique_all if d >= replay_date]
    if not filtered_dates:
        # Fallback if no dates in future to prevent UnboundLocalError or crash
        filtered_dates = unique_all if unique_all else [replay_date]

    return filtered_dates

def format_contract_symbol(underlying_symbol: str, expiry_date_val: date, strike: float, right: str, available_expiries=None) -> str:
    """
    Encodes an option contract into standardized NSE/Breeze symbol format.
    Determines whether the expiry is weekly or monthly (using parsed expiries if available)
    and formats accordingly, completely free of weekday math.
    """
    yy = expiry_date_val.strftime("%y") # e.g. "26"
    strike_str = str(int(float(strike)))
    right_upper = right.upper()
    right_code = "CE" if right_upper in ["CALL", "CE"] else "PE"

    # To avoid weekday math, we can see if it's the last expiry of the month from available_expiries
    is_monthly = False
    if available_expiries:
        sibling_expiries = [d for d in available_expiries if d.year == expiry_date_val.year and d.month == expiry_date_val.month]
        if sibling_expiries and expiry_date_val == max(sibling_expiries):
            is_monthly = True
    else:
        # Simple fallback: if day is >= 25 (last week of the month), treat it as monthly
        if expiry_date_val.day >= 25:
            is_monthly = True

    if is_monthly:
        month_str = expiry_date_val.strftime("%b").upper() # e.g. "AUG"
        return f"{underlying_symbol.upper()}{yy}{month_str}{strike_str}{right_code}"
    else:
        month_val = expiry_date_val.month
        inv_map = {v: k for k, v in WEEKLY_MONTH_MAP.items()}
        m_code = inv_map.get(month_val, str(month_val))
        dd_str = f"{expiry_date_val.day:02d}"
        return f"{underlying_symbol.upper()}{yy}{m_code}{dd_str}{strike_str}{right_code}"
