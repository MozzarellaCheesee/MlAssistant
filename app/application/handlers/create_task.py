from dataclasses import dataclass

from app.application.handlers.calendar import sync_item
from app.application.handlers.when import When, resolve_series, resolve_when
from app.application.intents import CreateTask
from app.application.ports.outbound import CalendarSync, ItemRepository
from app.application.render import format_when
from app.application.results import Question, Result, TurnContext
from app.domain.item import Item, ItemKind

PAST_Q = Question("Это время уже прошло. Сохранить всё равно или назовёте другое время?", "confirm", key="past")


def conflict_q(other: Item, verb: str = "Добавить") -> Question:
    return Question(
        f"С {other.starts_at:%H:%M} до {other.effective_end():%H:%M} уже {other.title}. "
        f"{verb} всё равно или назовёте другое время?",
        "confirm",
        key="conflict",
    )


def is_past(when: When | Item, ctx: TurnContext) -> bool:
    return bool((when.starts_at and when.starts_at < ctx.now) or (when.due_date and when.due_date < ctx.now.date()))


@dataclass
class CreateTaskHandler:
    repo: ItemRepository
    calendar: CalendarSync | None = None

    def handle(self, intent: CreateTask, ctx: TurnContext) -> Result | Question:
        if not intent.title:
            return Question("Что именно записать?", "slot", slot="title")
        reminder = intent.kind == "reminder"
        time_question = "Во сколько напомнить?"
        rrule = intent.rrule
        when = (resolve_series if rrule else resolve_when)(intent, ctx.now, reminder, time_question)
        if isinstance(when, Question):
            return when
        if not rrule and is_past(when, ctx) and "past" not in ctx.confirmed:
            return PAST_Q
        item = self.repo.add(
            Item(
                kind=ItemKind(intent.kind),
                title=intent.title,
                user_id=ctx.user_id,
                starts_at=when.starts_at,
                due_date=when.due_date,
                rrule=rrule,
                source_text=ctx.source_text,
            )
        )
        warn = sync_item(self.calendar, self.repo, item)
        when_text = format_when(item, ctx.now)
        if rrule and reminder:
            text = f"Буду напоминать {when_text}: {item.title}."
        elif reminder:
            text = f"Напомню {when_text}: {item.title}."
        elif when_text:
            text = f"Задача добавлена: {item.title}, {when_text}."
        else:
            text = f"Задача добавлена: {item.title}."
        return Result(text + warn, [item], [item.id])
