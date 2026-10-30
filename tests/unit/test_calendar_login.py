from datetime import datetime

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.adapters.inbound.http import create_app
from app.adapters.outbound.sqlite.db import Database
from app.application.ports.outbound import Profile
from app.bootstrap import build_auth, build_calendar
from app.config import Settings
from tests.unit.test_http_auth import FakeService
from unittest.mock import patch


class Idp:
    email = "i@ya.ru"

    def authorize_url(self, state):
        return "x"

    def fetch_user(self, code):
        return Profile(code, code, code, self.email, "AT", "RT", 60)


def setup(tmp_path, email="i@ya.ru", key=None):
    db = Database(str(tmp_path / "a.db"))
    s = Settings("", "", "", "", "", "", token_encryption_key=key or Fernet.generate_key().decode())
    idp = Idp()
    idp.email = email
    links, sync = build_calendar(s, db)
    with patch("app.bootstrap.YandexIdProvider", return_value=idp):
        auth = build_auth(s, db, links=links)
    return auth, links, sync


def login(auth):
    _, st = auth.start_login()
    return auth.finish_login("42", st, st)


def test_every_login_saves_link(tmp_path):
    auth, links, sync = setup(tmp_path)
    assert sync is not None
    login(auth)
    l1 = links.get("u42")
    assert l1.email == "i@ya.ru" and l1.access_token == "AT" and l1.refresh_token == "RT"
    login(auth)
    assert links.get("u42").expires_at >= l1.expires_at


def test_no_email_no_link(tmp_path):
    auth, links, _ = setup(tmp_path, email=None)
    login(auth)
    assert links.get("u42") is None


def test_me_calendar_flag(tmp_path):
    auth, links, _ = setup(tmp_path)
    c = TestClient(create_app(FakeService(), auth, False, links=links))
    _, st = auth.start_login()
    c.cookies.set("sid", login(auth))
    assert c.get("/api/me").json()["calendar"] is True
    links.delete("u42")
    assert c.get("/api/me").json()["calendar"] is False


def test_disabled_and_bad_key(tmp_path):
    db = Database(":memory:")
    assert build_calendar(Settings("", "", "", "", "", "", auth_disabled=True, token_encryption_key="x"), db) == (None, None)
    assert build_calendar(Settings("", "", "", "", "", ""), db) == (None, None)
    with pytest.raises(RuntimeError, match="TOKEN_ENCRYPTION_KEY is invalid"):
        build_calendar(Settings("", "", "", "", "", "", token_encryption_key="bad"), db)


def test_failing_on_login_does_not_block_login(tmp_path):
    auth, links, _ = setup(tmp_path)
    links.save = lambda link: 1 / 0
    assert login(auth)
