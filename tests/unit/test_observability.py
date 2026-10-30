import logging
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from app.adapters.inbound.http import create_app
from app.adapters.observability import Logged, RequestIdFilter, request_id
from app.application.ports.inbound import Reply


class Parser:
    calls = 0

    def parse(self, text, ctx):
        self.calls += 1
        return NS(intent="create_task", title=text)

    def broken(self):
        raise ValueError("boom")


def test_logged_keeps_interface_and_logs_without_values(caplog):
    p = Parser()
    w = Logged(p, "llm", {"parse", "broken"})
    with caplog.at_level(logging.INFO, "app.ports"):
        r = w.parse("купить хлеб", None)
    assert r.title == "купить хлеб" and w.calls == 1  # результат и атрибуты -- от обёрнутого объекта
    [rec] = caplog.records
    assert rec.getMessage().startswith("llm.parse ") and rec.getMessage().endswith("-> create_task")
    assert "хлеб" not in rec.getMessage()


def test_logged_reraises_and_logs_failure(caplog):
    w = Logged(Parser(), "llm", {"broken"})
    with caplog.at_level(logging.INFO, "app.ports"), pytest.raises(ValueError):
        w.broken()
    assert caplog.records[0].levelno == logging.WARNING and "failed" in caplog.records[0].getMessage()


def test_filter_adds_request_id():
    rec = logging.LogRecord("x", logging.INFO, "", 0, "m", (), None)
    token = request_id.set("abc")
    try:
        RequestIdFilter().filter(rec)
    finally:
        request_id.reset(token)
    assert rec.request_id == "abc"


class Svc:
    seen = None

    def handle_turn(self, session_id, user_id, text):
        Svc.seen = request_id.get()  # синхронный endpoint идёт в threadpool: id должен дойти и туда
        return Reply("ok")


def test_middleware_sets_request_id_and_access_log(caplog):
    c = TestClient(create_app(Svc(), None))
    with caplog.at_level(logging.INFO, "app.adapters.inbound.http"):
        r = c.post("/api/message", json={"session_id": "s", "text": "x"}, headers={"X-Request-ID": "req1"})
    assert r.headers["X-Request-ID"] == "req1" and Svc.seen == "req1"
    assert any(m.getMessage().startswith("POST /api/message 200") for m in caplog.records)
    r = c.post("/api/message", json={"session_id": "s", "text": "x"})
    assert len(r.headers["X-Request-ID"]) == 8 and request_id.get() == "-"
