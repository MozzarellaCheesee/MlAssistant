import re

import numpy as np

from app.adapters.outbound.sqlite.db import Database
from app.adapters.outbound.sqlite.item_repo import _from_row
from app.application.ports.outbound import Embedder, SearchFilters
from app.domain.item import Item
from app.domain.recurrence import occurrences

NO_DUE_DATE_DISTANCE = 10**6  # записи без даты уходят в конец выдачи
_STOP = {
    "задача",
    "задачу",
    "задачи",
    "встреча",
    "встречу",
    "встречи",
    "созвон",
    "напоминание",
    "найди",
    "найти",
    "про",
    "по",
    "с",
    "со",
    "у",
    "меня",
    "когда",
    "была",
    "был",
    "я",
    "ставил",
    "мои",
    "мой",
}


def _stems(text: str) -> set[str]:
    # префиксная псевдо-основа вместо морфологии; pymorphy/Natasha — если понадобится точнее
    return {w[:5] for w in re.findall(r"[а-яa-z]+", text.lower().replace("ё", "е")) if len(w) >= 3}


def _in_range(item: Item, start, end) -> bool:
    day = item.day()
    if not day:
        return False
    if item.rrule:
        return bool(occurrences(item.rrule, day, start, end, item.exdates))
    return start <= day <= end


def _share(left: set[str], right: set[str]) -> bool:
    return any(x.startswith(y) or y.startswith(x) for x in left for y in right)  # «Иван» ~ «Иваном»


def _rank(hit) -> tuple:
    return hit[:2]


class SqliteVectorIndex:
    def __init__(self, db: Database, embedder: Embedder, floor: float = 0.80, semantic_only: float = 0.88):
        self.db = db
        self.embedder = embedder
        self.floor = floor
        self.semantic_only = semantic_only

    def search(self, user_id: str, query: str, filters: SearchFilters, limit: int) -> list[Item]:
        sql, args = "i.user_id=? AND i.status != 'cancelled'", [user_id]
        if filters.kind:
            sql += f" AND i.kind IN ({','.join('?' * len(filters.kind.matches))})"
            args += filters.kind.matches
        if filters.tense == "past":
            sql += " AND i.due_date < ?"
            args.append(filters.today.isoformat())
        elif filters.tense == "future":
            sql += " AND (i.due_date >= ? OR i.rrule IS NOT NULL)"
            args.append(filters.today.isoformat())
        query_vec = np.asarray(self.embedder.embed_query(query), dtype=np.float32)
        with self.db.lock:
            rows = self.db.conn.execute(
                f"SELECT i.*, v.vec FROM items i JOIN items_vec v ON v.item_id=i.id WHERE {sql}", args
            ).fetchall()
        if not rows:
            return []
        # полный скан и матричное умножение, до ~100k записей хватает; дальше ANN-индекс
        scores = np.frombuffer(b"".join(r["vec"] for r in rows), dtype=np.float32).reshape(len(rows), -1) @ query_vec
        core = " ".join(w for w in re.findall(r"\w+", query.lower()) if w not in _STOP)
        query_stems = _stems(core)
        # слова-типы записи доминируют в эмбеддинге: обход «только семантика» оценивает очищенный запрос
        core_scores = (
            np.frombuffer(b"".join(r["vec"] for r in rows), dtype=np.float32).reshape(len(rows), -1)
            @ np.asarray(self.embedder.embed_query(core), dtype=np.float32)
            if core
            else np.zeros(len(rows))
        )
        hits = [
            (-float(score), abs((item.due_date - filters.today).days) if item.due_date else NO_DUE_DATE_DISTANCE, item)
            for score, core_score, row in zip(scores, core_scores, rows)
            if score >= self.floor
            and (item := _from_row(row))
            and (core_score >= self.semantic_only or _share(query_stems, _stems(item.title)))
        ]
        hits.sort(key=_rank)
        if filters.date_from or filters.date_to:
            start, end = filters.date_from or filters.date_to, filters.date_to or filters.date_from
            hits = [hit for hit in hits if _in_range(hit[2], start, end)]
        return [hit[2] for hit in hits[:limit]]
