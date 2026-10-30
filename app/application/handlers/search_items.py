from dataclasses import dataclass
from datetime import date

from app.application.intents import SearchItems
from app.application.ports.outbound import SearchFilters, SearchIndex
from app.application.render import format_list, format_when, plural
from app.application.results import Result, TurnContext
from app.domain.item import ItemKind

SEARCH_LIMIT = 5


def _day_or_min(item) -> date:
    return item.day() or date.min


@dataclass
class SearchItemsHandler:
    search: SearchIndex

    def handle(self, intent: SearchItems, ctx: TurnContext) -> Result:
        kind = ItemKind(intent.kind) if intent.kind else None
        items = self.search.search(
            ctx.user_id,
            intent.query,
            SearchFilters(ctx.now.date(), kind, intent.tense, intent.date_from, intent.date_to),
            SEARCH_LIMIT,
        )
        if not items:
            return Result("Ничего не нашёл. Попробуйте сказать иначе.")
        if intent.tense == "past":
            items.sort(key=_day_or_min, reverse=True)
            first, others = items[0], len(items) - 1
            text = f"Последний раз: {format_when(first, ctx.now)} — {first.title}."
            if others:
                text += f" Ещё {others} {plural(others, 'запись', 'записи', 'записей')}."
        else:
            text = f"Нашёл {format_list(items, ctx.now)}"
        return Result(text, items, [i.id for i in items])
