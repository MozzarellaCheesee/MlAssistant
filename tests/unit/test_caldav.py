from datetime import datetime, timedelta

import httpx
import pytest

from app.adapters.outbound.yandex_calendar.caldav import YandexCalendarSync
from app.application.errors import AssistantError, ErrorCode
from app.application.ports.outbound import CalendarLink
from app.domain.dates import MSK
from app.domain.item import Item, ItemKind

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=MSK)
TOKEN = "SECRET-TOKEN-123"


class Links:
    def __init__(self, link):
        self.link = link

    def get(self, uid):
        return self.link

    def save(self, link):
        self.link = link


class Clk:
    def now(self):
        return NOW


def link(exp=NOW + timedelta(days=1)):
    return CalendarLink("default", "a@ya.ru", TOKEN, "R1", exp)


def item(ext=None):
    return Item(id="1", kind=ItemKind.MEETING, title="Встреча", starts_at=NOW + timedelta(hours=1),
                ends_at=NOW + timedelta(hours=2), external_id=ext)


def make(h, lk=None):
    lk = lk if lk is not None else Links(link())
    return YandexCalendarSync(lk, "cid", "csec", Clk(), http=httpx.Client(transport=httpx.MockTransport(h))), lk


def test_put_and_reuse_uid():
    seen = []

    def h(r):
        seen.append(r)
        return httpx.Response(201)

    s, _ = make(h)
    uid = s.push(item())
    r = seen[0]
    assert r.method == "PUT" and str(r.url) == f"https://caldav.yandex.ru/calendars/a%40ya.ru/events-default/{uid}.ics"
    assert r.headers["Authorization"] == f"OAuth {TOKEN}"
    assert r.headers["Content-Type"] == "text/calendar; charset=utf-8"
    assert f"UID:{uid}" in r.content.decode()
    assert s.push(item(uid)) == uid and seen[1].url == r.url


def test_refresh_expired():
    seen = []

    def h(r):
        seen.append(r)
        if r.url.host == "oauth.yandex.ru":
            return httpx.Response(200, json={"access_token": "NEW", "expires_in": 100})
        return httpx.Response(201)

    s, lk = make(h, Links(link(NOW)))
    s.push(item())
    assert seen[0].url.path == "/token" and b"grant_type=refresh_token" in seen[0].content
    assert seen[1].headers["Authorization"] == "OAuth NEW"
    assert lk.link.access_token == "NEW" and lk.link.refresh_token == "R1" and lk.link.email == "a@ya.ru"
    assert lk.link.expires_at == NOW + timedelta(seconds=100)


@pytest.mark.parametrize("h", [lambda r: httpx.Response(401), lambda r: (_ for _ in ()).throw(httpx.ConnectError("x " + TOKEN))])
def test_failures(h):
    s, _ = make(h)
    with pytest.raises(AssistantError) as e:
        s.push(item())
    assert e.value.code == ErrorCode.CALENDAR_FAILED and TOKEN not in str(e.value.detail)


def test_delete_404_ok_and_noop():
    seen = []
    s, _ = make(lambda r: seen.append(r) or httpx.Response(404))
    s.remove(item())
    assert not seen
    s.remove(item("u1"))
    assert seen[0].method == "DELETE"


def test_no_link():
    s, _ = make(lambda r: httpx.Response(201), Links(None))
    with pytest.raises(AssistantError) as e:
        s.push(item())
    assert e.value.code == ErrorCode.CALENDAR_FAILED and e.value.detail == "no link"


def test_email_fully_escaped():
    seen = []
    s, _ = make(lambda r: seen.append(r) or httpx.Response(201), Links(CalendarLink("default", "a/../b@ya.ru", TOKEN, "R1", NOW + timedelta(days=1))))
    s.push(item())
    assert "a%2F..%2Fb%40ya.ru" in str(seen[0].url) and "/../" not in str(seen[0].url)


def test_bad_uid():
    seen = []
    s, _ = make(lambda r: seen.append(r) or httpx.Response(201))
    for call in (lambda: s.push(item("../x")), lambda: s.remove(item("../x"))):
        with pytest.raises(AssistantError) as e:
            call()
        assert e.value.code == ErrorCode.CALENDAR_FAILED and e.value.detail == "bad uid"
    assert not seen


@pytest.mark.parametrize("bad", ["get", "save"])
def test_store_errors(bad):
    class Bad(Links):
        def get(self, uid):
            if bad == "get":
                raise RuntimeError(TOKEN)
            return super().get(uid)

        def save(self, lk):
            raise RuntimeError(TOKEN)

    s, _ = make(lambda r: httpx.Response(200, json={"access_token": "N"}), Bad(link(NOW)))
    with pytest.raises(AssistantError) as e:
        s.push(item())
    assert e.value.code == ErrorCode.CALENDAR_FAILED and e.value.detail == "RuntimeError"


def test_lookup_user_id_and_http_errors():
    got = []

    class L(Links):
        def get(self, uid):
            got.append(uid)
            return self.link

    it = item()
    it.user_id = "u7"
    s, _ = make(lambda r: httpx.Response(201), L(link()))
    s.push(it)
    assert got == ["u7"]
    s, _ = make(lambda r: httpx.Response(400), Links(link(NOW)))
    with pytest.raises(AssistantError) as e:
        s.push(item())
    assert e.value.code == ErrorCode.CALENDAR_FAILED
    s, _ = make(lambda r: httpx.Response(500))
    with pytest.raises(AssistantError) as e:
        s.remove(item("u1"))
    assert e.value.code == ErrorCode.CALENDAR_FAILED
