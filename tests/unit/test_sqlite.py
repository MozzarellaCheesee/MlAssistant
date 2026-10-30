import threading
from datetime import date, datetime, timedelta

import pytest

from app.adapters.outbound.sqlite.db import Database
from app.adapters.outbound.sqlite.dialog_store import SqliteDialogStore
from app.application.dialog import DialogState
from app.application.intents import CreateMeeting
from app.application.results import Question
from app.adapters.outbound.sqlite.item_repo import SqliteItemRepository
from app.domain.dates import MSK, DateRange
from app.domain.item import Item, ItemKind, Status
from tests.scenarios.fakes import FakeEmbedder


@pytest.fixture
def db():
    return Database(":memory:")


@pytest.fixture
def repo(db):
    return SqliteItemRepository(db, FakeEmbedder())


def at(d, h, m=0):
    return datetime(2026, 10, d, h, m, tzinfo=MSK)


def test_add_and_get_roundtrip(repo):
    it = repo.add(Item(kind=ItemKind.MEETING, title="Встреча", starts_at=at(28, 15), ends_at=at(28, 16),
                       due_date=date(2026, 10, 28), source_text="x", rrule="FREQ=DAILY",
                       exdates=[date(2026, 10, 30)], external_id="u1", created_at=at(1, 1)))
    assert it.id
    assert repo.get("default", it.id) == it


def test_get_other_user_returns_none(repo):
    it = repo.add(Item(kind=ItemKind.TASK, title="a"))
    assert repo.get("other", it.id) is None


def test_list_between_filters_user_kind_and_cancelled(repo):
    d = date(2026, 10, 28)
    repo.add(Item(kind=ItemKind.TASK, title="чужая", user_id="u2", due_date=d))
    repo.add(Item(kind=ItemKind.TASK, title="другой день", due_date=date(2026, 10, 29)))
    repo.add(Item(kind=ItemKind.TASK, title="отмена", due_date=d, status=Status.CANCELLED))
    ok = repo.add(Item(kind=ItemKind.TASK, title="ок", due_date=d))
    repo.add(Item(kind=ItemKind.MEETING, title="встреча", starts_at=at(28, 10)))
    rng = DateRange(d, d)
    assert [i.title for i in repo.list_between("default", rng, ItemKind.TASK, None)] == ["ок"]
    assert ok.id and [i.title for i in repo.list_between("default", rng, ItemKind.MEETING, None)] == ["встреча"]


def test_list_between_uses_moscow_date(repo):
    repo.add(Item(kind=ItemKind.MEETING, title="ночь", starts_at=datetime.fromisoformat("2026-10-28T00:30+03:00")))
    assert len(repo.list_between("default", DateRange(date(2026, 10, 28), date(2026, 10, 28)), None, None)) == 1


def test_overlapping_meetings(repo):
    repo.add(Item(kind=ItemKind.MEETING, title="m", starts_at=at(28, 15)))
    assert len(repo.overlapping_meetings("default", at(28, 15, 30), at(28, 16, 30))) == 1
    assert repo.overlapping_meetings("default", at(28, 16), at(28, 17)) == []


def test_due_reminders_marks_notified(repo):
    repo.add(Item(kind=ItemKind.REMINDER, title="r", starts_at=at(28, 11, 50)))
    assert len(repo.due_reminders("default", at(28, 12))) == 1
    assert repo.due_reminders("default", at(28, 12)) == []


def _vec(db, i):
    r = db.conn.execute("SELECT vec FROM items_vec WHERE item_id=?", (i,)).fetchone()
    return r and r[0]


def test_update_foreign_user_is_noop(repo, db):
    it = repo.add(Item(kind=ItemKind.TASK, title="купить молоко"))
    before = _vec(db, it.id)
    it.title, it.user_id = "позвонить маме", "other"
    repo.update(it)
    assert repo.get("default", it.id).title == "купить молоко"
    assert _vec(db, it.id) == before


def test_vector_follows_title_and_delete(repo, db):
    it = repo.add(Item(kind=ItemKind.TASK, title="купить молоко"))
    v = _vec(db, it.id)
    assert v
    it.title = "позвонить маме"
    repo.update(it)
    assert _vec(db, it.id) not in (None, v)
    repo.delete("default", it.id)
    assert _vec(db, it.id) is None


