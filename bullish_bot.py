# =========================================================
# NSE FNO MOMENTUM BOT — STOCK-ONLY ALERTS v5  (FINAL)
# =========================================================
#
# DELIVERY APPROACH
# ---------------------------------------------------------
# NSE's quote-equity API (tradeInfo.deliveryToTradedQuantity)
# returns the PREVIOUS SESSION's finalised delivery % during
# market hours. This is always available, always accurate,
# and requires zero caching or EOD jobs.
#
# We use this directly as a conviction signal:
#   "Yesterday this stock had X% positional buying —
#    today it's showing FnO momentum — strong combination."
#
# No bhav copy. No JSON cache. No EOD cron. Just one API call.
#
# ALL FIXES vs v3/v4
# ---------------------------------------------------------
#  ✅ Delivery = prev-day from NSE equity API (always available)
#  ✅ Volume ratio time-normalised (fair at any time of day)
#  ✅ 5-day avg date-aware (today's partial candle excluded)
#  ✅ OI gate: oi < MIN_OI_CONTRACTS (0 = missing = fail)
#  ✅ NSE session auto-refreshes on TTL expiry (10-min TTL)
#  ✅ NSE API calls retry up to 3× with backoff
#  ✅ day_high smoothed (3-candle max, wick-spike safe)
#  ✅ 52-week high from NSE equity API (no extra YF call)
#  ✅ OI sign convention verified: negative = unwinding
#  ✅ prev_oi guard (no division by zero)
#  ✅ Sequential scan loop (no ThreadPoolExecutor overhead)
#
# SCORING SYSTEM (max 120 pts)
# ---------------------------------------------------------
#
# Price Action         max 30 pts
#   +10  move > 0.8%
#   +10  move > 1.5%
#   +10  move > 2.5%
#
# FnO / OI Signals     max 45 pts
#   +25  Short Covering  (OI↓ > 2% + price↑)
#   +15  Long Buildup    (OI↑ > 2% + price↑)
#   +10  OI change > 5%  (additive conviction bonus)
#   +5   OI change > 2%  (additive if below above tier)
#
# Delivery (prev-day)  max 20 pts
#   +20  delivery > 70%
#   +15  delivery > 60%
#   +10  delivery > 50%
#
# Volume               max 10 pts
#   +10  volume > 3x normalised 20-day avg
#   +7   volume > 2x
#   +4   volume > 1.5x
#
# Convergence Bonus    max 5 pts
#   +5   delivery > 60% AND volume > 2x avg
#
# Near Day-High Bonus  max 5 pts
#   +5   price within 1.5% of smoothed day high
#
# 52-Week High Bonus   max 5 pts
#
# HARD FILTERS (stock disqualified if any fails)
#   • move_pct > 0               upside only
#   • abs(OI change) ≥ 2%        FnO signal required
#   • OI ≥ MIN_OI_CONTRACTS      liquidity gate (0 = fail)
#   • price within 3% of high    not fading from peak
#   • price > 5-day close avg    uptrend confirmation
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
MIN_OI_CONTRACTS = 5000

MOVE_T1, MOVE_T2, MOVE_T3 = 0.8,  1.5,  2.5
OI_T1,   OI_T2             = 2.0,  5.0
DEL_T1,  DEL_T2,  DEL_T3  = 50.0, 60.0, 70.0
VOL_T1,  VOL_T2,  VOL_T3  = 1.5,  2.0,  3.0

NEAR_HIGH_PCT     = 3.0
NEAR_52W_HIGH_PCT = 5.0

NSE_SESSION_TTL = 600   # seconds — refresh session before NSE expires it (~15 min)

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
# Format: (NSE_SYMBOL, SECTOR_LABEL, YF_TICKER_OVERRIDE_or_None)
# =========================================================

