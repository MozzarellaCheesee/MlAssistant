from datetime import date, datetime

import pytest

from app.application.errors import AssistantError, ErrorCode
from app.application.intents import CreateMeeting, CreateTask, ListItems
from app.application.results import Result
from app.domain.dates import MSK, DateRange
from app.domain.item import ItemKind
from tests.scenarios.fakes import FakeParser, build_service, dt

WIDE = DateRange(date(2026, 1, 1), date(2026, 12, 31))


def run(mapping, *texts, now=None):
    svc, repo = build_service(FakeParser(mapping), **({"now": now} if now else {}))
    return svc, repo, [svc.handle_turn("s", "u", t) for t in texts]


def items(repo):
    return repo.list_between("u", WIDE, None, None)


def test_reminder_tomorrow_10():
    t = "Напомни мне завтра в 10 утра позвонить Ивану"
    _, repo, (r,) = run({t: CreateTask(kind="reminder", title="позвонить Ивану", date=date(2026, 10, 29), time="10:00")}, t)
    assert r.reply == "Напомню завтра в 10:00: позвонить Ивану."
    [i] = items(repo)
    assert i.kind == ItemKind.REMINDER and i.starts_at == datetime(2026, 10, 29, 10, 0, tzinfo=MSK)


def test_task_friday_no_time():
    t = "В пятницу нужно отправить отчет"
    _, repo, _ = run({t: CreateTask(title="отправить отчет", date=date(2026, 10, 30))}, t)
    [i] = items(repo)
    assert i.due_date == date(2026, 10, 30) and i.starts_at is None


def test_task_end_of_week():
    t = "Поставь задачу купить билеты до конца недели"
    _, repo, _ = run({t: CreateTask(title="купить билеты", date=date(2026, 11, 1))}, t)
    assert items(repo)[0].due_date == date(2026, 11, 1)


def test_meeting_with_time():
    t = "Добавь встречу с командой завтра в 15:00"
    _, repo, _ = run({t: CreateMeeting(title="встреча с командой", date=date(2026, 10, 29), time="15:00")}, t)
    assert items(repo)[0].starts_at == datetime(2026, 10, 29, 15, 0, tzinfo=MSK)


def test_meeting_thursday_11():
    t = "В четверг в 11 у меня встреча с дизайнером"
    _, repo, (r,) = run({t: CreateMeeting(title="встреча с дизайнером", date=date(2026, 10, 29), time="11:00")}, t)
    assert not r.clarify
    assert items(repo)[0].starts_at == datetime(2026, 10, 29, 11, 0, tzinfo=MSK)


def test_meeting_clarify_time_dialog():
    t = "Поставь встречу с Иваном завтра"
    m = {t: CreateMeeting(title="встреча с Иваном", date=date(2026, 10, 29)), "В 15": CreateMeeting(date=date(2026, 10, 29), time="15:00")}
    _, _, (r1, r2) = run(m, t, "В 15")
    assert r1.clarify and r1.reply == "На какое время поставить встречу?"
    assert r2.reply == "Встреча добавлена: встреча с Иваном, завтра в 15:00."


def test_clarify_then_new_command_resets():
    t = "Поставь встречу с Иваном завтра"
    m = {t: CreateMeeting(title="встреча с Иваном", date=date(2026, 10, 29)),
         "Что у меня завтра?": ListItems(date_from=date(2026, 10, 29))}
    svc, _ = build_service(FakeParser(m))
    svc.handle_turn("s", "u", t)
    assert svc.handle_turn("s", "u", "Что у меня завтра?").reply == "На завтра ничего не запланировано."
    assert svc.dialog.load("u:s").pending is None


def test_reminder_needs_time():
    t = "Напомни завтра позвонить маме"
    _, _, (r,) = run({t: CreateTask(kind="reminder", title="позвонить маме", date=date(2026, 10, 29))}, t)
    assert r.reply == "Во сколько напомнить?"


def test_time_without_date_today_or_tomorrow():
    a, b = "Напомни в 18 позвонить маме", "Напомни в 9 позвонить маме"
    m = {a: CreateTask(kind="reminder", title="позвонить маме", date=date(2026, 10, 28), time="18:00"),
         b: CreateTask(kind="reminder", title="позвонить маме", date=date(2026, 10, 29), time="9:00")}
    _, repo, _ = run(m, a, b)
    assert sorted(i.starts_at for i in items(repo)) == [datetime(2026, 10, 28, 18, 0, tzinfo=MSK),
                                                        datetime(2026, 10, 29, 9, 0, tzinfo=MSK)]


def test_past_requires_confirm():
    t = "Напомни сегодня в 10 утра позвонить маме"
    _, repo, (r1, r2) = run({t: CreateTask(kind="reminder", title="позвонить маме", date=date(2026, 10, 28), time="10:00")}, t, "да")
    assert r1.reply == "Это время уже прошло. Сохранить всё равно или назовёте другое время?"
    assert len(items(repo)) == 1 and r2.reply.startswith("Напомню")