def test_concurrent_adds(repo, db):
    def work():
        for _ in range(25):
            repo.add(Item(kind=ItemKind.TASK, title="t"))
    ts = [threading.Thread(target=work) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert db.conn.execute("SELECT count(*) FROM items").fetchone()[0] == 100


def test_dialog_store_roundtrip(db):
    s = SqliteDialogStore(db)
    s.save("s", DialogState(last_referenced_ids=[1]))
    assert s.load("s").last_referenced_ids == [1]
    assert s.load("x") == DialogState()


def test_dialog_store_garbage_row_gives_empty_state(db):
    s = SqliteDialogStore(db)
    s.save("g", DialogState())
    with db.lock, db.conn:
        db.conn.execute("UPDATE dialog_state SET state_json=? WHERE session_id=?", ('{"pending": 5}', "g"))
    assert s.load("g") == DialogState()


def test_dialog_store_pending_roundtrip(db):
    s = SqliteDialogStore(db)
    m = CreateMeeting(title="встреча", starts_at=datetime(2026, 10, 29, 15, 0, tzinfo=MSK))
    s.save("p", DialogState().ask(m, Question(text="?", awaiting="confirm"), set()))
    assert s.load("p").pending_intent() == m


def _series(repo, **kw):
    return repo.add(Item(kind=kw.pop("kind", ItemKind.MEETING), title="s", starts_at=datetime(2026, 11, 2, 9, tzinfo=MSK),
                         due_date=date(2026, 11, 2), rrule=kw.pop("rrule", "FREQ=WEEKLY;BYDAY=MO"),
                         created_at=kw.pop("created_at", at(1, 1)), **kw))


def test_series_expanded_in_range(repo):
    s = _series(repo)
    r = repo.list_between("default", DateRange(date(2026, 11, 1), date(2026, 11, 15)))
    assert [(i.occurrence, i.starts_at.day, i.starts_at.hour, i.id) for i in r] == [
        (date(2026, 11, 2), 2, 9, s.id), (date(2026, 11, 9), 9, 9, s.id)]


def test_series_exdate_hidden(repo):
    _series(repo, exdates=[date(2026, 11, 9)])
    assert len(repo.list_between("default", DateRange(date(2026, 11, 1), date(2026, 11, 15)))) == 1


def test_series_not_before_start(repo):
    _series(repo)
    assert repo.list_between("default", DateRange(date(2026, 10, 1), date(2026, 10, 31))) == []


def test_overlap_with_series(repo):
    _series(repo)
    d = datetime(2026, 11, 9, tzinfo=MSK)
    assert len(repo.overlapping_meetings("default", d.replace(hour=9, minute=30), d.replace(hour=10, minute=30))) == 1


def _rem(repo, **kw):
    return repo.add(Item(kind=ItemKind.REMINDER, title="r", starts_at=at(28, 10), due_date=date(2026, 10, 28),
                         rrule="FREQ=DAILY", created_at=at(28, 9), **kw))


def test_recurring_reminder_fires_once_per_occurrence(repo):
    _rem(repo)
    assert len(repo.due_reminders("default", at(28, 10, 1))) == 1
    assert repo.due_reminders("default", at(28, 10, 1)) == []
    r = repo.due_reminders("default", at(29, 10, 5))
    assert len(r) == 1 and r[0].occurrence == date(2026, 10, 29)
    assert len(repo.due_reminders("default", at(31, 12))) == 1


def test_recurring_reminder_exdate(repo):
    _rem(repo, exdates=[date(2026, 10, 28)])
    assert repo.due_reminders("default", at(28, 10, 1)) == []


def test_detach_occurrence_is_atomic(repo, monkeypatch):
    s = repo.add(Item(kind=ItemKind.MEETING, title="планёрка", starts_at=at(26, 9), due_date=date(2026, 10, 26),
                      rrule="FREQ=WEEKLY;BYDAY=MO"))
    one = Item(kind=ItemKind.MEETING, title="планёрка", starts_at=datetime(2026, 11, 2, 11, tzinfo=MSK),
               due_date=date(2026, 11, 2))
    s.exdates.append(date(2026, 11, 2))

    def boom(item, vec):
        raise RuntimeError("insert failed")
    monkeypatch.setattr(repo, "_insert", boom)
    with pytest.raises(RuntimeError):
        repo.detach_occurrence(s, one)
    assert repo.get("default", s.id).exdates == []  # серия не изменилась: повторение не потерялось
    monkeypatch.undo()
    new = repo.detach_occurrence(s, one)
    assert new.id and repo.get("default", s.id).exdates == [date(2026, 11, 2)]
