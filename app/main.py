import threading

from fastapi import FastAPI

from app.adapters.inbound.http import create_app
from app.adapters.observability import Logged, setup_logging
from app.adapters.outbound.e5_embedder import E5Embedder
from app.adapters.outbound.sqlite.db import Database
from app.bootstrap import LazyTTS, build, build_auth, build_calendar, build_voice
from app.config import load_settings


def create() -> FastAPI:
    """Сборка приложения: uvicorn --factory app.main:create. Импорт модуля ничего не грузит."""
    setup_logging()
    settings = load_settings()
    db = Database(settings.db_path)
    links, calendar_sync = build_calendar(settings, db)
    embedder = E5Embedder.load()
    assistant = build(settings, db=db, embedder=embedder, calendar=calendar_sync)
    tts = Logged(LazyTTS(settings), "tts", {"synthesize"})
    voice = build_voice(settings, assistant, tts)
    app = create_app(
        assistant, build_auth(settings, db, embedder, links), settings.cookie_secure, voice, tts, links=links
    )
    if settings.voice_enabled:  # Vosk может качаться долго: не задерживаем старт
        threading.Thread(target=voice.set_enabled, args=(True,), daemon=True).start()
    if settings.tts_preload:
        # модель грузится ~минуту: не блокируем старт, переключатель и /api/speech ждут ту же загрузку
        tts.warmup()
    return app
