import pytest
from datetime import date, datetime

from app.application.intents import (CompleteItem, Confirm, CreateMeeting, CreateTask, DeleteItem, ListItems, Target,
                                     UpdateItem)
from app.domain.dates import MSK, DateRange
from app.domain.item import Item, ItemKind, Status
from tests.scenarios.fakes import FakeParser, build_service, dt

WIDE = DateRange(date(2026, 1, 1), date(2026, 12, 31))


def task(repo, title, d=date(2026, 11, 1)):
    return repo.add(Item(kind=ItemKind.TASK, title=title, user_id="u", due_date=d))


def all_items(repo):
    return repo.list_between("u", WIDE, None, None)


def test_dialog_from_spec():
    a, b, c = "Поставь встречу с Иваном завтра", "В 15", "А перенеси её на 16"
    m = {a: CreateMeeting(title="встреча с Иваном", date=date(2026, 10, 29)),
         b: CreateMeeting(date=date(2026, 10, 29), time="15:00"),
         c: UpdateItem(target=Target(ref="last"), new_time="16:00")}
    svc, repo = build_service(FakeParser(m))
    assert svc.handle_turn("s", "u", a).reply == "На какое время поставить встречу?"
    svc.handle_turn("s", "u", b)
    assert svc.handle_turn("s", "u", c).reply == "Перенёс: встреча с Иваном, завтра в 16:00."
    assert all_items(repo)[0].starts_at == datetime(2026, 10, 29, 16, 0, tzinfo=MSK)


def test_complete_by_query():
    svc, repo = build_service(FakeParser({"x": CompleteItem(target=Target(query="отчёт"))}))
    task(repo, "отправить отчёт")
    assert svc.handle_turn("s", "u", "x").reply == "Отметил выполненной: отправить отчёт."
    assert all_items(repo)[0].status == Status.DONE


def test_delete_hides_from_list():
    m = {"x": DeleteItem(target=Target(query="билеты")), "y": ListItems(date_from=date(2026, 10, 28), date_to=date(2026, 11, 1))}
    svc, repo = build_service(FakeParser(m))
    task(repo, "купить билеты")
    assert svc.handle_turn("s", "u", "x").reply == "Удалил: купить билеты."
    assert svc.handle_turn("s", "u", "y").reply == "На этот период ничего не запланировано."


def test_choose_from_several():
    svc, repo = build_service(FakeParser({"x": CompleteItem(target=Target(query="отчёт"))}))
    task(repo, "отчёт за август"), task(repo, "отчёт за сентябрь")
    r = svc.handle_turn("s", "u", "x")
    assert r.clarify and r.reply.startswith("Какую из них? 1)")
    svc.handle_turn("s", "u", "вторую")
    done = [i for i in all_items(repo) if i.status == Status.DONE]
    assert [i.title for i in done] == ["отчёт за сентябрь"]


def test_last_after_list_with_many_asks():
    m = {"y": ListItems(date_from=date(2026, 10, 28), date_to=date(2026, 11, 1)), "x": DeleteItem(target=Target(ref="last"))}
    svc, repo = build_service(FakeParser(m))
    for t in "abc":
        task(repo, f"задача {t}", date(2026, 10, 30))
    svc.handle_turn("s", "u", "y")
    r = svc.handle_turn("s", "u", "x")
    assert r.clarify and r.reply.startswith("Какую из них?")


def test_last_without_context_asks():
    svc, repo = build_service(FakeParser({"x": CompleteItem(target=Target(ref="last")),
                                        "отчёт": CompleteItem(target=Target(query="отчёт"))}))
    task(repo, "отправить отчёт")
    assert svc.handle_turn("s", "u", "x").reply == "О какой записи речь?"
    svc.handle_turn("s", "u", "отчёт")
    assert all_items(repo)[0].status == Status.DONE


def test_update_date_keeps_time():
    svc, repo = build_service(FakeParser({"x": UpdateItem(target=Target(ref="last"), new_date=date(2026, 10, 30))}))
    m = repo.add(Item(kind=ItemKind.MEETING, title="встреча", user_id="u", due_date=date(2026, 10, 29),
                      starts_at=datetime(2026, 10, 29, 15, 0, tzinfo=MSK)))
    svc.dialog.save("u:s", svc.dialog.load("u:s").done([m.id]))
    svc.handle_turn("s", "u", "x")
    assert all_items(repo)[0].starts_at == datetime(2026, 10, 30, 15, 0, tzinfo=MSK)


def test_last_skips_cancelled():
    m = {"l": ListItems(date_from=date(2026, 10, 28), date_to=date(2026, 11, 1)), "d": DeleteItem(target=Target(ref="last")),
         "c": CompleteItem(target=Target(ref="last"))}
    svc, repo = build_service(FakeParser(m))
    task(repo, "билеты", date(2026, 10, 30))
    svc.handle_turn("s", "u", "l")
    svc.handle_turn("s", "u", "d")
    assert svc.handle_turn("s", "u", "c").reply == "О какой записи речь?"
    assert repo.get("u", 1).status == Status.CANCELLED


