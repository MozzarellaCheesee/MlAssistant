import logging
from datetime import datetime, timedelta

from app.adapters.outbound.sqlite.db import Database
from app.adapters.outbound.system_clock import SystemClock
from app.application.dialog import DialogState

log = logging.getLogger(__name__)
PENDING_TTL_SECONDS = 600
STATE_TTL = timedelta(hours=24)


class SqliteDialogStore:
    def __init__(self, db: Database, now=SystemClock().now, pending_ttl: float = PENDING_TTL_SECONDS):
        self.db = db
        self.now = now
        self.pending_ttl = pending_ttl

    def load(self, session_id: str) -> DialogState:
        with self.db.lock:
            row = self.db.conn.execute(
                "SELECT state_json, updated_at FROM dialog_state WHERE session_id=?", (session_id,)
            ).fetchone()
        if not row or datetime.fromisoformat(row[1]) < self.now() - STATE_TTL:
            return DialogState()
        try:
            state = DialogState.model_validate_json(row[0])
        except ValueError:  # ValidationError из pydantic наследует ValueError
            log.warning("bad dialog state for %s, resetting", session_id)
            return DialogState()
        pending = state.pending
        if pending and pending.created_at and pending.created_at < self.now() - timedelta(seconds=self.pending_ttl):
            return state.model_copy(update={"pending": None})
        return state

    def save(self, session_id: str, state: DialogState) -> None:
        now = self.now()
        pending = state.pending
        if pending and not pending.created_at:
            pending = pending.model_copy(update={"created_at": now})
        state = state.model_copy(update={"updated_at": now, "pending": pending})
        with self.db.lock, self.db.conn:
            self.db.conn.execute(
                "INSERT OR REPLACE INTO dialog_state(session_id, state_json, updated_at) VALUES (?,?,?)",
                (session_id, state.model_dump_json(), now.isoformat()),
            )
