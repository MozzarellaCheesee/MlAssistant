"""Озвучка: ответ ассистента по id сообщения и несколько фиксированных фраз страницы."""

import logging
import threading
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from app.application.ports.inbound import AssistantPort
from app.application.ports.outbound import Clock

log = logging.getLogger(__name__)
SPEECH_MAX_AGE = timedelta(minutes=10)
SPEECH_MAX_CHARS = 500
SPEECH_CACHE_SIZE = 32
SPEAK_PHRASES = {"Слушаю", "Не удалось распознать речь.", "Что-то пошло не так."}
TTS_UNAVAILABLE = {"reply": "Озвучка недоступна.", "error": "tts_unavailable"}


class SpeakIn(BaseModel):
    text: str


def speech_router(service: AssistantPort, current_user: Callable, tts, clock: Clock) -> APIRouter:
    router = APIRouter()
    cache_lock = threading.Lock()
    # LRU на 32 ответа в памяти процесса
    cache: OrderedDict[int, bytes] = OrderedDict()
    # кэш фиксированных фраз без вытеснения, их мало
    phrases: dict[str, bytes] = {}

    def cached(message_id: int) -> bytes | None:
        with cache_lock:
            wav = cache.get(message_id)
            if wav is not None:
                cache.move_to_end(message_id)
        return wav

    def remember(message_id: int, wav: bytes) -> None:
        with cache_lock:
            cache[message_id] = wav
            if len(cache) > SPEECH_CACHE_SIZE:
                cache.popitem(last=False)

    def synthesize(text: str) -> bytes:
        if not tts:
            raise RuntimeError("no tts")
        return tts.synthesize(text)

    @router.get("/api/speech/{message_id}")
    def speech(message_id: int, user: str = Depends(current_user)):
        message = service.message(user, message_id)
        if not message or message["role"] != "assistant":
            return JSONResponse({"reply": "Сообщение не найдено.", "error": "not_found"}, 404)
        wav = cached(message_id)
        if wav is not None:
            return Response(wav, media_type="audio/wav")
        if clock.now() - datetime.fromisoformat(message["created_at"]) > SPEECH_MAX_AGE:
            return JSONResponse({"reply": "Сообщение слишком старое для озвучки.", "error": "not_found"}, 404)
        if len(message["text"]) > SPEECH_MAX_CHARS:
            return JSONResponse({"reply": "Текст слишком длинный для озвучки.", "error": "too_long"}, 413)
        try:
            wav = synthesize(message["text"])
        except Exception:
            log.exception("speech failed")
            return JSONResponse(TTS_UNAVAILABLE, 503)
        remember(message_id, wav)
        return Response(wav, media_type="audio/wav")

    @router.post("/api/speak-text")
    def speak_text(body: SpeakIn, user: str = Depends(current_user)):
        if body.text not in SPEAK_PHRASES:
            return JSONResponse({"reply": "Эту фразу озвучить нельзя.", "error": "bad_request"}, 422)
        try:
            if body.text not in phrases:
                phrases[body.text] = synthesize(body.text)
        except Exception:
            log.exception("speak-text failed")
            return JSONResponse(TTS_UNAVAILABLE, 503)
        return Response(phrases[body.text], media_type="audio/wav")

    return router
