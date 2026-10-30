import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.application.intents import Confirm, Intent, Target, parse_intent
from app.application.results import Question

YES = {"да", "ага", "угу", "конечно", "добавь", "давай", "сохрани", "подтверждаю", "верно"}
NO = {"нет", "не надо", "не нужно", "отмена", "отмени", "не"}
_ORD = {
    "1": 0,
    "первую": 0,
    "первый": 0,
    "первое": 0,
    "первая": 0,
    "2": 1,
    "вторую": 1,
    "второй": 1,
    "второе": 1,
    "вторая": 1,
    "3": 2,
    "третью": 2,
    "третий": 2,
    "третье": 2,
    "третья": 2,
    "4": 3,
    "четвертую": 3,
    "четвертый": 3,
    "четвертое": 3,
    "четвертая": 3,
    "5": 4,
    "пятую": 4,
    "пятый": 4,
    "пятое": 4,
    "пятая": 4,
    "последнюю": -1,
    "последний": -1,
}


class Pending(BaseModel):
    intent: dict
    question: str
    awaiting: Literal["slot", "confirm", "choose"]
    slot: Literal["time", "date", "title", "target", "recurrence", "scope"] | None = None
    key: str | None = None
    options: list[int] = []
    confirmed: list[str] = []
    unclear: int = 0
    queue: list[str] = []  # команды той же фразы, отложенные до ответа на этот вопрос
    created_at: datetime | None = None  # ставит хранилище при save


class DialogState(BaseModel):
    pending: Pending | None = None
    last_referenced_ids: list[int] = []
    updated_at: datetime | None = None

    def pending_intent(self) -> Intent | None:
        return parse_intent(self.pending.intent) if self.pending else None

    def ask(self, intent: Intent, question: Question, confirmed: set[str]) -> "DialogState":
        pending = Pending(
            intent=intent.model_dump(mode="json"),
            question=question.text,
            awaiting=question.awaiting,
            slot=question.slot,
            key=question.key,
            options=list(question.options),
            confirmed=sorted(confirmed),
        )
        return self.model_copy(update={"pending": pending})

    def done(self, referenced_ids: list[int]) -> "DialogState":
        return self.model_copy(
            update={"pending": None, "last_referenced_ids": referenced_ids or self.last_referenced_ids}
        )


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[.,!?]", "", text.lower().replace("ё", "е")).split())


_NUM = re.compile(
    r"\d|\b(ноль|один|одна|два|две|три|четыре|пять|шесть|семь|восемь|девять|десять|одиннадцать|двенадцать|"
    r"тринадцать|четырнадцать|пятнадцать|шестнадцать|семнадцать|восемнадцать|девятнадцать|двадцать|тридцать|"
    r"сорок|пятьдесят|шестьдесят)\b"
)


def is_number(text: str) -> bool:
    return bool(_NUM.search(_norm(text)))


def resolve_reply(state: DialogState, text: str, now: datetime) -> Intent | None:
    """Без LLM только точные короткие ответы: «да»/«нет» на подтверждение и номер на выбор.
    Всё остальное («да нет», «не знаю», название, «только эту») разбирает модель с черновиком в контексте."""
    pending = state.pending
    if pending is None:
        return None
    reply = _norm(text)
    if pending.awaiting == "confirm":
        if reply in YES:
            return Confirm(answer="yes")
        if reply in NO:
            return Confirm(answer="no")
        return None
    if pending.awaiting == "choose":
        index = _ORD.get(reply)
        if index is not None and -len(pending.options) <= index < len(pending.options):
            return parse_intent(pending.intent).model_copy(update={"target": Target(id=pending.options[index])})
    return None
