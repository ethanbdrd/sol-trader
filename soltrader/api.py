"""Appels API — Gate.io futures (public, sans clé) + calendrier Forex Factory.

Pourquoi Gate.io : Binance et Bybit bloquent les IPs des runners GitHub
Actions (451/403). Ne pas migrer sans vérifier ce point.

Pièges Gate.io :
- klines et funding renvoyés newest-first (tri nécessaire)
- tailles de trades en CONTRATS, pas en SOL
"""

import json
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from .config import GATE_BASE, TIMEOUT, MACRO_CACHE
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


FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
MACRO_CACHE_MAX_AGE = timedelta(hours=6)
MACRO_WINDOW = timedelta(hours=4)
ET = ZoneInfo("America/New_York")


def _event_time_utc(ev):
    """Heure UTC d'un event Forex Factory, ou None si illisible."""
    try:
        # Format actuel : ISO 8601 avec offset ("2026-07-12T08:30:00-04:00")
        return datetime.fromisoformat(ev["date"]).astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError):
        pass
    try:
        # Ancien format : "01-06-2026" + "8:30am", heure de New York (EST/EDT)
        dt_et = datetime.strptime(f"{ev['date']} {ev['time']}", "%m-%d-%Y %I:%M%p")
        return dt_et.replace(tzinfo=ET).astimezone(timezone.utc)
    except Exception:
        return None


def _load_macro_cache(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            cache = json.load(f)
        return datetime.fromisoformat(cache["fetched"]), cache["events"]
    except (OSError, ValueError, KeyError, TypeError):
        return None, None


def _covers(events, until):
    """True si le calendrier contient des events jusqu'à `until` au moins."""
    times = [t for t in (_event_time_utc(e) for e in events) if t]
    return bool(times) and max(times) >= until


def upcoming_high_impact(events, now, window=MACRO_WINDOW):
    out = []
    for ev in events:
        if ev.get("impact") != "High":
            continue
        t = _event_time_utc(ev)
        if t and now <= t <= now + window:
            out.append({"title": ev.get("title", "?"), "time_utc": t})
    return out


def fetch_macro_calendar(now=None, cache_path=MACRO_CACHE):
    """
    Events macro HIGH dans les 4h (Forex Factory, JSON public non officiel).
    Le fichier couvre toute la semaine : il est mis en cache (6h) pour ne pas
    déclencher le rate limit (HTTP 429). En cas d'erreur, le cache est utilisé
    s'il couvre encore la fenêtre des 4h. Retourne None si rien d'exploitable.
    """
    now = now or datetime.now(timezone.utc)
    fetched, events = _load_macro_cache(cache_path)
    if events is not None and now - fetched < MACRO_CACHE_MAX_AGE and _covers(events, now):
        return upcoming_high_impact(events, now)
    try:
        r = requests.get(FF_URL, timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        fresh = r.json()
        if not isinstance(fresh, list):
            raise ValueError("format inattendu")
    except Exception as e:
        print(Y + f"  [MACRO] Calendrier indisponible: {e}" + RST)
        if events is not None and _covers(events, now + MACRO_WINDOW):
            print(Y + f"  [MACRO] Cache du {fetched:%Y-%m-%d %H:%M} UTC utilisé" + RST)
            return upcoming_high_impact(events, now)
        return None
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"fetched": now.isoformat(), "events": fresh}, f)
    except OSError:
        pass
    return upcoming_high_impact(fresh, now)
