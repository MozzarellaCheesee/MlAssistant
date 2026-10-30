import pytest
from datetime import datetime

from app.application.intents import CreateMeeting, CreateTask, ListItems, SearchItems
from app.domain.dates import MSK, DateRange
from tests.scenarios.fakes import dt
from tests.scenarios.test_create import items as _all, run


def items(repo):
    return list({i.id: i for i in sorted(_all(repo), key=lambda i: i.starts_at, reverse=True)}.values())[::-1]


def _weekly(extra=None):
    t = "планёрка по понедельникам"
    return t, {t: CreateMeeting(title="планёрка", date=date(2026, 10, 28), time="9:00", rrule="FREQ=WEEKLY;BYDAY=MO"), **(extra or {})}


def test_create_weekly_meeting():
    t, m = _weekly()
    _, repo, (r,) = run(m, t)
    assert r.reply == "Встреча добавлена: планёрка, каждый понедельник в 9:00, с 2 ноября."
    [i] = items(repo)
    assert i.rrule == "FREQ=WEEKLY;BYDAY=MO" and i.starts_at == datetime(2026, 11, 2, 9, 0, tzinfo=MSK)


def test_create_weekday_reminder():
    t = "зарядка по будням"
    _, repo, (r,) = run({t: CreateTask(kind="reminder", title="зарядка", date=date(2026, 10, 28), time="8:00", rrule="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR")}, t)
    assert r.reply.startswith("Буду напоминать по будням в 8:00")
    assert items(repo)[0].starts_at == datetime(2026, 10, 29, 8, 0, tzinfo=MSK)


