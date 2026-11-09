"""Simulateur d'exécution : SL/TP, priorité au stop, sortie temps, coûts."""

import pandas as pd
import pytest

from soltrader.config import FEE_TAKER, SLIPPAGE, SL_BUFFER, MAX_HOLD_H
from soltrader.execution import plan_trade, simulate, summarize

T0 = pd.Timestamp("2026-11-09 14:00", tz="UTC")
COSTS = 2 * (FEE_TAKER + SLIPPAGE) * 100


def candles(rows, start=T0):
    """rows = [(high, low, close), ...] en bougies 15 min à partir de start."""
    idx = pd.date_range(start, periods=len(rows), freq="15min", tz="UTC")
    return pd.DataFrame({"open": [r[2] for r in rows], "high": [r[0] for r in rows],
                         "low": [r[1] for r in rows], "close": [r[2] for r in rows]}, index=idx)


def long_trade():
    tr, why = plan_trade("long", 100.0, T0, sl_level=98.0)
    assert why is None
    return tr


class TestPlan:
    def test_long_levels(self):
        tr = long_trade()
        assert tr.stop == pytest.approx(98.0 * (1 - SL_BUFFER))
        assert tr.target == pytest.approx(100 + 2 * (100 - tr.stop))

    def test_short_levels(self):
        tr, _ = plan_trade("short", 100.0, T0, sl_level=103.0)
        assert tr.stop == pytest.approx(103.0 * (1 + SL_BUFFER))
        assert tr.target < 100

    def test_refuses_tight_wide_or_missing_stop(self):
        assert plan_trade("long", 100.0, T0, 99.6)[0] is None       # < 1 %
        assert plan_trade("long", 100.0, T0, 90.0)[0] is None       # > 6 %
        assert plan_trade("long", 100.0, T0, None)[0] is None
        assert plan_trade("long", 100.0, T0, 101.0)[0] is None      # mauvais côté


class TestSimulate:
    def test_target_hit(self):
        tr = simulate(long_trade(), candles([(101, 99.5, 100.5), (106, 100, 105)]))
        assert tr.exit_reason == "target"
        assert tr.gross_pct == pytest.approx((tr.target - 100) / 100 * 100)
        assert tr.net_pct == pytest.approx(tr.gross_pct - COSTS)
        assert tr.r_net == pytest.approx(tr.net_pct / tr.risk_pct)

    def test_stop_wins_when_both_touched(self):
        tr = simulate(long_trade(), candles([(110, 90, 100)]))
        assert tr.exit_reason == "stop" and tr.r_net < -1   # -1R moins les coûts

    def test_still_open_without_touch(self):
        tr = simulate(long_trade(), candles([(100.5, 99.5, 100)] * 4))
        assert tr.status == "open"

    def test_time_exit(self):
        n = MAX_HOLD_H * 4
        tr = simulate(long_trade(), candles([(100.5, 99.5, 100.2)] * (n + 5)))
        assert tr.exit_reason == "time" and tr.exit_price == 100.2
        assert pd.Timestamp(tr.exit_time) == T0 + pd.Timedelta(hours=MAX_HOLD_H)

    def test_ignores_candles_before_entry(self):
        before = candles([(100, 50, 100)], start=T0 - pd.Timedelta("15min"))
        tr = simulate(long_trade(), before)
        assert tr.status == "open"

    def test_funding_cost_sign(self):
        settle = int((T0 + pd.Timedelta(hours=2)).timestamp())
        funding = [{"fundingRate": "0.0001", "fundingTime": settle}]
        path = candles([(100.5, 99.5, 100)] * 12 + [(106, 100, 105)])
        lg = simulate(long_trade(), path, funding)
        sh, _ = plan_trade("short", 100.0, T0, sl_level=102.0)
        sh = simulate(sh, candles([(100.5, 99.5, 100)] * 12 + [(100, 95, 96)]), funding)
        assert lg.funding_pct == pytest.approx(0.01)       # long paie
        assert sh.funding_pct == pytest.approx(-0.01)      # short reçoit


def test_summarize():
    a = simulate(long_trade(), candles([(106, 100, 105)]))
    b = simulate(long_trade(), candles([(100, 97, 98)]))
    s = summarize([a, b])
    assert s["n"] == 2 and s["win_rate"] == 0.5
    assert s["exits"] == {"target": 1, "stop": 1}
    assert s["max_drawdown_r"] > 0
