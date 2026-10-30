from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.domain.item import Item, ItemKind

MSK = ZoneInfo("Europe/Moscow")


def test_meeting_default_end_is_one_hour():
    s = datetime(2026, 10, 29, 15, 0, tzinfo=MSK)
    assert Item(kind=ItemKind.MEETING, title="x", starts_at=s).effective_end() == s + timedelta(hours=1)


def test_reminder_end_is_start():
    s = datetime(2026, 10, 29, 10, 0, tzinfo=MSK)
    assert Item(kind=ItemKind.REMINDER, title="x", starts_at=s).effective_end() == s


def test_day_prefers_due_date_then_start():
    assert Item(kind=ItemKind.TASK, title="x", due_date=date(2026, 10, 30)).day() == date(2026, 10, 30)
    assert Item(kind=ItemKind.TASK, title="x", starts_at=datetime(2026, 10, 29, 1, 0, tzinfo=MSK)).day() == date(2026, 10, 29)
