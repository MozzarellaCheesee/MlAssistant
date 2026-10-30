from datetime import datetime

from app.adapters.outbound.sqlite.db import Database
from app.adapters.outbound.system_clock import SystemClock
from app.application.ports.outbound import Clock, User


class SqliteUserStore:
    def __init__(self, db: Database, clock: Clock | None = None):
        self.db = db
        self.clock = clock or SystemClock()

    def upsert_user(self, yandex_uid: str, login: str, display_name: str) -> tuple[User, bool]:
        uid = "u" + yandex_uid
        with self.db.lock, self.db.conn as conn:
            created = conn.execute("SELECT 1 FROM users WHERE yandex_uid=?", (yandex_uid,)).fetchone() is None
            if created:
                conn.execute(
                    "INSERT INTO users(id, yandex_uid, login, display_name, created_at) VALUES (?,?,?,?,?)",
                    (uid, yandex_uid, login, display_name, self.clock.now().isoformat()),
                )
            else:
                conn.execute(
                    "UPDATE users SET login=?, display_name=? WHERE yandex_uid=?", (login, display_name, yandex_uid)
                )
        return User(uid, yandex_uid, login, display_name), created

    def save_state(self, state: str, expires_at: datetime, now: datetime) -> None:
        with self.db.lock, self.db.conn as conn:
            conn.execute("DELETE FROM oauth_states WHERE expires_at < ?", (now.isoformat(),))
            conn.execute(
                "INSERT OR REPLACE INTO oauth_states(state, expires_at) VALUES (?,?)", (state, expires_at.isoformat())
            )

    def pop_state(self, state: str, now: datetime) -> bool:
        with self.db.lock, self.db.conn as conn:
            row = conn.execute("SELECT expires_at FROM oauth_states WHERE state=?", (state,)).fetchone()
            conn.execute("DELETE FROM oauth_states WHERE state=?", (state,))
        return row is not None and datetime.fromisoformat(row[0]) >= now

    def create_session(self, token_hash: str, user_id: str, expires_at: datetime, now: datetime) -> None:
        with self.db.lock, self.db.conn as conn:
            conn.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (now.isoformat(),))
            conn.execute(
                "INSERT OR REPLACE INTO auth_sessions(token_hash, user_id, expires_at) VALUES (?,?,?)",
                (token_hash, user_id, expires_at.isoformat()),
            )

    def user_by_session(self, token_hash: str, now: datetime) -> User | None:
        with self.db.lock, self.db.conn as conn:
            row = conn.execute(
                "SELECT u.id, u.yandex_uid, u.login, u.display_name, s.expires_at FROM auth_sessions s "
                "JOIN users u ON u.id=s.user_id WHERE s.token_hash=?",
                (token_hash,),
            ).fetchone()
            if row and datetime.fromisoformat(row[4]) < now:
                conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash,))
                row = None
        return User(*row[:4]) if row else None

    def delete_session(self, token_hash: str) -> None:
        with self.db.lock, self.db.conn as conn:
            conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash,))
