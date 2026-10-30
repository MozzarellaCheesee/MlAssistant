from datetime import datetime, timedelta

from app.adapters.outbound.sqlite.db import Database
from app.adapters.outbound.sqlite.user_store import SqliteUserStore
from app.domain.dates import MSK

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=MSK)
H = timedelta(hours=1)


def test_upsert_creates_then_updates(tmp_path):
    s = SqliteUserStore(Database(str(tmp_path / "a.db")))
    u, created = s.upsert_user("42", "ivan", "Иван")
    assert created and u.id == "u42"
    u2, created = s.upsert_user("42", "ivan2", "Иван И")
    assert not created and (u2.id, u2.login, u2.display_name) == ("u42", "ivan2", "Иван И")


def test_pop_state_once_and_expiry(tmp_path):
    s = SqliteUserStore(Database(str(tmp_path / "a.db")))
    s.save_state("a", NOW + H, NOW)
    s.save_state("b", NOW - H, NOW)
    assert s.pop_state("a", NOW) and not s.pop_state("a", NOW)
    assert not s.pop_state("b", NOW)


def test_session_lifecycle_and_persistence(tmp_path):
    p = str(tmp_path / "a.db")
    s = SqliteUserStore(Database(p))
    u, _ = s.upsert_user("42", "ivan", "Иван")
    s.create_session("h1", u.id, NOW + H, NOW)
    s.create_session("h2", u.id, NOW - H, NOW)
    s = SqliteUserStore(Database(p))
    assert s.user_by_session("h1", NOW) == u
    assert s.user_by_session("h2", NOW) is None
    s.delete_session("h1")
    assert s.user_by_session("h1", NOW) is None


def test_save_state_purges_expired(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    s = SqliteUserStore(db)
    for i in range(3):
        s.save_state(f"old{i}", NOW - H, NOW)
    s.save_state("new", NOW + H, NOW)
    rows = db.conn.execute("SELECT * FROM oauth_states").fetchall()
    assert len(rows) == 1 and rows[0][0] == "new"


def test_create_session_purges_expired(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    s = SqliteUserStore(db)
    u, _ = s.upsert_user("42", "ivan", "Иван")
    s.create_session("old", u.id, NOW - H, NOW)
    s.create_session("new", u.id, NOW + H, NOW)
    rows = db.conn.execute("SELECT * FROM auth_sessions").fetchall()
    assert len(rows) == 1 and rows[0][0] == "new"
