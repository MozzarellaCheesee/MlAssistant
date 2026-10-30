from datetime import date, datetime

from app.domain.dates import MONTHS_GEN, WEEKDAYS_ACC
from app.domain.item import Item
from app.domain.recurrence import describe

SOON_NAMES = ("сегодня", "завтра", "послезавтра")
WEEKDAY_NAMED_DAYS = 6  # до скольких дней вперёд называем день недели
LIST_LIMIT = 7


def format_when(value: Item | datetime | date, now: datetime) -> str:
    if isinstance(value, Item):
        if value.rrule and value.occurrence is None:
            return _series(value, now)
        value = value.starts_at or value.due_date
    if value is None:
        return ""
    moment = value if isinstance(value, datetime) else None
    day = value.date() if moment else value
    delta = (day - now.date()).days
    day_month = f"{day.day} {MONTHS_GEN[day.month - 1]}"
    if 0 <= delta < len(SOON_NAMES):
        text = SOON_NAMES[delta]
    elif len(SOON_NAMES) <= delta <= WEEKDAY_NAMED_DAYS:
        text = f"{WEEKDAYS_ACC[day.weekday()]}, {day_month}"
    else:
        text = day_month if day.year == now.year else f"{day_month} {day.year}"
    return f"{text} в {moment.hour}:{moment.minute:02d}" if moment else text


def _series(item: Item, now: datetime) -> str:
    text, first = describe(item.rrule), item.day()
    if item.starts_at:
        text += f" в {item.starts_at.hour}:{item.starts_at.minute:02d}"
    if first and (first - now.date()).days not in (0, 1):
        text += f", с {first.day} {MONTHS_GEN[first.month - 1]}"
    return text


def plural(n: int, one: str, few: str, many: str) -> str:
    if 11 <= n % 100 <= 14:
        return many
    if n % 10 == 1:
        return one
    if 2 <= n % 10 <= 4:
        return few
    return many


def _line(item: Item, now: datetime) -> str:
    return f"{format_when(item, now) or 'без срока'} — {item.title}" + (" (повтор)" if item.occurrence else "")


def format_list(items: list[Item], now: datetime, limit: int = LIST_LIMIT) -> str:
    count = len(items)
    text = f"{count} {plural(count, 'запись', 'записи', 'записей')}: " + "; ".join(
        _line(item, now) for item in items[:limit]
    )
    return text + f" и ещё {count - limit}" if count > limit else text


def format_choice(items: list[Item], now: datetime) -> str:
    return "Какую из них? " + " ".join(f"{number}) {_line(item, now)}" for number, item in enumerate(items, 1))
