import json
import logging
import re
import time
from datetime import date, timedelta

from gigachat.models import Chat, Messages, MessagesRole
from pydantic import ValidationError

from app.adapters.outbound.gigachat.client import chat_retry
from app.adapters.outbound.gigachat.prompts import SDK_FUNCTIONS, SPLIT, SYSTEM_PROMPT
from app.application.errors import AssistantError
from app.application.intents import Several, Unknown, parse_intent
from app.application.ports.outbound import IntentParser
from app.application.render import format_when
from app.domain.dates import parse_date_expr, parse_period_expr

log = logging.getLogger(__name__)
PARSE_ATTEMPTS = 2
WEEKDAYS_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def _pending_view(pending):
    view = {k: v for k, v in pending.items() if v is not None}
    if view.get("intent") in ("create_task", "create_meeting"):  # модель видит единый create_item
        view["kind"] = "meeting" if view["intent"] == "create_meeting" else view.get("kind", "task")
        view["intent"] = "create_item"
    return view


def _dsl(args: dict, today: date) -> None:
    """Даты модель отдаёт кодом (+1m, fri, 12-15), в ISO их переводит код.

    Непонятное отбрасываем -- ассистент переспросит."""
    for key in ("date", "new_date"):
        if args.get(key):
            parsed = parse_date_expr(str(args.pop(key)), today)
            if parsed:
                args[key] = parsed.isoformat()
    if period := args.pop("period", None):
        bounds = parse_period_expr(str(period), today)
        if bounds:
            args["date_from"], args["date_to"] = bounds[0].isoformat(), bounds[1].isoformat()


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w:]+", " ", text.lower().replace("ё", "е")).split())


def _relative(args: dict, last) -> None:
    """«в это же время», «за час до неё», «на следующий день»: модель отмечает смысл,
    дату и время считает код от последней записи."""
    same_day, same_time = args.pop("same_day", None), args.pop("same_time", None)
    minutes, days = args.pop("offset_minutes", None) or 0, args.pop("offset_days", None) or 0
    if last is None or not (same_day or same_time or minutes or days):
        return
    args.pop("shift_minutes", None)
    if (same_time or minutes) and last.starts_at:
        moment = last.starts_at + timedelta(days=days, minutes=minutes)
        args["date"], args["time"] = moment.date().isoformat(), f"{moment:%H:%M}"
    elif last.day():
        args["date"] = (last.day() + timedelta(days=days)).isoformat()


def _hour_said(hhmm: str, said: str) -> bool:
    hour = hhmm.split(":")[0]
    if not hour.isdigit():
        return False
    return bool({str(int(hour)), str(int(hour) % 12 or 12)} & set(re.findall(r"\d+", said)))


def _ground(text: str, args: dict, recent=()) -> None:
    """Время и дату, которых нет во фразе, модель выдумала (полночь вместо «не названо») -- отбрасываем,
    ассистент переспросит. Значение из последних записей («в это же время») тоже считается названным."""
    said = _norm(text)
    known = {
        "time": {f"{i.starts_at:%H:%M}" for i in recent if i.starts_at},
        "date": {i.day().isoformat() for i in recent if i.day()},
    }
    for field, quote in (("time", "time_said"), ("date", "date_said")):
        if field == "date" and args.get("rrule"):  # у серии дата -- первое повторение, её во фразе может не быть
            args.pop(quote, None)
            continue
        quoted = _norm(args.pop(quote, None) or "")
        value = str(args.get(field) or "").zfill(5) if field == "time" else args.get(field)
        # модель не процитировала время, но час числом во фразе есть
        if field == "time" and not quoted and _hour_said(value, said):
            continue
        if args.get(field) and (not quoted or quoted not in said) and value not in known[field]:
            args.pop(field)


def _create_intent_name(args: dict, text: str, ctx) -> str:
    """create_item модели -- это create_task или create_meeting приложения; дата и время проверяются по фразе."""
    if ctx.pending:  # ответ на вопрос ассистента: время и дату пользователь называет сам
        args.pop("time_said", None)
        args.pop("date_said", None)
    else:
        _ground(text, args, ctx.recent)
    _relative(args, ctx.recent[0] if ctx.recent else None)
    kind = args.pop("kind", None) or "task"
    if kind == "meeting":
        return "create_meeting"
    args.pop("duration_minutes", None)
    args["kind"] = kind
    return "create_task"


