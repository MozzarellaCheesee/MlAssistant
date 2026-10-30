import os

import pytest
from dotenv import load_dotenv

from app.adapters.outbound.gigachat.client import make_client
from app.adapters.outbound.gigachat.intent_parser import GigaChatIntentParser
from app.application.ports.outbound import ParseContext
from app.config import load_settings
from datetime import datetime, time, timedelta

from app.domain.dates import MSK, parse_date_expr, parse_period_expr
from app.domain.item import Item, ItemKind
from tests.llm_eval.phrases import CASES


@pytest.mark.llm
def test_intent_accuracy():
    load_dotenv()  # ключ из .env, как у сервера
    if not os.getenv("GIGACHAT_CREDENTIALS"):
        pytest.skip("GIGACHAT_CREDENTIALS not set")
    s = load_settings()
    parser = GigaChatIntentParser(make_client(s.gigachat_credentials, s.gigachat_scope, s.gigachat_model_pro, s.certs_path))
    ok = 0
    print("\nфраза | ожидали | получили | ок")
    for phrase, expected, *rest in CASES:
        now = datetime.now(MSK)
        want = dict(rest[0]) if rest else {}
        recent = [Item(kind=ItemKind.MEETING, title="встреча с Иваном", user_id="u", id=1, due_date=now.date() + timedelta(days=1),
                       starts_at=datetime.combine(now.date() + timedelta(days=1), time(10), MSK))] if want.pop("recent", None) else []
        r = parser.parse(phrase, ParseContext(now=now, pending=None, recent=recent))
        if "period" in want:
            want["date_from"], want["date_to"] = parse_period_expr(want.pop("period"), now.date())
        for k in ("date", "new_date"):
            if k in want:
                want[k] = parse_date_expr(want[k], now.date())
        good = r.intent == expected and all(getattr(r, k, None) == v for k, v in want.items())
        ok += good
        print(f"{phrase} | {expected} {want} | {r.intent} {r.model_dump(exclude_none=True)} | {'+' if good else '-'}")
    print(f"accuracy {ok}/{len(CASES)}")
    assert ok / len(CASES) >= 0.9
