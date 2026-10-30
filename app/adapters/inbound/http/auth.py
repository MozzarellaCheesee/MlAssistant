"""Вход через Яндекс ID: /api/me и, если вход настроен, /auth/login, /auth/callback, /auth/logout."""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from app.adapters.inbound.http.common import DEFAULT_USER
from app.application.auth import AuthService
from app.application.errors import AssistantError, ErrorCode
from app.application.ports.outbound import CalendarLinks

log = logging.getLogger(__name__)
OAUTH_STATE_MAX_AGE = 600  # секунд на возврат с Яндекса
SESSION_MAX_AGE = 30 * 86400  # cookie sid живёт 30 дней


def me_router(auth: AuthService | None, links: CalendarLinks | None) -> APIRouter:
    router = APIRouter()

    @router.get("/api/me")
    def me(request: Request):
        if auth is None:
            return {"user_id": DEFAULT_USER, "login": DEFAULT_USER, "display_name": "Гость", "calendar": False}
        user = auth.user_for(request.cookies.get("sid"))
        if user is None:
            raise AssistantError(ErrorCode.UNAUTHORIZED, "no session")
        return {
            "user_id": user.id,
            "login": user.login,
            "display_name": user.display_name,
            "calendar": links is not None and links.get(user.id) is not None,
        }

    return router


def login_router(auth: AuthService, cookie_secure: bool) -> APIRouter:
    router = APIRouter()
    cookie = dict(httponly=True, samesite="lax", secure=cookie_secure)

    @router.get("/auth/login")
    def login():
        url, state = auth.start_login()
        response = RedirectResponse(url, 302)
        response.set_cookie("oauth_state", state, max_age=OAUTH_STATE_MAX_AGE, path="/auth", **cookie)
        return response

    @router.get("/auth/callback")
    def callback(request: Request, code: str = "", state: str = ""):
        try:
            token = auth.finish_login(code, state, request.cookies.get("oauth_state"))
        except AssistantError as e:
            log.warning("login failed code=%s detail=%s", e.code, e.detail)
            response = RedirectResponse("/?auth_error=1", 302)
        except Exception as e:
            log.error("login crashed", exc_info=e)
            response = RedirectResponse("/?auth_error=1", 302)
        else:
            auth.logout(request.cookies.get("sid"))
            response = RedirectResponse("/", 302)
            response.set_cookie("sid", token, max_age=SESSION_MAX_AGE, path="/", **cookie)
        response.delete_cookie("oauth_state", path="/auth")
        return response

    @router.post("/auth/logout")
    def logout(request: Request):
        auth.logout(request.cookies.get("sid"))
        response = Response(status_code=204)
        response.delete_cookie("sid", path="/")
        return response

    return router
