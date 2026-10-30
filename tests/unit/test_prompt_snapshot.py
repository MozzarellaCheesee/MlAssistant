"""Снимок схем функций GigaChat: рефакторинг не должен менять ни слова, ни порядка того, что видит модель."""

import json
from pathlib import Path

from app.adapters.outbound.gigachat.prompts import FUNCTIONS, SPLIT, SYSTEM_PROMPT

SNAPSHOT = Path(__file__).parent / "snapshots" / "gigachat_prompts.json"


def _dump() -> str:
    data = {"functions": FUNCTIONS, "split": SPLIT.model_dump(), "system_prompt": SYSTEM_PROMPT}
    return json.dumps(data, ensure_ascii=False, indent=1) + "\n"


def test_prompts_match_snapshot():
    assert _dump() == SNAPSHOT.read_text(encoding="utf-8")


if __name__ == "__main__":  # перезаписать снимок после осознанной правки промптов
    SNAPSHOT.write_text(_dump(), encoding="utf-8")
