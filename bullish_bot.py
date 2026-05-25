# =========================================================
# NSE FNO MOMENTUM BOT — STOCK-ONLY ALERTS v3
# =========================================================
#
# WHAT'S NEW vs v2
# ---------------------------------------------------------
#  ✅ No sector pulse / sector scoring — purely stock-level
#  ✅ Every qualifying stock gets its own detailed Telegram card
#  ✅ Additional filters added:
#       • FnO signal REQUIRED (price move alone not enough)
#       • Minimum OI size gate (skip illiquid F&O contracts)
#       • Near-day-high filter (price within 3% of day high)
#       • 5-day momentum check (price > 5-day avg = uptrend)
#       • Near 52-week high bonus (breakout zone confirmation)
#       • Delivery + Volume convergence bonus (dual conviction)
#       • Short covering BONUS over long buildup (stronger signal)
#  ✅ Score cap raised to 120; gate kept at MIN_SCORE
#  ✅ Each alert card shows every data point used in scoring
#  ✅ Clean "WHY THIS PICK" section with all triggered reasons
#
# SCORING SYSTEM (max 120 pts)
# ---------------------------------------------------------
#
# Price Action        max 30 pts
#   +10  move > MOVE_T1 (0.8%)
#   +10  move > MOVE_T2 (1.5%)
#   +10  move > MOVE_T3 (2.5%)
#
# FnO / OI Signals    max 45 pts
#   +25  Short Covering  (OI↓ > 2% + Price↑)  ← strongest
#   +15  Long Buildup    (OI↑ > 2% + Price↑)
#   +10  OI change > 5%  (strong conviction, additive)
#   +5   OI change > 2%  (moderate, additive if not above)
#
# Delivery            max 20 pts
#   +20  delivery > 70%
#   +15  delivery > 60%
#   +10  delivery > 50%
#
# Volume              max 10 pts
#   +10  volume > 3x avg
#   +7   volume > 2x avg
#   +4   volume > 1.5x avg
#
# Convergence Bonus   max 5 pts
#   +5   delivery > 60% AND volume > 2x avg (dual conviction)
#
# Near Day-High Bonus max 5 pts
#   +5   price within 1.5% of day high (holding breakout)
#
# 52-Week High Bonus  max 5 pts
#   +5   price within 5% of 52-week high (breakout zone)
#
# HARD FILTERS (disqualify stock entirely if failed)
#   • move_pct > 0 (upside only)
#   • At least ONE FnO signal (OI change ≥ 2% either direction)
#   • Minimum OI contracts ≥ MIN_OI_CONTRACTS
#   • Price within 3% of day high (still near the top)
#   • 5-day momentum: current price > 5-day close avg
#
# ALERT GATE
#   Min score = MIN_SCORE (default 50)
#   Max alerts = MAX_ALERTS (default 7)
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


def log(msg):
    print(msg, flush=True)
    logger.info(msg)


log("🚀 SCRIPT STARTED")

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = os.getenv("CHAT_ID")

MIN_SCORE        = 50     # Minimum score to trigger alert
MAX_ALERTS       = 7      # Max stock alerts per run
MIN_OI_CONTRACTS = 5000   # Minimum open interest (contracts) — skip illiquid

# Price move thresholds vs day open (upside %)
MOVE_T1, MOVE_T2, MOVE_T3 = 0.8, 1.5, 2.5

# OI change % thresholds
OI_T1, OI_T2 = 2.0, 5.0

# Delivery % thresholds
DEL_T1, DEL_T2, DEL_T3 = 50.0, 60.0, 70.0

# Volume vs 20-day avg multiples
VOL_T1, VOL_T2, VOL_T3 = 1.5, 2.0, 3.0

# Near-day-high filter: price must be within this % of day high
NEAR_HIGH_PCT = 3.0

# 52-week high bonus: price within this % of 52w high
NEAR_52W_HIGH_PCT = 5.0