def test_recurrence_slot_answer_keeps_rrule():
    t = "зарядка по будням"
    m = {t: CreateTask(kind="reminder", title="зарядка", rrule="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
         "в 8": CreateTask(date=date(2026, 10, 29), time="8:00")}
    _, repo, (r1, r2) = run(m, t, "в 8")
    assert r1.reply == "Во сколько напомнить?" and r2.reply.startswith("Буду напоминать по будням в 8:00")
    assert len(items(repo)) == 1




def test_list_week_shows_occurrences():
    t, m = _weekly({"что на следующей неделе": ListItems(date_from=date(2026, 11, 2), date_to=date(2026, 11, 8))})
    svc, _, (_, r) = run(m, t, "что на следующей неделе")
    assert "в понедельник, 2 ноября в 9:00 — планёрка (повтор)" in r.reply
    ids = svc.dialog.load("u:s").last_referenced_ids
    assert len(set(ids)) == len(ids)


def test_search_returns_series_once():
    t, m = _weekly({"найди планёрку": SearchItems(query="планёрка")})
    _, _, (_, r) = run(m, t, "найди планёрку", now=datetime(2026, 10, 28, 12, 0, tzinfo=MSK))
    assert r.reply.startswith("Нашёл 1 запись") and "каждый понедельник" in r.reply


from datetime import date  # noqa: E402

from app.application.intents import CompleteItem, DeleteItem, Target, UpdateItem  # noqa: E402
from app.domain.item import Status  # noqa: E402

LAST = Target(ref="last")


def _series(*, then, extra=None):
    t, m = _weekly(extra)
    return run(m, t, *then)


def _lst():
    return "что на следующей неделе", ListItems(date_from=date(2026, 11, 2), date_to=date(2026, 11, 8))


def test_delete_one_occurrence():
    d = "удали планёрку"
    lw, li = _lst()
    _, repo, (_, q, r, a) = _series(then=(d, "только эту", lw), extra={
        d: DeleteItem(target=Target(query="планёрка")), lw: li,
        "только эту": DeleteItem(target=Target(query="планёрка"), scope="one")})
    assert q.clarify and "2 ноября" in q.reply and "каждый понедельник" in q.reply
    assert r.reply == "Удалил повторение 2 ноября: планёрка."
    assert items(repo)[0].exdates == [date(2026, 11, 2)]
    assert "планёрка" not in a.reply
    assert [o.occurrence for o in repo.list_between("u", DateRange(date(2026, 11, 9), date(2026, 11, 9)), None, None)] == [date(2026, 11, 9)]


def test_delete_all():
    d = "удали всю планёрку"
    _, repo, (_, r) = _series(then=(d,), extra={d: DeleteItem(target=Target(query="планёрка"), scope="all")})
    assert r.reply == "Удалил всю серию: планёрка." and repo.get('u', 1).status == Status.CANCELLED


def test_update_one_occurrence_time():
    u = "перенеси на 11"
    _, repo, (_, r) = _series(then=(u,), extra={u: UpdateItem(target=LAST, new_time="11:00", scope="one")})
    assert r.reply.startswith("Перенёс повторение: планёрка")
    ser, new = repo.get('u', 1), repo.get('u', 2)
    assert ser.exdates == [date(2026, 11, 2)]
    assert new.starts_at == datetime(2026, 11, 2, 11, 0, tzinfo=MSK) and new.exdates == []


def test_update_all_time():
    u, lw = "перенеси на 10", "что на следующей неделе"
    _, repo, (_, r, l) = _series(then=(u, lw), extra={
        u: UpdateItem(target=LAST, new_time="10:00", scope="all"), lw: ListItems(date_from=date(2026, 11, 2), date_to=date(2026, 11, 8))})
    assert r.reply.startswith("Перенёс всю серию: планёрка")
    assert items(repo)[0].starts_at.hour == 10 and "2 ноября в 10:00" in l.reply


def test_update_all_date_refused():
    u = "перенеси на вторник"
    _, repo, (_, r) = _series(then=(u,), extra={u: UpdateItem(target=LAST, new_date=date(2026, 11, 3), new_time="09:00", scope="all")})
    assert r.reply.startswith("Всю серию на другой день пока не переношу")
    assert items(repo)[0].starts_at == datetime(2026, 11, 2, 9, 0, tzinfo=MSK)


def test_complete_one():
    c = "отметь планёрку"
    _, repo, (_, r) = _series(then=(c,), extra={c: CompleteItem(target=LAST, scope="one")})
    i = items(repo)[0]
    assert r.reply == "Отметил выполненным повторение 2 ноября: планёрка." and i.exdates == [date(2026, 11, 2)] and i.status == Status.OPEN


def test_regular_item_unchanged():
    t = "купить молоко"
    d = "удали молоко"
    _, repo, (_, r) = run({t: CreateTask(title="купить молоко"), d: DeleteItem(target=LAST)}, t, d)
    assert not r.clarify and repo.get('u', 1).status == Status.CANCELLED


def _far_series(cmd, intent):
    t, m = _weekly({cmd: intent})
    svc, repo, _ = run(m, t)
    s = repo.get('u', 1)
    s.rrule, s.due_date, s.starts_at = "FREQ=YEARLY", date(2028, 1, 1), datetime(2028, 1, 1, 9, 0, tzinfo=MSK)
    repo.update(s)
    return repo, svc.handle_turn("s", "u", cmd)


def test_update_one_moves_occurrence_to_new_date():
    u = "перенеси ближайшую на вторник"
    _, repo, (_, r) = _series(then=(u,), extra={u: UpdateItem(target=LAST, new_date=date(2026, 11, 3), new_time="09:00", scope="one")})
    assert repo.get('u', 1).exdates == [date(2026, 11, 2)]
    assert repo.get('u', 2).starts_at == datetime(2026, 11, 3, 9, 0, tzinfo=MSK)


def test_all_scope_without_occurrences():
    repo, r = _far_series("удали", DeleteItem(target=LAST))
    assert not r.clarify and r.reply == "Удалил всю серию: планёрка." and repo.get('u', 1).status == Status.CANCELLED
    repo, r = _far_series("отметь", CompleteItem(target=LAST))
    assert not r.clarify and repo.get('u', 1).status == Status.DONE


def test_one_scope_without_occurrences():
    repo, r = _far_series("удали", DeleteItem(target=LAST, scope="one"))
    assert r.reply == "У этой серии больше нет повторений." and repo.get('u', 1).status == Status.OPEN


from tests.scenarios.fakes import FakeParser, build_service  # noqa: E402


def _svc(m, now=datetime(2026, 10, 28, 9, 0, tzinfo=MSK)):
    svc, repo = build_service(FakeParser(m), now=now)
    return svc, repo, lambda *a: setattr(svc.clock, "dt", datetime(*a, tzinfo=MSK))


def test_moved_occurrence_of_fired_series_fires():
    m = {"a": CreateTask(kind="reminder", title="пить воду", date=date(2026, 10, 28), time="13:00", rrule="FREQ=DAILY"),
         "one": UpdateItem(target=Target(id=1), new_date=date(2026, 10, 30), new_time="15:00", scope="one")}  # по id: диалог живёт 24 ч по часам, а тест перематывает на двое суток
    svc, _, at = _svc(m)
    svc.handle_turn("s", "u", "a")
    at(2026, 10, 29, 13, 5)
    assert svc.due_reminders("u")
    at(2026, 10, 30, 9, 0)
    svc.handle_turn("s", "u", "one")
    at(2026, 10, 30, 15, 1)
    assert [i.starts_at.hour for i in svc.due_reminders("u")] == [15]


def test_moved_fired_reminder_fires_again():
    m = {"a": CreateTask(kind="reminder", title="позвонить", date=date(2026, 10, 28), time="10:00"),
         "mv": UpdateItem(target=LAST, new_time="15:00")}
    svc, _, at = _svc(m)
    svc.handle_turn("s", "u", "a")
    at(2026, 10, 28, 10, 1)
    assert svc.due_reminders("u")
    svc.handle_turn("s", "u", "mv")
    at(2026, 10, 28, 15, 1)
    assert [i.starts_at.hour for i in svc.due_reminders("u")] == [15]


def test_update_all_shift_to_other_day_refused():
    u = "через 2 дня"
    _, repo, (_, r) = _series(then=(u,), extra={u: UpdateItem(target=LAST, new_date=date(2026, 10, 30), new_time="09:00", scope="all")})
    assert r.reply.startswith("Всю серию на другой день пока не переношу")
    assert items(repo)[0].starts_at == datetime(2026, 11, 2, 9, 0, tzinfo=MSK)


def test_rename_one_occurrence_reply():
    u = "назови стендап"
    _, _, (_, r) = _series(then=(u,), extra={u: UpdateItem(target=LAST, title="стендап", scope="one")})
    assert r.reply.startswith("Переименовал повторение: стендап")


def test_update_all_pure_time_shift_keeps_series_day():
    m = {"a": CreateTask(kind="reminder", title="пить воду", date=date(2026, 10, 28), time="9:00", rrule="FREQ=DAILY"),
         "u": UpdateItem(target=LAST, new_time="13:00", scope="all")}
    svc, repo, at = _svc(m)
    svc.handle_turn("s", "u", "a")
    s = repo.get("u", 1)
    s.due_date, s.starts_at = date(2026, 10, 20), datetime(2026, 10, 20, 9, 0, tzinfo=MSK)
    repo.update(s)
    at(2026, 10, 28, 12, 0)
    r = svc.handle_turn("s", "u", "u")
    assert r.reply.startswith("Перенёс всю серию")
    assert repo.get("u", 1).starts_at == datetime(2026, 10, 20, 13, 0, tzinfo=MSK)


def test_rename_to_same_title_is_noop_reply():
    m = {"a": CreateTask(title="x", date=date(2026, 10, 29)), "u": UpdateItem(target=LAST, title="x")}
    svc, _, _ = _svc(m)
    svc.handle_turn("s", "u", "a")
    assert svc.handle_turn("s", "u", "u").reply == "Запись уже называется «x»."


# --- ближайшее вхождение вместо первого ---
def _old_series(m, now=datetime(2026, 10, 28, 8, 0, tzinfo=MSK)):
    """Ежедневная планёрка 9:00 с первым вхождением 20.10; сейчас 28.10 8:00."""
    svc, repo, at = _svc({"a": CreateMeeting(title="планёрка", date=date(2026, 10, 28), time="9:00", rrule="FREQ=DAILY"), **m})
    svc.handle_turn("s", "u", "a")
    s = repo.get("u", 1)
    s.due_date, s.starts_at = date(2026, 10, 20), datetime(2026, 10, 20, 9, 0, tzinfo=MSK)
    repo.update(s)
    at(now.year, now.month, now.day, now.hour, now.minute)
    return svc, repo


def test_delay_one_occurrence_of_old_series_shifts_nearest():
    svc, repo = _old_series({"u": UpdateItem(target=Target(id=1), delay_minutes=60, scope="one")})
    r = svc.handle_turn("s", "u", "u")
    assert r.reply.startswith("Перенёс повторение")
    assert repo.get("u", 2).starts_at == datetime(2026, 10, 28, 10, 0, tzinfo=MSK)


def test_delay_whole_old_series_keeps_first_day():
    svc, repo = _old_series({"u": UpdateItem(target=Target(id=1), shift_minutes=90, scope="all")})
    r = svc.handle_turn("s", "u", "u")
    assert r.reply.startswith("Перенёс всю серию")
    assert repo.get("u", 1).starts_at == datetime(2026, 10, 20, 9, 30, tzinfo=MSK)


def test_move_occurrence_onto_own_series_asks_conflict():
    svc, _ = _old_series({"u": UpdateItem(target=Target(id=1), new_date=date(2026, 10, 29), new_time="09:30", scope="one")})
    assert svc.handle_turn("s", "u", "u").reply.startswith("С 09:00 до 10:00 уже планёрка. Перенести всё равно")


def test_whole_series_conflict_checked_on_nearest_occurrence():
    svc, _ = _old_series({"b": CreateMeeting(title="обед", date=date(2026, 10, 28), time="10:00"),
                          "u": UpdateItem(target=Target(id=1), new_time="10:00", scope="all")})
    svc.handle_turn("s", "u", "b")
    assert svc.handle_turn("s", "u", "u").reply.startswith("С 10:00 до 11:00 уже обед")


def test_remind_about_meeting_in_past_asks():
    svc, _ = _old_series({"u": UpdateItem(target=Target(id=1), new_date=date(2026, 10, 27), new_time="08:00", remind=True, scope="one")})
    assert svc.handle_turn("s", "u", "u").reply.startswith("Это время уже прошло")
