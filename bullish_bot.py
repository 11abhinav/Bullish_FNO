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

logger.info("=" * 80)
logger.info("🚀 SCRIPT STARTED")
logger.info("=" * 80)

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

# VERY IMPORTANT
# LOWER THREADS TO REDUCE NSE BLOCKING
MAX_WORKERS = 1

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
# WATCHLIST
# =========================================================

ALL_FNO_SYMBOLS = [

    "SBIN",
    "HDFCBANK",
    "ICICIBANK",
    "AXISBANK",
    "KOTAKBANK",
    "INFY",
    "TCS",
    "WIPRO",
    "LT",
    "BEL",
    "RVNL",
    "PFC",
    "RECLTD",
    "SUZLON",
    "JIOFIN",
    "TRENT",
    "TITAN",
    "MARUTI",
    "ONGC",
    "TATAPOWER",
    "JSWENERGY",
    "MAZDOCK",
    "HAL",
    "COCHINSHIP",
    "ADANIPORTS",
    "ADANIENT",
    "CGPOWER",
    "BHEL",
    "IRB",
    "NTPC",
    "BANKBARODA",
    "CANBK",
    "UNIONBANK",
    "LODHA",
    "DLF",
    "KPITTECH",
    "PERSISTENT",
    "COFORGE",
    "POLYCAB",
    "KEI",
    "VBL",
    "TATATECH",
    "PIDILITIND",
    "PVRINOX",
    "INDUSTOWER",
    "HINDCOPPER",
    "TATASTEEL",
    "JSWSTEEL",
    "HINDALCO",
    "POWERGRID",
    "IOC",
    "BPCL",
    "GAIL",
    "SUNPHARMA",
    "DIVISLAB",
    "DRREDDY",
    "CIPLA",
    "BAJFINANCE",
    "BAJAJFINSV",
    "CHOLAFIN",
    "SHRIRAMFIN",
    "MUTHOOTFIN",
    "MANAPPURAM",
    "TATAMOTORS",
    "M&M",
    "EICHERMOT",
    "TVSMOTOR",
    "ASHOKLEY"
]

logger.info(
    f"📊 Total Symbols Loaded: "
    f"{len(ALL_FNO_SYMBOLS)}"
)

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
        f"📈 Market Open Status: "
        f"{is_open}"
    )

    return is_open

# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(msg):

    try:

        if not BOT_TOKEN or not CHAT_ID:

            logger.error(
                "❌ BOT_TOKEN / CHAT_ID missing"
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

        r = requests.post(

            url,

            json=payload,

            timeout=20
        )

        logger.info(
            f"📨 Telegram Status="
            f"{r.status_code}"
        )

    except Exception:

        logger.exception(
            "❌ Telegram Error"
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

            r = s.get(

                "https://www.nseindia.com/api/marketStatus",

                timeout=10
            )

            logger.info(
                f"🌐 NSE Session Init "
                f"Status={r.status_code}"
            )

            thread_local.session = s

            thread_local.session_created = now

            logger.info(
                "✅ NSE session created"
            )

        except Exception:

            logger.exception(
                "❌ Failed to create NSE session"
            )

    return thread_local.session

# =========================================================
# NSE GET
# =========================================================

def nse_get(url, retries=1):

    for attempt in range(retries):

        try:

            session = get_session()

            logger.info(
                f"🌐 NSE API Call | "
                f"Attempt={attempt+1}"
            )

            r = session.get(

                url,

                timeout=20
            )

            logger.info(
                f"📡 NSE Status="
                f"{r.status_code}"
            )

            if r.status_code == 200:

                logger.info(
                    "✅ NSE API Success"
                )

                return r.json()

            if r.status_code in (401, 403):

                logger.warning(
                    "⚠️ NSE session expired / blocked"
                )

                thread_local.session_created = 0

            if r.status_code == 429:

                logger.warning(
                    "⚠️ NSE rate limit hit"
                )

                time.sleep(2)

        except Exception:

            logger.exception(
                "❌ NSE ERROR"
            )

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

        # IMPORTANT
        # SLOW DOWN REQUESTS
        time.sleep(1)

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

        logger.info(
            f"📦 NSE Response | "
            f"{symbol} | "
            f"HasData={bool(eq_data)}"
        )

        if not eq_data:

            logger.warning(
                f"❌ Empty NSE response: "
                f"{symbol}"
            )

            logger.warning(
                "⚠️ NSE likely blocked "
                "or returned empty response"
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

            logger.warning(
                f"❌ Invalid prev_close: "
                f"{symbol}"
            )

            return None

        price_pct = (
            (
                price - prev_close
            ) / prev_close
        ) * 100

        result["price_pct"] = price_pct

        logger.info(
            f"✅ {symbol} | "
            f"Price={price} | "
            f"Move={price_pct:+.2f}%"
        )

        return result

    except Exception:

        logger.exception(
            f"❌ {symbol} FETCH ERROR"
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

        # LOWERED THRESHOLD
        if price_pct < 0.8:

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
                    f"⚠️ Duplicate skipped: "
                    f"{symbol}"
                )

                return None

            seen_alerts.add(key)

        msg = (
            f"🔥 BULLISH SETUP\n\n"
            f"Stock: {symbol}\n"
            f"Move: {price_pct:+.2f}%"
        )

        logger.info(
            f"🚀 Bullish setup detected: "
            f"{symbol}"
        )

        return msg

    except Exception:

        logger.exception(
            f"❌ {symbol} PROCESS ERROR"
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

    logger.info(
        "📊 Starting NSE fetch..."
    )

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

    logger.info(
        f"📦 Valid Stocks Received: "
        f"{len(all_data)}"
    )

    alerts_sent = 0

    for idx, (symbol, stock) in enumerate(

        all_data.items(),

        start=1
    ):

        logger.info(
            f"🔍 Checking: {symbol} | "
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
                f"✅ ALERT SENT: "
                f"{symbol}"
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

    try:

        if not BOT_TOKEN or not CHAT_ID:

            logger.error(
                "❌ FATAL: BOT_TOKEN / CHAT_ID not set"
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

        logger.info(
            "✅ BOT EXECUTION FINISHED"
        )

    except Exception:

        logger.exception(
            "❌ MAIN CRITICAL ERROR"
        )