MAX_WORKERS = 1     # Keep 1 to avoid Yahoo Finance rate limits

IST         = timezone(timedelta(hours=5, minutes=30))
ALERT_START = (9, 15)
ALERT_END   = (15, 30)

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
# Format: (NSE_SYMBOL, DISPLAY_SECTOR_LABEL, YF_OVERRIDE_or_None)
# =========================================================

WATCHLIST = [
    # ── BANKING ─────────────────────────────────────────
    ("SBIN",       "🏦 Banking",             None),
    ("HDFCBANK",   "🏦 Banking",             None),
    ("ICICIBANK",  "🏦 Banking",             None),
    ("AXISBANK",   "🏦 Banking",             None),
    ("KOTAKBANK",  "🏦 Banking",             None),
    ("BANKBARODA", "🏦 Banking",             None),
    ("CANBK",      "🏦 Banking",             None),
    ("UNIONBANK",  "🏦 Banking",             None),

    # ── PSU FINANCE ─────────────────────────────────────
    ("PFC",        "🏛️ PSU Finance",         None),
    ("RECLTD",     "🏛️ PSU Finance",         None),
    ("JIOFIN",     "🏛️ PSU Finance",         None),

    # ── IT ──────────────────────────────────────────────
    ("INFY",       "💻 IT",                  None),
    ("TCS",        "💻 IT",                  None),
    ("WIPRO",      "💻 IT",                  None),
    ("KPITTECH",   "💻 IT",                  None),
    ("PERSISTENT", "💻 IT",                  None),
    ("COFORGE",    "💻 IT",                  None),
    ("TATATECH",   "💻 IT",                  None),

    # ── INFRA / DEFENCE ─────────────────────────────────
    ("LT",         "🛡️ Infra & Defence",     None),
    ("BEL",        "🛡️ Infra & Defence",     None),
    ("HAL",        "🛡️ Infra & Defence",     None),
    ("RVNL",       "🛡️ Infra & Defence",     None),
    ("MAZDOCK",    "🛡️ Infra & Defence",     None),
    ("COCHINSHIP", "🛡️ Infra & Defence",     None),

    # ── POWER / ENERGY ──────────────────────────────────
    ("NTPC",       "⚡ Power & Energy",       None),
    ("TATAPOWER",  "⚡ Power & Energy",       None),
    ("JSWENERGY",  "⚡ Power & Energy",       None),
    ("POWERGRID",  "⚡ Power & Energy",       None),
    ("SUZLON",     "⚡ Power & Energy",       None),
    ("ADANIENT",   "⚡ Power & Energy",       None),
    ("ADANIPORTS", "⚡ Power & Energy",       None),

    # ── OIL & GAS ───────────────────────────────────────
    ("ONGC",       "🛢️ Oil & Gas",           None),
    ("IOC",        "🛢️ Oil & Gas",           None),
    ("BPCL",       "🛢️ Oil & Gas",           None),
    ("GAIL",       "🛢️ Oil & Gas",           None),

    # ── METALS ──────────────────────────────────────────
    ("TATASTEEL",  "⚙️ Metals",              None),
    ("JSWSTEEL",   "⚙️ Metals",              None),
    ("HINDALCO",   "⚙️ Metals",              None),
    ("HINDCOPPER", "⚙️ Metals",              None),

    # ── PHARMA ──────────────────────────────────────────
    ("SUNPHARMA",  "💊 Pharma",              None),
    ("DRREDDY",    "💊 Pharma",              None),
    ("DIVISLAB",   "💊 Pharma",              None),
    ("CIPLA",      "💊 Pharma",              None),

    # ── NBFC / FINANCE ──────────────────────────────────
    ("BAJFINANCE",  "💰 NBFC & Finance",     None),
    ("BAJAJFINSV",  "💰 NBFC & Finance",     None),
    ("CHOLAFIN",    "💰 NBFC & Finance",     None),
    ("SHRIRAMFIN",  "💰 NBFC & Finance",     None),
    ("MUTHOOTFIN",  "💰 NBFC & Finance",     None),
    ("MANAPPURAM",  "💰 NBFC & Finance",     None),

    # ── AUTO ────────────────────────────────────────────
    ("MARUTI",     "🚗 Auto",                None),
    ("M&M",        "🚗 Auto",                "MM.NS"),
    ("EICHERMOT",  "🚗 Auto",                None),
    ("TVSMOTOR",   "🚗 Auto",                None),
    ("ASHOKLEY",   "🚗 Auto",                None),
    ("TATAMTRDVR", "🚗 Auto",                None),

    # ── REALTY ──────────────────────────────────────────
    ("LODHA",      "🏗️ Realty",              None),
    ("DLF",        "🏗️ Realty",              None),

    # ── CONSUMER ────────────────────────────────────────
    ("TITAN",      "🛍️ Consumer",            None),
    ("TRENT",      "🛍️ Consumer",            None),
    ("PIDILITIND", "🛍️ Consumer",            None),
    ("PVRINOX",    "🛍️ Consumer",            None),
    ("VBL",        "🛍️ Consumer",            None),

    # ── INFRA MISC ──────────────────────────────────────
    ("POLYCAB",    "🔌 Infra & Cap Goods",   None),
    ("KEI",        "🔌 Infra & Cap Goods",   None),
    ("INDUSTOWER", "🔌 Infra & Cap Goods",   None),
    ("CGPOWER",    "🔌 Infra & Cap Goods",   None),
    ("IRB",        "🔌 Infra & Cap Goods",   None),
    ("BHEL",       "🔌 Infra & Cap Goods",   None),
]

