"""Appels API — Gate.io futures (public, sans clé) + calendrier Forex Factory.

Pourquoi Gate.io : Binance et Bybit bloquent les IPs des runners GitHub
Actions (451/403). Ne pas migrer sans vérifier ce point.

Pièges Gate.io :
- klines et funding renvoyés newest-first (tri nécessaire)
- tailles de trades en CONTRATS, pas en SOL
"""

import requests
import pandas as pd
from datetime import datetime, timezone, timedelta

from .config import GATE_BASE, TIMEOUT
from .display import R, Y, RST


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
    if symbol.endswith("USDT"):
        return symbol[:-4] + "_USDT"
    return symbol


def fetch_ohlcv(symbol, interval, limit=500):
    """
    Gate.io futures candlesticks.
    Intervals: 10s,1m,5m,15m,30m,1h,4h,8h,1d,7d
    """
    contract = _gate_contract(symbol)
    data = get(f"{GATE_BASE}/futures/usdt/candlesticks",
               {"contract": contract, "interval": interval, "limit": limit})
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
    return df


def fetch_funding_rate(symbol, limit=10):
    """
    Gate.io funding rate history.
    GET /futures/usdt/funding_rate?contract=SOL_USDT&limit=10
    """
    contract = _gate_contract(symbol)
    data = get(f"{GATE_BASE}/futures/usdt/funding_rate",
               {"contract": contract, "limit": limit})
    if not data:
        return None
    # Gate.io renvoie newest-first — on trie en ascendant pour que [-1] = le plus récent
    data = sorted(data, key=lambda r: int(r["t"]))
    # Normalize: [{fundingRate, fundingTime}]
    return [{"fundingRate": str(r["r"]), "fundingTime": r["t"]} for r in data]


STATS_NUMERIC = ("long_taker_size", "short_taker_size", "mark_price",
                 "open_interest", "open_interest_usd", "lsr_account",
                 "long_liq_size", "short_liq_size", "last_funding_rate")


def normalize_contract_stats(df):
    """Colonnes numériques + index timestamp trié. sumOpenInterestValue = OI en USD."""
    df = df.copy()
    for col in STATS_NUMERIC:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True)
    if "open_interest_usd" in df.columns:
        df["sumOpenInterestValue"] = df["open_interest_usd"]
    return df.sort_values("timestamp").set_index("timestamp")


def fetch_contract_stats(symbol, interval="1h", limit=48):
    """
    Gate.io contract_stats (OI, ratios, taker flow, liquidations par période).
    La ligne horodatée T couvre la période [T, T+interval[.
    Retourne un DataFrame normalisé indexé par timestamp, ou None.
    """
    contract = _gate_contract(symbol)
    data = get(f"{GATE_BASE}/futures/usdt/contract_stats",
               {"contract": contract, "interval": interval, "limit": limit})
    if not data:
        return None
    return normalize_contract_stats(pd.DataFrame(data))


def fetch_current_price(symbol):
    contract = _gate_contract(symbol)
    data = get(f"{GATE_BASE}/futures/usdt/tickers",
               {"contract": contract})
    if data and len(data) > 0:
        return float(data[0]["last"])
    return None


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
            # Format actuel FF : date ISO 8601 avec offset ("2026-07-12T08:30:00-04:00")
            dt_utc = datetime.fromisoformat(ev["date"]).astimezone(timezone.utc)
        except (KeyError, TypeError, ValueError):
            try:
                # Ancien format : "01-06-2026" + "8:30am" en ET (approx UTC-5)
                dt_str = f"{ev['date']} {ev['time']}"
                dt_et  = datetime.strptime(dt_str, "%m-%d-%Y %I:%M%p")
                dt_utc = dt_et.replace(tzinfo=timezone.utc) + timedelta(hours=5)
            except Exception:
                continue
        if now_utc <= dt_utc <= window:
            upcoming.append({"title": ev.get("title", "?"), "time_utc": dt_utc})
    return upcoming
