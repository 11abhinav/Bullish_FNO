import os
import sys
import time
import requests
import pytz
import logging
import traceback
import pandas as pd
import yfinance as yf

from zoneinfo import ZoneInfo
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

# =========================================================
# FORCE START LOG
# =========================================================

print("🚀 FILE STARTED", flush=True)

# =========================================================
# LOGGER
# =========================================================

logging.basicConfig(

    level=logging.INFO,

    format="%(asctime)s | %(levelname)s | %(message)s",

    datefmt="%Y-%m-%d %H:%M:%S",

    force=True,

    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger()

logger.setLevel(logging.INFO)

# FORCE FLUSH
for handler in logger.handlers:

    handler.flush = sys.stdout.flush

def log(message):

    print(message, flush=True)

    logger.info(message)

log("=" * 80)
log("🚀 SCRIPT STARTED")
log("=" * 80)

# =========================================================
# IST
# =========================================================

IST = ZoneInfo("Asia/Kolkata")

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

CHAT_ID = os.getenv("CHAT_ID")

PRICE_MOVE_THRESHOLD = 0.8

MAX_WORKERS = 1

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

log(
    f"📊 Total Symbols Loaded: "
    f"{len(ALL_FNO_SYMBOLS)}"
)

# =========================================================
# SAFE FLOAT
# =========================================================

def safe_float(v):

    try:
        return float(v)

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

    log(
        f"⏰ Market Check IST: "
        f"{now.strftime('%Y-%m-%d %H:%M:%S')}"
    )

    if now.weekday() >= 5:

        log("❌ Weekend detected")

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

    log(
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

            log(
                "❌ BOT_TOKEN / CHAT_ID missing"
            )

            return

        log(
            "📨 Sending Telegram alert..."
        )

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

        log(
            f"📨 Telegram Status="
            f"{r.status_code}"
        )

    except Exception:

        traceback.print_exc()

        log("❌ Telegram Error")

# =========================================================
# FETCH STOCK
# =========================================================

def fetch_stock(symbol):

    try:

        log(
            f"🚀 START FETCH: {symbol}"
        )

        df = yf.download(

            f"{symbol}.NS",

            period="1d",

            interval="5m",

            progress=False,

            auto_adjust=True,

            threads=False
        )

        log(
            f"📦 Rows={len(df)} | "
            f"{symbol}"
        )

        if df.empty:

            log(
                f"❌ No data: {symbol}"
            )

            return None

        # FIX MULTI INDEX
        if isinstance(
            df.columns,
            pd.MultiIndex
        ):

            log(
                f"🛠️ Fixing MultiIndex: "
                f"{symbol}"
            )

            df.columns = (
                df.columns
                .get_level_values(0)
            )

        latest = df.iloc[-1]

        prev_close = safe_float(
            df["Close"].iloc[-2]
        )

        last_price = safe_float(
            latest["Close"]
        )

        if prev_close <= 0:

            log(
                f"❌ Invalid prev close: "
                f"{symbol}"
            )

            return None

        move_pct = (
            (
                last_price - prev_close
            ) / prev_close
        ) * 100

        log(
            f"✅ {symbol} | "
            f"Move={round(move_pct,2)}%"
        )

        return {

            "symbol": symbol,

            "price": last_price,

            "move_pct": move_pct
        }

    except Exception:

        traceback.print_exc()

        log(
            f"❌ FETCH ERROR: "
            f"{symbol}"
        )

        return None

# =========================================================
# PROCESS STOCK
# =========================================================

def process_stock(symbol, stock):

    try:

        move_pct = stock["move_pct"]

        log(
            f"🔎 Checking: {symbol} | "
            f"Move={round(move_pct,2)}%"
        )

        if move_pct < PRICE_MOVE_THRESHOLD:

            log(
                f"❌ Rejected: {symbol}"
            )

            return

        msg = (

            f"🔥 BULLISH SETUP\n\n"

            f"Stock: {symbol}\n"

            f"Move: {move_pct:+.2f}%\n"

            f"Price: ₹{stock['price']}"
        )

        send_telegram(msg)

        log(
            f"✅ ALERT SENT: "
            f"{symbol}"
        )

    except Exception:

        traceback.print_exc()

# =========================================================
# MAIN BOT
# =========================================================

def run_bot():

    log("=" * 80)

    log(
        "🚀 CRON RUN STARTED"
    )

    log(
        f"⏰ IST Time: "
        f"{datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}"
    )

    log("=" * 80)

    if not is_market_open():

        log("⏰ Market closed")

        return

    log(
        "📊 STARTING PARALLEL FETCH"
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

    log(
        f"📦 FETCH FINISHED | "
        f"Results={len(results)}"
    )

    valid = 0

    for stock in results:

        if not stock:
            continue

        valid += 1

        process_stock(
            stock["symbol"],
            stock
        )

    log(
        f"✅ VALID STOCKS: "
        f"{valid}"
    )

    log(
        "✅ CRON RUN FINISHED"
    )

# =========================================================
# ENTRY
# =========================================================

if __name__ == "__main__":

    try:

        log(
            f"BOT_TOKEN EXISTS="
            f"{bool(BOT_TOKEN)}"
        )

        log(
            f"CHAT_ID EXISTS="
            f"{bool(CHAT_ID)}"
        )

        if not BOT_TOKEN or not CHAT_ID:

            log(
                "❌ ENV VARIABLES MISSING"
            )

            raise SystemExit(1)

        run_bot()

    except Exception:

        traceback.print_exc()

        log(
            "❌ MAIN CRITICAL ERROR"
        )
