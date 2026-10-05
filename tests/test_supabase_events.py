"""record_api_event must never block the request that triggered it."""
import threading
import time

from app.services import supabase


def test_record_api_event_returns_before_slow_post_finishes(monkeypatch):
    monkeypatch.setattr(supabase.settings, "supabase_url", "https://example.supabase.co")
    monkeypatch.setattr(supabase.settings, "supabase_key", "test-key")
    posted = threading.Event()
    captured = {}

    def slow_post(url, headers, json, timeout):
        time.sleep(0.5)
        captured.update(url=url, json=json)
        posted.set()

        class _Resp:
            def raise_for_status(self):
                return None

        return _Resp()

    monkeypatch.setattr(supabase.httpx, "post", slow_post)

    start = time.perf_counter()
    supabase.record_api_event({"path": "/x", "method": "GET", "status_code": 200, "user_id": None})
    elapsed = time.perf_counter() - start

    assert elapsed < 0.2
    assert posted.wait(timeout=3)
    assert captured["url"] == "https://example.supabase.co/rest/v1/api_usage_events"
    assert captured["json"]["path"] == "/x"


def test_record_api_event_noop_when_not_configured(monkeypatch):
    monkeypatch.setattr(supabase.settings, "supabase_url", "")
    monkeypatch.setattr(supabase.settings, "supabase_key", "")

    def fail_post(*args, **kwargs):
        raise AssertionError("should not post")

    monkeypatch.setattr(supabase.httpx, "post", fail_post)
    supabase.record_api_event({"path": "/x"})


def test_record_api_event_noop_when_app_writes_to_supabase_db_directly(monkeypatch):
    # The usage middleware already inserts the row into Supabase Postgres;
    # forwarding it over REST as well would store every event twice.
    monkeypatch.setattr(supabase.settings, "supabase_url", "https://example.supabase.co")
    monkeypatch.setattr(supabase.settings, "supabase_key", "test-key")
    monkeypatch.setattr(supabase.settings, "supabase_db_url", "postgresql://u:pw@host:6543/postgres")

    def fail_post(*args, **kwargs):
        raise AssertionError("should not post")

    monkeypatch.setattr(supabase.httpx, "post", fail_post)
    supabase.record_api_event({"path": "/x"})