log(f"📊 Watchlist: {len(WATCHLIST)} stocks loaded")

# =========================================================
# MARKET HOURS CHECK
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
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        r = requests.post(
            url,
            data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=20,
        )
        log(f"📨 Telegram status={r.status_code}")
        if r.status_code != 200:
            log(f"⚠️ Telegram error body: {r.text[:200]}")
    except Exception:
        traceback.print_exc()

# =========================================================
# NSE SESSION
# =========================================================

_nse_session = None


def get_nse_session():
    global _nse_session
    if _nse_session:
        return _nse_session
    try:
        s = requests.Session()
        s.headers.update(NSE_HEADERS)
        s.get("https://www.nseindia.com", timeout=10)
        time.sleep(1)
        _nse_session = s
        log("✅ NSE session ready")
    except Exception:
        log("⚠️ NSE session init failed — FnO/delivery data may be unavailable")
        _nse_session = None
    return _nse_session

# =========================================================
# NSE FnO DATA
# =========================================================

def fetch_nse_fno(symbol):
    try:
        s = get_nse_session()
        if not s:
            return {}
        url = f"https://www.nseindia.com/api/quote-derivative?symbol={symbol}"
        r   = s.get(url, timeout=8)
        if r.status_code != 200:
            return {}
        data   = r.json()
        stocks = data.get("stocks", [])
        fut = next(
            (row for row in stocks
             if row.get("metadata", {}).get("instrumentType") == "Stock Futures"),
            None,
        )
        if not fut:
            return {}
        meta      = fut.get("metadata", {})
        tradeinfo = fut.get("marketDeptOrderBook", {}).get("tradeInfo", {})
        oi        = tradeinfo.get("openInterest", 0) or 0
        oi_chg    = tradeinfo.get("changeinOpenInterest", 0) or 0
        lot_size  = meta.get("lotSize", 0) or 0

        oi_chg_pct = 0.0
        prev_oi = oi - oi_chg
        if prev_oi > 0:
            oi_chg_pct = (oi_chg / prev_oi) * 100

        return {
            "oi":         oi,
            "oi_chg":     oi_chg,
            "oi_chg_pct": oi_chg_pct,
            "lot_size":   lot_size,
        }
    except Exception:
        return {}

