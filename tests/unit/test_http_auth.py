from datetime import datetime
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.adapters.inbound.http import create_app
from app.adapters.outbound.sqlite.db import Database
from app.adapters.outbound.sqlite.user_store import SqliteUserStore
from app.application.auth import AuthService
from app.application.ports.inbound import Reply
from app.application.ports.outbound import Profile
from app.domain.dates import MSK
from tests.scenarios.fakes import FixedClock


class FakeIdp:
    def authorize_url(self, state):
        return f"https://idp/auth?state={state}"

    def fetch_user(self, code):
        return Profile(code, code, code.title(), f"{code}@ya.ru", "AT", None, None)


class FakeService:
    def __init__(self):
        self.users = []

    def handle_turn(self, session_id, user_id, text):
        self.users.append(user_id)
        return Reply("ok")

    def handle_audio(self, session_id, user_id, audio):
        return Reply("ok")

    def list_items(self, user_id, rng, kind, status):
        self.users.append(user_id)
        return []

    def due_reminders(self, user_id):
        return []


def make(svc=None):
    auth = AuthService(FakeIdp(), SqliteUserStore(Database(":memory:")), FixedClock(datetime(2026, 10, 28, tzinfo=MSK)))
    return auth, svc or FakeService()


def client(auth, svc):
    return TestClient(create_app(svc, auth, cookie_secure=False), follow_redirects=False)


def login(c, code="ivan"):
    r = c.get("/auth/login")
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    return c.get("/auth/callback", params={"code": code, "state": state}), state


def test_login_redirects_with_state():
    c = client(*make())
    r = c.get("/auth/login")
    assert r.status_code == 302 and r.headers["location"].startswith("https://idp/auth?state=")
    sc = r.headers["set-cookie"].lower()
    assert "oauth_state=" in sc and "httponly" in sc and "samesite=lax" in sc and "max-age=600" in sc and "path=/auth" in sc


def test_callback_sets_cookie_and_me():
    c = client(*make())
    r, _ = login(c)
    assert r.status_code == 302 and r.headers["location"] == "/"
    sid = [h for h in r.headers.get_list("set-cookie") if h.startswith("sid=")][0].lower()
    assert "httponly" in sid and "samesite=lax" in sid
    me = c.get("/api/me").json()
    assert me.pop("user_id") and me == {"login": "ivan", "display_name": "Ivan", "calendar": False}


def test_callback_secure_cookie_and_clears_state():
    auth, svc = make()
    c = TestClient(create_app(svc, auth, cookie_secure=True), base_url="https://testserver", follow_redirects=False)
    r, _ = login(c)
    cookies = r.headers.get_list("set-cookie")
    assert "secure" in [h for h in cookies if h.startswith("sid=")][0].lower()
    assert any(h.startswith("oauth_state=") and "max-age=0" in h.lower() for h in cookies)


def test_callback_bad_state_redirects_error_no_cookie():
    c = client(*make())
    c.get("/auth/login")
    r = c.get("/auth/callback", params={"code": "x", "state": "bad"})
    assert r.headers["location"] == "/?auth_error=1"
    assert not any(h.startswith("sid=") for h in r.headers.get_list("set-cookie"))


def test_callback_requires_matching_browser_state():
    auth, svc = make()
    victim = client(auth, svc)
    state = parse_qs(urlparse(victim.get("/auth/login").headers["location"]).query)["state"][0]
    attacker = client(auth, svc)  # без cookie oauth_state
    r = attacker.get("/auth/callback", params={"code": "x", "state": state})
    assert r.headers["location"] == "/?auth_error=1" and "sid" not in attacker.cookies
    attacker.cookies.set("oauth_state", "other", path="/auth")
    r = attacker.get("/auth/callback", params={"code": "x", "state": state})
    assert r.headers["location"] == "/?auth_error=1" and "sid" not in attacker.cookies
    victim.cookies.set("oauth_state", state, path="/auth")
    assert victim.get("/auth/callback", params={"code": "x", "state": state}).headers["location"] == "/"
    assert "sid" in victim.cookies


def test_api_requires_auth():
    c = client(*make())
    for r in (c.post("/api/message", json={"session_id": "s", "text": "x"}),
              c.get("/api/items", params={"from": "2026-10-01", "to": "2026-10-02"}),
              c.post("/api/reminders/due"),
              c.post("/api/audio", data={"session_id": "s"}, files={"file": ("a.wav", b"x")})):
        assert r.status_code == 401 and r.json()["error"] == "unauthorized"


def test_me_unauthorized_without_or_with_unknown_sid():
    c = client(*make())
    assert c.get("/api/me").status_code == 401
    c.cookies.set("sid", "unknown")
    r = c.get("/api/me")
    assert r.status_code == 401 and r.json()["error"] == "unauthorized"


def test_logout_invalidates_cookie():
    c = client(*make())
    login(c)
    sid = c.cookies["sid"]
    assert c.post("/auth/logout").status_code == 204
    c.cookies.set("sid", sid)
    assert c.post("/api/reminders/due").status_code == 401


def test_auth_disabled_mode():
    c = client(None, FakeService())
    assert c.post("/api/message", json={"session_id": "s", "text": "x"}).status_code == 200
    assert c.get("/auth/login").status_code == 404
    assert c.get("/api/me").json()["display_name"] == "Гость"


def test_users_see_own_items():
    auth, svc = make()
    a, b = client(auth, svc), client(auth, svc)
    login(a, "ann")
    login(b, "bob")
    for c in (a, b):
        c.post("/api/message", json={"session_id": "s", "text": "x"})
    assert len(svc.users) == 2 and svc.users[0] != svc.users[1]


def test_callback_unexpected_error_redirects_and_clears_state():
    auth, svc = make()

    def boom(*a):
        raise RuntimeError("db down")
    auth.users.pop_state = boom
    c = client(auth, svc)
    r, _ = login(c)
    assert r.headers["location"] == "/?auth_error=1" and "sid" not in c.cookies
    assert "oauth_state" in r.headers["set-cookie"] and "Max-Age=0" in r.headers["set-cookie"]


def test_new_login_revokes_old_sid():
    auth, svc = make()
    c = client(auth, svc)
    login(c)
    old = c.cookies["sid"]
    login(c)
    assert c.get("/api/me").status_code == 200
    other = client(auth, svc)
    other.cookies.set("sid", old)
    assert other.get("/api/me").status_code == 401


def test_reminders_due_rejects_get():
    c = client(*make())
    login(c)
    assert c.get("/api/reminders/due").status_code in (404, 405)
