import json
from dataclasses import replace
from datetime import date, datetime, timedelta

import numpy as np

from app.adapters.outbound.sqlite.db import Database
from app.adapters.outbound.system_clock import SystemClock
from app.application.ports.outbound import Clock
from app.domain.dates import MSK, DateRange
from app.domain.item import Item, ItemKind, Status
from app.domain.recurrence import last_due, occurrences

EMBED_BATCH_SIZE = 64


def _iso(value):
    return value.isoformat() if value else None


def _dt(value):
    return datetime.fromisoformat(value) if value else None


def _to_row(item: Item) -> dict:
    due = item.due_date or (item.starts_at.astimezone(MSK).date() if item.starts_at else None)
    return dict(
        user_id=item.user_id,
        kind=item.kind.value,
        title=item.title,
        starts_at=_iso(item.starts_at),
        ends_at=_iso(item.ends_at),
        due_date=_iso(due),
        status=item.status.value,
        created_at=_iso(item.created_at),
        source=item.source,
        source_text=item.source_text,
        notified_at=_iso(item.notified_at),
        rrule=item.rrule,
        exdates=json.dumps([x.isoformat() for x in item.exdates]),
        external_id=item.external_id,
    )


def _from_row(r) -> Item:
    return Item(
        id=r["id"],
        user_id=r["user_id"],
        kind=ItemKind(r["kind"]),
        title=r["title"],
        starts_at=_dt(r["starts_at"]),
        ends_at=_dt(r["ends_at"]),
        due_date=date.fromisoformat(r["due_date"]) if r["due_date"] else None,
        status=Status(r["status"]),
        created_at=_dt(r["created_at"]),
        source=r["source"],
        source_text=r["source_text"],
        notified_at=_dt(r["notified_at"]),
        rrule=r["rrule"],
        exdates=[date.fromisoformat(x) for x in json.loads(r["exdates"] or "[]")],
        external_id=r["external_id"],
    )


def _sort_key(item: Item) -> tuple:
    return item.due_date, item.starts_at is None, item.starts_at, item.id


