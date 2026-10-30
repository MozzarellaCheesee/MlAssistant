import re
import zlib
from datetime import datetime

import numpy as np

from app.adapters.outbound.sqlite.db import Database
from app.adapters.outbound.sqlite.dialog_store import SqliteDialogStore
from app.adapters.outbound.sqlite.message_log import SqliteMessageLog
from app.adapters.outbound.sqlite.item_repo import SqliteItemRepository
from app.application.assistant import AssistantService
from app.adapters.outbound.sqlite.vector_index import SqliteVectorIndex
from scripts.import_csv import import_csv
from app.application.handlers.registry import build_handlers
from app.application.intents import Intent, Unknown
from app.domain.dates import MSK


def dt(mo, d, h=0, mi=0):
    return datetime(2026, mo, d, h, mi, tzinfo=MSK)


class FakeEmbedder:
    """Bag of words hashed into a normalized vector; no prefixes, no morphology."""

    def _v(self, text):
        v = np.zeros(256)
        for w in re.findall(r"\w+", text.lower()):
            v[zlib.crc32(w.encode()) % 256] += 1
        return (v / (np.linalg.norm(v) or 1)).tolist()

    def embed_passages(self, texts):
        return [self._v(t) for t in texts]

    def embed_query(self, text):
        return self._v(text)


class FakeParser:
    def __init__(self, mapping: dict[str, Intent | Exception]):
        self.mapping = {k.lower(): v for k, v in mapping.items()}
        self.last_ctx = None

    def parse(self, text, ctx):
        self.last_ctx = ctx
        v = self.mapping.get(text.lower(), Unknown())
        if isinstance(v, Exception):
            raise v
        return v


class FixedClock:
    def __init__(self, dt: datetime):
        self.dt = dt

    def now(self) -> datetime:
        return self.dt


def build_service(parser, now=datetime(2026, 10, 28, 12, 0, tzinfo=MSK), csv: bool = False, floor=0.5, calendar=None):
    db = Database(":memory:")
    emb = FakeEmbedder()
    clock = FixedClock(now)
    repo = SqliteItemRepository(db, emb, clock)
    if csv:
        import_csv(repo, db, "data/voice_assistant_events_1000.csv", "u")
    search = SqliteVectorIndex(db, emb, floor=floor)
    handlers = build_handlers(repo, search, calendar)
    return AssistantService(parser, None, repo, SqliteDialogStore(db, clock.now), clock, handlers, SqliteMessageLog(db, clock),
                            calendar), repo
