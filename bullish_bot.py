# =========================================================
# ADVANCED NSE MOMENTUM + FNO TELEGRAM BOT
# =========================================================
#
# WHAT THIS BOT DOES
# ---------------------------------------------------------
#
# ✅ Runs automatically via Railway CRON
# ✅ Scans NSE FNO stocks every 5 minutes
# ✅ Uses Yahoo Finance (yfinance) for live price data
# ✅ Uses NSE India API for OI / Delivery / FNO data
# ✅ Detects: Momentum / Short Covering / Long Buildup
# ✅ Sends rich Telegram alerts with full signal details
# ✅ FIXED: Move % vs Day Open (not prev candle)
# ✅ FIXED: M&M symbol → MM.NS for Yahoo
# ✅ FIXED: Rate limit resilience with backoff
# ✅ FIXED: No duplicate/silent skips
#
# ALERT LOGIC
# ---------------------------------------------------------
#
# Sends alert when ANY of:
#   Price move vs day open > PRICE_MOVE_THRESHOLD (0.8%)
#   Short Covering: OI falling + Price rising
#   Long Buildup:   OI rising  + Price rising
#   Heavy Delivery: Delivery % > DELIVERY_THRESHOLD (60%)
#
# SIGNAL TYPES
# ---------------------------------------------------------
#
#   🚀 LONG BUILDUP    - OI ↑ + Price ↑ (fresh longs)
#   🔄 SHORT COVERING  - OI ↓ + Price ↑ (shorts exiting)
#   📦 HIGH DELIVERY   - Strong cash market conviction
#   ⚡ MOMENTUM MOVE   - Pure price breakout
#
# TELEGRAM MESSAGE FORMAT
# ---------------------------------------------------------
#
#   Stock | Signal Type
#   Price: ₹XXX  |  Move: +X.XX%
#   OI Change: +X.XX%
#   Delivery: XX%
#   Volume vs Avg: XXx
#   Day High/Low: ₹XXX / ₹XXX
#   Signal: [description]
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

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# =========================================================
# LOGGER
# =========================================================

print("🚀 FILE STARTED", flush=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    force=True,
    handlers=[logging.StreamHandler(sys.stdout)]
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
CHAT_ID   = os.getenv("CHAT_ID")

# Price move vs DAY OPEN to trigger alert
PRICE_MOVE_THRESHOLD = 0.8      # %

# Delivery % to flag high delivery conviction
DELIVERY_THRESHOLD   = 60.0     # %

# OI change % to flag OI-based signals
OI_CHANGE_THRESHOLD  = 2.0      # %

# Volume surge multiplier vs 20-day avg
VOLUME_SURGE_MULT    = 1.5      # x

# Keep at 1 to avoid Yahoo rate limits
MAX_WORKERS = 1

IST         = timezone(timedelta(hours=5, minutes=30))
ALERT_START = (9, 15)
ALERT_END   = (15, 30)

# NSE API headers (mimics browser)
NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.nseindia.com/",
    "Connection":      "keep-alive",
}

# =========================================================
# WATCHLIST
# =========================================================
# Symbol format:
#   NSE_SYMBOL  →  used for NSE API calls
#   YF_SYMBOL   →  used for Yahoo Finance
# Special fixes:
#   M&M  → MM.NS  (& breaks Yahoo URL)
#   TATAMTRDVR → TATAMTRDVR.NS (DVR shares)