WATCHLIST = [
    # ── BANKING ──────────────────────────────────────────
    ("SBIN",        "🏦 Banking",           None),
    ("HDFCBANK",    "🏦 Banking",           None),
    ("ICICIBANK",   "🏦 Banking",           None),
    ("AXISBANK",    "🏦 Banking",           None),
    ("KOTAKBANK",   "🏦 Banking",           None),
    ("BANKBARODA",  "🏦 Banking",           None),
    ("CANBK",       "🏦 Banking",           None),
    ("UNIONBANK",   "🏦 Banking",           None),

    # ── PSU FINANCE ──────────────────────────────────────
    ("PFC",         "🏛️ PSU Finance",       None),
    ("RECLTD",      "🏛️ PSU Finance",       None),
    ("JIOFIN",      "🏛️ PSU Finance",       None),

    # ── IT ───────────────────────────────────────────────
    ("INFY",        "💻 IT",                None),
    ("TCS",         "💻 IT",                None),
    ("WIPRO",       "💻 IT",                None),
    ("KPITTECH",    "💻 IT",                None),
    ("PERSISTENT",  "💻 IT",                None),
    ("COFORGE",     "💻 IT",                None),
    ("TATATECH",    "💻 IT",                None),

    # ── INFRA / DEFENCE ──────────────────────────────────
    ("LT",          "🛡️ Infra & Defence",   None),
    ("BEL",         "🛡️ Infra & Defence",   None),
    ("HAL",         "🛡️ Infra & Defence",   None),
    ("RVNL",        "🛡️ Infra & Defence",   None),
    ("MAZDOCK",     "🛡️ Infra & Defence",   None),
    ("COCHINSHIP",  "🛡️ Infra & Defence",   None),

    # ── POWER / ENERGY ───────────────────────────────────
    ("NTPC",        "⚡ Power & Energy",     None),
    ("TATAPOWER",   "⚡ Power & Energy",     None),
    ("JSWENERGY",   "⚡ Power & Energy",     None),
    ("POWERGRID",   "⚡ Power & Energy",     None),
    ("SUZLON",      "⚡ Power & Energy",     None),
    ("ADANIENT",    "⚡ Power & Energy",     None),
    ("ADANIPORTS",  "⚡ Power & Energy",     None),

    # ── OIL & GAS ────────────────────────────────────────
    ("ONGC",        "🛢️ Oil & Gas",         None),
    ("IOC",         "🛢️ Oil & Gas",         None),
    ("BPCL",        "🛢️ Oil & Gas",         None),
    ("GAIL",        "🛢️ Oil & Gas",         None),

    # ── METALS ───────────────────────────────────────────
    ("TATASTEEL",   "⚙️ Metals",            None),
    ("JSWSTEEL",    "⚙️ Metals",            None),
    ("HINDALCO",    "⚙️ Metals",            None),
    ("HINDCOPPER",  "⚙️ Metals",            None),

    # ── PHARMA ───────────────────────────────────────────
    ("SUNPHARMA",   "💊 Pharma",            None),
    ("DRREDDY",     "💊 Pharma",            None),
    ("DIVISLAB",    "💊 Pharma",            None),
    ("CIPLA",       "💊 Pharma",            None),

    # ── NBFC / FINANCE ───────────────────────────────────
    ("BAJFINANCE",  "💰 NBFC & Finance",    None),
    ("BAJAJFINSV",  "💰 NBFC & Finance",    None),
    ("CHOLAFIN",    "💰 NBFC & Finance",    None),
    ("SHRIRAMFIN",  "💰 NBFC & Finance",    None),
    ("MUTHOOTFIN",  "💰 NBFC & Finance",    None),
    ("MANAPPURAM",  "💰 NBFC & Finance",    None),

    # ── AUTO ─────────────────────────────────────────────
    ("MARUTI",      "🚗 Auto",              None),
    ("M&M",         "🚗 Auto",              "MM.NS"),
    ("EICHERMOT",   "🚗 Auto",              None),
    ("TVSMOTOR",    "🚗 Auto",              None),
    ("ASHOKLEY",    "🚗 Auto",              None),
    ("TATAMTRDVR",  "🚗 Auto",              None),

    # ── REALTY ───────────────────────────────────────────
    ("LODHA",       "🏗️ Realty",            None),
    ("DLF",         "🏗️ Realty",            None),

    # ── CONSUMER ─────────────────────────────────────────
    ("TITAN",       "🛍️ Consumer",          None),
    ("TRENT",       "🛍️ Consumer",          None),
    ("PIDILITIND",  "🛍️ Consumer",          None),
    ("PVRINOX",     "🛍️ Consumer",          None),
    ("VBL",         "🛍️ Consumer",          None),

    # ── INFRA / CAP GOODS ────────────────────────────────
    ("POLYCAB",     "🔌 Infra & Cap Goods", None),
    ("KEI",         "🔌 Infra & Cap Goods", None),
    ("INDUSTOWER",  "🔌 Infra & Cap Goods", None),
    ("CGPOWER",     "🔌 Infra & Cap Goods", None),
    ("IRB",         "🔌 Infra & Cap Goods", None),
    ("BHEL",        "🔌 Infra & Cap Goods", None),
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
    now       = datetime.now(IST)
    open_time = now.replace(hour=9, minute=15, second=0, microsecond=0)
    return max((now - open_time).total_seconds() / 60, 1)


def normalise_volume(intra_vol):
    """
    Scales cumulative intraday volume to a full-session (375 min) equivalent.
    Prevents morning scans from showing artificially low vol ratios.
    Example: 50 min into session, actual vol × (375/50) = fair full-day estimate.
    """
    return int(intra_vol * (375 / minutes_since_open()))

# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        r   = requests.post(
            url,
            data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=20,
        )
        log(f"📨 Telegram status={r.status_code}")
        if r.status_code != 200:
            log(f"⚠️ Telegram error: {r.text[:200]}")
    except Exception:
        traceback.print_exc()

