from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.application.results import Question
from app.domain.dates import parse_hhmm
from app.domain.recurrence import first_on_or_after


@dataclass
class When:
    starts_at: datetime | None
    due_date: date | None


def resolve_when(intent, now: datetime, need_time: bool, time_question: str) -> When | Question:
    if intent.shift_minutes and not intent.time:  # названное время важнее сдвига
        start = (now + timedelta(minutes=intent.shift_minutes)).replace(second=0, microsecond=0)
        return When(start, start.date())
    if intent.time:
        said_time = parse_hhmm(intent.time)
        if said_time is None:
            return Question("Такого времени нет. Во сколько?", "slot", slot="time")
        day = intent.date or now.date()
        start = datetime.combine(day, said_time, now.tzinfo)
        if not intent.date and start <= now:  # только время: ближайшее будущее
            start += timedelta(days=1)
            day += timedelta(days=1)
        return When(start, day)
    if need_time:
        return Question(time_question, "slot", slot="time")
    return When(None, intent.date)


def resolve_series(intent, now: datetime, need_time: bool, time_question: str) -> When | Question:
    when = resolve_when(intent, now, need_time, time_question)
    if isinstance(when, Question):
        return when
    day = when.due_date or now.date()
    first = first_on_or_after(intent.rrule, day)
    start_time = when.starts_at.timetz() if when.starts_at else None
    if first == now.date() == day and start_time and datetime.combine(first, start_time) <= now:
        first = first_on_or_after(intent.rrule, first + timedelta(days=1))
    if first is None:
        return Question(
            "Не понял, как часто повторять. Например: каждый день, по будням, каждый понедельник.",
            "slot",
            slot="recurrence",
        )
    return When(datetime.combine(first, start_time) if start_time else None, first)
