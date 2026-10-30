from datetime import date

import pytest

from app.adapters.outbound.e5_embedder import E5Embedder
from app.adapters.outbound.sqlite.db import Database
from app.adapters.outbound.sqlite.item_repo import SqliteItemRepository
from app.adapters.outbound.sqlite.vector_index import SqliteVectorIndex
from app.application.ports.outbound import SearchFilters
from app.domain.item import Item, ItemKind
from scripts.import_csv import import_csv

pytestmark = pytest.mark.model
F = SearchFilters(today=date(2026, 10, 28))


@pytest.fixture(scope="module")
def index():
    db, e = Database(":memory:"), E5Embedder.load()
    repo = SqliteItemRepository(db, e)
    import_csv(repo, db, "data/voice_assistant_events_1000.csv")
    repo.add(Item(kind=ItemKind.TASK, title="купить билеты в театр", due_date=date(2026, 11, 1)))
    return SqliteVectorIndex(db, e)


def titles(index, q):
    return [i.title for i in index.search("default", q, F, 5)]


def test_meeting_with_ivan(index):
    r = titles(index, "встреча с Иваном")
    assert r and all("Иван" in t for t in r[:3])


def test_synonym(index):
    assert any("договор" in t.lower() for t in titles(index, "соглашение"))


def test_nonsense_is_empty(index):
    assert titles(index, "абракадабра") == []


def test_one_word_query_with_kind(index):
    from app.domain.item import ItemKind
    r = index.search("default", "Иван", SearchFilters(today=F.today, kind=ItemKind.MEETING), 5)
    assert r and any("Иван" in i.title for i in r)
    assert index.search("default", "абракадабра", F, 5) == []


def test_tickets_only_matching(index):
    assert titles(index, "задача про билеты") == ["купить билеты в театр"]


def test_absent_topic_is_empty(index):
    assert titles(index, "поход к стоматологу") == []


def test_agreement_synonym_has_contract_or_agree(index):
    r = titles(index, "соглашение")
    assert r and all("договор" in t.lower() or "соглас" in t.lower() for t in r)


def test_ivan_meetings_only(index):
    r = index.search("default", "Иван", SearchFilters(today=F.today, kind=ItemKind.MEETING), 5)
    assert r and all("Иван" in i.title for i in r)


def test_unknown_name_with_kind_word_finds_nothing(index):
    assert index.search("default", "встреча с Иннокентием", SearchFilters(today=F.today, kind=ItemKind.MEETING), 5) == []