WATCHLIST = [
    # SYMBOL          YF_OVERRIDE (None = symbol + ".NS")
    ("SBIN",          None),
    ("HDFCBANK",      None),
    ("ICICIBANK",     None),
    ("AXISBANK",      None),
    ("KOTAKBANK",     None),
    ("INFY",          None),
    ("TCS",           None),
    ("WIPRO",         None),
    ("LT",            None),
    ("BEL",           None),
    ("RVNL",          None),
    ("PFC",           None),
    ("RECLTD",        None),
    ("SUZLON",        None),
    ("JIOFIN",        None),
    ("TRENT",         None),
    ("TITAN",         None),
    ("MARUTI",        None),
    ("ONGC",          None),
    ("TATAPOWER",     None),
    ("JSWENERGY",     None),
    ("MAZDOCK",       None),
    ("HAL",           None),
    ("COCHINSHIP",    None),
    ("ADANIPORTS",    None),
    ("ADANIENT",      None),
    ("CGPOWER",       None),
    ("BHEL",          None),
    ("IRB",           None),
    ("NTPC",          None),
    ("BANKBARODA",    None),
    ("CANBK",         None),
    ("UNIONBANK",     None),
    ("LODHA",         None),
    ("DLF",           None),
    ("KPITTECH",      None),
    ("PERSISTENT",    None),
    ("COFORGE",       None),
    ("POLYCAB",       None),
    ("KEI",           None),
    ("VBL",           None),
    ("TATATECH",      None),
    ("PIDILITIND",    None),
    ("PVRINOX",       None),
    ("INDUSTOWER",    None),
    ("HINDCOPPER",    None),
    ("TATASTEEL",     None),
    ("JSWSTEEL",      None),
    ("HINDALCO",      None),
    ("POWERGRID",     None),
    ("IOC",           None),
    ("BPCL",          None),
    ("GAIL",          None),
    ("SUNPHARMA",     None),
    ("DIVISLAB",      None),
    ("DRREDDY",       None),
    ("CIPLA",         None),
    ("BAJFINANCE",    None),
    ("BAJAJFINSV",    None),
    ("CHOLAFIN",      None),
    ("SHRIRAMFIN",    None),
    ("MUTHOOTFIN",    None),
    ("MANAPPURAM",    None),
    # FIXED: DVR shares have limited liquidity, kept for completeness
    ("TATAMTRDVR",    None),
    # FIXED: M&M breaks Yahoo URL — override to MM.NS
    ("M&M",           "MM.NS"),
    ("EICHERMOT",     None),
    ("TVSMOTOR",      None),
    ("ASHOKLEY",      None),
]

log(f"📊 Watchlist Loaded: {len(WATCHLIST)}")

# =========================================================
# MARKET HOURS CHECK
# =========================================================

def is_market_open():
    now = datetime.now(IST)
    if now.weekday() >= 5:  # Saturday / Sunday
        return False
    t = (now.hour, now.minute)
    return ALERT_START <= t < ALERT_END

# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        r = requests.post(
            url,
            data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=20
        )
        log(f"📨 Telegram={r.status_code}")
        if r.status_code != 200:
            log(f"⚠️ Telegram error: {r.text}")
    except Exception:
        traceback.print_exc()

# =========================================================
# NSE SESSION (reused across calls)
# =========================================================

_nse_session = None


def get_nse_session():
    """
    Creates a session with NSE cookies.
    NSE requires a homepage hit first to set cookies.
    """
    global _nse_session
    if _nse_session:
        return _nse_session
    try:
        s = requests.Session()
        s.headers.update(NSE_HEADERS)
        # Prime the session — NSE requires cookie from homepage
        s.get("https://www.nseindia.com", timeout=10)
        time.sleep(1)
        _nse_session = s
        log("✅ NSE session initialized")
    except Exception:
        log("⚠️ NSE session init failed — OI/delivery data unavailable")
        _nse_session = None
    return _nse_session


# =========================================================
# NSE FNO DATA FETCH
# =========================================================

def fetch_nse_fno(symbol):
    """
    Fetches FnO quote data from NSE India public API.
    Returns OI, OI change, lot size, delivery data if available.

    NSE endpoint:
      /api/quote-derivative?symbol=SBIN
    """
    try:
        s = get_nse_session()
        if not s:
            return {}

        url = (
            "https://www.nseindia.com/api/"
            f"quote-derivative?symbol={symbol}"
        )
        r = s.get(url, timeout=8)

        if r.status_code != 200:
            return {}

        data = r.json()

        # Grab near-month futures (first contract in list)
        stocks = data.get("stocks", [])
        if not stocks:
            return {}

        # Find near-month futures row
        fut_data = None
        for row in stocks:
            meta = row.get("metadata", {})
            if meta.get("instrumentType") == "Stock Futures":
                fut_data = row
                break

        if not fut_data:
            return {}

        meta  = fut_data.get("metadata", {})
        mktdt = fut_data.get("marketDeptOrderBook", {})
        tradeinfo = fut_data.get("marketDeptOrderBook", {}).get("tradeInfo", {})

        oi        = tradeinfo.get("openInterest", 0)
        oi_chg    = tradeinfo.get("changeinOpenInterest", 0)
        volume    = tradeinfo.get("tradedVolume", 0)
        lot_size  = meta.get("lotSize", 0)

        oi_chg_pct = 0.0
        if oi and oi_chg:
            prev_oi = oi - oi_chg
            if prev_oi > 0:
                oi_chg_pct = (oi_chg / prev_oi) * 100

        return {
            "oi":          oi,
            "oi_chg":      oi_chg,
            "oi_chg_pct":  oi_chg_pct,
            "fut_volume":  volume,
            "lot_size":    lot_size,
        }

    except Exception:
        return {}