# =========================================================
# NSE DELIVERY DATA
# =========================================================

def fetch_nse_delivery(symbol):
    try:
        s = get_nse_session()
        if not s:
            return {}
        url = f"https://www.nseindia.com/api/quote-equity?symbol={symbol}"
        r   = s.get(url, timeout=8)
        if r.status_code != 200:
            return {}
        data  = r.json()
        trade = data.get("tradeInfo", {})
        try:
            delivery_pct = float(trade.get("deliveryToTradedQuantity", 0.0) or 0.0)
        except (ValueError, TypeError):
            delivery_pct = 0.0
        return {
            "delivery_pct": delivery_pct,
            "delivery_qty": trade.get("deliveryQuantity", 0),
            "traded_qty":   trade.get("tradedQuantity", 0),
        }
    except Exception:
        return {}

# =========================================================
# YAHOO FINANCE — PRICE + HISTORICAL DATA
# =========================================================

def fetch_price(symbol, yf_sym_override=None):
    """
    Returns intraday price data + 20-day avg volume + 5-day close avg + 52w high.
    Returns None if:
      - No data
      - Price move is negative / flat (upside-only filter)
      - Price is more than NEAR_HIGH_PCT below the day high (soft filter here,
        hard gate applied in process_stock)
    """
    try:
        time.sleep(random.uniform(1.5, 3.0))

        yf_sym = yf_sym_override or f"{symbol}.NS"

        # ── Intraday (5-min bars) ─────────────────────────
        df = yf.download(
            yf_sym,
            period="1d",
            interval="5m",
            progress=False,
            auto_adjust=True,
            threads=False,
        )

        if df.empty or len(df) < 2:
            return None

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        day_open   = float(df["Open"].iloc[0])
        last_price = float(df["Close"].iloc[-1])
        day_high   = float(df["High"].max())
        day_low    = float(df["Low"].min())
        intra_vol  = int(df["Volume"].sum())

        if day_open <= 0 or last_price <= 0:
            return None

        move_pct = ((last_price - day_open) / day_open) * 100

        # Upside-only
        if move_pct <= 0:
            return None

        # ── Daily history (25 days) ───────────────────────
        avg_volume   = 0
        five_day_avg = 0.0
        week_52_high = 0.0

        try:
            df_d = yf.download(
                yf_sym,
                period="25d",
                interval="1d",
                progress=False,
                auto_adjust=True,
                threads=False,
            )
            if not df_d.empty:
                if isinstance(df_d.columns, pd.MultiIndex):
                    df_d.columns = df_d.columns.get_level_values(0)
                avg_volume   = int(df_d["Volume"].iloc[:-1].tail(20).mean())
                five_day_avg = float(df_d["Close"].iloc[:-1].tail(5).mean())
        except Exception:
            pass

        # ── 52-week high ──────────────────────────────────
        try:
            df_52 = yf.download(
                yf_sym,
                period="52wk",
                interval="1d",
                progress=False,
                auto_adjust=True,
                threads=False,
            )
            if not df_52.empty:
                if isinstance(df_52.columns, pd.MultiIndex):
                    df_52.columns = df_52.columns.get_level_values(0)
                week_52_high = float(df_52["High"].max())
        except Exception:
            pass

        return {
            "symbol":        symbol,
            "price":         last_price,
            "day_open":      day_open,
            "day_high":      day_high,
            "day_low":       day_low,
            "move_pct":      move_pct,
            "intra_vol":     intra_vol,
            "avg_volume":    avg_volume,
            "five_day_avg":  five_day_avg,
            "week_52_high":  week_52_high,
        }

    except Exception:
        err = traceback.format_exc()
        if "YFRateLimitError" in err:
            log(f"⏳ {symbol}: Rate limited — skipping")
            return None
        if any(x in err for x in ["possibly delisted", "404", "No data found"]):
            return None
        log(f"❌ {symbol}: Price fetch error")
        return None

