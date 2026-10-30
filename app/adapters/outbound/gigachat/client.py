import os
import ssl
import time
from contextlib import contextmanager

import httpx
from gigachat import GigaChat
from gigachat.exceptions import GigaChatException, ResponseError

from app.application.errors import AssistantError, ErrorCode

DEFAULT_TIMEOUT_SECONDS = 20
HTTP_PAYMENT_REQUIRED = 402
HTTP_TOO_MANY_REQUESTS = 429
API_URL = "https://gigachat.devices.sberbank.ru/api/v1"
_FORBIDDEN_ENV = ("GIGACHAT_CA_BUNDLE_FILE", "GIGACHAT_VERIFY_SSL_CERTS", "GIGACHAT_BASE_URL", "GIGACHAT_AUTH_URL")


def build_ssl_context(pem_path: str) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # без системных CA: доверяем только pinned-сертификатам
    ctx.load_verify_locations(pem_path)
    ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def make_client(
    credentials: str, scope: str, model: str, pem_path: str, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> GigaChat:
    bad = [k for k in _FORBIDDEN_ENV if k in os.environ]
    if bad:
        raise RuntimeError(f"Удалите из окружения {', '.join(bad)}: они переопределяют pinned-сертификат")
    return GigaChat(
        credentials=credentials,
        scope=scope,
        model=model,
        ssl_context=build_ssl_context(pem_path),
        timeout=timeout,
        base_url=API_URL,
    )


@contextmanager
def call_errors():
    try:
        yield
    except AssistantError:
        raise
    except Exception as e:
        if isinstance(e, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(e):
            raise AssistantError(ErrorCode.CERT_MISMATCH, "run python -m scripts.pin_certs") from e
        if isinstance(e, ResponseError) and e.status_code == HTTP_PAYMENT_REQUIRED:
            raise AssistantError(ErrorCode.LLM_QUOTA, str(e)) from e
        if isinstance(e, (GigaChatException, httpx.HTTPError, TimeoutError)):
            raise AssistantError(ErrorCode.LLM_UNAVAILABLE, str(e)) from e
        raise


def chat_retry(client, payload, sleep=time.sleep):
    """429: до двух повторов с паузой 1 и 2 с, затем LLM_RATE_LIMIT."""
    for delay in (1, 2, None):
        try:
            with call_errors():
                return client.chat(payload)
        except Exception as e:
            cause = e.__cause__ if isinstance(e, AssistantError) else e
            if not (isinstance(cause, ResponseError) and cause.status_code == HTTP_TOO_MANY_REQUESTS):
                raise
            if delay is None:
                raise AssistantError(ErrorCode.LLM_RATE_LIMIT, str(cause)) from cause
            sleep(delay)
