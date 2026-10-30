import pytest
from pydantic import ValidationError

from app.application.intents import (CompleteItem, Confirm, CreateMeeting, CreateTask, DeleteItem,
                                     ListItems, SearchItems, Target, Unknown, UpdateItem, parse_intent)

T = {"ref": "last"}


@pytest.mark.parametrize("data,cls", [
    ({"intent": "create_task"}, CreateTask),
    ({"intent": "create_meeting"}, CreateMeeting),
    ({"intent": "list_items"}, ListItems),
    ({"intent": "search_items", "query": "x"}, SearchItems),
    ({"intent": "complete_item", "target": T}, CompleteItem),
    ({"intent": "update_item", "target": T}, UpdateItem),
    ({"intent": "delete_item", "target": T}, DeleteItem),
    ({"intent": "confirm", "answer": "yes"}, Confirm),
    ({"intent": "unknown"}, Unknown),
])
def test_parse_each_intent(data, cls):
    assert isinstance(parse_intent(data), cls)


def test_blank_strings_become_none():
    i = parse_intent({"intent": "create_task", "title": "  ", "date": ""})
    assert i.title is None and i.date is None


def test_target_requires_exactly_one():
    with pytest.raises(ValidationError):
        Target()
    with pytest.raises(ValidationError):
        Target(ref="last", query="x")


def test_unknown_intent_name_rejected():
    with pytest.raises(ValidationError):
        parse_intent({"intent": "fly"})


def test_search_requires_query():
    with pytest.raises(ValidationError):
        parse_intent({"intent": "search_items"})


def test_iso_fields():
    i = parse_intent({"intent": "create_task", "date": "2026-10-30", "time": "24:30"})
    assert i.date.day == 30 and i.time == "24:30"
    assert parse_intent({"intent": "list_items"}).date_from is None
    assert parse_intent({"intent": "list_items", "date_from": "2026-10-29", "date_to": "2026-11-01"}).date_to.month == 11


def test_rrule_validation():
    assert parse_intent({"intent": "create_task", "rrule": "FREQ=WEEKLY;BYDAY=MO"}).rrule == "FREQ=WEEKLY;BYDAY=MO"
    assert parse_intent({"intent": "create_task", "rrule": "BYDAY=MO;FREQ=WEEKLY"}).rrule == "FREQ=WEEKLY;BYDAY=MO"
    assert parse_intent({"intent": "create_task", "rrule": " "}).rrule is None
    for bad in ("FREQ=HOURLY", "FREQ=MONTHLY;BYMONTHDAY=40"):
        with pytest.raises(ValidationError):
            parse_intent({"intent": "create_meeting", "rrule": bad})


def test_scope():
    assert parse_intent({"intent": "delete_item", "target": T, "scope": "all"}).scope == "all"
    with pytest.raises(ValidationError):
        parse_intent({"intent": "delete_item", "target": T, "scope": "sometimes"})