def test_conflict_confirm_no():
    a, b = "Добавь встречу с командой завтра в 15:00", "Поставь встречу с Петром завтра в 15:30"
    m = {a: CreateMeeting(title="встреча с командой", date=date(2026, 10, 29), time="15:00"),
         b: CreateMeeting(title="встреча с Петром", date=date(2026, 10, 29), time="15:30")}
    _, repo, (_, r2, r3) = run(m, a, b, "нет")
    assert "уже" in r2.reply and r2.clarify
    assert r3.reply == "Хорошо, отменил." and len(items(repo)) == 1


def test_conflict_shows_interval_and_new_time_creates():
    a, b, c = "Встреча с Ксюшей завтра в 13:28", "Встреча с Петром завтра в 14:00", "давай в 15:30"
    m = {a: CreateMeeting(title="встреча с Ксюшей", date=date(2026, 10, 29), time="13:28", duration_minutes=60),
         b: CreateMeeting(title="встреча с Петром", date=date(2026, 10, 29), time="14:00"),
         c: CreateMeeting(title="встреча с Петром", date=date(2026, 10, 29), time="15:30")}
    _, repo, (_, r2, r3) = run(m, a, b, c)
    assert r2.reply == "С 13:28 до 14:28 уже встреча с Ксюшей. Добавить всё равно или назовёте другое время?"
    assert len(items(repo)) == 2 and r3.reply.startswith("Встреча добавлена")


def test_shift_two_hours():
    t = "Напомни через два часа выпить таблетку"
    _, repo, _ = run({t: CreateTask(kind="reminder", title="выпить таблетку", date=date(2026, 10, 28), time="14:00")}, t)
    assert items(repo)[0].starts_at == datetime(2026, 10, 28, 14, 0, tzinfo=MSK)


def test_missing_title():
    t = "Запиши на завтра"
    _, repo, (r1, r2) = run({t: CreateTask(title=None, date=date(2026, 10, 29)),
                              "купить хлеб": CreateTask(title="купить хлеб")}, t, "купить хлеб")  # ответ на вопрос разбирает модель
    assert r1.reply == "Что именно записать?"
    [i] = items(repo)
    assert i.title == "купить хлеб" and i.due_date == date(2026, 10, 29)


class _Stt:
    def __init__(self, s):
        self.s = s

    def transcribe(self, audio):
        return self.s


def test_audio_empty_raises():
    svc, _ = build_service(FakeParser({}))
    svc.stt = _Stt("  ")
    with pytest.raises(AssistantError) as e:
        svc.handle_audio("s", "u", b"x")
    assert e.value.code == ErrorCode.STT_EMPTY


def test_audio_sets_transcript():
    svc, _ = build_service(FakeParser({}))
    svc.stt = _Stt(" привет ")
    assert svc.handle_audio("s", "u", b"x").transcript == "привет"


def test_confirmation_does_not_leak():
    a, b, c, d = "встреча а", "встреча с Петром", "да", "встреча с Олегом"
    m = {a: CreateMeeting(title="встреча а", date=date(2026, 10, 28), time="10:00"),
         b: CreateMeeting(title="встреча с Петром", date=date(2026, 10, 28), time="10:30"),
         d: CreateMeeting(title="встреча с Олегом", date=date(2026, 10, 28), time="9:00")}
    _, repo, rs = run(m, a, c, b, c, d)
    assert rs[3].clarify and rs[4].reply == "Это время уже прошло. Сохранить всё равно или назовёте другое время?" and len(items(repo)) == 1


def test_slot_answer_via_parser_merges():
    t, a = "Поставь встречу с Иваном завтра", "давай в пятнадцать часов"
    m = {t: CreateMeeting(title="встреча с Иваном", date=date(2026, 10, 29)), a: CreateMeeting(date=date(2026, 10, 29), time="15:00")}
    _, repo, (r1, _) = run(m, t, a)
    assert r1.clarify
    [i] = items(repo)
    assert i.title == "встреча с Иваном" and i.starts_at == datetime(2026, 10, 29, 15, 0, tzinfo=MSK)


def test_conflict_yes_saves():
    a, b = "Добавь встречу с командой завтра в 15:00", "Поставь встречу с Петром завтра в 15:30"
    m = {a: CreateMeeting(title="встреча с командой", date=date(2026, 10, 29), time="15:00"),
         b: CreateMeeting(title="встреча с Петром", date=date(2026, 10, 29), time="15:30")}
    _, repo, _ = run(m, a, b, "да")
    assert len(items(repo)) == 2


def test_slot_answer_keeps_draft_kind():
    a, b = "Напомни завтра позвонить маме", "ну давай где-нибудь в десять утра пожалуй"
    _, repo, (_, r) = run({a: CreateTask(kind="reminder", title="позвонить маме", date=date(2026, 10, 29)),
                           b: CreateTask(date=date(2026, 10, 29), time="10:00")}, a, b)
    assert [i.kind for i in items(repo)] == [ItemKind.REMINDER]