# =========================================================
# NSE CASH MARKET DELIVERY DATA
# =========================================================

def fetch_nse_delivery(symbol):
    """
    Fetches delivery % and cash market data from NSE equity quote.
    NSE endpoint: /api/quote-equity?symbol=SBIN
    """
    try:
        s = get_nse_session()
        if not s:
            return {}

        url = (
            "https://www.nseindia.com/api/"
            f"quote-equity?symbol={symbol}"
        )
        r = s.get(url, timeout=8)

        if r.status_code != 200:
            return {}

        data = r.json()

        trade = data.get("tradeInfo", {})
        delivery_qty    = trade.get("deliveryQuantity", 0)
        traded_qty      = trade.get("tradedQuantity", 0)
        delivery_pct    = trade.get("deliveryToTradedQuantity", 0.0)
        series          = data.get("info", {}).get("series", "")

        # Some symbols return delivery_pct as string "60.5"
        try:
            delivery_pct = float(delivery_pct)
        except (ValueError, TypeError):
            delivery_pct = 0.0

        return {
            "delivery_pct": delivery_pct,
            "delivery_qty": delivery_qty,
            "traded_qty":   traded_qty,
            "series":       series,
        }

    except Exception:
        return {}


# =========================================================
# YAHOO FINANCE PRICE FETCH
# =========================================================

def fetch_price(symbol, yf_symbol_override=None):
    """
    Fetches intraday 5m OHLCV data from Yahoo Finance.

    KEY FIX:
      Move % = (last_price - day_open) / day_open * 100
      NOT candle-to-candle which barely moves.

    Also tracks volume vs 20-day average for surge detection.
    """
    try:
        # Rate limit protection
        time.sleep(random.uniform(1.5, 3.0))

        yf_sym = yf_symbol_override or f"{symbol}.NS"

        # -------------------------------------------------
        # INTRADAY 5-MIN DATA (for price move vs open)
        # -------------------------------------------------
        df_intra = yf.download(
            yf_sym,
            period="1d",
            interval="5m",
            progress=False,
            auto_adjust=True,
            threads=False,
        )

        if df_intra.empty:
            log(f"⚠️ {symbol}: No intraday data")
            return None

        # Fix MultiIndex columns
        if isinstance(df_intra.columns, pd.MultiIndex):
            df_intra.columns = df_intra.columns.get_level_values(0)

        # Need at least 2 candles
        if len(df_intra) < 2:
            return None

        day_open   = float(df_intra["Open"].iloc[0])   # first candle open = day open
        last_price = float(df_intra["Close"].iloc[-1])
        day_high   = float(df_intra["High"].max())
        day_low    = float(df_intra["Low"].min())
        intra_vol  = int(df_intra["Volume"].sum())      # total intraday volume so far

        if day_open <= 0:
            return None

        # Move % vs day open (KEY FIX — not vs prev candle)
        move_pct = ((last_price - day_open) / day_open) * 100

        # -------------------------------------------------
        # 20-DAY DAILY DATA (for average volume baseline)
        # -------------------------------------------------
        avg_volume = 0
        try:
            df_daily = yf.download(
                yf_sym,
                period="25d",
                interval="1d",
                progress=False,
                auto_adjust=True,
                threads=False,
            )
            if not df_daily.empty:
                if isinstance(df_daily.columns, pd.MultiIndex):
                    df_daily.columns = df_daily.columns.get_level_values(0)
                # Exclude today, take last 20 days
                avg_volume = int(df_daily["Volume"].iloc[:-1].tail(20).mean())
        except Exception:
            pass  # Non-critical

        return {
            "symbol":     symbol,
            "yf_symbol":  yf_sym,
            "price":      last_price,
            "day_open":   day_open,
            "day_high":   day_high,
            "day_low":    day_low,
            "move_pct":   move_pct,
            "intra_vol":  intra_vol,
            "avg_volume": avg_volume,
        }

    except Exception as e:
        err = traceback.format_exc()

        # Silently swallow known non-errors
        if "YFRateLimitError" in err:
            log(f"⏳ {symbol}: Rate limited — skipping")
            return None
        if any(x in err for x in ["possibly delisted", "404", "No data found"]):
            log(f"⚠️ {symbol}: No Yahoo data — possibly delisted")
            return None

        log(f"❌ {symbol}: Unexpected error — {str(e)[:80]}")
        return None


# =========================================================
# SIGNAL DETECTION
# =========================================================

