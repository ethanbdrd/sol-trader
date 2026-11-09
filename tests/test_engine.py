"""Tests du moteur unique (engine.evaluate) sur données synthétiques."""

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
