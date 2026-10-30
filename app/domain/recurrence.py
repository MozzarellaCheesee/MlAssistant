from datetime import date, datetime, time, timedelta

from app.domain.dates import MSK

# подмножество RRULE (FREQ, INTERVAL, BYDAY для WEEKLY, BYMONTHDAY для MONTHLY), без COUNT/UNTIL/BYSETPOS;
# ключи добавлять, когда их потребует сценарий
_DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
_ACC = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
_DAT = ["понедельникам", "вторникам", "средам", "четвергам", "пятницам", "субботам", "воскресеньям"]
_FORMS = {
    "DAILY": ("день", "дня", "дней"),
    "WEEKLY": ("неделю", "недели", "недель"),
    "MONTHLY": ("месяц", "месяца", "месяцев"),
    "YEARLY": ("год", "года", "лет"),
}
_MAX = 1000
_SPAN = 3660
_MAX_GAP_DAYS = 62  # самый длинный промежуток: BYMONTHDAY=31 через короткие месяцы
_WORKDAYS = [0, 1, 2, 3, 4]
_WEEKEND = [5, 6]
_FEMININE_DAYS = (2, 4, 5)  # среда, пятница, суббота: «каждую»
_SUNDAY = 6
_MAX_MONTHDAY = 31


def validate_rrule(rrule: str) -> str | None:
    """Приводит к порядку FREQ;INTERVAL;BYDAY;BYMONTHDAY; ValueError, если правило вне поддерживаемого подмножества."""
    if rrule is None or not rrule.strip():
        return None
    try:
        parts = _parse(rrule.strip().upper())
    except ValueError:
        raise ValueError(f"bad rrule: {rrule}") from None
    freq = parts.get("FREQ")
    if freq not in _FORMS or not set(parts) <= {"FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY"}:
        raise ValueError(f"unsupported rrule: {rrule}")
    if "INTERVAL" in parts and not (parts["INTERVAL"].isdigit() and int(parts["INTERVAL"]) >= 1):
        raise ValueError("INTERVAL must be >= 1")
    if "BYDAY" in parts and (freq != "WEEKLY" or not set(parts["BYDAY"].split(",")) <= set(_DAYS)):
        raise ValueError("BYDAY only for WEEKLY with MO..SU")
    if "BYMONTHDAY" in parts and (
        freq != "MONTHLY" or not (parts["BYMONTHDAY"].isdigit() and 1 <= int(parts["BYMONTHDAY"]) <= _MAX_MONTHDAY)
    ):
        raise ValueError("BYMONTHDAY only for MONTHLY, 1..31")
    return ";".join(f"{key}={parts[key]}" for key in ("FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY") if key in parts)


def _parse(rrule: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in rrule.split(";"))


def _plural_form(number: int, one: str, few: str, many: str) -> str:
    if number % 100 in range(11, 15):
        return many
    if number % 10 == 1:
        return one
    if number % 10 in (2, 3, 4):
        return few
    return many


def _every_prefix(weekday: int) -> str:
    if weekday in _FEMININE_DAYS:
        return "каждую "
    if weekday == _SUNDAY:
        return "каждое "
    return "каждый "


def describe(rrule: str) -> str:
    parts = _parse(rrule)
    freq, interval = parts["FREQ"], int(parts.get("INTERVAL", 1))
    weekdays = [_DAYS.index(day) for day in parts["BYDAY"].split(",")] if freq == "WEEKLY" and "BYDAY" in parts else []
    dative = [_DAT[index] for index in weekdays]
    dative = "по " + (", ".join(dative[:-1]) + " и " + dative[-1] if len(dative) > 1 else dative[0]) if weekdays else ""
    if interval != 1:
        one, few, many = _FORMS[freq]
        unit = _plural_form(interval, one, few, many)
        return f"каждые {interval} {unit}" + (" " + dative if weekdays else "")
    if weekdays:
        if weekdays == _WORKDAYS:
            return "по будням"
        if weekdays == _WEEKEND:
            return "по выходным"
        if len(weekdays) == 1:
            return _every_prefix(weekdays[0]) + _ACC[weekdays[0]]
        return dative
    if freq == "MONTHLY" and "BYMONTHDAY" in parts:
        return f"каждое {parts['BYMONTHDAY']} число"
    return {"DAILY": "каждый день", "WEEKLY": "каждую неделю", "MONTHLY": "каждый месяц", "YEARLY": "каждый год"}[freq]


def _match(parts: dict[str, str], day: date, dtstart: date | None) -> bool:
    """dtstart=None: проверяются только BYDAY/BYMONTHDAY (создание серии)."""
    interval = max(1, int(parts.get("INTERVAL", 1)))
    freq = parts["FREQ"]
    if freq == "WEEKLY":
        days = (
            [_DAYS.index(code) for code in parts["BYDAY"].split(",")]
            if "BYDAY" in parts
            else [dtstart.weekday()]
            if dtstart
            else None
        )
        weeks = (
            ((day - timedelta(days=day.weekday())) - (dtstart - timedelta(days=dtstart.weekday()))).days // 7
            if dtstart
            else 0
        )
        return (days is None or day.weekday() in days) and weeks % interval == 0
    if freq == "MONTHLY":
        month_day = int(parts["BYMONTHDAY"]) if "BYMONTHDAY" in parts else dtstart.day if dtstart else None
        months = (day.year - dtstart.year) * 12 + day.month - dtstart.month if dtstart else 0
        return (month_day is None or day.day == month_day) and months % interval == 0
    if not dtstart:
        return True
    if freq == "DAILY":
        return (day - dtstart).days % interval == 0
    return (day.year - dtstart.year) % interval == 0 and (day.month, day.day) == (dtstart.month, dtstart.day)


def first_on_or_after(rrule: str, start: date) -> date | None:
    parts = _parse(rrule)
    for offset in range(_MAX_GAP_DAYS):
        if _match(parts, candidate := start + timedelta(days=offset), None):
            return candidate
    return None


def occurrences(rrule: str, dtstart: date, rng_start: date, rng_end: date, exdates: list[date] = ()) -> list[date]:
    parts, found, day, excluded = _parse(rrule), [], max(dtstart, rng_start), set(exdates)
    if (rng_end - day).days > _SPAN:
        rng_end = day + timedelta(days=_SPAN)
    # перебор по дням, не дальше _SPAN дней от начала диапазона;
    # для длинных диапазонов -- прыгать по периоду
    while day <= rng_end and len(found) < _MAX:
        if day not in excluded and _match(parts, day, dtstart):
            found.append(day)
        if day == date.max:
            break
        day += timedelta(days=1)
    return found


def last_due(
    rrule: str, dtstart: date, at: time, base: datetime, now: datetime, exdates: list[date] = ()
) -> datetime | None:
    """Пропущенные повторения схлопываются в одно -- последнее, что наступило после base (at: время суток в MSK)."""
    low = max(dtstart, base.astimezone(MSK).date())
    moments = [datetime.combine(day, at) for day in occurrences(rrule, dtstart, low, now.date(), exdates)]
    moments = [moment for moment in moments if moment <= now]
    return moments[-1] if moments and moments[-1] > base else None
