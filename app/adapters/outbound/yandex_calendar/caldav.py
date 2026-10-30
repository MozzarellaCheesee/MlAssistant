import re
from dataclasses import replace
from datetime import timedelta
from urllib.parse import quote
from uuid import uuid4

import httpx

from app.adapters.outbound.yandex_calendar.ics import build_ics
from app.application.errors import AssistantError, ErrorCode
from app.application.ports.outbound import CalendarLink, CalendarLinks, Clock
from app.domain.item import Item

HTTP_TIMEOUT_SECONDS = 10
DEFAULT_TOKEN_TTL_SECONDS = 365 * 86400  # если Яндекс не вернул expires_in


def _fail(detail: str) -> AssistantError:
    return AssistantError(ErrorCode.CALENDAR_FAILED, detail)


class YandexCalendarSync:
    def __init__(
        self, links: CalendarLinks, client_id: str, client_secret: str, clock: Clock, http: httpx.Client | None = None
    ):
        self._links = links
        self._id = client_id
        self._secret = client_secret
        self._clock = clock
        self._http = http or httpx.Client()

    def _link(self, user_id: str) -> CalendarLink:
        try:
            link = self._links.get(user_id)
        except Exception as e:
            raise _fail(type(e).__name__) from None
        if link is None:
            raise _fail("no link")
        now = self._clock.now()
        if link.expires_at > now:
            return link
        if not link.refresh_token:
            raise _fail("no refresh token")
        try:
            response = self._http.post(
                "https://oauth.yandex.ru/token",
                timeout=HTTP_TIMEOUT_SECONDS,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": link.refresh_token,
                    "client_id": self._id,
                    "client_secret": self._secret,
                },
            )
            response.raise_for_status()
            token_data = response.json()
            link = replace(
                link,
                access_token=token_data["access_token"],
                refresh_token=token_data.get("refresh_token") or link.refresh_token,
                expires_at=now + timedelta(seconds=int(token_data.get("expires_in") or DEFAULT_TOKEN_TTL_SECONDS)),
            )
        except httpx.HTTPStatusError as e:
            raise _fail(f"http {e.response.status_code}") from None
        except Exception as e:  # str(e) не берём: вдруг в нём токен
            raise _fail(type(e).__name__) from None
        try:
            self._links.save(link)
        except Exception as e:
            raise _fail(type(e).__name__) from None
        return link

    def _send(self, method: str, item: Item, uid: str, body: str | None = None, ok404: bool = False) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", uid):
            raise _fail("bad uid")
        link = self._link(item.user_id)
        url = f"https://caldav.yandex.ru/calendars/{quote(link.email, safe='')}/events-default/{uid}.ics"
        headers = {"Authorization": f"OAuth {link.access_token}"}
        if body is not None:
            headers["Content-Type"] = "text/calendar; charset=utf-8"
        try:
            response = self._http.request(
                method,
                url,
                timeout=HTTP_TIMEOUT_SECONDS,
                headers=headers,
                content=body.encode() if body is not None else None,
            )
        except Exception as e:
            raise _fail(type(e).__name__) from None
        if not response.is_success and not (ok404 and response.status_code == 404):
            raise _fail(f"http {response.status_code}")

    def push(self, item: Item) -> str:
        uid = item.external_id or uuid4().hex
        try:
            body = build_ics(item, uid, self._clock.now())
        except ValueError:
            raise _fail("no starts_at") from None
        self._send("PUT", item, uid, body)
        return uid

    def remove(self, item: Item) -> None:
        if item.external_id:
            self._send("DELETE", item, item.external_id, ok404=True)
