import os
import time
import threading
import requests
import pytz
import logging

from zoneinfo import ZoneInfo
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor

# =========================================================
# LOGGER
# =========================================================

logging.basicConfig(

    level=logging.INFO,

    format="%(asctime)s | %(levelname)s | %(message)s",

    datefmt="%Y-%m-%d %H:%M:%S"
)

logger = logging.getLogger(__name__)

# =========================================================
# IST
# =========================================================

IST = ZoneInfo("Asia/Kolkata")

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

PE_OI_MIN_THRESHOLD = 50000

MAX_WORKERS = 5

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
    date(2026, 1, 26),
    date(2026, 3, 14),
    date(2026, 3, 25),
    date(2026, 4, 2),
    date(2026, 4, 14),
    date(2026, 5, 1),
    date(2026, 8, 15),
    date(2026, 10, 2),
    date(2026, 11, 12),
    date(2026, 12, 25)
}

# =========================================================
# GLOBALS
# =========================================================

seen_alerts = set()

seen_alerts_lock = threading.Lock()

thread_local = threading.local()

# =========================================================
# F&O SECTOR MAPPING
# =========================================================

FNO_SECTORS = {

    "BANKING": [
        "SBIN",
        "HDFCBANK",
        "ICICIBANK",
        "AXISBANK",
        "KOTAKBANK",
        "INDUSINDBK",
        "BANKBARODA",
        "PNB",
        "FEDERALBNK",
        "AUBANK",
        "IDFCFIRSTB",
        "CANBK",
        "UNIONBANK",
        "BANDHANBNK",
        "INDIANB",
        "IOB",
        "MAHABANK",
        "UCOBANK",
        "CENTRALBK",
        "RBLBANK",
        "KARURVYSYA",
        "CSBBANK",
    ],

    "IT": [
        "INFY",
        "TCS",
        "WIPRO",
        "TECHM",
        "HCLTECH",
        "LTIM",
        "PERSISTENT",
        "COFORGE",
        "KPITTECH",
        "MPHASIS",
        "LTTS",
        "OFSS",
        "TATAELXSI",
        "CYIENT",
        "MASTEK",
        "ZENSAR",
        "BIRLASOFT",
        "HEXAWARE",
    ],

    "POWER": [
        "PFC",
        "RECLTD",
        "SUZLON",
        "TATAPOWER",
        "JSWENERGY",
        "NTPC",
        "NHPC",
        "POWERGRID",
        "IREDA",
        "SJVN",
        "CESC",
        "TORNTPOWER",
        "RPOWER",
        "ADANIPOWER",
        "ADANIGREEN",
        "ADANIENSOL",
        "INOXWIND",
    ],

    "INFRA": [
        "LT",
        "IRB",
        "RVNL",
        "NBCC",
        "KEC",
        "PNCINFRA",
        "RAILTEL",
        "RITES",
        "IRCON",
        "NCC",
        "HGINFRA",
        "KNRCON",
        "ASHOKA",
        "GMRAIRPORT",
        "TITAGARH",
        "TEXRAIL",
    ],

    "ENERGY": [
        "ONGC",
        "RELIANCE",
        "IOC",
        "BPCL",
        "HINDPETRO",
        "OIL",
        "GAIL",
        "IGL",
        "MGL",
        "PETRONET",
        "GUJGASLTD",
        "GSPL",
        "CPCL",
        "MRPL",
    ],

    "AUTO": [
        "TATAMOTORS",
        "MARUTI",
        "M&M",
        "HEROMOTOCO",
        "BAJAJ-AUTO",
        "EICHERMOT",
        "TVSMOTOR",
        "ASHOKLEY",
        "MOTHERSON",
        "BALKRISIND",
        "BOSCHLTD",
        "MRF",
        "BHARATFORG",
        "APOLLOTYRE",
        "CEATLTD",
        "EXIDEIND",
        "AMARAJABAT",
    ],

    "METAL": [
        "TATASTEEL",
        "JSWSTEEL",
        "HINDALCO",
        "SAIL",
        "JINDALSTEL",
        "VEDL",
        "NMDC",
        "NATIONALUM",
        "HINDCOPPER",
        "JSPL",
        "WELCORP",
        "RATNAMANI",
    ],

    "PHARMA": [
        "SUNPHARMA",
        "CIPLA",
        "DIVISLAB",
        "DRREDDY",
        "LUPIN",
        "AUROPHARMA",
        "TORNTPHARM",
        "ALKEM",
        "IPCALAB",
        "GLENMARK",
        "BIOCON",
        "AJANTPHARM",
        "LAURUSLABS",
        "GRANULES",
        "NATCOPHARM",
        "ZYDUSLIFE",
        "MANKIND",
    ],

    "FMCG": [
        "ITC",
        "HINDUNILVR",
        "NESTLEIND",
        "BRITANNIA",
        "DABUR",
        "TATACONSUM",
        "COLPAL",
        "MARICO",
        "GODREJCP",
        "EMAMILTD",
        "VBL",
        "RADICO",
        "PATANJALI",
        "BIKAJI",
        "DEVYANI",
    ],

    "FINANCE": [
        "BAJFINANCE",
        "BAJAJFINSV",
        "JIOFIN",
        "CHOLAFIN",
        "SHRIRAMFIN",
        "SBICARD",
        "MUTHOOTFIN",
        "MANAPPURAM",
        "LICHSGFIN",
        "SUNDARMFIN",
        "M&MFIN",
        "POONAWALLA",
        "AAVAS",
        "HOMEFIRST",
    ],

    "DEFENCE": [
        "BEL",
        "HAL",
        "BDL",
        "MAZDOCK",
        "COCHINSHIP",
        "BEML",
        "GRSE",
        "PARAS",
        "DATAPATTNS",
    ],

    "REAL_ESTATE": [
        "DLF",
        "GODREJPROP",
        "OBEROIRLTY",
        "PRESTIGE",
        "BRIGADE",
        "SOBHA",
        "PHOENIXLTD",
        "LODHA",
    ]
}