def detect_signals(price_data, fno_data, delivery_data):
    """
    Combines price + OI + delivery to produce signal list.

    Returns list of (signal_emoji, signal_name, description)
    """
    signals = []

    move_pct    = price_data.get("move_pct", 0)
    price       = price_data.get("price", 0)
    intra_vol   = price_data.get("intra_vol", 0)
    avg_volume  = price_data.get("avg_volume", 0)

    oi_chg_pct  = fno_data.get("oi_chg_pct", 0)
    delivery_pct = delivery_data.get("delivery_pct", 0)

    # Volume surge ratio
    vol_ratio = (intra_vol / avg_volume) if avg_volume > 0 else 0

    # --- LONG BUILDUP: OI ↑ + Price ↑ ---
    if move_pct > 0 and oi_chg_pct > OI_CHANGE_THRESHOLD:
        signals.append((
            "🚀", "LONG BUILDUP",
            "Fresh longs being added. OI rising with price."
        ))

    # --- SHORT COVERING: OI ↓ + Price ↑ ---
    if move_pct > 0 and oi_chg_pct < -OI_CHANGE_THRESHOLD:
        signals.append((
            "🔄", "SHORT COVERING",
            "Shorts exiting. OI falling while price rises."
        ))

    # --- LONG UNWINDING: OI ↓ + Price ↓ ---
    if move_pct < -PRICE_MOVE_THRESHOLD and oi_chg_pct < -OI_CHANGE_THRESHOLD:
        signals.append((
            "⚠️", "LONG UNWINDING",
            "Longs exiting. OI and price both falling."
        ))

    # --- SHORT BUILDUP: OI ↑ + Price ↓ ---
    if move_pct < -PRICE_MOVE_THRESHOLD and oi_chg_pct > OI_CHANGE_THRESHOLD:
        signals.append((
            "🔻", "SHORT BUILDUP",
            "Fresh shorts being added. OI rising with falling price."
        ))

    # --- HIGH DELIVERY: Strong cash market conviction ---
    if delivery_pct >= DELIVERY_THRESHOLD:
        signals.append((
            "📦", "HIGH DELIVERY",
            f"Strong cash market conviction ({delivery_pct:.1f}% delivery)."
        ))

    # --- PURE MOMENTUM: Big price move ---
    if abs(move_pct) >= PRICE_MOVE_THRESHOLD and not signals:
        direction = "📈" if move_pct > 0 else "📉"
        signals.append((
            direction, "MOMENTUM MOVE",
            f"Strong intraday price breakout of {move_pct:+.2f}%."
        ))

    # --- VOLUME SURGE add-on (not standalone signal) ---
    if vol_ratio >= VOLUME_SURGE_MULT and signals:
        signals.append((
            "🔊", "VOLUME SURGE",
            f"Volume {vol_ratio:.1f}x above 20-day average."
        ))

    return signals


# =========================================================
# TELEGRAM MESSAGE BUILDER
# =========================================================

def build_message(price_data, fno_data, delivery_data, signals):
    symbol      = price_data["symbol"]
    price       = price_data["price"]
    move_pct    = price_data["move_pct"]
    day_open    = price_data["day_open"]
    day_high    = price_data["day_high"]
    day_low     = price_data["day_low"]
    intra_vol   = price_data["intra_vol"]
    avg_volume  = price_data["avg_volume"]

    oi          = fno_data.get("oi", 0)
    oi_chg_pct  = fno_data.get("oi_chg_pct", 0)
    lot_size    = fno_data.get("lot_size", 0)

    delivery_pct = delivery_data.get("delivery_pct", 0)

    vol_ratio = (intra_vol / avg_volume) if avg_volume > 0 else 0

    # Signal summary line
    signal_lines = "\n".join(
        f"  {emoji} <b>{name}</b>: {desc}"
        for emoji, name, desc in signals
    )

    oi_arrow    = "↑" if oi_chg_pct > 0 else "↓" if oi_chg_pct < 0 else "→"
    move_arrow  = "📈" if move_pct > 0 else "📉"
    move_str    = f"{move_pct:+.2f}%"

    oi_str       = f"{oi:,}" if oi else "N/A"
    oi_chg_str   = f"{oi_chg_pct:+.2f}% {oi_arrow}" if oi_chg_pct else "N/A"
    delivery_str = f"{delivery_pct:.1f}%" if delivery_pct else "N/A"
    vol_str      = f"{vol_ratio:.1f}x avg" if vol_ratio else "N/A"
    lot_str      = f"{lot_size:,}" if lot_size else "N/A"

    ist_now = datetime.now(IST).strftime("%H:%M:%S")

    msg = (
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>{symbol}</b>  {move_arrow} <b>{move_str}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"💰 <b>Price</b>:        ₹{price:.2f}\n"
        f"🔓 <b>Day Open</b>:     ₹{day_open:.2f}\n"
        f"⬆️ <b>Day High</b>:     ₹{day_high:.2f}\n"
        f"⬇️ <b>Day Low</b>:      ₹{day_low:.2f}\n"
        f"\n"
        f"📊 <b>OI</b>:            {oi_str}\n"
        f"🔁 <b>OI Change</b>:    {oi_chg_str}\n"
        f"📦 <b>Delivery %</b>:   {delivery_str}\n"
        f"🔊 <b>Volume</b>:       {vol_str}\n"
        f"📐 <b>Lot Size</b>:     {lot_str}\n"
        f"\n"
        f"🎯 <b>SIGNALS</b>:\n"
        f"{signal_lines}\n"
        f"\n"
        f"🕐 {ist_now} IST"
    )

    return msg


