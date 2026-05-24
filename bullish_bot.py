import os
import time
import threading
import requests
import pytz

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

CHAT_ID = os.getenv("CHAT_ID")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive"
}

# =========================================================
# GLOBALS
# =========================================================

seen_alerts = set()

seen_alerts_lock = threading.Lock()

latest_market_data = {}

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
        "UNIONBANK"
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
        "KPITTECH"
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
        "IREDA"
    ],

    "INFRA": [
        "IRB",
        "LT",
        "RVNL",
        "NBCC",
        "KEC",
        "PNCINFRA",
        "RAILTEL",
        "RITES",
        "IRCON"
    ],

    "ENERGY": [
        "ONGC",
        "RELIANCE",
        "IOC",
        "BPCL",
        "HINDPETRO",
        "OIL",
        "GAIL",
        "IGL"
    ],

    "AUTO": [
        "TATAMOTORS",
        "MARUTI",
        "M&M",
        "HEROMOTOCO",
        "BAJAJ-AUTO",
        "EICHERMOT",
        "TVSMOTOR",
        "ASHOKLEY"
    ],

    "METAL": [
        "TATASTEEL",
        "JSWSTEEL",
        "HINDALCO",
        "SAIL",
        "JINDALSTEL",
        "VEDL",
        "NMDC"
    ],

    "PHARMA": [
        "SUNPHARMA",
        "CIPLA",
        "DIVISLAB",
        "DRREDDY",
        "LUPIN",
        "AUROPHARMA"
    ],

    "FMCG": [
        "ITC",
        "HINDUNILVR",
        "NESTLEIND",
        "BRITANNIA",
        "DABUR",
        "TATACONSUM"
    ],

    "FINANCE": [
        "BAJFINANCE",
        "JIOFIN",
        "CHOLAFIN",
        "SHRIRAMFIN",
        "SBICARD"
    ],

    "DEFENCE": [
        "BEL",
        "HAL",
        "BDL",
        "MAZDOCK",
        "COCHINSHIP",
        "BEML"
    ],

    "REAL_ESTATE": [
        "DLF",
        "GODREJPROP",
        "OBEROIRLTY",
        "PRESTIGE"
    ],

    "RETAIL": [
        "DMART",
        "TRENT",
        "ABFRL"
    ]
}

# =========================================================
# ALL F&O SYMBOLS
# =========================================================

ALL_FNO_SYMBOLS = sorted(list({

    symbol

    for symbols in FNO_SECTORS.values()

    for symbol in symbols

}))

# =========================================================
# SYMBOL TO SECTOR
# =========================================================

SYMBOL_TO_SECTOR = {

    symbol: sector

    for sector, symbols in FNO_SECTORS.items()

    for symbol in symbols
}

# =========================================================
# SAFE FUNCTIONS
# =========================================================

def safe_float(v):

    try:
        return float(
            str(v).replace(",", "")
        )

    except:
        return 0.0


def safe_int(v):

    try:
        return int(
            float(
                str(v).replace(",", "")
            )
        )

    except:
        return 0

# =========================================================
# TIME
# =========================================================

def ist_now():

    tz = pytz.timezone(
        "Asia/Kolkata"
    )

    return datetime.now(tz)

# =========================================================
# MARKET HOURS
# =========================================================

def is_market_open():

    now = ist_now()

    # Saturday / Sunday

    if now.weekday() >= 5:
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

    return (
        market_open
        <= now
        <= market_close
    )

# =========================================================
# 5 MIN BUCKET
# =========================================================

def get_candle_bucket(
    dt,
    minutes=5
):

    bucket = (
        dt.minute // minutes
    ) * minutes

    return (
        dt.strftime(
            "%Y-%m-%d %H:"
        )
        + f"{bucket:02d}"
    )

# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(msg):

    try:

        if not BOT_TOKEN or not CHAT_ID:
            print(
                "BOT_TOKEN / CHAT_ID missing"
            )
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

        print(
            "Telegram Error:",
            e
        )

# =========================================================
# SESSION MANAGEMENT
# =========================================================

def get_session():

    now = time.time()

    if (

        not hasattr(
            thread_local,
            "session"
        )

        or

        now - getattr(
            thread_local,
            "session_created",
            0
        ) > 3600
    ):

        s = requests.Session()

        s.get(
            "https://www.nseindia.com",
            headers=HEADERS,
            timeout=15
        )

        thread_local.session = s

        thread_local.session_created = now

    return thread_local.session

# =========================================================
# NSE GET
# =========================================================

def nse_get(
    url,
    retries=2
):

    for attempt in range(retries):

        try:

            session = get_session()

            r = session.get(
                url,
                headers=HEADERS,
                timeout=20
            )

            if r.status_code == 200:

                return r.json()

            if r.status_code in (
                401,
                403
            ):

                thread_local.session_created = 0

            if r.status_code == 429:

                time.sleep(2)

        except Exception as e:

            print(
                "NSE ERROR:",
                e
            )

        time.sleep(1)

    return {}

