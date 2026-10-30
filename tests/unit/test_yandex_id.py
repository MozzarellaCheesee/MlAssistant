from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.adapters.outbound.yandex_id import YandexIdProvider
from app.application.ports.outbound import Profile
from app.application.errors import AssistantError, ErrorCode

TOKEN = "SECRET-TOKEN-123"


def make(handler, **kw):
    return YandexIdProvider("cid", "csec", "http://x/cb?a=1&b=2", http=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def test_authorize_url():
    url = make(lambda r: None).authorize_url("s t&1")
    u = urlparse(url)
    assert (u.scheme, u.netloc, u.path) == ("https", "oauth.yandex.ru", "/authorize")
    q = parse_qs(u.query)
    assert q["state"] == ["s t&1"] and q["redirect_uri"] == ["http://x/cb?a=1&b=2"]
    assert q["response_type"] == ["code"] and q["client_id"] == ["cid"] and "scope" not in q


def test_fetch_user_ok():
    seen = {}

    def h(r):
        if r.url.host == "oauth.yandex.ru":
            seen["form"] = parse_qs(r.content.decode())
            return httpx.Response(200, json={"access_token": TOKEN, "refresh_token": "R", "expires_in": 99})
        seen["auth"] = r.headers["Authorization"]
        seen["url"] = str(r.url)
        return httpx.Response(200, json={"id": "42", "login": "ivan", "default_email": "i@ya.ru"})

    assert make(h).fetch_user("code1") == Profile("42", "ivan", "ivan", "i@ya.ru", TOKEN, "R", 99)
    assert seen["form"]["grant_type"] == ["authorization_code"] and seen["form"]["code"] == ["code1"]
    assert seen["auth"] == f"OAuth {TOKEN}" and TOKEN not in seen["url"]


def test_display_name():
    def h(r):
        if r.url.host == "oauth.yandex.ru":
            return httpx.Response(200, json={"access_token": TOKEN})
        return httpx.Response(200, json={"id": "1", "login": "l", "display_name": "Иван"})
    assert make(h).fetch_user("c").display_name == "Иван"


@pytest.mark.parametrize("h", [
    lambda r: httpx.Response(400, json={"error": "bad"}),
    lambda r: httpx.Response(200, content=b"not json"),
    lambda r: httpx.Response(200, json={"nope": 1}),
    lambda r: (_ for _ in ()).throw(httpx.ConnectError("boom")),
])
def test_failures(h):
    with pytest.raises(AssistantError) as e:
        make(h).fetch_user("c")
    assert e.value.code == ErrorCode.AUTH_FAILED


def test_info_failure_no_token_in_detail():
    def h(r):
        if r.url.host == "oauth.yandex.ru":
            return httpx.Response(200, json={"access_token": TOKEN})
        return httpx.Response(401, text=TOKEN)
    with pytest.raises(AssistantError) as e:
        make(h).fetch_user("c")
    assert e.value.code == ErrorCode.AUTH_FAILED and TOKEN not in e.value.detail
