from typing import Any, Protocol

from app.application.results import Question, Result, TurnContext


class Handler(Protocol):
    def handle(self, intent: Any, ctx: TurnContext) -> Result | Question: ...  # у каждого обработчика свой тип интента