def _error_message(call, error: str) -> Messages:
    """Ошибка разбора для второй попытки: ответом на вызов функции либо репликой пользователя."""
    fix = f"Ошибка: {error}. Вызови одну из функций с корректными аргументами."
    if call:
        return Messages(
            role=MessagesRole.FUNCTION, name=call.name, content=json.dumps({"error": fix}, ensure_ascii=False)
        )
    return Messages(role=MessagesRole.USER, content=fix)


class GigaChatIntentParser(IntentParser):
    def __init__(self, client, sleep=time.sleep, split_client=None):
        # деление фразы -- лёгкая модель: 0,3 с против 0,7 с у Pro при той же точности на наборе фраз
        self.client = client
        self.sleep = sleep
        self.split_client = split_client or client

    def _split(self, text: str, ctx) -> list[str]:
        recent = "; ".join(f"{i.title} ({format_when(i, ctx.now)})" for i in ctx.recent) or "нет"
        msgs = [Messages(role=MessagesRole.USER, content=text)]
        if ctx.recent:  # чтобы «в это время», «её» стали конкретными
            msgs.append(Messages(role=MessagesRole.USER, content=f"(Последние записи: {recent})"))
        try:
            function_call = (
                chat_retry(
                    self.split_client,
                    Chat(messages=msgs, functions=[SPLIT], function_call={"name": SPLIT.name}, temperature=0.1),
                    self.sleep,
                )
                .choices[0]
                .message.function_call
            )
            commands = (function_call.arguments or {}).get("commands") if function_call else None
        except AssistantError:
            raise
        except Exception as e:
            log.warning("split failed: %s", type(e).__name__)
            return [text]
        return [c for c in commands if isinstance(c, str) and c.strip()] if isinstance(commands, list) else [text]

    def parse(self, text, ctx):
        if not ctx.split or ctx.pending:
            return self._parse(text, ctx)
        # «купи хлеб, потом удали встречу…»: сначала короткое деление, потом разбор. Не параллельно: личный тариф
        # GigaChat отвечает 429 на второй одновременный запрос, и ход только дольше
        commands = self._split(text, ctx)
        return Several(commands=commands) if len(commands) > 1 else self._parse(text, ctx)

    def _parse(self, text, ctx):
        """Одна команда: модель вызывает функцию; при ошибке -- ещё одна попытка с текстом ошибки."""
        messages = [
            Messages(role=MessagesRole.SYSTEM, content=self._system_prompt(ctx)),
            Messages(role=MessagesRole.USER, content=text),
        ]
        for _ in range(PARSE_ATTEMPTS):
            message = (
                chat_retry(
                    self.client,
                    Chat(messages=messages, functions=SDK_FUNCTIONS, function_call="auto", temperature=0.1),
                    self.sleep,
                )
                .choices[0]
                .message
            )
            call = message.function_call
            intent, error = self._to_intent(call, text, ctx)
            if intent is not None:
                return intent
            messages += [message, _error_message(call, error)]
        return Unknown()

    @staticmethod
    def _system_prompt(ctx) -> str:
        now = f"{ctx.now.isoformat(timespec='minutes')}, {WEEKDAYS_RU[ctx.now.weekday()]}"
        recent = (
            "\n".join(
                json.dumps(
                    {"id": i.id, "kind": i.kind.value, "title": i.title, "when": format_when(i, ctx.now)},
                    ensure_ascii=False,
                )
                for i in ctx.recent
            )
            or "нет"
        )
        pending = "нет"
        if ctx.pending:
            pending = json.dumps(
                {"question": ctx.pending_question, "intent": _pending_view(ctx.pending)}, ensure_ascii=False
            )
        return SYSTEM_PROMPT.format(now=now, pending=pending, recent=recent)

    @staticmethod
    def _to_intent(call, text, ctx):
        """(намерение, None) или (None, текст ошибки для повторной попытки)."""
        if not call:
            return None, "нет вызова функции"
        args = dict(call.arguments or {})
        log.info("llm %s %s", call.name, sorted(args))  # без значений: названия и запросы -- слова пользователя
        log.debug("llm args %s", args)
        _dsl(args, ctx.now.date())
        if not any(v not in (None, "") for v in args.values()):
            return None, "все аргументы пустые"
        ref, query = args.pop("target_ref", None), args.pop("target_query", None)
        if ref or query:  # названы слова -- ищем по ним, «последняя запись» лишняя
            args["target"] = {"ref": None if query else ref, "query": query}
        name = call.name
        if name == "create_item":
            name = _create_intent_name(args, text, ctx)
        try:
            return parse_intent({"intent": name, **args}), None
        except ValidationError as e:
            return None, str(e)
