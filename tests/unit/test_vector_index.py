from datetime import date

import pytest

from app.adapters.outbound.sqlite.db import Database
from app.adapters.outbound.sqlite.item_repo import SqliteItemRepository
from app.adapters.outbound.sqlite.vector_index import SqliteVectorIndex
from app.application.ports.outbound import SearchFilters
from app.domain.item import Item, ItemKind, Status
from tests.scenarios.fakes import FakeEmbedder

F = SearchFilters(today=date(2026, 10, 28))


@pytest.fixture
def env():
    db = Database(":memory:")
    e = FakeEmbedder()
    return SqliteItemRepository(db, e), SqliteVectorIndex(db, e, floor=0.5)


def add(repo, title, kind=ItemKind.TASK, d=date(2026, 10, 28), **kw):
    return repo.add(Item(kind=kind, title=title, due_date=d, **kw))


def test_word_match_and_cutoff(env):
    repo, s = env
    a = add(repo, "договор с Вектором")
    add(repo, "купить молоко")
    assert [i.id for i in s.search("default", "договор", F, 5)] == [a.id]
    assert s.search("default", "абракадабра", F, 5) == []


def test_closest_date_breaks_ties(env):
    repo, s = env
    far, near = add(repo, "отчёт", d=date(2026, 1, 1)), add(repo, "отчёт", d=date(2026, 10, 27))
    assert [i.id for i in s.search("default", "отчёт", F, 5)] == [near.id, far.id]


def test_cancelled_hidden_and_user_isolation(env):
    repo, s = env
    add(repo, "ерунда", status=Status.CANCELLED)
    add(repo, "ерунда", user_id="u2")
    assert s.search("default", "ерунда", F, 5) == []
    assert len(s.search("u2", "ерунда", F, 5)) == 1


def test_kind_and_tense_filters(env):
    repo, s = env
    m = add(repo, "синк", ItemKind.MEETING, date(2026, 10, 1))
    add(repo, "синк", ItemKind.TASK, date(2026, 11, 1))
    f = lambda **k: [i.id for i in s.search("default", "синк", SearchFilters(F.today, **k), 5)]
    assert f(kind=ItemKind.MEETING) == [m.id]
    assert f(tense="past") == [m.id]
    assert len(f(tense="future")) == 1 and m.id not in f(tense="future")


def test_future_includes_running_series(env):
    repo, s = env
    add(repo, "планёрка", ItemKind.MEETING, date(2026, 11, 2), rrule="FREQ=WEEKLY;BYDAY=MO")
    assert len(s.search("default", "планёрка", SearchFilters(date(2026, 12, 1), tense="future"), 5)) == 1


def test_embed_missing_backfills(env):
    repo, s = env
    repo.db.conn.execute("INSERT INTO items(kind,title,created_at) VALUES ('task','старая запись','2026-01-01')")
    assert s.search("default", "старая запись", F, 5) == []
    repo.embed_missing(batch=1)
    assert len(s.search("default", "старая запись", F, 5)) == 1


def test_stem_gate_and_semantic_only():
    db, e = Database(":memory:"), FakeEmbedder()
    repo = SqliteItemRepository(db, e)
    a, b = add(repo, "договор с Вектором"), add(repo, "купить молоко")
    # fake scores are 0 for unrelated and ~0.7 for partial overlap: floor -1 lets the gate decide
    idx = lambda **k: SqliteVectorIndex(db, e, floor=-1, **k).search("default", "задача про договоры", F, 5)
    assert [i.id for i in idx()] == [a.id]  # stem "догов" shared; stop words ignored; молоко dropped (< 0.88)
    assert {i.id for i in idx(semantic_only=-1)} == {a.id, b.id}  # score above semantic_only: no stem needed
    assert SqliteVectorIndex(db, e, floor=1.1).search("default", "договор", F, 5) == []


def test_date_range_filter_with_series(env):
    repo, s = env
    a = add(repo, "синк", d=date(2026, 10, 29))
    add(repo, "синк", d=date(2026, 10, 30))
    ser = add(repo, "синк", d=date(2026, 10, 5), rrule="FREQ=WEEKLY;BYDAY=TH")  # 2026-10-29 is Thu
    day = date(2026, 10, 29)
    got = {i.id for i in s.search("default", "синк", SearchFilters(F.today, date_from=day, date_to=day), 5)}
    assert got == {a.id, ser.id}


def test_kind_words_do_not_trigger_semantic_bypass():
    db, e = Database(":memory:"), FakeEmbedder()
    repo, s = SqliteItemRepository(db, e), SqliteVectorIndex(db, e, floor=0.5, semantic_only=0.5)
    add(repo, "Встреча с Анной по интеграции", ItemKind.MEETING)
    assert s.search("default", "встреча с Иннокентием", F, 5) == []
    assert s.search("default", "встреча", F, 5) == []
