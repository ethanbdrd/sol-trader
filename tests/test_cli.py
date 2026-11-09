"""Dédoublonnage des crons redondants (--min-interval)."""

import json
from datetime import datetime, timezone

import sol_analyzer

NOW = datetime(2026, 11, 9, 15, 47, tzinfo=timezone.utc)


def _log(tmp_path, monkeypatch, ts):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "signal_log.jsonl").write_text(json.dumps({"ts": ts}) + "\n")


def test_recent_analysis_skips(tmp_path, monkeypatch):
    _log(tmp_path, monkeypatch, "2026-11-09 15:20 UTC")
    assert sol_analyzer.recent_analysis(40, now=NOW)


def test_old_analysis_runs(tmp_path, monkeypatch):
    _log(tmp_path, monkeypatch, "2026-11-09 14:20 UTC")
    assert not sol_analyzer.recent_analysis(40, now=NOW)


def test_empty_log_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert not sol_analyzer.recent_analysis(40, now=NOW)
