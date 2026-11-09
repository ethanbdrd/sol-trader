"""Paper trading : ouverture sur verdict, suivi, clôture, carnets séparés."""

import pandas as pd

from soltrader import paper
from soltrader.engine import Evaluation
from soltrader.scoring import SignalBoard
from tests.fixtures import market, NOW


def _ev(sl_long=97.0, sl_short=103.0):
    return Evaluation(board=SignalBoard(), sl_long=sl_long, sl_short=sl_short)


def _candles(rows, start):
    idx = pd.date_range(start, periods=len(rows), freq="15min", tz="UTC")
    return pd.DataFrame({"open": [r[2] for r in rows], "high": [r[0] for r in rows],
                         "low": [r[1] for r in rows], "close": [r[2] for r in rows]}, index=idx)


def _paths(tmp_path):
    return {"state_path": str(tmp_path / "s.json"), "log_path": str(tmp_path / "t.jsonl")}


def test_go_opens_only_go_book(tmp_path):
    md = market(price=100.0)
    ev = paper.update(md, _ev(), "go", "long", None, [], **_paths(tmp_path))
    assert [(k, b) for k, b, _ in ev] == [("open", "go")]
    state = paper.load_state(_paths(tmp_path)["state_path"])
    assert state["go"].direction == "long" and state["possible"] is None


def test_non_actionable_status_opens_nothing(tmp_path):
    md = market(price=100.0)
    for status in ("mixed", "blocked", "directional_blocked"):
        assert paper.update(md, _ev(), status, "long", None, [], **_paths(tmp_path)) == []


def test_no_second_position_while_open(tmp_path):
    md = market(price=100.0)
    paper.update(md, _ev(), "go", "long", None, [], **_paths(tmp_path))
    ev = paper.update(md, _ev(), "go", "short", None, [], **_paths(tmp_path))
    assert ev == []


def test_close_is_logged_and_frees_book(tmp_path):
    p = _paths(tmp_path)
    md = market(price=100.0)
    paper.update(md, _ev(), "go", "long", None, [], **p)
    path = _candles([(100.5, 99.5, 100), (110, 100, 108)], NOW)
    ev = paper.update(md, _ev(), "mixed", None, path, [], **p)
    assert [(k, b) for k, b, _ in ev] == [("close", "go")]
    assert ev[0][2].exit_reason == "target"
    assert paper.load_state(p["state_path"])["go"] is None
    logged = paper.read_trades(p["log_path"])
    assert len(logged) == 1 and logged[0]["book"] == "go"
    assert paper.stats_by_book(p["log_path"])["go"]["n"] == 1


def test_missing_stop_is_skipped_not_opened(tmp_path):
    md = market(price=100.0)
    ev = paper.update(md, _ev(sl_long=None), "possible", "long", None, [], **_paths(tmp_path))
    assert ev[0][0] == "skip"
    assert paper.load_state(_paths(tmp_path)["state_path"])["possible"] is None


def test_messages_are_plain_text():
    md = market(price=100.0)
    from soltrader.execution import plan_trade, simulate
    tr, _ = plan_trade("short", 100.0, NOW, 103.0)
    assert "[PAPER GO] Ouverture ▼ SHORT" in paper.format_event("open", "go", tr)
    tr = simulate(tr, _candles([(100, 90, 91)], NOW))
    msg = paper.format_event("close", "possible", tr)
    assert "TARGET" in msg and "R" in msg
    assert paper.format_event("skip", "go", "raison") is None