# =========================================================
# HARD FILTERS  (applied before scoring)
# =========================================================

def passes_hard_filters(symbol, price_data, fno_data):
    """
    Returns (passed: bool, reason: str)
    All must pass for the stock to be scored.
    """
    move_pct     = price_data["move_pct"]
    last_price   = price_data["price"]
    day_high     = price_data["day_high"]
    five_day_avg = price_data["five_day_avg"]
    oi           = fno_data.get("oi", 0)
    oi_chg_pct   = abs(fno_data.get("oi_chg_pct", 0))

    # 1. Upside only (already pre-filtered in fetch_price, but double-check)
    if move_pct <= 0:
        return False, "No upside move"

    # 2. FnO signal required — OI must be moving meaningfully
    if oi_chg_pct < OI_T1:
        return False, f"Weak OI change ({oi_chg_pct:.1f}% < {OI_T1}%)"

    # 3. Minimum OI for liquidity
    if oi > 0 and oi < MIN_OI_CONTRACTS:
        return False, f"Low OI ({oi:,} contracts)"

    # 4. Price must be near day high (not fading from peak)
    pct_from_high = ((day_high - last_price) / day_high) * 100
    if pct_from_high > NEAR_HIGH_PCT:
        return False, f"Fading from high ({pct_from_high:.1f}% below day high)"

    # 5. 5-day momentum: price above 5-day avg close (uptrend confirmation)
    if five_day_avg > 0 and last_price < five_day_avg:
        return False, f"Below 5-day avg (₹{five_day_avg:.2f})"

    return True, "OK"

# =========================================================
# SCORING ENGINE  (0–120 pts)
# =========================================================

