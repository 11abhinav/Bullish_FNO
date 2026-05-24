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

MAX_WORKERS = 3

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
        "SBIN", "HDFCBANK", "ICICIBANK", "AXISBANK"
    ],
    "IT": [
        "INFY", "TCS", "WIPRO", "TECHM"
    ],
    "POWER": [
        "PFC", "RECLTD", "SUZLON", "NTPC"
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

    if now.weekday() >= 5:

        return False

    if now.date() in NSE_HOLIDAYS_2026:

        return False

    market_open = now.replace(
        hour=8,
        minute=0,
        second=0,
        microsecond=0
    )

    market_close = now.replace(
        hour=16,
        minute=0,
        second=0,
        microsecond=0
    )

    return market_open <= now <= market_close

# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(msg):

    try:

        if not BOT_TOKEN or not CHAT_ID:

            print("BOT_TOKEN / CHAT_ID missing")

            return

        url = (
            f"https://api.telegram.org/"
            f"bot{BOT_TOKEN}/sendMessage"
        )

        payload = {
            "chat_id": CHAT_ID,
            "text": msg,
            "parse_mode": "HTML"
        }

        requests.post(
            url,
            json=payload,
            timeout=20
        )

    except Exception as e:

        print("Telegram Error:", e)

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

        s = requests.Session()

        s.headers.update(HEADERS)

        try:

            s.get(
                "https://www.nseindia.com/api/marketStatus",
                timeout=10
            )

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

            r = session.get(
                url,
                timeout=20
            )

            if r.status_code == 200:

                return r.json()

            if r.status_code in (401, 403):

                thread_local.session_created = 0

            if r.status_code == 429:

                time.sleep(2)

        except Exception as e:

            print("NSE ERROR:", e)

        if attempt < retries - 1:

            time.sleep(1)

    return {}

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
        }

        eq_url = (
            f"https://www.nseindia.com/api/"
            f"quote-equity?symbol={symbol}"
        )

        eq_data = nse_get(eq_url)

        if not eq_data:

            return None

        p = eq_data.get("priceInfo", {})

        result["price"] = safe_float(
            p.get("lastPrice")
        )

        result["prev_close"] = safe_float(
            p.get("previousClose")
        )

        result["vwap"] = safe_float(
            p.get("vwap")
        )

        price = result["price"]

        prev_close = result["prev_close"]

        if prev_close <= 0:

            return result

        price_pct = (
            (
                price - prev_close
            ) / prev_close
        ) * 100

        result["price_pct"] = price_pct

        time.sleep(0.5)

        return result

    except Exception as e:

        print(symbol, "FETCH ERROR:", e)

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

            return None

        key = (
            f"{symbol}-"
            f"{ist_now().strftime('%Y-%m-%d-%H-%M')}"
        )

        with seen_alerts_lock:

            if key in seen_alerts:

                return None

            seen_alerts.add(key)

        msg = (
            f"🔥 BULLISH SETUP\n\n"
            f"Stock: {symbol}\n"
            f"Move: {price_pct:+.2f}%"
        )

        return msg

    except Exception as e:

        print(symbol, "PROCESS ERROR:", e)

        return None

# =========================================================
# MAIN BOT
# =========================================================

def run_bot():

    if not is_market_open():

        print("⏰ Market closed")

        return

    print("🚀 Checking bullish setups...")

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

    for r in results:

        if not r:

            continue

        all_data[r["symbol"]] = r

    alerts_sent = 0

    for symbol, stock in all_data.items():

        msg = process_bullish_setup(
            symbol,
            stock
        )

        if msg:

            send_telegram(msg)

            alerts_sent += 1

            print(f"✅ ALERT: {symbol}")

    print(
        f"✅ Scan completed | "
        f"Alerts: {alerts_sent}"
    )

# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    if not BOT_TOKEN or not CHAT_ID:

        print("FATAL: BOT_TOKEN / CHAT_ID not set")

        raise SystemExit(1)

    print(
        f"🚀 Bullish Institutional Bot Started | "
        f"{datetime.now()}"
    )

    run_bot()
