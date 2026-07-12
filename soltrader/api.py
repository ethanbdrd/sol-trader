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


def fetch_taker_flow(symbol, interval="1h", limit=6):
    """
    Volumes taker par période depuis contract_stats.
    long_taker_size / short_taker_size = volume taker acheteur / vendeur
    (contrats) PAR période — vérifié : lsr_taker == long/short exactement,
    valeurs non monotones donc bien par période, pas cumulées.
    mark_price permet de comparer flux et prix sur la MÊME fenêtre.
    """
    contract = _gate_contract(symbol)
    data = get(f"{GATE_BASE}/futures/usdt/contract_stats",
               {"contract": contract, "interval": interval, "limit": limit})
    if not data:
        return None
    df = pd.DataFrame(data)
    for col in ("long_taker_size", "short_taker_size", "mark_price"):
        if col not in df.columns:
            return None
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.sort_values("timestamp")
    df["delta"] = df["long_taker_size"] - df["short_taker_size"]
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


def fetch_contract_stats(symbol, interval="1h", limit=48):
    """
    Gate.io contract_stats brut (OI, ratios, taker flow, liquidations par période).
    Colonnes numériques normalisées, trié par timestamp ascendant.
    """
    contract = _gate_contract(symbol)
    data = get(f"{GATE_BASE}/futures/usdt/contract_stats",
               {"contract": contract, "interval": interval, "limit": limit})
    if not data:
        return None
    df = pd.DataFrame(data)
    df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.sort_values("timestamp")
    return df


def fetch_open_interest_history(symbol, period="1h", limit=48):
    """
    Gate.io open interest via contract_stats.
    Returns OI in number of contracts — open_interest_usd for USD value.
    """
    df = fetch_contract_stats(symbol, interval=period, limit=limit)
    if df is None:
        return None
    df["sumOpenInterest"]      = pd.to_numeric(df.get("open_interest", 0), errors="coerce")
    df["sumOpenInterestValue"] = pd.to_numeric(df.get("open_interest_usd", 0), errors="coerce")
    return df


def fetch_long_short_ratio(symbol, period="1h", limit=24):
    """
    Gate.io long/short account ratio from contract_stats.
    lsr_account = long accounts / short accounts ratio (> 1 = more longs)
    """
    df = fetch_contract_stats(symbol, interval=period, limit=limit)
    if df is None or "lsr_account" not in df.columns:
        return None
    # lsr_account: ratio long/short accounts (e.g. 1.5 = 60% long, 40% short)
    df["lsr"] = pd.to_numeric(df["lsr_account"], errors="coerce")
    # Convert ratio to longAccount fraction: ratio/(1+ratio)
    df["longAccount"]  = df["lsr"] / (1 + df["lsr"])
    df["shortAccount"] = 1 - df["longAccount"]
    df["longShortRatio"] = df["lsr"]
    return df


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
    Falls back to empty list on error rather than None (avoids ERREUR API display).
    """
    contract = _gate_contract(symbol)
    try:
        data = get(f"{GATE_BASE}/futures/usdt/liq_orders",
                   {"contract": contract, "limit": limit})
        if not data:
            return []
        # Gate.io sometimes wraps in {"data": [...]} — handle both
        if isinstance(data, dict):
            return data.get("data", data.get("result", []))
        return data
    except Exception:
        return []


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