def score_stock(price_data, fno_data, delivery_data):
    """
    Returns (score: int, breakdown: dict, signals: list of (emoji, name, detail))
    """
    score     = 0
    breakdown = {}
    signals   = []

    move_pct     = price_data["move_pct"]
    last_price   = price_data["price"]
    day_high     = price_data["day_high"]
    week_52_high = price_data["week_52_high"]
    intra_vol    = price_data["intra_vol"]
    avg_volume   = price_data["avg_volume"]

    oi_chg_pct   = fno_data.get("oi_chg_pct", 0)
    delivery_pct = delivery_data.get("delivery_pct", 0)
    vol_ratio    = (intra_vol / avg_volume) if avg_volume > 0 else 0

    # ── 1. Price Action  (max 30 pts) ─────────────────────
    pp = 0
    if move_pct >= MOVE_T1: pp += 10
    if move_pct >= MOVE_T2: pp += 10
    if move_pct >= MOVE_T3: pp += 10
    score += pp
    breakdown["price"] = pp
    if pp >= 10:
        signals.append(("📈", "PRICE BREAKOUT",
                         f"+{move_pct:.2f}% above day open"))

    # ── 2. FnO / OI  (max 45 pts) ─────────────────────────
    fp = 0
    fno_type = ""
    if move_pct > 0 and oi_chg_pct < -OI_T1:
        fp += 25
        fno_type = "SHORT_COVERING"
        signals.append(("🔄", "SHORT COVERING",
                         f"OI {oi_chg_pct:+.1f}% — shorts exiting as price rises"))
    elif move_pct > 0 and oi_chg_pct > OI_T1:
        fp += 15
        fno_type = "LONG_BUILDUP"
        signals.append(("🚀", "LONG BUILDUP",
                         f"OI {oi_chg_pct:+.1f}% — fresh longs entering"))

    # Additive OI conviction bonus
    if abs(oi_chg_pct) >= OI_T2:
        fp += 10
        signals.append(("💪", "STRONG OI CONVICTION",
                         f"OI moved {abs(oi_chg_pct):.1f}% — high certainty"))
    elif abs(oi_chg_pct) >= OI_T1:
        fp += 5

    score += fp
    breakdown["fno"]      = fp
    breakdown["fno_type"] = fno_type

    # ── 3. Delivery  (max 20 pts) ─────────────────────────
    dp = 0
    if delivery_pct >= DEL_T3:
        dp = 20
        signals.append(("📦", "HIGH DELIVERY",
                         f"{delivery_pct:.1f}% — strong cash market conviction"))
    elif delivery_pct >= DEL_T2:
        dp = 15
        signals.append(("📦", "GOOD DELIVERY",
                         f"{delivery_pct:.1f}% delivery"))
    elif delivery_pct >= DEL_T1:
        dp = 10
        signals.append(("📦", "MODERATE DELIVERY",
                         f"{delivery_pct:.1f}% delivery"))
    score += dp
    breakdown["delivery"] = dp

    # ── 4. Volume  (max 10 pts) ───────────────────────────
    vp = 0
    if vol_ratio >= VOL_T3:
        vp = 10
        signals.append(("🔊", "VOLUME EXPLOSION",
                         f"{vol_ratio:.1f}x 20-day avg"))
    elif vol_ratio >= VOL_T2:
        vp = 7
        signals.append(("🔊", "VOLUME SURGE",
                         f"{vol_ratio:.1f}x 20-day avg"))
    elif vol_ratio >= VOL_T1:
        vp = 4
        signals.append(("🔊", "ELEVATED VOLUME",
                         f"{vol_ratio:.1f}x 20-day avg"))
    score += vp
    breakdown["volume"] = vp

    # ── 5. Convergence Bonus  (max 5 pts) ─────────────────
    cp = 0
    if delivery_pct >= DEL_T2 and vol_ratio >= VOL_T2:
        cp = 5
        signals.append(("🎯", "DUAL CONVICTION",
                         "High delivery + high volume — cash + derivatives aligned"))
    score += cp
    breakdown["convergence"] = cp

    # ── 6. Near Day-High Bonus  (max 5 pts) ───────────────
    hp = 0
    pct_from_high = ((day_high - last_price) / day_high) * 100
    if pct_from_high <= 1.5:
        hp = 5
        signals.append(("🏔️", "HOLDING DAY HIGH",
                         f"Price within {pct_from_high:.1f}% of high — breakout intact"))
    score += hp
    breakdown["near_high"] = hp

    # ── 7. 52-Week High Bonus  (max 5 pts) ────────────────
    wp = 0
    if week_52_high > 0:
        pct_from_52w = ((week_52_high - last_price) / week_52_high) * 100
        if pct_from_52w <= NEAR_52W_HIGH_PCT:
            wp = 5
            signals.append(("🌟", "52-WEEK HIGH ZONE",
                             f"Within {pct_from_52w:.1f}% of 52-week high — breakout territory"))
    score += wp
    breakdown["near_52w"] = wp

    breakdown["total"] = score
    return score, breakdown, signals

# =========================================================
# VISUAL AIDS
# =========================================================

def score_bar(score, max_score=120):
    pct    = min(score / max_score, 1.0)
    filled = round(pct * 10)
    empty  = 10 - filled
    return f"{'█' * filled}{'░' * empty} {score}/{max_score}"


def score_label(score):
    if score >= 100: return "🔥🔥 EXCEPTIONAL"
    if score >= 80:  return "🔥 VERY STRONG"
    if score >= 65:  return "✅ STRONG"
    if score >= 50:  return "🟡 MODERATE"
    return "⚪ WEAK"


def fno_badge(fno_type):
    return {
        "SHORT_COVERING": "🔄 Short Covering",
        "LONG_BUILDUP":   "🚀 Long Buildup",
    }.get(fno_type, "📊 FnO Active")

# =========================================================
# PROCESS ONE STOCK
# =========================================================

