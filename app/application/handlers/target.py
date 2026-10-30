from datetime import date, timedelta

from app.application.intents import CreateTask
from app.application.ports.outbound import ItemRepository, SearchFilters, SearchIndex
from app.application.render import format_choice
from app.application.results import Question, Result, TurnContext
from app.domain.dates import MONTHS_GEN
from app.domain.item import Item, ItemKind, Status
from app.domain.recurrence import describe, occurrences

CHOICE_LIMIT = 5  # сколько вариантов предлагаем на выбор и ищем
NOT_FOUND = Result("Не нашёл такую запись.")


def _pick(items: list[Item], ctx: TurnContext) -> Item | Question:
    if len(items) == 1:
        return items[0]
    items = items[:CHOICE_LIMIT]
    return Question(format_choice(items, ctx.now), "choose", options=[i.id for i in items], items=items)


def resolve_target(intent, ctx: TurnContext, repo: ItemRepository, search: SearchIndex) -> Item | Question | Result:
    target = intent.target
    if target.id is not None:
        item = repo.get(ctx.user_id, target.id)
        return item if item and item.status != Status.CANCELLED else NOT_FOUND
    if target.ref == "last":
        items = [
            i
            for i in (repo.get(ctx.user_id, x) for x in dict.fromkeys(ctx.last_ids))
            if i and i.status != Status.CANCELLED
        ]
        return _pick(items, ctx) if items else Question("О какой записи речь?", "slot", slot="target")
    items = search.search(
        ctx.user_id,
        target.query,
        SearchFilters(ctx.now.date(), ItemKind(intent.kind) if intent.kind else None, "any", intent.date, intent.date),
        CHOICE_LIMIT,
    )
    if not items:
        if intent.intent in ("delete_item", "complete_item"):  # похоже на новое дело, сказанное как команда
            kind = "reminder" if intent.kind == "reminder" else "task"
            return Question(
                f"Такой записи нет. Создать {'напоминание' if kind == 'reminder' else 'задачу'} «{target.query}»?",
                "confirm",
                key="create",
                intent=CreateTask(title=target.query, kind=kind),
            )
        return Result(f"Ничего не нашёл по запросу «{target.query}».")
    return _pick(items, ctx)


def series_scope(item: Item, scope: str | None, ctx: TurnContext) -> date | Question | Result | None:
    """Дата ближайшего повторения серии, None (= вся серия) либо вопрос/отказ, который нужно вернуть как есть."""
    near = occurrences(item.rrule, item.day(), ctx.now.date(), ctx.now.date() + timedelta(days=400), item.exdates)
    if not near:
        return None if scope != "one" else Result("У этой серии больше нет повторений.")
    if scope is None:
        nearest = near[0]
        return Question(
            f"Это повторяющаяся запись ({describe(item.rrule)}). "
            f"Изменить только ближайшее повторение ({dm(nearest)}) или все?",
            "slot",
            slot="scope",
        )
    # ближайшим может оказаться уже прошедшее сегодняшнее повторение;
    # дату повторения можно брать из прошлого списка
    return near[0]


def resolve_scoped(
    intent, ctx: TurnContext, repo: ItemRepository, search: SearchIndex
) -> tuple[Item, date | None] | Question | Result:
    """Цель + дата повторения серии (None для одиночной записи) либо вопрос/отказ, который нужно вернуть как есть."""
    item = resolve_target(intent, ctx, repo, search)
    if not hasattr(item, "kind"):
        return item
    occurrence = series_scope(item, intent.scope, ctx) if item.rrule else None
    if occurrence is not None and not isinstance(occurrence, date):
        return occurrence
    return item, occurrence


def dm(day: date) -> str:
    return f"{day.day} {MONTHS_GEN[day.month - 1]}"
