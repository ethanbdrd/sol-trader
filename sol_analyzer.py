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

# Pivot detection: N candles each side to qualify as swing high/low
SWING_N     = 3
# Minimum number of swings to assess structure
MIN_SWINGS  = 4


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
    oi_pct_of_max = oi_current / oi_max * 100

    return oi_change, oi_current, oi_pct_of_max


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
        self.signals = []  # (name, direction, weight, detail)
        self.blockers = [] # (name, reason)

    def add(self, name, direction, weight=1, detail=""):
        """direction: 'long', 'short', or None (neutral/unknown)"""
        self.signals.append((name, direction, weight, detail))

    def block(self, name, reason=""):
        self.blockers.append((name, reason))

    @property
    def is_blocked(self):
        return len(self.blockers) > 0

    def score(self):
        long_pts  = sum(w for _, d, w, _ in self.signals if d == "long")
        short_pts = sum(w for _, d, w, _ in self.signals if d == "short")
        total     = long_pts + short_pts
        answered  = sum(1 for _, d, _, _ in self.signals if d is not None)
        total_items = len(self.signals)

        completion = answered / total_items if total_items else 0

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
        struct_4h, sh_4h, sl_4h = assess_structure(df_4h, n=SWING_N)
        color = G if struct_4h == "bullish" else (R if struct_4h == "bearish" else Y)
        dir_4h = "long" if struct_4h == "bullish" else ("short" if struct_4h == "bearish" else None)
        signal_row("[S3] Structure 4H",
                   struct_4h.upper(), color, dir_4h)
        board.add("S3_4h_structure", dir_4h, weight=1)
    else:
        row("[S3] Structure 4H", "ERREUR API", R)

    # S4 — MAs alignment on 15min
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
        signal_row("[S4] MAs alignées 15min",
                   dir_ma.upper() if dir_ma else "RANGE", color, dir_ma, ma_label)
        board.add("S4_ma_alignment", dir_ma, weight=1)
    else:
        row("[S4] MAs 15min", "ERREUR API", R)

    # ── 2. MACRO & SESSION ────────────────────
    section("02 · SESSION & MACRO")

    session_id, session_name, session_color = check_session()
    row("[M2] Session actuelle", session_name, session_color)
    if session_id == "asian":
        board.block("session", "Session asiatique — fakeouts fréquents")

    row("[M1] Calendrier macro",
        "⚠ VÉRIFIER MANUELLEMENT",
        Y,
        "→ https://fr.investing.com/economic-calendar/")

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

    # F2 — Open Interest
    oi_df = fetch_open_interest_history(symbol, period="1h", limit=48)
    if oi_df is not None:
        oi_change, oi_current, oi_pct_max = assess_oi(oi_df)
        oi_m = oi_current / 1e6
        color = (R if oi_pct_max > 90
                 else Y if oi_pct_max > 75
                 else G)
        row("[F2] Open Interest",
            f"${oi_m:.1f}M  (6h change: {oi_change:+.2f}%  |  {oi_pct_max:.0f}% du max)",
            color,
            "⚠ EXTRÊME — flush probable" if oi_pct_max > 90 else "")
        if oi_pct_max > 90:
            board.block("oi_extreme", f"OI à {oi_pct_max:.0f}% de son max historique récent")
    else:
        row("[F2] Open Interest", "ERREUR API", R)

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

    # ── 6. ENTRY TIMING ──────────────────────
    section("06 · TIMING")

    row("[E2] Session de trading", session_name, session_color)

    # ─────────────────────────────────────────
    # VERDICT FINAL
    # ─────────────────────────────────────────
    section("── VERDICT ──")

    status, direction, long_pts, short_pts, completion = board.score()
    total_pts = long_pts + short_pts

    print(f"\n  {DIM}Points LONG   {G}{long_pts:>4} pts")
    print(f"  {DIM}Points SHORT  {R}{short_pts:>4} pts")
    print(f"  {DIM}Completion    {W}{completion*100:.0f}%")

    if board.is_blocked:
        print()
        for name, reason in board.blockers:
            print(f"  {Back.RED}{Style.BRIGHT} BLOQUEUR {RST}  {R}{name}{DIM}: {reason}")
        verdict_box("🚫  BLOQUEUR ACTIF — NE PAS TRADER", Fore.RED, Back.RED)

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
    print(DIM + "  Items manuels non couverts par ce script:")
    print(DIM + "  ├─ Liquidation Heatmap  → coinank.com/chart/derivatives/liq-heat-map/solusdt/1w")
    print(DIM + "  ├─ Calendrier macro     → fr.investing.com/economic-calendar")
    print(DIM + "  ├─ Order Blocks / FVG   → TradingView (manuel)")
    print(DIM + "  └─ SL/TP/taille pos.    → utiliser la calculatrice dans la checklist HTML")
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

    if board.is_blocked:
        lines.append("== BLOQUEURS ==")
        for name, reason in board.blockers:
            # Truncate long reasons cleanly
            r = reason[:120] + "..." if len(reason) > 120 else reason
            lines.append(f"  ! {name}: {r}")
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


def signal_changed(new_status: str, new_direction, old_state: dict) -> bool:
    """Retourne True si le signal a changé depuis la dernière notification."""
    return (old_state.get("status") != new_status or
            old_state.get("direction") != new_direction)


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
                    and signal_changed(effective_status, direction, old_state))
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
                    })
            else:
                if effective_status not in NOTIFY_ON_STATUSES:
                    print(DIM + f"\n  [NOTIFY] Signal '{effective_status}' non actionnable — pas de notif." + RST)
                else:
                    print(DIM + f"\n  [NOTIFY] Signal inchange ({effective_status}/{direction}) — pas de notif." + RST)
                    print(DIM + f"           Utilise --force pour forcer l'envoi." + RST)
