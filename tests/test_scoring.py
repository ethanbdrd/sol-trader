"""Tests du SignalBoard — points, bloqueurs, gates du verdict."""

from soltrader.config import AUTO_ITEMS
from soltrader.scoring import SignalBoard


def _fill_neutral(board, count):
    """Ajoute `count` items neutres (évalués, sans direction)."""
    for i in range(count):
        board.add(f"neutral_{i}", "neutral", weight=1)


class TestScore:
    def test_empty_board_is_wait(self):
        status, direction, lp, sp, completion = SignalBoard().score()
        assert status == "wait" and direction is None
        assert completion == 0

    def test_go_requires_min_points_and_completion(self):
        board = SignalBoard()
        # 7 pts long (S1 x2 + S2 x2 + 3 x1), 0 short, tout le reste évalué neutre
        board.add("S1", "long", weight=2)
        board.add("S2", "long", weight=2)
        board.add("s3", "long")
        board.add("s4", "long")
        board.add("s5", "long")
        _fill_neutral(board, AUTO_ITEMS - 5)
        status, direction, lp, sp, completion = board.score()
        assert status == "go" and direction == "long"
        assert lp == 7 and sp == 0
        assert completion == 1.0

    def test_no_go_below_min_points(self):
        # Direction claire mais seulement 4 pts (tendance daily seule)
        board = SignalBoard()
        board.add("S1", "long", weight=2)
        board.add("S2", "long", weight=2)
        _fill_neutral(board, AUTO_ITEMS - 2)
        status, direction, *_ = board.score()
        assert status == "possible" and direction == "long"

    def test_two_points_direction_is_mixed(self):
        # Ratio 100% long mais 2 pts → pas exploitable
        board = SignalBoard()
        board.add("a", "long")
        board.add("b", "long")
        _fill_neutral(board, AUTO_ITEMS - 2)
        status, direction, *_ = board.score()
        assert status == "mixed" and direction is None

    def test_conflicting_signals_are_mixed(self):
        board = SignalBoard()
        for i in range(4):
            board.add(f"l{i}", "long")
        for i in range(4):
            board.add(f"s{i}", "short")
        _fill_neutral(board, AUTO_ITEMS - 8)
        status, direction, *_ = board.score()
        assert status == "mixed" and direction is None

    def test_incomplete_when_data_missing(self):
        # 5 items évalués sur 20 (25%) → incomplete même avec direction
        board = SignalBoard()
        for i in range(5):
            board.add(f"l{i}", "long")
        # les items manquants ne sont pas ajoutés (API down)
        status, *_ = board.score()
        assert status == "incomplete"

    def test_short_direction_symmetry(self):
        board = SignalBoard()
        for i in range(6):
            board.add(f"s{i}", "short")
        board.add("l0", "long")
        _fill_neutral(board, AUTO_ITEMS - 7)
        status, direction, lp, sp, _ = board.score()
        assert direction == "short" and status == "go"
        assert sp == 6 and lp == 1


class TestBlockers:
    def test_global_blocker(self):
        board = SignalBoard()
        board.block("session", "asiatique")
        assert board.is_blocked
        assert board.is_blocked_for("long") and board.is_blocked_for("short")
        assert board.global_blockers() == [("session", "asiatique")]
        assert board.directional_blockers() == []

    def test_directional_blocker(self):
        board = SignalBoard()
        board.block("cvd_divergence", "prix monte sans acheteurs", direction="long")
        assert not board.is_blocked
        assert board.is_blocked_for("long")
        assert not board.is_blocked_for("short")
        assert len(board.directional_blockers()) == 1

    def test_effective_status(self):
        board = SignalBoard()
        assert board.effective_status("possible", 1.0) == "possible"
        board.block("sl_trop_serre_long", "", direction="long")
        assert board.effective_status("possible", 1.0) == "directional_blocked"
        # completion insuffisante → pas de directional_blocked
        assert board.effective_status("incomplete", 0.3) == "incomplete"
        board.block("session", "")
        assert board.effective_status("possible", 1.0) == "blocked"
