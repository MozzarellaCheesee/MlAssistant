from datetime import datetime

from cryptography.fernet import Fernet

from app.adapters.outbound.sqlite.calendar_links import SqliteCalendarLinks
from app.adapters.outbound.sqlite.db import Database
from app.application.ports.outbound import CalendarLink
from app.domain.dates import MSK

EXP = datetime(2026, 11, 1, 12, 0, tzinfo=MSK)


def mk(uid="u1", tok="ACCESS-SECRET", ref="REFRESH-SECRET"):
    return CalendarLink(uid, "a@yandex.ru", tok, ref, EXP)


def test_roundtrip_and_no_plaintext(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    s = SqliteCalendarLinks(db, Fernet.generate_key())
    s.save(mk())
    assert s.get("u1") == mk()
    raw = str(db.conn.execute("SELECT * FROM calendar_links").fetchone()[:])
    assert "ACCESS-SECRET" not in raw and "REFRESH-SECRET" not in raw
    s.save(mk(ref=None))
    assert s.get("u1").refresh_token is None


def test_wrong_key_none(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    SqliteCalendarLinks(db, Fernet.generate_key()).save(mk())
    assert SqliteCalendarLinks(db, Fernet.generate_key()).get("u1") is None


def test_upsert_isolation_delete(tmp_path):
    s = SqliteCalendarLinks(Database(str(tmp_path / "t.db")), Fernet.generate_key())
    s.save(mk(tok="one"))
    s.save(mk(tok="two"))
    s.save(mk("u2", tok="other"))
    assert s.get("u1").access_token == "two" and s.get("u2").access_token == "other"
    s.delete("u1")
    assert s.get("u1") is None and s.get("u2")
