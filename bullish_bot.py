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

# FORCE IMMEDIATE FLUSH
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

        log(
            "❌ Telegram Error"
        )

# =========================================================
# FETCH STOCK
# =========================================================

def fetch_stock(symbol):

    try:

        log(
            f"🚀 START FETCH: {symbol}"
        )

        time.sleep(1)

        log(
            f"🌐 CALLING YFINANCE: "
            f"{symbol}"
        )

        start_time = time.time()

        df = yf.download(

            f"{symbol}.NS",

            period="1d",

            interval="5m",

            progress=False,

            auto_adjust=True,

            threads=False
        )

        elapsed = round(
            time.time() - start_time,
            2
        )

        log(
            f"📦 YF DONE: {symbol} | "
            f"Rows={len(df)} | "
            f"Time={elapsed}s"
        )

        if df.empty:

            log(
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

            log(
                f"🛠️ Fixing MultiIndex: "
                f"{symbol}"
            )

            df.columns = (
                df.columns
                .get_level_values(0)
            )

        log(
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

                log(
                    f"❌ Missing column "
                    f"{col_name}: {symbol}"
                )

                return None

            if isinstance(

                df[col_name],

                pd.DataFrame
            ):

                log(
                    f"⚠️ DataFrame column detected "
                    f"{col_name}: {symbol}"
                )

                df[col_name] = (
                    df[col_name]
                    .iloc[:, 0]
                )

        df.dropna(inplace=True)

        log(
            f"📦 Clean rows: "
            f"{len(df)} | {symbol}"
        )

        if len(df) < 5:

            log(
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

            log(
                f"❌ Invalid prev close: "
                f"{symbol}"
            )

            return None

        price_pct = (
            (
                last_price - prev_close
            ) / prev_close
        ) * 100

        log(
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

        traceback.print_exc()

        log(
            f"❌ FETCH ERROR: {symbol}"
        )

        return None

# =========================================================
# PROCESS STOCK
# =========================================================

def process_bullish_setup(symbol, stock):

    try:

        log(
            f"🔎 Processing: {symbol}"
        )

        price_pct = stock.get(
            "price_pct",
            0
        )

        log(
            f"📈 {symbol} Move="
            f"{round(price_pct,2)}%"
        )

        if price_pct < PRICE_MOVE_THRESHOLD:

            log(
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

        log(
            f"🚀 ALERT TRIGGERED: "
            f"{symbol}"
        )

        return msg

    except Exception:

        traceback.print_exc()

        log(
            f"❌ PROCESS ERROR: {symbol}"
        )

        return None

# =========================================================
# MAIN BOT
# =========================================================

def run_bot():

    log("=" * 80)

    log(
        "🚀 NEW SCAN STARTED"
    )

    log(
        f"⏰ IST Time: "
        f"{datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}"
    )

    log("=" * 80)

    if not is_market_open():

        log(
            "⏰ Market closed"
        )

        return

    all_data = {}

    log(
        "📊 STARTING PARALLEL FETCH"
    )

    start_scan = time.time()

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        results = list(

            executor.map(
                fetch_stock,
                ALL_FNO_SYMBOLS
            )
        )

    elapsed = round(
        time.time() - start_scan,
        2
    )

    log(
        f"📦 FETCH FINISHED | "
        f"Results={len(results)} | "
        f"Time={elapsed}s"
    )

    for r in results:

        if not r:

            log(
                "⚠️ Empty stock result skipped"
            )

            continue

        all_data[r["symbol"]] = r

    log(
        f"✅ Valid stocks: "
        f"{len(all_data)}"
    )

    alerts_sent = 0

    for idx, (symbol, stock) in enumerate(

        all_data.items(),

        start=1
    ):

        log(
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

            log(
                f"✅ ALERT SENT: "
                f"{symbol}"
            )

    log("=" * 80)

    log(
        f"✅ SCAN FINISHED | "
        f"Alerts={alerts_sent}"
    )

    log("=" * 80)

# =========================================================
# CONTINUOUS LOOP
# =========================================================

if __name__ == "__main__":

    try:

        if not BOT_TOKEN or not CHAT_ID:

            log(
                "❌ FATAL: BOT_TOKEN / CHAT_ID missing"
            )

            raise SystemExit(1)

        log("=" * 80)

        log(
            "🚀 Momentum Bot Started"
        )

        log(
            f"⏰ IST Time: "
            f"{datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}"
        )

        log("=" * 80)

        while True:

            try:

                log("=" * 80)

                log(
                    f"🔄 HEARTBEAT | "
                    f"{datetime.now(IST).strftime('%H:%M:%S')}"
                )

                run_bot()

            except Exception:

                traceback.print_exc()

                log(
                    "❌ LOOP ERROR"
                )

            log(
                f"😴 Sleeping "
                f"{CHECK_INTERVAL} sec..."
            )

            time.sleep(CHECK_INTERVAL)

    except Exception:

        traceback.print_exc()

        log(
            "❌ MAIN CRITICAL ERROR"
        )
