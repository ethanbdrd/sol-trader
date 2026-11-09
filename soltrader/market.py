"""Récupération live de toutes les données nécessaires au moteur."""

from datetime import datetime, timezone

from .config import SYMBOL, BTC_SYMBOL
from .api import (fetch_ohlcv, fetch_current_price, fetch_funding_rate,
                  fetch_contract_stats, fetch_macro_calendar)
from .engine import MarketData, closed_only


def fetch_market_data(symbol=SYMBOL, now=None) -> MarketData:
    now = now or datetime.now(timezone.utc)
    return MarketData(
        now=now,
        price=fetch_current_price(symbol),
        btc_price=fetch_current_price(BTC_SYMBOL),
        # +1 sur chaque limite : la période en cours est retirée par closed_only
        sol_1d=closed_only(fetch_ohlcv(symbol, "1d", limit=251), "1D", now),
        sol_4h=closed_only(fetch_ohlcv(symbol, "4h", limit=201), "4h", now),
        sol_15m=closed_only(fetch_ohlcv(symbol, "15m", limit=101), "15min", now),
        btc_1d=closed_only(fetch_ohlcv(BTC_SYMBOL, "1d", limit=251), "1D", now),
        # 336 h = 14 jours : percentile OI + liquidations 8h + flux taker 6h/40h
        stats_1h=closed_only(fetch_contract_stats(symbol, interval="1h", limit=337), "1h", now),
        funding=fetch_funding_rate(symbol),
        macro=fetch_macro_calendar(),
    )