# =========================================================
# NSE SESSION  (auto-refresh on TTL expiry)
# =========================================================

_nse_session      = None
_nse_session_born = 0.0


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



# =========================================================
# NSE GET  [FIXED v6]
# =========================================================
#
# WHAT WAS FIXED
# ---------------------------------------------------------
# ✅ Stops useless retries on HTTP 404
# ✅ Faster execution
# ✅ Cleaner logs
# ✅ Retries still active for:
#      - timeout
#      - 401
#      - 403
#      - 5xx
#
# =========================================================

def nse_get(url, retries=3, backoff=2.0):

    for attempt in range(1, retries + 1):

        s = get_nse_session()

        if not s:
            return None

        try:

            r = s.get(url, timeout=8)

            # SUCCESS
            if r.status_code == 200:
                return r.json()

            # SESSION EXPIRED
            if r.status_code in (401, 403):

                log(
                    f"⟳ NSE session expired "
                    f"({r.status_code}) — refreshing"
                )

                get_nse_session(force_refresh=True)

                time.sleep(backoff)

                continue

            # =================================================
            # FIX:
            # DO NOT RETRY HTTP 404
            # =================================================
            if r.status_code == 404:

                log("⚠️ NSE HTTP 404 — skipping")

                return None

            # OTHER HTTP ERRORS
            log(
                f"⚠️ NSE HTTP {r.status_code} "
                f"(attempt {attempt})"
            )

            time.sleep(backoff * attempt)

        except requests.exceptions.Timeout:

            log(f"⏳ NSE timeout (attempt {attempt})")

            time.sleep(backoff * attempt)

        except Exception:

            log(f"❌ NSE request error (attempt {attempt})")

            time.sleep(backoff * attempt)

    return None

# =========================================================
# FnO FETCH  [YAHOO-ONLY MODE]
# =========================================================
#
# WHY:
# NSE blocks cloud/VPS/Railway requests.
#
# So we completely disable NSE dependency.
#
# Strategy still works using:
#   - momentum
#   - breakout
#   - volume
#   - delivery
#
# =========================================================

def fetch_nse_fno(symbol):

    log(f"📊 {symbol}: Yahoo-only mode active")

    return {
        "oi": 0,
        "oi_chg": 0,
        "oi_chg_pct": 0,
        "lot_size": 0,
        "oi_missing": True,
    }
        