def test_move_time_only_keeps_item_date_even_if_model_knows_nothing():
    svc, repo = build_service(FakeParser({"x": UpdateItem(target=Target(ref="last"), new_time="18:00")}))
    m = repo.add(Item(kind=ItemKind.MEETING, title="встреча", user_id="u", due_date=date(2026, 10, 29),
                      starts_at=datetime(2026, 10, 29, 17, 0, tzinfo=MSK), ends_at=datetime(2026, 10, 29, 18, 0, tzinfo=MSK)))
    svc.dialog.save("u:s", svc.dialog.load("u:s").done([m.id]))
    svc.handle_turn("s", "u", "x")
    i = all_items(repo)[0]
    assert i.starts_at == datetime(2026, 10, 29, 18, 0, tzinfo=MSK) and i.ends_at == datetime(2026, 10, 29, 19, 0, tzinfo=MSK)


def test_stale_choose_replaced_by_new_delete_with_date():
    svc, repo = build_service(FakeParser({
        "убери": DeleteItem(target=Target(query="обзор")),
        "встречу завтра убери с иннокентием": DeleteItem(target=Target(query="Иннокентий"), date=date(2026, 10, 29))}))
    for n in ("Анной", "Иваном"):
        repo.add(Item(kind=ItemKind.MEETING, title=f"обзор с {n}", user_id="u", due_date=date(2026, 10, 29)))
    keep = repo.add(Item(kind=ItemKind.MEETING, title="обзор с Иннокентий", user_id="u", due_date=date(2026, 10, 29)))
    other = repo.add(Item(kind=ItemKind.MEETING, title="обзор с Иннокентий", user_id="u", due_date=date(2026, 10, 30)))
    assert svc.handle_turn("s", "u", "убери").clarify
    r = svc.handle_turn("s", "u", "встречу завтра убери с иннокентием")
    assert r.reply.startswith("Удалил: обзор с Иннокентий")
    assert repo.get("u", keep.id).status == Status.CANCELLED and repo.get("u", other.id).status != Status.CANCELLED


def test_delete_by_query_with_kind():
    svc, repo = build_service(FakeParser({"x": DeleteItem(target=Target(query="билеты"), kind="task")}))
    task(repo, "купить билеты")
    assert svc.handle_turn("s", "u", "x").reply == "Удалил: купить билеты."


def test_delete_not_found_offers_create_yes():
    svc, repo = build_service(FakeParser({"x": DeleteItem(target=Target(query="молоко")), "да": Confirm(answer="yes")}))
    assert svc.handle_turn("s", "u", "x").reply == "Такой записи нет. Создать задачу «молоко»?"
    assert svc.handle_turn("s", "u", "да").reply == "Задача добавлена: молоко."
    assert repo.get("u", 1).title == "молоко"


def test_complete_not_found_offer_no():
    svc, repo = build_service(FakeParser({"x": CompleteItem(target=Target(query="хлеб"), kind="reminder")}))
    assert svc.handle_turn("s", "u", "x").reply == "Такой записи нет. Создать напоминание «хлеб»?"
    assert svc.handle_turn("s", "u", "нет").reply.startswith("Хорошо")
    assert repo.get("u", 1) is None


def test_remind_about_last_task_sets_its_time():
    m = {"a": CreateTask(title="купить молока", date=date(2026, 10, 29)),
         "b": UpdateItem(target=Target(ref="last"), remind=True, new_date=date(2026, 10, 29)),
         "c": UpdateItem(target=Target(ref="last"), new_time="15:00"),
         "d": ListItems(date_from=date(2026, 10, 29), date_to=date(2026, 10, 29), kind="task")}
    svc, repo = build_service(FakeParser(m))
    svc.handle_turn("s", "u", "a")
    assert svc.handle_turn("s", "u", "b").reply == "Во сколько напомнить?"
    assert svc.handle_turn("s", "u", "c").reply == "Напомню завтра в 15:00: купить молока."
    assert len(all_items(repo)) == 1 and all_items(repo)[0].starts_at.hour == 15
    assert all_items(repo)[0].kind == ItemKind.REMINDER
    assert [i.title for i in svc.due_reminders("u")] == [] and "купить молока" in svc.handle_turn("s", "u", "d").reply


def test_task_list_includes_reminders():
    m = {"x": ListItems(date_from=date(2026, 10, 29), date_to=date(2026, 10, 29), kind="task")}
    svc, repo = build_service(FakeParser(m))
    task(repo, "купить хлеб", date(2026, 10, 29))
    repo.add(Item(kind=ItemKind.REMINDER, title="позвонить маме", user_id="u", due_date=date(2026, 10, 29)))
    repo.add(Item(kind=ItemKind.MEETING, title="встреча", user_id="u", due_date=date(2026, 10, 29)))
    reply = svc.handle_turn("s", "u", "x").reply
    assert "купить хлеб" in reply and "позвонить маме" in reply and "встреча" not in reply


