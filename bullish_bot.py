# =========================================================
# ADVANCED NSE MOMENTUM + FNO TELEGRAM BOT
# SECTOR-AWARE SCORING EDITION
# =========================================================
#
# WHAT'S NEW vs v2
# ---------------------------------------------------------
#
# ✅ 10 sectors defined — each stock tagged to a sector
# ✅ Sector score computed per run (breadth + avg move)
# ✅ Sector bonus (0–30 pts) added to each stock's score
# ✅ Sector pulse message sent first (which sectors are hot)
# ✅ Each stock alert shows its sector + sector context
# ✅ Sector breadth tracked: "3/8 Banking stocks signalling"
# ✅ Only upside signals, top 5 picks, min score gate
#
# SCORING SYSTEM (max 130 pts = 100 stock + 30 sector bonus)
# ---------------------------------------------------------
#
# STOCK SCORE (0–100)
#
#   Price Action      max 30 pts
#     +10  move > 0.8%
#     +10  move > 1.5%
#     +10  move > 2.5%
#
#   FnO Signals       max 40 pts
#     +20  Short Covering  (OI↓ + Price↑)  ← strongest
#     +15  Long Buildup    (OI↑ + Price↑)
#     +10  OI change > 5%  (strong conviction)
#     +5   OI change > 2%  (moderate)
#
#   Delivery          max 20 pts
#     +20  delivery > 70%
#     +15  delivery > 60%
#     +10  delivery > 50%
#
#   Volume            max 10 pts
#     +10  volume > 3x avg
#     +7   volume > 2x avg
#     +4   volume > 1.5x avg
#
# SECTOR BONUS (0–30 pts, added to stock score)
#
#   Sector breadth (stocks signalling / sector size)
#     ≥ 60% of sector stocks signalling  →  +15 pts
#     ≥ 40%                              →  +10 pts
#     ≥ 25%                              →  +5  pts
#
#   Sector avg move
#     avg move > 1.5%  →  +10 pts
#     avg move > 0.8%  →  +5  pts
#
#   Sector OI confirmation
#     majority short covering in sector  →  +5 pts
#
# ALERT GATE
#   Min score (after sector bonus) = MIN_SCORE (default 45)
#   Max alerts per run = MAX_ALERTS (default 5)
#
# TELEGRAM MESSAGE FLOW
#   1. Sector pulse — hot/warm/quiet sectors at a glance
#   2. Top N stock picks — detailed cards with sector context
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
from collections import defaultdict

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

MIN_SCORE   = 45    # Minimum adjusted score (stock + sector bonus)
MAX_ALERTS  = 5     # Top N picks to send

# Price move thresholds (vs day open, upside only)
MOVE_T1, MOVE_T2, MOVE_T3 = 0.8, 1.5, 2.5

# OI change % thresholds
OI_T1, OI_T2 = 2.0, 5.0

# Delivery % thresholds
DEL_T1, DEL_T2, DEL_T3 = 50.0, 60.0, 70.0

# Volume vs 20-day avg
VOL_T1, VOL_T2, VOL_T3 = 1.5, 2.0, 3.0

# Sector breadth thresholds (fraction of sector stocks signalling)
BREADTH_T1, BREADTH_T2, BREADTH_T3 = 0.25, 0.40, 0.60

# Sector avg move thresholds
SECT_MOVE_T1, SECT_MOVE_T2 = 0.8, 1.5

