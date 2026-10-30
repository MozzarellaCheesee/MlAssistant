"""Голос: переключатель Джарвиса сервера и режим без рук с микрофоном браузера (/api/listen)."""

import time
from collections import OrderedDict
from collections.abc import Callable

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.adapters.inbound.http.common import read_audio, reply_to_dict
from app.application.ports.inbound import AssistantPort
from app.domain.wake import WakeSession

VOICE_OFF = {"enabled": False, "state": "off", "detail": "голос не настроен", "wake_word": "", "available": False}
DEFAULT_WAKE_WORD, DEFAULT_SESSION_SILENCE = "Джарвис", 5.0  # для /api/listen, когда голос сервера не настроен
MAX_WAKE_SESSIONS = 200


class VoiceIn(BaseModel):
    enabled: bool | None = None
    volume: float | None = None
    beep_volume: float | None = None


def _clamp_volume(value: float) -> float:
    return min(1.0, max(0.0, value))


def voice_router(service: AssistantPort, current_user: Callable, voice, tts) -> APIRouter:
    router = APIRouter()
    # что видят все, кроме владельца: Джарвис сервера слушает только его микрофон
    foreign = {**VOICE_OFF, "detail": "голосовой режим доступен только владельцу"}
    if voice:
        foreign |= {
            "wake_word": voice.wake_word,
            "phrase_pause": voice.phrase_pause,
            "session_silence": voice.session_silence,
        }

    def with_tts(state: dict) -> dict:
        ready = tts is None or getattr(tts, "ready", True)
        if ready or not voice:
            return {**state, "tts_ready": True} if voice else state
        if state["state"] in ("off", "loading") and state["available"]:  # голос грузится, даже если выключен
            state = {**state, "state": "loading", "detail": "загружаю голос… (около минуты при первом запуске)"}
        return {**state, "tts_ready": False}

    @router.get("/api/voice")
    def voice_state(user: str = Depends(current_user)):
        if not voice:
            return with_tts(VOICE_OFF)
        if user == voice.owner:
            return with_tts(voice.status())
        return with_tts({**foreign, "volume": voice.volume, "beep_volume": voice.beep_volume})

    @router.post("/api/voice")
    def voice_set(body: VoiceIn, user: str = Depends(current_user)):
        if voice and user != voice.owner:
            return JSONResponse({"reply": foreign["detail"], "error": "forbidden"}, status_code=403)
        if not voice:
            return VOICE_OFF
        if body.volume is not None:
            voice.volume = _clamp_volume(body.volume)
        if body.beep_volume is not None:
            voice.beep_volume = _clamp_volume(body.beep_volume)
        if body.enabled is not None:
            return with_tts(voice.set_enabled(body.enabled))
        return with_tts(voice.status())

    # сессии в памяти процесса, 200 последних
    sessions: OrderedDict[tuple, WakeSession] = OrderedDict()

    def wake_session(key: tuple) -> WakeSession:
        session = sessions.pop(key, None)
        if session is None and voice:
            session = WakeSession(voice.wake_word, voice.session_silence)
        elif session is None:
            session = WakeSession(DEFAULT_WAKE_WORD, DEFAULT_SESSION_SILENCE)
        sessions[key] = session
        if len(sessions) > MAX_WAKE_SESSIONS:
            sessions.popitem(last=False)
        return session

    @router.post("/api/listen")
    async def listen(
        request: Request,
        session_id: str = Form(min_length=1, max_length=100),
        file: UploadFile = File(),
        played: float = Form(0, ge=0, le=60),
        user: str = Depends(current_user),
    ):
        request.state.session_id = session_id
        # Намеренно для любого вошедшего: это режим без рук с микрофоном браузера;
        # только Джарвис сервера доступен владельцу
        if voice and user == voice.owner and voice.status()["enabled"]:
            return JSONResponse({"reply": "Джарвис уже слушает микрофон компьютера.", "error": "voice_busy"}, 409)
        data = await read_audio(file)
        text = await run_in_threadpool(service.transcribe, data)
        session = wake_session((user, session_id))
        # played: сколько секунд страница проигрывала прошлый ответ
        command = session.on_transcript(text, time.monotonic() - played)
        if command is None:
            return {"heard": False}
        if session.name_only:
            return {"heard": True, "wake": True, "reply": command}
        result = reply_to_dict(await run_in_threadpool(service.handle_turn, session_id, user, command, "voice"))
        session.on_speech(time.monotonic())
        return result | {"heard": True, "transcript": text, "speech": True}

    return router
