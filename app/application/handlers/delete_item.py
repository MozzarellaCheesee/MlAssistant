from dataclasses import dataclass

from app.application.handlers.calendar import sync_item, unsync_item
from app.application.handlers.target import dm, resolve_scoped
from app.application.intents import DeleteItem
from app.application.ports.outbound import CalendarSync, ItemRepository, SearchIndex
from app.application.results import Question, Result, TurnContext
from app.domain.item import Status


@dataclass
class DeleteItemHandler:
    repo: ItemRepository
    search: SearchIndex
    calendar: CalendarSync | None = None

    def handle(self, intent: DeleteItem, ctx: TurnContext) -> Result | Question:
        resolved = resolve_scoped(intent, ctx, self.repo, self.search)
        if not isinstance(resolved, tuple):
            return resolved
        item, occurrence = resolved
        if item.rrule:
            if intent.scope == "one":
                item.exdates.append(occurrence)
                self.repo.update(item)
                return Result(
                    f"Удалил повторение {dm(occurrence)}: {item.title}." + sync_item(self.calendar, self.repo, item),
                    [],
                    [],
                )
            item.status = Status.CANCELLED
            self.repo.update(item)
            return Result(f"Удалил всю серию: {item.title}." + unsync_item(self.calendar, item), [], [])
        item.status = Status.CANCELLED
        self.repo.update(item)
        return Result(
            f"Удалил: {item.title}." + unsync_item(self.calendar, item), [], [item.id]
        )  # день нужен следующей команде цепочки
