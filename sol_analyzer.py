#!/usr/bin/env python3
"""
SOL/USDT x10 — Automated Trade Signal Analyzer + Telegram Alerts
Binance Futures public API — no API key required

Dependencies:
    pip install requests pandas numpy colorama

Usage:
    python sol_analyzer.py                   # analyse one-shot, console only
    python sol_analyzer.py --notify          # one-shot + Telegram si signal actionnable
    python sol_analyzer.py --notify --force  # envoie la notif meme si signal inchange
    python sol_analyzer.py --watch 60        # refresh every 60s (local seulement)

Telegram setup:
    1. Cree un bot via @BotFather -> recupere TELEGRAM_TOKEN
    2. Recupere ton TELEGRAM_CHAT_ID via @userinfobot
    3. Exporte les vars d'env :
       export TELEGRAM_TOKEN="123456:ABC-xyz"
       export TELEGRAM_CHAT_ID="987654321"

GitHub Actions (execution sans PC) :
    Ajoute ces deux vars comme Repository Secrets dans Settings > Secrets.
    Le workflow .github/workflows/sol_signal.yml tourne selon le cron configure.

State file:
    Le script ecrit l'etat du dernier signal dans .sol_signal_state.json
    pour eviter de spammer Telegram si le signal ne change pas entre deux runs.
"""

import requests
import pandas as pd
import numpy as np
import argparse
import time
import sys
import os
import json
from datetime import datetime, timezone, timedelta
from colorama import init, Fore, Back, Style

# strip=False + convert=False en CI (pas de TTY) pour eviter les crashes
# colorama detecte automatiquement si stdout est un terminal
IS_TTY = sys.stdout.isatty()
init(autoreset=True, strip=not IS_TTY, convert=False)

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
GATE_BASE   = "https://api.gateio.ws/api/v4"

SYMBOL      = "SOLUSDT"
BTC_SYMBOL  = "BTCUSDT"
TIMEOUT     = 10

# Telegram — lus depuis les variables d'environnement
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Fichier de state pour eviter les doublons de notif
STATE_FILE = ".sol_signal_state.json"

# Statuts qui declenchent une notification Telegram
NOTIFY_ON_STATUSES = {"go", "blocked"}

# Total items dans la checklist HTML (27)
# Le script en automatise 13 — les 14 restants sont manuels
# La completion est calculée sur 27 pour rester cohérente avec la checklist
TOTAL_CHECKLIST_ITEMS = 27
AUTO_ITEMS   = 20   # automatisés par ce script
MANUAL_ITEMS = 7    # nécessitent vraiment des données personnelles

# Pivot detection: N candles each side to qualify as swing high/low
# 2 sur Daily (délai ~4 jours), 3 sur 4H/15min (délai ~12-45 bougies)
SWING_N      = 2
SWING_N_FAST = 3   # pour 4H et 15min


# ─────────────────────────────────────────────
# TERMINAL HELPERS
# ─────────────────────────────────────────────
W  = Style.BRIGHT + Fore.WHITE
DIM = Style.DIM + Fore.WHITE
G  = Style.BRIGHT + Fore.GREEN
R  = Style.BRIGHT + Fore.RED
Y  = Style.BRIGHT + Fore.YELLOW
C  = Style.BRIGHT + Fore.CYAN
M  = Style.BRIGHT + Fore.MAGENTA
RST = Style.RESET_ALL

def header(text):
    w = 62
    print()
    print(C + "┌" + "─" * (w-2) + "┐")
    print(C + "│" + W + f"  {text:<{w-4}}" + C + "│")
    print(C + "└" + "─" * (w-2) + "┘" + RST)

def section(text):
    print()
    print(C + f"  ── {text} " + DIM + "─" * max(0, 52 - len(text)) + RST)

def row(label, value, color=W, hint=""):
    label_str = DIM + f"  {label:<30}" + RST
    hint_str  = DIM + f"  {hint}" if hint else ""
    print(f"{label_str}{color}{value}{RST}{hint_str}")

def signal_row(label, verdict, color, direction=None, hint=""):
    dir_str = ""
    if direction == "long":
        dir_str = G + " ▲ LONG"
    elif direction == "short":
        dir_str = R + " ▼ SHORT"
    label_str = DIM + f"  {label:<30}" + RST
    hint_str  = DIM + f"   {hint}" if hint else ""
    print(f"{label_str}{color}{verdict}{dir_str}{RST}{hint_str}")

def verdict_box(label, color, bg=None):
    w = 60
    pad = (w - len(label) - 4) // 2
    line = " " * pad + f"  {label}  " + " " * pad
    # Background colors suppressed in non-TTY (CI) to avoid encoding issues
    if bg and IS_TTY:
        print(bg + color + Style.BRIGHT + f"\n  {line}\n" + RST)
    else:
        print(color + Style.BRIGHT + f"\n  {'='*(w-4)}")
        print(f"  {line[:w-4]}")
        print(f"  {'='*(w-4)}" + RST)


# ─────────────────────────────────────────────
# API CALLS
# ─────────────────────────────────────────────
def get(url, params=None):
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        print(R + f"  [API ERROR] {url}: {e}" + RST)
        return None


def _gate_contract(symbol):
    """Convert SOLUSDT -> SOL_USDT for Gate.io futures."""
    # e.g. SOLUSDT -> SOL_USDT, BTCUSDT -> BTC_USDT
    if symbol.endswith("USDT"):
        return symbol[:-4] + "_USDT"
    return symbol


