"""Archive data/*.csv : fusion sans doublon, périodes closes uniquement, funding."""

import pandas as pd

import soltrader.archive as ar
from tests.fixtures import NOW, ohlcv, stats


def test_merge_dedupes_and_prefers_new():
    old = ohlcv(5, "15min", NOW)
    new = old.tail(2).copy()
    new["close"] = 999.0
    out = ar.merge_series(old, new)
    assert len(out) == 5 and (out["close"].tail(2) == 999.0).all()


def test_update_appends_only_closed_periods(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "_candles_page",
                        lambda sym, itv, start=None, end=None, limit=200:
                        ohlcv(20, {"15m": "15min", "4h": "4h", "1d": "1D"}[itv], NOW,
                              include_forming=True)[ar.OHLCV_COLS])
    monkeypatch.setattr(ar, "_stats_page",
                        lambda start=None, end=None, limit=48: stats(n=10, include_forming=True))
    ar.update(str(tmp_path), now=NOW)
    ar.update(str(tmp_path), now=NOW)                  # 2e passage : pas de doublon
    df = ar.read_series("sol_15m", str(tmp_path))
    assert len(df) == 19
    assert (df.index + pd.Timedelta("15min") <= NOW).all()
    assert len(ar.read_series("stats_1h", str(tmp_path))) == 9


def test_roundtrip_keeps_utc_index(tmp_path):
    df = ohlcv(3, "4h", NOW)[ar.OHLCV_COLS]
    ar._write("sol_4h", df, str(tmp_path))
    back = ar.read_series("sol_4h", str(tmp_path))
    assert back.index.equals(df.index)


def test_funding_from_stats_takes_settlement_hours():
    idx = pd.date_range("2026-11-09 05:00", periods=6, freq="1h", tz="UTC")
    st = pd.DataFrame({"last_funding_rate": [1, 2, 3, 4, 5, 6]}, index=idx)
    out = ar.funding_from_stats(st)
    # période [07:00, 08:00[ → versement de 08:00
    assert out == [{"fundingRate": "3", "fundingTime": int(pd.Timestamp("2026-11-09 08:00", tz="UTC").timestamp())}]
