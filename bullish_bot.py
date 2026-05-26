# =========================================================
# NSE FNO MOMENTUM BOT — STOCK-ONLY ALERTS v4
# =========================================================
#
# KEY FIXES vs v3
# ---------------------------------------------------------
#  ✅ Volume ratio now uses same-timeframe comparison
#       (intraday vol normalised to full-day equivalent)
#  ✅ 5-day momentum uses correct iloc slicing
#  ✅ OI gate: oi < MIN_OI_CONTRACTS (not `oi > 0 and ...`)
#  ✅ NSE session auto-refreshes on expiry (15-min TTL)
#  ✅ NSE API calls have retry logic (up to 3 attempts)
#  ✅ day_high uses smoothed high (avoids 1-candle spike filter)
#  ✅ 52-week high pulled from NSE equity API (no extra YF call)
#  ✅ Delivery data distinguished from "unavailable" (None vs 0)
#  ✅ Short covering OI direction verified + prev_oi guard
#  ✅ MAX_WORKERS=1 replaced with simple sequential loop
#  ✅ All three thresholds clearly labelled in scoring summary
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
#   +10  volume > 3x time-normalised avg
#   +7   volume > 2x
#   +4   volume > 1.5x
#
# Convergence Bonus   max 5 pts
#   +5   delivery > 60% AND volume > 2x avg
#
# Near Day-High Bonus max 5 pts
#   +5   price within 1.5% of smoothed day high
#
# 52-Week High Bonus  max 5 pts
#   +5   price within 5% of 52-week high
#
# HARD FILTERS (disqualify if failed)
#   • move_pct > 0                 (upside only)
#   • OI change ≥ 2% either dir   (FnO signal required)
#   • OI ≥ MIN_OI_CONTRACTS        (liquidity gate; 0 = fail)
#   • Price within 3% of smoothed day high
#   • Price > 5-day close avg      (uptrend check)
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

# =========================================================
# LOGGER
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    force=True,
    handlers=[logging.StreamHandler(sys.stdout)],
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

MIN_SCORE        = 50
MAX_ALERTS       = 7
MIN_OI_CONTRACTS = 5000   # 0 OI = fail (guard against missing data)

MOVE_T1, MOVE_T2, MOVE_T3 = 0.8, 1.5, 2.5
OI_T1, OI_T2               = 2.0, 5.0
DEL_T1, DEL_T2, DEL_T3     = 50.0, 60.0, 70.0
VOL_T1, VOL_T2, VOL_T3     = 1.5, 2.0, 3.0

NEAR_HIGH_PCT    = 3.0
NEAR_52W_HIGH_PCT = 5.0

# NSE session TTL: refresh if older than this many seconds
NSE_SESSION_TTL  = 600   # 10 minutes (NSE sessions die around 15m)

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
# HELPERS
# =========================================================

def minutes_since_open():
    """Returns minutes elapsed since 9:15 AM IST today."""
    now = datetime.now(IST)
    open_time = now.replace(hour=9, minute=15, second=0, microsecond=0)
    elapsed = (now - open_time).total_seconds() / 60
    return max(elapsed, 1)


def normalise_volume(intra_vol):
    """
    Scale intraday volume to a full-session equivalent
    so it compares fairly against the 20-day daily average.
    NSE session = 375 minutes total.
    """
    elapsed = minutes_since_open()
    if elapsed <= 0:
        return intra_vol
    return intra_vol * (375 / elapsed)

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
# NSE SESSION  (auto-refresh on TTL expiry)
# =========================================================

_nse_session      = None
_nse_session_born = 0.0   # epoch seconds when session was created


def get_nse_session(force_refresh=False):
    global _nse_session, _nse_session_born
    age = time.time() - _nse_session_born
    if _nse_session and not force_refresh and age < NSE_SESSION_TTL:
        return _nse_session
    try:
        s = requests.Session()
        s.headers.update(NSE_HEADERS)
        s.get("https://www.nseindia.com", timeout=10)
        time.sleep(1)
        _nse_session      = s
        _nse_session_born = time.time()
        log("✅ NSE session (re)created")
    except Exception:
        log("⚠️ NSE session init failed")
        _nse_session = None
    return _nse_session


