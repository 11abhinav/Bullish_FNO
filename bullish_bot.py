# =========================================================
# ADVANCED NSE MOMENTUM TELEGRAM BOT
# =========================================================
#
# WHAT THIS BOT DOES
# ---------------------------------------------------------
#
# ✅ Runs automatically via Railway CRON
# ✅ Scans NSE stocks every 5 minutes
# ✅ Uses Yahoo Finance (yfinance) for live data
# ✅ Detects bullish momentum stocks
# ✅ Sends Telegram alerts for strong movers
# ✅ Tracks intraday percentage moves
# ✅ Uses custom FNO + momentum watchlist
# ✅ Prevents Railway log flooding
# ✅ Handles yfinance rate limits safely
# ✅ Handles MultiIndex dataframe issues
# ✅ Railway-safe low logging version
#
# ALERT LOGIC
# ---------------------------------------------------------
#
# Sends alert when:
#
# Current move % > PRICE_MOVE_THRESHOLD
#
# Example:
#   Threshold = 0.8
#   Alert triggers at:
#       +0.8%
#       +1.5%
#       +3%
#
# CURRENT FLOW
# ---------------------------------------------------------
#
# Railway Cron Starts
#        ↓
# Check Market Hours
#        ↓
# Fetch Yahoo Finance Data
#        ↓
# Calculate Intraday Move %
#        ↓
# Filter Momentum Stocks
#        ↓
# Send Telegram Alerts
#        ↓
# Exit Cleanly
#
# =========================================================

import os
import sys
import time
import random
import logging
import traceback
import requests
import pandas as pd
import yfinance as yf

from datetime import (
    datetime,
    timedelta,
    timezone
)

from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed
)

# =========================================================
# LOGGER
# =========================================================

print("🚀 FILE STARTED", flush=True)

logging.basicConfig(

    level=logging.INFO,

    format="%(asctime)s | %(levelname)s | %(message)s",

    force=True,

    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger()

def log(message):

    print(message, flush=True)

    logger.info(message)

log("🚀 SCRIPT STARTED")

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

CHAT_ID = os.getenv("CHAT_ID")

PRICE_MOVE_THRESHOLD = 0.8

# IMPORTANT
# KEEP LOW TO AVOID YF RATE LIMIT

MAX_WORKERS = 1

IST = timezone(
    timedelta(hours=5, minutes=30)
)

ALERT_START = (9, 15)

ALERT_END = (15, 30)

# =========================================================
# YAHOO SYMBOL OVERRIDES
# =========================================================

YF_SYMBOL_MAP = {

    "TMCV": "TATAMOTORS.NS"
}

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
    "TMCV",
    "M&M",
    "EICHERMOT",
    "TVSMOTOR",
    "ASHOKLEY"
]

log(
    f"📊 Watchlist Loaded: "
    f"{len(ALL_FNO_SYMBOLS)}"
)

# =========================================================
# MARKET HOURS
# =========================================================

def is_market_open():

    now = datetime.now(IST)

    if now.weekday() >= 5:

        return False

    t = (now.hour, now.minute)

    return ALERT_START <= t < ALERT_END

# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(msg):

    try:

        url = (
            f"https://api.telegram.org/"
            f"bot{BOT_TOKEN}/sendMessage"
        )

        r = requests.post(

            url,

            data={

                "chat_id": CHAT_ID,

                "text": msg
            },

            timeout=20
        )

        log(
            f"📨 Telegram="
            f"{r.status_code}"
        )

    except Exception:

        traceback.print_exc()

# =========================================================
# FETCH STOCK
# =========================================================

def fetch_stock(symbol):

    try:

        # =============================================
        # RANDOM DELAY
        # FIXES YFINANCE RATE LIMIT
        # =============================================

        time.sleep(
            random.uniform(0.8, 2.0)
        )

        yf_symbol = YF_SYMBOL_MAP.get(
            symbol,
            f"{symbol}.NS"
        )

        df = yf.download(

            yf_symbol,

            period="1d",

            interval="5m",

            progress=False,

            auto_adjust=True,

            threads=False
        )

        if df.empty:

            return None

        # FIX MULTIINDEX
        if isinstance(
            df.columns,
            pd.MultiIndex
        ):

            df.columns = (
                df.columns
                .get_level_values(0)
            )

        latest = df.iloc[-1]

        prev_close = float(
            df["Close"].iloc[-2]
        )

        last_price = float(
            latest["Close"]
        )

        if prev_close <= 0:

            return None

        move_pct = (
            (
                last_price - prev_close
            ) / prev_close
        ) * 100

        return {

            "symbol": symbol,

            "price": last_price,

            "move_pct": move_pct
        }

    except Exception:

        err = str(traceback.format_exc())

        # SILENT YFINANCE RATE LIMIT HANDLING
        if "YFRateLimitError" in err:

            return None

        traceback.print_exc()

        return None

# =========================================================
# PROCESS STOCK
# =========================================================

def process_stock(symbol, stock):

    try:

        move_pct = stock["move_pct"]

        if move_pct < PRICE_MOVE_THRESHOLD:

            return

        log(
            f"🚨 ALERT | "
            f"{symbol} | "
            f"{move_pct:+.2f}%"
        )

        msg = (

            f"🔥 BULLISH SETUP\n\n"

            f"Stock: {symbol}\n"

            f"Move: {move_pct:+.2f}%\n"

            f"Price: ₹{stock['price']}"
        )

        send_telegram(msg)

    except Exception:

        traceback.print_exc()

# =========================================================
# MAIN BOT
# =========================================================

def run_bot():

    log("🚀 CRON RUN STARTED")

    if not is_market_open():

        log("⏰ Market closed")

        return

    log("📊 FETCH STARTED")

    valid = 0

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                fetch_stock,
                symbol
            ): symbol

            for symbol in ALL_FNO_SYMBOLS
        }

        for future in as_completed(futures):

            try:

                stock = future.result()

                if not stock:
                    continue

                valid += 1

                process_stock(
                    stock["symbol"],
                    stock
                )

            except Exception:

                traceback.print_exc()

    log(
        f"📦 FETCH DONE | "
        f"Valid={valid}"
    )

    log("✅ CRON RUN FINISHED")

# =========================================================
# ENTRY
# =========================================================

if __name__ == "__main__":

    try:

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
