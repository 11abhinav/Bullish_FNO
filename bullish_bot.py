import os
import time
import threading
import requests
import pytz

from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

PE_OI_MIN_THRESHOLD = 50000

# Reduced from 15 to 3 to prevent Akamai WAF IP bans
MAX_WORKERS = 3
CYCLE_SECONDS = 300

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive"
}

# =========================================================
# NSE HOLIDAYS 2026
# =========================================================

NSE_HOLIDAYS_2026 = {
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 14),   # Holi
    date(2026, 3, 25),   # Ram Navami
    date(2026, 4, 2),    # Mahavir Jayanti
    date(2026, 4, 14),   # Ambedkar Jayanti / Dr. Babasaheb
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 8, 15),   # Independence Day
    date(2026, 10, 2),   # Gandhi Jayanti
    date(2026, 11, 12),  # Diwali Laxmi Pujan
    date(2026, 12, 25)   # Christmas
}

# =========================================================
# GLOBALS
# =========================================================

seen_alerts = set()
seen_alerts_lock = threading.Lock()
thread_local = threading.local()

# =========================================================
# F&O SECTOR MAPPING — FULL NSE F&O UNIVERSE
# =========================================================

FNO_SECTORS = {
    "BANKING": [
        "SBIN", "HDFCBANK", "ICICIBANK", "AXISBANK", "KOTAKBANK", "INDUSINDBK",
        "BANKBARODA", "PNB", "FEDERALBNK", "AUBANK", "IDFCFIRSTB", "CANBK",
        "UNIONBANK", "BANDHANBNK", "INDIANB", "IOB", "MAHABANK", "UCOBANK",
        "CENTRALBK", "J&KBANK", "DCBBANK", "RBLBANK", "KARURVYSYA", "CSBBANK",
    ],
    "IT": [
        "INFY", "TCS", "WIPRO", "TECHM", "HCLTECH", "LTIM", "PERSISTENT",
        "COFORGE", "KPITTECH", "MPHASIS", "LTTS", "OFSS", "TATAELXSI",
        "CYIENT", "MASTEK", "ZENSAR", "BIRLASOFT", "NIITLTD", "HEXAWARE",
    ],
    "POWER": [
        "PFC", "RECLTD", "SUZLON", "TATAPOWER", "JSWENERGY", "NTPC", "NHPC",
        "POWERGRID", "IREDA", "SJVN", "CESC", "TORNTPOWER", "RPOWER",
        "ADANIPOWER", "ADANIGREEN", "ADANIENSOL", "INOXWIND", "GREENKO",
    ],
    "INFRA": [
        "LT", "IRB", "RVNL", "NBCC", "KEC", "PNCINFRA", "RAILTEL", "RITES",
        "IRCON", "NCC", "HGINFRA", "KNRCON", "ASHOKA", "GMRAIRPORT",
        "TITAGARH", "TEXRAIL", "APLAPOLLO", "GPPL",
    ],
    "ENERGY": [
        "ONGC", "RELIANCE", "IOC", "BPCL", "HINDPETRO", "OIL", "GAIL",
        "IGL", "MGL", "PETRONET", "GUJGASLTD", "GSPL", "CPCL", "MRPL", "AEGISCHEM",
    ],
    "AUTO": [
        "TATAMOTORS", "MARUTI", "M&M", "HEROMOTOCO", "BAJAJ-AUTO", "EICHERMOT",
        "TVSMOTOR", "ASHOKLEY", "MOTHERSON", "BALKRISIND", "BOSCHLTD", "MRF",
        "BHARATFORG", "APOLLOTYRE", "CEATLTD", "EXIDEIND", "AMARAJABAT",
        "TIINDIA", "ENDURANCE", "SUNDRMFAST", "CRAFTSMAN", "SUPRAJIT",
    ],
    "METAL": [
        "TATASTEEL", "JSWSTEEL", "HINDALCO", "SAIL", "JINDALSTEL", "VEDL",
        "NMDC", "NATIONALUM", "HINDCOPPER", "JSPL", "WELCORP", "RATNAMANI",
        "MOIL", "GMDC", "APL", "MIDHANI",
    ],
    "PHARMA": [
        "SUNPHARMA", "CIPLA", "DIVISLAB", "DRREDDY", "LUPIN", "AUROPHARMA",
        "TORNTPHARM", "ALKEM", "IPCALAB", "GLENMARK", "BIOCON", "ABBOTINDIA",
        "PFIZER", "SANOFI", "GLAXO", "AJANTPHARM", "LAURUSLABS", "GRANULES",
        "NATCOPHARM", "ZYDUSLIFE", "MANKIND", "JBCHEPHARM",
    ],
    "FMCG": [
        "ITC", "HINDUNILVR", "NESTLEIND", "BRITANNIA", "DABUR", "TATACONSUM",
        "COLPAL", "MARICO", "GODREJCP", "EMAMILTD", "VBL", "UBL", "RADICO",
        "MCDOWELL-N", "PATANJALI", "BIKAJI", "DEVYANI", "WESTLIFE",
    ],
    "FINANCE": [
        "BAJFINANCE", "BAJAJFINSV", "JIOFIN", "CHOLAFIN", "SHRIRAMFIN",
        "SBICARD", "MUTHOOTFIN", "MANAPPURAM", "LICHSGFIN", "SUNDARMFIN",
        "M&MFIN", "POONAWALLA", "CREDITACC", "AAVAS", "HOMEFIRST", "MASFIN",
        "PEL", "CANFINHOME", "REPCO", "UGROCAP",
    ],
    "INSURANCE": [
        "HDFCLIFE", "SBILIFE", "ICICIPRULI", "ICICIGI", "NIACL", "GICRE",
        "STARHEALTH", "LICI", "MAXFINSERV",
    ],
    "DEFENCE": [
        "BEL", "HAL", "BDL", "MAZDOCK", "COCHINSHIP", "BEML", "GRSE",
        "PARAS", "DATAPATTNS", "ASTRA", "HSCL",
    ],
    "CEMENT": [
        "ULTRACEMCO", "AMBUJACEM", "ACC", "SHREECEM", "JKCEMENT",
        "RAMCOCEM", "HEIDELBERG", "BIRLACORPN", "ORIENTCEM",
        "DALMIACEM", "NUVOCO", "STARCEMENT",
    ],
    "CHEMICALS": [
        "PIDILITIND", "AARTIIND", "DEEPAKNTR", "NAVINFLUOR", "FINEORG",
        "GALAXYSURF", "ATUL", "SUMICHEM", "TATACHEM", "GNFC", "GSFC",
        "CHAMBAL", "COROMANDEL", "RALLIS", "BAYERCROP", "VINATIORGA",
        "ALKYLAMINE", "CLEAN", "JUBILANT", "PIIND",
    ],
    "TELECOM": [
        "BHARTIARTL", "IDEA", "TATACOMM", "HFCL", "TEJASNET", "STLTECH", "ROUTE",
    ],
    "CONSUMER_DURABLES": [
        "HAVELLS", "VOLTAS", "WHIRLPOOL", "BLUESTAR", "CROMPTON",
        "ORIENTELEC", "BAJAJELEC", "AMBER", "DIXON", "KAJARIACER",
        "CERA", "SYMPHONY", "VGUARD", "POLYCAB", "KEI",
    ],
    "REAL_ESTATE": [
        "DLF", "GODREJPROP", "OBEROIRLTY", "PRESTIGE", "BRIGADE",
        "SOBHA", "PHOENIXLTD", "MAHLIFE", "KOLTEPATIL", "LODHA",
        "SUNTECK", "SIGNATURE",
    ],
    "AVIATION_HOSPITALITY": [
        "INDIGO", "IRCTC", "SPICEJET", "THOMASCOOK", "LEMONTREE",
        "EIHOTEL", "CHALET",
    ],
    "MEDIA": [
        "ZEEL", "PVRINOX", "SUNTV", "NAZARA", "NETWORK18", "TV18BRDCST", "SAREGAMA",
    ],
    "HOSPITALS": [
        "APOLLOHOSP", "FORTIS", "MAXHEALTH", "NH", "KIMS", "RAINBOW", "YATHARTH",
    ],
    "LOGISTICS": [
        "CONCOR", "BLUEDART", "TCI", "ALLCARGO", "DELHIVERY", "GATEWAY", "MAHLOG",
    ],
    "RETAIL": [
        "DMART", "TRENT", "ABFRL", "VMART", "SHOPERSTOP", "BATA", "RELAXO", "METRO", "VEDANT",
    ],
    "AGRI": [
        "UPL", "DHANUKA", "KAVERI", "ASTEC", "JUBLPHARMA",
    ],
    "PAPER_PACKAGING": [
        "TNPL", "WCIL", "HUHTAMAKI", "MOLD-TEK",
    ],
    "TEXTILES": [
        "PAGEIND", "RAYMOND", "ARVIND", "TRIDENT", "VARDHMAN",
        "WELSPUNIND", "GRASIM", "FILATEX",
    ],
    "CAPITAL_GOODS": [
        "SIEMENS", "ABB", "BHEL", "THERMAX", "CUMMINSIND", "GRINDWELL",
        "ELGIEQUIP", "KENNAMETAL", "AIAENG", "KAYNES", "SYRMA",
    ],
    "SPECIALTY": [
        "MGLI", "NOCIL", "INDIGOPNTS", "AKZOINDIA", "BERGER",
        "ASIANPAINT", "KANSAINER", "SHEELA",
    ],
}