# =========================================================
# ALL SYMBOLS
# =========================================================

ALL_FNO_SYMBOLS = sorted(list({

    symbol

    for symbols in FNO_SECTORS.values()

    for symbol in symbols
}))

logger.info(
    f"📊 Total Watchlist Symbols: "
    f"{len(ALL_FNO_SYMBOLS)}"
)

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


# =========================================================
# TIME
# =========================================================

def ist_now():

    tz = pytz.timezone("Asia/Kolkata")

    return datetime.now(tz)

# =========================================================
# MARKET HOURS
# =========================================================

def is_market_open():

    now = ist_now()

    logger.info(
        f"⏰ Market Check IST: "
        f"{now.strftime('%Y-%m-%d %H:%M:%S')}"
    )

    if now.weekday() >= 5:

        logger.info("❌ Weekend detected")

        return False

    if now.date() in NSE_HOLIDAYS_2026:

        logger.info("❌ NSE Holiday")

        return False

    market_open = now.replace(
        hour=9,
        minute=15,
        second=0,
        microsecond=0
    )

    market_close = now.replace(
        hour=15,
        minute=30,
        second=0,
        microsecond=0
    )

    is_open = market_open <= now <= market_close

    logger.info(
        f"📈 Market Open Status: {is_open}"
    )

    return is_open

# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(msg):

    try:

        if not BOT_TOKEN or not CHAT_ID:

            logger.error(
                "BOT_TOKEN / CHAT_ID missing"
            )

            return

        url = (
            f"https://api.telegram.org/"
            f"bot{BOT_TOKEN}/sendMessage"
        )

        payload = {
            "chat_id": CHAT_ID,
            "text": msg
        }

        requests.post(
            url,
            json=payload,
            timeout=20
        )

        logger.info(
            "📨 Telegram message sent"
        )

    except Exception:

        logger.exception(
            "Telegram Error"
        )

# =========================================================
# SESSION
# =========================================================

def get_session():

    now = time.time()

    if (

        not hasattr(thread_local, "session")

        or

        now - getattr(
            thread_local,
            "session_created",
            0
        ) > 1800
    ):

        logger.info(
            "🌐 Creating NSE session..."
        )

        s = requests.Session()

        s.headers.update(HEADERS)

        try:

            s.get(
                "https://www.nseindia.com/api/marketStatus",
                timeout=10
            )

            thread_local.session = s

            thread_local.session_created = now

            logger.info(
                "✅ NSE session created"
            )

        except Exception:

            logger.exception(
                "Failed to create NSE session"
            )

    return thread_local.session

# =========================================================
# NSE GET
# =========================================================

def nse_get(url, retries=2):

    for attempt in range(retries):

        try:

            session = get_session()

            r = session.get(
                url,
                timeout=20
            )

            logger.info(
                f"🌐 NSE API Call | "
                f"Status={r.status_code}"
            )

            if r.status_code == 200:

                logger.info(
                    "✅ NSE API Success"
                )

                return r.json()

            if r.status_code in (401, 403):

                logger.warning(
                    "⚠️ NSE session expired"
                )

                thread_local.session_created = 0

            if r.status_code == 429:

                logger.warning(
                    "⚠️ Rate limit hit"
                )

                time.sleep(2)

        except Exception:

            logger.exception(
                "NSE ERROR"
            )

        if attempt < retries - 1:

            time.sleep(1)

    return {}