# =========================================================
# NIFTY TREND
# =========================================================

def get_market_trend():

    try:

        url = (
            "https://www.nseindia.com/api/"
            "equity-stockIndices?"
            "index=NIFTY%2050"
        )

        data = nse_get(url)

        if not data:
            return 0

        items = data.get(
            "data",
            []
        )

        if not items:
            return 0

        nifty = items[0]

        return safe_float(
            nifty.get("pChange")
        )

    except:
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

        # =====================================================
        # EQUITY
        # =====================================================

        eq_url = (
            "https://www.nseindia.com/api/"
            f"quote-equity?symbol={symbol}"
        )

        eq_data = nse_get(eq_url)

        if not eq_data:
            return None

        p = eq_data.get(
            "priceInfo",
            {}
        )

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

        # =====================================================
        # PRICE %
        # =====================================================

        price_pct = (
            (
                price - prev_close
            )
            / prev_close
        ) * 100

        result["price_pct"] = price_pct

        # =====================================================
        # EARLY FILTER
        # =====================================================

        if price_pct < 1:
            return result

        if price_pct > 5:
            return result

        # =====================================================
        # FUTURES OI
        # =====================================================

        try:

            oi_url = (
                "https://www.nseindia.com/api/"
                f"quote-derivative?"
                f"symbol={symbol}"
            )

            oi_data = nse_get(
                oi_url
            )

            stocks = oi_data.get(
                "stocks",
                []
            )

            if stocks:

                md = stocks[0].get(
                    "metadata",
                    {}
                )

                oi_change_pct = safe_float(
                    md.get(
                        "pChangeinOpenInterest"
                    )
                )

                result["oi_change_pct"] = (
                    oi_change_pct
                )

                result["oi_strength"] = (
                    oi_change_pct > 5
                )

        except:
            pass

        # =====================================================
        # OPTION CHAIN
        # =====================================================

        try:

            option_url = (
                "https://www.nseindia.com/api/"
                f"option-chain-equities?"
                f"symbol={symbol}"
            )

            option_data = nse_get(
                option_url
            )

            records = option_data.get(
                "records",
                {}
            )

            rows = records.get(
                "data",
                []
            )

            total = 0

            atm_rows = [

                item

                for item in rows

                if abs(

                    safe_float(
                        item.get(
                            "strikePrice",
                            0
                        )
                    )

                    - price

                ) < price * 0.03
            ]

            for item in atm_rows[:5]:

                if not isinstance(
                    item,
                    dict
                ):
                    continue

                pe = item.get("PE")

                if not pe:
                    continue

                total += safe_int(
                    pe.get(
                        "changeinOpenInterest"
                    )
                )

            result["pe_oi_change"] = total

            result["put_writing"] = (
                total > 0
            )

        except:
            pass

        return result

    except Exception as e:

        print(
            symbol,
            "FETCH ERROR:",
            e
        )

        return None

# =========================================================
# SECTOR STRENGTH
# =========================================================

def get_sector_strength(
    symbol,
    market_data
):

    sector_name = "UNKNOWN"

    sector_score = 0

    sector = SYMBOL_TO_SECTOR.get(
        symbol
    )

    if not sector:

        return (
            sector_name,
            sector_score
        )

    stocks = FNO_SECTORS.get(
        sector,
        []
    )

    moves = []

    for s in stocks:

        if s not in market_data:
            continue

        d = market_data[s]

        pc = d.get(
            "prev_close",
            0
        )

        if pc <= 0:
            continue

        move = (
            (
                d.get("price", 0)
                - pc
            )
            / pc
        ) * 100

        moves.append(move)

    if not moves:

        return (
            sector_name,
            sector_score
        )

    avg_move = (
        sum(moves)
        / len(moves)
    )

    if avg_move > 2:
        sector_score = 15

    elif avg_move > 1:
        sector_score = 10

    elif avg_move > 0.5:
        sector_score = 5

    sector_name = sector

    return (
        sector_name,
        sector_score
    )

# =========================================================
# BULLISH ENGINE
# =========================================================

