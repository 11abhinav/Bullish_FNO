# =========================================================
# ADVANCED NSE MOMENTUM + FNO TELEGRAM BOT
# SCORING EDITION — TOP PICKS ONLY (UPSIDE ONLY)
# =========================================================
#
# SCORING SYSTEM (max 100 points)
# ---------------------------------------------------------
#
#  PRICE ACTION         (max 30 pts)
#    +10   Move > 0.8%  vs day open
#    +10   Move > 1.5%  vs day open
#    +10   Move > 2.5%  vs day open
#
#  FNO SIGNALS          (max 40 pts)
#    +20   Short Covering  (OI ↓ + Price ↑)   ← strongest signal
#    +15   Long Buildup    (OI ↑ + Price ↑)   ← fresh longs
#    +10   OI change > 5%  (strong conviction)
#    +5    OI change > 2%  (moderate)
#
#  DELIVERY              (max 20 pts)
#    +20   Delivery % > 70%
#    +15   Delivery % > 60%
#    +10   Delivery % > 50%
#
#  VOLUME                (max 10 pts)
#    +10   Volume > 3x avg
#    +7    Volume > 2x avg
#    +4    Volume > 1.5x avg
#
#  ALERT GATE
#    Minimum score to send alert: MIN_SCORE (default 40)
#    Max alerts per run: MAX_ALERTS (default 5)
#    Only sends TOP picks sorted by score
#
#  UPSIDE ONLY FILTERS
#    ❌ No short buildup signals
#    ❌ No long unwinding signals
#    ❌ No downside momentum
#    ✅ Only signals where price is UP from day open
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

# ── Scoring thresholds ────────────────────────────────────
MIN_SCORE   = 40    # Minimum score to be considered (0–100)
MAX_ALERTS  = 5     # Max top picks to send per run

# ── Price move vs DAY OPEN (upside only) ─────────────────
MOVE_T1 = 0.8       # +10 pts
MOVE_T2 = 1.5       # +10 pts (cumulative with T1)
MOVE_T3 = 2.5       # +10 pts (cumulative)

# ── OI change % thresholds ───────────────────────────────
OI_T1   = 2.0       # moderate OI signal
OI_T2   = 5.0       # strong OI signal

# ── Delivery % thresholds ────────────────────────────────
DEL_T1  = 50.0
DEL_T2  = 60.0
DEL_T3  = 70.0

# ── Volume vs 20-day avg ─────────────────────────────────
VOL_T1  = 1.5
VOL_T2  = 2.0
VOL_T3  = 3.0

# ── Infrastructure ────────────────────────────────────────
MAX_WORKERS = 1     # Keep at 1 to avoid Yahoo rate limits

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
# =========================================================
# (NSE_SYMBOL, YF_OVERRIDE or None)
# None → symbol + ".NS" used for Yahoo Finance
# M&M  → "MM.NS" because & breaks Yahoo URL

WATCHLIST = [
    ("SBIN",       None),
    ("HDFCBANK",   None),
    ("ICICIBANK",  None),
    ("AXISBANK",   None),
    ("KOTAKBANK",  None),
    ("INFY",       None),
    ("TCS",        None),
    ("WIPRO",      None),
    ("LT",         None),
    ("BEL",        None),
    ("RVNL",       None),
    ("PFC",        None),
    ("RECLTD",     None),
    ("SUZLON",     None),
    ("JIOFIN",     None),
    ("TRENT",      None),
    ("TITAN",      None),
    ("MARUTI",     None),
    ("ONGC",       None),
    ("TATAPOWER",  None),
    ("JSWENERGY",  None),
    ("MAZDOCK",    None),
    ("HAL",        None),
    ("COCHINSHIP", None),
    ("ADANIPORTS", None),
    ("ADANIENT",   None),
    ("CGPOWER",    None),
    ("BHEL",       None),
    ("IRB",        None),
    ("NTPC",       None),
    ("BANKBARODA", None),
    ("CANBK",      None),
    ("UNIONBANK",  None),
    ("LODHA",      None),
    ("DLF",        None),
    ("KPITTECH",   None),
    ("PERSISTENT", None),
    ("COFORGE",    None),
    ("POLYCAB",    None),
    ("KEI",        None),
    ("VBL",        None),
    ("TATATECH",   None),
    ("PIDILITIND", None),
    ("PVRINOX",    None),
    ("INDUSTOWER", None),
    ("HINDCOPPER", None),
    ("TATASTEEL",  None),
    ("JSWSTEEL",   None),
    ("HINDALCO",   None),
    ("POWERGRID",  None),
    ("IOC",        None),
    ("BPCL",       None),
    ("GAIL",       None),
    ("SUNPHARMA",  None),
    ("DIVISLAB",   None),
    ("DRREDDY",    None),
    ("CIPLA",      None),
    ("BAJFINANCE", None),
    ("BAJAJFINSV", None),
    ("CHOLAFIN",   None),
    ("SHRIRAMFIN", None),
    ("MUTHOOTFIN", None),
    ("MANAPPURAM", None),
    ("TATAMTRDVR", None),
    ("M&M",        "MM.NS"),   # & breaks Yahoo URL
    ("EICHERMOT",  None),
    ("TVSMOTOR",   None),
    ("ASHOKLEY",   None),
]

