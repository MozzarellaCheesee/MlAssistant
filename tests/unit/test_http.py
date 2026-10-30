from datetime import date

from fastapi.testclient import TestClient

from app.adapters.inbound.http import create_app
from app.application.errors import AssistantError, ErrorCode
from app.application.ports.inbound import Reply
from app.domain.dates import DateRange


class FakeService:
    def __init__(self, error=None):
        self.error, self.range = error, None

    def handle_turn(self, session_id, user_id, text):
        if self.error:
            raise self.error
        return Reply("ok")

    def handle_audio(self, session_id, user_id, audio):
        return Reply("ok", transcript="привет")

    def history(self, user_id, after_id, limit):
        return [{"id": 1, "after": after_id, "limit": limit, "user": user_id}]

    def list_items(self, user_id, rng, kind, status):
        self.range = rng
        return []

    def due_reminders(self, user_id):
        return []


def test_reminders_due_empty_for_owner_while_voice_runs():
    from types import SimpleNamespace as NS

    class Svc(FakeService):
        def due_reminders(self, user_id):
            return [NS(title="x")]

    v = NS(owner="default", status=lambda: {"enabled": True}, wake_word="Джарвис", phrase_pause=1, session_silence=1,
           volume=1, beep_volume=1)
    assert TestClient(create_app(Svc(), None, voice=v)).post("/api/reminders/due").json() == []


def client(svc=None):
    return TestClient(create_app(svc or FakeService(), None))


def test_message_ok():
    r = client().post("/api/message", json={"session_id": "s", "text": "x"})
    assert r.status_code == 200
    assert r.json() == {"reply": "ok", "clarify": False, "items": [], "error": None}


def test_message_error_maps():
    e = AssistantError(ErrorCode.LLM_UNAVAILABLE)
    r = client(FakeService(e)).post("/api/message", json={"session_id": "s", "text": "x"})
    assert r.status_code == 503
    assert r.json() == {"reply": e.user_message, "error": "llm_unavailable"}


def test_unexpected_500():
    r = TestClient(create_app(FakeService(ValueError("boom")), None), raise_server_exceptions=False) \
        .post("/api/message", json={"session_id": "s", "text": "x"})
    assert r.status_code == 500 and r.json()["error"] == "internal"


def test_audio_ok():
    r = client().post("/api/audio", data={"session_id": "s"}, files={"file": ("a.webm", b"123")})
    assert r.status_code == 200 and r.json()["transcript"] == "привет"


def test_audio_too_big():
    r = client().post("/api/audio", data={"session_id": "s"}, files={"file": ("a.webm", b"0" * (2 * 1024 * 1024 + 1))})
    assert r.status_code == 413 and r.json()["error"] == "audio_too_long"


def test_items_query():
    svc = FakeService()
    r = client(svc).get("/api/items?from=2026-10-28&to=2026-10-29")
    assert r.status_code == 200 and r.json() == []
    assert svc.range == DateRange(date(2026, 10, 28), date(2026, 10, 29))


def test_reminders_due():
    r = client().post("/api/reminders/due")
    assert r.status_code == 200 and r.json() == []


def test_index_served():
    r = client().get("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")


def test_validation_error_friendly():
    r = client().post("/api/message", json={"session_id": "s", "text": ""})
    assert r.status_code == 422
    assert r.json()["error"] == "bad_request"


def test_items_range_too_big():
    for q in ("from=0001-01-01&to=9999-12-31", "from=2026-10-29&to=2026-10-28"):
        r = client().get("/api/items?" + q)
        assert r.status_code == 422 and r.json()["error"] == "bad_request"


# form endpoints through the real service (same handlers as the chat)
def form_client(user="u"):
    from tests.scenarios.fakes import FakeParser, build_service
    svc, repo = build_service(FakeParser({}))
    return TestClient(create_app(svc, None)), svc


def test_form_create_edit_delete_and_isolation():
    c, svc = form_client()
    r = c.post("/api/items", json={"kind": "meeting", "title": "планёрка", "date": "2026-10-29", "time": "10:00", "duration_minutes": 30})
    assert r.status_code == 201 and r.json()["kind"] == "meeting"
    i = r.json()["id"]
    assert c.post("/api/items", json={"kind": "meeting", "title": "ещё", "date": "2026-10-29", "time": "10:15"}).status_code == 201  # conflict does not block
    r = c.patch(f"/api/items/{i}", json={"time": "11:00", "title": "синк"})
    assert r.status_code == 200 and r.json()["title"] == "синк" and "11:00" in r.json()["starts_at"] and r.json()["ends_at"].endswith("11:30:00+03:00")
    r = c.patch(f"/api/items/{i}", json={"kind": "task", "time": None})  # kind change in place
    assert r.json()["kind"] == "task" and r.json()["starts_at"] is None and r.json()["id"] == i
    assert c.delete(f"/api/items/{r.json()['id']}").status_code == 204
    assert c.delete(f"/api/items/{r.json()['id']}").status_code == 404
    assert c.patch("/api/items/999", json={"title": "x"}).status_code == 404


def test_form_bad_time_and_other_user():
    c, svc = form_client()
    assert c.post("/api/items", json={"kind": "task", "title": "x", "date": "2026-10-29", "time": "25:70"}).status_code == 422
    assert c.post("/api/items", json={"kind": "meeting", "title": "x", "date": "2026-10-29"}).status_code == 422  # meeting needs a time
    i = c.post("/api/items", json={"kind": "task", "title": "x", "date": "2026-10-29"}).json()["id"]
    assert svc.repo.get("other", i) is None
    from app.application.errors import FormError
    import pytest
    with pytest.raises(FormError):
        svc.delete_item("other", i)
