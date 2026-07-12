"""Fonctions de calcul des signaux — pures (données → verdict), testables."""

import numpy as np
import pandas as pd
from datetime import datetime, timezone

from .config import SWING_N
from .display import G, R, Y


# ─────────────────────────────────────────────
# PRIMITIVES
# ─────────────────────────────────────────────

def is_decreasing(arr, count=3):
    """True si les `count` DERNIÈRES valeurs sont strictement décroissantes."""
    tail = arr[-count:] if len(arr) >= count else arr
    return len(tail) >= 2 and all(tail[i] < tail[i-1] for i in range(1, len(tail)))


def is_increasing(arr, count=3):
    """True si les `count` DERNIÈRES valeurs sont strictement croissantes."""
    tail = arr[-count:] if len(arr) >= count else arr
    return len(tail) >= 2 and all(tail[i] > tail[i-1] for i in range(1, len(tail)))


def calc_mas(df):
    df = df.copy()
    df["ma5"]   = df["close"].rolling(5).mean()
    df["ma15"]  = df["close"].rolling(15).mean()
    df["ma30"]  = df["close"].rolling(30).mean()
    df["ma200"] = df["close"].rolling(200).mean()
    df["ema9"]  = df["close"].ewm(span=9, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
    return df


# ─────────────────────────────────────────────
# STRUCTURE (pivots, BOS/CHoCH)
# ─────────────────────────────────────────────

def detect_swings(df, n=SWING_N):
    """
    Detect swing highs and lows.
    Returns two boolean arrays: is_swing_high, is_swing_low
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
    Returns: ('bullish'|'bearish'|'ranging'|'unclear', swing_highs, swing_lows)
    Logic:
    - bullish : HH + HL (les deux), OU HH seuls si >= 3 pivots hauts croissants
    - bearish : LH + LL, OU LH seuls si >= 3 pivots hauts décroissants
    - ranging sinon
    """
    sh, sl = detect_swings(df, n)
    swing_highs = df["high"][sh].values[-6:]
    swing_lows  = df["low"][sl].values[-6:]

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "unclear", swing_highs, swing_lows

    lh = is_decreasing(swing_highs)
    ll = is_decreasing(swing_lows)
    hh = is_increasing(swing_highs)
    hl = is_increasing(swing_lows)

    if hh and hl:
        return "bullish", swing_highs, swing_lows
    elif lh and ll:
        return "bearish", swing_highs, swing_lows
    elif lh and len(swing_highs) >= 3:
        # LH seuls avec 3+ pivots = bearish confirmé même si les bas sont ambigus
        return "bearish", swing_highs, swing_lows
    elif hh and len(swing_highs) >= 3:
        # HH seuls avec 3+ pivots = bullish probable
        return "bullish", swing_highs, swing_lows
    else:
        return "ranging", swing_highs, swing_lows


def detect_bos_choch(df_4h, struct_4h, n):
    """
    BOS / CHoCH sur les pivots 4H.
    Returns: (bos_signal, bos_label)
    bos_signal: 'bullish_bos' | 'bearish_bos' | 'choch_warning' | None
    """
    sh4, sl4 = detect_swings(df_4h, n=n)
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
    return bos_signal, bos_label


# ─────────────────────────────────────────────
# PATTERNS OHLCV
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
    Returns (absorbed, vol_ratio, body_pct).
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
    for _, candle in recent.iterrows():
        # Distribute the candle's volume across the bins it spans
        lo_c, hi_c, vol = candle["low"], candle["high"], candle["volume"]
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
# DÉRIVÉS (funding, OI, ratio L/S, flux taker)
# ─────────────────────────────────────────────

def assess_taker_flow(flow_df, min_buy_share_dev=0.02, min_price_move_pct=0.3):
    """
    Compare le flux taker cumulé et le mouvement de prix sur la MÊME fenêtre.
    min_buy_share_dev : écart minimal de la part acheteuse vs 50% pour
    considérer le flux directionnel (0.02 → buy >= 52% ou <= 48%).
    min_price_move_pct : en-dessous, le prix est considéré plat.
    Returns: (status, direction, cvd, buy_share, price_chg_pct)
    status: aligned_bullish / aligned_bearish / divergence_bearish /
            divergence_bullish / flow_neutral / price_flat / unclear
    """
    if flow_df is None or len(flow_df) < 2:
        return "unclear", None, 0.0, 0.5, 0.0
    buy   = flow_df["long_taker_size"].sum()
    sell  = flow_df["short_taker_size"].sum()
    total = buy + sell
    if not total or pd.isna(total):
        return "unclear", None, 0.0, 0.5, 0.0
    cvd = buy - sell
    buy_share = buy / total
    p0, p1 = flow_df["mark_price"].iloc[0], flow_df["mark_price"].iloc[-1]
    if not p0 or pd.isna(p0) or pd.isna(p1):
        return "unclear", None, cvd, buy_share, 0.0
    price_chg = (p1 - p0) / p0 * 100

    flow_dir = ("bullish" if buy_share >= 0.5 + min_buy_share_dev
                else "bearish" if buy_share <= 0.5 - min_buy_share_dev
                else None)
    price_dir = ("up" if price_chg >= min_price_move_pct
                 else "down" if price_chg <= -min_price_move_pct
                 else None)

    if flow_dir is None:
        return "flow_neutral", None, cvd, buy_share, price_chg
    if price_dir is None:
        # Prix plat + flux directionnel = accumulation/distribution → biais léger
        return "price_flat", ("long" if flow_dir == "bullish" else "short"), cvd, buy_share, price_chg
    if (price_dir == "up") == (flow_dir == "bullish"):
        status = "aligned_bullish" if flow_dir == "bullish" else "aligned_bearish"
        return status, ("long" if flow_dir == "bullish" else "short"), cvd, buy_share, price_chg
    status = "divergence_bearish" if price_dir == "up" else "divergence_bullish"
    return status, None, cvd, buy_share, price_chg


def check_session(now=None):
    """Returns (session_id, label, color) based on UTC time."""
    now_utc = now or datetime.now(timezone.utc)
    h = now_utc.hour + now_utc.minute / 60.0
    # Paris = UTC+1 (CET) or UTC+2 (CEST)
    # Sessions in UTC:
    # London:  07:00 – 11:00 UTC  (09h-13h Paris)
    # Inter:   11:00 – 13:00 UTC  (creux entre Londres et US — pas de bloqueur)
    # US:      13:00 – 22:00 UTC  (15h-minuit Paris) — étendu à 22h pour couvrir
    #          le cron de 21h UTC (délai GitHub Actions inclus)
    # Asian:   22:00 – 07:00 UTC (bloqueur fakeouts)
    if 7.0 <= h < 11.0:
        return "london", "🇬🇧 Londres (09h-13h Paris)", Y
    elif 11.0 <= h < 13.0:
        return "inter", "⏸ Entre Londres et US — liquidité réduite", Y
    elif 13.0 <= h < 22.0:
        return "us", "🇺🇸 US (15h-00h Paris)", G
    else:
        return "asian", "🌙 Asiatique — FAKEOUTS", R


def assess_funding(rates):
    """
    Returns (value, signal, direction_hint)
    signal: 'favorable_long', 'caution_long', 'danger_long', 'danger_short', 'neutral'
    """
    if not rates:
        return None, "unknown", None
    current = float(rates[-1]["fundingRate"])   # fraction brute (0.0001 = 0.01%)
    pct = current * 100                          # seuils exprimés en % par période 8h

    if pct < -0.05:
        return current, "danger_short", "long"
    elif pct < -0.01:
        return current, "favorable_long", "long"
    elif pct <= 0.03:
        return current, "neutral", None
    elif pct <= 0.05:
        return current, "caution_long", "short"
    else:
        return current, "danger_long", "short"


def assess_oi(oi_df):
    """
    Compare OI trend vs price trend.
    Returns: (oi_change_pct, oi_current, oi_pct_of_max, oi_percentile)
    Percentile 50 (neutre) si données insuffisantes — évite un faux bloqueur.
    """
    if oi_df is None or len(oi_df) < 6:
        return 0.0, 0.0, 0.0, 50.0

    recent_oi    = oi_df["sumOpenInterestValue"].iloc[-6:]
    base_oi      = recent_oi.iloc[0]
    oi_change    = (recent_oi.iloc[-1] - base_oi) / base_oi * 100 if base_oi else 0.0
    oi_current   = oi_df["sumOpenInterestValue"].iloc[-1]
    oi_max       = oi_df["sumOpenInterestValue"].max()
    # Percentile sur 14j — plus stable qu'un % du max ponctuel
    oi_pct_of_max = oi_current / oi_max * 100 if oi_max else 0.0
    # Percentile rank : quelle fraction des valeurs 14j est sous la valeur actuelle
    oi_percentile = (oi_df["sumOpenInterestValue"] <= oi_current).mean() * 100

    return oi_change, oi_current, oi_pct_of_max, oi_percentile


def assess_ls_ratio(ls_df):
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
