"""HTTP-адаптер: сборка FastAPI из роутеров по областям и общие обработчики ошибок."""

import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app.adapters.inbound.http.auth import login_router, me_router
from app.adapters.inbound.http.chat import chat_router
from app.adapters.inbound.http.common import make_current_user
from app.adapters.inbound.http.items import items_router
from app.adapters.inbound.http.speech import speech_router
from app.adapters.inbound.http.voice import voice_router
from app.adapters.observability import request_id
from app.adapters.outbound.system_clock import SystemClock
from app.application.auth import AuthService
from app.application.errors import AssistantError, ErrorCode, FormError
from app.application.ports.inbound import AssistantPort
from app.application.ports.outbound import CalendarLinks, Clock

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent.parent / "static"
REQUEST_ID_MAX_CHARS = 32
# 200 -- ошибка ассистента, которую страница показывает обычной репликой
HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.LLM_UNAVAILABLE: 503,
    ErrorCode.LLM_QUOTA: 503,
    ErrorCode.LLM_RATE_LIMIT: 503,
    ErrorCode.CERT_MISMATCH: 503,
    ErrorCode.LLM_BAD_RESPONSE: 200,
    ErrorCode.STT_EMPTY: 200,
    ErrorCode.STT_FAILED: 200,
    ErrorCode.CALENDAR_FAILED: 200,
    ErrorCode.AUDIO_TOO_LONG: 413,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.AUTH_FAILED: 400,
}


def create_app(
    service: AssistantPort,
    auth: AuthService | None,
    cookie_secure: bool = True,
    voice=None,
    tts=None,
    links: CalendarLinks | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_):
        yield
        if voice:
            await run_in_threadpool(voice.stop)

    app = FastAPI(lifespan=lifespan)
    app.middleware("http")(_access_log)
    _add_error_handlers(app)
    current_user = make_current_user(auth)
    app.include_router(chat_router(service, current_user))
    app.include_router(voice_router(service, current_user, voice, tts))
    app.include_router(speech_router(service, current_user, tts, clock or SystemClock()))
    app.include_router(items_router(service, current_user, voice))
    app.include_router(me_router(auth, links))
    if auth is not None:
        app.include_router(login_router(auth, cookie_secure))
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True))
    return app


async def _access_log(request: Request, call_next):
    """id запроса попадает в каждую строку лога этого запроса и в заголовок ответа; статика -- на уровне debug."""
    token = request_id.set(request.headers.get("x-request-id", "")[:REQUEST_ID_MAX_CHARS] or secrets.token_hex(4))
    start, status = time.perf_counter(), 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id.get()
        return response
    finally:
        level = logging.INFO if request.url.path.startswith(("/api", "/auth")) else logging.DEBUG
        elapsed_ms = (time.perf_counter() - start) * 1000
        log.log(level, "%s %s %s %.0fms", request.method, request.url.path, status, elapsed_ms)
        request_id.reset(token)


def _add_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AssistantError)
    def _assistant_error(request: Request, e: AssistantError):
        log.warning(
            "session=%s user=%s code=%s detail=%s",
            getattr(request.state, "session_id", None),
            getattr(request.state, "user_id", None),
            e.code,
            e.detail,
        )
        body = {"reply": e.user_message, "error": e.code}
        if ids := getattr(e, "message_ids", None):
            body["message_ids"] = ids
        return JSONResponse(body, HTTP_STATUS[e.code])

    @app.exception_handler(FormError)
    def _form_error(request: Request, e: FormError):
        if e.not_found:
            return JSONResponse({"reply": e.text, "error": "not_found"}, 404)
        return JSONResponse({"reply": e.text, "error": "bad_request"}, 422)

    @app.exception_handler(RequestValidationError)
    def _bad_request(request: Request, e: RequestValidationError):
        return JSONResponse(
            {"reply": "Не получилось разобрать запрос. Проверьте текст и попробуйте ещё раз.", "error": "bad_request"},
            422,
        )

    @app.exception_handler(Exception)
    def _unexpected(request: Request, e: Exception):
        log.error("unhandled error", exc_info=e)
        return JSONResponse({"reply": "Что-то пошло не так. Попробуйте ещё раз.", "error": "internal"}, 500)
