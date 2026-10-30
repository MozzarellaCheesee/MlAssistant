from datetime import date, datetime

import pytest

from app.adapters.outbound.sqlite.db import Database
from app.adapters.outbound.sqlite.item_repo import SqliteItemRepository
from app.domain.dates import MSK, DateRange
from app.domain.item import ItemKind
from tests.scenarios.fakes import FakeEmbedder
from scripts.import_csv import import_csv, kind_for

CSV = "data/voice_assistant_events_1000.csv"


def test_kind_rules():
    assert kind_for("Созвон с Партнёром по интеграции") == ItemKind.MEETING
    assert kind_for("Демо проекта по CRM") == ItemKind.MEETING
    assert kind_for("Провести планёрку по договору") == ItemKind.MEETING
    assert kind_for("Напомнить Анной про CRM") == ItemKind.REMINDER
    assert kind_for("Подготовиться к встрече с Вектором") == ItemKind.TASK


def test_import_counts_and_is_idempotent():
    db = Database(":memory:")
    repo = SqliteItemRepository(db, FakeEmbedder())
    assert import_csv(repo, db, CSV) == 1000
    assert import_csv(repo, db, CSV) == 1000
    n = lambda w: db.conn.execute(f"SELECT count(*) FROM items {w}").fetchone()[0]
    assert n("") == 1000
    assert db.conn.execute("SELECT count(*) FROM items_vec").fetchone()[0] == 1000
    assert n("WHERE kind='meeting'") == 186
    assert n("WHERE kind='reminder'") == 53


def test_import_times():
    db = Database(":memory:")
    repo = SqliteItemRepository(db, FakeEmbedder())
    import_csv(repo, db, CSV)
    items = [i for i in repo.list_between("default", DateRange(date(2026, 10, 29), date(2026, 10, 29)))
             if i.title == "Созвон с Орбитой по презентации"]
    assert items
    i = items[0]
    assert i.starts_at.date() == date(2026, 10, 29) and i.starts_at.tzinfo is not None
    assert i.starts_at.utcoffset() == datetime(2026, 10, 29, tzinfo=MSK).utcoffset()
    assert i.due_date == date(2026, 10, 29)


def test_if_empty_skips(tmp_path, monkeypatch, capsys):
    from scripts import import_csv as m
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr("sys.argv", ["x", CSV, "--db", db_path, "--if-empty"])
    m.main()
    assert "Imported 1000" in capsys.readouterr().out
    m.main()
    assert "skip" in capsys.readouterr().out


def test_past_reminders_not_due_after_import():
    db = Database(":memory:")
    repo = SqliteItemRepository(db, FakeEmbedder())
    now = datetime(2026, 10, 28, 12, 0, tzinfo=MSK)
    import_csv(repo, db, CSV, now=now)
    assert repo.due_reminders("default", now) == []
    assert db.conn.execute("SELECT count(*) FROM items WHERE kind='reminder' AND notified_at IS NULL").fetchone()[0] > 0
