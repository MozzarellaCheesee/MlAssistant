import ast
from pathlib import Path

BANNED = {"fastapi", "starlette", "gigachat", "onnx_asr", "sqlite3", "pymorphy3", "rapidfuzz", "httpx", "numpy"}
ROOT = Path(__file__).resolve().parents[2] / "app"


def test_core_does_not_import_infrastructure():
    for layer in ("domain", "application"):
        for f in (ROOT / layer).rglob("*.py"):
            for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                if isinstance(n, ast.Import):
                    mods = {a.name.split(".")[0] for a in n.names}
                elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                    mods = {n.module.split(".")[0]}
                else:
                    continue
                assert not mods & BANNED, f"{f}: imports {mods & BANNED}"


def test_every_intent_has_a_handler():
    from typing import get_args

    from app.application.handlers.registry import build_handlers
    from app.application.intents import Intent, Several
    variants = set(get_args(get_args(Intent)[0]))
    assert set(build_handlers(None, None)) == variants - {Several}  # Several раскладывает AssistantService
