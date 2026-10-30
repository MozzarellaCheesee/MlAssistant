import pytest
from datetime import date, datetime, timedelta, timezone

from app.adapters.outbound.sqlite.db import Database
from app.adapters.outbound.sqlite.dialog_store import SqliteDialogStore
from app.application.dialog import DialogState
from app.application.intents import Confirm, CreateMeeting, DeleteItem, Target
from app.application.results import Question
from app.domain.dates import MSK

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=MSK)
MEET = CreateMeeting(title="встреча с Иваном", due_date=date(2026, 10, 29))


def st(intent, **q):
    return DialogState().ask(intent, Question(text="?", **q), set())


def test_no_pending_returns_none():
    from app.application.dialog import resolve_reply
    assert resolve_reply(DialogState(), "да", NOW) is None


def test_time_date_recurrence_slots_go_to_llm():
    from app.application.dialog import resolve_reply
    for slot, text in [("time", "В 15"), ("time", "послезавтра в 11"), ("date", "5.11"), ("recurrence", "по будням")]:
        assert resolve_reply(st(MEET, awaiting="slot", slot=slot), text, NOW) is None


def test_time_slot_new_command_returns_none():
    from app.application.dialog import resolve_reply
    assert resolve_reply(st(MEET, awaiting="slot", slot="time"), "Что у меня завтра?", NOW) is None


def test_confirm_yes_no():
    from app.application.dialog import resolve_reply
    s = st(MEET, awaiting="confirm")
    assert resolve_reply(s, "Да.", NOW) == Confirm(answer="yes")
    assert resolve_reply(s, "нет, не надо", NOW) is None
    assert resolve_reply(s, "нет", NOW) == Confirm(answer="no")


def test_choose_by_word_and_number():
    from app.application.dialog import resolve_reply
    s = st(DeleteItem(target=Target(query="x")), awaiting="choose", options=[7, 9, 11])
    assert resolve_reply(s, "вторую", NOW).target == Target(id=9)
    assert resolve_reply(s, "3", NOW).target == Target(id=11)
    assert resolve_reply(s, "последнюю", NOW).target == Target(id=11)


def test_store_expires_after_24h():
    db = Database(":memory:")
    s = SqliteDialogStore(db)
    s.save("s", DialogState(last_referenced_ids=[1]))
    old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    db.conn.execute("UPDATE dialog_state SET updated_at=?", (old,))
    assert s.load("s") == DialogState()


def test_new_command_is_not_an_answer():
    from app.application.dialog import resolve_reply
    for slot, text in [("date", "Что у меня завтра?"), ("title", "Что у меня завтра?"),
                       ("target", "найди отчёт"), ("time", "Покажи встречи в 15")]:
        assert resolve_reply(st(MEET, awaiting="slot", slot=slot), text, NOW) is None
    c = st(DeleteItem(target=Target(query="x")), awaiting="confirm")
    assert resolve_reply(c, "Да, а что у меня завтра?", NOW) is None
    assert resolve_reply(c, "да", NOW) == Confirm(answer="yes")


def test_slot_answers_go_to_llm():
    """Название и «только эту» без LLM не разбираются: модель видит черновик и дополняет его."""
    from app.application.dialog import resolve_reply
    assert resolve_reply(st(MEET, awaiting="slot", slot="title"), "купить хлеб", NOW) is None
    s = st(DeleteItem(target=Target(ref="last")), awaiting="slot", slot="scope")
    assert all(resolve_reply(s, w, NOW) is None for w in ("только эту", "все", "не знаю"))


def test_done_keeps_or_replaces_ids():
    assert DialogState(last_referenced_ids=[1, 2]).done([]).last_referenced_ids == [1, 2]
    assert DialogState(last_referenced_ids=[1, 2]).done([5]).last_referenced_ids == [5]


def test_scope_slot_whole_phrase_only():
    from app.application.dialog import resolve_reply
    s = st(DeleteItem(target=Target(ref="last")), awaiting="slot", slot="scope")
    assert resolve_reply(s, "все равно", NOW) is None
    assert resolve_reply(s, "все отчеты удали", NOW) is None


def test_pending_expires_after_ttl_keeps_refs():
    now = [datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)]
    s = SqliteDialogStore(Database(":memory:"), now=lambda: now[0], pending_ttl=600)
    s.save("s", st(DeleteItem(target=Target(query="x")), awaiting="choose", options=[1, 2]).model_copy(update={"last_referenced_ids": [5]}))
    now[0] += timedelta(seconds=599)
    assert s.load("s").pending is not None
    now[0] += timedelta(seconds=2)
    r = s.load("s")
    assert r.pending is None and r.last_referenced_ids == [5]
