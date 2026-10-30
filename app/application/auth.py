import hashlib
import hmac
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from app.application.errors import AssistantError, ErrorCode
from app.application.ports.outbound import Clock, IdentityProvider, Profile, User, UserStore

log = logging.getLogger(__name__)
STATE_TTL = timedelta(minutes=10)
SESSION_TTL = timedelta(days=30)
STATE_BYTES = 16
SESSION_TOKEN_BYTES = 32


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class AuthService:
    idp: IdentityProvider
    users: UserStore
    clock: Clock
    on_first_login: Callable[[User], None] | None = None
    on_login: Callable[[User, Profile], None] | None = None

    def start_login(self) -> tuple[str, str]:
        state = secrets.token_urlsafe(STATE_BYTES)
        now = self.clock.now()
        self.users.save_state(state, now + STATE_TTL, now)
        return self.idp.authorize_url(state), state

    def finish_login(self, code: str, state: str, browser_state: str | None) -> str:
        now = self.clock.now()
        # привязка state к браузеру (login CSRF); проверяем до pop_state, чтобы чужая попытка не сожгла state
        if (
            not browser_state
            or not hmac.compare_digest(state.encode(), browser_state.encode())
            or not self.users.pop_state(state, now)
        ):
            raise AssistantError(ErrorCode.AUTH_FAILED)
        profile = self.idp.fetch_user(code)
        user, created = self.users.upsert_user(profile.yandex_uid, profile.login, profile.display_name)
        token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
        self.users.create_session(_hash(token), user.id, now + SESSION_TTL, now)
        if created and self.on_first_login:
            try:
                self.on_first_login(user)
            except Exception:
                log.exception("on_first_login failed user=%s", user.id)
        if self.on_login:
            try:
                self.on_login(user, profile)
            except Exception:
                log.exception("on_login failed user=%s", user.id)
        return token

    def user_for(self, token: str | None) -> User | None:
        return self.users.user_by_session(_hash(token), self.clock.now()) if token else None

    def logout(self, token: str | None) -> None:
        if token:
            self.users.delete_session(_hash(token))
