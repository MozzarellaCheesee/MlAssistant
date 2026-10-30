from dataclasses import dataclass
from datetime import timedelta

from app.application.intents import ListItems
from app.application.ports.outbound import ItemRepository
from app.application.render import format_list
from app.application.results import Result, TurnContext
from app.domain.dates import DateRange
from app.domain.item import ItemKind, Status

MAX_PERIOD_DAYS = 366


@dataclass
class ListItemsHandler:
    repo: ItemRepository

    def handle(self, intent: ListItems, ctx: TurnContext) -> Result:
        start = intent.date_from or ctx.now.date()
        rng = DateRange(start, intent.date_to or start)
        if (rng.end - rng.start).days > MAX_PERIOD_DAYS:
            return Result("Слишком большой период. Скажите, например, «на этой неделе» или «на месяц».")
        kind = ItemKind(intent.kind) if intent.kind else None
        items = self.repo.list_between(ctx.user_id, rng, kind, Status.OPEN)
        if not items:
            today = ctx.now.date()
            one = rng.start if rng.start == rng.end else None
            if one == today:
                return Result("На сегодня ничего не запланировано.")
            if one == today + timedelta(days=1):
                return Result("На завтра ничего не запланировано.")
            return Result("На этот период ничего не запланировано.")
        return Result(format_list(items, ctx.now), items, list(dict.fromkeys(i.id for i in items)))
