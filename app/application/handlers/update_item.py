from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from app.application.handlers.calendar import sync_item
from app.application.handlers.create_task import PAST_Q, conflict_q, is_past
from app.application.handlers.target import resolve_target, series_scope
from app.application.intents import UpdateItem
from app.application.ports.outbound import CalendarSync, ItemRepository, SearchIndex
from app.application.render import format_when
from app.application.results import Question, Result, TurnContext
from app.domain.dates import add_months, parse_hhmm
from app.domain.item import Item, ItemKind

TITLE_QUESTION_WORDS = ("напиши", "с кем", "как называется")


def _fold(text: str) -> str:
    return text.lower().replace("ё", "е")


def _title_is_question(title: str | None, source_text: str) -> bool:
    """«напиши, с кем встреча»: модель приняла кусок вопроса за новое название.

    Эвристика по словам; надёжнее спросить LLM, вопрос это или новое имя.
    """
    said = _fold(source_text)
    if not title or _fold(title).strip(" .?!") not in said:
        return False
    return said.rstrip().endswith("?") or any(word in said for word in TITLE_QUESTION_WORDS)


def _detached_occurrence(series: Item, occurrence: date, ctx) -> Item:
    """Одно повторение серии как отдельная запись: серия получает exdate, повторение -- свою строку."""
    start_time = series.starts_at.timetz() if series.starts_at else None
    return replace(
        series,
        id=None,
        rrule=None,
        exdates=[],
        occurrence=None,
        due_date=occurrence,
        starts_at=datetime.combine(occurrence, start_time) if start_time else None,
        ends_at=series.ends_at and datetime.combine(occurrence, start_time) + (series.ends_at - series.starts_at),
        source_text=ctx.source_text,
        notified_at=None,
        created_at=None,
        external_id=None,
    )


def _pick_occurrence(item: Item, scope, ctx):
    """(запись, серия, дата повторения) либо вопрос «только ближайшее или все?», который нужно вернуть как есть.

    Правка одного повторения работает с его копией; серия тогда -- исходная запись, иначе None.
    """
    if not item.rrule:
        return item, None, None
    occurrence = series_scope(item, scope, ctx)
    if occurrence is not None and not isinstance(occurrence, date):
        return occurrence
    if occurrence is not None and scope != "all":
        return _detached_occurrence(item, occurrence, ctx), item, occurrence
    return item, None, occurrence


def _rename_and_mark(item: Item, intent) -> None:
    if intent.title:
        item.title = intent.title
    if intent.remind and item.kind == ItemKind.TASK:
        item.kind = ItemKind.REMINDER  # срабатывают только напоминания; в списке задач оно всё равно видно


def _delayed(item: Item, shift: timedelta, first_day, delay: timedelta, months: int, new_time, new_date):
    """«на час позже», «на месяц»: сдвиг считается от самой записи, а не от сегодня."""
    if item.starts_at:
        moved_to = add_months(item.starts_at + shift, months) + delay
        return f"{moved_to:%H:%M}", moved_to.date()
    if first_day:
        return new_time, add_months(first_day, months) + timedelta(days=delay.days)
    return new_time, new_date


