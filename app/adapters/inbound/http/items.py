"""Записи: календарь страницы, форма создания и правки, напоминания к показу."""

from collections.abc import Callable
from datetime import date
from datetime import date as Date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.adapters.inbound.http.common import item_to_dict
from app.application.ports.inbound import AssistantPort
from app.domain.dates import DateRange
from app.domain.item import ItemKind, Status

TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"
MAX_PERIOD_DAYS = 366
MAX_DURATION_MINUTES = 24 * 60


class ItemIn(BaseModel):
    kind: ItemKind
    title: str = Field(min_length=1, max_length=200)
    date: Date
    time: str | None = Field(None, pattern=TIME_PATTERN)
    duration_minutes: int | None = Field(None, gt=0, le=MAX_DURATION_MINUTES)


class ItemPatch(BaseModel):
    kind: ItemKind | None = None
    title: str | None = Field(None, min_length=1, max_length=200)
    date: Date | None = None
    time: str | None = Field(None, pattern=TIME_PATTERN)
    duration_minutes: int | None = Field(None, gt=0, le=MAX_DURATION_MINUTES)


def items_router(service: AssistantPort, current_user: Callable, voice) -> APIRouter:
    router = APIRouter()

    @router.get("/api/items")
    def items(
        from_: date = Query(alias="from"),
        to: date = Query(),
        kind: ItemKind | None = None,
        status: Status | None = None,
        user: str = Depends(current_user),
    ):
        if not 0 <= (to - from_).days <= MAX_PERIOD_DAYS:
            return JSONResponse(
                {"reply": "Слишком большой период. Выберите не больше года.", "error": "bad_request"}, 422
            )
        return [item_to_dict(i) for i in service.list_items(user, DateRange(from_, to), kind, status)]

    @router.post("/api/items", status_code=201)
    def item_create(body: ItemIn, user: str = Depends(current_user)):
        item = service.create_from_form(user, body.kind, body.title, body.date, body.time, body.duration_minutes)
        return item_to_dict(item)

    @router.patch("/api/items/{item_id}")
    def item_update(item_id: int, body: ItemPatch, user: str = Depends(current_user)):
        sent = body.model_fields_set  # не переданное поле остаётся как есть (...), переданный null снимает
        item = service.update_from_form(
            user,
            item_id,
            body.kind,
            body.title,
            body.date,
            body.time if "time" in sent else ...,
            body.duration_minutes if "duration_minutes" in sent else ...,
        )
        return item_to_dict(item)

    @router.delete("/api/items/{item_id}", status_code=204)
    def item_delete(item_id: int, user: str = Depends(current_user)):
        service.delete_item(user, item_id)

    @router.post("/api/reminders/due")
    def reminders_due(user: str = Depends(current_user)):
        if voice and user == voice.owner and voice.status()["enabled"]:
            return []  # Джарвис произнесёт сам
        return [item_to_dict(i) for i in service.due_reminders(user)]

    return router
