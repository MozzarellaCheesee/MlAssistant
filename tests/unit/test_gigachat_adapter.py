import re
import os
import shutil
import ssl
import subprocess
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from dotenv import load_dotenv
from gigachat.exceptions import ResponseError, ServerError
from gigachat.models import ChatCompletion

from app.adapters.outbound.gigachat.client import API_URL, build_ssl_context, make_client
from app.adapters.outbound.gigachat.intent_parser import GigaChatIntentParser
from app.application.errors import AssistantError, ErrorCode
from app.application.intents import CreateTask, Target, Unknown, UpdateItem
from app.application.ports.outbound import ParseContext

CTX = ParseContext(now=datetime(2026, 10, 28, 12, 0, tzinfo=ZoneInfo("Europe/Moscow")), pending=None, recent=[], split=False)
URL = "https://x"


def completion(content="", function_call=None):
    msg = {"role": "assistant", "content": content}
    if function_call:
        msg["function_call"] = function_call
    return ChatCompletion.model_validate({"choices": [{"message": msg, "index": 0, "finish_reason": "stop"}],
                                          "created": 1, "model": "m", "object": "chat.completion",
                                          "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})


class Fake:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), 0

    def chat(self, payload):
        self.calls += 1
        self.last = payload
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def fc(name, arguments):
    return completion(function_call={"name": name, "arguments": arguments})


def test_ssl_context_is_pinned(tmp_path):
    if not shutil.which("openssl"):
        pytest.skip("no openssl")
    pem = tmp_path / "c.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=t", "-days", "1",
                    "-keyout", str(tmp_path / "k.pem"), "-out", str(pem)], check=True, capture_output=True)
    ctx = build_ssl_context(str(pem))
    assert ctx.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN
    assert ctx.check_hostname
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert len(ctx.get_ca_certs()) == 1


def test_function_call_to_intent():
    p = GigaChatIntentParser(Fake(fc("create_item", {"kind": "reminder", "title": "позвонить Ивану",
                                                      "date": "2026-10-29", "time": "10:00",
                                                      "date_said": "завтра", "time_said": "в 10"})))
    assert p.parse("напомни завтра в 10 позвонить Ивану", CTX) == CreateTask(kind="reminder", title="позвонить Ивану", date=date(2026, 10, 29), time="10:00")


def test_target_flattening():
    p = GigaChatIntentParser(Fake(fc("update_item", {"target_ref": "last", "new_time": "16:00"})))
    assert p.parse("перенеси её на 16", CTX) == UpdateItem(target=Target(ref="last"), new_time="16:00")


def test_invalid_then_retry_then_unknown():
    f = Fake(completion("?"), completion("?"))
    assert GigaChatIntentParser(f).parse("x", CTX) == Unknown()
    assert f.calls == 2


def test_quota_error_maps():
    with pytest.raises(AssistantError) as e:
        GigaChatIntentParser(Fake(ResponseError(URL, 402, b"", None))).parse("x", CTX)
    assert e.value.code == ErrorCode.LLM_QUOTA


def test_server_error_maps():
    with pytest.raises(AssistantError) as e:
        GigaChatIntentParser(Fake(ServerError(URL, 500, b"", None))).parse("x", CTX)
    assert e.value.code == ErrorCode.LLM_UNAVAILABLE


