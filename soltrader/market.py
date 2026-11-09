"""Récupération live de toutes les données nécessaires au moteur."""

from datetime import datetime, timezone

from .config import SYMBOL, BTC_SYMBOL
from .api import (fetch_ohlcv, fetch_current_price, fetch_funding_rate,
                  fetch_contract_stats, fetch_macro_calendar)
from .engine import MarketData


def fetch_market_data(symbol=SYMBOL, now=None) -> MarketData:
    now = now or datetime.now(timezone.utc)
    return MarketData(
        now=now,
        price=fetch_current_price(symbol),
        btc_price=fetch_current_price(BTC_SYMBOL),
        sol_1d=fetch_ohlcv(symbol, "1d", limit=250),
        sol_4h=fetch_ohlcv(symbol, "4h", limit=200),
        sol_15m=fetch_ohlcv(symbol, "15m", limit=100),
        btc_1d=fetch_ohlcv(BTC_SYMBOL, "1d", limit=250),
        # 336 h = 14 jours : percentile OI + liquidations 8h + flux taker 6h/40h
        stats_1h=fetch_contract_stats(symbol, interval="1h", limit=336),
        funding=fetch_funding_rate(symbol),
        macro=fetch_macro_calendar(),
    )
