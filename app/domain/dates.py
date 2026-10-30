import calendar
import re
from dataclasses import dataclass
from datetime import date, time, timedelta
from zoneinfo import ZoneInfo

MONTHS_IN_YEAR = 12
DAYS_IN_WEEK = 7
MSK = ZoneInfo("Europe/Moscow")
WEEKDAYS_ACC = ["в понедельник", "во вторник", "в среду", "в четверг", "в пятницу", "в субботу", "в воскресенье"]
MONTHS_GEN = [
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end


def parse_hhmm(text: str) -> time | None:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text.strip())
    return time(int(match[1]), int(match[2])) if match and int(match[1]) <= 23 and int(match[2]) <= 59 else None


def add_months(day: date, months: int) -> date:
    """+months месяцев, день обрезается до конца месяца (31 янв + 1 мес = 28/29 фев)."""
    years, month_index = divmod(day.month - 1 + months, MONTHS_IN_YEAR)
    year, month = day.year + years, month_index + 1
    return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1]))


_WD = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _next_month_day(first: int, second: int, today: date) -> date | None:
    """MM-DD (или DD-MM, если первое число больше 12): ближайшая такая дата не раньше сегодня."""
    month, day = (second, first) if first > MONTHS_IN_YEAR else (first, second)
    for year in (today.year, today.year + 1):
        try:
            if (candidate := date(year, month, day)) >= today:
                return candidate
        except ValueError:
            pass
    return None


def parse_date_expr(expr: str, today: date) -> date | None:
    """Дата из DSL модели: today, +Nd/+Nw/+Nm, mon..sun, next mon..sun, eow, eom, MM-DD, YYYY-MM-DD. Мусор -> None."""
    text = expr.strip().lower()
    try:
        if text == "today":
            return today
        if match := re.fullmatch(r"([+-]\d+)([dwm])", text):
            amount, unit = int(match[1]), match[2]
            if unit == "m":
                return add_months(today, amount)
            return today + timedelta(days=amount * (DAYS_IN_WEEK if unit == "w" else 1))
        if match := re.fullmatch(r"(next )?(" + "|".join(_WD) + ")", text):
            weekday = _WD.index(match[2])
            if match[1]:
                return today - timedelta(days=today.weekday()) + timedelta(days=DAYS_IN_WEEK + weekday)
            return today + timedelta(days=(weekday - today.weekday() - 1) % DAYS_IN_WEEK + 1)  # строго после сегодня
        if text == "eow":
            return today + timedelta(days=DAYS_IN_WEEK - 1 - today.weekday())
        if text == "eom":
            return today.replace(day=calendar.monthrange(today.year, today.month)[1])
        if match := re.fullmatch(r"(\d\d)-(\d\d)", text):
            return _next_month_day(int(match[1]), int(match[2]), today)
        if re.fullmatch(r"\d{4}-\d\d-\d\d", text):
            return date.fromisoformat(text)
    except (ValueError, OverflowError):
        pass
    return None


def parse_period_expr(expr: str, today: date) -> tuple[date, date] | None:
    """Период из DSL: week, next week, month, next month, A..B или один день."""
    text = expr.strip().lower()
    if text == "week":
        return today, parse_date_expr("eow", today)
    if text == "next week":
        monday = parse_date_expr("next mon", today)
        return monday, monday + timedelta(days=DAYS_IN_WEEK - 1)
    if text == "month":
        return today, parse_date_expr("eom", today)
    if text == "next month":
        first = add_months(today.replace(day=1), 1)
        return first, add_months(first, 1) - timedelta(days=1)
    start_text, _, end_text = text.partition("..")
    start = parse_date_expr(start_text, today)
    end = parse_date_expr(end_text, today) if end_text else None
    if start and (end or ".." not in text):
        return start, end or start
    return None
