from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, Protocol

from app.application.dialog import DialogState
from app.application.intents import Intent
from app.domain.dates import DateRange
from app.domain.item import Item, ItemKind, Status


@dataclass
class ParseContext:
    now: datetime
    pending: dict | None
    recent: list[Item]
    pending_question: str | None = None
    split: bool = True  # можно ли разбить фразу на несколько команд (не для подкоманд)


@dataclass
class SearchFilters:
    today: date
    kind: ItemKind | None = None
    tense: Literal["past", "future", "any"] = "any"
    date_from: date | None = None
    date_to: date | None = None


class IntentParser(Protocol):
    def parse(self, text: str, ctx: ParseContext) -> Intent: ...


class Embedder(Protocol):
    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class SpeechToText(Protocol):
    def transcribe(self, audio: bytes) -> str: ...


class ItemRepository(Protocol):
    def add(self, item: Item) -> Item: ...
    def get(self, user_id: str, item_id: int) -> Item | None: ...
    def update(self, item: Item) -> None: ...
    def detach_occurrence(self, series: Item, occurrence: Item) -> Item:
        """Обновляет серию (с новым exdate) и добавляет отдельное повторение одной транзакцией."""
        ...

    def delete(self, user_id: str, item_id: int) -> None: ...
    def list_between(
        self, user_id: str, rng: DateRange, kind: ItemKind | None, status: Status | None
    ) -> list[Item]: ...
    def overlapping_meetings(self, user_id: str, start: datetime, end: datetime) -> list[Item]: ...
    def due_reminders(self, user_id: str, now: datetime) -> list[Item]:
        """Открытые напоминания со starts_at <= now и пустым notified_at; выставляет им notified_at = now."""
        ...


class SearchIndex(Protocol):
    def search(self, user_id: str, query: str, filters: SearchFilters, limit: int) -> list[Item]: ...


class DialogStore(Protocol):
    def load(self, session_id: str) -> DialogState: ...
    def save(self, session_id: str, state: DialogState) -> None: ...


class MessageLog(Protocol):
    def add(self, user_id: str, role: str, text: str, source: str, items: list[Item] | None = None) -> int: ...
    def since(self, user_id: str, after_id: int, limit: int = 100) -> list[dict]: ...
    def get(self, user_id: str, message_id: int) -> dict | None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


@dataclass
class User:
    id: str
    yandex_uid: str
    login: str
    display_name: str


@dataclass
class Profile:
    yandex_uid: str
    login: str
    display_name: str
    email: str | None
    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    expires_in: int | None


class IdentityProvider(Protocol):
    def authorize_url(self, state: str) -> str: ...
    def fetch_user(self, code: str) -> Profile: ...


class UserStore(Protocol):
    def upsert_user(self, yandex_uid: str, login: str, display_name: str) -> tuple[User, bool]:
        """Bool равен True, если пользователя создал именно этот вызов."""
        ...

    def save_state(self, state: str, expires_at: datetime, now: datetime) -> None: ...
    def pop_state(self, state: str, now: datetime) -> bool:
        """Удаляет state; True, только если он существовал и не истёк."""
        ...

    def create_session(self, token_hash: str, user_id: str, expires_at: datetime, now: datetime) -> None: ...
    def user_by_session(self, token_hash: str, now: datetime) -> User | None: ...
    def delete_session(self, token_hash: str) -> None: ...


@dataclass
class CalendarLink:
    user_id: str
    email: str
    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    expires_at: datetime


class CalendarLinks(Protocol):
    def save(self, link: CalendarLink) -> None: ...
    def get(self, user_id: str) -> CalendarLink | None: ...
    def delete(self, user_id: str) -> None: ...


class CalendarSync(Protocol):
    def push(self, item: Item) -> str:
        """Создаёт или обновляет событие, возвращает его UID (новый, если item.external_id пуст)."""
        ...

    def remove(self, item: Item) -> None: ...


class TextToSpeech(Protocol):
    def synthesize(self, text: str) -> bytes: ...
