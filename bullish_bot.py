import os
import time
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
    "User-Agent": "Mozilla/5.0"
}

# =========================================================
# GLOBALS
# =========================================================

seen_alerts = set()

latest_market_data = {}

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

ALL_FNO_SYMBOLS = list({

    symbol

    for symbols in FNO_SECTORS.values()

    for symbol in symbols

})

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
        return float(str(v).replace(",", ""))

    except:
        return 0


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
# TELEGRAM
# =========================================================

def send_telegram(msg):

    try:

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
            timeout=15
        )

    except Exception as e:

        print("Telegram Error", e)


# =========================================================
# NSE SESSION
# =========================================================

session = requests.Session()

# =========================================================
# NSE GET
# =========================================================

def nse_get(url):

    try:

        session.get(
            "https://www.nseindia.com",
            headers=HEADERS,
            timeout=10
        )

        r = session.get(
            url,
            headers=HEADERS,
            timeout=20
        )

        if r.status_code != 200:
            return {}

        return r.json()

    except Exception as e:

        print("NSE ERROR", e)

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

        items = data.get("data", [])

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

        url = (
            "https://www.nseindia.com/api/"
            f"quote-equity?symbol={symbol}"
        )

        data = nse_get(url)

        if not data:
            return None

        p = data.get(
            "priceInfo",
            {}
        )

        return {

            "symbol": symbol,

            "price": safe_float(
                p.get("lastPrice")
            ),

            "prev_close": safe_float(
                p.get("previousClose")
            ),

            "vwap": safe_float(
                p.get("vwap")
            )
        }

    except Exception as e:

        print(symbol, e)

        return None

# =========================================================
# SECTOR STRENGTH
# =========================================================

def get_sector_strength(symbol):

    sector_name = "UNKNOWN"

    sector_score = 0

    sector = SYMBOL_TO_SECTOR.get(symbol)

    if not sector:
        return sector_name, sector_score

    stocks = FNO_SECTORS.get(sector, [])

    moves = []

    for s in stocks:

        if s not in latest_market_data:
            continue

        d = latest_market_data[s]

        pc = d["prev_close"]

        if pc <= 0:
            continue

        move = (
            (
                d["price"] - pc
            )
            / pc
        ) * 100

        moves.append(move)

    if not moves:
        return sector_name, sector_score

    avg_move = (
        sum(moves)
        / len(moves)
    )

    if avg_move > 0.5:
        sector_score = 5

    if avg_move > 1:
        sector_score = 10

    if avg_move > 2:
        sector_score = 15

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
    market_trend
):

    try:

        price = stock["price"]

        prev_close = stock["prev_close"]

        vwap = stock["vwap"]

        if prev_close <= 0:
            return

        # =====================================================
        # PRICE STRENGTH
        # =====================================================

        price_pct = (
            (
                price - prev_close
            )
            / prev_close
        ) * 100

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
        # FUTURES OI
        # =====================================================

        oi_change_pct = 0

        oi_strength = False

        try:

            url = (
                "https://www.nseindia.com/api/"
                f"quote-derivative?"
                f"symbol={symbol}"
            )

            data = nse_get(url)

            stocks = data.get(
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

                oi_strength = (
                    oi_change_pct > 5
                )

        except:
            pass

        # =====================================================
        # PE WRITING
        # =====================================================

        put_writing = False

        pe_oi_change = 0

        try:

            url = (
                "https://www.nseindia.com/api/"
                f"option-chain-equities?"
                f"symbol={symbol}"
            )

            data = nse_get(url)

            records = data.get(
                "records",
                {}
            )

            rows = records.get(
                "data",
                []
            )

            total = 0

            for item in rows[:5]:

                if not isinstance(item, dict):
                    continue

                pe = item.get("PE")

                if not pe:
                    continue

                total += safe_int(
                    pe.get(
                        "changeinOpenInterest"
                    )
                )

            pe_oi_change = total

            put_writing = (
                total > 0
            )

        except:
            pass

        # =====================================================
        # SECTOR
        # =====================================================

        sector_name, sector_score = (
            get_sector_strength(symbol)
        )

        # =====================================================
        # SCORE
        # =====================================================

        score = 20

        if above_vwap:
            score += 20

        if oi_strength:
            score += 20

        if put_writing:
            score += 15

        score += sector_score

        # =====================================================
        # NIFTY BOOST
        # =====================================================

        if market_trend > 0.5:
            score += 10

        if market_trend > 1:
            score += 15

        # =====================================================
        # ALERT FILTER
        # =====================================================

        if score < 70:
            return

        candle = ist_now().strftime(
            "%Y-%m-%d %H:%M"
        )

        key = (
            f"{symbol}-{candle}"
        )

        if key in seen_alerts:
            return

        seen_alerts.add(key)

        # =====================================================
        # ICONS
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
        # TELEGRAM MESSAGE
        # =====================================================

        msg = (

            f"🔥 <b>BULLISH SETUP</b>\n\n"

            f"<b>Stock:</b> {symbol}\n"

            f"<b>Price:</b> ₹{price:,.2f}\n"

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

            f"VWAP: ₹{vwap:,.2f}\n"

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

        print(symbol, e)

# =========================================================
# MAIN LOOP
# =========================================================

def run_bot():

    global latest_market_data

    while True:

        try:

            print(
                "Checking bullish setups..."
            )

            market_trend = (
                get_market_trend()
            )

            all_data = {}

            with ThreadPoolExecutor(
                max_workers=5
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

            for symbol, stock in all_data.items():

                process_bullish_setup(
                    symbol,
                    stock,
                    market_trend
                )

            print(
                "Cycle complete",
                datetime.now()
            )

        except Exception as e:

            print(
                "MAIN LOOP ERROR",
                e
            )

        time.sleep(300)

# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    send_telegram(
        "🚀 Bullish Institutional Bot Started"
    )

    run_bot()