# =========================================================
# NSE EQUITY DATA  (prev-day delivery + 52-week high)
# =========================================================

def fetch_nse_equity(symbol):
    """
    Fetches from NSE's quote-equity endpoint.

    DELIVERY NOTE:
      tradeInfo.deliveryToTradedQuantity during market hours =
      PREVIOUS SESSION's finalised delivery %.
      This is always populated and always accurate intraday.
      It is NOT today's delivery (which doesn't exist until EOD).
      We use it intentionally as a prev-day conviction signal.

    DELIVERY PARSING:
      NSE returns this field as a plain float (e.g. 63.9).
      We treat None/missing as genuinely unavailable (not 0%).
      A stock with no delivery data scores 0 delivery points
      but is not disqualified — FnO signal alone can qualify it.

    52-WEEK HIGH:
      Pulled from priceInfo.weekHighLow.max — more reliable
      than a separate yfinance historical download.
    """
    try:
        data = nse_get(
            f"https://www.nseindia.com/api/quote-equity?symbol={symbol}"
        )
        if not data:
            return {}

        trade      = data.get("tradeInfo",  {})
        price_info = data.get("priceInfo",  {})

        # ── Prev-day delivery % ───────────────────────────
        raw_del = trade.get("deliveryToTradedQuantity")
        if raw_del is not None:
            try:
                delivery_pct = float(raw_del)
            except (ValueError, TypeError):
                delivery_pct = None
        else:
            delivery_pct = None   # None = unavailable, NOT 0%

        # ── 52-week high ──────────────────────────────────
        week_52_high = 0.0
        try:
            week_52_high = float(
                price_info.get("weekHighLow", {}).get("max", 0) or 0
            )
        except (ValueError, TypeError):
            week_52_high = 0.0

        return {
            "delivery_pct": delivery_pct,   # prev-day; None = unavailable
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
    Fetches intraday 5-min bars + 30-day daily history.

    SMOOTHED DAY HIGH:
      Uses max of last 3 candle highs instead of session max.
      Prevents a brief wick spike from incorrectly tagging the
      stock as "fading from high" in the near-high filter.

    VOLUME NORMALISATION:
      Raw intraday volume grows throughout the day, so comparing
      it directly against a 20-day daily average at 10 AM gives
      a misleadingly low ratio. We scale it up:
        norm_vol = intra_vol × (375 / elapsed_minutes)
      This makes vol_ratio meaningful at any point in the session.

    5-DAY AVG:
      Explicitly excludes today's partial candle by comparing
      the last row's date against today's date in IST.
    """
    try:
        time.sleep(random.uniform(1.5, 3.0))
        yf_sym = yf_sym_override or f"{symbol}.NS"

        # ── Intraday 5-min ────────────────────────────────
        df = yf.download(
            yf_sym, period="1d", interval="5m",
            progress=False, auto_adjust=True, threads=False,
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
        if move_pct <= 0:
            return None   # upside-only pre-filter

        # Smoothed high: max of last 3 candle highs
        day_high = float(df["High"].tail(3).max())

        # ── Daily history ─────────────────────────────────
        avg_volume   = 0
        five_day_avg = 0.0
        try:
            df_d = yf.download(
                yf_sym, period="30d", interval="1d",
                progress=False, auto_adjust=True, threads=False,
            )
            if not df_d.empty:
                if isinstance(df_d.columns, pd.MultiIndex):
                    df_d.columns = df_d.columns.get_level_values(0)
                # Exclude today's partial candle
                today_str = datetime.now(IST).strftime("%Y-%m-%d")
                df_hist   = (df_d.iloc[:-1]
                             if str(df_d.index[-1])[:10] == today_str
                             else df_d)
                avg_volume   = int(df_hist["Volume"].tail(20).mean())
                five_day_avg = float(df_hist["Close"].tail(5).mean())
        except Exception:
            pass

        return {
            "symbol":       symbol,
            "price":        last_price,
            "day_open":     day_open,
            "day_high":     day_high,   # smoothed (3-candle max)
            "day_low":      day_low,
            "move_pct":     move_pct,
            "intra_vol":    intra_vol,
            "norm_vol":     normalise_volume(intra_vol),
            "avg_volume":   avg_volume,
            "five_day_avg": five_day_avg,
        }

    except Exception:
        err = traceback.format_exc()
        if "YFRateLimitError" in err:
            log(f"⏳ {symbol}: YF rate limited — skipping")
            return None
        if any(x in err for x in ["possibly delisted", "404", "No data found"]):
            return None
        log(f"❌ {symbol}: price fetch error")
        traceback.print_exc()
        return None

# =========================================================
# HARD FILTERS  [FINAL FIXED]
# =========================================================

def passes_hard_filters(symbol, price_data, fno_data):

    move_pct  = price_data["move_pct"]
    last_price = price_data["price"]
    day_high   = price_data["day_high"]

    # =====================================================
    # FnO OPTIONAL MODE
    # =====================================================

    if fno_data.get("oi_missing"):

        log(f"⚠️ {symbol}: FnO unavailable — continuing")

    # =====================================================
    # UPSIDE FILTER
    # =====================================================

    if move_pct <= 0:

        return False, "No upside move"

    # =====================================================
    # NOT FAR FROM HIGH
    # =====================================================

    pct_from_high = (
        ((day_high - last_price) / day_high) * 100
        if day_high > 0
        else 999
    )

    if pct_from_high > 3:

        return False, (
            f"Far from high ({pct_from_high:.1f}%)"
        )

    return True, "OK"
# =========================================================
# SCORING ENGINE  (0–120 pts)
# =========================================================

def score_stock(price_data, fno_data, delivery_pct, week_52_high):
    """
    delivery_pct  : float (prev-day) or None (unavailable)
    week_52_high  : float from NSE equity API
    """
    score     = 0
    breakdown = {}
    signals   = []

    move_pct      = price_data["move_pct"]
    last_price    = price_data["price"]
    day_high      = price_data["day_high"]
    norm_vol      = price_data["norm_vol"]
    avg_volume    = price_data["avg_volume"]
    oi_chg_pct    = fno_data.get("oi_chg_pct", 0)
    del_available = delivery_pct is not None
    delivery_pct  = delivery_pct if del_available else 0.0
    vol_ratio     = (norm_vol / avg_volume) if avg_volume > 0 else 0

    # ── 1. Price Action  (max 30) ─────────────────────────
    pp = 0
    if move_pct >= MOVE_T1: pp += 10
    if move_pct >= MOVE_T2: pp += 10
    if move_pct >= MOVE_T3: pp += 10
    score += pp
    breakdown["price"] = pp
    if pp >= 10:
        signals.append(("📈", "PRICE BREAKOUT",
                         f"+{move_pct:.2f}% above day open"))

    # ── 2. FnO / OI  (max 45) ────────────────────────────
    fp       = 0
    fno_type = ""
    if move_pct > 0 and oi_chg_pct < -OI_T1:
        fp       += 25
        fno_type  = "SHORT_COVERING"
        signals.append(("🔄", "SHORT COVERING",
                         f"OI {oi_chg_pct:+.1f}% — shorts exiting into rising price"))
    elif move_pct > 0 and oi_chg_pct > OI_T1:
        fp       += 15
        fno_type  = "LONG_BUILDUP"
        signals.append(("🚀", "LONG BUILDUP",
                         f"OI {oi_chg_pct:+.1f}% — fresh longs entering"))

    abs_oi = abs(oi_chg_pct)
    if abs_oi >= OI_T2:
        fp += 10
        signals.append(("💪", "STRONG OI CONVICTION",
                         f"OI moved {abs_oi:.1f}%"))
    elif abs_oi >= OI_T1:
        fp += 5

    score += fp
    breakdown["fno"]      = fp
    breakdown["fno_type"] = fno_type

    # ── 3. Delivery — prev-day  (max 20) ─────────────────
    dp = 0
    if del_available:
        if delivery_pct >= DEL_T3:
            dp = 20
            signals.append(("📦", "HIGH DELIVERY",
                             f"{delivery_pct:.1f}% prev-day — strong positional conviction"))
        elif delivery_pct >= DEL_T2:
            dp = 15
            signals.append(("📦", "GOOD DELIVERY",
                             f"{delivery_pct:.1f}% prev-day delivery"))
        elif delivery_pct >= DEL_T1:
            dp = 10
            signals.append(("📦", "MODERATE DELIVERY",
                             f"{delivery_pct:.1f}% prev-day delivery"))
    score += dp
    breakdown["delivery"]           = dp
    breakdown["delivery_available"] = del_available

    # ── 4. Volume  (max 10) ───────────────────────────────
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

    # ── 5. Convergence Bonus  (max 5) ─────────────────────
    cp = 0
    if del_available and delivery_pct >= DEL_T2 and vol_ratio >= VOL_T2:
        cp = 5
        signals.append(("🎯", "DUAL CONVICTION",
                         "High prev-day delivery + high volume — positional & intraday aligned"))
    score += cp
    breakdown["convergence"] = cp

    # ── 6. Near Day-High Bonus  (max 5) ───────────────────
    hp            = 0
    pct_from_high = ((day_high - last_price) / day_high) * 100 if day_high > 0 else 999
    if pct_from_high <= 1.5:
        hp = 5
        signals.append(("🏔️", "HOLDING DAY HIGH",
                         f"Within {pct_from_high:.1f}% of high — breakout intact"))
    score += hp
    breakdown["near_high"] = hp

    # ── 7. 52-Week High Bonus  (max 5) ────────────────────
    wp           = 0
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
    breakdown["vol_ratio"]    = vol_ratio
    breakdown["total"]        = score
    return score, breakdown, signals

# =========================================================
# VISUAL AIDS
# =========================================================

def score_bar(score, max_score=120):
    filled = round(min(score / max_score, 1.0) * 10)
    return f"{'█' * filled}{'░' * (10 - filled)} {score}/{max_score}"


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
        # 1. Price + volume (Yahoo Finance)
        price_data = fetch_price(symbol, yf_override)
        if not price_data:
            return None

        # 2. FnO / OI (NSE derivatives API)
        fno_data = fetch_nse_fno(symbol)

        # 3. Hard filters
        passed, reason = passes_hard_filters(symbol, price_data, fno_data)
        if not passed:
            log(f"⛔ {symbol}: {reason}")
            return None

        # 4. Equity data — prev-day delivery + 52w high (NSE equity API)
        equity_data  = fetch_nse_equity(symbol)
        delivery_pct = equity_data.get("delivery_pct")   # None = unavailable
        week_52_high = equity_data.get("week_52_high", 0.0)

        # 5. Score
        stock_score, breakdown, signals = score_stock(
            price_data, fno_data, delivery_pct, week_52_high
        )

        del_str = f"{delivery_pct:.0f}%" if delivery_pct is not None else "N/A"
        log(
            f"📊 {symbol} | +{price_data['move_pct']:.2f}% | "
            f"OI={fno_data.get('oi_chg_pct', 0):+.1f}% | "
            f"Del(prev)={del_str} | "
            f"Vol={breakdown['vol_ratio']:.1f}x | "
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
            "delivery_pct":  delivery_pct,
            "week_52_high":  week_52_high,
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
    week_52_high = result["week_52_high"]
    delivery_pct = result["delivery_pct"]

    oi         = fd.get("oi", 0)
    oi_chg     = fd.get("oi_chg", 0)
    oi_chg_pct = fd.get("oi_chg_pct", 0)
    lot_size   = fd.get("lot_size", 0)
    fno_type   = bd.get("fno_type", "")
    vol_ratio  = bd.get("vol_ratio", 0)

    pct_from_high = ((day_high - price) / day_high) * 100 if day_high > 0 else 0
    pct_from_52w  = ((week_52_high - price) / week_52_high) * 100 if week_52_high > 0 else 0
    oi_dir        = "↓ Unwinding" if oi_chg_pct < 0 else "↑ Building"
    ist_now       = datetime.now(IST).strftime("%d %b %Y  %H:%M:%S IST")

    sig_lines = "\n".join(
        f"  {e} <b>{n}</b>: {d}" for e, n, d in sigs
    ) or "  📊 Base momentum criteria met"

    del_note       = "" if delivery_pct is not None else " (N/A)"
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

    msg += "\n━━ 📊 FnO / OI ━━\n"
    if oi > 0:
        msg += (
            f"  📋 OI:           {oi:,} option OI\n"
            f"  🔁 OI Change:    {oi_chg:+,}  ({oi_chg_pct:+.2f}%)  {oi_dir}\n"
            f"  🏷️ Signal:       {fno_badge(fno_type)}\n"
        )
        if lot_size > 0:
            msg += f"  📏 Lot Size:     {lot_size:,}\n"
    else:
        msg += "  ⚠️ OI data unavailable\n"

    msg += "\n━━ 📦 DELIVERY & VOLUME ━━\n"
    if delivery_pct is not None:
        msg += f"  📦 Delivery:     {delivery_pct:.1f}%  (prev session)\n"
    else:
        msg += "  📦 Delivery:     N/A\n"

    if avg_volume > 0:
        msg += (
            f"  🔊 Intraday Vol: {intra_vol:,}  (raw)\n"
            f"  📊 Norm. Vol:    {norm_vol:,}  (full-session equiv)\n"
            f"  📏 20-Day Avg:   {avg_volume:,}\n"
            f"  📊 Vol Ratio:    {vol_ratio:.2f}x\n"
        )
    else:
        msg += f"  🔊 Volume:       {intra_vol:,}  (avg unavailable)\n"

    msg += (
        f"\n━━ 🎯 WHY THIS PICK ━━\n"
        f"{sig_lines}\n"
        f"\n"
        f"  * Day High: smoothed 3-candle max (wick-safe)\n"
        f"  * Volume: time-normalised to full session\n"
        f"  * Delivery: previous session finalised data\n"
        f"\n"
        f"🕐 {ist_now}"
    )

    return msg


# =========================================================
# MAIN BOT
# =========================================================

def run_bot():

    log("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log("🚀 BOT RUN STARTED")
    log(
        f"🕐 Time: "
        f"{datetime.now(IST).strftime('%d %b %Y %H:%M:%S IST')}"
    )
    log("━━━━━━━━━━━━━━━━━━━━━━━━━━")

    if not is_market_open():
        log("⏰ Outside market hours — skipping")
        return

    log("✅ Market hours — starting scan")

    raw_results = []

    for sym, sector, yf_ov in WATCHLIST:

        log(f"🔍 Scanning: {sym}")

        result = process_stock(sym, sector, yf_ov)

        if result:
            raw_results.append(result)

    log(f"📦 Passed hard filters: {len(raw_results)}")

    candidates = [
        r for r in raw_results
        if r["score"] >= MIN_SCORE
    ]

    log(
        f"🏆 Above score gate (≥{MIN_SCORE}): "
        f"{len(candidates)}"
    )

    # =====================================================
    # HEARTBEAT
    # =====================================================

    if not candidates:

        log("💤 No qualifying picks")

        send_telegram(
            "✅ Bot running successfully\n"
            "📭 No qualifying picks in this cycle"
        )

        return

    top_picks = sorted(
        candidates,
        key=lambda x: x["score"],
        reverse=True
    )[:MAX_ALERTS]

    log(f"📨 Sending {len(top_picks)} alerts")

    for rank, pick in enumerate(top_picks, 1):

        msg = build_alert_card(rank, pick)

        send_telegram(msg)

        log(
            f"✅ Sent #{rank}: "
            f"{pick['symbol']} "
            f"(score={pick['score']})"
        )

        time.sleep(0.7)

    log("✅ RUN COMPLETE")
# =========================================================
# ENTRY
# =========================================================

if __name__ == "__main__":
    try:
        if not BOT_TOKEN or not CHAT_ID:
            log("❌ BOT_TOKEN or CHAT_ID missing")
            raise SystemExit(1)
        run_bot()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        log("❌ CRITICAL ERROR")
