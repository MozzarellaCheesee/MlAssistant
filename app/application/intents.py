from datetime import date
from datetime import date as Date
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter, field_validator, model_validator

from app.domain.recurrence import validate_rrule


class _Base(BaseModel):
    # model-level, а не field_validator("*"): pydantic запрещает before-валидаторы на поле-дискриминаторе
    @model_validator(mode="before")
    @classmethod
    def _blank_to_none(cls, data):
        if isinstance(data, dict):
            return {
                key: None if key != "intent" and isinstance(value, str) and not value.strip() else value
                for key, value in data.items()
            }
        return data


class Target(_Base):
    ref: Literal["last"] | None = None
    query: str | None = None
    id: int | None = None

    @model_validator(mode="after")
    def _exactly_one(self):
        if sum(v is not None for v in (self.ref, self.query, self.id)) != 1:
            raise ValueError("exactly one of ref, query, id required")
        return self


class CreateTask(_Base):
    intent: Literal["create_task"] = "create_task"
    kind: Literal["task", "reminder"] = "task"
    title: str | None = None
    date: Date | None = None
    time: str | None = None  # как сказано, например "24:30"; проверяет handler
    rrule: str | None = None
    shift_minutes: int | None = Field(None, gt=0)

    @field_validator("rrule")
    @classmethod
    def _check_rrule(cls, value):
        return validate_rrule(value)


class CreateMeeting(_Base):
    intent: Literal["create_meeting"] = "create_meeting"
    title: str | None = None
    date: Date | None = None
    time: str | None = None
    duration_minutes: int | None = Field(None, gt=0)
    rrule: str | None = None
    shift_minutes: int | None = Field(None, gt=0)

    @field_validator("rrule")
    @classmethod
    def _check_rrule(cls, value):
        return validate_rrule(value)


class ListItems(_Base):
    intent: Literal["list_items"] = "list_items"
    date_from: date | None = None  # None -> сегодня, подставляет handler
    date_to: date | None = None
    kind: Literal["task", "meeting", "reminder"] | None = None


class SearchItems(_Base):
    intent: Literal["search_items"] = "search_items"
    query: str
    kind: Literal["task", "meeting", "reminder"] | None = None
    tense: Literal["past", "future", "any"] = "any"
    date_from: date | None = None
    date_to: date | None = None


class CompleteItem(_Base):
    intent: Literal["complete_item"] = "complete_item"
    target: Target
    date: Date | None = None  # сужает поиск цели
    kind: Literal["task", "meeting", "reminder"] | None = None
    scope: Literal["one", "all"] | None = None


class UpdateItem(_Base):
    intent: Literal["update_item"] = "update_item"
    target: Target
    date: Date | None = None  # сужает поиск цели
    kind: Literal["task", "meeting", "reminder"] | None = None
    title: str | None = None
    new_time: str | None = None  # как сказано; проверяет handler
    new_date: Date | None = None
    shift_minutes: int | None = Field(None, gt=0)
    scope: Literal["one", "all"] | None = None
    remind: bool | None = None  # «эту же задачу напомни в 15»: время ставится самой записи
    delay_minutes: int | None = None  # «на час позже» 60, «на полчаса раньше» -30: от времени самой записи
    delay_days: int | None = None  # «на день позже», «на следующий день» 1: от дня самой записи
    delay_months: int | None = None  # «на месяц позже» 1: от дня самой записи


class DeleteItem(_Base):
    intent: Literal["delete_item"] = "delete_item"
    target: Target
    date: Date | None = None  # сужает поиск цели
    kind: Literal["task", "meeting", "reminder"] | None = None
    scope: Literal["one", "all"] | None = None


class Confirm(_Base):
    intent: Literal["confirm"] = "confirm"
    answer: Literal["yes", "no"]


class Unknown(_Base):
    intent: Literal["unknown"] = "unknown"


class Several(_Base):
    """Фраза с несколькими командами: каждая разбирается и выполняется отдельно, по порядку."""

    intent: Literal["several"] = "several"
    commands: list[str] = Field(min_length=2)


Intent = Annotated[
    CreateTask
    | CreateMeeting
    | ListItems
    | SearchItems
    | CompleteItem
    | UpdateItem
    | DeleteItem
    | Confirm
    | Unknown
    | Several,
    Field(discriminator="intent"),
]
_adapter = TypeAdapter(Intent)


def parse_intent(data: dict) -> Intent:
    return _adapter.validate_python(data)
