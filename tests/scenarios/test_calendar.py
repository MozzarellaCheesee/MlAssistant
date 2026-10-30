from datetime import date, datetime

from app.application.errors import AssistantError, ErrorCode
from app.application.intents import CreateMeeting, CreateTask, DeleteItem, Target, UpdateItem
from app.domain.dates import MSK
from tests.scenarios.fakes import FakeParser, build_service, dt
from app.domain.item import Item, ItemKind
from tests.scenarios.test_modify import all_items

WARN = "Запись сохранена, но в календарь не попала."
LAST = Target(ref="last")
T = date(2026, 10, 29)


class FakeCal:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def push(self, item):
        self.calls.append(("push", item.title, item.external_id, list(item.exdates)))
        if self.fail:
            raise AssistantError(ErrorCode.CALENDAR_FAILED)
        return item.external_id or f"uid{len(self.calls)}"

    def remove(self, item):
        self.calls.append(("remove", item.title, item.external_id))
        if self.fail:
            raise AssistantError(ErrorCode.CALENDAR_FAILED)


def run(m, *texts, cal=None):
    cal = cal or FakeCal()
    svc, repo = build_service(FakeParser(m), calendar=cal)
    return cal, repo, [svc.handle_turn("s", "u", t).reply for t in texts]


def test_create_move_delete_meeting():
    m = {"a": CreateMeeting(title="планёрка", date=T, time="10:00"), "b": UpdateItem(target=LAST, new_time="11:00"),
         "c": DeleteItem(target=LAST)}
    cal, repo, _ = run(m, "a", "b", "c")
    assert cal.calls == [("push", "планёрка", None, []), ("push", "планёрка", "uid1", []), ("remove", "планёрка", "uid1")]


def test_external_id_saved():
    cal, repo, _ = run({"a": CreateMeeting(title="планёрка", date=T, time="10:00")}, "a")
    assert all_items(repo)[0].external_id == "uid1"


def test_timed_reminder_exported_task_not():
    m = {"a": CreateTask(kind="reminder", title="позвонить", date=T, time="10:00"), "b": CreateTask(title="хлеб", date=T)}
    cal, _, _ = run(m, "a", "b")
    assert cal.calls == [("push", "позвонить", None, [])]


def test_meeting_reminder_is_synced_separately():
    m = {"a": CreateMeeting(title="планёрка", date=T, time="10:00"), "b": UpdateItem(target=LAST, remind=True, new_time="09:00")}
    cal, _, _ = run(m, "a", "b")
    assert [c[:3] for c in cal.calls] == [("push", "планёрка", None), ("push", "планёрка", None)]
    assert cal.calls[1][2] is None  # новая запись, а не перезапись встречи


def test_one_occurrence_pushes_series_with_exdate_and_copy():
    m = {"a": CreateMeeting(title="планёрка", date=date(2026, 10, 28), time="9:00", rrule="FREQ=WEEKLY;BYDAY=MO"),
         "b": UpdateItem(target=LAST, new_time="11:00", scope="one")}
    cal, _, _ = run(m, "a", "b")
    assert cal.calls[1][2] == "uid1" and cal.calls[1][3] == [date(2026, 11, 2)]
    assert cal.calls[2][2] is None


def test_delete_one_occurrence_pushes_series():
    m = {"a": CreateMeeting(title="планёрка", date=date(2026, 10, 28), time="9:00", rrule="FREQ=WEEKLY;BYDAY=MO"),
         "b": DeleteItem(target=LAST, scope="one")}
    cal, _, _ = run(m, "a", "b")
    assert cal.calls[1] == ("push", "планёрка", "uid1", [date(2026, 11, 2)])


def test_failure_keeps_item_and_warns():
    cal, repo, (r,) = run({"a": CreateMeeting(title="планёрка", date=T, time="10:00")}, "a", cal=FakeCal(fail=True))
    assert r.endswith(" " + WARN) and len(all_items(repo)) == 1


def test_no_calendar_no_warning():
    svc, repo = build_service(FakeParser({"a": CreateMeeting(title="планёрка", date=T, time="10:00")}))
    assert WARN not in svc.handle_turn("s", "u", "a").reply


def test_not_connected_calendar_is_silent():
    from app.application.handlers.calendar import sync_item
    from app.application.errors import AssistantError, ErrorCode

    class NoLink:
        def push(self, item):
            raise AssistantError(ErrorCode.CALENDAR_FAILED, "no link")

    item = Item(kind=ItemKind.MEETING, title="встреча", user_id="u", starts_at=dt(10, 29, 10))
    assert sync_item(NoLink(), None, item) == ""


def test_form_edit_meeting_syncs_calendar():
    cal = FakeCal()
    svc, repo = build_service(FakeParser({}), calendar=cal)
    m = svc.create_from_form("u", ItemKind.MEETING, "планёрка", T, "10:00", None)
    m = svc.update_from_form("u", m.id, None, None, None, "11:00")
    assert m.starts_at.hour == 11 and cal.calls == [("push", "планёрка", None, []), ("push", "планёрка", "uid1", [])]
    svc.delete_item("u", m.id)
    assert cal.calls[-1] == ("remove", "планёрка", "uid1")


def test_complete_one_occurrence_pushes_series():
    from app.application.intents import CompleteItem
    m = {"a": CreateMeeting(title="планёрка", date=date(2026, 10, 28), time="9:00", rrule="FREQ=WEEKLY;BYDAY=MO"),
         "b": CompleteItem(target=LAST, scope="one")}
    cal, _, _ = run(m, "a", "b")
    assert cal.calls[1] == ("push", "планёрка", "uid1", [date(2026, 11, 2)])


def test_shift_whole_series_same_day_ok_other_day_refused():
    m = {"a": CreateMeeting(title="планёрка", date=date(2026, 10, 28), time="9:00", rrule="FREQ=WEEKLY;BYDAY=MO"),
         "b": UpdateItem(target=LAST, delay_minutes=60, scope="all"),
         "c": UpdateItem(target=LAST, delay_days=1, scope="all")}
    _, repo, (_, r1, r2) = run(m, "a", "b", "c")
    assert r1.startswith("Перенёс всю серию") and all_items(repo)[0].starts_at.hour == 10
    assert r2.startswith("Всю серию на другой день")


def test_reschedule_past_and_conflict_need_confirm():
    m = {"a": CreateMeeting(title="планёрка", date=T, time="10:00"), "b": CreateMeeting(title="обед", date=T, time="13:00"),
         "p": UpdateItem(target=Target(query="обед"), new_time="11:00", new_date=date(2026, 10, 27)),
         "c": UpdateItem(target=Target(query="обед"), new_time="10:30", new_date=T)}
    _, repo, (_, _, p, yes, c, yes2) = run(m, "a", "b", "p", "да", "c", "да")
    assert p.startswith("Это время уже прошло") and yes.startswith("Перенёс")
    assert c.startswith("С 10:00 до 11:00 уже планёрка") and yes2.startswith("Перенёс")


def test_complete_whole_series_removes_from_calendar():
    from app.application.intents import CompleteItem
    m = {"a": CreateMeeting(title="планёрка", date=date(2026, 10, 28), time="9:00", rrule="FREQ=WEEKLY;BYDAY=MO"),
         "b": CompleteItem(target=LAST, scope="all")}
    cal, _, _ = run(m, "a", "b")
    assert cal.calls[-1] == ("remove", "планёрка", "uid1")
