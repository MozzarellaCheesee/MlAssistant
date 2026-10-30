from datetime import date

import pytest

from app.domain.dates import (MONTHS_GEN, MSK, WEEKDAYS_ACC, DateRange, add_months, parse_date_expr, parse_hhmm,
                              parse_period_expr)


def test_date_range_and_constants():
    r = DateRange(date(2026, 10, 28), date(2026, 11, 1))
    assert r.contains(date(2026, 10, 30)) and not r.contains(date(2026, 11, 2))
    assert len(WEEKDAYS_ACC) == 7 and len(MONTHS_GEN) == 12 and str(MSK) == "Europe/Moscow"


@pytest.mark.parametrize("s,ok", [("9:05", True), ("00:00", True), ("23:59", True), ("24:30", False), ("29:00", False),
                                  ("25:70", False), ("-3:00", False), ("10", False), ("10:5", False)])
def test_parse_hhmm(s, ok):
    assert (parse_hhmm(s) is not None) == ok


WED = date(2026, 10, 28)  # среда


@pytest.mark.parametrize("expr,today,want", [
    ("today", WED, WED), ("+1d", WED, date(2026, 10, 29)), ("+2w", WED, date(2026, 11, 11)), ("+1m", WED, date(2026, 11, 28)),
    ("+1m", date(2026, 1, 31), date(2026, 2, 28)), ("+1m", date(2028, 1, 31), date(2028, 2, 29)),
    ("+1m", date(2026, 12, 15), date(2027, 1, 15)), ("+13m", WED, date(2027, 11, 28)), ("-1d", WED, date(2026, 10, 27)),
    ("fri", WED, date(2026, 10, 30)), ("wed", WED, date(2026, 11, 4)), ("tue", WED, date(2026, 11, 3)),
    ("mon", date(2026, 10, 25), date(2026, 10, 26)),  # сегодня воскресенье
    ("next mon", WED, date(2026, 11, 2)), ("next wed", WED, date(2026, 11, 4)), ("next sun", WED, date(2026, 11, 8)),
    ("next fri", date(2026, 10, 25), date(2026, 10, 30)),  # сегодня воскресенье: следующая неделя -- та, что после этой
    ("eow", WED, date(2026, 11, 1)), ("eow", date(2026, 11, 1), date(2026, 11, 1)), ("eom", WED, date(2026, 10, 31)),
    ("eom", date(2028, 2, 3), date(2028, 2, 29)),
    ("12-15", WED, date(2026, 12, 15)), ("10-28", WED, WED), ("10-27", WED, date(2027, 10, 27)),
    ("12-15", date(2026, 12, 16), date(2027, 12, 15)), ("2026-11-05", WED, date(2026, 11, 5)), (" FRI ", WED, date(2026, 10, 30)),
    ("15-12", WED, date(2026, 12, 15)), ("завтра", WED, None), ("", WED, None), ("13-13", WED, None), ("02-30", WED, None), ("+d", WED, None),
    ("2026-13-45", WED, None), ("next", WED, None), ("+1y", WED, None),
])
def test_parse_date_expr(expr, today, want):
    assert parse_date_expr(expr, today) == want


def test_add_months_clamps_and_wraps():
    assert add_months(date(2026, 3, 31), -1) == date(2026, 2, 28)
    assert add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)


@pytest.mark.parametrize("expr,want", [
    ("week", (WED, date(2026, 11, 1))), ("next week", (date(2026, 11, 2), date(2026, 11, 8))),
    ("month", (WED, date(2026, 10, 31))), ("next month", (date(2026, 11, 1), date(2026, 11, 30))),
    ("+1d", (date(2026, 10, 29),) * 2), ("mon..wed", (date(2026, 11, 2), date(2026, 11, 4))),
    ("today..eow", (WED, date(2026, 11, 1))), ("bad", None), ("mon..bad", None), ("..mon", None),
])
def test_parse_period_expr(expr, want):
    assert parse_period_expr(expr, WED) == want


def test_next_month_year_wrap():
    assert parse_period_expr("next month", date(2026, 12, 10)) == (date(2027, 1, 1), date(2027, 1, 31))
