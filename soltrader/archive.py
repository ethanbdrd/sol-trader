"""Archive locale des données de marché (data/*.csv), versionnée dans le repo.

Gate.io ne garde que 10 000 bougies par intervalle (≈104 jours en 15 min)
et 180 jours de contract_stats. Pour backtester sur des mois, on archive
nous-mêmes : chaque run du workflow ajoute les périodes clôturées récentes.

Usage :
    python -m soltrader.archive            # mise à jour incrémentale
    python -m soltrader.archive --backfill # remonte aussi loin que l'API le permet
"""

import argparse
import os
import time
from datetime import datetime, timezone

import pandas as pd

from .api import GATE_BASE, _gate_contract, get, normalize_contract_stats
from .config import SYMBOL, BTC_SYMBOL
from .engine import closed_only

DATA_DIR = "data"

# nom de fichier → (symbole, intervalle Gate, fréquence pandas)
CANDLES = {
    "sol_15m": (SYMBOL, "15m", "15min"),
    "sol_4h":  (SYMBOL, "4h", "4h"),
    "sol_1d":  (SYMBOL, "1d", "1D"),
    "btc_15m": (BTC_SYMBOL, "15m", "15min"),
    "btc_1d":  (BTC_SYMBOL, "1d", "1D"),
}
OHLCV_COLS = ["open", "high", "low", "close", "volume"]
STATS_COLS = ["long_taker_size", "short_taker_size", "mark_price", "open_interest",
              "open_interest_usd", "lsr_account", "long_liq_size", "short_liq_size",
              "last_funding_rate"]

MAX_POINTS = 10_000          # limite Gate.io candlesticks
STATS_MAX_DAYS = 179         # limite Gate.io contract_stats (180 j)
PAGE = 2000


def _path(name, data_dir):
    return os.path.join(data_dir, f"{name}.csv")


def read_series(name, data_dir=DATA_DIR):
    """Charge une série archivée, indexée en UTC. None si absente."""
    p = _path(name, data_dir)
    if not os.path.exists(p):
        return None
    df = pd.read_csv(p, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True)
    df.index.name = "open_time" if name in CANDLES else "timestamp"
    return df


def merge_series(old, new):
    """Fusionne et dédoublonne (y compris à l'intérieur d'un même lot : les
    pages de backfill Gate.io se chevauchent). La donnée la plus récente gagne."""
    frames = [f for f in (old, new) if f is not None and not f.empty]
    if not frames:
        return old if old is not None else new
    out = pd.concat(frames)
    return out[~out.index.duplicated(keep="last")].sort_index()


def _write(name, df, data_dir):
    os.makedirs(data_dir, exist_ok=True)
    df.to_csv(_path(name, data_dir), float_format="%.10g",
              date_format="%Y-%m-%dT%H:%M:%SZ")


def _candles_page(symbol, interval, start=None, end=None, limit=PAGE):
    params = {"contract": _gate_contract(symbol), "interval": interval}
    if start is None:
        params["limit"] = limit
    else:
        params.update({"from": int(start), "to": int(end)})
    data = get(f"{GATE_BASE}/futures/usdt/candlesticks", params)
    if not data:
        return None
    df = pd.DataFrame(data).rename(columns={"t": "open_time", "o": "open", "h": "high",
                                            "l": "low", "c": "close", "v": "volume"})
    for c in OHLCV_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["open_time"] = pd.to_datetime(df["open_time"], unit="s", utc=True)
    return df.set_index("open_time")[OHLCV_COLS].sort_index()


def _stats_page(start=None, end=None, limit=PAGE):
    params = {"contract": _gate_contract(SYMBOL), "interval": "1h"}
    if start is None:
        params["limit"] = limit
    else:
        params.update({"from": int(start), "to": int(end), "limit": limit})
    data = get(f"{GATE_BASE}/futures/usdt/contract_stats", params)
    if not data:
        return None
    df = normalize_contract_stats(pd.DataFrame(data))
    return df[[c for c in STATS_COLS if c in df.columns]]


def update(data_dir=DATA_DIR, backfill=False, now=None):
    """Met à jour toutes les séries. Retourne {nom: nb de lignes}."""
    now = now or datetime.now(timezone.utc)
    now_ts = now.timestamp()
    counts = {}

    for name, (symbol, interval, freq) in CANDLES.items():
        step = pd.Timedelta(freq).total_seconds()
        frames = []
        if backfill:
            start = now_ts - (MAX_POINTS - 10) * step
            while start < now_ts:
                end = min(start + (PAGE - 1) * step, now_ts)
                page = _candles_page(symbol, interval, start, end)
                if page is not None:
                    frames.append(page)
                start = end + step
                time.sleep(0.2)
        else:
            page = _candles_page(symbol, interval, limit=200)
            if page is not None:
                frames.append(page)
        new = closed_only(pd.concat(frames), freq, now) if frames else None
        merged = merge_series(read_series(name, data_dir), new)
        if merged is not None:
            _write(name, merged, data_dir)
            counts[name] = len(merged)

    frames = []
    if backfill:
        start = now_ts - STATS_MAX_DAYS * 86400
        while start < now_ts:
            end = min(start + (PAGE - 1) * 3600, now_ts)
            page = _stats_page(start, end)
            if page is not None:
                frames.append(page)
            start = end + 3600
            time.sleep(0.2)
    else:
        page = _stats_page(limit=48)
        if page is not None:
            frames.append(page)
    new = closed_only(pd.concat(frames), "1h", now) if frames else None
    merged = merge_series(read_series("stats_1h", data_dir), new)
    if merged is not None:
        _write("stats_1h", merged, data_dir)
        counts["stats_1h"] = len(merged)
    return counts


def funding_from_stats(stats):
    """Funding versé à chaque échéance 8h (00/08/16 UTC), reconstitué depuis
    contract_stats : taux versé à T ≈ last_funding_rate de la période [T-1h, T[
    (écart observé < 0,0002 %). Format identique à api.fetch_funding_rate."""
    if stats is None or "last_funding_rate" not in stats.columns:
        return []
    out = []
    for ts, rate in stats["last_funding_rate"].dropna().items():
        settle = ts + pd.Timedelta("1h")
        if settle.hour % 8 == 0:
            out.append({"fundingRate": str(rate), "fundingTime": int(settle.timestamp())})
    return out


def main():
    parser = argparse.ArgumentParser(description="Archive des données Gate.io")
    parser.add_argument("--backfill", action="store_true",
                        help="Remonte aussi loin que l'API le permet")
    parser.add_argument("--data-dir", default=DATA_DIR)
    args = parser.parse_args()
    counts = update(args.data_dir, backfill=args.backfill)
    for name, n in counts.items():
        print(f"  [ARCHIVE] {name:<9} {n:>6} lignes")


if __name__ == "__main__":
    main()
