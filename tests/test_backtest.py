"""Backtest de bout en bout sur une archive synthétique (sans réseau)."""

import pandas as pd

import backtest
from soltrader import archive
from tests.fixtures import ohlcv, stats

END = pd.Timestamp("2026-11-09 12:00", tz="UTC")


def _archive(tmp_path):
    d = str(tmp_path)
    cols = archive.OHLCV_COLS
    archive._write("sol_1d", ohlcv(260, "1D", END, seed=1)[cols], d)
    archive._write("btc_1d", ohlcv(260, "1D", END, start_price=50000, seed=2)[cols], d)
    archive._write("sol_4h", ohlcv(260, "4h", END, seed=3)[cols], d)
    archive._write("sol_15m", ohlcv(4 * 24 * 4, "15min", END, seed=4)[cols], d)
    archive._write("btc_15m", ohlcv(4 * 24 * 4, "15min", END, start_price=50000, seed=5)[cols], d)
    st = stats(n=24 * 20, end=END)
    st["open_interest"] = st["open_interest_usd"]
    st["last_funding_rate"] = 0.0001
    archive._write("stats_1h", st[[c for c in archive.STATS_COLS if c in st.columns]], d)
    return d


def test_no_lookahead_in_market_at(tmp_path):
    data = backtest.load_data(_archive(tmp_path))
    t = END - pd.Timedelta(hours=10) + pd.Timedelta("15min")
    md = backtest.market_at(t, data)
    assert md is not None
    for df, itv in ((md.sol_1d, "1D"), (md.sol_4h, "4h"),
                    (md.sol_15m, "15min"), (md.stats_1h, "1h")):
        assert (df.index + pd.Timedelta(itv) <= t).all()
    assert all(r["fundingTime"] <= t.timestamp() for r in md.funding)


def test_run_end_to_end(tmp_path):
    data = backtest.load_data(_archive(tmp_path))
    start = END - pd.Timedelta(days=2) + pd.Timedelta("15min")
    verdicts, go, possible, refusals = backtest.run(data, start, END, step_h=1)
    assert len(verdicts) >= 40
    for tr in go + possible:
        assert tr.status == "closed" and tr.net_pct is not None
