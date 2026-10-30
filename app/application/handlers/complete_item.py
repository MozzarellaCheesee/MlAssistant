from dataclasses import dataclass

from app.application.handlers.calendar import sync_item, unsync_item
from app.application.handlers.target import dm, resolve_scoped
from app.application.intents import CompleteItem
from app.application.ports.outbound import CalendarSync, ItemRepository, SearchIndex
from app.application.results import Question, Result, TurnContext
from app.domain.item import Status


@dataclass
class CompleteItemHandler:
    repo: ItemRepository
    search: SearchIndex
    calendar: CalendarSync | None = None

    def handle(self, intent: CompleteItem, ctx: TurnContext) -> Result | Question:
        resolved = resolve_scoped(intent, ctx, self.repo, self.search)
        if not isinstance(resolved, tuple):
            return resolved
        item, occurrence = resolved
        if item.rrule:
            if intent.scope == "one":
                item.exdates.append(occurrence)
                self.repo.update(item)
                return Result(
                    f"Отметил выполненным повторение {dm(occurrence)}: {item.title}."
                    + sync_item(self.calendar, self.repo, item),
                    [item],
                    [item.id],
                )
            item.status = Status.DONE
            self.repo.update(item)
            return Result(
                f"Отметил выполненной всю серию: {item.title}." + unsync_item(self.calendar, item), [item], [item.id]
            )
        item.status = Status.DONE
        self.repo.update(item)
        return Result(f"Отметил выполненной: {item.title}.", [item], [item.id])
