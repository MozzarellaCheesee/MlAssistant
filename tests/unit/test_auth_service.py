from datetime import datetime, timedelta

import pytest

from app.application.auth import AuthService
from app.application.errors import AssistantError, ErrorCode
from app.application.ports.outbound import Profile, User
from app.domain.dates import MSK
from tests.scenarios.fakes import FixedClock

T0 = datetime(2026, 10, 28, 12, 0, tzinfo=MSK)


class FakeIdp:
    def authorize_url(self, state):
        return f"https://idp/auth?state={state}"

    def fetch_user(self, code):
        return Profile("y1", "ivan", "Иван", "i@ya.ru", "AT", "RT", 3600)


class FakeStore:
    def __init__(self):
        self.users, self.states, self.sessions = {}, {}, {}

    def upsert_user(self, yandex_uid, login, display_name):
        if yandex_uid in self.users:
            return self.users[yandex_uid], False
        u = self.users[yandex_uid] = User(str(len(self.users) + 1), yandex_uid, login, display_name)
        return u, True

    def save_state(self, state, expires_at, now):
        self.states[state] = expires_at

    def pop_state(self, state, now):
        exp = self.states.pop(state, None)
        return exp is not None and exp > now

    def create_session(self, token_hash, user_id, expires_at, now):
        self.sessions[token_hash] = (user_id, expires_at)

    def user_by_session(self, token_hash, now):
        s = self.sessions.get(token_hash)
        if not s or s[1] <= now:
            return None
        return next(u for u in self.users.values() if u.id == s[0])

    def delete_session(self, token_hash):
        self.sessions.pop(token_hash, None)


def fin(svc):
    s = login(svc)
    return svc.finish_login("c", s, s)


def make(hook=None):
    clock, store = FixedClock(T0), FakeStore()
    return AuthService(FakeIdp(), store, clock, hook), clock, store


def login(svc):
    return svc.start_login()[1]


def test_login_roundtrip():
    svc, _, _ = make()
    state = login(svc)
    token = svc.finish_login("code", state, state)
    assert svc.user_for(token).login == "ivan"


def test_state_single_use_and_expiry():
    svc, clock, _ = make()
    s = login(svc)
    svc.finish_login("c", s, s)
    with pytest.raises(AssistantError) as e:
        svc.finish_login("c", s, s)
    assert e.value.code == ErrorCode.AUTH_FAILED
    s2 = login(svc)
    clock.dt = T0 + timedelta(minutes=11)
    with pytest.raises(AssistantError):
        svc.finish_login("c", s2, s2)
    with pytest.raises(AssistantError):
        svc.finish_login("c", "unknown", "unknown")


def test_session_expiry_and_logout():
    svc, clock, _ = make()
    token = fin(svc)
    assert svc.user_for(None) is None
    svc.logout(token)
    assert svc.user_for(token) is None
    token = fin(svc)
    clock.dt = T0 + timedelta(days=31)
    assert svc.user_for(token) is None


def test_first_login_hook_once():
    calls = []
    svc, _, _ = make(calls.append)
    fin(svc)
    fin(svc)
    assert len(calls) == 1


def test_token_not_stored_raw():
    svc, _, store = make()
    token = fin(svc)
    (h,) = store.sessions
    assert h != token and len(h) == 64


def test_browser_state_mismatch_keeps_state():
    svc, _, _ = make()
    s = login(svc)
    for bad in (None, "", "other"):
        with pytest.raises(AssistantError) as e:
            svc.finish_login("c", s, bad)
        assert e.value.code == ErrorCode.AUTH_FAILED
    assert svc.finish_login("c", s, s)


def test_failing_hook_is_non_fatal():
    def boom(user):
        raise RuntimeError("x")
    svc, _, _ = make(boom)
    assert svc.user_for(fin(svc)).login == "ivan"
