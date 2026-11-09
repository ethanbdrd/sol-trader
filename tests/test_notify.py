"""Tests du state anti-spam et du message Telegram."""

from soltrader.notify import signal_changed, build_telegram_message
from soltrader.scoring import SignalBoard


class TestSignalChanged:
    def test_empty_state_means_changed(self):
        assert signal_changed("possible", "long", {})

    def test_same_signal_not_changed(self):
        old = {"status": "possible", "direction": "long", "blockers": []}
        board = SignalBoard()
        assert not signal_changed("possible", "long", old, board)

    def test_status_change(self):
        old = {"status": "possible", "direction": "long"}
        assert signal_changed("blocked", "long", old)

    def test_direction_change(self):
        old = {"status": "possible", "direction": "long"}
        assert signal_changed("possible", "short", old)

    def test_blocker_fingerprint_change(self):
        old = {"status": "blocked", "direction": None, "blockers": ["oi_extreme"]}
        board = SignalBoard()
        board.block("cvd_divergence", "", direction="long")
        assert signal_changed("blocked", None, old, board)

    def test_same_blockers_not_changed(self):
        old = {"status": "blocked", "direction": None, "blockers": ["session"]}
        board = SignalBoard()
        board.block("session", "asiatique")
        assert not signal_changed("blocked", None, old, board)


class TestTelegramMessage:
    def _board(self):
        board = SignalBoard()
        board.add("S1_daily_structure", "long", weight=2)
        board.add("F2_oi", "neutral", weight=1)
        return board

    def test_go_header(self):
        msg = build_telegram_message("go", "long", self._board(), 80.0,
                                     7, 1, "2026-07-12 15:00 UTC", "SOLUSDT")
        assert "[SIGNAL]" in msg
        assert "LONG" in msg
        assert "80.0000" in msg

    def test_directional_blocked_header(self):
        board = self._board()
        board.block("sl_trop_serre_long", "SL 0.5%", direction="long")
        msg = build_telegram_message("directional_blocked", "long", board, 80.0,
                                     7, 1, "2026-07-12 15:00 UTC", "SOLUSDT")
        assert "[BLOQUEUR LONG]" in msg
        assert "sl_trop_serre_long" in msg

    def test_neutral_markers_not_listed_as_signals(self):
        msg = build_telegram_message("possible", "long", self._board(), 80.0,
                                     2, 0, "2026-07-12 15:00 UTC", "SOLUSDT")
        assert "F2_oi" not in msg
        assert "S1_daily_structure" in msg


class TestRedact:
    def test_token_is_masked(self, monkeypatch):
        import soltrader.notify as n
        monkeypatch.setattr(n, "TELEGRAM_TOKEN", "123456:SECRET")
        msg = "Max retries exceeded with url: /bot123456:SECRET/sendMessage"
        assert "SECRET" not in n.redact(msg) and "/bot***/" in n.redact(msg)

    def test_network_error_does_not_print_token(self, monkeypatch, capsys):
        import requests
        import soltrader.notify as n
        monkeypatch.setattr(n, "TELEGRAM_TOKEN", "123456:SECRET")
        monkeypatch.setattr(n, "TELEGRAM_CHAT_ID", "42")
        def boom(url, **kw):
            raise requests.exceptions.ConnectionError(f"failed for {url}")
        monkeypatch.setattr(n.requests, "post", boom)
        assert n.send_telegram("x") is False
        assert "SECRET" not in capsys.readouterr().out
