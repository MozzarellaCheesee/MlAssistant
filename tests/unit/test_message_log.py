from fastapi.testclient import TestClient

from app.adapters.inbound.http import create_app
from app.adapters.outbound.sqlite.db import Database
from app.adapters.outbound.sqlite.message_log import SqliteMessageLog
from app.application.intents import CreateTask
from app.domain.item import Item, ItemKind
from tests.scenarios.fakes import FakeParser, build_service


def test_since_after_and_isolation():
    log = SqliteMessageLog(Database(":memory:"))
    a = log.add("u", "user", "привет", "voice")
    b = log.add("u", "assistant", "ок", "voice", [Item(ItemKind.TASK, "t", id=1)])
    log.add("v", "user", "чужое", "text")
    assert [m["id"] for m in log.since("u", 0)] == [a, b]
    [m] = log.since("u", a)
    assert m["role"] == "assistant" and m["items"][0]["title"] == "t" and m["source"] == "voice"
    assert [m["id"] for m in log.since("u", 0, limit=1)] == [b]


def test_turn_logs_two_records_in_order():
    t = "добавь задачу"
    svc, _ = build_service(FakeParser({t: CreateTask(kind="task", title="x")}))
    r = svc.handle_turn("s", "u", t)
    h = svc.history("u", 0, 100)
    assert [m["role"] for m in h] == ["user", "assistant"] and [m["id"] for m in h] == r.message_ids
    assert h[0]["text"] == t and h[1]["items"][0]["title"] == "x"


def test_history_endpoint_requires_user():
    svc, _ = build_service(FakeParser({}))

    class Auth:
        def user_for(self, sid):
            return None
    assert TestClient(create_app(svc, Auth())).get("/api/history").status_code == 401
    assert TestClient(create_app(svc, None)).get("/api/history?after=0").json() == []


def test_error_turn_logs_both_and_returns_ids():
    from app.application.errors import AssistantError, ErrorCode

    class Boom:
        def parse(self, *a):
            raise AssistantError(ErrorCode.LLM_UNAVAILABLE)
    svc, _ = build_service(Boom())
    try:
        svc.handle_turn("s", "u", "привет")
    except AssistantError as e:
        h = svc.history("u", 0, 100)
        assert [m["role"] for m in h] == ["user", "assistant"] and h[1]["text"] == e.user_message
        assert e.message_ids == [m["id"] for m in h]
    else:
        raise AssertionError


def test_stt_empty_logs_assistant_record():
    from app.application.errors import AssistantError, ErrorCode

    class Stt:
        def transcribe(self, audio):
            return " "
    svc, _ = build_service(FakeParser({}))
    svc.stt = Stt()
    try:
        svc.handle_audio("s", "u", b"x")
    except AssistantError as e:
        [m] = svc.history("u", 0, 100)
        assert m["role"] == "assistant" and m["source"] == "voice" and m["text"] == e.user_message
        assert e.message_ids == [m["id"]]
    else:
        raise AssertionError


def test_http_error_has_message_ids():
    from app.application.errors import AssistantError, ErrorCode
    from tests.unit.test_http import FakeService
    e = AssistantError(ErrorCode.LLM_UNAVAILABLE)
    e.message_ids = [1, 2]
    r = TestClient(create_app(FakeService(e), None)).post("/api/message", json={"session_id": "s", "text": "x"})
    assert r.json()["message_ids"] == [1, 2]
