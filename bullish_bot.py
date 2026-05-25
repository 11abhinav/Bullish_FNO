import os
import time
import threading
import requests
import pytz
import logging
import pandas as pd
import yfinance as yf

from zoneinfo import ZoneInfo
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor

# =========================================================
# LOGGER
# =========================================================

logging.basicConfig(

    level=logging.INFO,

    format="%(asctime)s | %(levelname)s | %(message)s",

    datefmt="%Y-%m-%d %H:%M:%S",

    force=True
)

logger = logging.getLogger()

logger.setLevel(logging.INFO)

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

CHECK_INTERVAL = 300

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

logger.info(
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

    logger.info(
        f"⏰ Market Check IST: "
        f"{now.strftime('%Y-%m-%d %H:%M:%S')}"
    )

    if now.weekday() >= 5:

        logger.info("❌ Weekend detected")

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

        logger.info(
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

        logger.info(
            f"📨 Telegram Status="
            f"{r.status_code}"
        )

    except Exception:

        logger.exception(
            "❌ Telegram Error"
        )

# =========================================================
# FETCH STOCK
# =========================================================

def fetch_stock(symbol):

    try:

        logger.info(
            f"🔍 Fetching: {symbol}"
        )

        time.sleep(1)

        df = yf.download(

            f"{symbol}.NS",

            period="1d",

            interval="5m",

            progress=False,

            auto_adjust=True,

            threads=False
        )

        logger.info(
            f"📦 Raw rows received: "
            f"{len(df)} | {symbol}"
        )

        if df.empty:

            logger.warning(
                f"❌ No data: {symbol}"
            )

            return None

        # =================================================
        # FIX MULTI INDEX
        # =================================================

        if isinstance(
            df.columns,
            pd.MultiIndex
        ):

            logger.info(
                f"🛠️ Fixing MultiIndex: "
                f"{symbol}"
            )

            df.columns = (
                df.columns
                .get_level_values(0)
            )

        logger.info(
            f"📊 Columns: "
            f"{list(df.columns)}"
        )

        required_cols = [

            "Open",
            "High",
            "Low",
            "Close",
            "Volume"
        ]

        for col_name in required_cols:

            if col_name not in df.columns:

                logger.warning(
                    f"❌ Missing column "
                    f"{col_name}: {symbol}"
                )

                return None

            if isinstance(

                df[col_name],

                pd.DataFrame
            ):

                logger.warning(
                    f"⚠️ DataFrame column detected "
                    f"{col_name}: {symbol}"
                )

                df[col_name] = (
                    df[col_name]
                    .iloc[:, 0]
                )

        df.dropna(inplace=True)

        logger.info(
            f"📦 Clean rows: "
            f"{len(df)} | {symbol}"
        )

        if len(df) < 5:

            logger.warning(
                f"❌ Not enough candles: "
                f"{symbol}"
            )

            return None

        latest = df.iloc[-1]

        prev_close = safe_float(
            df["Close"].iloc[-2]
        )

        last_price = safe_float(
            latest["Close"]
        )

        volume = int(
            latest["Volume"]
        )

        if prev_close <= 0:

            logger.warning(
                f"❌ Invalid prev close: "
                f"{symbol}"
            )

            return None

        price_pct = (
            (
                last_price - prev_close
            ) / prev_close
        ) * 100

        logger.info(
            f"✅ {symbol} | "
            f"Price={round(last_price,2)} | "
            f"Move={round(price_pct,2)}%"
        )

        return {

            "symbol": symbol,

            "price": last_price,

            "prev_close": prev_close,

            "price_pct": price_pct,

            "volume": volume
        }

    except Exception:

        logger.exception(
            f"❌ FETCH ERROR: {symbol}"
        )

        return None

# =========================================================
# PROCESS STOCK
# =========================================================

def process_bullish_setup(symbol, stock):

    try:

        logger.info(
            f"🔎 Processing: {symbol}"
        )

        price_pct = stock.get(
            "price_pct",
            0
        )

        logger.info(
            f"📈 {symbol} Move="
            f"{round(price_pct,2)}%"
        )

        if price_pct < PRICE_MOVE_THRESHOLD:

            logger.info(
                f"❌ Rejected: {symbol} | "
                f"Move below threshold"
            )

            return None

        msg = (
            f"🔥 BULLISH SETUP\n\n"
            f"Stock: {symbol}\n"
            f"Move: {price_pct:+.2f}%\n"
            f"Price: ₹{stock['price']}"
        )

        logger.info(
            f"🚀 ALERT TRIGGERED: "
            f"{symbol}"
        )

        return msg

    except Exception:

        logger.exception(
            f"❌ PROCESS ERROR: {symbol}"
        )

        return None

# =========================================================
# MAIN BOT
# =========================================================

def run_bot():

    logger.info("=" * 80)

    logger.info(
        "🚀 NEW SCAN STARTED"
    )

    logger.info(
        f"⏰ IST Time: "
        f"{datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}"
    )

    logger.info("=" * 80)

    if not is_market_open():

        logger.info(
            "⏰ Market closed"
        )

        return

    all_data = {}

    logger.info(
        "📊 Starting stock fetch..."
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
        f"📦 Total results received: "
        f"{len(results)}"
    )

    for r in results:

        if not r:

            logger.warning(
                "⚠️ Empty stock result skipped"
            )

            continue

        all_data[r["symbol"]] = r

    logger.info(
        f"✅ Valid stocks: "
        f"{len(all_data)}"
    )

    alerts_sent = 0

    for idx, (symbol, stock) in enumerate(

        all_data.items(),

        start=1
    ):

        logger.info(
            f"🔄 Checking "
            f"{idx}/{len(all_data)}: "
            f"{symbol}"
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
        f"✅ SCAN FINISHED | "
        f"Alerts={alerts_sent}"
    )

    logger.info("=" * 80)

# =========================================================
# CONTINUOUS LOOP
# =========================================================

if __name__ == "__main__":

    try:

        if not BOT_TOKEN or not CHAT_ID:

            logger.error(
                "❌ FATAL: BOT_TOKEN / CHAT_ID missing"
            )

            raise SystemExit(1)

        logger.info("=" * 80)

        logger.info(
            "🚀 Momentum Bot Started"
        )

        logger.info(
            f"⏰ IST Time: "
            f"{datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}"
        )

        logger.info("=" * 80)

        while True:

            try:

                run_bot()

            except Exception:

                logger.exception(
                    "❌ LOOP ERROR"
                )

            logger.info(
                f"😴 Sleeping "
                f"{CHECK_INTERVAL} sec..."
            )

            time.sleep(CHECK_INTERVAL)

    except Exception:

        logger.exception(
            "❌ MAIN CRITICAL ERROR"
        )
