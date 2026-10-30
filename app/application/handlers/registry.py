from app.application.handlers import Handler
from app.application.handlers.complete_item import CompleteItemHandler
from app.application.handlers.create_meeting import CreateMeetingHandler
from app.application.handlers.create_task import CreateTaskHandler
from app.application.handlers.delete_item import DeleteItemHandler
from app.application.handlers.list_items import ListItemsHandler
from app.application.handlers.search_items import SearchItemsHandler
from app.application.handlers.unknown import ConfirmHandler, UnknownHandler
from app.application.handlers.update_item import UpdateItemHandler
from app.application.intents import (
    CompleteItem,
    Confirm,
    CreateMeeting,
    CreateTask,
    DeleteItem,
    ListItems,
    SearchItems,
    Unknown,
    UpdateItem,
)
from app.application.ports.outbound import CalendarSync, ItemRepository, SearchIndex


def build_handlers(
    repo: ItemRepository, search: SearchIndex, calendar: CalendarSync | None = None
) -> dict[type, Handler]:
    """Интент -> обработчик. Один реестр для приложения и тестов; Several разбирает сам AssistantService."""
    return {
        CreateTask: CreateTaskHandler(repo, calendar),
        CreateMeeting: CreateMeetingHandler(repo, calendar),
        ListItems: ListItemsHandler(repo),
        SearchItems: SearchItemsHandler(search),
        CompleteItem: CompleteItemHandler(repo, search, calendar),
        UpdateItem: UpdateItemHandler(repo, search, calendar),
        DeleteItem: DeleteItemHandler(repo, search, calendar),
        Confirm: ConfirmHandler(),
        Unknown: UnknownHandler(),
    }