class SqliteItemRepository:
    def __init__(self, db: Database, embedder, clock: Clock | None = None):
        self.db = db
        self.embedder = embedder
        self.clock = clock or SystemClock()

    def _vec(self, title: str) -> bytes:
        return np.asarray(self.embedder.embed_passages([title])[0], dtype=np.float32).tobytes()

    def embed_missing(self, batch: int = EMBED_BATCH_SIZE) -> None:
        """Считает эмбеддинги для записей без вектора (импорт, уже существующие базы)."""
        with self.db.lock:
            rows = self.db.conn.execute(
                "SELECT id, title FROM items WHERE id NOT IN (SELECT item_id FROM items_vec)"
            ).fetchall()
        for i in range(0, len(rows), batch):
            part = rows[i : i + batch]
            vecs = self.embedder.embed_passages([r["title"] for r in part])
            with self.db.lock, self.db.conn:
                self.db.conn.executemany(
                    "INSERT OR REPLACE INTO items_vec(item_id, vec) VALUES (?,?)",
                    [(r["id"], np.asarray(v, dtype=np.float32).tobytes()) for r, v in zip(part, vecs)],
                )

    def _query(self, sql: str, args: tuple) -> list[Item]:
        with self.db.lock:
            return [_from_row(r) for r in self.db.conn.execute(sql, args).fetchall()]

    # _insert и _update пишут под уже взятым lock внутри открытой транзакции; вектор считается до lock
    def _insert(self, item: Item, vec: bytes | None) -> None:
        item.created_at = item.created_at or self.clock.now()
        row = _to_row(item)
        cur = self.db.conn.execute(
            f"INSERT INTO items({','.join(row)}) VALUES ({','.join('?' * len(row))})", tuple(row.values())
        )
        item.id = cur.lastrowid
        if vec:
            self.db.conn.execute("INSERT INTO items_vec(item_id, vec) VALUES (?,?)", (item.id, vec))

    def _update(self, item: Item, vec: bytes | None) -> None:
        row = _to_row(item)
        cur = self.db.conn.execute(
            f"UPDATE items SET {','.join(f'{k}=?' for k in row)} WHERE id=? AND user_id=?",
            (*row.values(), item.id, item.user_id),
        )
        if cur.rowcount and vec:
            self.db.conn.execute("INSERT OR REPLACE INTO items_vec(item_id, vec) VALUES (?,?)", (item.id, vec))

    def _new_vec(self, item: Item) -> bytes | None:
        """Вектор нужен заново, только если сменилось название."""
        old = self.get(item.user_id, item.id)
        return self._vec(item.title) if old and old.title != item.title else None

    def add(self, item: Item, embed: bool = True) -> Item:
        vec = self._vec(item.title) if embed else None
        with self.db.lock, self.db.conn:
            self._insert(item, vec)
        return item

    def get(self, user_id: str, item_id: int) -> Item | None:
        found = self._query("SELECT * FROM items WHERE user_id=? AND id=?", (user_id, item_id))
        return found[0] if found else None

    def update(self, item: Item) -> None:
        vec = self._new_vec(item)
        with self.db.lock, self.db.conn:
            self._update(item, vec)

    def detach_occurrence(self, series: Item, occurrence: Item) -> Item:
        """Исключение даты из серии и отдельная запись на неё -- одной транзакцией: при сбое повторение не теряется."""
        series_vec, vec = self._new_vec(series), self._vec(occurrence.title)
        with self.db.lock, self.db.conn:
            self._update(series, series_vec)
            self._insert(occurrence, vec)
        return occurrence

    def delete(self, user_id: str, item_id: int) -> None:
        with self.db.lock, self.db.conn:
            self.db.conn.execute(
                "DELETE FROM items_vec WHERE item_id IN (SELECT id FROM items WHERE user_id=? AND id=?)",
                (user_id, item_id),
            )
            self.db.conn.execute("DELETE FROM items WHERE user_id=? AND id=?", (user_id, item_id))

    def list_between(
        self, user_id: str, rng: DateRange, kind: ItemKind | None = None, status: Status | None = None
    ) -> list[Item]:
        sql = (
            "SELECT * FROM items WHERE user_id=? AND ((rrule IS NULL AND due_date BETWEEN ? AND ?) "
            "OR (rrule IS NOT NULL AND due_date <= ?))"
        )
        args = [user_id, rng.start.isoformat(), rng.end.isoformat(), rng.end.isoformat()]
        if kind:
            sql += f" AND kind IN ({','.join('?' * len(kind.matches))})"
            args += kind.matches
        if status:
            sql += " AND status=?"
            args.append(status.value)
        else:
            sql += " AND status != 'cancelled'"
        out = []
        for item in self._query(sql, tuple(args)):
            if not item.rrule:
                out.append(item)
                continue
            for day in occurrences(item.rrule, item.due_date, rng.start, rng.end, item.exdates):
                shift = timedelta(days=(day - item.due_date).days)
                out.append(
                    replace(
                        item,
                        occurrence=day,
                        due_date=day,
                        starts_at=item.starts_at and item.starts_at + shift,
                        ends_at=item.ends_at and item.ends_at + shift,
                    )
                )
        out.sort(key=_sort_key)
        return out

    def overlapping_meetings(self, user_id: str, start: datetime, end: datetime) -> list[Item]:
        day = self.list_between(user_id, DateRange(start.date(), start.date()), ItemKind.MEETING, Status.OPEN)
        return [i for i in day if i.starts_at and i.starts_at < end and i.effective_end() > start]

    def due_reminders(self, user_id: str, now: datetime) -> list[Item]:
        with self.db.lock, self.db.conn:
            rows = self.db.conn.execute(
                "SELECT * FROM items WHERE user_id=? AND kind='reminder' AND status='open' "
                "AND starts_at IS NOT NULL AND (notified_at IS NULL OR rrule IS NOT NULL)",
                (user_id,),
            ).fetchall()
            out = []
            for item in (_from_row(row) for row in rows):
                if item.rrule:
                    item = self._due_occurrence(item, now)
                elif item.starts_at > now:
                    continue
                if item:
                    out.append(item)
            for item in out:
                self.db.conn.execute("UPDATE items SET notified_at=? WHERE id=?", (now.isoformat(), item.id))
                item.notified_at = now
        return out

    @staticmethod
    def _due_occurrence(item: Item, now: datetime) -> Item | None:
        due = last_due(
            item.rrule,
            item.due_date,
            item.starts_at.astimezone(MSK).timetz(),
            item.notified_at or item.created_at,
            now,
            item.exdates,
        )
        return replace(item, occurrence=due.date(), starts_at=due) if due else None
