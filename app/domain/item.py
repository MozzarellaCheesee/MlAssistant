from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum

DEFAULT_MEETING_DURATION = timedelta(hours=1)


class ItemKind(StrEnum):
    TASK = "task"
    MEETING = "meeting"
    REMINDER = "reminder"

    @property
    def matches(self) -> tuple[str, ...]:
        """Значения kind, попадающие под фильтр: «задачи» -- это и задачи, и напоминания."""
        return ("task", "reminder") if self is ItemKind.TASK else (self.value,)


class Status(StrEnum):
    OPEN = "open"
    DONE = "done"
    CANCELLED = "cancelled"


@dataclass
class Item:
    kind: ItemKind
    title: str
    id: int | None = None
    user_id: str = "default"
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    due_date: date | None = None
    status: Status = Status.OPEN
    created_at: datetime | None = None
    source: str = "user"
    source_text: str | None = None
    notified_at: datetime | None = None
    rrule: str | None = None
    exdates: list[date] = field(default_factory=list)
    external_id: str | None = None
    occurrence: date | None = None

    def effective_end(self) -> datetime | None:
        if self.ends_at:
            return self.ends_at
        if self.kind == ItemKind.MEETING and self.starts_at:
            return self.starts_at + DEFAULT_MEETING_DURATION
        return self.starts_at

    def day(self) -> date | None:
        return self.due_date or (self.starts_at.date() if self.starts_at else None)