def test_reminder_day_shift_asks_time():
    t = "Напомни через 2 дня позвонить маме"
    m = {t: CreateTask(kind="reminder", title="позвонить маме", date=date(2026, 10, 30)),
         "в 10": CreateTask(date=date(2026, 10, 30), time="10:00")}
    _, repo, (r1, r2) = run(m, t, "в 10")
    assert r1.reply == "Во сколько напомнить?"
    assert items(repo)[0].starts_at == datetime(2026, 10, 30, 10, 0, tzinfo=MSK)


def test_shift_minutes_30_and_48_hours():
    now = dt(10, 28, 21, 54)
    for m, exp in ((1800, dt(10, 30, 3, 54)), (2880, dt(10, 30, 21, 54))):
        t = "напомни"
        _, repo, _ = run({t: CreateTask(kind="reminder", title="позвонить маме", shift_minutes=m)}, t, now=now)
        assert items(repo)[0].starts_at == exp


def test_meeting_duration_minutes():
    t = "встреча"
    _, repo, _ = run({t: CreateMeeting(title="команда", date=date(2026, 10, 29), time="10:00", duration_minutes=120)}, t)
    assert items(repo)[0].ends_at == dt(10, 29, 12)


def test_update_shift_minutes():
    from app.application.intents import Target, UpdateItem
    a, b = "напомни", "через 3 часа"
    now = dt(10, 28, 12)
    _, repo, _ = run({a: CreateTask(kind="reminder", title="выпить воды", date=date(2026, 10, 28), time="18:00"),
                      b: UpdateItem(target=Target(ref="last"), shift_minutes=180)}, a, b, now=now)
    assert items(repo)[0].starts_at == dt(10, 28, 15)


@pytest.mark.parametrize("bad", ["24:30", "29:00", "25:70", "-3:00"])
def test_invalid_time_asks_then_valid_answer_completes(bad):
    t = "напомни завтра позвонить маме"
    m = {t: CreateTask(kind="reminder", title="позвонить маме", date=date(2026, 10, 29), time=bad),
         "в 15": CreateTask(time="15:00")}
    _, repo, (r1, r2) = run(m, t, "в 15")
    assert r1.clarify and r1.reply == "Такого времени нет. Во сколько?"
    assert r2.reply == "Напомню завтра в 15:00: позвонить маме."


def test_invalid_meeting_time_asks():
    t = "Поставь встречу завтра в 24:30"
    _, repo, (r,) = run({t: CreateMeeting(title="встреча", date=date(2026, 10, 29), time="24:30")}, t)
    assert r.clarify and r.reply == "Такого времени нет. Во сколько?" and items(repo) == []


def test_invalid_new_time_asks():
    from app.application.intents import Target, UpdateItem
    _, repo, (_, r) = run({"a": CreateTask(title="x", date=date(2026, 10, 29)),
                           "b": UpdateItem(target=Target(ref="last"), new_time="25:00")}, "a", "b")
    assert r.clarify and r.reply == "Такого времени нет. Во сколько?"


def test_unclear_answers_keep_draft_and_no_rename_from_question():
    from app.application.intents import Target, UpdateItem
    t1 = "Запланируй-ка завтра мне встречу с Ванёчком."
    m = {t1: CreateMeeting(title="встреча с Ванёчком", date=date(2026, 10, 29)),
         "В 26 часов.": CreateMeeting(time="26:00"),
         "Ну тогда на 13:00.": CreateMeeting(time="13:00"),
         "Так ты напиши, с кем встреча, блядь.": UpdateItem(target=Target(ref="last"), title="с кем встреча")}
    svc, repo, rs = run(m, t1, "В 26 часов.", "Да хуй знает, сам поставь.", "Ну тогда на 13:00.",
                        "Так ты напиши, с кем встреча, блядь.")
    assert rs[1].reply == "Такого времени нет. Во сколько?"
    assert rs[2].reply == "Не понял. Такого времени нет. Во сколько? Например: в 15." and rs[2].clarify
    [i] = items(repo)
    assert i.kind == ItemKind.MEETING and i.title == "встреча с Ванёчком"
    assert i.starts_at == datetime(2026, 10, 29, 13, 0, tzinfo=MSK)
    assert rs[4].reply == "Как назвать запись?"


def test_named_time_wins_over_shift():
    t = "через неделю в 15"
    _, repo, _ = run({t: CreateTask(kind="reminder", title="отчёт", date=date(2026, 11, 4), time="15:00", shift_minutes=60)}, t)
    assert items(repo)[0].starts_at == dt(11, 4, 15)


def test_week_ahead_task_has_date_only():
    t = "через неделю сдать отчёт"
    _, repo, _ = run({t: CreateTask(kind="task", title="сдать отчёт", date=date(2026, 11, 4))}, t)
    assert items(repo)[0].due_date == date(2026, 11, 4) and items(repo)[0].starts_at is None


def test_update_named_time_wins_over_shift():
    from app.application.intents import Target, UpdateItem
    a, b = "напомни", "через час в 20"
    _, repo, _ = run({a: CreateTask(kind="reminder", title="выпить воды", date=date(2026, 10, 28), time="18:00"),
                      b: UpdateItem(target=Target(ref="last"), shift_minutes=60, new_time="20:00")}, a, b, now=dt(10, 28, 12))
    assert items(repo)[0].starts_at == dt(10, 28, 20)
