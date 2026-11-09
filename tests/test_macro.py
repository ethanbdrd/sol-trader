"""Calendrier macro : cache anti-429 et conversion horaire."""

import json
from datetime import datetime, timedelta, timezone

import requests

import soltrader.api as api

NOW = datetime(2026, 11, 9, 13, 0, tzinfo=timezone.utc)


def _ev(dt, impact="High", title="CPI"):
    return {"title": title, "impact": impact, "date": dt.isoformat()}


class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error")

    def json(self):
        return self._data


def _write_cache(path, fetched, events):
    path.write_text(json.dumps({"fetched": fetched.isoformat(), "events": events}))


def test_fresh_fetch_is_cached_and_filtered(tmp_path, monkeypatch):
    cache = tmp_path / "c.json"
    events = [_ev(NOW + timedelta(hours=2)), _ev(NOW + timedelta(hours=2), impact="Low"),
              _ev(NOW + timedelta(hours=10)), _ev(NOW + timedelta(days=4), title="end")]
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: _Resp(events))
    out = api.fetch_macro_calendar(now=NOW, cache_path=str(cache))
    assert [e["title"] for e in out] == ["CPI"]
    assert cache.exists()


def test_recent_cache_avoids_network(tmp_path, monkeypatch):
    cache = tmp_path / "c.json"
    _write_cache(cache, NOW - timedelta(hours=1), [_ev(NOW + timedelta(days=3))])
    def boom(*a, **k):
        raise AssertionError("pas d'appel réseau attendu")
    monkeypatch.setattr(api.requests, "get", boom)
    assert api.fetch_macro_calendar(now=NOW, cache_path=str(cache)) == []


def test_429_falls_back_to_cache_covering_window(tmp_path, monkeypatch):
    cache = tmp_path / "c.json"
    _write_cache(cache, NOW - timedelta(hours=20),
                 [_ev(NOW + timedelta(hours=1)), _ev(NOW + timedelta(days=3))])
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: _Resp(None, 429))
    out = api.fetch_macro_calendar(now=NOW, cache_path=str(cache))
    assert len(out) == 1


def test_429_with_stale_week_returns_none(tmp_path, monkeypatch):
    cache = tmp_path / "c.json"
    _write_cache(cache, NOW - timedelta(days=3), [_ev(NOW - timedelta(days=1))])
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: _Resp(None, 429))
    assert api.fetch_macro_calendar(now=NOW, cache_path=str(cache)) is None


def test_legacy_format_uses_new_york_dst():
    # 8:30am New York le 9 juillet (EDT, UTC-4) = 12:30 UTC
    t = api._event_time_utc({"date": "07-09-2026", "time": "8:30am"})
    assert t == datetime(2026, 7, 9, 12, 30, tzinfo=timezone.utc)
    # 8:30am le 9 janvier (EST, UTC-5) = 13:30 UTC
    t = api._event_time_utc({"date": "01-09-2026", "time": "8:30am"})
    assert t == datetime(2026, 1, 9, 13, 30, tzinfo=timezone.utc)
