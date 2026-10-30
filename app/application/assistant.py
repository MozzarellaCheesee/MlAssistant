from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.application.dialog import DialogState, is_number, resolve_reply
from app.application.errors import AssistantError, ErrorCode, FormError
from app.application.handlers import Handler
from app.application.handlers.calendar import sync_item, unsync_item
from app.application.handlers.when import resolve_when
from app.application.intents import Confirm, CreateMeeting, CreateTask, DeleteItem, Several, Target, Unknown, UpdateItem
from app.application.ports.inbound import Reply
from app.application.ports.outbound import (
    CalendarSync,
    Clock,
    DialogStore,
    IntentParser,
    ItemRepository,
    MessageLog,
    ParseContext,
    SpeechToText,
)
from app.application.results import Question, Result, TurnContext
from app.domain.dates import DateRange
from app.domain.item import Item, ItemKind, Status

MAX_COMMANDS = 10  # команд в одной фразе
CONFIRM_HINT_AFTER = 2  # после скольких непонятых ответов подсказываем, что сказать
SECONDS_PER_MINUTE = 60


@dataclass
class _Turn:
    key: str  # ключ диалога: пользователь и сессия
    state: DialogState
    user_id: str
    now: datetime
    text: str


class AssistantService:
    def __init__(
        self,
        parser: IntentParser,
        stt: SpeechToText | None,
        repo: ItemRepository,
        dialog: DialogStore,
        clock: Clock,
        handlers: dict[type, Handler],
        log: MessageLog | None = None,
        calendar: CalendarSync | None = None,
    ):
        self.parser = parser
        self.stt = stt
        self.repo = repo
        self.dialog = dialog
        self.clock = clock
        self.handlers = handlers
        self.log = log
        self.calendar = calendar

    def handle_turn(self, session_id: str, user_id: str, text: str, source: str = "text") -> Reply:
        user_message_id = self.log.add(user_id, "user", text, source) if self.log else None
        key = f"{user_id}:{session_id}"
        before = self.dialog.load(key).pending
        try:
            reply = self._turn(session_id, user_id, text)
            if (
                before and before.queue and self.dialog.load(key).pending is None
            ):  # ответили на вопрос -- доделываем фразу
                rest = self._chain(session_id, user_id, before.queue)
                reply = Reply(f"{reply.reply} {rest.reply}", items=reply.items + rest.items, clarify=rest.clarify)
        except AssistantError as error:
            self._log_error(user_id, error, source, user_message_id)
            raise
        if self.log:
            reply.message_ids = [user_message_id, self.log.add(user_id, "assistant", reply.reply, source, reply.items)]
        return reply

    def _log_error(self, user_id: str, error: AssistantError, source: str, user_message_id: int | None = None) -> None:
        if self.log:
            assistant_id = self.log.add(user_id, "assistant", error.user_message, source)
            error.message_ids = [message_id for message_id in (user_message_id, assistant_id) if message_id]

    def log_voice_error(self, user_id: str, text: str) -> None:
        if self.log:
            self.log.add(user_id, "assistant", text, "voice")

    def history(self, user_id: str, after_id: int, limit: int) -> list[dict]:
        return self.log.since(user_id, after_id, limit) if self.log else []

    def message(self, user_id: str, message_id: int) -> dict | None:
        return self.log.get(user_id, message_id) if self.log else None

    def _turn(self, session_id: str, user_id: str, text: str, depth: int = 0, day: date | None = None) -> Reply:
        """Один ход: понять фразу, связать её с открытым вопросом (pending), выполнить команду."""
        key = f"{user_id}:{session_id}"
        turn = _Turn(key, self.dialog.load(key), user_id, self.clock.now(), text)
        pending = turn.state.pending_intent()
        intent = resolve_reply(turn.state, text, turn.now)
        from_reply = intent is not None  # ответ на вопрос понят без LLM (выбор из списка, «да», время)
        if intent is None:
            intent = self._parse(turn, depth)
        if isinstance(intent, Several):  # «купи хлеб, потом удали встречу и напомни …»: команды по очереди
            if not depth:
                return self._chain(session_id, user_id, intent.commands[:MAX_COMMANDS])
            intent = Unknown()
        if day and isinstance(intent, (CreateTask, CreateMeeting)) and not (intent.date or intent.shift_minutes):
            # «…послезавтра в 19 и поставь на 12 встречу…»: день предыдущей команды
            intent = intent.model_copy(update={"date": day})
        if pending is None:
            if isinstance(intent, Confirm):  # «да/нет» без вопроса -- болтовня, не подтверждение
                intent = Unknown()
            return self._run(turn, intent, set())
        return self._continue_pending(turn, pending, intent, from_reply)

    def _parse(self, turn: _Turn, depth: int):
        state = turn.state
        recent = [item for item in (self.repo.get(turn.user_id, id_) for id_ in state.last_referenced_ids) if item]
        context = ParseContext(
            turn.now,
            state.pending.intent if state.pending else None,
            recent,
            state.pending.question if state.pending else None,
            depth == 0,  # делить фразу на команды только на верхнем уровне
        )
        return self.parser.parse(turn.text, context)

    def _continue_pending(self, turn: _Turn, pending, intent, from_reply: bool) -> Reply:
        """Ассистент ждёт ответа: подтверждение, уточнение черновика или смена темы."""
        asked = turn.state.pending
        awaiting_confirm = asked.awaiting == "confirm"
        if isinstance(intent, Confirm) and not awaiting_confirm:
            intent = Unknown()
        if isinstance(intent, Unknown):  # непонятный ответ не сбрасывает черновик
            return self._unclear(turn.key, turn.state)
        if isinstance(intent, Confirm):
            if not from_reply and is_number(turn.text):
                return self._unclear(turn.key, turn.state)  # «пять» -- это время, не «нет»
            return self._answer_confirm(turn, pending, intent)
        if from_reply:
            return self._run(turn, intent, set(asked.confirmed))
        if asked.awaiting in ("slot", "confirm") and type(intent) is type(pending):
            # ответ на слот дополняет черновик
            intent = type(pending).model_validate(
                pending.model_dump() | intent.model_dump(exclude_unset=True, exclude_none=True)
            )
        return self._run(turn, intent, set())  # уточнённая команда или новая тема: прошлые подтверждения не в счёт

    def _answer_confirm(self, turn: _Turn, pending, answer: Confirm) -> Reply:
        if answer.answer == "no":
            self.dialog.save(turn.key, turn.state.done([]))
            return Reply("Хорошо, отменил.")
        asked = turn.state.pending
        return self._run(turn, pending, set(asked.confirmed) | {asked.key})

    def _run(self, turn: _Turn, intent, confirmed: set[str]) -> Reply:
        """Выполнить команду; вопрос обработчика сохраняется как pending до следующей реплики."""
        ctx = TurnContext(turn.user_id, turn.now, turn.state.last_referenced_ids, frozenset(confirmed), turn.text)
        out = self.handlers[type(intent)].handle(intent, ctx)
        if isinstance(out, Question):
            self.dialog.save(turn.key, turn.state.ask(out.intent or intent, out, confirmed))
            return Reply(out.text, items=out.items, clarify=True)
        self.dialog.save(turn.key, turn.state.done(out.referenced_ids))
        return Reply(out.text, items=out.items)

    def _chain(self, session_id: str, user_id: str, cmds: list[str], day: date | None = None) -> Reply:
        """Команды по очереди. Вопрос держит черновик: оставшиеся команды ждут ответа в pending.queue."""
        key = f"{user_id}:{session_id}"
        replies = []
        for index, command in enumerate(cmds):
            replies.append(self._turn(session_id, user_id, command, 1, day))
            state = self.dialog.load(key)
            referenced = [self.repo.get(user_id, id_) for id_ in state.last_referenced_ids]
            day = next((item.day() for item in referenced if item and item.day()), day)
            if replies[-1].clarify:
                rest = cmds[index + 1 :]
                if state.pending and rest:
                    pending = state.pending.model_copy(update={"queue": rest})
                    self.dialog.save(key, state.model_copy(update={"pending": pending}))
                break
        return Reply(
            " ".join(reply.reply for reply in replies),
            items=[item for reply in replies for item in reply.items],
            clarify=replies[-1].clarify,
        )

    def _unclear(self, key: str, state) -> Reply:
        pending = state.pending
        unclear = pending.unclear + 1
        self.dialog.save(key, state.model_copy(update={"pending": pending.model_copy(update={"unclear": unclear})}))
        if unclear >= CONFIRM_HINT_AFTER and pending.awaiting == "confirm":
            hint = " Скажите «да», «нет» или новое время."
        elif pending.slot == "time":
            hint = " Например: в 15."
        else:
            hint = ""
        return Reply(f"Не понял. {pending.question}{hint}", clarify=True)

    def transcribe(self, audio: bytes) -> str:
        return self.stt.transcribe(audio).strip()

    def handle_audio(self, session_id: str, user_id: str, audio: bytes) -> Reply:
        try:
            text = self.stt.transcribe(audio).strip()
            if not text:
                raise AssistantError(ErrorCode.STT_EMPTY)
        except AssistantError as error:
            self._log_error(user_id, error, "voice")
            raise
        reply = self.handle_turn(session_id, user_id, text, "voice")
        reply.transcript = text
        return reply

    def list_items(self, user_id: str, rng: DateRange, kind: ItemKind | None, status: Status | None) -> list[Item]:
        return self.repo.list_between(user_id, rng, kind, status)

    def due_reminders(self, user_id: str) -> list[Item]:
        due = self.repo.due_reminders(user_id, self.clock.now())
        for item in due:
            self.log_voice_error(user_id, f"Напоминание: {item.title}")
        return due

    # Форма панели: те же handlers, что у чата. Прошлое и пересечение встреч форма подтверждает сама (явный выбор).
    def _form(self, intent, user_id: str) -> Result:
        ctx = TurnContext(user_id, self.clock.now(), [], frozenset({"past", "conflict"}), "")
        out = self.handlers[type(intent)].handle(intent, ctx)
        if isinstance(out, Question):
            raise FormError(out.text)
        return out

    def _own(self, user_id: str, item_id: int) -> Item:
        item = self.repo.get(user_id, item_id)
        if not item or item.status == Status.CANCELLED:
            raise FormError("Запись не найдена.", not_found=True)
        return item

    def create_from_form(
        self, user_id: str, kind: ItemKind, title: str, day: date, time: str | None, duration_minutes: int | None
    ) -> Item:
        fields = dict(title=title, date=day, time=time)
        if kind == ItemKind.MEETING:
            intent = CreateMeeting(**fields, duration_minutes=duration_minutes)
        else:
            intent = CreateTask(**fields, kind=kind.value)
        out = self._form(intent, user_id)
        return out.items[0]

    def update_from_form(
        self,
        user_id: str,
        item_id: int,
        kind: ItemKind | None,
        title: str | None,
        day: date | None,
        time: str | None = ...,
        duration_minutes: int | None = ...,
    ) -> Item:
        """Ellipsis = поле не передано, оставить как есть; None = снять."""
        item = self._own(user_id, item_id)
        was_minutes = (
            int((item.ends_at - item.starts_at).total_seconds() // SECONDS_PER_MINUTE)
            if item.ends_at and item.starts_at
            else None
        )
        hhmm = f"{item.starts_at:%H:%M}" if item.starts_at else None
        kind, title, day = kind or item.kind, title or item.title, day or item.day()
        time = hhmm if time is ... else time
        duration_minutes = was_minutes if duration_minutes is ... else duration_minutes
        if not item.rrule and (
            kind != item.kind or (hhmm and not time) or kind == ItemKind.MEETING and duration_minutes != was_minutes
        ):
            # UpdateItem не меняет вид, не снимает время и не правит длительность: правим запись на месте, id остаётся
            meeting = kind == ItemKind.MEETING
            fields = dict(title=title, date=day, time=time)
            when = resolve_when(
                CreateMeeting(**fields) if meeting else CreateTask(**fields, kind=kind.value),
                self.clock.now(),
                kind != ItemKind.TASK,
                "Во сколько?",
            )
            if isinstance(when, Question):
                raise FormError(when.text)
            item.kind = kind
            item.title = title
            item.starts_at = when.starts_at
            item.due_date = when.due_date
            item.notified_at = None
            item.ends_at = (
                when.starts_at + timedelta(minutes=duration_minutes) if meeting and duration_minutes else None
            )
            self.repo.update(item)
            if item.starts_at is None or kind == ItemKind.TASK:
                unsync_item(self.calendar, item)
                item.external_id = None
                self.repo.update(item)
            else:
                sync_item(self.calendar, self.repo, item)
            return item
        changes = {
            key: value
            for key, value in dict(
                title=title if title != item.title else None,
                new_time=time if time != hhmm else None,
                new_date=day if day != item.day() else None,
            ).items()
            if value
        }
        if changes:
            out = self._form(UpdateItem(target=Target(id=item_id), scope="all", **changes), user_id)
            if not out.items:
                raise FormError(out.text)
        return self._own(user_id, item_id)

    def delete_item(self, user_id: str, item_id: int) -> None:
        self._own(user_id, item_id)
        self._form(DeleteItem(target=Target(id=item_id), scope="all"), user_id)
