import logging
from datetime import datetime

from cryptography.fernet import Fernet, InvalidToken

from app.adapters.outbound.sqlite.db import Database
from app.application.ports.outbound import CalendarLink

log = logging.getLogger(__name__)


class SqliteCalendarLinks:
    def __init__(self, db: Database, key: bytes):
        self.db = db
        self.fernet = Fernet(key)

    def _enc(self, plain: str) -> str:
        return self.fernet.encrypt(plain.encode()).decode()

    def save(self, link: CalendarLink) -> None:
        with self.db.lock, self.db.conn as conn:
            conn.execute(
                "INSERT OR REPLACE INTO calendar_links(user_id, email, token_enc, refresh_enc, expires_at) "
                "VALUES (?,?,?,?,?)",
                (
                    link.user_id,
                    link.email,
                    self._enc(link.access_token),
                    self._enc(link.refresh_token) if link.refresh_token else None,
                    link.expires_at.isoformat(),
                ),
            )

    def get(self, user_id: str) -> CalendarLink | None:
        with self.db.lock, self.db.conn as conn:
            row = conn.execute(
                "SELECT email, token_enc, refresh_enc, expires_at FROM calendar_links WHERE user_id=?", (user_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            return CalendarLink(
                user_id,
                row[0],
                self.fernet.decrypt(row[1].encode()).decode(),
                self.fernet.decrypt(row[2].encode()).decode() if row[2] else None,
                datetime.fromisoformat(row[3]),
            )
        except (InvalidToken, ValueError):
            log.warning("calendar link unreadable user=%s", user_id)
            return None

    def delete(self, user_id: str) -> None:
        with self.db.lock, self.db.conn as conn:
            conn.execute("DELETE FROM calendar_links WHERE user_id=?", (user_id,))