@pytest.mark.llm
def test_live_ping():
    load_dotenv()  # ключ из .env, как у сервера
    cred = os.getenv("GIGACHAT_CREDENTIALS")
    if not cred:
        pytest.skip("GIGACHAT_CREDENTIALS not set")
    c = make_client(cred, os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"), "GigaChat", "certs/gigachat.pem")
    names = [m.id_ for m in c.get_models().data]
    print(names)
    assert names


def test_retry_after_function_call_uses_function_role():
    f = Fake(fc("create_item", {"kind": "bogus"}), completion("?"))
    GigaChatIntentParser(f).parse("x", CTX)
    last = f.last.messages[-1]
    assert last.role == "function" and last.name == "create_item" and "error" in last.content


def test_retry_without_function_call_uses_user_role():
    f = Fake(completion("?"), completion("?"))
    GigaChatIntentParser(f).parse("x", CTX)
    assert f.last.messages[-1].role == "user"


def test_prompt_has_pending_question():
    f = Fake(completion("?"), completion("?"))
    ctx = ParseContext(CTX.now, {"intent": "create_meeting"}, [], "На какое время?")
    GigaChatIntentParser(f).parse("x", ctx)
    assert "На какое время?" in f.last.messages[0].content


def test_required_params():
    from app.adapters.outbound.gigachat.prompts import FUNCTIONS
    req = {f["name"]: f["parameters"].get("required") for f in FUNCTIONS}
    assert req["search_items"] == ["query"] and req["confirm"] == ["answer"] and req["delete_item"] is None


@pytest.mark.parametrize("var", ["GIGACHAT_CA_BUNDLE_FILE", "GIGACHAT_VERIFY_SSL_CERTS", "GIGACHAT_BASE_URL",
                                 "GIGACHAT_AUTH_URL"])
def test_make_client_rejects_overriding_env(monkeypatch, var):
    monkeypatch.setenv(var, "x")
    with pytest.raises(RuntimeError, match=var):
        make_client("c", "s", "m", "unused.pem")


def test_recurrence_functions_and_prompt():
    from app.adapters.outbound.gigachat.prompts import FUNCTIONS, SYSTEM_PROMPT
    f = {x["name"]: x["parameters"]["properties"] for x in FUNCTIONS}
    assert "rrule" in f["create_item"] and {"date", "time", "duration_minutes"} <= f["create_item"].keys()
    assert "create_task" not in f and "create_meeting" not in f
    assert "period" in f["list_items"] and "period" in f["search_items"] and "date_from" not in f["list_items"]
    assert not [k for p in f.values() for k in p if k.endswith("_expr")]
    assert f["delete_item"]["scope"]["enum"] == ["one", "all"]
    assert "scope" in f["complete_item"] and "scope" in f["update_item"]


def test_prompt_has_now_and_weekday():
    f = Fake(completion("?"), completion("?"))
    GigaChatIntentParser(f).parse("x", CTX)
    assert "2026-10-28T12:00+03:00, среда" in f.last.messages[0].content


def test_bad_date_expr_is_dropped():
    f = Fake(fc("create_item", {"kind": "task", "title": "отчёт", "date": "завтра", "date_said": "завтра"}))
    assert GigaChatIntentParser(f).parse("завтра отчёт", CTX).date is None


def test_make_client_uses_pinned_api_host():
    c = make_client("c", "s", "m", "certs/gigachat.pem")
    assert c._settings.base_url == API_URL


def test_429_then_ok_retries():
    f, waits = Fake(ResponseError(URL, 429, b"", None), completion("?"), completion("?")), []
    assert GigaChatIntentParser(f, sleep=waits.append).parse("x", CTX) == Unknown()
    assert waits == [1]


def test_429_three_times_rate_limit():
    f, waits = Fake(*[ResponseError(URL, 429, b"", None)] * 3), []
    with pytest.raises(AssistantError) as e:
        GigaChatIntentParser(f, sleep=waits.append).parse("x", CTX)
    assert e.value.code == ErrorCode.LLM_RATE_LIMIT
    assert waits == [1, 2] and f.calls == 3


def test_prompt_task_vs_meeting_rule():
    from app.adapters.outbound.gigachat.prompts import FUNCTIONS, SYSTEM_PROMPT
    assert "title" in FUNCTIONS[0]["parameters"]["properties"] and "встреча/созвон" in str(FUNCTIONS[0])
    ex = FUNCTIONS[0]["few_shot_examples"]
    assert any(e["params"].get("title") == "купить билеты в театр" and e["params"]["date"] == "eow" for e in ex)
    assert "new_time" in FUNCTIONS[4]["parameters"]["properties"]


def test_empty_function_call_retries():
    f = Fake(fc("create_item", {}), fc("create_item", {"kind": "task", "title": "купить билеты", "date": "eow"}))
    assert GigaChatIntentParser(f).parse("x", CTX).title == "купить билеты"
    assert f.calls == 2
    f = Fake(fc("create_item", {"title": ""}), fc("create_item", {}))
    assert GigaChatIntentParser(f).parse("x", CTX) == Unknown()


def test_shift_and_duration_in_schema_and_prompt():
    from app.adapters.outbound.gigachat.prompts import FUNCTIONS, SYSTEM_PROMPT
    p = {f["name"]: f["parameters"]["properties"] for f in FUNCTIONS}
    assert all("shift_minutes" in p[n] for n in ("create_item", "update_item"))
    assert "duration_minutes" in p["create_item"] and "ends_at" not in p["create_item"]


def test_prompt_has_no_calendar_but_dsl_in_schema():
    from app.adapters.outbound.gigachat.prompts import FUNCTIONS
    f = Fake(completion("?"), completion("?"))
    GigaChatIntentParser(f).parse("x", CTX)
    assert "Календарь" not in f.last.messages[0].content
    assert "конец недели = воскресенье" in str(FUNCTIONS) and "+1m" in str(FUNCTIONS)


def test_pending_draft_skips_guards():
    draft = {"intent": "create_meeting", "title": "встреча", "date": "2026-10-29", "time": "29:00"}
    ctx = ParseContext(CTX.now, draft, [], "Во сколько?")
    f = Fake(fc("create_item", {"kind": "meeting", "time": "11:00", "time_said": "в 11"}))
    r = GigaChatIntentParser(f).parse("в 11", ctx)
    assert r.intent == "create_meeting" and r.time == "11:00"
    f = Fake(fc("create_item", {"kind": "task", "time": "11:00"}))
    r = GigaChatIntentParser(f).parse("напомни в 11", ParseContext(CTX.now, draft, [], "?"))
    assert r.intent == "create_task" and r.kind == "task"


def test_pending_prompt_omits_null_fields():
    draft = {"intent": "create_meeting", "title": "встреча", "date": "2026-10-29", "time": None, "rrule": None}
    f = Fake(fc("create_item", {"kind": "meeting", "time": "17:00"}))
    GigaChatIntentParser(f).parse("в 17", ParseContext(CTX.now, draft, [], "На какое время?"))
    sys_prompt = f.last.messages[0].content
    assert "null" not in sys_prompt.split("Ожидается ответ на вопрос:")[1].split("Последние")[0]


def test_ubery_is_a_delete_not_a_create():
    f = Fake(fc("delete_item", {"target_query": "Иннокентий", "kind": "meeting", "date": "2026-10-29"}))
    i = GigaChatIntentParser(f).parse("встречу завтра убери с иннокентием", CTX)
    assert i.target.query == "Иннокентий" and i.date == date(2026, 10, 29) and f.calls == 1


def test_create_item_kind_mapping():
    from app.application.intents import CreateMeeting
    r = GigaChatIntentParser(Fake(fc("create_item", {"kind": "meeting", "title": "x", "duration_minutes": 60}))).parse("x", CTX)
    assert r == CreateMeeting(title="x", duration_minutes=60)
    r = GigaChatIntentParser(Fake(fc("create_item", {"kind": "task", "title": "x", "duration_minutes": 60}))).parse("x", CTX)
    assert r == CreateTask(kind="task", title="x")


def test_pending_shown_as_create_item():
    f = Fake(completion("?"), completion("?"))
    GigaChatIntentParser(f).parse("x", ParseContext(CTX.now, {"intent": "create_meeting", "title": "в"}, [], "Во сколько?"))
    assert '"intent": "create_item", "title": "в", "kind": "meeting"' in f.last.messages[0].content or '"kind": "meeting"' in f.last.messages[0].content


def test_ground_drops_time_and_date_not_said():
    from app.adapters.outbound.gigachat.intent_parser import _ground
    a = {"date": "2026-10-30", "date_said": "завтра", "time": "00:00", "title": "x"}
    _ground("Напомни мне завтра маме подарок купить", a)
    assert a == {"date": "2026-10-30", "title": "x"}
    a = {"date": "2026-11-01", "date_said": "до конца недели", "time": "10:00", "time_said": "в 10"}
    _ground("Купить подарок маме в 10", a)
    assert a == {"time": "10:00"}
    a = {"date": "2026-11-03", "time": "11:00", "time_said": "в 11", "rrule": "FREQ=WEEKLY;BYDAY=TU"}
    _ground("Каждый вторник в 11 созвон", a)
    assert a["date"] == "2026-11-03" and a["time"] == "11:00"


@pytest.mark.parametrize("name,args,want", [
    ("create_item", {"kind": "task", "title": "x", "date": "+1m", "date_said": "через месяц"}, {"date": date(2026, 11, 28)}),
    ("create_item", {"kind": "task", "title": "x", "date": "12-15", "date_said": "15 декабря"}, {"date": date(2026, 12, 15)}),
    ("create_item", {"kind": "task", "title": "x", "date": "+3w", "date_said": "через три недели"}, {"date": date(2026, 11, 18)}),
    ("update_item", {"target_ref": "last", "new_date": "next wed"}, {"new_date": date(2026, 11, 4)}),
    ("delete_item", {"target_query": "x", "date": "fri"}, {"date": date(2026, 10, 30)}),
    ("list_items", {"period": "next month"}, {"date_from": date(2026, 11, 1), "date_to": date(2026, 11, 30)}),
    ("search_items", {"query": "x", "period": "+1d"}, {"date_from": date(2026, 10, 29), "date_to": date(2026, 10, 29)}),
    ("list_items", {"period": "мусор", "kind": "task"}, {"date_from": None, "date_to": None}),
    ("update_item", {"target_ref": "last", "delay_months": 1}, {"delay_months": 1}),
])
def test_dsl_converted_to_dates(name, args, want):
    r = GigaChatIntentParser(Fake(fc(name, args))).parse(args.get("date_said", "x"), CTX)
    assert {k: getattr(r, k) for k in want} == want


def test_pending_answer_keeps_time_without_quote():
    draft = {"intent": "create_task", "kind": "reminder", "title": "купить подарок маме", "date": "2026-10-30"}
    f = Fake(fc("create_item", {"kind": "reminder", "time": "18:00", "date": "2026-10-30"}))
    r = GigaChatIntentParser(f).parse("в 18", ParseContext(CTX.now, draft, [], "Во сколько напомнить?"))
    assert r.time == "18:00" and r.date == date(2026, 10, 30)


def test_ground_keeps_values_from_recent_items():
    from datetime import date, datetime
    from app.adapters.outbound.gigachat.intent_parser import _ground
    from app.domain.dates import MSK
    from app.domain.item import Item, ItemKind
    petr = Item(kind=ItemKind.MEETING, title="встреча с Петром", user_id="u", due_date=date(2026, 10, 31),
                starts_at=datetime(2026, 10, 31, 19, 0, tzinfo=MSK))
    a = {"date": "2026-10-31", "time": "19:00", "title": "встреча с Анной"}
    _ground("создать встречу с Анной в это же время", a, [petr])
    assert a["date"] == "2026-10-31" and a["time"] == "19:00"
    a = {"date": "2026-11-02", "time": "00:00"}
    _ground("создать встречу с Анной", a, [petr])
    assert "time" not in a and "date" not in a


def test_split_returns_several_commands():
    from app.application.intents import Several
    ctx = ParseContext(CTX.now, None, [])
    f = SplitFake(fc("split_commands", {"commands": ["купи хлеб завтра", "удали задачу про отчёт"]}), fc("create_item", {"kind": "task", "title": "x"}))
    assert GigaChatIntentParser(f).parse("купи хлеб завтра, потом удали задачу про отчёт", ctx) == \
        Several(commands=["купи хлеб завтра", "удали задачу про отчёт"])


class SplitFake:  # деление и разбор идут параллельно: ответ выбирается по запросу, а не по очереди
    def __init__(self, split, parse):
        self.split, self.parse, self.calls = split, parse, 0

    def chat(self, payload):
        self.calls += 1
        fcall = payload.function_call
        return self.split if getattr(fcall, "name", None) == "split_commands" else self.parse


def test_single_command_after_split_goes_to_normal_parse():
    ctx = ParseContext(CTX.now, None, [])
    f = SplitFake(fc("split_commands", {"commands": ["отметь её выполненной"]}), fc("complete_item", {"target_ref": "last"}))
    assert GigaChatIntentParser(f).parse("отметь её выполненной", ctx).intent == "complete_item"
    assert f.calls == 2


def test_ground_keeps_time_whose_hour_is_in_phrase():
    from app.adapters.outbound.gigachat.intent_parser import _ground
    a = {"time": "18:00"}
    _ground("Напомни в 6 вечера забрать посылку", a)
    assert a == {"time": "18:00"}
    a = {"time": "00:00"}
    _ground("Напомни завтра позвонить в банк", a)
    assert a == {}


def test_query_wins_over_last_ref():
    f = Fake(fc("update_item", {"new_time": "17:00", "target_query": "созвон", "target_ref": "last"}))
    r = GigaChatIntentParser(f).parse("Измени время созвона на 17:00", CTX)
    assert r.intent == "update_item" and r.target.query == "созвон" and r.target.ref is None


def test_same_day_takes_day_of_last_record():
    from datetime import date, datetime
    from app.domain.dates import MSK
    from app.domain.item import Item, ItemKind
    ana = Item(kind=ItemKind.MEETING, title="встреча с Анастасией", user_id="u", due_date=date(2026, 10, 31),
               starts_at=datetime(2026, 10, 31, 19, 0, tzinfo=MSK))
    f = Fake(fc("create_item", {"kind": "meeting", "title": "встреча с Евгением", "same_day": True,
                                "time": "12:00", "time_said": "12:00"}))
    r = GigaChatIntentParser(f).parse("поставь на 12:00 этого же дня встречу с Евгением", ParseContext(CTX.now, None, [ana], split=False))
    assert r.date == date(2026, 10, 31) and r.time == "12:00"


def test_relative_to_last_record_is_computed_in_code():
    from datetime import date, datetime
    from app.adapters.outbound.gigachat.intent_parser import _relative
    from app.domain.dates import MSK
    from app.domain.item import Item, ItemKind
    petr = Item(kind=ItemKind.MEETING, title="встреча с Петром", user_id="u", due_date=date(2026, 10, 31),
                starts_at=datetime(2026, 10, 31, 9, 0, tzinfo=MSK))
    for args, want in [({"same_time": True}, ("2026-10-31", "09:00")),
                       ({"offset_minutes": -60}, ("2026-10-31", "08:00")),
                       ({"same_time": True, "offset_days": 1}, ("2026-11-01", "09:00")),
                       ({"offset_days": 1, "time": "10:00"}, ("2026-11-01", "10:00")),
                       ({"same_day": True, "time": "12:00"}, ("2026-10-31", "12:00"))]:
        _relative(args, petr)
        assert (args["date"], args["time"]) == want