def process_stock(symbol, sector_label, yf_override):
    try:
        # Step 1: Price data
        price_data = fetch_price(symbol, yf_override)
        if not price_data:
            return None

        # Step 2: FnO data
        fno_data = fetch_nse_fno(symbol)

        # Step 3: Hard filters
        passed, reason = passes_hard_filters(symbol, price_data, fno_data)
        if not passed:
            log(f"⛔ {symbol}: filtered — {reason}")
            return None

        # Step 4: Delivery data
        delivery_data = fetch_nse_delivery(symbol)

        # Step 5: Score
        stock_score, breakdown, signals = score_stock(
            price_data, fno_data, delivery_data
        )

        log(
            f"📊 {symbol} | +{price_data['move_pct']:.2f}% | "
            f"OI={fno_data.get('oi_chg_pct', 0):+.1f}% | "
            f"Del={delivery_data.get('delivery_pct', 0):.0f}% | "
            f"Score={stock_score}"
        )

        return {
            "symbol":        symbol,
            "sector_label":  sector_label,
            "score":         stock_score,
            "breakdown":     breakdown,
            "signals":       signals,
            "price_data":    price_data,
            "fno_data":      fno_data,
            "delivery_data": delivery_data,
        }

    except Exception:
        traceback.print_exc()
        return None

# =========================================================
# BUILD TELEGRAM ALERT CARD
# =========================================================

def build_alert_card(rank, result):
    pd_  = result["price_data"]
    fd   = result["fno_data"]
    dd   = result["delivery_data"]
    bd   = result["breakdown"]
    sigs = result["signals"]

    sym          = pd_["symbol"]
    price        = pd_["price"]
    move_pct     = pd_["move_pct"]
    day_open     = pd_["day_open"]
    day_high     = pd_["day_high"]
    day_low      = pd_["day_low"]
    intra_vol    = pd_["intra_vol"]
    avg_volume   = pd_["avg_volume"]
    five_day_avg = pd_["five_day_avg"]
    week_52_high = pd_["week_52_high"]

    oi           = fd.get("oi", 0)
    oi_chg       = fd.get("oi_chg", 0)
    oi_chg_pct   = fd.get("oi_chg_pct", 0)
    lot_size     = fd.get("lot_size", 0)
    delivery_pct = dd.get("delivery_pct", 0)

    vol_ratio     = (intra_vol / avg_volume) if avg_volume > 0 else 0
    pct_from_high = ((day_high - price) / day_high) * 100 if day_high > 0 else 0
    pct_from_52w  = ((week_52_high - price) / week_52_high) * 100 if week_52_high > 0 else 0

    oi_dir   = "↑ Building" if oi_chg_pct > 0 else "↓ Unwinding"
    fno_type = bd.get("fno_type", "")
    ist_now  = datetime.now(IST).strftime("%d %b %Y  %H:%M:%S IST")

    # Signals block
    sig_lines = "\n".join(
        f"  {e} <b>{n}</b>: {d}" for e, n, d in sigs
    ) or "  📊 Base momentum criteria met"

    # Score breakdown line
    breakdown_line = (
        f"Price +{bd.get('price',0)} | "
        f"FnO +{bd.get('fno',0)} | "
        f"Del +{bd.get('delivery',0)} | "
        f"Vol +{bd.get('volume',0)} | "
        f"Conv +{bd.get('convergence',0)} | "
        f"High +{bd.get('near_high',0)} | "
        f"52W +{bd.get('near_52w',0)}"
    )

    msg = (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏅 <b>#{rank}  {sym}</b>  —  {result['sector_label']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"⭐ <b>Score: {score_bar(result['score'])}</b>\n"
        f"   {score_label(result['score'])}\n"
        f"<code>{breakdown_line}</code>\n"
        f"\n"
        f"━━ 📌 PRICE ━━\n"
        f"  💰 LTP:          ₹{price:.2f}  (<b>+{move_pct:.2f}%</b>)\n"
        f"  🔓 Day Open:     ₹{day_open:.2f}\n"
        f"  ⬆️ Day High:     ₹{day_high:.2f}  ({pct_from_high:.1f}% from now)\n"
        f"  ⬇️ Day Low:      ₹{day_low:.2f}\n"
        f"  📐 5-Day Avg:    ₹{five_day_avg:.2f}\n"
    )

    if week_52_high > 0:
        msg += f"  🌟 52W High:     ₹{week_52_high:.2f}  ({pct_from_52w:.1f}% below)\n"

    msg += f"\n━━ 📊 FnO / OI ━━\n"

    if oi > 0:
        msg += (
            f"  📋 OI:           {oi:,} contracts\n"
            f"  🔁 OI Change:    {oi_chg:+,}  ({oi_chg_pct:+.2f}%)  {oi_dir}\n"
            f"  🏷️ Signal:       {fno_badge(fno_type)}\n"
        )
        if lot_size > 0:
            msg += f"  📦 Lot Size:     {lot_size:,}\n"
    else:
        msg += "  ⚠️ OI data unavailable\n"

    msg += f"\n━━ 📦 DELIVERY & VOLUME ━━\n"

    if delivery_pct > 0:
        msg += f"  📦 Delivery:     {delivery_pct:.1f}%\n"
    else:
        msg += "  📦 Delivery:     N/A\n"

    if avg_volume > 0:
        msg += (
            f"  🔊 Intraday Vol: {intra_vol:,}\n"
            f"  📏 20-Day Avg:   {avg_volume:,}\n"
            f"  📊 Vol Ratio:    {vol_ratio:.2f}x\n"
        )
    else:
        msg += f"  🔊 Volume:       {intra_vol:,} (avg unavailable)\n"

    msg += (
        f"\n━━ 🎯 WHY THIS PICK ━━\n"
        f"{sig_lines}\n"
        f"\n"
        f"🕐 {ist_now}"
    )

    return msg

