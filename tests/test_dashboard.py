"""Le dashboard se génère avec et sans historique paper."""

import json

from soltrader import dashboard


def test_dashboard_renders_paper_section(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "signal_log.jsonl").write_text(json.dumps(
        {"ts": "2026-11-09 15:17 UTC", "price": 86.5, "status": "go", "direction": "short",
         "long_pts": 1, "short_pts": 7, "completion": 1.0, "blockers": [], "notified": True}) + "\n")
    dashboard.generate()
    page = (tmp_path / "docs" / "index.html").read_text()
    assert "Paper trading" in page and "0/30 trades" in page

    (tmp_path / "paper_trades.jsonl").write_text(json.dumps(
        {"book": "go", "direction": "short", "entry_time": "2026-11-09T15:17:00+00:00",
         "entry": 86.5, "stop": 88.0, "target": 83.5, "risk_pct": 1.7, "status": "closed",
         "exit_time": "2026-11-10T03:00:00+00:00", "exit_price": 83.5, "exit_reason": "target",
         "gross_pct": 3.4, "costs_pct": 0.19, "funding_pct": -0.01, "net_pct": 3.2,
         "r_net": 1.9, "margin_pct": 32.0, "meta": {}}) + "\n")
    dashboard.generate()
    page = (tmp_path / "docs" / "index.html").read_text()
    assert "1/30 trades" in page and "+1.90 R" in page
