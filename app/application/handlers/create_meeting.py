from dataclasses import dataclass
from datetime import timedelta

from app.application.handlers.calendar import sync_item
from app.application.handlers.create_task import PAST_Q, conflict_q, is_past
from app.application.handlers.when import resolve_series, resolve_when
from app.application.intents import CreateMeeting
from app.application.ports.outbound import CalendarSync, ItemRepository
from app.application.render import format_when
from app.application.results import Question, Result, TurnContext
from app.domain.item import Item, ItemKind


@dataclass
class CreateMeetingHandler:
    repo: ItemRepository
    calendar: CalendarSync | None = None

    def handle(self, intent: CreateMeeting, ctx: TurnContext) -> Result | Question:
        if not intent.title:
            return Question("Что за встреча?", "slot", slot="title")
        time_question = "На какое время поставить встречу?"
        rrule = intent.rrule
        when = (resolve_series if rrule else resolve_when)(intent, ctx.now, True, time_question)
        if isinstance(when, Question):
            return when
        if not rrule and is_past(when, ctx) and "past" not in ctx.confirmed:
            return PAST_Q
        duration = timedelta(minutes=intent.duration_minutes) if intent.duration_minutes else None
        item = Item(
            kind=ItemKind.MEETING,
            title=intent.title,
            user_id=ctx.user_id,
            starts_at=when.starts_at,
            due_date=when.due_date,
            rrule=rrule,
            ends_at=when.starts_at + duration if duration else None,
            source_text=ctx.source_text,
        )
        clash = self.repo.overlapping_meetings(ctx.user_id, item.starts_at, item.effective_end())
        if clash and "conflict" not in ctx.confirmed:
            return conflict_q(clash[0])
        item = self.repo.add(item)
        warn = sync_item(self.calendar, self.repo, item)
        return Result(f"Встреча добавлена: {item.title}, {format_when(item, ctx.now)}." + warn, [item], [item.id])