# =========================================================
# MAIN BOT
# =========================================================

def run_bot():
    ist_now = datetime.now(IST).strftime("%H:%M:%S")
    log(f"🚀 RUN STARTED | {ist_now} IST")

    if not is_market_open():
        log("⏰ Outside market hours — skipping run")
        return

    log("✅ Market hours active — starting scan")
    get_nse_session()

    raw_results = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(process_stock, sym, sector, yf_ov): sym
            for sym, sector, yf_ov in WATCHLIST
        }
        for future in as_completed(futures):
            sym = futures[future]
            try:
                result = future.result()
                if result:
                    raw_results.append(result)
            except Exception:
                log(f"❌ {sym}: thread exception")
                traceback.print_exc()

    log(f"📦 Stocks passing hard filters: {len(raw_results)}")

    # Filter by minimum score
    candidates = [
        r for r in raw_results if r["score"] >= MIN_SCORE
    ]

    log(f"🏆 Candidates above score gate (≥{MIN_SCORE}): {len(candidates)}")

    if not candidates:
        log("💤 No qualifying picks this run — no alerts sent")
        return

    # Sort by score descending, take top N
    top_picks = sorted(candidates, key=lambda x: x["score"], reverse=True)[:MAX_ALERTS]

    log(f"📨 Sending {len(top_picks)} stock alerts")

    for rank, pick in enumerate(top_picks, 1):
        msg = build_alert_card(rank, pick)
        send_telegram(msg)
        log(f"✅ Sent #{rank}: {pick['symbol']} (score={pick['score']})")
        time.sleep(0.7)

    log("✅ CRON RUN COMPLETE")

# =========================================================
# ENTRY
# =========================================================

if __name__ == "__main__":
    try:
        if not BOT_TOKEN or not CHAT_ID:
            log("❌ BOT_TOKEN or CHAT_ID env variable missing")
            raise SystemExit(1)
        run_bot()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        log("❌ CRITICAL ERROR IN MAIN")