def nse_get(url, retries=3, backoff=2.0):
    """GET with session auto-refresh + retry on 401/403/5xx."""
    for attempt in range(1, retries + 1):
        s = get_nse_session()
        if not s:
            return None
        try:
            r = s.get(url, timeout=8)
            if r.status_code == 200:
                return r.json()
            # Session likely expired — force refresh and retry
            if r.status_code in (401, 403):
                log(f"⟳ NSE session expired ({r.status_code}), refreshing...")
                get_nse_session(force_refresh=True)
            else:
                log(f"⚠️ NSE HTTP {r.status_code} for {url} (attempt {attempt})")
            time.sleep(backoff * attempt)
        except requests.exceptions.Timeout:
            log(f"⏳ NSE timeout {url} (attempt {attempt})")
            time.sleep(backoff * attempt)
        except Exception:
            log(f"❌ NSE request error {url} (attempt {attempt})")
            time.sleep(backoff * attempt)
    return None

# =========================================================
# NSE FnO DATA
# =========================================================

def fetch_nse_fno(symbol):
    """
    Returns OI, OI change, OI change %, and lot size from
    NSE's derivative quote endpoint.

    Short covering: oi_chg_pct is NEGATIVE (OI decreasing)
    Long buildup:   oi_chg_pct is POSITIVE (OI increasing)

    NSE's `changeinOpenInterest` = today's OI − yesterday's OI
    So prev_oi = oi − oi_chg.  We guard against prev_oi <= 0.
    """
    try:
        data = nse_get(
            f"https://www.nseindia.com/api/quote-derivative?symbol={symbol}"
        )
        if not data:
            return {}

        stocks = data.get("stocks", [])
        # Prefer the nearest expiry stock futures contract
        fut = next(
            (row for row in stocks
             if row.get("metadata", {}).get("instrumentType") == "Stock Futures"),
            None,
        )
        if not fut:
            return {}

        meta      = fut.get("metadata", {})
        tradeinfo = fut.get("marketDeptOrderBook", {}).get("tradeInfo", {})

        oi       = int(tradeinfo.get("openInterest", 0) or 0)
        oi_chg   = int(tradeinfo.get("changeinOpenInterest", 0) or 0)
        lot_size = int(meta.get("lotSize", 0) or 0)

        # oi_chg from NSE = current OI - previous OI
        # Positive → OI building; Negative → OI unwinding (short covering context)
        prev_oi = oi - oi_chg
        if prev_oi > 0:
            oi_chg_pct = (oi_chg / prev_oi) * 100
        elif oi > 0:
            # First day / no prev data — treat as pure buildup
            oi_chg_pct = 0.0
        else:
            oi_chg_pct = 0.0

        return {
            "oi":         oi,
            "oi_chg":     oi_chg,
            "oi_chg_pct": oi_chg_pct,  # negative = unwinding = short covering signal
            "lot_size":   lot_size,
        }
    except Exception:
        traceback.print_exc()
        return {}

# =========================================================
# NSE DELIVERY + 52W HIGH DATA
# =========================================================

def fetch_nse_equity(symbol):
    """
    Returns delivery %, quantities, and 52-week high
    all from the single NSE equity quote endpoint.
    Returns None for delivery_pct when data is unavailable
    (distinguishes "no data" from "0% delivery").
    """
    try:
        data = nse_get(
            f"https://www.nseindia.com/api/quote-equity?symbol={symbol}"
        )
        if not data:
            return {}

        trade = data.get("tradeInfo", {})
        price_info = data.get("priceInfo", {})

        # Delivery
        raw_del = trade.get("deliveryToTradedQuantity")
        if raw_del is not None:
            try:
                delivery_pct = float(raw_del)
            except (ValueError, TypeError):
                delivery_pct = None
        else:
            delivery_pct = None   # explicitly unavailable

        # 52-week high from NSE (more reliable than a YF download)
        week_52_high = 0.0
        try:
            week_52_high = float(
                price_info.get("weekHighLow", {}).get("max", 0) or 0
            )
        except (ValueError, TypeError):
            week_52_high = 0.0

        return {
            "delivery_pct": delivery_pct,   # None = unavailable
            "delivery_qty": trade.get("deliveryQuantity", 0),
            "traded_qty":   trade.get("tradedQuantity", 0),
            "week_52_high": week_52_high,
        }
    except Exception:
        traceback.print_exc()
        return {}

# =========================================================
# YAHOO FINANCE — PRICE + HISTORICAL DATA
# =========================================================

