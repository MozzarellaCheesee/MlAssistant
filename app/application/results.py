from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from app.domain.item import Item


@dataclass
class Result:
    text: str
    items: list[Item] = field(default_factory=list)
    referenced_ids: list[int] = field(default_factory=list)


@dataclass
class Question:
    text: str
    awaiting: Literal["slot", "confirm", "choose"]
    slot: Literal["time", "date", "title", "target", "recurrence", "scope"] | None = None
    key: str | None = None
    options: list[int] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    intent: Any = None  # сохраняется в pending вместо текущего intent, чтобы «да» выполнило его


@dataclass
class TurnContext:
    user_id: str
    now: datetime
    last_ids: list[int]
    confirmed: frozenset[str]
    source_text: str
