from datetime import date, datetime

import pytest

from app.application.render import format_list, format_when, plural
from app.domain.dates import MSK
from app.domain.item import Item, ItemKind

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=MSK)


@pytest.mark.parametrize("x,exp", [
    (datetime(2026, 10, 28, 15, 0, tzinfo=MSK), "сегодня в 15:00"),
    (datetime(2026, 10, 29, 10, 0, tzinfo=MSK), "завтра в 10:00"),
    (date(2026, 10, 30), "послезавтра"),
    (datetime(2026, 11, 2, 9, 0, tzinfo=MSK), "в понедельник, 2 ноября в 9:00"),
    (datetime(2026, 3, 5, 10, 0, tzinfo=MSK), "5 марта в 10:00"),
    (date(2027, 3, 20), "20 марта 2027"),
])
def test_format_when(x, exp):
    assert format_when(x, NOW) == exp
    assert format_when(Item(ItemKind.TASK, "t", due_date=x) if type(x) is date else Item(ItemKind.MEETING, "t", starts_at=x), NOW) == exp


def test_plural():
    assert [plural(n, "запись", "записи", "записей") for n in (1, 3, 5, 11, 21)] == [
        "запись", "записи", "записей", "записей", "запись"]


def test_format_list_truncates():
    s = format_list([Item(ItemKind.TASK, f"t{i}") for i in range(9)], NOW)
    assert s.startswith("9 записей:") and s.endswith("и ещё 2") and "без срока — t0" in s


def test_render_series():
    s = Item(ItemKind.MEETING, "t", starts_at=datetime(2026, 11, 2, 9, 0, tzinfo=MSK), rrule="FREQ=WEEKLY;BYDAY=MO")
    assert format_when(s, NOW) == "каждый понедельник в 9:00, с 2 ноября"
    s = Item(ItemKind.TASK, "t", due_date=date(2026, 10, 29), rrule="FREQ=DAILY")
    assert format_when(s, NOW) == "каждый день"
