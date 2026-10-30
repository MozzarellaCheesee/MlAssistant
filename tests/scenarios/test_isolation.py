import pytest
from app.application.intents import CreateMeeting, CreateTask, ListItems, SearchItems
from datetime import date

from tests.scenarios.fakes import FakeParser, build_service, dt

Q = "На какое время поставить встречу?"


def test_dialog_isolated_by_user():
    m = {"встреча": CreateMeeting(title="Синк", date=date(2026, 10, 29)), "в 15": CreateMeeting(date=date(2026, 10, 29), time="15:00")}
    parser = FakeParser(m)
    svc, repo = build_service(parser)
    assert svc.handle_turn("s", "a", "встреча").reply == Q
    svc.handle_turn("s", "b", "в 15")
    assert parser.last_ctx.pending is None
    assert repo.list_between("a", *_wide(), None, None) == []
    svc.handle_turn("s", "a", "в 15")
    assert [i.title for i in repo.list_between("a", *_wide(), None, None)] == ["Синк"]
    assert repo.list_between("b", *_wide(), None, None) == []


def _wide():
    from datetime import date
    from app.domain.dates import DateRange
    return (DateRange(date(2026, 1, 1), date(2026, 12, 31)),)


def test_items_isolated():
    m = {"add": CreateTask(title="секретный отчёт", date=date(2026, 10, 29)),
         "list": ListItems(date_from=date(2026, 10, 29)), "find": SearchItems(query="отчёт")}
    svc, _ = build_service(FakeParser(m))
    svc.handle_turn("s", "a", "add")
    assert "секретный" in svc.handle_turn("s", "a", "list").reply
    assert "секретный" not in svc.handle_turn("s", "b", "list").reply
    assert "секретный" not in svc.handle_turn("s", "b", "find").reply
