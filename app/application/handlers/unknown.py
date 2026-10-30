from app.application.intents import Confirm, Unknown
from app.application.results import Result, TurnContext


class UnknownHandler:
    def handle(self, intent: Unknown, ctx: TurnContext) -> Result:
        return Result(
            "Я умею создавать задачи, напоминания и встречи, показывать планы и искать записи. "
            "Например: «Напомни завтра в 10 позвонить Ивану»."
        )


class ConfirmHandler:
    def handle(self, intent: Confirm, ctx: TurnContext) -> Result:
        return Result("Хорошо.")