MAX_WORKERS = 1     # Keep 1 to avoid Yahoo rate limits

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
# SECTORS & WATCHLIST
# =========================================================
#
# Format: (NSE_SYMBOL, SECTOR, YF_OVERRIDE_or_None)
#
# Sectors:
#   BANKING         — private + public banks
#   PSU_FINANCE     — PFC, REC, JIOFIN etc.
#   IT              — software
#   INFRA_DEFENCE   — BEL, HAL, L&T, RVNL, MAZDOCK, COCHINSHIP
#   POWER_ENERGY    — NTPC, TATAPOWER, JSWENERGY, POWERGRID, SUZLON
#   OIL_GAS         — ONGC, IOC, BPCL, GAIL
#   METALS          — TATASTEEL, JSWSTEEL, HINDALCO, HINDCOPPER
#   PHARMA          — SUNPHARMA, DRREDDY, DIVISLAB, CIPLA
#   NBFC_FINANCE    — BAJFINANCE, BAJAJFINSV, CHOLAFIN etc.
#   AUTO            — MARUTI, M&M, EICHERMOT, TVSMOTOR, ASHOKLEY
#   REALTY          — LODHA, DLF
#   CONSUMER        — TITAN, TRENT, PIDILITIND, PVRINOX, VBL
#   INFRA_MISC      — POLYCAB, KEI, INDUSTOWER, CGPOWER, IRB

WATCHLIST = [
    # ── BANKING ─────────────────────────────────────────
    ("SBIN",       "BANKING",       None),
    ("HDFCBANK",   "BANKING",       None),
    ("ICICIBANK",  "BANKING",       None),
    ("AXISBANK",   "BANKING",       None),
    ("KOTAKBANK",  "BANKING",       None),
    ("BANKBARODA", "BANKING",       None),
    ("CANBK",      "BANKING",       None),
    ("UNIONBANK",  "BANKING",       None),

    # ── PSU FINANCE ─────────────────────────────────────
    ("PFC",        "PSU_FINANCE",   None),
    ("RECLTD",     "PSU_FINANCE",   None),
    ("JIOFIN",     "PSU_FINANCE",   None),

    # ── IT ──────────────────────────────────────────────
    ("INFY",       "IT",            None),
    ("TCS",        "IT",            None),
    ("WIPRO",      "IT",            None),
    ("KPITTECH",   "IT",            None),
    ("PERSISTENT", "IT",            None),
    ("COFORGE",    "IT",            None),
    ("TATATECH",   "IT",            None),

    # ── INFRA / DEFENCE ─────────────────────────────────
    ("LT",         "INFRA_DEFENCE", None),
    ("BEL",        "INFRA_DEFENCE", None),
    ("HAL",        "INFRA_DEFENCE", None),
    ("RVNL",       "INFRA_DEFENCE", None),
    ("MAZDOCK",    "INFRA_DEFENCE", None),
    ("COCHINSHIP", "INFRA_DEFENCE", None),

    # ── POWER / ENERGY ──────────────────────────────────
    ("NTPC",       "POWER_ENERGY",  None),
    ("TATAPOWER",  "POWER_ENERGY",  None),
    ("JSWENERGY",  "POWER_ENERGY",  None),
    ("POWERGRID",  "POWER_ENERGY",  None),
    ("SUZLON",     "POWER_ENERGY",  None),
    ("ADANIENT",   "POWER_ENERGY",  None),
    ("ADANIPORTS", "POWER_ENERGY",  None),

    # ── OIL & GAS ───────────────────────────────────────
    ("ONGC",       "OIL_GAS",       None),
    ("IOC",        "OIL_GAS",       None),
    ("BPCL",       "OIL_GAS",       None),
    ("GAIL",       "OIL_GAS",       None),

    # ── METALS ──────────────────────────────────────────
    ("TATASTEEL",  "METALS",        None),
    ("JSWSTEEL",   "METALS",        None),
    ("HINDALCO",   "METALS",        None),
    ("HINDCOPPER", "METALS",        None),

    # ── PHARMA ──────────────────────────────────────────
    ("SUNPHARMA",  "PHARMA",        None),
    ("DRREDDY",    "PHARMA",        None),
    ("DIVISLAB",   "PHARMA",        None),
    ("CIPLA",      "PHARMA",        None),

    # ── NBFC / FINANCE ──────────────────────────────────
    ("BAJFINANCE",  "NBFC_FINANCE", None),
    ("BAJAJFINSV",  "NBFC_FINANCE", None),
    ("CHOLAFIN",    "NBFC_FINANCE", None),
    ("SHRIRAMFIN",  "NBFC_FINANCE", None),
    ("MUTHOOTFIN",  "NBFC_FINANCE", None),
    ("MANAPPURAM",  "NBFC_FINANCE", None),

    # ── AUTO ────────────────────────────────────────────
    ("MARUTI",     "AUTO",          None),
    ("M&M",        "AUTO",          "MM.NS"),   # & breaks Yahoo
    ("EICHERMOT",  "AUTO",          None),
    ("TVSMOTOR",   "AUTO",          None),
    ("ASHOKLEY",   "AUTO",          None),
    ("TATAMTRDVR", "AUTO",          None),

    # ── REALTY ──────────────────────────────────────────
    ("LODHA",      "REALTY",        None),
    ("DLF",        "REALTY",        None),

    # ── CONSUMER / DISCRETIONARY ────────────────────────
    ("TITAN",      "CONSUMER",      None),
    ("TRENT",      "CONSUMER",      None),
    ("PIDILITIND", "CONSUMER",      None),
    ("PVRINOX",    "CONSUMER",      None),
    ("VBL",        "CONSUMER",      None),

    # ── INFRA MISC ──────────────────────────────────────
    ("POLYCAB",    "INFRA_MISC",    None),
    ("KEI",        "INFRA_MISC",    None),
    ("INDUSTOWER", "INFRA_MISC",    None),
    ("CGPOWER",    "INFRA_MISC",    None),
    ("IRB",        "INFRA_MISC",    None),
    ("BHEL",       "INFRA_MISC",    None),
]

