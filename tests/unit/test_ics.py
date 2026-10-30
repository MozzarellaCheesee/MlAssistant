from datetime import date, datetime, timezone

import pytest

from app.adapters.outbound.yandex_calendar.ics import build_ics
from app.domain.dates import MSK
from app.domain.item import Item, ItemKind

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=MSK)


def _lines(s):
    return s.split("\r\n")


def test_meeting():
    it = Item(ItemKind.MEETING, "Планерка", starts_at=datetime(2026, 11, 2, 9, 0, tzinfo=MSK))
    s = build_ics(it, "u1", NOW)
    assert s.endswith("\r\n") and "\n" not in s.replace("\r\n", "")
    for x in ("VERSION:2.0", "PRODID:-//ml-assistant//RU", "BEGIN:VTIMEZONE", "TZID:Europe/Moscow", "TZNAME:MSK",
              "TZOFFSETFROM:+0300", "TZOFFSETTO:+0300", "DTSTART:19700101T000000", "UID:u1",
              "DTSTAMP:20261028T090000Z", "DTSTART;TZID=Europe/Moscow:20261102T090000",
              "DTEND;TZID=Europe/Moscow:20261102T100000", "SUMMARY:Планерка"):
        assert x in _lines(s)
    assert "BEGIN:VALARM" not in s and "RRULE" not in s


def test_series_exdates():
    it = Item(ItemKind.MEETING, "Еженедельная", starts_at=datetime(2026, 11, 2, 9, 0, tzinfo=MSK),
              rrule="FREQ=WEEKLY;BYDAY=MO", exdates=[date(2026, 11, 9), date(2026, 11, 16)])
    s = build_ics(it, "u2", NOW)
    assert "RRULE:FREQ=WEEKLY;BYDAY=MO" in _lines(s)
    assert "EXDATE;TZID=Europe/Moscow:20261109T090000,20261116T090000" in _lines(s)


def test_reminder():
    it = Item(ItemKind.REMINDER, "Позвонить", starts_at=datetime(2026, 11, 2, 9, 0, tzinfo=MSK))
    s = _lines(build_ics(it, "u3", NOW))
    assert "DTEND;TZID=Europe/Moscow:20261102T091500" in s
    for x in ("BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:PT0S", "DESCRIPTION:Позвонить", "END:VALARM"):
        assert x in s


def test_escape():
    it = Item(ItemKind.MEETING, "Встреча, план; итоги\nновое", starts_at=datetime(2026, 11, 2, 9, 0, tzinfo=MSK))
    assert "SUMMARY:Встреча\\, план\\; итоги\\nновое" in _lines(build_ics(it, "u4", NOW))


def test_fold():
    title = "ж" * 200
    it = Item(ItemKind.MEETING, title, starts_at=datetime(2026, 11, 2, 9, 0, tzinfo=MSK))
    s = build_ics(it, "u5", NOW)
    assert all(len(l.encode()) <= 75 for l in _lines(s))
    assert "SUMMARY:" + title in s.replace("\r\n ", "")


def test_no_start():
    with pytest.raises(ValueError):
        build_ics(Item(ItemKind.TASK, "x"), "u6", NOW)


def test_utc_start_in_msk():
    it = Item(ItemKind.MEETING, "x", starts_at=datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc))
    assert "DTSTART;TZID=Europe/Moscow:20261102T090000" in _lines(build_ics(it, "u7", NOW))


def test_bare_cr():
    it = Item(ItemKind.MEETING, "a\rb", starts_at=datetime(2026, 11, 2, 9, 0, tzinfo=MSK))
    assert "SUMMARY:a\\nb" in _lines(build_ics(it, "u8", NOW))


def test_utc_dtend_in_msk():
    st = datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc)
    s = _lines(build_ics(Item(ItemKind.MEETING, "x", starts_at=st), "u9", NOW))
    assert "DTEND;TZID=Europe/Moscow:20261102T100000" in s
    s = _lines(build_ics(Item(ItemKind.MEETING, "x", starts_at=st, ends_at=datetime(2026, 11, 2, 7, 30, tzinfo=timezone.utc)), "u10", NOW))
    assert "DTEND;TZID=Europe/Moscow:20261102T103000" in s
