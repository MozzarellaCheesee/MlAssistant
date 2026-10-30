from urllib.parse import urlencode

import httpx

from app.application.errors import AssistantError, ErrorCode
from app.application.ports.outbound import Profile

HTTP_TIMEOUT_SECONDS = 10


class YandexIdProvider:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, http: httpx.Client | None = None):
        self._id = client_id
        self._secret = client_secret
        self._redirect = redirect_uri
        self._http = http or httpx.Client()

    def authorize_url(self, state: str) -> str:
        params = {"response_type": "code", "client_id": self._id, "redirect_uri": self._redirect, "state": state}
        return "https://oauth.yandex.ru/authorize?" + urlencode(params)

    def fetch_user(self, code: str) -> Profile:
        try:
            response = self._http.post(
                "https://oauth.yandex.ru/token",
                timeout=HTTP_TIMEOUT_SECONDS,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": self._id,
                    "client_secret": self._secret,
                },
            )
            response.raise_for_status()
            token_data = response.json()
            token = token_data["access_token"]
            response = self._http.get(
                "https://login.yandex.ru/info?format=json",
                timeout=HTTP_TIMEOUT_SECONDS,
                headers={"Authorization": f"OAuth {token}"},
            )
            response.raise_for_status()
            info = response.json()
            return Profile(
                str(info["id"]),
                info["login"],
                info.get("display_name") or info["login"],
                info.get("default_email"),
                token,
                token_data.get("refresh_token"),
                token_data.get("expires_in"),
            )
        except httpx.HTTPStatusError as e:
            raise AssistantError(ErrorCode.AUTH_FAILED, f"http {e.response.status_code}") from None
        except Exception as e:  # сеть, не-JSON, нет полей; str(e) не берём: вдруг в нём токен
            raise AssistantError(ErrorCode.AUTH_FAILED, type(e).__name__) from None