log(f"📊 Watchlist Loaded: {len(WATCHLIST)} stocks across sectors")

# Pre-build sector size map for breadth calculations
SECTOR_SIZES = defaultdict(int)
for _, sector, _ in WATCHLIST:
    SECTOR_SIZES[sector] += 1

# Human-readable sector names for messages
SECTOR_DISPLAY = {
    "BANKING":       "🏦 Banking",
    "PSU_FINANCE":   "🏛️ PSU Finance",
    "IT":            "💻 IT",
    "INFRA_DEFENCE": "🛡️ Infra & Defence",
    "POWER_ENERGY":  "⚡ Power & Energy",
    "OIL_GAS":       "🛢️ Oil & Gas",
    "METALS":        "⚙️ Metals",
    "PHARMA":        "💊 Pharma",
    "NBFC_FINANCE":  "💰 NBFC & Finance",
    "AUTO":          "🚗 Auto",
    "REALTY":        "🏗️ Realty",
    "CONSUMER":      "🛍️ Consumer",
    "INFRA_MISC":    "🔌 Infra & Capital Goods",
}

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
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        r = requests.post(
            url,
            data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=20
        )
        log(f"📨 Telegram={r.status_code}")
        if r.status_code != 200:
            log(f"⚠️ Telegram error: {r.text[:200]}")
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
        log("✅ NSE session initialized")
    except Exception:
        log("⚠️ NSE session init failed")
        _nse_session = None
    return _nse_session