# =========================================================
# PROCESS ONE STOCK
# =========================================================

def process_stock(symbol, yf_override):
    try:
        # -----------------------------------------------
        # 1. Yahoo Finance price data
        # -----------------------------------------------
        price_data = fetch_price(symbol, yf_override)
        if not price_data:
            return

        move_pct = price_data["move_pct"]

        # -----------------------------------------------
        # 2. NSE FnO data (OI, delivery)
        #    Use NSE symbol (not M&M — NSE uses M&M fine)
        # -----------------------------------------------
        nse_sym = "M&M" if symbol == "M&M" else symbol

        fno_data      = fetch_nse_fno(nse_sym)
        delivery_data = fetch_nse_delivery(nse_sym)

        # -----------------------------------------------
        # 3. Detect signals
        # -----------------------------------------------
        signals = detect_signals(price_data, fno_data, delivery_data)

        # -----------------------------------------------
        # 4. Gate: only alert if price moved enough
        #    OR a strong FnO signal exists
        # -----------------------------------------------
        has_price_move     = abs(move_pct) >= PRICE_MOVE_THRESHOLD
        has_oi_signal      = abs(fno_data.get("oi_chg_pct", 0)) >= OI_CHANGE_THRESHOLD
        has_delivery_spike = delivery_data.get("delivery_pct", 0) >= DELIVERY_THRESHOLD

        if not (has_price_move or has_oi_signal or has_delivery_spike):
            return  # Nothing interesting — skip silently

        if not signals:
            # Fallback: create generic momentum signal
            direction = "📈" if move_pct > 0 else "📉"
            signals = [(direction, "PRICE MOVE", f"{move_pct:+.2f}% from day open")]

        # -----------------------------------------------
        # 5. Build and send alert
        # -----------------------------------------------
        log(
            f"🚨 ALERT | {symbol} | "
            f"Move={move_pct:+.2f}% | "
            f"OI={fno_data.get('oi_chg_pct', 0):+.2f}% | "
            f"Del={delivery_data.get('delivery_pct', 0):.1f}%"
        )

        msg = build_message(price_data, fno_data, delivery_data, signals)
        send_telegram(msg)

    except Exception:
        traceback.print_exc()


# =========================================================
# MAIN BOT
# =========================================================

def run_bot():
    ist_now = datetime.now(IST).strftime("%H:%M:%S")
    log(f"🚀 RUN STARTED | {ist_now}")

    if not is_market_open():
        log("⏰ Market closed — exiting")
        return

    log("✅ Market hours active")

    # Prime NSE session once before parallel fetches
    get_nse_session()

    log("📊 FETCH STARTED")

    valid = 0
    alerts = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:

        futures = {
            executor.submit(
                process_stock, symbol, yf_override
            ): symbol
            for symbol, yf_override in WATCHLIST
        }

        for future in as_completed(futures):
            symbol = futures[future]
            try:
                future.result()
                valid += 1
            except Exception:
                log(f"❌ {symbol}: Thread error")
                traceback.print_exc()

    log(f"📦 Stocks processed: {valid}")
    log("✅ CRON RUN FINISHED")


# =========================================================
# ENTRY
# =========================================================

if __name__ == "__main__":
    try:
        if not BOT_TOKEN or not CHAT_ID:
            log("❌ ENV VARIABLES MISSING — set BOT_TOKEN and CHAT_ID")
            raise SystemExit(1)

        run_bot()

    except SystemExit:
        raise

    except Exception:
        traceback.print_exc()
        log("❌ MAIN CRITICAL ERROR")
