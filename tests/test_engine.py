"""Tests du moteur unique (engine.evaluate) sur données synthétiques."""

import pandas as pd

from soltrader.config import AUTO_ITEMS
from soltrader.engine import evaluate

from tests.fixtures import market, stats


def _signals(ev):
    return {n: d for n, d, _, _ in ev.board.signals}


class TestEngineSmoke:
    def test_full_data_answers_every_auto_item(self):
        ev = evaluate(market())
        sig = _signals(ev)
        assert len(sig) == AUTO_ITEMS
        assert all(d is not None for d in sig.values())

    def test_rows_cover_all_sections(self):
        ev = evaluate(market())
        assert len({r.section for r in ev.rows}) == 6

    def test_replay_skips_macro(self):
        ev = evaluate(market(replay=True, macro=None))
        assert "M1_macro" not in _signals(ev)

    def test_macro_event_blocks_everything(self):
        ev = evaluate(market(macro=[{"title": "CPI", "time_utc": None}]))
        assert ev.board.is_blocked


class TestEngineDerivatives:
    def test_long_liquidation_spike_blocks_short(self):
        ev = evaluate(market(stats_1h=stats(liq_long=200.0)))   # 8h × 200 = 1600 contrats
        assert ("recent_liquidation", "short") in [(n, d) for n, _, d in ev.board.blockers]

    def test_crowded_long_retail_gives_short(self):
        ev = evaluate(market(stats_1h=stats(lsr=3.0)))           # 75 % long
        assert _signals(ev)["F3_ls_ratio"] == "short"

    def test_missing_stats_leaves_items_unanswered(self):
        ev = evaluate(market(stats_1h=None))
        sig = _signals(ev)
        for name in ("F2_oi", "F4_liquidations", "F3_ls_ratio", "C1_cvd"):
            assert name not in sig


class TestClosedCandles:
    """A1 — la bougie / période en cours renvoyée par Gate.io est ignorée."""

    def test_closed_only_drops_forming_candle(self):
        from soltrader.engine import closed_only
        from tests.fixtures import NOW, ohlcv
        df = ohlcv(10, "15min", NOW, include_forming=True)
        assert df.index[-1] + pd.Timedelta("15min") > NOW       # bougie en cours
        out = closed_only(df, "15min", NOW)
        assert len(out) == 9
        assert out.index[-1] + pd.Timedelta("15min") <= NOW

    def test_closed_only_keeps_exactly_finished_candle(self):
        from soltrader.engine import closed_only
        from tests.fixtures import ohlcv
        end = pd.Timestamp("2026-11-09 15:30", tz="UTC")
        df = ohlcv(4, "15min", end)                 # dernière bougie 15:15-15:30
        assert len(closed_only(df, "15min", end)) == 4

    def test_live_fetch_removes_forming_periods(self, monkeypatch):
        import soltrader.market as m
        from tests.fixtures import NOW, ohlcv, stats
        freq = {"1d": "1D", "4h": "4h", "15m": "15min"}
        monkeypatch.setattr(m, "fetch_ohlcv", lambda sym, itv, limit=500:
                            ohlcv(limit, freq[itv], NOW, include_forming=True))
        monkeypatch.setattr(m, "fetch_contract_stats", lambda sym, interval="1h", limit=48:
                            stats(n=limit, include_forming=True))
        monkeypatch.setattr(m, "fetch_current_price", lambda sym: 100.0)
        monkeypatch.setattr(m, "fetch_funding_rate", lambda sym: [])
        monkeypatch.setattr(m, "fetch_macro_calendar", lambda: [])
        md = m.fetch_market_data(now=NOW)
        for df, itv in ((md.sol_1d, "1D"), (md.sol_4h, "4h"),
                        (md.sol_15m, "15min"), (md.stats_1h, "1h")):
            assert (df.index + pd.Timedelta(itv) <= NOW).all()
        assert len(md.sol_15m) == 100 and len(md.stats_1h) == 336


class TestFailSafe:
    """A3 — une panne d'API n'est jamais interprétée comme un feu vert."""

    def test_macro_unavailable_blocks_live(self):
        ev = evaluate(market(macro=None, replay=False))
        assert "macro_inconnu" in [n for n, _ in ev.board.global_blockers()]
        assert "M1_macro" not in _signals(ev)

    def test_macro_unavailable_in_replay_does_not_block(self):
        ev = evaluate(market(macro=None, replay=True))
        assert "macro_inconnu" not in [n for n, _, _ in ev.board.blockers]

    def test_missing_15m_leaves_absorption_unanswered(self):
        ev = evaluate(market(sol_15m=None, price=100.0))
        assert _signals(ev)["C3_absorption"] is None

    def test_nan_liquidations_are_unanswered_not_safe(self):
        st = stats()
        st[["long_liq_size", "short_liq_size"]] = float("nan")
        ev = evaluate(market(stats_1h=st))
        assert "F4_liquidations" not in _signals(ev)

    def test_api_outage_cannot_produce_go(self):
        ev = evaluate(market(stats_1h=None, funding=None, macro=None, sol_15m=None, price=100.0))
        status, _dir, *_, completion = ev.board.score()
        assert ev.board.effective_status(status, completion, _dir) != "go"


class TestStopLevel:
    def test_stop_uses_pivot_nearest_to_price(self, monkeypatch):
        import numpy as np
        import soltrader.engine as eng
        # creux 4H : récent 90 (loin), ancien 97 (proche) ; prix 100
        monkeypatch.setattr(eng, "assess_structure",
                            lambda df, n=2: ("ranging", np.array([105.0, 103.0]), np.array([97.0, 90.0])))
        ev = evaluate(market(price=100.0))
        assert ev.sl_long == 97.0 and ev.sl_short == 103.0