# =========================================================
# NSE FNO DATA
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
        fut    = next(
            (row for row in stocks
             if row.get("metadata", {}).get("instrumentType") == "Stock Futures"),
            None
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
        delivery_pct = trade.get("deliveryToTradedQuantity", 0.0)
        try:
            delivery_pct = float(delivery_pct)
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
# YAHOO FINANCE — PRICE DATA
# =========================================================

def fetch_price(symbol, yf_sym_override=None):
    try:
        time.sleep(random.uniform(1.5, 3.0))

        yf_sym = yf_sym_override or f"{symbol}.NS"

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

        if day_open <= 0:
            return None

        move_pct = ((last_price - day_open) / day_open) * 100

        # UPSIDE ONLY — reject negative/flat
        if move_pct <= 0:
            return None

        # 20-day avg volume
        avg_volume = 0
        try:
            df_d = yf.download(
                yf_sym, period="25d", interval="1d",
                progress=False, auto_adjust=True, threads=False,
            )
            if not df_d.empty:
                if isinstance(df_d.columns, pd.MultiIndex):
                    df_d.columns = df_d.columns.get_level_values(0)
                avg_volume = int(df_d["Volume"].iloc[:-1].tail(20).mean())
        except Exception:
            pass

        return {
            "symbol":     symbol,
            "price":      last_price,
            "day_open":   day_open,
            "day_high":   day_high,
            "day_low":    day_low,
            "move_pct":   move_pct,
            "intra_vol":  intra_vol,
            "avg_volume": avg_volume,
        }

    except Exception:
        err = traceback.format_exc()
        if "YFRateLimitError" in err:
            log(f"⏳ {symbol}: Rate limited")
            return None
        if any(x in err for x in ["possibly delisted", "404", "No data found"]):
            return None
        log(f"❌ {symbol}: fetch error")
        return None

# =========================================================
# STOCK SCORING ENGINE (0–100)
# =========================================================

def score_stock(price_data, fno_data, delivery_data):
    """
    Returns (score 0–100, breakdown dict, signals list).
    All signals are upside-only.
    """
    score     = 0
    breakdown = {}
    signals   = []

    move_pct     = price_data.get("move_pct", 0)
    intra_vol    = price_data.get("intra_vol", 0)
    avg_volume   = price_data.get("avg_volume", 0)
    oi_chg_pct   = fno_data.get("oi_chg_pct", 0)
    delivery_pct = delivery_data.get("delivery_pct", 0)
    vol_ratio    = (intra_vol / avg_volume) if avg_volume > 0 else 0

    # ── Price action (max 30) ─────────────────────────────
    pp = 0
    if move_pct >= MOVE_T1: pp += 10
    if move_pct >= MOVE_T2: pp += 10
    if move_pct >= MOVE_T3: pp += 10
    score += pp
    breakdown["price"] = pp
    if pp > 0:
        signals.append(("📈", "PRICE BREAKOUT", f"+{move_pct:.2f}% above day open"))

    # ── FnO / OI signals (max 40) ─────────────────────────
    fp = 0
    if move_pct > 0 and oi_chg_pct < -OI_T1:
        fp += 20
        signals.append(("🔄", "SHORT COVERING",
                         "Shorts exiting as price rises"))
    elif move_pct > 0 and oi_chg_pct > OI_T1:
        fp += 15
        signals.append(("🚀", "LONG BUILDUP",
                         "Fresh longs entering"))
    if abs(oi_chg_pct) >= OI_T2:
        fp += 10
        signals.append(("💪", "STRONG OI MOVE",
                         f"OI {oi_chg_pct:+.1f}% — high conviction"))
    elif abs(oi_chg_pct) >= OI_T1:
        fp += 5
    score += fp
    breakdown["fno"] = fp

    # ── Delivery (max 20) ─────────────────────────────────
    dp = 0
    if delivery_pct >= DEL_T3:
        dp = 20
        signals.append(("📦", "HIGH DELIVERY",
                         f"{delivery_pct:.1f}% — strong cash conviction"))
    elif delivery_pct >= DEL_T2:
        dp = 15
        signals.append(("📦", "GOOD DELIVERY",
                         f"{delivery_pct:.1f}% delivery"))
    elif delivery_pct >= DEL_T1:
        dp = 10
    score += dp
    breakdown["delivery"] = dp

    # ── Volume (max 10) ───────────────────────────────────
    vp = 0
    if vol_ratio >= VOL_T3:
        vp = 10
        signals.append(("🔊", "VOLUME EXPLOSION",
                         f"{vol_ratio:.1f}x avg"))
    elif vol_ratio >= VOL_T2:
        vp = 7
        signals.append(("🔊", "VOLUME SURGE",
                         f"{vol_ratio:.1f}x avg"))
    elif vol_ratio >= VOL_T1:
        vp = 4

    score += vp
    breakdown["volume"] = vp
    breakdown["total"]  = score
    return score, breakdown, signals

# =========================================================
# SECTOR SCORING ENGINE (0–30 bonus pts per sector)
# =========================================================

def compute_sector_scores(all_results):
    """
    all_results: list of result dicts from process_stock.
    Returns:
        sector_scores: dict sector → bonus_pts (0–30)
        sector_stats:  dict sector → {count, total, avg_move,
                                       short_covering_count, stocks}
    """
    # Group results by sector
    by_sector = defaultdict(list)
    for r in all_results:
        by_sector[r["sector"]].append(r)

    sector_scores = {}
    sector_stats  = {}

    for sector, results in by_sector.items():
        total_in_sector = SECTOR_SIZES[sector]
        count_signalling = len(results)  # stocks that passed price filter

        if count_signalling == 0:
            sector_scores[sector] = 0
            sector_stats[sector]  = {
                "count": 0, "total": total_in_sector,
                "avg_move": 0, "short_covering_count": 0,
                "bonus": 0, "stocks": [],
            }
            continue

        breadth = count_signalling / total_in_sector
        avg_move = sum(r["price_data"]["move_pct"] for r in results) / count_signalling
        short_covering_count = sum(
            1 for r in results
            if r["fno_data"].get("oi_chg_pct", 0) < -OI_T1
        )

        bonus = 0

        # Breadth bonus (max 15 pts)
        if breadth >= BREADTH_T3:
            bonus += 15
        elif breadth >= BREADTH_T2:
            bonus += 10
        elif breadth >= BREADTH_T1:
            bonus += 5

        # Sector avg move bonus (max 10 pts)
        if avg_move >= SECT_MOVE_T2:
            bonus += 10
        elif avg_move >= SECT_MOVE_T1:
            bonus += 5

        # OI confirmation bonus (max 5 pts)
        if count_signalling > 0:
            sc_ratio = short_covering_count / count_signalling
            if sc_ratio >= 0.5:     # majority short covering in sector
                bonus += 5

        sector_scores[sector] = min(bonus, 30)  # cap at 30
        sector_stats[sector] = {
            "count":                 count_signalling,
            "total":                 total_in_sector,
            "avg_move":              avg_move,
            "short_covering_count":  short_covering_count,
            "bonus":                 min(bonus, 30),
            "stocks":                [r["symbol"] for r in results],
        }

    return sector_scores, sector_stats

# =========================================================
# SECTOR HEAT LABEL
# =========================================================

def sector_heat_label(bonus, avg_move, breadth):
    if bonus >= 20:
        return "🔥 HOT"
    if bonus >= 10:
        return "♨️ WARM"
    if bonus > 0:
        return "🟡 ACTIVE"
    return "⚪ QUIET"

# =========================================================
# VISUAL AIDS
# =========================================================

def score_bar(score, max_score=130):
    pct    = min(score / max_score, 1.0)
    filled = round(pct * 10)
    empty  = 10 - filled
    return f"{'█' * filled}{'░' * empty} {score}/{max_score}"


def score_label(score):
    if score >= 95:
        return "🔥 EXCEPTIONAL"
    if score >= 70:
        return "🔥 STRONG"
    if score >= 55:
        return "✅ GOOD"
    if score >= 45:
        return "🟡 MODERATE"
    return "⚪ WEAK"

# =========================================================
# PROCESS ONE STOCK (raw result, no alert yet)
# =========================================================

def process_stock(symbol, sector, yf_override):
    try:
        price_data = fetch_price(symbol, yf_override)
        if not price_data:
            return None

        fno_data      = fetch_nse_fno(symbol)
        delivery_data = fetch_nse_delivery(symbol)

        stock_score, breakdown, signals = score_stock(
            price_data, fno_data, delivery_data
        )

        log(
            f"📊 {symbol} [{sector}] | "
            f"+{price_data['move_pct']:.2f}% | "
            f"OI={fno_data.get('oi_chg_pct', 0):+.1f}% | "
            f"Del={delivery_data.get('delivery_pct', 0):.0f}% | "
            f"Score={stock_score}"
        )

        return {
            "symbol":        symbol,
            "sector":        sector,
            "stock_score":   stock_score,
            "adjusted_score": stock_score,   # sector bonus added later
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
# SECTOR PULSE MESSAGE
# =========================================================

def build_sector_pulse(sector_stats, ist_now):
    """
    Single message showing which sectors are hot this run.
    Sorted by bonus pts descending.
    Only shows sectors with at least 1 signalling stock.
    """
    active = [
        (s, info) for s, info in sector_stats.items()
        if info["count"] > 0
    ]
    active.sort(key=lambda x: x[1]["bonus"], reverse=True)

    if not active:
        return None

    lines = [
        f"📡 <b>SECTOR PULSE</b>  |  {ist_now} IST",
        "━" * 24,
    ]

    for sector, info in active:
        display  = SECTOR_DISPLAY.get(sector, sector)
        breadth  = info["count"] / info["total"]
        avg_move = info["avg_move"]
        bonus    = info["bonus"]
        heat     = sector_heat_label(bonus, avg_move, breadth)

        lines.append(
            f"{heat}  <b>{display}</b>\n"
            f"   {info['count']}/{info['total']} stocks up  "
            f"|  Avg +{avg_move:.2f}%  "
            f"|  Bonus +{bonus}pts"
        )

    lines.append("━" * 24)
    lines.append(f"🔎 Scanned {len(WATCHLIST)} stocks")
    return "\n".join(lines)

# =========================================================
# STOCK ALERT MESSAGE
# =========================================================

def build_stock_message(rank, result, sector_stats):
    pd_   = result["price_data"]
    fd    = result["fno_data"]
    dd    = result["delivery_data"]
    sector = result["sector"]
    score  = result["adjusted_score"]
    bd     = result["breakdown"]
    sigs   = result["signals"]

    sym          = pd_["symbol"]
    price        = pd_["price"]
    move_pct     = pd_["move_pct"]
    day_open     = pd_["day_open"]
    day_high     = pd_["day_high"]
    day_low      = pd_["day_low"]
    intra_vol    = pd_["intra_vol"]
    avg_volume   = pd_["avg_volume"]

    oi           = fd.get("oi", 0)
    oi_chg_pct   = fd.get("oi_chg_pct", 0)
    lot_size     = fd.get("lot_size", 0)
    delivery_pct = dd.get("delivery_pct", 0)

    vol_ratio    = (intra_vol / avg_volume) if avg_volume > 0 else 0
    oi_arrow     = "↑" if oi_chg_pct > 0 else "↓" if oi_chg_pct < 0 else "→"

    # Sector context line
    st           = sector_stats.get(sector, {})
    sect_display = SECTOR_DISPLAY.get(sector, sector)
    sect_count   = st.get("count", 0)
    sect_total   = st.get("total", 0)
    sect_move    = st.get("avg_move", 0)
    sect_bonus   = st.get("bonus", 0)
    sect_heat    = sector_heat_label(
        sect_bonus,
        sect_move,
        sect_count / sect_total if sect_total else 0
    )

    # Score breakdown line
    pts_line = (
        f"  Price={bd.get('price',0)}  "
        f"FnO={bd.get('fno',0)}  "
        f"Del={bd.get('delivery',0)}  "
        f"Vol={bd.get('volume',0)}  "
        f"Sector=+{sect_bonus}"
    )

    sig_lines = "\n".join(
        f"  {e} <b>{n}</b>: {d}" for e, n, d in sigs
    ) or "  📊 Meets momentum criteria"

    ist_now = datetime.now(IST).strftime("%H:%M:%S")

    msg = (
        f"{'━' * 24}\n"
        f"#{rank}  <b>{sym}</b>  📈 <b>+{move_pct:.2f}%</b>\n"
        f"{'━' * 24}\n"
        f"\n"
        f"⭐ <b>Score: {score_bar(score)}</b>\n"
        f"   {score_label(score)}\n"
        f"<code>{pts_line}</code>\n"
        f"\n"
        f"🏷️ <b>Sector:</b> {sect_display}  {sect_heat}\n"
        f"   {sect_count}/{sect_total} sector stocks up  "
        f"|  Sector avg +{sect_move:.2f}%\n"
        f"\n"
        f"💰 <b>Price</b>:       ₹{price:.2f}\n"
        f"🔓 <b>Day Open</b>:    ₹{day_open:.2f}\n"
        f"⬆️ <b>Day High</b>:    ₹{day_high:.2f}\n"
        f"⬇️ <b>Day Low</b>:     ₹{day_low:.2f}\n"
        f"\n"
        f"📊 <b>OI</b>:           {oi:,}" if oi else f"📊 <b>OI</b>:           N/A"
    )

    # Append rest of fields (workaround for conditional f-string above)
    msg += (
        f"\n"
        f"🔁 <b>OI Change</b>:   "
        f"{oi_chg_pct:+.2f}% {oi_arrow}" if oi_chg_pct else "\n🔁 <b>OI Change</b>:   N/A"
    )
    msg += (
        f"\n📦 <b>Delivery</b>:    {delivery_pct:.1f}%"
        if delivery_pct else "\n📦 <b>Delivery</b>:    N/A"
    )
    msg += (
        f"\n🔊 <b>Volume</b>:      {vol_ratio:.1f}x avg"
        if vol_ratio else "\n🔊 <b>Volume</b>:      N/A"
    )
    msg += (
        f"\n📐 <b>Lot Size</b>:    {lot_size:,}"
        if lot_size else "\n📐 <b>Lot Size</b>:    N/A"
    )
    msg += (
        f"\n\n🎯 <b>WHY THIS PICK</b>:\n"
        f"{sig_lines}\n"
        f"\n🕐 {ist_now} IST"
    )

    return msg

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
    get_nse_session()

    log("📊 SCANNING ALL STOCKS")

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
                log(f"❌ {sym}: Thread error")
                traceback.print_exc()

    log(f"📦 Stocks with upside move: {len(raw_results)}")

    # ── Compute sector scores ─────────────────────────────
    sector_scores, sector_stats = compute_sector_scores(raw_results)

    log("📊 SECTOR SCORES:")
    for sector, bonus in sorted(sector_scores.items(),
                                key=lambda x: -x[1]):
        st = sector_stats[sector]
        if st["count"] > 0:
            log(
                f"  {SECTOR_DISPLAY.get(sector, sector)}: "
                f"bonus={bonus}  "
                f"{st['count']}/{st['total']} up  "
                f"avg +{st['avg_move']:.2f}%"
            )

    # ── Apply sector bonus to each result ─────────────────
    for r in raw_results:
        bonus = sector_scores.get(r["sector"], 0)
        r["adjusted_score"] = r["stock_score"] + bonus

    # ── Filter by minimum adjusted score ──────────────────
    candidates = [
        r for r in raw_results
        if r["adjusted_score"] >= MIN_SCORE and r["signals"]
    ]

    log(f"🏆 Candidates after score gate (≥{MIN_SCORE}): {len(candidates)}")

    if not candidates:
        log("💤 No qualifying picks this run")
        return

    # ── Sort by adjusted score, take top N ────────────────
    top_picks = sorted(
        candidates, key=lambda x: x["adjusted_score"], reverse=True
    )[:MAX_ALERTS]

    log(f"📨 Sending sector pulse + {len(top_picks)} stock alerts")

    # ── 1. Sector pulse ───────────────────────────────────
    pulse_msg = build_sector_pulse(sector_stats, ist_now)
    if pulse_msg:
        send_telegram(pulse_msg)
        time.sleep(1)

    # ── 2. Individual stock alerts ────────────────────────
    for rank, pick in enumerate(top_picks, 1):
        msg = build_stock_message(rank, pick, sector_stats)
        send_telegram(msg)
        time.sleep(0.5)

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