log(f"📊 Watchlist Loaded: {len(WATCHLIST)}")

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
        log("⚠️ NSE session init failed — OI/delivery unavailable")
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
        r = s.get(url, timeout=8)
        if r.status_code != 200:
            return {}

        data   = r.json()
        stocks = data.get("stocks", [])
        if not stocks:
            return {}

        # Near-month Stock Futures row
        fut_data = next(
            (row for row in stocks
             if row.get("metadata", {}).get("instrumentType") == "Stock Futures"),
            None
        )
        if not fut_data:
            return {}

        meta      = fut_data.get("metadata", {})
        tradeinfo = fut_data.get("marketDeptOrderBook", {}).get("tradeInfo", {})

        oi       = tradeinfo.get("openInterest", 0) or 0
        oi_chg   = tradeinfo.get("changeinOpenInterest", 0) or 0
        volume   = tradeinfo.get("tradedVolume", 0) or 0
        lot_size = meta.get("lotSize", 0) or 0

        oi_chg_pct = 0.0
        prev_oi    = oi - oi_chg
        if prev_oi > 0:
            oi_chg_pct = (oi_chg / prev_oi) * 100

        return {
            "oi":         oi,
            "oi_chg":     oi_chg,
            "oi_chg_pct": oi_chg_pct,
            "fut_volume": volume,
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

def fetch_price(symbol, yf_symbol_override=None):
    try:
        time.sleep(random.uniform(1.5, 3.0))   # rate limit buffer

        yf_sym = yf_symbol_override or f"{symbol}.NS"

        # ── Intraday 5-min candles ─────────────────────
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

        # KEY FIX: move vs day open, not candle-to-candle
        move_pct = ((last_price - day_open) / day_open) * 100

        # UPSIDE ONLY — reject if price is below day open
        if move_pct <= 0:
            return None

        # ── 20-day avg volume baseline ─────────────────
        avg_volume = 0
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
        log(f"❌ {symbol}: {traceback.format_exc()[-100:]}")
        return None

# =========================================================
# SCORING ENGINE
# =========================================================

def score_stock(price_data, fno_data, delivery_data):
    """
    Scores each stock 0–100 based on upside-only criteria.
    Returns (total_score, score_breakdown_dict, signals_list).

    Only positive/upside signals are scored.
    """
    score     = 0
    breakdown = {}
    signals   = []    # list of (emoji, name, description)

    move_pct     = price_data.get("move_pct", 0)
    intra_vol    = price_data.get("intra_vol", 0)
    avg_volume   = price_data.get("avg_volume", 0)

    oi_chg_pct   = fno_data.get("oi_chg_pct", 0)
    delivery_pct = delivery_data.get("delivery_pct", 0)
    vol_ratio    = (intra_vol / avg_volume) if avg_volume > 0 else 0

    # ── BLOCK 1: Price Action (max 30 pts) ───────────────
    price_pts = 0
    if move_pct >= MOVE_T1:
        price_pts += 10
    if move_pct >= MOVE_T2:
        price_pts += 10
    if move_pct >= MOVE_T3:
        price_pts += 10

    score += price_pts
    breakdown["price"] = price_pts
    if price_pts > 0:
        signals.append((
            "📈", "PRICE BREAKOUT",
            f"+{move_pct:.2f}% above day open"
        ))

    # ── BLOCK 2: FnO / OI Signals (max 40 pts) ───────────
    fno_pts = 0

    # Short Covering: OI falling + price rising (best signal)
    if move_pct > 0 and oi_chg_pct < -OI_T1:
        fno_pts += 20
        signals.append((
            "🔄", "SHORT COVERING",
            "Shorts exiting as price rises — strong buy pressure"
        ))

    # Long Buildup: OI rising + price rising
    elif move_pct > 0 and oi_chg_pct > OI_T1:
        fno_pts += 15
        signals.append((
            "🚀", "LONG BUILDUP",
            "Fresh longs entering — institutional conviction"
        ))

    # OI magnitude bonus (stacks on top of above)
    if abs(oi_chg_pct) >= OI_T2:
        fno_pts += 10
        signals.append((
            "💪", "STRONG OI MOVE",
            f"OI changed {oi_chg_pct:+.1f}% — high conviction"
        ))
    elif abs(oi_chg_pct) >= OI_T1:
        fno_pts += 5

    score += fno_pts
    breakdown["fno"] = fno_pts

    # ── BLOCK 3: Delivery (max 20 pts) ───────────────────
    del_pts = 0
    if delivery_pct >= DEL_T3:
        del_pts = 20
        signals.append((
            "📦", "HIGH DELIVERY",
            f"{delivery_pct:.1f}% delivery — strong cash market buying"
        ))
    elif delivery_pct >= DEL_T2:
        del_pts = 15
        signals.append((
            "📦", "GOOD DELIVERY",
            f"{delivery_pct:.1f}% delivery — solid conviction"
        ))
    elif delivery_pct >= DEL_T1:
        del_pts = 10

    score += del_pts
    breakdown["delivery"] = del_pts

    # ── BLOCK 4: Volume (max 10 pts) ─────────────────────
    vol_pts = 0
    if vol_ratio >= VOL_T3:
        vol_pts = 10
        signals.append((
            "🔊", "VOLUME EXPLOSION",
            f"{vol_ratio:.1f}x avg — aggressive buying"
        ))
    elif vol_ratio >= VOL_T2:
        vol_pts = 7
        signals.append((
            "🔊", "VOLUME SURGE",
            f"{vol_ratio:.1f}x avg — above normal activity"
        ))
    elif vol_ratio >= VOL_T1:
        vol_pts = 4

    score += vol_pts
    breakdown["volume"] = vol_pts

    breakdown["total"] = score
    return score, breakdown, signals

# =========================================================
# SCORE BAR (visual indicator in Telegram)
# =========================================================

def score_bar(score):
    """Returns a visual bar like ████░░░░░░ 65/100"""
    filled = round(score / 10)
    empty  = 10 - filled
    bar    = "█" * filled + "░" * empty
    return f"{bar} {score}/100"


def score_label(score):
    if score >= 75:
        return "🔥 STRONG"
    if score >= 55:
        return "✅ GOOD"
    if score >= 40:
        return "🟡 MODERATE"
    return "⚪ WEAK"

# =========================================================
# TELEGRAM MESSAGE BUILDER
# =========================================================

def build_message(rank, price_data, fno_data, delivery_data,
                  score, breakdown, signals):
    symbol       = price_data["symbol"]
    price        = price_data["price"]
    move_pct     = price_data["move_pct"]
    day_open     = price_data["day_open"]
    day_high     = price_data["day_high"]
    day_low      = price_data["day_low"]
    intra_vol    = price_data["intra_vol"]
    avg_volume   = price_data["avg_volume"]

    oi           = fno_data.get("oi", 0)
    oi_chg_pct   = fno_data.get("oi_chg_pct", 0)
    lot_size     = fno_data.get("lot_size", 0)
    delivery_pct = delivery_data.get("delivery_pct", 0)

    vol_ratio    = (intra_vol / avg_volume) if avg_volume > 0 else 0
    ist_now      = datetime.now(IST).strftime("%H:%M:%S")

    oi_arrow     = "↑" if oi_chg_pct > 0 else "↓" if oi_chg_pct < 0 else "→"
    oi_str       = f"{oi:,}" if oi else "N/A"
    oi_chg_str   = f"{oi_chg_pct:+.2f}% {oi_arrow}" if oi_chg_pct else "N/A"
    del_str      = f"{delivery_pct:.1f}%" if delivery_pct else "N/A"
    vol_str      = f"{vol_ratio:.1f}x avg" if vol_ratio else "N/A"
    lot_str      = f"{lot_size:,}" if lot_size else "N/A"

    signal_lines = "\n".join(
        f"  {e} <b>{n}</b>: {d}"
        for e, n, d in signals
    ) or "  📊 Meets basic momentum criteria"

    # Score breakdown mini-bar
    pts_line = (
        f"  Price={breakdown.get('price',0)}  "
        f"FnO={breakdown.get('fno',0)}  "
        f"Del={breakdown.get('delivery',0)}  "
        f"Vol={breakdown.get('volume',0)}"
    )

    msg = (
        f"{'━'*24}\n"
        f"#{rank}  <b>{symbol}</b>  📈 <b>+{move_pct:.2f}%</b>\n"
        f"{'━'*24}\n"
        f"\n"
        f"⭐ <b>Score: {score_bar(score)}</b>  {score_label(score)}\n"
        f"<code>{pts_line}</code>\n"
        f"\n"
        f"💰 <b>Price</b>:       ₹{price:.2f}\n"
        f"🔓 <b>Day Open</b>:    ₹{day_open:.2f}\n"
        f"⬆️ <b>Day High</b>:    ₹{day_high:.2f}\n"
        f"⬇️ <b>Day Low</b>:     ₹{day_low:.2f}\n"
        f"\n"
        f"📊 <b>OI</b>:           {oi_str}\n"
        f"🔁 <b>OI Change</b>:   {oi_chg_str}\n"
        f"📦 <b>Delivery</b>:    {del_str}\n"
        f"🔊 <b>Volume</b>:      {vol_str}\n"
        f"📐 <b>Lot Size</b>:    {lot_str}\n"
        f"\n"
        f"🎯 <b>WHY THIS PICK</b>:\n"
        f"{signal_lines}\n"
        f"\n"
        f"🕐 {ist_now} IST"
    )
    return msg

# =========================================================
# PROCESS ONE STOCK (collect result, don't send yet)
# =========================================================

def process_stock(symbol, yf_override):
    """
    Returns a result dict if stock passes minimum criteria,
    None otherwise. Caller ranks and filters.
    """
    try:
        # 1. Price data (already filters out downside moves)
        price_data = fetch_price(symbol, yf_override)
        if not price_data:
            return None

        # 2. NSE FnO + delivery data
        nse_sym       = symbol   # NSE handles M&M fine
        fno_data      = fetch_nse_fno(nse_sym)
        delivery_data = fetch_nse_delivery(nse_sym)

        # 3. Score
        score, breakdown, signals = score_stock(
            price_data, fno_data, delivery_data
        )

        log(
            f"📊 {symbol} | "
            f"Move=+{price_data['move_pct']:.2f}% | "
            f"OI={fno_data.get('oi_chg_pct', 0):+.1f}% | "
            f"Del={delivery_data.get('delivery_pct', 0):.0f}% | "
            f"Score={score}"
        )

        # 4. Reject below minimum
        if score < MIN_SCORE:
            return None

        # Must have at least one real signal (not just weak price move)
        if not signals:
            return None

        return {
            "symbol":        symbol,
            "score":         score,
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
# SUMMARY MESSAGE (sent first, before individual alerts)
# =========================================================

def build_summary(picks, ist_now):
    lines = [
        f"📡 <b>NSE TOP PICKS</b>  |  {ist_now} IST\n",
        f"{'━'*24}",
    ]
    for i, p in enumerate(picks, 1):
        sym      = p["symbol"]
        move_pct = p["price_data"]["move_pct"]
        score    = p["score"]
        label    = score_label(score)
        lines.append(
            f"#{i}  <b>{sym}</b>  +{move_pct:.2f}%  "
            f"│  Score {score}  {label}"
        )
    lines.append(f"{'━'*24}")
    lines.append(f"🔎 Scanned {len(WATCHLIST)} stocks — "
                 f"showing top {len(picks)}")
    return "\n".join(lines)

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
    get_nse_session()   # prime NSE session once

    log("📊 SCANNING ALL STOCKS")
    candidates = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(process_stock, sym, yf_ov): sym
            for sym, yf_ov in WATCHLIST
        }
        for future in as_completed(futures):
            sym = futures[future]
            try:
                result = future.result()
                if result:
                    candidates.append(result)
            except Exception:
                log(f"❌ {sym}: Thread error")
                traceback.print_exc()

    log(f"📦 Candidates scoring ≥{MIN_SCORE}: {len(candidates)}")

    if not candidates:
        log("💤 No qualifying picks this run")
        return

    # ── Sort by score descending, take top MAX_ALERTS ────
    top_picks = sorted(
        candidates,
        key=lambda x: x["score"],
        reverse=True
    )[:MAX_ALERTS]

    log(f"🏆 Sending top {len(top_picks)} picks")

    # ── Send summary first ────────────────────────────────
    summary = build_summary(top_picks, ist_now)
    send_telegram(summary)
    time.sleep(1)   # brief gap before individual alerts

    # ── Send individual detailed alerts ──────────────────
    for rank, pick in enumerate(top_picks, 1):
        msg = build_message(
            rank            = rank,
            price_data      = pick["price_data"],
            fno_data        = pick["fno_data"],
            delivery_data   = pick["delivery_data"],
            score           = pick["score"],
            breakdown       = pick["breakdown"],
            signals         = pick["signals"],
        )
        send_telegram(msg)
        time.sleep(0.5)   # avoid Telegram flood limit

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
