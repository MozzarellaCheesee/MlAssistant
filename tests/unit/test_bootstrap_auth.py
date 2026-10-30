from dataclasses import replace
from unittest.mock import patch

from app.adapters.outbound.sqlite.db import Database
from app.bootstrap import build_auth
from app.application.ports.outbound import Profile
from app.config import Settings


class Idp:
    def authorize_url(self, state):
        return "x"

    def fetch_user(self, code):
        return Profile(code, code, code, None, "AT", None, None)


def test_demo_seed_imports_rows_for_new_user_only(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    s = Settings("", "", "", "", str(tmp_path / "a.db"), "", demo_seed=True)
    with patch("app.bootstrap.YandexIdProvider", return_value=Idp()):
        auth = build_auth(s, db)
    for _ in range(2):
        _, state = auth.start_login()
        auth.finish_login("42", state, state)
    n = db.conn.execute("SELECT COUNT(*) FROM items WHERE user_id='u42'").fetchone()[0]
    assert n > 0 and db.conn.execute("SELECT COUNT(*) FROM items WHERE user_id!='u42'").fetchone()[0] == 0
