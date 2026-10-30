import argparse
import csv
import os
from datetime import date, datetime, time

from dotenv import load_dotenv

from app.adapters.outbound.e5_embedder import E5Embedder
from app.adapters.outbound.sqlite.db import Database
from app.adapters.outbound.sqlite.item_repo import SqliteItemRepository
from app.adapters.outbound.system_clock import SystemClock
from app.domain.dates import MSK
from app.domain.item import Item, ItemKind


def kind_for(text: str) -> ItemKind:
    t = text.lower()
    first = t.split()[0] if t.split() else ""
    if first in ("встреча", "созвон", "демо") or "планёрк" in t or "планерк" in t:
        return ItemKind.MEETING
    if first == "напомнить":
        return ItemKind.REMINDER
    return ItemKind.TASK


def import_csv(
    repo: SqliteItemRepository, db: Database, path: str, user_id: str = "default", now: datetime | None = None
) -> int:
    now = now or SystemClock().now()
    with db.lock, db.conn:
        db.conn.execute(
            "DELETE FROM items_vec WHERE item_id IN (SELECT id FROM items WHERE user_id=? AND source='import')",
            (user_id,),
        )
        db.conn.execute("DELETE FROM items WHERE user_id=? AND source='import'", (user_id,))
    n = 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            d = date.fromisoformat(r["date"])
            kind, at = kind_for(r["text"]), datetime.combine(d, time.fromisoformat(r["time"]), tzinfo=MSK)
            # прошедшие напоминания не должны сработать разом при первом открытии
            repo.add(
                Item(
                    kind=kind,
                    title=r["text"],
                    user_id=user_id,
                    source="import",
                    starts_at=at,
                    due_date=d,
                    notified_at=at if kind == ItemKind.REMINDER and at < now else None,
                ),
                embed=False,
            )
            n += 1
    repo.embed_missing()
    return n


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("path", nargs="?", default="data/voice_assistant_events_1000.csv")
    p.add_argument("--db", default=os.environ.get("DB_PATH", "./assistant.db"))
    p.add_argument("--if-empty", action="store_true", help="import only if items table is empty")
    a = p.parse_args()
    db = Database(a.db)
    if a.if_empty and db.conn.execute("SELECT count(*) FROM items").fetchone()[0]:
        print("items not empty, skip import")
        return
    print(f"Imported {import_csv(SqliteItemRepository(db, E5Embedder.load()), db, a.path)} items")


if __name__ == "__main__":
    main()