# =========================================================
# FETCH STOCK
# =========================================================

def fetch_stock(symbol):

    try:

        logger.info(
            f"🔍 Fetching: {symbol}"
        )

        result = {
            "symbol": symbol,
            "price": 0,
            "prev_close": 0,
            "price_pct": 0,
        }

        eq_url = (
            f"https://www.nseindia.com/api/"
            f"quote-equity?symbol={symbol}"
        )

        eq_data = nse_get(eq_url)

        if not eq_data:

            logger.warning(
                f"❌ Empty data: {symbol}"
            )

            return None

        p = eq_data.get("priceInfo", {})

        result["price"] = safe_float(
            p.get("lastPrice")
        )

        result["prev_close"] = safe_float(
            p.get("previousClose")
        )

        price = result["price"]

        prev_close = result["prev_close"]

        if prev_close <= 0:

            return None

        price_pct = (
            (
                price - prev_close
            ) / prev_close
        ) * 100

        result["price_pct"] = price_pct

        logger.info(
            f"✅ {symbol} | "
            f"Move={price_pct:+.2f}%"
        )

        time.sleep(0.3)

        return result

    except Exception:

        logger.exception(
            f"{symbol} FETCH ERROR"
        )

        return None

# =========================================================
# PROCESS STOCK
# =========================================================

def process_bullish_setup(symbol, stock):

    try:

        price_pct = stock.get(
            "price_pct",
            0
        )

        if price_pct < 2:

            logger.info(
                f"❌ Rejected: {symbol} | "
                f"Move={price_pct:+.2f}%"
            )

            return None

        key = (
            f"{symbol}-"
            f"{ist_now().strftime('%Y-%m-%d-%H-%M')}"
        )

        with seen_alerts_lock:

            if key in seen_alerts:

                logger.info(
                    f"⚠️ Duplicate skipped: {symbol}"
                )

                return None

            seen_alerts.add(key)

        msg = (
            f"🔥 BULLISH SETUP\n\n"
            f"Stock: {symbol}\n"
            f"Move: {price_pct:+.2f}%"
        )

        logger.info(
            f"🚀 Bullish setup detected: {symbol}"
        )

        return msg

    except Exception:

        logger.exception(
            f"{symbol} PROCESS ERROR"
        )

        return None

# =========================================================
# MAIN BOT
# =========================================================

def run_bot():

    logger.info("=" * 80)

    logger.info(
        "🚀 Bullish Setup Scan Started"
    )

    logger.info(
        f"⏰ IST Time: "
        f"{datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}"
    )

    logger.info(
        f"📊 Total Symbols: "
        f"{len(ALL_FNO_SYMBOLS)}"
    )

    logger.info("=" * 80)

    if not is_market_open():

        logger.info(
            "⏰ Market closed"
        )

        return

    all_data = {}

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        results = list(

            executor.map(
                fetch_stock,
                ALL_FNO_SYMBOLS
            )
        )

    logger.info(
        f"✅ NSE Fetch Completed | "
        f"Received={len(results)}"
    )

    for r in results:

        if not r:

            continue

        all_data[r["symbol"]] = r

    alerts_sent = 0

    for idx, (symbol, stock) in enumerate(

        all_data.items(),

        start=1
    ):

        sector = SYMBOL_TO_SECTOR.get(
            symbol,
            "UNKNOWN"
        )

        logger.info(
            f"🔍 Checking: {symbol} | "
            f"Sector={sector} | "
            f"Progress={idx}/{len(all_data)}"
        )

        msg = process_bullish_setup(
            symbol,
            stock
        )

        if msg:

            send_telegram(msg)

            alerts_sent += 1

            logger.info(
                f"✅ ALERT SENT: {symbol}"
            )

    logger.info("=" * 80)

    logger.info(
        f"✅ Scan completed | "
        f"Alerts={alerts_sent}"
    )

    logger.info("=" * 80)

# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    if not BOT_TOKEN or not CHAT_ID:

        logger.error(
            "FATAL: BOT_TOKEN / CHAT_ID not set"
        )

        raise SystemExit(1)

    logger.info("=" * 80)

    logger.info(
        "🚀 Bullish Institutional Bot Started"
    )

    logger.info(
        f"⏰ IST Time: "
        f"{datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}"
    )

    logger.info("=" * 80)

    run_bot()