def process_bullish_setup(
    symbol,
    stock,
    market_trend,
    market_data
):

    try:

        price = stock.get(
            "price",
            0
        )

        prev_close = stock.get(
            "prev_close",
            0
        )

        vwap = stock.get(
            "vwap",
            0
        )

        price_pct = stock.get(
            "price_pct",
            0
        )

        oi_change_pct = stock.get(
            "oi_change_pct",
            0
        )

        oi_strength = stock.get(
            "oi_strength",
            False
        )

        put_writing = stock.get(
            "put_writing",
            False
        )

        pe_oi_change = stock.get(
            "pe_oi_change",
            0
        )

        if prev_close <= 0:
            return

        # =====================================================
        # FILTER
        # =====================================================

        if price_pct < 1:
            return

        if price_pct > 5:
            return

        # =====================================================
        # VWAP
        # =====================================================

        above_vwap = (
            vwap > 0
            and
            price > vwap
        )

        # =====================================================
        # SECTOR
        # =====================================================

        sector_name, sector_score = (
            get_sector_strength(
                symbol,
                market_data
            )
        )

        # =====================================================
        # SCORE
        # =====================================================

        score = 0

        if price_pct >= 3:
            score += 25

        elif price_pct >= 2:
            score += 20

        elif price_pct >= 1:
            score += 15

        if above_vwap:
            score += 20

        if oi_strength:
            score += 20

        if put_writing:
            score += 15

        score += sector_score

        if market_trend > 1:
            score += 15

        elif market_trend > 0.5:
            score += 10

        score = min(score, 100)

        # =====================================================
        # MIN SCORE
        # =====================================================

        if score < 70:
            return

        # =====================================================
        # DUPLICATE FILTER
        # =====================================================

        key = (
            f"{symbol}-"
            f"{get_candle_bucket(ist_now())}"
        )

        with seen_alerts_lock:

            if key in seen_alerts:
                return

            seen_alerts.add(key)

        # =====================================================
        # ICON
        # =====================================================

        def icon(v):

            return (
                "✅"
                if v
                else
                "❌"
            )

        # =====================================================
        # INTERPRETATION
        # =====================================================

        interpretation = (

            "🟢 Strong institutional bullish setup"

            if score >= 85

            else

            "🟡 Moderate bullish setup"
        )

        # =====================================================
        # MESSAGE
        # =====================================================

        msg = (

            f"🔥 <b>BULLISH SETUP</b>\n\n"

            f"<b>Stock:</b> "
            f"{symbol}\n"

            f"<b>Price:</b> "
            f"₹{price:,.2f}\n"

            f"<b>Change:</b> "
            f"{price_pct:+.2f}%\n\n"

            f"<b>MARKET</b>\n"

            f"📈 NIFTY Trend: "
            f"{market_trend:+.2f}%\n"

            f"🏭 Sector: "
            f"{sector_name}\n\n"

            f"<b>SIGNALS</b>\n"

            f"{icon(above_vwap)} Above VWAP\n"

            f"{icon(oi_strength)} Futures OI Buildup\n"

            f"{icon(put_writing)} PE Writing\n\n"

            f"<b>DETAILS</b>\n"

            f"VWAP: "
            f"₹{vwap:,.2f}\n"

            f"OI Change: "
            f"{oi_change_pct:+.2f}%\n"

            f"PE OI Change: "
            f"{pe_oi_change:,}\n"

            f"Sector Score: "
            f"{sector_score}\n\n"

            f"<b>CONFIDENCE</b>\n"

            f"🎯 {score}/100\n\n"

            f"{interpretation}"
        )

        send_telegram(msg)

    except Exception as e:

        print(
            symbol,
            "PROCESS ERROR:",
            e
        )

# =========================================================
# MAIN LOOP
# =========================================================

def run_bot():

    global latest_market_data

    last_cleared = None

    while True:

        cycle_start = time.time()

        today = ist_now().date()

        if last_cleared != today:

            with seen_alerts_lock:

                seen_alerts.clear()

            last_cleared = today

            print(
                "Cleared old alerts"
            )

        try:

            # =================================================
            # MARKET HOURS
            # =================================================

            if not is_market_open():

                print(
                    "Market closed..."
                )

                time.sleep(60)

                continue

            print(
                "Checking bullish setups..."
            )

            # =================================================
            # MARKET TREND
            # =================================================

            market_trend = (
                get_market_trend()
            )

            # =================================================
            # FETCH DATA
            # =================================================

            all_data = {}

            with ThreadPoolExecutor(
                max_workers=15
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

                all_data[
                    r["symbol"]
                ] = r

            latest_market_data = all_data

            snapshot = dict(all_data)

            # =================================================
            # PROCESS ALERTS
            # =================================================

            for symbol, stock in snapshot.items():

                process_bullish_setup(
                    symbol,
                    stock,
                    market_trend,
                    snapshot
                )

            print(
                "Cycle complete:",
                datetime.now()
            )

        except Exception as e:

            print(
                "MAIN LOOP ERROR:",
                e
            )

        # =====================================================
        # FIXED 5 MIN CADENCE
        # =====================================================

        elapsed = (
            time.time()
            - cycle_start
        )

        sleep_time = max(
            0,
            300 - elapsed
        )

        print(
            f"Sleeping {sleep_time:.2f}s"
        )

        time.sleep(sleep_time)

# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    send_telegram(
        "🚀 Bullish Institutional Bot Started"
    )

    run_bot()
