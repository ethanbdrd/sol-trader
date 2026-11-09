"""Données de marché synthétiques pour tester le moteur sans réseau."""

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from soltrader.engine import MarketData

NOW = datetime(2026, 11, 9, 15, 17, tzinfo=timezone.utc)   # session US


def ohlcv(n, freq, end, start_price=100.0, drift=0.0, seed=0, include_forming=False):
    """OHLCV indexé par open_time. Les bougies se terminent avant `end`,
    sauf la dernière si include_forming=True (bougie en cours, comme Gate.io)."""
    rng = np.random.default_rng(seed)
    step = pd.Timedelta(freq)
    last_open = pd.Timestamp(end).floor(freq) - (pd.Timedelta(0) if include_forming else step)
    idx = pd.date_range(end=last_open, periods=n, freq=freq, tz="UTC")
    close = start_price * np.cumprod(1 + drift + rng.normal(0, 0.004, n))
    open_ = np.concatenate([[start_price], close[:-1]])
    high = np.maximum(open_, close) * (1 + rng.uniform(0.001, 0.006, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0.001, 0.006, n))
    vol = rng.uniform(800, 1200, n)
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                       "volume": vol, "quote_vol": vol * close}, index=idx)
    df.index.name = "open_time"
    return df


def stats(n=336, end=NOW, buy=1000.0, sell=1000.0, liq_long=0.0, liq_short=0.0,
          lsr=1.0, price=100.0, include_forming=False):
    last = pd.Timestamp(end).floor("1h") - (pd.Timedelta(0) if include_forming else pd.Timedelta("1h"))
    idx = pd.date_range(end=last, periods=n, freq="1h", tz="UTC")
    oi = np.linspace(1e8, 1.1e8, n)
    df = pd.DataFrame({
        "long_taker_size": buy, "short_taker_size": sell, "mark_price": price,
        "open_interest_usd": oi, "sumOpenInterestValue": oi, "lsr_account": lsr,
        "long_liq_size": liq_long, "short_liq_size": liq_short,
    }, index=idx)
    df.index.name = "timestamp"
    return df


def market(**over):
    """MarketData complet et neutre ; surcharger n'importe quel champ."""
    base = dict(
        now=NOW, price=100.0, btc_price=50000.0,
        sol_1d=ohlcv(250, "1D", NOW, seed=1),
        sol_4h=ohlcv(200, "4h", NOW, seed=2),
        sol_15m=ohlcv(100, "15min", NOW, seed=3),
        btc_1d=ohlcv(250, "1D", NOW, start_price=50000.0, seed=4),
        stats_1h=stats(),
        funding=[{"fundingRate": "0.0001", "fundingTime": int(NOW.timestamp()) - 3600}],
        macro=[], replay=False,
    )
    base.update(over)
    md = MarketData(**base)
    if "price" not in over and md.sol_15m is not None:
        md.price = float(md.sol_15m["close"].iloc[-1])
    return md
