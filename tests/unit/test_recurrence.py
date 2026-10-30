from datetime import date, timedelta

import pytest

from app.domain.recurrence import describe, first_on_or_after, occurrences, validate_rrule

def test_validate_rrule():
    assert validate_rrule("FREQ=WEEKLY;BYDAY=MO") == "FREQ=WEEKLY;BYDAY=MO"
    assert validate_rrule("BYDAY=MO;FREQ=WEEKLY") == "FREQ=WEEKLY;BYDAY=MO"
    assert validate_rrule("BYMONTHDAY=15;INTERVAL=2;FREQ=MONTHLY") == "FREQ=MONTHLY;INTERVAL=2;BYMONTHDAY=15"
    assert validate_rrule(" ") is None
    for bad in ("FREQ=HOURLY", "FREQ=MONTHLY;BYMONTHDAY=40", "FREQ=DAILY;INTERVAL=0", "FREQ=DAILY;BYDAY=MO",
                "FREQ=WEEKLY;COUNT=3", "каждый день", "FREQ=WEEKLY;BYDAY=XX"):
        with pytest.raises(ValueError):
            validate_rrule(bad)


def test_describe_roundtrip():
    got = [describe(x) for x in ("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "FREQ=WEEKLY;BYDAY=MO", "FREQ=WEEKLY;INTERVAL=2")]
    assert got == ["по будням", "каждый понедельник", "каждые 2 недели"]
    assert describe("FREQ=WEEKLY;BYDAY=MO,WE") == "по понедельникам и средам"
    assert describe("FREQ=MONTHLY;BYMONTHDAY=15") == "каждое 15 число"
    assert [describe(f"FREQ={f}") for f in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")] == [
        "каждый день", "каждую неделю", "каждый месяц", "каждый год"]


def test_first_on_or_after():
    wed = date(2026, 10, 28)
    assert first_on_or_after("FREQ=WEEKLY;BYDAY=MO", wed) == date(2026, 11, 2)
    assert first_on_or_after("FREQ=WEEKLY;BYDAY=WE", wed) == wed
    assert first_on_or_after("FREQ=MONTHLY;BYMONTHDAY=15", wed) == date(2026, 11, 15)
    assert first_on_or_after("FREQ=DAILY", wed) == wed


def test_occurrences_weekly():
    r = occurrences("FREQ=WEEKLY;BYDAY=MO", date(2026, 11, 2), date(2026, 11, 1), date(2026, 11, 30))
    assert r == [date(2026, 11, d) for d in (2, 9, 16, 23, 30)]


def test_occurrences_interval():
    r = occurrences("FREQ=WEEKLY;INTERVAL=2;BYDAY=MO", date(2026, 11, 2), date(2026, 11, 1), date(2026, 11, 30))
    assert r == [date(2026, 11, d) for d in (2, 16, 30)]


def test_monthly_31_skips():
    r = occurrences("FREQ=MONTHLY;BYMONTHDAY=31", date(2026, 10, 31), date(2026, 10, 1), date(2027, 3, 31))
    assert r == [date(2026, 10, 31), date(2026, 12, 31), date(2027, 1, 31), date(2027, 3, 31)]


def test_exdates_and_dtstart():
    r = occurrences("FREQ=WEEKLY;BYDAY=MO", date(2026, 11, 2), date(2026, 10, 1), date(2026, 11, 16),
                    [date(2026, 11, 9)])
    assert r == [date(2026, 11, 2), date(2026, 11, 16)]


def test_cap():
    d = date(2026, 1, 1)
    assert len(occurrences("FREQ=DAILY", d, d, d + timedelta(days=3650))) == 1000


def test_interval_guards():
    assert occurrences("FREQ=DAILY;INTERVAL=0", date(2026, 11, 1), date(2026, 11, 1), date(2026, 11, 3))


def test_interval_plurals():
    for n, w in [(5, "недель"), (2, "недели")]:
        assert describe(f"FREQ=WEEKLY;INTERVAL={n}") == f"каждые {n} {w}"
    assert describe("FREQ=DAILY;INTERVAL=11") == "каждые 11 дней"
    assert describe("FREQ=DAILY;INTERVAL=22") == "каждые 22 дня"
    assert describe("FREQ=YEARLY;INTERVAL=3") == "каждые 3 года"
    assert describe("FREQ=YEARLY;INTERVAL=5") == "каждые 5 лет"


def test_interval_with_days():
    assert describe("FREQ=WEEKLY;INTERVAL=2;BYDAY=MO") == "каждые 2 недели по понедельникам"


def test_describe_three_days():
    assert describe("FREQ=WEEKLY;BYDAY=MO,TU,WE") == "по понедельникам, вторникам и средам"


def test_occurrences_scan_bounded():
    import time
    t = time.time()
    r = occurrences("FREQ=YEARLY", date(2024, 1, 1), date(1, 1, 1), date(9999, 12, 31))
    assert time.time() - t < 0.5 and len(r) <= 11


def test_occurrences_near_date_max():
    from datetime import date
    from app.domain.recurrence import occurrences
    assert len(occurrences("FREQ=DAILY", date(9999, 1, 1), date(9999, 12, 1), date(9999, 12, 31))) == 31


def test_last_due_collapses_missed():
    from datetime import datetime, time
    from app.domain.dates import MSK
    from app.domain.recurrence import last_due
    base, now = datetime(2026, 10, 20, 12, tzinfo=MSK), datetime(2026, 10, 25, 12, tzinfo=MSK)
    args = ("FREQ=DAILY", date(2026, 10, 1), time(9, tzinfo=MSK))
    assert last_due(*args, base, now) == datetime(2026, 10, 25, 9, tzinfo=MSK)
    assert last_due(*args, now, now) is None
    assert last_due(*args, base, now, [date(2026, 10, 25)]) == datetime(2026, 10, 24, 9, tzinfo=MSK)