# =========================================================
# ALL SYMBOLS — deduplicated flat list
# =========================================================

ALL_FNO_SYMBOLS = sorted(list({
    symbol
    for symbols in FNO_SECTORS.values()
    for symbol in symbols
}))

# =========================================================
# SYMBOL -> SECTOR
# =========================================================

SYMBOL_TO_SECTOR = {}
for _sector, _symbols in FNO_SECTORS.items():
    for _symbol in _symbols:
        if _symbol not in SYMBOL_TO_SECTOR:
            SYMBOL_TO_SECTOR[_symbol] = _sector

# =========================================================
# SAFE FUNCTIONS
# =========================================================

def safe_float(v):
    try:
        return float(str(v).replace(",", ""))
    except:
        return 0.0

def safe_int(v):
    try:
        return int(float(str(v).replace(",", "")))
    except:
        return 0

# =========================================================
# TIME & MARKET HOURS
# =========================================================

def ist_now():
    tz = pytz.timezone("Asia/Kolkata")
    return datetime.now(tz)

def is_market_open():
    now = ist_now()
    if now.weekday() >= 5:
        return False
    if now.date() in NSE_HOLIDAYS_2026:
        return False
    
    market_open = now.replace(hour=8, minute=0, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close

def get_candle_bucket(dt_obj, minutes=5):
    bucket = (dt_obj.minute // minutes) * minutes
    return dt_obj.strftime("%Y-%m-%d %H:") + f"{bucket:02d}"

# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(msg, retry=True):
    try:
        if not BOT_TOKEN or not CHAT_ID:
            print("BOT_TOKEN / CHAT_ID missing")
            return

        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": CHAT_ID,
            "text": msg,
            "parse_mode": "HTML"
        }
        r = requests.post(url, json=payload, timeout=20)

        if r.status_code == 429 and retry:
            print("Telegram rate limit, retrying in 3s...")
            time.sleep(3)
            send_telegram(msg, retry=False)
            
    except Exception as e:
        print("Telegram Error:", e)

# =========================================================
# SESSION (FIXED)
# =========================================================

def get_session():
    now = time.time()
    # Refresh session every 30 minutes
    if not hasattr(thread_local, "session") or now - getattr(thread_local, "session_created", 0) > 1800:
        s = requests.Session()
        s.headers.update(HEADERS)
        try:
            # Hitting a lightweight endpoint instead of the homepage
            s.get("https://www.nseindia.com/api/marketStatus", timeout=10)
            thread_local.session = s
            thread_local.session_created = now
        except Exception as e:
            print("Failed to create session:", e)
    return thread_local.session

# =========================================================
# NSE GET
# =========================================================

def nse_get(url, retries=2):
    for attempt in range(retries):
        try:
            session = get_session()
            r = session.get(url, timeout=20)
            
            if r.status_code == 200:
                return r.json()
                
            if r.status_code in (401, 403):
                # Force session refresh on next call
                thread_local.session_created = 0
                
            if r.status_code == 429:
                time.sleep(2)
                
        except Exception as e:
            print("NSE ERROR:", e)
            
        if attempt < retries - 1:
            time.sleep(1)
            
    return {}

# =========================================================
# MARKET TREND
# =========================================================

def get_market_trend():
    try:
        url = "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050"
        data = nse_get(url)
        if not data:
            return 0
            
        items = data.get("data", [])
        if not items:
            return 0
            
        nifty = items[0]
        return safe_float(nifty.get("pChange"))
        
    except Exception as e:
        print("NIFTY ERROR:", e)
        return 0

# =========================================================
# FETCH STOCK
# =========================================================

def fetch_stock(symbol):
    try:
        result = {
            "symbol": symbol,
            "price": 0,
            "prev_close": 0,
            "vwap": 0,
            "price_pct": 0,
            "oi_change_pct": 0,
            "oi_strength": False,
            "put_writing": False,
            "pe_oi_change": 0
        }

        # 1. EQUITY
        eq_url = f"https://www.nseindia.com/api/quote-equity?symbol={symbol}"
        eq_data = nse_get(eq_url)
        
        if not eq_data:
            time.sleep(0.5) # Throttle before exit
            return None

        p = eq_data.get("priceInfo", {})
        result["price"] = safe_float(p.get("lastPrice"))
        result["prev_close"] = safe_float(p.get("previousClose"))
        result["vwap"] = safe_float(p.get("vwap"))

        price = result["price"]
        prev_close = result["prev_close"]

        if prev_close <= 0:
            time.sleep(0.5)
            return result

        price_pct = ((price - prev_close) / prev_close) * 100
        result["price_pct"] = price_pct

        # EARLY FILTER: Skip heavy deriv/option endpoints if not moving
        if price_pct < 1 or price_pct > 5 or result["vwap"] <= 0:
            time.sleep(0.5) # Throttle
            return result

        # 2. FUTURES OI (FIXED JSON PARSING)
        try:
            oi_url = f"https://www.nseindia.com/api/quote-derivative?symbol={symbol}"
            oi_data = nse_get(oi_url)
            stocks = oi_data.get("stocks", [])
            
            # Robust case-insensitive check
            futures = [
                s for s in stocks
                if str(s.get("metadata", {}).get("instrumentType")).strip().upper() in ["STOCK FUTURES", "FUTSTK"]
            ]

            def parse_expiry(x):
                try:
                    return datetime.strptime(
                        x.get("metadata", {}).get("expiryDate", ""),
                        "%d-%b-%Y"
                    )
                except:
                    return datetime.max

            futures = sorted(futures, key=parse_expiry)

            if futures:
                md = futures[0].get("metadata", {})
                oi_change_pct = safe_float(md.get("pChangeinOpenInterest"))
                result["oi_change_pct"] = oi_change_pct
                result["oi_strength"] = (oi_change_pct > 5)
        except Exception as e:
            print(f"{symbol} OI ERROR:", e)

        # 3. OPTION CHAIN
        try:
            option_url = f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"
            option_data = nse_get(option_url)
            records = option_data.get("records", {})
            rows = records.get("data", [])

            total = 0
            atm_rows = sorted(
                [item for item in rows if abs(safe_float(item.get("strikePrice", 0)) - price) < price * 0.03],
                key=lambda x: abs(safe_float(x.get("strikePrice", 0)) - price)
            )

            for item in atm_rows[:5]:
                if not isinstance(item, dict): continue
                pe = item.get("PE")
                if not pe: continue
                total += safe_int(pe.get("changeinOpenInterest"))

            result["pe_oi_change"] = total
            result["put_writing"] = (total > PE_OI_MIN_THRESHOLD)
        except Exception as e:
            print(f"{symbol} OPTION ERROR:", e)

        time.sleep(0.5) # Throttle to prevent IP block
        return result

    except Exception as e:
        print(symbol, "FETCH ERROR:", e)
        time.sleep(0.5)
        return None

# =========================================================
# SECTOR STRENGTH
# =========================================================

def get_sector_strength(symbol, market_data):
    sector_name = "UNKNOWN"
    sector_score = 0
    sector = SYMBOL_TO_SECTOR.get(symbol)

    if not sector:
        return (sector_name, sector_score)

    stocks = FNO_SECTORS.get(sector, [])
    moves = []

    for s in stocks:
        if s not in market_data: continue
        d = market_data[s]
        pc = d.get("prev_close", 0)
        if pc <= 0: continue
        move = ((d.get("price", 0) - pc) / pc) * 100
        moves.append(move)

    if not moves:
        return (sector_name, sector_score)

    avg_move = sum(moves) / len(moves)

    if avg_move > 2:
        sector_score = 15
    elif avg_move > 1:
        sector_score = 10
    elif avg_move > 0.5:
        sector_score = 5

    sector_name = sector
    return (sector_name, sector_score)

# =========================================================
# BULLISH ENGINE
# =========================================================

def process_bullish_setup(symbol, stock, market_trend, market_data):
    try:
        price = stock.get("price", 0)
        prev_close = stock.get("prev_close", 0)
        vwap = stock.get("vwap", 0)
        price_pct = stock.get("price_pct", 0)
        oi_change_pct = stock.get("oi_change_pct", 0)
        oi_strength = stock.get("oi_strength", False)
        put_writing = stock.get("put_writing", False)
        pe_oi_change = stock.get("pe_oi_change", 0)

        if prev_close <= 0 or price_pct < 1 or price_pct > 5:
            return None

        above_vwap = (vwap > 0 and price > vwap)
        sector_name, sector_score = get_sector_strength(symbol, market_data)

        # SCORE CALCULATION
        score = 0
        if price_pct >= 3: score += 25
        elif price_pct >= 2: score += 20
        elif price_pct >= 1: score += 15

        if above_vwap: score += 20
        if oi_strength: score += 20
        if put_writing: score += 15
        
        score += sector_score

        if market_trend > 1: score += 15
        elif market_trend > 0.5: score += 10

        score = min(score, 100)

        if score < 70:
            return None

        # DUPLICATE FILTER
        key = f"{symbol}-{get_candle_bucket(ist_now())}"
        with seen_alerts_lock:
            if key in seen_alerts:
                return None
            seen_alerts.add(key)

        def icon(v): return "✅" if v else "❌"
        interpretation = "🟢 Strong institutional bullish setup" if score >= 85 else "🟡 Moderate bullish setup"

        msg = (
            f"🔥 <b>BULLISH SETUP</b>\n\n"
            f"<b>Stock:</b> {symbol}\n"
            f"<b>Price:</b> ₹{price:,.2f}\n"
            f"<b>Change:</b> {price_pct:+.2f}%\n\n"
            f"<b>MARKET</b>\n"
            f"📈 NIFTY Trend: {market_trend:+.2f}%\n"
            f"🏭 Sector: {sector_name}\n\n"
            f"<b>SIGNALS</b>\n"
            f"{icon(above_vwap)} Above VWAP\n"
            f"{icon(oi_strength)} Futures OI Buildup\n"
            f"{icon(put_writing)} PE Writing\n\n"
            f"<b>DETAILS</b>\n"
            f"VWAP: ₹{vwap:,.2f}\n"
            f"OI Change: {oi_change_pct:+.2f}%\n"
            f"PE OI Change: {pe_oi_change:,}\n"
            f"Sector Score: {sector_score}\n\n"
            f"<b>CONFIDENCE</b>\n"
            f"🎯 {score}/100\n\n"
            f"{interpretation}"
        )

        return msg

    except Exception as e:
        print(symbol, "PROCESS ERROR:", e)
        return None

# =========================================================
# MAIN LOOP
# =========================================================

def run_bot():
    last_cleared = None

    while True:
        cycle_start = time.time()
        today = ist_now().date()

        if last_cleared != today:
            with seen_alerts_lock:
                seen_alerts.clear()
            last_cleared = today
            print("Cleared old alerts")

        try:
            if not is_market_open():
                print("Market closed...")
                time.sleep(60)
                continue

            print("Checking bullish setups...")
            market_trend = get_market_trend()
            all_data = {}

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                results = list(executor.map(fetch_stock, ALL_FNO_SYMBOLS))

            for r in results:
                if not r: continue
                all_data[r["symbol"]] = r

            snapshot = dict(all_data)
            
            # TELEGRAM BATCHING
            alerts_to_send = []
            for symbol, stock in snapshot.items():
                msg = process_bullish_setup(symbol, stock, market_trend, snapshot)
                if msg:
                    alerts_to_send.append(msg)

            if alerts_to_send:
                batch_msg = ""
                for alert in alerts_to_send:
                    # Telegram max length is 4096. Give a safe buffer.
                    if len(batch_msg) + len(alert) > 3500:
                        send_telegram(batch_msg)
                        batch_msg = alert
                    else:
                        batch_msg += alert + "\n\n---------------------------\n\n"
                
                if batch_msg:
                    send_telegram(batch_msg)

            print(f"Cycle complete: {datetime.now()} | Stocks: {len(snapshot)}")

        except Exception as e:
            print("MAIN LOOP ERROR:", e)

        elapsed = time.time() - cycle_start
        sleep_time = max(0, CYCLE_SECONDS - elapsed)
        print(f"Sleeping {sleep_time:.2f}s")
        time.sleep(sleep_time)

# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    if not BOT_TOKEN or not CHAT_ID:
        print("FATAL: BOT_TOKEN / CHAT_ID not set")
        raise SystemExit(1)

    send_telegram(
        "🚀 Bullish Institutional Bot Started\n"
        f"📊 Tracking {len(ALL_FNO_SYMBOLS)} F&O stocks"
    )
    run_bot()
