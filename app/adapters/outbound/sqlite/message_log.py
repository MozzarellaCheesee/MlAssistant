import json
from dataclasses import asdict

from app.adapters.outbound.sqlite.db import Database
from app.adapters.outbound.system_clock import SystemClock
from app.application.ports.outbound import Clock
from app.domain.item import Item

DEFAULT_LIMIT = 100


def _isoformat(value):
    return value.isoformat()


class SqliteMessageLog:
    def __init__(self, db: Database, clock: Clock | None = None):
        self.db = db
        self.clock = clock or SystemClock()

    def add(self, user_id: str, role: str, text: str, source: str, items: list[Item] | None = None) -> int:
        items_json = json.dumps([asdict(i) for i in items], default=_isoformat) if items else None
        with self.db.lock, self.db.conn:
            return self.db.conn.execute(
                "INSERT INTO messages(user_id, role, text, source, items_json, created_at) VALUES (?,?,?,?,?,?)",
                (user_id, role, text, source, items_json, self.clock.now().isoformat()),
            ).lastrowid

    def since(self, user_id: str, after_id: int, limit: int = DEFAULT_LIMIT) -> list[dict]:
        # последние limit записей новее after_id, по возрастанию id
        with self.db.lock:
            rows = self.db.conn.execute(
                "SELECT id, role, text, source, items_json, created_at FROM messages WHERE user_id=? AND id>? "
                "ORDER BY id DESC LIMIT ?",
                (user_id, after_id, limit),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "role": row["role"],
                "text": row["text"],
                "source": row["source"],
                "items": json.loads(row["items_json"] or "[]"),
                "created_at": row["created_at"],
            }
            for row in reversed(rows)
        ]

    def get(self, user_id: str, message_id: int) -> dict | None:
        with self.db.lock:
            row = self.db.conn.execute(
                "SELECT id, role, text, source, created_at FROM messages WHERE user_id=? AND id=?",
                (user_id, message_id),
            ).fetchone()
        return dict(row) if row else None
