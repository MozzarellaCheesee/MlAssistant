import pytest
from datetime import date, datetime

from app.application.intents import CreateMeeting, ListItems, SearchItems
from app.domain.dates import MSK
from tests.scenarios.fakes import FakeParser, build_service, dt

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=MSK)


def run(mapping, text, **kw):
    svc, repo = build_service(FakeParser(mapping), NOW, csv=True, **kw)
    return svc, svc.handle_turn("s", "u", text)


def test_list_same_action_for_phrasings():
    m = {"Что у меня сегодня?": ListItems(date_from=date(2026, 10, 28)),
         "Какие планы на сегодня?": ListItems(date_from=date(2026, 10, 28)),
         "Покажи мои встречи на сегодня": ListItems(date_from=date(2026, 10, 28), kind="meeting"),
         "создай": CreateMeeting(title="Синк", date=date(2026, 10, 28), time="15:00")}
    svc, _ = run(m, "создай")
    for t in list(m)[:3]:
        assert "сегодня в 15:00" in svc.handle_turn("s", "u", t).reply


def test_list_week_from_csv():
    _, r = run({"x": ListItems(date_from=date(2026, 10, 28), date_to=date(2026, 11, 1))}, "x")
    assert r.reply.startswith("6 записей:")


def test_list_empty():
    _, r = run({"x": ListItems(date_from=date(2026, 10, 29), kind="reminder")}, "x")
    assert r.reply == "На завтра ничего не запланировано."


def test_search_presentation():
    _, r = run({"x": SearchItems(query="по презентации", kind="task")}, "x")
    assert r.reply.startswith("Нашёл 5 записей:")


def test_when_was_meeting_with_ivan():
    _, r = run({"x": SearchItems(query="Встреча с Иваном по презентации", kind="meeting", tense="past")}, "x",
               floor=0.9)
    assert r.reply.startswith("Последний раз: 5 марта в ")
    assert "Встреча с Иваном по презентации" in r.reply


def test_search_contract():
    svc, r = run({"x": SearchItems(query="по договору")}, "x")
    assert r.reply
    assert svc.dialog.load("u:s").last_referenced_ids


def test_search_nothing():
    _, r = run({"x": SearchItems(query="абракадабра")}, "x")
    assert r.reply == "Ничего не нашёл. Попробуйте сказать иначе."


def test_list_empty_texts():
    _, r = run({"x": ListItems(date_from=date(2026, 10, 28))}, "x")
    assert r.reply == "На сегодня ничего не запланировано."