def fetch_ohlcv(symbol, interval, limit=500):
    """
    Gate.io futures candlesticks.
    Intervals: 10s,1m,5m,15m,30m,1h,4h,8h,1d,7d
    """
    interval_map = {"15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
    iv = interval_map.get(interval, interval)
    contract = _gate_contract(symbol)
    data = get(f"{GATE_BASE}/futures/usdt/candlesticks",
               {"contract": contract, "interval": iv, "limit": limit})
    if not data:
        return None
    # Gate.io returns list of dicts:
    # {t: timestamp_sec, o, h, l, c, v (contracts), sum (quote volume)}
    df = pd.DataFrame(data)
    df = df.rename(columns={"t": "open_time", "o": "open", "h": "high",
                             "l": "low", "c": "close", "v": "volume",
                             "sum": "quote_vol"})
    for col in ["open", "high", "low", "close", "volume", "quote_vol"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["open_time"] = pd.to_datetime(df["open_time"], unit="s", utc=True)
    df = df.sort_values("open_time").set_index("open_time")
    df["taker_buy_quote"] = df["quote_vol"] * 0.5  # neutral fallback for CVD proxy
    return df


def fetch_trades_cvd(symbol, limit=1000):
    """
    Gate.io recent trades for CVD.
    GET /futures/usdt/trades?contract=SOL_USDT&limit=1000
    Each trade: {size (+ = buy, - = sell), price, ...}
    """
    contract = _gate_contract(symbol)
    data = get(f"{GATE_BASE}/futures/usdt/trades",
               {"contract": contract, "limit": min(limit, 1000)})
    if not data:
        return None
    buy_vol  = sum(float(t["size"]) for t in data if float(t["size"]) > 0)
    sell_vol = sum(abs(float(t["size"])) for t in data if float(t["size"]) < 0)
    cvd = buy_vol - sell_vol
    direction = "bullish" if cvd > 0 else "bearish"
    return cvd, direction, buy_vol, sell_vol


def fetch_funding_rate(symbol):
    """
    Gate.io funding rate history.
    GET /futures/usdt/funding_rate?contract=SOL_USDT&limit=10
    """
    contract = _gate_contract(symbol)
    data = get(f"{GATE_BASE}/futures/usdt/funding_rate",
               {"contract": contract, "limit": 10})
    if not data:
        return None
    # Normalize: [{fundingRate, fundingTime}]
    return [{"fundingRate": str(r["r"]), "fundingTime": r["t"]} for r in data]


def fetch_open_interest_history(symbol, period="1h", limit=48):
    """
    Gate.io open interest. /futures/usdt/contract_stats
    interval: 5m, 15m, 30m, 1h, 4h, 1d
    Returns OI in number of contracts — multiply by price for USD value.
    """
    interval_map = {"1h": "1h", "4h": "4h", "1d": "1d", "15m": "15m"}
    iv = interval_map.get(period, period)
    contract = _gate_contract(symbol)
    data = get(f"{GATE_BASE}/futures/usdt/contract_stats",
               {"contract": contract, "interval": iv, "limit": limit})
    if not data:
        return None
    df = pd.DataFrame(data)
    # Gate returns: {time, lsr_taker, lsr_account, long_liq_size, short_liq_size,
    #                open_interest, open_interest_usd, ...}
    df["sumOpenInterest"]      = pd.to_numeric(df.get("open_interest", 0), errors="coerce")
    df["sumOpenInterestValue"] = pd.to_numeric(df.get("open_interest_usd", 0), errors="coerce")
    df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.sort_values("timestamp")
    return df


def fetch_long_short_ratio(symbol, period="1h", limit=24):
    """
    Gate.io long/short account ratio from contract_stats.
    lsr_account = long accounts / short accounts ratio (> 1 = more longs)
    """
    interval_map = {"1h": "1h", "4h": "4h", "1d": "1d", "15m": "15m"}
    iv = interval_map.get(period, period)
    contract = _gate_contract(symbol)
    data = get(f"{GATE_BASE}/futures/usdt/contract_stats",
               {"contract": contract, "interval": iv, "limit": limit})
    if not data:
        return None
    df = pd.DataFrame(data)
    # lsr_account: ratio long/short accounts (e.g. 1.5 = 60% long, 40% short)
    df["lsr"] = pd.to_numeric(df["lsr_account"], errors="coerce")
    # Convert ratio to longAccount fraction: ratio/(1+ratio)
    df["longAccount"]  = df["lsr"] / (1 + df["lsr"])
    df["shortAccount"] = 1 - df["longAccount"]
    df["longShortRatio"] = df["lsr"]
    df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.sort_values("timestamp")
    return df


def fetch_top_trader_ratio(symbol, period="1h", limit=24):
    """Not available on Gate.io public API — returns None gracefully."""
    return None


def fetch_current_price(symbol):
    contract = _gate_contract(symbol)
    data = get(f"{GATE_BASE}/futures/usdt/tickers",
               {"contract": contract})
    if data and len(data) > 0:
        return float(data[0]["last"])
    return None


def fetch_liquidations(symbol, limit=100):
    """
    Gate.io recent liquidation orders.
    GET /futures/usdt/liq_orders?contract=SOL_USDT
    Returns list of {time, contract, size (+ = long liq, - = short liq), price}
    """
    contract = _gate_contract(symbol)
    data = get(f"{GATE_BASE}/futures/usdt/liq_orders",
               {"contract": contract, "limit": limit})
    if not data:
        return None
    return data


def fetch_macro_calendar():
    """
    Forex Factory public JSON — unofficial but stable.
    Returns high-impact events in the next 4h.
    """
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    try:
        r = requests.get(url, timeout=TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        events = r.json()
    except Exception as e:
        print(Y + f"  [MACRO] Calendrier indisponible: {e}" + RST)
        return None

    now_utc  = datetime.now(timezone.utc)
    window   = now_utc + timedelta(hours=4)
    upcoming = []
    for ev in events:
        if ev.get("impact") != "High":
            continue
        try:
            # Format: "01-06-2026" + "8:30am" — parse in ET then convert
            dt_str = f"{ev['date']} {ev['time']}"
            dt_et  = datetime.strptime(dt_str, "%m-%d-%Y %I:%M%p")
            # ET = UTC-5 (EST) or UTC-4 (EDT) — approximate with UTC-5
            dt_utc = dt_et.replace(tzinfo=timezone.utc) + timedelta(hours=5)
            if now_utc <= dt_utc <= window:
                upcoming.append({"title": ev.get("title","?"), "time_utc": dt_utc})
        except Exception:
            continue
    return upcoming


# ─────────────────────────────────────────────
# PATTERN DETECTION (OHLCV-based)
# ─────────────────────────────────────────────

def detect_fvg(df, n_candles=50):
    """
    Detect Fair Value Gaps on the last n_candles.
    Bullish FVG : candle[i-2].high < candle[i].low  (gap up)
    Bearish FVG : candle[i-2].low  > candle[i].high (gap down)
    Returns list of (type, low, high, idx) sorted by recency.
    """
    fvgs = []
    data = df.iloc[-n_candles:].reset_index()
    for i in range(2, len(data)):
        c0_high = data.loc[i-2, "high"]
        c0_low  = data.loc[i-2, "low"]
        c2_high = data.loc[i,   "high"]
        c2_low  = data.loc[i,   "low"]
        if c0_high < c2_low:   # bullish FVG
            fvgs.append(("bullish", c0_high, c2_low, data.loc[i, "open_time"] if "open_time" in data.columns else i))
        elif c0_low > c2_high: # bearish FVG
            fvgs.append(("bearish", c2_high, c0_low, data.loc[i, "open_time"] if "open_time" in data.columns else i))
    return fvgs[-5:]  # return 5 most recent


def detect_order_blocks(df, n_candles=50, min_move_pct=0.8):
    """
    Detect Order Blocks:
    Bullish OB : last bearish candle before a strong bullish move (>= min_move_pct%)
    Bearish OB : last bullish candle before a strong bearish move
    """
    obs = []
    data = df.iloc[-n_candles:].reset_index()
    for i in range(1, len(data) - 1):
        body_pct = abs(data.loc[i+1, "close"] - data.loc[i+1, "open"]) / data.loc[i+1, "open"] * 100
        if body_pct < min_move_pct:
            continue
        is_bearish_candle = data.loc[i, "close"] < data.loc[i, "open"]
        is_bullish_next   = data.loc[i+1, "close"] > data.loc[i+1, "open"]
        is_bullish_candle = data.loc[i, "close"] > data.loc[i, "open"]
        is_bearish_next   = data.loc[i+1, "close"] < data.loc[i+1, "open"]

        if is_bearish_candle and is_bullish_next:
            obs.append(("bullish", data.loc[i, "low"], data.loc[i, "high"]))
        elif is_bullish_candle and is_bearish_next:
            obs.append(("bearish", data.loc[i, "low"], data.loc[i, "high"]))
    return obs[-3:]


def detect_candle_confirmation(df):
    """
    Detect confirmation candle patterns on the last 3 candles.
    Returns: (pattern_name, direction) or (None, None)
    """
    if df is None or len(df) < 3:
        return None, None
    c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]

    def body(c):  return abs(c["close"] - c["open"])
    def wick_lo(c): return min(c["open"], c["close"]) - c["low"]
    def wick_hi(c): return c["high"] - max(c["open"], c["close"])
    def is_bull(c): return c["close"] > c["open"]
    def is_bear(c): return c["close"] < c["open"]

    # Bullish engulfing
    if is_bear(c2) and is_bull(c3) and c3["close"] > c2["open"] and c3["open"] < c2["close"]:
        return "engulfing_bullish", "long"
    # Bearish engulfing
    if is_bull(c2) and is_bear(c3) and c3["close"] < c2["open"] and c3["open"] > c2["close"]:
        return "engulfing_bearish", "short"
    # Bullish pin bar (hammer) : long lower wick, small body
    if body(c3) < (c3["high"] - c3["low"]) * 0.35 and wick_lo(c3) > body(c3) * 2:
        return "pin_bar_bullish", "long"
    # Bearish pin bar (shooting star)
    if body(c3) < (c3["high"] - c3["low"]) * 0.35 and wick_hi(c3) > body(c3) * 2:
        return "pin_bar_bearish", "short"
    return None, None


def detect_absorption(df, n_candles=10, vol_multiplier=2.0, max_body_pct=0.3):
    """
    Detect absorption: high volume + small price body.
    Returns True if absorption is present (warning signal).
    """
    if df is None or len(df) < n_candles:
        return False, 0, 0
    recent   = df.iloc[-n_candles:]
    avg_vol  = recent["volume"].mean()
    last     = df.iloc[-1]
    body_pct = abs(last["close"] - last["open"]) / max(last["open"], 1e-9) * 100
    vol_ratio = last["volume"] / max(avg_vol, 1e-9)
    absorbed  = vol_ratio >= vol_multiplier and body_pct <= max_body_pct
    return absorbed, vol_ratio, body_pct


def calc_volume_profile(df, n_candles=100, bins=30):
    """
    Compute a simple Volume Profile over the last n_candles.
    Returns (poc_price, lvns) where lvns = list of price levels with low volume.
    """
    if df is None or len(df) < 10:
        return None, []
    recent = df.iloc[-n_candles:]
    lo, hi = recent["low"].min(), recent["high"].max()
    if hi <= lo:
        return None, []
    edges  = np.linspace(lo, hi, bins + 1)
    vols   = np.zeros(bins)
    for _, row in recent.iterrows():
        # Distribute the candle's volume across the bins it spans
        lo_c, hi_c, vol = row["low"], row["high"], row["volume"]
        for b in range(bins):
            overlap = max(0, min(hi_c, edges[b+1]) - max(lo_c, edges[b]))
            span    = hi_c - lo_c if hi_c > lo_c else 1e-9
            vols[b] += vol * overlap / span
    poc_bin   = int(np.argmax(vols))
    poc_price = (edges[poc_bin] + edges[poc_bin + 1]) / 2
    avg_vol_b = vols.mean()
    lvns      = [(edges[b] + edges[b+1]) / 2
                 for b in range(bins) if vols[b] < avg_vol_b * 0.4]
    return poc_price, lvns

# ─────────────────────────────────────────────
# CALCULATIONS
# ─────────────────────────────────────────────
def calc_mas(df):
    df = df.copy()
    df["ma5"]   = df["close"].rolling(5).mean()
    df["ma15"]  = df["close"].rolling(15).mean()
    df["ma30"]  = df["close"].rolling(30).mean()
    df["ma200"] = df["close"].rolling(200).mean()
    df["ema9"]  = df["close"].ewm(span=9, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
    return df


def detect_swings(df, n=SWING_N):
    """
    Detect swing highs and lows.
    Returns two boolean Series: is_swing_high, is_swing_low
    """
    highs = df["high"].values
    lows  = df["low"].values
    sh = np.zeros(len(df), dtype=bool)
    sl = np.zeros(len(df), dtype=bool)
    for i in range(n, len(df) - n):
        if all(highs[i] > highs[i-j] for j in range(1, n+1)) and \
           all(highs[i] > highs[i+j] for j in range(1, n+1)):
            sh[i] = True
        if all(lows[i] < lows[i-j] for j in range(1, n+1)) and \
           all(lows[i] < lows[i+j] for j in range(1, n+1)):
            sl[i] = True
    return sh, sl


def assess_structure(df, n=SWING_N):
    """
    Returns: 'bullish', 'bearish', 'ranging', or 'unclear'
    Also returns list of recent swing highs/lows for display.
    """
    sh, sl = detect_swings(df, n)
    swing_highs = df["high"][sh].values[-6:]
    swing_lows  = df["low"][sl].values[-6:]

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "unclear", swing_highs, swing_lows

    # Check last 3 pairs
    def is_hh_hl(highs, lows):
        hh = all(highs[i] > highs[i-1] for i in range(1, min(3, len(highs))))
        hl = all(lows[i]  > lows[i-1]  for i in range(1, min(3, len(lows))))
        return hh and hl

    def is_lh_ll(highs, lows):
        lh = all(highs[i] < highs[i-1] for i in range(1, min(3, len(highs))))
        ll = all(lows[i]  < lows[i-1]  for i in range(1, min(3, len(lows))))
        return lh and ll

    if is_hh_hl(swing_highs, swing_lows):
        return "bullish", swing_highs, swing_lows
    elif is_lh_ll(swing_highs, swing_lows):
        return "bearish", swing_highs, swing_lows
    else:
        return "ranging", swing_highs, swing_lows


def calc_cvd(trades):
    """
    CVD from aggTrades.
    m=True  → buyer is maker → aggressive SELL → negative delta
    m=False → buyer is taker → aggressive BUY  → positive delta
    Returns: (cvd_total, cvd_direction, buy_vol, sell_vol)
    """
    buy_vol  = sum(float(t["q"]) for t in trades if not t["m"])
    sell_vol = sum(float(t["q"]) for t in trades if t["m"])
    cvd = buy_vol - sell_vol
    direction = "bullish" if cvd > 0 else "bearish"
    return cvd, direction, buy_vol, sell_vol


def calc_cvd_divergence(df_price, trades):
    """
    Check if CVD diverges from price over the last N trades window.
    Compares price direction (last close vs N-candles ago) with CVD direction.
    Returns: 'aligned', 'divergence_bearish', 'divergence_bullish'
    """
    price_change = df_price["close"].iloc[-1] - df_price["close"].iloc[-20]
    cvd, direction, buy_vol, sell_vol = calc_cvd(trades)

    if price_change > 0 and direction == "bullish":
        return "aligned_bullish", cvd
    elif price_change < 0 and direction == "bearish":
        return "aligned_bearish", cvd
    elif price_change > 0 and direction == "bearish":
        return "divergence_bearish", cvd  # price up, CVD down = bearish divergence
    elif price_change < 0 and direction == "bullish":
        return "divergence_bullish", cvd  # price down, CVD up = bullish divergence
    return "unclear", cvd


def check_session():
    """Returns current trading session based on UTC time."""
    now_utc = datetime.now(timezone.utc)
    h = now_utc.hour + now_utc.minute / 60.0
    # Paris = UTC+1 (CET) or UTC+2 (CEST)
    # Sessions in UTC:
    # Asian:   23:00 – 07:00 UTC
    # London:  07:00 – 11:00 UTC  (09h-13h Paris)
    # US:      13:00 – 21:00 UTC  (15h-23h Paris)
    if 7.0 <= h < 11.0:
        return "london", "🇬🇧 Londres (09h-13h Paris)", Y
    elif 13.0 <= h < 21.0:
        return "us", "🇺🇸 US (15h-23h Paris)", G
    else:
        return "asian", "🌙 Asiatique — FAKEOUTS", R


def assess_funding(rates):
    """
    Returns (value, signal, direction_hint)
    signal: 'favorable_long', 'favorable_short', 'danger_long', 'danger_short', 'neutral'
    """
    if not rates:
        return None, "unknown", None
    current = float(rates[-1]["fundingRate"])
    recent  = [float(r["fundingRate"]) for r in rates[-3:]]
    avg     = sum(recent) / len(recent)

    if current < -0.01:
        return current, "favorable_long", "long"
    elif -0.01 <= current <= 0.03:
        return current, "neutral", None
    elif 0.03 < current <= 0.05:
        return current, "caution_long", "short"
    elif current > 0.05:
        return current, "danger_long", "short"
    elif current < -0.05:
        return current, "danger_short", "long"
    return current, "neutral", None


def assess_oi(oi_df):
    """
    Compare OI trend vs price trend.
    Returns: 'rising_healthy', 'falling', 'squeeze', 'extreme', or 'unclear'
    """
    if oi_df is None or len(oi_df) < 6:
        return "unclear", 0, 0

    recent_oi    = oi_df["sumOpenInterestValue"].iloc[-6:]
    oi_change    = (recent_oi.iloc[-1] - recent_oi.iloc[0]) / recent_oi.iloc[0] * 100
    oi_current   = oi_df["sumOpenInterestValue"].iloc[-1]
    oi_max       = oi_df["sumOpenInterestValue"].max()
    oi_min       = oi_df["sumOpenInterestValue"].min()
    # Percentile sur 14j — plus stable qu'un % du max ponctuel
    oi_pct_of_max = oi_current / oi_max * 100
    # Percentile rank : quelle fraction des valeurs 14j est sous la valeur actuelle
    oi_percentile = (oi_df["sumOpenInterestValue"] <= oi_current).mean() * 100

    return oi_change, oi_current, oi_pct_of_max, oi_percentile


def assess_ls_ratio(ls_df, top_df=None):
    """
    Returns contrarian signal based on retail long/short ratio.
    """
    if ls_df is None or len(ls_df) == 0:
        return None, "unknown", None

    current_long  = ls_df["longAccount"].iloc[-1]
    current_short = ls_df["shortAccount"].iloc[-1]

    if current_long >= 0.70:
        return current_long, "crowded_long", "short"   # too many longs → short signal
    elif current_short >= 0.70:
        return current_long, "crowded_short", "long"   # too many shorts → long signal
    else:
        return current_long, "neutral", None


# ─────────────────────────────────────────────
# DIRECTION SCORING ENGINE
# ─────────────────────────────────────────────
class SignalBoard:
    def __init__(self):
        self.signals  = []  # (name, direction, weight, detail)
        self.blockers = []  # (name, reason, blocks_direction)
        # blocks_direction: None = bloque tout, 'long' = bloque seulement long,
        #                   'short' = bloque seulement short

    def add(self, name, direction, weight=1, detail=""):
        """direction: 'long', 'short', or None (neutral/unknown)"""
        self.signals.append((name, direction, weight, detail))

    def block(self, name, reason="", direction=None):
        """direction=None bloque tout. 'long' ou 'short' bloque seulement ce sens."""
        self.blockers.append((name, reason, direction))

    @property
    def is_blocked(self):
        """True si au moins un bloqueur global (direction=None)."""
        return any(d is None for _, _, d in self.blockers)

    def is_blocked_for(self, trade_dir):
        """True si ce sens de trade est bloqué (bloqueur global ou bloqueur directionnel)."""
        for _, _, d in self.blockers:
            if d is None or d == trade_dir:
                return True
        return False

    def directional_blockers(self):
        """Retourne les bloqueurs directionnels (long ou short uniquement)."""
        return [(n, r, d) for n, r, d in self.blockers if d is not None]

    def global_blockers(self):
        """Retourne les bloqueurs globaux (bloquent tout)."""
        return [(n, r) for n, r, d in self.blockers if d is None]

    def score(self):
        long_pts  = sum(w for _, d, w, _ in self.signals if d == "long")
        short_pts = sum(w for _, d, w, _ in self.signals if d == "short")
        total     = long_pts + short_pts
        # Neutral placeholders that don't carry directional info
        NEUTRAL_VALUES = {"ok_no_event", "no_absorption", "safe", "ok_no_event"}
        # answered = items with a real directional signal (long/short) OR confirmed neutral
        answered = sum(1 for _, d, _, _ in self.signals
                       if d in ("long", "short") or d in NEUTRAL_VALUES)
        # total_items includes TOTAL_CHECKLIST_ITEMS so completion reflects
        # the full 27-item checklist, not just the automated subset
        completion = answered / TOTAL_CHECKLIST_ITEMS if TOTAL_CHECKLIST_ITEMS else 0

        if total == 0:
            return "wait", None, 0, 0, 0
        ratio = long_pts / total
        if ratio >= 0.80:
            direction = "long"
            strength  = "FORT"
        elif ratio >= 0.65:
            direction = "long"
            strength  = "PROBABLE"
        elif ratio <= 0.20:
            direction = "short"
            strength  = "FORT"
        elif ratio <= 0.35:
            direction = "short"
            strength  = "PROBABLE"
        else:
            direction = None
            strength  = "MIXTES"

        if completion < 0.60:
            return "incomplete", direction, long_pts, short_pts, completion
        if direction is None:
            return "mixed", None, long_pts, short_pts, completion
        if completion >= 0.80 and strength in ("FORT", "PROBABLE"):
            return "go", direction, long_pts, short_pts, completion
        return "possible", direction, long_pts, short_pts, completion


# ─────────────────────────────────────────────
# MAIN ANALYSIS
# ─────────────────────────────────────────────
def run_analysis(symbol=SYMBOL, verbose=False):
    board = SignalBoard()
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    header(f"SOL/USDT × 10  —  {now_utc}")

    # ── 0. Current price ─────────────────────
    price = fetch_current_price(symbol)
    btc_price = fetch_current_price(BTC_SYMBOL)
    if price:
        print(f"\n  {DIM}Prix SOL  {W}{price:.4f} USDT{RST}   "
              f"{DIM}BTC  {W}{btc_price:,.2f} USDT" if btc_price else "")

    # ── 1. STRUCTURE DE MARCHÉ ─────────────────
    section("01 · STRUCTURE DE MARCHÉ  [Gate.io OHLCV]")

    # Daily data → MA200 + structure
    df_daily = fetch_ohlcv(symbol, "1d", limit=250)
    df_daily = calc_mas(df_daily) if df_daily is not None else None

    # 4H data → structure
    df_4h = fetch_ohlcv(symbol, "4h", limit=200)
    df_4h = calc_mas(df_4h) if df_4h is not None else None

    # 15min data → MAs alignment
    df_15m = fetch_ohlcv(symbol, "15m", limit=100)
    df_15m = calc_mas(df_15m) if df_15m is not None else None

    # S1 — Daily structure
    if df_daily is not None:
        struct_d, sh_d, sl_d = assess_structure(df_daily, n=SWING_N)
        color = G if struct_d == "bullish" else (R if struct_d == "bearish" else Y)
        dir_d = "long" if struct_d == "bullish" else ("short" if struct_d == "bearish" else None)
        signal_row("[S1] Tendance Daily (HH/HL vs LH/LL)",
                   struct_d.upper(), color, dir_d,
                   f"derniers hauts: {[f'{v:.2f}' for v in sh_d[-3:]]}")
        board.add("S1_daily_structure", dir_d, weight=2)
    else:
        row("[S1] Tendance Daily", "ERREUR API", R)

    # S2 — MA200 Daily
    if df_daily is not None and df_daily["ma200"].iloc[-1] is not np.nan:
        ma200 = df_daily["ma200"].iloc[-1]
        price_vs_ma200 = "above" if price > ma200 else "below"
        dir_ma200 = "long" if price_vs_ma200 == "above" else "short"
        dist_pct = (price - ma200) / ma200 * 100
        color = G if dir_ma200 == "long" else R
        signal_row("[S2] MA200 Daily",
                   f"{'AU-DESSUS' if dir_ma200=='long' else 'EN-DESSOUS'} ({ma200:.2f})",
                   color, dir_ma200,
                   f"distance: {dist_pct:+.2f}%")
        board.add("S2_ma200", dir_ma200, weight=2)
    else:
        row("[S2] MA200 Daily", "DONNÉES INSUFFISANTES", Y)

    # S3 — 4H structure
    if df_4h is not None:
        struct_4h, sh_4h, sl_4h = assess_structure(df_4h, n=SWING_N_FAST)
        color = G if struct_4h == "bullish" else (R if struct_4h == "bearish" else Y)
        dir_4h = "long" if struct_4h == "bullish" else ("short" if struct_4h == "bearish" else None)
        signal_row("[S3] Structure 4H",
                   struct_4h.upper(), color, dir_4h)
        board.add("S3_4h_structure", dir_4h, weight=1)
    else:
        row("[S3] Structure 4H", "ERREUR API", R)

    # S4 — MAs alignment on 15min  (item S5 dans la checklist HTML)
    if df_15m is not None:
        last = df_15m.iloc[-1]
        ma5, ma15, ma30 = last["ma5"], last["ma15"], last["ma30"]
        ma30_slope = df_15m["ma30"].iloc[-1] - df_15m["ma30"].iloc[-5]
        if ma5 > ma15 > ma30 and ma30_slope > 0:
            dir_ma = "long"
            ma_label = f"MA5({ma5:.2f}) > MA15({ma15:.2f}) > MA30({ma30:.2f}) ↑"
            color = G
        elif ma5 < ma15 < ma30 and ma30_slope < 0:
            dir_ma = "short"
            ma_label = f"MA5({ma5:.2f}) < MA15({ma15:.2f}) < MA30({ma30:.2f}) ↓"
            color = R
        else:
            dir_ma = None
            ma_label = f"MA5({ma5:.2f}) MA15({ma15:.2f}) MA30({ma30:.2f}) — ENCHEVÊTRÉES"
            color = Y
        signal_row("[S5] MAs alignées 15min",
                   dir_ma.upper() if dir_ma else "RANGE", color, dir_ma, ma_label)
        board.add("S5_ma_alignment", dir_ma, weight=1)
    else:
        row("[S5] MAs 15min", "ERREUR API", R)

    # S4 — BOS / CHoCH sur 4H  (item S4 dans la checklist HTML)
    if df_4h is not None:
        sh4, sl4 = detect_swings(df_4h, n=SWING_N_FAST)
        highs_idx = df_4h.index[sh4]
        lows_idx  = df_4h.index[sl4]
        bos_signal = None
        # Label contextuel selon la structure globale
        if struct_4h == "ranging":
            bos_label = "RANGING 4H — pas de BOS récent"
        elif struct_4h == "bullish":
            bos_label = "structure haussière intacte"
        elif struct_4h == "bearish":
            bos_label = "structure baissière intacte"
        else:
            bos_label = "structure indéfinie"
        if len(highs_idx) >= 2 and len(lows_idx) >= 2:
            last_high  = df_4h.loc[highs_idx[-1], "high"]
            prev_high  = df_4h.loc[highs_idx[-2], "high"]
            last_low   = df_4h.loc[lows_idx[-1],  "low"]
            prev_low   = df_4h.loc[lows_idx[-2],  "low"]
            cur_close  = df_4h["close"].iloc[-1]
            # BOS haussier : prix dépasse le dernier pivot haut
            if cur_close > last_high:
                bos_signal = "bullish_bos"
                bos_label  = f"BOS HAUSSIER — cassure de {last_high:.2f}"
            # BOS baissier : prix casse sous le dernier pivot bas
            elif cur_close < last_low:
                bos_signal = "bearish_bos"
                bos_label  = f"BOS BAISSIER — cassure de {last_low:.2f}"
            # CHoCH : premier bas plus bas dans une tendance haussière
            elif struct_4h == "bullish" and last_low < prev_low:
                bos_signal = "choch_warning"
                bos_label  = f"CHoCH DÉTECTÉ — bas plus bas ({last_low:.2f} < {prev_low:.2f})"
            elif struct_4h == "bearish" and last_high > prev_high:
                bos_signal = "choch_warning"
                bos_label  = f"CHoCH DÉTECTÉ — haut plus haut ({last_high:.2f} > {prev_high:.2f})"

        bos_dir = (
            "long"  if bos_signal == "bullish_bos"
            else "short" if bos_signal == "bearish_bos"
            else None
        )
        color = G if bos_dir == "long" else (R if bos_dir == "short" else (Y if bos_signal == "choch_warning" else DIM))
        signal_row("[S4] BOS / CHoCH 4H", bos_label, color, bos_dir)
        board.add("S4_bos_choch", bos_dir, weight=1)
        if bos_signal == "choch_warning":
            board.block("choch_4h", f"CHoCH detecte sur 4H — potentiel retournement")
    else:
        row("[S4] BOS/CHoCH 4H", "ERREUR API", R)

    # ── 2. MACRO & SESSION ────────────────────
    section("02 · SESSION & MACRO")

    session_id, session_name, session_color = check_session()
    row("[E2] Session de trading", session_name, session_color)
    if session_id == "asian":
        board.block("session", "Session asiatique — fakeouts fréquents")
    else:
        # M2 : BTC en tendance claire (pas en range) — item neutre scoré
        # On le résoudra après avoir calculé le biais BTC (struct_btc)
        pass  # filled below after BTC fetch

    # M1 — Macro calendar (Forex Factory JSON)
    macro_events = fetch_macro_calendar()
    if macro_events is None:
        row("[M1] Calendrier macro", "API indisponible — verifier manuellement", Y,
            "→ fr.investing.com/economic-calendar")
    elif len(macro_events) == 0:
        row("[M1] Calendrier macro", "Aucun event HIGH dans les 4h", G)
        board.add("M1_macro", "ok_no_event", weight=1)
    else:
        names = ", ".join(e["title"] for e in macro_events[:3])
        row("[M1] Calendrier macro", f"⚠ {len(macro_events)} EVENT(S) HIGH dans les 4h", R,
            names)
        board.block("macro_event", f"Event(s) macro imminents: {names}")

    # BTC structure (même analyse que SOL)
    df_btc_d  = fetch_ohlcv(BTC_SYMBOL, "1d", limit=250)
    df_btc_4h = fetch_ohlcv(BTC_SYMBOL, "4h", limit=100)
    if df_btc_d is not None:
        df_btc_d = calc_mas(df_btc_d)
        struct_btc, _, _ = assess_structure(df_btc_d, n=SWING_N)
        ma200_btc = df_btc_d["ma200"].iloc[-1]
        dir_btc = "long" if (struct_btc == "bullish" and btc_price > ma200_btc) \
                  else "short" if (struct_btc == "bearish" and btc_price < ma200_btc) \
                  else None
        color = G if dir_btc == "long" else (R if dir_btc == "short" else Y)
        signal_row("[M3] Biais BTC",
                   f"{struct_btc.upper()} / MA200 {'OK ▲' if btc_price > ma200_btc else 'KO ▼'}",
                   color, dir_btc,
                   f"MA200={ma200_btc:,.2f}")
        board.add("M3_btc_bias", dir_btc, weight=1)
        # M2 : BTC en tendance claire (non-ranging)
        btc_trending = struct_btc in ("bullish", "bearish")
        m2_dir = dir_btc if btc_trending else None
        m2_label = "TENDANCE CLAIRE" if btc_trending else "EN RANGE — signal non exploitable"
        m2_color = (G if dir_btc == "long" else R if dir_btc == "short" else Y)
        row("[M2] BTC en tendance", m2_label, m2_color,
            "" if btc_trending else "attends une tendance BTC directionnelle")
        board.add("M2_btc_trending", m2_dir, weight=1)
    else:
        row("[M3] Biais BTC", "ERREUR API", R)

    # ── 3. SENTIMENT & DÉRIVÉS ────────────────
    section("03 · SENTIMENT & DÉRIVÉS  [Gate.io]")

    # F1 — Funding Rate
    funding_data = fetch_funding_rate(symbol)
    if funding_data:
        fr_val, fr_signal, fr_dir = assess_funding(funding_data)
        fr_pct = fr_val * 100 if fr_val else 0
        recent_fr = [float(r["fundingRate"]) * 100 for r in funding_data[-3:]]
        color = (G if fr_signal in ("favorable_long", "neutral")
                 else Y if "caution" in fr_signal
                 else R)
        annualized = fr_pct * 3 * 365  # 3 periods/day
        signal_row("[F1] Funding Rate",
                   f"{fr_pct:+.4f}%  ({fr_signal})",
                   color, fr_dir,
                   f"annualisé: {annualized:+.1f}%  |  3 derniers: {[f'{v:+.4f}%' for v in recent_fr]}")
        board.add("F1_funding", fr_dir, weight=1)
        if abs(fr_pct) > 0.07:
            board.block("funding_extreme",
                        f"Funding extrême ({fr_pct:+.4f}%) — flush probable")
    else:
        row("[F1] Funding Rate", "ERREUR API", R)

    # F2 — Open Interest (fenêtre 14 jours pour un max de référence stable)
    oi_df = fetch_open_interest_history(symbol, period="1h", limit=336)
    if oi_df is not None:
        oi_change, oi_current, oi_pct_max, oi_percentile = assess_oi(oi_df)
        oi_m      = oi_current / 1e6
        is_extreme = oi_percentile >= 90
        color = (R if oi_percentile >= 90
                 else Y if oi_percentile >= 75
                 else G)
        row("[F2] Open Interest",
            f"${oi_m:.1f}M  (6h: {oi_change:+.2f}%  |  percentile 14j: {oi_percentile:.0f}%)",
            color,
            "⚠ EXTREME 14j — flush probable" if is_extreme else "")
        board.add("F2_oi", None, weight=1)
        if is_extreme:
            board.block("oi_extreme",
                        f"OI au {oi_percentile:.0f}e percentile sur 14j — marche surexpose")
    else:
        row("[F2] Open Interest", "ERREUR API", R)

    # F4 — Liquidations récentes (Gate.io /liq_orders — données réelles)
    liq_data = fetch_liquidations(symbol, limit=100)
    if liq_data:
        now_ts   = datetime.now(timezone.utc).timestamp()
        cutoff   = now_ts - 8 * 3600   # dernières 8h
        recent_liqs = [l for l in liq_data if int(l.get("time", 0)) >= cutoff]
        long_liqs  = sum(abs(float(l["size"])) for l in recent_liqs if float(l["size"]) > 0)
        short_liqs = sum(abs(float(l["size"])) for l in recent_liqs if float(l["size"]) < 0)
        total_liqs = long_liqs + short_liqs
        # Seuil minimum : 500 contracts (~40 000$ à 80$/SOL) pour éviter les faux positifs
        LIQ_MIN_THRESHOLD = 500
        dominant = max(long_liqs, short_liqs)
        if total_liqs == 0 or dominant < LIQ_MIN_THRESHOLD:
            liq_hint  = f"aucune liquidation significative (8h, total: {total_liqs:.0f} contracts)"
            liq_color = G
            liq_dir   = "safe"
            liq_block_reason = None
        elif long_liqs > short_liqs * 2 and long_liqs >= LIQ_MIN_THRESHOLD:
            liq_hint  = f"pic LIQS LONGS ({long_liqs:.0f} contracts) — eviter SHORT ici (bottom possible)"
            liq_color = Y
            liq_dir   = None
            liq_block_reason = f"Longs viennent d'etre liquides ({long_liqs:.0f} contracts) — ne pas shorter un potentiel bottom"
        elif short_liqs > long_liqs * 2 and short_liqs >= LIQ_MIN_THRESHOLD:
            liq_hint  = f"pic LIQS SHORTS ({short_liqs:.0f} contracts) — eviter LONG ici (top possible)"
            liq_color = Y
            liq_dir   = None
            liq_block_reason = f"Shorts viennent d'etre liquides ({short_liqs:.0f} contracts) — ne pas longer un potentiel top"
        else:
            liq_hint  = f"liqs equilibrees (L:{long_liqs:.0f} / S:{short_liqs:.0f} contracts)"
            liq_color = DIM
            liq_dir   = "safe"
            liq_block_reason = None
        row("[F4] Liquidations récentes (8h)", liq_hint, liq_color)
        # Score F4 : "safe" = signal neutre confirmé, None = pas de direction exploitable
        board.add("F4_liquidations", None, weight=1)
        if liq_dir is None and liq_block_reason:
            # Bloque uniquement la direction risquée, pas l'opposée
            blocked_dir = "short" if long_liqs > short_liqs else "long"
            board.block("recent_liquidation", liq_block_reason, direction=blocked_dir)
    else:
        row("[F4] Liquidations récentes", "ERREUR API", R)

    # F3 — Long/Short Ratio
    ls_df  = fetch_long_short_ratio(symbol, period="1h", limit=12)
    top_df = fetch_top_trader_ratio(symbol, period="1h", limit=12)
    if ls_df is not None:
        ls_long, ls_signal, ls_dir = assess_ls_ratio(ls_df)
        ls_short = 1 - ls_long if ls_long else None
        color = (G if ls_dir == "long" else R if ls_dir == "short" else DIM)
        retail_str = f"Retail: {ls_long*100:.1f}% L / {ls_short*100:.1f}% S"
        top_str = ""
        if top_df is not None:
            tl = top_df["longAccount"].iloc[-1]
            top_str = f"  |  Top traders: {tl*100:.1f}% L"
        signal_row("[F3] Ratio Long/Short (contrarian)",
                   ls_signal.upper(), color, ls_dir,
                   retail_str + top_str)
        board.add("F3_ls_ratio", ls_dir, weight=1)
    else:
        row("[F3] Ratio Long/Short", "ERREUR API", R)

    # ── 4. CVD ────────────────────────────────
    section("04 · CVD — PRESSION D'ACHAT  [Gate.io recent-trade]")

    trades_result = fetch_trades_cvd(symbol, limit=1000)
    if trades_result:
        cvd_val, cvd_direction, buy_vol, sell_vol = trades_result
        # Compare price direction with CVD direction
        price_change = 0
        if df_15m is not None and len(df_15m) >= 20:
            price_change = df_15m["close"].iloc[-1] - df_15m["close"].iloc[-20]

        if price_change > 0 and cvd_direction == "bullish":
            cvd_status = "aligned_bullish"
        elif price_change < 0 and cvd_direction == "bearish":
            cvd_status = "aligned_bearish"
        elif price_change > 0 and cvd_direction == "bearish":
            cvd_status = "divergence_bearish"
        elif price_change < 0 and cvd_direction == "bullish":
            cvd_status = "divergence_bullish"
        else:
            cvd_status = "unclear"

        cvd_dir = (
            "long"  if cvd_status == "aligned_bullish"
            else "short" if cvd_status == "aligned_bearish"
            else None
        )
        color = (G if "bullish" in cvd_status
                 else R if "bearish" in cvd_status
                 else Y)
        buy_ratio = buy_vol / (buy_vol + sell_vol) * 100 if (buy_vol + sell_vol) > 0 else 50
        signal_row("[C1] CVD (last 1000 trades)",
                   cvd_status.upper().replace("_", " "),
                   color, cvd_dir,
                   f"CVD={cvd_val:+.0f} contracts  |  buy%={buy_ratio:.1f}%  |  sell%={100-buy_ratio:.1f}%")
        board.add("C1_cvd", cvd_dir, weight=1)
        if "divergence" in cvd_status:
            board.block("cvd_divergence",
                        f"Divergence CVD ({cvd_status}) — signal annule")
    else:
        row("[C1] CVD", "ERREUR API", R)

    # CVD 4H : Gate.io ne fournit pas le taker split par bougie
    # On utilise le momentum prix (close vs open) sur les 10 dernières bougies 4H
    # comme proxy directionnel — simple mais sans bug d'unité
    if df_4h is not None and len(df_4h) >= 10:
        recent = df_4h.iloc[-10:]
        bullish_candles = (recent["close"] > recent["open"]).sum()
        bearish_candles = (recent["close"] < recent["open"]).sum()
        bull_ratio = bullish_candles / len(recent)
        cvd4h_dir = "long" if bull_ratio > 0.60 else "short" if bull_ratio < 0.40 else None
        color = G if cvd4h_dir == "long" else (R if cvd4h_dir == "short" else Y)
        row("[C2] Momentum 4H (10 dernieres bougies)",
            f"{bullish_candles} haussières / {bearish_candles} baissières"
            f"  ->  {'BULLISH' if cvd4h_dir=='long' else 'BEARISH' if cvd4h_dir=='short' else 'NEUTRE'}",
            color)
        board.add("C2_cvd_4h", cvd4h_dir, weight=1)

    # ── 5. HEATMAP ────────────────────────────
    section("05 · LIQUIDATION HEATMAP  [Manuel requis]")
    row("[L1] Liquidation Heatmap",
        "⚠ VÉRIFIER MANUELLEMENT", Y,
        "→ https://coinank.com/chart/derivatives/liq-heat-map/solusdt/1w")
    row("", "(aucune API publique disponible)", DIM)

    # ── 6. ENTRY ANALYSIS ────────────────────
    section("06 · ZONE D'ENTRÉE & TIMING  [OHLCV + Gate.io]")

    # C3 — Absorption (volume fort + body plat)
    absorbed, vol_ratio, body_pct = detect_absorption(df_15m)
    if absorbed:
        row("[C3] Absorption 15min",
            f"ABSORPTION DETECTEE (vol x{vol_ratio:.1f} avg, body {body_pct:.2f}%)", R,
            "volume fort sans mouvement = vendeurs/acheteurs cachés")
        board.block("absorption", "Absorption detectee sur 15min — attendre resolution")
    else:
        row("[C3] Absorption 15min", "Aucune absorption detectee", G)
        board.add("C3_absorption", "no_absorption", weight=1)

    # E1 — Order Blocks + FVG sur 15min
    if df_15m is not None and price:
        fvgs = detect_fvg(df_15m, n_candles=80)
        obs  = detect_order_blocks(df_15m, n_candles=80)
        # Find closest FVG to current price
        nearest_fvg  = None
        nearest_dist = float("inf")
        for ftype, flo, fhi, _ in fvgs:
            mid  = (flo + fhi) / 2
            dist = abs(price - mid) / price * 100
            if dist < nearest_dist:
                nearest_dist = dist
                nearest_fvg  = (ftype, flo, fhi, dist)
        if nearest_fvg:
            ftype, flo, fhi, dist = nearest_fvg
            # Prix réellement à l'intérieur du FVG
            strictly_in = flo <= price <= fhi
            # Prix proche mais pas encore dans le FVG (en approche)
            approaching = not strictly_in and dist < 0.8
            # FVG déjà comblé : bullish FVG comblé si prix > fhi, bearish si prix < flo
            filled = (ftype == "bullish" and price > fhi) or \
                     (ftype == "bearish" and price < flo)

            if strictly_in:
                zone_label = "DANS LA ZONE"
                in_zone = True
                color   = G
            elif approaching and not filled:
                zone_label = f"EN APPROCHE ({dist:.2f}%)"
                in_zone = True   # encore exploitable
                color   = Y
            elif filled:
                zone_label = f"COMBLÉ — prix {'au-dessus' if ftype=='bullish' else 'en dessous'}"
                in_zone = False
                color   = DIM
            else:
                zone_label = f"hors zone ({dist:.2f}%)"
                in_zone = False
                color   = DIM

            e1_dir = "long"  if (ftype == "bullish" and in_zone) \
                else "short" if (ftype == "bearish" and in_zone) \
                else None
            row("[E1] FVG le plus proche (15min)",
                f"{ftype.upper()} FVG [{flo:.2f} - {fhi:.2f}]  dist: {dist:.2f}%",
                color, zone_label)
            board.add("E1_fvg_ob", e1_dir, weight=1)
        else:
            row("[E1] FVG / Order Block", "Aucun FVG recent detectable", DIM)
            board.add("E1_fvg_ob", None, weight=1)
    else:
        row("[E1] FVG / Order Block", "DONNÉES INSUFFISANTES", DIM)

    # E3 — Confirmation bougie (15min)
    pattern, pattern_dir = detect_candle_confirmation(df_15m)
    if pattern:
        color = G if pattern_dir == "long" else R
        signal_row("[E3] Confirmation bougie 15min",
                   pattern.upper().replace("_", " "), color, pattern_dir)
        board.add("E3_candle_confirm", pattern_dir, weight=1)
    else:
        row("[E3] Confirmation bougie 15min", "Pas de pattern clair", DIM)
        board.add("E3_candle_confirm", None, weight=1)

    # Détection contradiction E1 vs E3
    e1_dir_val = next((d for n,d,_,_ in board.signals if n == "E1_fvg_ob"), None)
    if (e1_dir_val and pattern_dir
            and e1_dir_val != pattern_dir):
        row("[!] Contradiction E1/E3",
            f"FVG {e1_dir_val.upper()} vs bougie {pattern_dir.upper()} — signaux opposés",
            Y, "prendre le signal E3 (bougie) comme confirmation prioritaire")

    # E4 — Volume Profile POC (15min, 100 dernières bougies)
    poc_price, lvns = calc_volume_profile(df_15m, n_candles=100, bins=40)
    if poc_price and price:
        dist_poc = abs(price - poc_price) / price * 100
        # Nearest LVN in direction — minimum 0.5% de distance pour être exploitable
        min_dist = price * 0.005
        lvns_above = [l for l in lvns if l > price + min_dist]
        lvns_below = [l for l in lvns if l < price - min_dist]
        lvn_above  = min(lvns_above) if lvns_above else None
        lvn_below  = max(lvns_below) if lvns_below else None
        color = G if dist_poc < 1.0 else DIM
        lvn_str = ""
        if lvn_above: lvn_str += f"  LVN↑ {lvn_above:.2f}"
        if lvn_below: lvn_str += f"  LVN↓ {lvn_below:.2f}"
        row("[E4] Volume Profile (15min)",
            f"POC: {poc_price:.2f}  (dist: {dist_poc:.2f}%){lvn_str}", color)
        e4_dir = None  # POC ne donne pas de direction mais confirme la zone
        board.add("E4_vp_poc", e4_dir, weight=1)

        # R1 suggestion SL basé sur les pivots structurels
        if df_4h is not None and price:
            _, sh4, sl4 = assess_structure(df_4h, n=SWING_N_FAST)
            # SL long  = dernier swing low SOUS le prix actuel
            sl_below = [v for v in sl4 if v < price]
            # SL short = dernier swing high AU-DESSUS du prix actuel
            sh_above = [v for v in sh4 if v > price]

            def sl_warning(dist_pct, direction):
                """Retourne warning si SL trop proche de la liquidation x10."""
                lev_impact = dist_pct * 10   # impact en % sur la position
                if lev_impact >= 90:
                    return f" !! TROP LARGE — liquidation x10 à {direction}10% (SL jamais atteint)"
                elif lev_impact >= 70:
                    return f" ! proche liquidation ({lev_impact:.0f}% de la position)"
                return ""

            if sl_below and sh_above:
                nearest_hl    = sl_below[-1]
                nearest_lh    = sh_above[-1]
                sl_long_dist  = (price - nearest_hl) / price * 100
                sl_short_dist = (nearest_lh - price) / price * 100
                warn_long  = sl_warning(sl_long_dist,  "-")
                warn_short = sl_warning(sl_short_dist, "+")
                col = R if (warn_long and "TROP" in warn_long) or (warn_short and "TROP" in warn_short) else DIM
                row("[R1] SL suggéré (auto)",
                    f"LONG: sous {nearest_hl:.2f} (-{sl_long_dist:.1f}%){warn_long}"
                    f"  |  SHORT: dessus {nearest_lh:.2f} (+{sl_short_dist:.1f}%){warn_short}",
                    col, "a valider — placer 0.3% au-dela du niveau")
            elif sl_below:
                nearest_hl   = sl_below[-1]
                sl_long_dist = (price - nearest_hl) / price * 100
                warn_long    = sl_warning(sl_long_dist, "-")
                col = R if warn_long and "TROP" in warn_long else DIM
                row("[R1] SL suggéré (auto)",
                    f"LONG: sous {nearest_hl:.2f} (-{sl_long_dist:.1f}%){warn_long}"
                    f"  |  SHORT: pas de pivot haut visible",
                    col, "a valider")
            elif sh_above:
                nearest_lh    = sh_above[-1]
                sl_short_dist = (nearest_lh - price) / price * 100
                warn_short    = sl_warning(sl_short_dist, "+")
                col = R if warn_short and "TROP" in warn_short else DIM
                row("[R1] SL suggéré (auto)",
                    f"LONG: pas de pivot bas visible"
                    f"  |  SHORT: dessus {nearest_lh:.2f} (+{sl_short_dist:.1f}%){warn_short}",
                    col, "a valider")
            else:
                row("[R1] SL suggéré (auto)",
                    "Pas de pivot structurel identifiable — SL manuel requis", Y)
    else:
        row("[E4] Volume Profile", "DONNÉES INSUFFISANTES", DIM)

    # ─────────────────────────────────────────
    # VERDICT FINAL
    # ─────────────────────────────────────────
    section("── VERDICT ──")

    status, direction, long_pts, short_pts, completion = board.score()
    total_pts    = long_pts + short_pts
    NEUTRAL_VALUES = {"ok_no_event", "no_absorption", "safe"}
    auto_answered = sum(1 for _, d, _, _ in board.signals
                        if d in ("long", "short") or d in NEUTRAL_VALUES)

    print(f"\n  {DIM}Points LONG   {G}{long_pts:>4} pts")
    print(f"  {DIM}Points SHORT  {R}{short_pts:>4} pts")
    print(f"  {DIM}Auto  {W}{auto_answered:>2}/{AUTO_ITEMS} items{DIM}  |  "
          f"Manuel  {W}{MANUAL_ITEMS} items restants{DIM}  |  "
          f"Completion  {W}{completion*100:.0f}%/{TOTAL_CHECKLIST_ITEMS} items")

    if board.is_blocked:
        # Bloqueurs globaux présents — bloquent tout trading
        print()
        for name, reason, bdir in board.blockers:
            if bdir is None:
                print(f"  {Back.RED}{Style.BRIGHT} BLOQUEUR {RST}  {R}{name}{DIM}: {reason}")
            else:
                print(f"  {Back.RED}{Style.BRIGHT} BLOQUEUR {bdir.upper():<6}{RST}  {R}{name}{DIM}: {reason}")
        verdict_box("🚫  BLOQUEUR ACTIF — NE PAS TRADER", Fore.RED, Back.RED)

    elif board.directional_blockers() and not board.is_blocked:
        # Seulement des bloqueurs directionnels — l'autre sens reste envisageable
        print()
        for name, reason, bdir in board.directional_blockers():
            print(f"  {Back.RED}{Style.BRIGHT} BLOQUEUR {bdir.upper():<6}{RST}  {R}{name}{DIM}: {reason}")
        if direction and board.is_blocked_for(direction):
            opposite = "LONG" if direction == "short" else "SHORT"
            verdict_box(f"⚠  {direction.upper()} BLOQUÉ — {opposite} ENVISAGEABLE", Fore.YELLOW)
        elif direction:
            pts = long_pts if direction == "long" else short_pts
            col = Fore.GREEN if direction == "long" else Fore.RED
            arrow = "▲" if direction == "long" else "▼"
            verdict_box(f"{arrow}  {direction.upper()} POSSIBLE  ({pts}/{total_pts} pts)", col)

    elif status == "go" and direction == "long":
        print()
        verdict_box(f"▲  LONG — TRADE OK  ({long_pts}/{total_pts} pts)", Fore.GREEN, Back.GREEN)

    elif status == "go" and direction == "short":
        print()
        verdict_box(f"▼  SHORT — TRADE OK  ({short_pts}/{total_pts} pts)", Fore.RED, Back.RED)

    elif status == "possible" and direction:
        dir_str = "LONG POSSIBLE" if direction == "long" else "SHORT POSSIBLE"
        print()
        verdict_box(f"~  {dir_str}  — COMPLÉTER L'ANALYSE", Fore.YELLOW)

    elif status == "mixed":
        print()
        verdict_box("⚡  SIGNAUX MIXTES — NE PAS TRADER", Fore.MAGENTA)

    else:
        print()
        verdict_box("—  ANALYSE INCOMPLÈTE — ATTENDRE", Fore.WHITE)

    # Reminder for manual items
    print()
    print(DIM + f"  {MANUAL_ITEMS} items nécessitant tes données personnelles (non automatisables):")
    print(DIM + "  ├─ [L2] Cluster contre dir.  → heatmap CoinAnk (bloqueur si cluster proche)")
    print(DIM + "  ├─ [L3] Cluster dans dir.    → heatmap CoinAnk (aimant de prix ?)")
    print(DIM + "  ├─ [R2] R:R minimum 1:2      → calculatrice checklist HTML (besoin prix entrée)")
    print(DIM + "  ├─ [R3] Taille de position   → calculatrice checklist HTML (besoin capital)")
    print(DIM + "  ├─ [R4] TPs partiels définis → noter TP1/TP2 avant d'entrer")
    print(DIM + "  ├─ [R5] Pas de position cor. → vérifier tes positions sur XT.com")
    print(DIM + "  └─ [R1] SL structurel        → suggestion ci-dessus, à valider manuellement")
    print()

    return status, direction, board


# ─────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────
def send_telegram(text: str) -> bool:
    """Envoie un message Telegram en texte brut. Retourne True si OK."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(Y + "  [TELEGRAM] Token ou chat_id manquant — notif ignoree." + RST)
        print(DIM + "  Exporte TELEGRAM_TOKEN et TELEGRAM_CHAT_ID." + RST)
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    # Pas de parse_mode — texte brut, zero risque de 400 Bad Request
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text":    text,
    }
    try:
        r = requests.post(url, json=payload, timeout=TIMEOUT)
        if not r.ok:
            # Log le body pour debugger facilement
            print(R + f"  [TELEGRAM] Erreur {r.status_code}: {r.text[:300]}" + RST)
            return False
        print(G + "  [TELEGRAM] Notification envoyee." + RST)
        return True
    except requests.exceptions.RequestException as e:
        print(R + f"  [TELEGRAM] Erreur reseau : {e}" + RST)
        return False


def build_telegram_message(status: str, direction, board, price: float,
                            long_pts: int, short_pts: int,
                            now_str: str, symbol: str) -> str:
    """Construit le message Telegram en texte brut."""
    dir_label = "LONG" if direction == "long" else ("SHORT" if direction == "short" else "?")

    SEP = "-" * 40

    if status == "go":
        header = f"[SIGNAL] {'▲' if direction=='long' else '▼'} {dir_label} -- SOL/USDT x10"
    elif status == "blocked":
        header = "[BLOQUEUR] NE PAS TRADER -- SOL/USDT x10"
    elif status == "possible":
        header = f"[POSSIBLE] {'▲' if direction=='long' else '▼'} {dir_label} -- SOL/USDT x10"
    else:
        header = "[INFO] Pas de signal -- SOL/USDT x10"

    lines = [
        SEP,
        header,
        SEP,
        f"Date  : {now_str}",
        f"Prix  : {price:.4f} USDT",
        f"Score : LONG {long_pts} pts / SHORT {short_pts} pts",
        "",
    ]

    all_blockers = board.blockers  # list of (name, reason, direction)
    if all_blockers:
        lines.append("== BLOQUEURS ==")
        for name, reason, bdir in all_blockers:
            r    = reason[:120] + "..." if len(reason) > 120 else reason
            dlbl = f"[{bdir.upper()}] " if bdir else ""
            lines.append(f"  ! {dlbl}{name}: {r}")
        lines.append("")

    actionable = [s for s in board.signals if s[1] is not None]
    if actionable:
        lines.append("== SIGNAUX ==")
        for name, sig_dir, weight, detail in actionable[-8:]:
            arrow = "+" if sig_dir == "long" else "-"
            w_str = " [x2]" if weight == 2 else ""
            lines.append(f"  {arrow} {name}{w_str}")
        lines.append("")

    lines += [
        "== VERIFICATION MANUELLE ==",
        "  Heatmap : coinank.com/chart/derivatives/liq-heat-map/solusdt/1w",
        "  Macro   : fr.investing.com/economic-calendar",
        "  CVD     : velo.xyz/futures/SOL",
        SEP,
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────
# STATE — évite les doublons de notif
# ─────────────────────────────────────────────
def load_state() -> dict:
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except OSError as e:
        print(Y + f"  [STATE] Impossible d'écrire {STATE_FILE}: {e}" + RST)


def signal_changed(new_status: str, new_direction, old_state: dict,
                   board=None) -> bool:
    """
    Retourne True si le signal a changé depuis la dernière notification.
    Compare status, direction ET les bloqueurs actifs (pour éviter
    les doublons quand seul le status 'blocked' persiste mais que
    les bloqueurs changent — ex: oi_extreme remplacé par cvd_divergence).
    """
    if old_state.get("status") != new_status:
        return True
    if old_state.get("direction") != new_direction:
        return True
    # Si le state est vide (cache miss GitHub Actions), on considère comme changé
    if not old_state:
        return True
    # Compare le fingerprint des bloqueurs actifs
    if board is not None:
        new_blockers = frozenset(n for n, _, _ in board.blockers)
        old_blockers = frozenset(old_state.get("blockers", []))
        if new_blockers != old_blockers:
            return True
    return False


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SOL/USDT x10 automated signal analyzer")
    parser.add_argument("--symbol",  default=SYMBOL,  help="Futures symbol (default: SOLUSDT)")
    parser.add_argument("--verbose", action="store_true", help="Extra debug info")
    parser.add_argument("--notify",  action="store_true",
                        help="Envoie une notification Telegram si signal actionnable")
    parser.add_argument("--force",   action="store_true",
                        help="Force l'envoi Telegram meme si le signal n'a pas change")
    parser.add_argument("--watch",   type=int, default=0,
                        help="Refresh interval en secondes (0 = run once, local seulement)")
    args = parser.parse_args()

    if args.watch > 0:
        # Mode local en boucle — sans notif pour eviter le spam
        print(C + f"\n  Mode watch: refresh toutes les {args.watch}s  (Ctrl+C pour quitter)" + RST)
        try:
            while True:
                print("\033[2J\033[H", end="")
                run_analysis(symbol=args.symbol, verbose=args.verbose)
                print(DIM + f"\n  Prochain refresh dans {args.watch}s...")
                time.sleep(args.watch)
        except KeyboardInterrupt:
            print(C + "\n  Arret." + RST)

    else:
        # Run unique (mode cron / GitHub Actions)
        price = fetch_current_price(args.symbol) or 0.0
        status, direction, board = run_analysis(symbol=args.symbol, verbose=args.verbose)

        if args.notify:
            now_str    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            old_state  = load_state()
            long_pts   = sum(w for _, d, w, _ in board.signals if d == "long")
            short_pts  = sum(w for _, d, w, _ in board.signals if d == "short")

            # Statut "blocked" si des bloqueurs sont actifs, sinon on utilise status
            effective_status = "blocked" if board.is_blocked else status

            should_notify = (
                args.force
                or (effective_status in NOTIFY_ON_STATUSES
                    and signal_changed(effective_status, direction, old_state, board))
            )

            if should_notify:
                msg = build_telegram_message(
                    effective_status, direction, board, price,
                    long_pts, short_pts, now_str, args.symbol
                )
                sent = send_telegram(msg)
                if sent:
                    save_state({
                        "status":    effective_status,
                        "direction": direction,
                        "price":     price,
                        "timestamp": now_str,
                        "blockers":  [n for n, _, _ in board.blockers],
                    })
            else:
                if effective_status not in NOTIFY_ON_STATUSES:
                    print(DIM + f"\n  [NOTIFY] Signal '{effective_status}' non actionnable — pas de notif." + RST)
                else:
                    print(DIM + f"\n  [NOTIFY] Signal inchange ({effective_status}/{direction}) — pas de notif." + RST)
                    print(DIM + f"           Utilise --force pour forcer l'envoi." + RST)
