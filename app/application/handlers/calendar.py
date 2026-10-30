import logging

from app.application.errors import ERRORS, AssistantError, ErrorCode
from app.application.ports.outbound import CalendarSync, ItemRepository
from app.domain.item import Item, ItemKind

_WARN = " " + ERRORS[ErrorCode.CALENDAR_FAILED]
log = logging.getLogger(__name__)


def _warn(error: AssistantError) -> str:
    if error.detail == "no link":  # календарь не подключён: выгружать некуда, это не ошибка
        return ""
    log.warning("calendar sync failed: %s", error.detail)
    return _WARN


def sync_item(cal: CalendarSync | None, repo: ItemRepository, item: Item) -> str:
    """Выгружает встречу/напоминание со временем; ошибка календаря не отменяет действие, возвращает предупреждение."""
    if cal is None or item.kind == ItemKind.TASK or item.starts_at is None:
        return ""
    try:
        uid = cal.push(item)
        if uid != item.external_id:
            item.external_id = uid
            repo.update(item)
    except AssistantError as error:
        return _warn(error)
    return ""


def unsync_item(cal: CalendarSync | None, item: Item) -> str:
    if cal is None or not item.external_id:
        return ""
    try:
        cal.remove(item)
    except AssistantError as error:
        return _warn(error)
    return ""
