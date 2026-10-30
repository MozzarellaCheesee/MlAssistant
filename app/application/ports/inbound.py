from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from app.domain.dates import DateRange
from app.domain.item import Item, ItemKind, Status


@dataclass
class Reply:
    reply: str
    items: list[Item] = field(default_factory=list)
    clarify: bool = False
    transcript: str | None = None
    error: str | None = None
    message_ids: list[int] = field(default_factory=list)


class AssistantPort(Protocol):
    def handle_turn(self, session_id: str, user_id: str, text: str, source: str = "text") -> Reply: ...
    def transcribe(self, audio: bytes) -> str: ...
    def handle_audio(self, session_id: str, user_id: str, audio: bytes) -> Reply: ...
    def log_voice_error(self, user_id: str, text: str) -> None: ...
    def history(self, user_id: str, after_id: int, limit: int) -> list[dict]: ...
    def message(self, user_id: str, message_id: int) -> dict | None: ...
    def list_items(self, user_id: str, rng: DateRange, kind: ItemKind | None, status: Status | None) -> list[Item]: ...
    def due_reminders(self, user_id: str) -> list[Item]: ...
    def create_from_form(
        self, user_id: str, kind: ItemKind, title: str, day: date, time: str | None, duration_minutes: int | None
    ) -> Item: ...
    def update_from_form(
        self,
        user_id: str,
        item_id: int,
        kind: ItemKind | None,
        title: str | None,
        day: date | None,
        time: str | None = ...,
        duration_minutes: int | None = ...,
    ) -> Item: ...
    def delete_item(self, user_id: str, item_id: int) -> None: ...