@dataclass
class UpdateItemHandler:
    repo: ItemRepository
    search: SearchIndex
    calendar: CalendarSync | None = None

    def handle(self, intent: UpdateItem, ctx: TurnContext) -> Result | Question:
        new_time, new_date = self._requested_moment(intent, ctx)
        question = self._ask_missing(intent, ctx, new_time)
        if question:
            return question
        delay = timedelta(days=intent.delay_days or 0, minutes=intent.delay_minutes or 0)
        months = intent.delay_months or 0
        moved = bool(new_time or new_date or delay or months)
        if not (intent.title or moved):
            return Result("Не понял, что изменить.")

        item = resolve_target(intent, ctx, self.repo, self.search)
        if not hasattr(item, "kind"):
            return item
        if intent.title == item.title and not moved:
            return Result(f"Запись уже называется «{item.title}».")
        picked = _pick_occurrence(item, intent.scope, ctx)
        if not isinstance(picked, tuple):
            return picked
        item, series, occurrence = picked

        # starts_at серии -- первое вхождение, оно может быть давно в прошлом; отсчёт от ближайшего
        shift = timedelta(days=(occurrence - item.day()).days) if item.rrule and occurrence else timedelta()
        first_day = item.day() and item.day() + shift
        if delay or months:
            new_time, new_date = _delayed(item, shift, first_day, delay, months, new_time, new_date)
        if intent.remind and item.kind == ItemKind.MEETING:
            return self._remind_about_meeting(item, new_time, new_date or first_day or ctx.now.date(), ctx)
        if item.rrule and new_date:
            if new_date != first_day:
                return Result("Всю серию на другой день пока не переношу. Могу перенести только ближайшее повторение.")
            new_date = None  # меняется только время, день серии остаётся прежним

        _rename_and_mark(item, intent)
        if moved:
            has_time = self._reschedule(item, new_time, new_date, ctx)
            question = self._check_new_time(item, has_time, ctx, series, occurrence, shift)
            if question:
                return question
        if series:
            return self._save_occurrence(series, item, occurrence, moved, ctx)
        return self._save(item, moved, intent.remind, ctx)

    @staticmethod
    def _requested_moment(intent, ctx):
        """Новое время и дата из фразы; «через 2 часа» считается от текущего момента."""
        if intent.shift_minutes and not intent.new_time:
            moment = (ctx.now + timedelta(minutes=intent.shift_minutes)).replace(second=0, microsecond=0)
            return f"{moment:%H:%M}", moment.date()
        return intent.new_time, intent.new_date

    @staticmethod
    def _ask_missing(intent, ctx, new_time) -> Question | None:
        if new_time and parse_hhmm(new_time) is None:
            return Question("Такого времени нет. Во сколько?", "slot", slot="time")
        if _title_is_question(intent.title, ctx.source_text):
            return Question("Как назвать запись?", "slot", slot="title")
        if intent.remind and not new_time:
            return Question("Во сколько напомнить?", "slot", slot="time")
        return None

    def _remind_about_meeting(self, meeting: Item, new_time: str, day: date, ctx):
        """Встречу не двигаем: напоминание о ней -- отдельная запись."""
        starts_at = datetime.combine(day, parse_hhmm(new_time).replace(tzinfo=None), ctx.now.tzinfo)
        reminder = Item(
            kind=ItemKind.REMINDER, title=meeting.title, user_id=meeting.user_id, due_date=day, starts_at=starts_at
        )
        if "past" not in ctx.confirmed and is_past(reminder, ctx):
            return PAST_Q
        reminder = self.repo.add(reminder)
        text = f"Напомню {format_when(reminder, ctx.now)}: {reminder.title}."
        return Result(text + sync_item(self.calendar, self.repo, reminder), [reminder], [reminder.id])

    @staticmethod
    def _reschedule(item: Item, new_time, new_date, ctx) -> bool:
        """Ставит записи новый день и время (длительность встречи сохраняется). True, если у записи есть время."""
        day = new_date or item.day() or ctx.now.date()
        start_time = parse_hhmm(new_time) if new_time else item.starts_at and item.starts_at.timetz()
        if not start_time:
            item.due_date = day
            return False
        starts_at = datetime.combine(day, start_time.replace(tzinfo=None), ctx.now.tzinfo)
        if not item.rrule:
            item.notified_at = None
        if item.kind == ItemKind.MEETING and item.ends_at and item.starts_at:
            item.ends_at += starts_at - item.starts_at
        item.starts_at, item.due_date = starts_at, day
        return True

    def _check_new_time(self, item: Item, has_time: bool, ctx, series, occurrence, shift) -> Question | None:
        """Новое время в прошлом или занято другой встречей -- спросить подтверждение."""
        if not item.rrule and "past" not in ctx.confirmed and is_past(item, ctx):
            return PAST_Q
        if not has_time or item.kind != ItemKind.MEETING or "conflict" in ctx.confirmed:
            return None
        # у серии проверяется только ближайшее вхождение, остальные даты не сверяются
        overlapping = self.repo.overlapping_meetings(item.user_id, item.starts_at + shift, item.effective_end() + shift)
        clash = [
            other
            for other in overlapping
            if other.id != item.id and not (series and other.id == series.id and other.occurrence == occurrence)
        ]
        return conflict_q(clash[0], "Перенести") if clash else None

    def _save_occurrence(self, series: Item, item: Item, occurrence: date, moved: bool, ctx) -> Result:
        series.exdates.append(occurrence)
        item = self.repo.detach_occurrence(series, item)  # одной транзакцией: при сбое повторение не теряется
        warn = sync_item(self.calendar, self.repo, series) + sync_item(self.calendar, self.repo, item)
        verb = "Перенёс" if moved else "Переименовал"
        return Result(f"{verb} повторение: {item.title}, {format_when(item, ctx.now)}." + warn, [item], [item.id])

    def _save(self, item: Item, moved: bool, remind, ctx) -> Result:
        self.repo.update(item)
        warn = sync_item(self.calendar, self.repo, item)
        when = format_when(item, ctx.now)
        if item.rrule and moved:
            text = f"Перенёс всю серию: {item.title}, {when}."
        elif moved and remind:
            text = f"Напомню {when}: {item.title}."
        elif moved:
            text = f"Перенёс: {item.title}, {when}."
        else:
            text = f"Переименовал: {item.title}."
        return Result(text + warn, [item], [item.id])