def test_remind_about_meeting_keeps_meeting_and_adds_reminder():
    m = {"a": CreateMeeting(title="встреча с Иваном", date=date(2026, 10, 29), time="10:00"),
         "b": UpdateItem(target=Target(ref="last"), remind=True, new_time="09:00")}
    svc, repo = build_service(FakeParser(m))
    svc.handle_turn("s", "u", "a")
    assert svc.handle_turn("s", "u", "b").reply == "Напомню завтра в 9:00: встреча с Иваном."
    meeting, rem = sorted(all_items(repo), key=lambda i: i.id)
    assert meeting.kind == ItemKind.MEETING and meeting.starts_at.hour == 10
    assert rem.kind == ItemKind.REMINDER and rem.starts_at.hour == 9 and rem.title == "встреча с Иваном"


def test_due_reminders_logged_to_chat():
    svc, repo = build_service(FakeParser({}), now=datetime(2026, 10, 28, 12, 0, tzinfo=MSK))
    repo.add(Item(kind=ItemKind.REMINDER, title="выпить воды", user_id="u", due_date=date(2026, 10, 28),
                  starts_at=datetime(2026, 10, 28, 11, 0, tzinfo=MSK)))
    assert len(svc.due_reminders("u")) == 1
    assert [m["text"] for m in svc.history("u", 0, 10)] == ["Напоминание: выпить воды"]



def test_several_commands_run_in_order():
    from app.application.intents import Several
    m = {"x": Several(commands=["a", "b"]), "a": DeleteItem(target=Target(query="билеты")),
         "b": CreateTask(title="купить молоко", date=date(2026, 10, 29))}
    svc, repo = build_service(FakeParser(m))
    task(repo, "купить билеты")
    assert svc.handle_turn("s", "u", "x").reply == "Удалил: купить билеты. Задача добавлена: купить молоко, завтра."
    assert [i.title for i in all_items(repo)] == ["купить молоко"]


def test_several_commands_capped_at_ten():
    from app.application.intents import Several
    m = {"x": Several(commands=[str(n) for n in range(12)]),
         **{str(n): CreateTask(title=f"дело {n}", date=date(2026, 10, 29)) for n in range(12)}}
    svc, repo = build_service(FakeParser(m))
    svc.handle_turn("s", "u", "x")
    assert len(all_items(repo)) == 10


def test_several_stops_at_question():
    from app.application.intents import Several
    m = {"x": Several(commands=["a", "b"]), "a": CreateMeeting(title="встреча с Анной", date=date(2026, 10, 29)),
         "b": CreateTask(title="купить молоко", date=date(2026, 10, 29)), "в 15": CreateMeeting(time="15:00")}
    svc, repo = build_service(FakeParser(m))
    r = svc.handle_turn("s", "u", "x")
    assert r.reply == "На какое время поставить встречу?" and r.clarify and all_items(repo) == []
    r = svc.handle_turn("s", "u", "в 15")  # после ответа выполняется и отложенная «b»
    assert r.reply == "Встреча добавлена: встреча с Анной, завтра в 15:00. Задача добавлена: купить молоко, завтра."


def test_several_inherits_previous_day():
    from app.application.intents import Several
    m = {"x": Several(commands=["a", "b"]),
         "a": DeleteItem(target=Target(query="встреча с Анастасией")),
         "b": CreateMeeting(title="встреча с Евгением", time="12:00")}
    svc, repo = build_service(FakeParser(m))
    repo.add(Item(kind=ItemKind.MEETING, title="встреча с Анастасией", user_id="u", due_date=date(2026, 10, 30),
                  starts_at=dt(10, 30, 19), ends_at=dt(10, 30, 20)))
    assert svc.handle_turn("s", "u", "x").reply == \
        "Удалил: встреча с Анастасией. Встреча добавлена: встреча с Евгением, послезавтра в 12:00."


def test_delay_moves_relative_to_item_itself():
    m = {"a": UpdateItem(target=Target(ref="last"), delay_minutes=60),
         "b": UpdateItem(target=Target(ref="last"), delay_days=1)}
    svc, repo = build_service(FakeParser({**m, "c": CreateMeeting(title="встреча с Петром", date=date(2026, 10, 30), time="09:00")}))
    svc.handle_turn("s", "u", "c")
    assert svc.handle_turn("s", "u", "a").reply == "Перенёс: встреча с Петром, послезавтра в 10:00."
    svc.handle_turn("s", "u", "b")
    assert all_items(repo)[0].starts_at == dt(10, 31, 10)


def test_delay_months_keeps_time():
    m = {"a": CreateMeeting(title="встреча", date=date(2026, 10, 29), time="10:00"),
         "b": UpdateItem(target=Target(ref="last"), delay_months=1)}
    svc, repo = build_service(FakeParser(m))
    svc.handle_turn("s", "u", "a")
    svc.handle_turn("s", "u", "b")
    assert all_items(repo)[0].starts_at == dt(11, 29, 10)