def fetch_price(symbol, yf_sym_override=None):
    """
    Returns intraday price data + 20-day avg volume + 5-day close avg.
    Volume normalisation is applied here for fair comparison.
    52-week high is now sourced from NSE, NOT yfinance.
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
        day_low    = float(df["Low"].min())
        intra_vol  = int(df["Volume"].sum())

        if day_open <= 0 or last_price <= 0:
            return None

        move_pct = ((last_price - day_open) / day_open) * 100

        # Upside-only pre-filter
        if move_pct <= 0:
            return None

        # ── Smoothed day high ─────────────────────────────
        # Use the rolling max of the last 3 candle highs to ignore
        # brief wicks that would unfairly penalise stocks in the
        # near-day-high filter.
        recent_highs = df["High"].tail(3)
        day_high = float(recent_highs.max())

        # ── Daily history (26 days to safely exclude today) ──
        avg_volume   = 0
        five_day_avg = 0.0

        try:
            df_d = yf.download(
                yf_sym,
                period="30d",
                interval="1d",
                progress=False,
                auto_adjust=True,
                threads=False,
            )
            if not df_d.empty:
                if isinstance(df_d.columns, pd.MultiIndex):
                    df_d.columns = df_d.columns.get_level_values(0)

                # Exclude today's partial candle if it's the last row
                # by comparing its date against today
                today_str = datetime.now(IST).strftime("%Y-%m-%d")
                last_date = str(df_d.index[-1])[:10]
                if last_date == today_str:
                    df_hist = df_d.iloc[:-1]
                else:
                    df_hist = df_d

                avg_volume   = int(df_hist["Volume"].tail(20).mean())
                five_day_avg = float(df_hist["Close"].tail(5).mean())

        except Exception:
            pass

        # ── Normalise intraday volume to full-day equivalent ──
        # This makes vol_ratio meaningful at any time of day.
        norm_vol = int(normalise_volume(intra_vol))

        return {
            "symbol":       symbol,
            "price":        last_price,
            "day_open":     day_open,
            "day_high":     day_high,   # smoothed (3-candle max)
            "day_low":      day_low,
            "move_pct":     move_pct,
            "intra_vol":    intra_vol,  # raw (for display)
            "norm_vol":     norm_vol,   # normalised (for ratio)
            "avg_volume":   avg_volume,
            "five_day_avg": five_day_avg,
        }

    except Exception:
        err = traceback.format_exc()
        if "YFRateLimitError" in err:
            log(f"⏳ {symbol}: Rate limited — skipping")
            return None
        if any(x in err for x in ["possibly delisted", "404", "No data found"]):
            return None
        log(f"❌ {symbol}: Price fetch error")
        traceback.print_exc()
        return None

# =========================================================
# HARD FILTERS  (applied before scoring)
# =========================================================

def passes_hard_filters(symbol, price_data, fno_data):
    """
    Returns (passed: bool, reason: str).
    Every condition must pass.
    """
    move_pct     = price_data["move_pct"]
    last_price   = price_data["price"]
    day_high     = price_data["day_high"]
    five_day_avg = price_data["five_day_avg"]
    oi           = fno_data.get("oi", 0)
    oi_chg_pct   = abs(fno_data.get("oi_chg_pct", 0))

    # 1. Upside only
    if move_pct <= 0:
        return False, "No upside move"

    # 2. FnO signal required
    if oi_chg_pct < OI_T1:
        return False, f"Weak OI change ({oi_chg_pct:.1f}% < {OI_T1}%)"

    # 3. Minimum OI — 0 means data unavailable, also fail
    if oi < MIN_OI_CONTRACTS:
        return False, f"Low/missing OI ({oi:,} contracts < {MIN_OI_CONTRACTS:,})"

    # 4. Price must be near smoothed day high
    pct_from_high = ((day_high - last_price) / day_high) * 100
    if pct_from_high > NEAR_HIGH_PCT:
        return False, f"Fading from high ({pct_from_high:.1f}% below smoothed day high)"

    # 5. 5-day uptrend (only check if we have valid history)
    if five_day_avg > 0 and last_price < five_day_avg:
        return False, f"Below 5-day avg (₹{five_day_avg:.2f})"

    return True, "OK"

# =========================================================
# SCORING ENGINE  (0–120 pts)
# =========================================================

def score_stock(price_data, fno_data, equity_data):
    """
    Returns (score: int, breakdown: dict, signals: list).
    Uses normalised volume for vol_ratio.
    Handles None delivery_pct gracefully.
    """
    score     = 0
    breakdown = {}
    signals   = []

    move_pct     = price_data["move_pct"]
    last_price   = price_data["price"]
    day_high     = price_data["day_high"]
    week_52_high = equity_data.get("week_52_high", 0)
    intra_vol    = price_data["intra_vol"]
    norm_vol     = price_data["norm_vol"]
    avg_volume   = price_data["avg_volume"]

    oi_chg_pct   = fno_data.get("oi_chg_pct", 0)

    # delivery_pct=None means "data unavailable" → score 0, don't mislead
    delivery_pct = equity_data.get("delivery_pct")
    del_available = delivery_pct is not None
    delivery_pct  = delivery_pct if del_available else 0.0

    # Use normalised volume for fair intraday comparison
    vol_ratio = (norm_vol / avg_volume) if avg_volume > 0 else 0

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
    # oi_chg_pct NEGATIVE = OI unwinding = short covering (stronger signal)
    # oi_chg_pct POSITIVE = OI building  = long buildup
    fp = 0
    fno_type = ""
    if move_pct > 0 and oi_chg_pct < -OI_T1:
        fp += 25
        fno_type = "SHORT_COVERING"
        signals.append(("🔄", "SHORT COVERING",
                         f"OI {oi_chg_pct:+.1f}% (unwinding) — shorts exiting into rising price"))
    elif move_pct > 0 and oi_chg_pct > OI_T1:
        fp += 15
        fno_type = "LONG_BUILDUP"
        signals.append(("🚀", "LONG BUILDUP",
                         f"OI {oi_chg_pct:+.1f}% (building) — fresh longs entering"))

    # Additive OI conviction bonus
    abs_oi = abs(oi_chg_pct)
    if abs_oi >= OI_T2:
        fp += 10
        signals.append(("💪", "STRONG OI CONVICTION",
                         f"OI moved {abs_oi:.1f}% — high certainty signal"))
    elif abs_oi >= OI_T1:
        fp += 5

    score += fp
    breakdown["fno"]      = fp
    breakdown["fno_type"] = fno_type

    # ── 3. Delivery  (max 20 pts) ─────────────────────────
    dp = 0
    if del_available:
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
    breakdown["delivery"]           = dp
    breakdown["delivery_available"] = del_available

    # ── 4. Volume  (max 10 pts) ───────────────────────────
    vp = 0
    if vol_ratio >= VOL_T3:
        vp = 10
        signals.append(("🔊", "VOLUME EXPLOSION",
                         f"{vol_ratio:.1f}x 20-day avg (normalised)"))
    elif vol_ratio >= VOL_T2:
        vp = 7
        signals.append(("🔊", "VOLUME SURGE",
                         f"{vol_ratio:.1f}x 20-day avg (normalised)"))
    elif vol_ratio >= VOL_T1:
        vp = 4
        signals.append(("🔊", "ELEVATED VOLUME",
                         f"{vol_ratio:.1f}x 20-day avg (normalised)"))
    score += vp
    breakdown["volume"] = vp

    # ── 5. Convergence Bonus  (max 5 pts) ─────────────────
    cp = 0
    if del_available and delivery_pct >= DEL_T2 and vol_ratio >= VOL_T2:
        cp = 5
        signals.append(("🎯", "DUAL CONVICTION",
                         "High delivery + high volume — cash & F&O aligned"))
    score += cp
    breakdown["convergence"] = cp

    # ── 6. Near Day-High Bonus  (max 5 pts) ───────────────
    hp = 0
    pct_from_high = ((day_high - last_price) / day_high) * 100 if day_high > 0 else 999
    if pct_from_high <= 1.5:
        hp = 5
        signals.append(("🏔️", "HOLDING DAY HIGH",
                         f"Price within {pct_from_high:.1f}% of high — breakout intact"))
    score += hp
    breakdown["near_high"] = hp

    # ── 7. 52-Week High Bonus  (max 5 pts) ────────────────
    wp = 0
    pct_from_52w = 999
    if week_52_high > 0:
        pct_from_52w = ((week_52_high - last_price) / week_52_high) * 100
        if pct_from_52w <= NEAR_52W_HIGH_PCT:
            wp = 5
            signals.append(("🌟", "52-WEEK HIGH ZONE",
                             f"Within {pct_from_52w:.1f}% of 52-week high"))
    score += wp
    breakdown["near_52w"]     = wp
    breakdown["pct_from_52w"] = pct_from_52w

    breakdown["total"]     = score
    breakdown["vol_ratio"] = vol_ratio
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
        # Step 1: Price data (YF)
        price_data = fetch_price(symbol, yf_override)
        if not price_data:
            return None

        # Step 2: FnO data (NSE)
        fno_data = fetch_nse_fno(symbol)

        # Step 3: Hard filters
        passed, reason = passes_hard_filters(symbol, price_data, fno_data)
        if not passed:
            log(f"⛔ {symbol}: filtered — {reason}")
            return None

        # Step 4: Equity data — delivery + 52w high (NSE)
        equity_data = fetch_nse_equity(symbol)

        # Merge 52w high into price_data for alert card
        price_data["week_52_high"] = equity_data.get("week_52_high", 0.0)

        # Step 5: Score
        stock_score, breakdown, signals = score_stock(
            price_data, fno_data, equity_data
        )

        del_pct = equity_data.get("delivery_pct")
        del_str = f"{del_pct:.0f}%" if del_pct is not None else "N/A"

        log(
            f"📊 {symbol} | +{price_data['move_pct']:.2f}% | "
            f"OI={fno_data.get('oi_chg_pct', 0):+.1f}% | "
            f"Del={del_str} | "
            f"Vol={breakdown.get('vol_ratio', 0):.1f}x | "
            f"Score={stock_score}"
        )

        return {
            "symbol":       symbol,
            "sector_label": sector_label,
            "score":        stock_score,
            "breakdown":    breakdown,
            "signals":      signals,
            "price_data":   price_data,
            "fno_data":     fno_data,
            "equity_data":  equity_data,
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
    ed   = result["equity_data"]
    bd   = result["breakdown"]
    sigs = result["signals"]

    sym          = pd_["symbol"]
    price        = pd_["price"]
    move_pct     = pd_["move_pct"]
    day_open     = pd_["day_open"]
    day_high     = pd_["day_high"]
    day_low      = pd_["day_low"]
    intra_vol    = pd_["intra_vol"]
    norm_vol     = pd_["norm_vol"]
    avg_volume   = pd_["avg_volume"]
    five_day_avg = pd_["five_day_avg"]
    week_52_high = pd_.get("week_52_high", 0)

    oi           = fd.get("oi", 0)
    oi_chg       = fd.get("oi_chg", 0)
    oi_chg_pct   = fd.get("oi_chg_pct", 0)
    lot_size     = fd.get("lot_size", 0)

    delivery_pct = ed.get("delivery_pct")
    del_available = delivery_pct is not None
    vol_ratio     = bd.get("vol_ratio", 0)

    pct_from_high = ((day_high - price) / day_high) * 100 if day_high > 0 else 0
    pct_from_52w  = ((week_52_high - price) / week_52_high) * 100 if week_52_high > 0 else 0

    # OI direction label: negative oi_chg_pct = unwinding
    oi_dir   = "↓ Unwinding" if oi_chg_pct < 0 else "↑ Building"
    fno_type = bd.get("fno_type", "")
    ist_now  = datetime.now(IST).strftime("%d %b %Y  %H:%M:%S IST")

    # Signals block
    sig_lines = "\n".join(
        f"  {e} <b>{n}</b>: {d}" for e, n, d in sigs
    ) or "  📊 Base momentum criteria met"

    # Score breakdown line
    del_note = "" if del_available else " (N/A)"
    breakdown_line = (
        f"Price +{bd.get('price',0)} | "
        f"FnO +{bd.get('fno',0)} | "
        f"Del +{bd.get('delivery',0)}{del_note} | "
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
        f"  ⬆️ Day High*:    ₹{day_high:.2f}  ({pct_from_high:.1f}% from now)\n"
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

    if del_available:
        msg += f"  📦 Delivery:     {delivery_pct:.1f}%\n"
    else:
        msg += "  📦 Delivery:     N/A (NSE data unavailable)\n"

    if avg_volume > 0:
        msg += (
            f"  🔊 Intraday Vol: {intra_vol:,}  (raw)\n"
            f"  📊 Norm. Vol:    {norm_vol:,}  (day-equiv)\n"
            f"  📏 20-Day Avg:   {avg_volume:,}\n"
            f"  📊 Vol Ratio:    {vol_ratio:.2f}x\n"
        )
    else:
        msg += f"  🔊 Volume:       {intra_vol:,} (avg unavailable)\n"

    msg += (
        f"\n━━ 🎯 WHY THIS PICK ━━\n"
        f"{sig_lines}\n"
        f"\n"
        f"  * Day High is smoothed (3-candle max) to filter wicks\n"
        f"  * Volume ratio uses time-normalised intraday volume\n"
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
    get_nse_session()  # pre-warm session

    raw_results = []

    # Sequential loop — no ThreadPoolExecutor overhead at MAX_WORKERS=1
    for sym, sector, yf_ov in WATCHLIST:
        result = process_stock(sym, sector, yf_ov)
        if result:
            raw_results.append(result)

    log(f"📦 Stocks passing hard filters: {len(raw_results)}")

    candidates = [r for r in raw_results if r["score"] >= MIN_SCORE]

    log(f"🏆 Candidates above score gate (≥{MIN_SCORE}): {len(candidates)}")

    if not candidates:
        log("💤 No qualifying picks this run — no alerts sent")
        return

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
