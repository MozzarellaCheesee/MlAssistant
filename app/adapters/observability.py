"""Сквозное логирование: id запроса в каждой строке лога и обёртка портов с временем вызова.
Ядро про это не знает: middleware ставит id в http.py, обёртки надевает bootstrap."""

import logging
import time
from contextvars import ContextVar

request_id: ContextVar[str] = ContextVar("request_id", default="-")
log = logging.getLogger("app.ports")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        return True


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s")
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, RequestIdFilter) for f in handler.filters):
            handler.addFilter(RequestIdFilter())


def _summary(result) -> str:
    """Без значений: в тексте и названиях -- слова пользователя."""
    if result is None:
        return "none"
    if hasattr(result, "intent"):
        return result.intent
    if isinstance(result, (str, bytes, list, tuple)):
        return f"len={len(result)}"
    return type(result).__name__


class Logged:
    """Декоратор порта: тот же интерфейс, вызовы из `methods` пишутся в лог с длительностью и итогом.
    Остальные атрибуты (свойства, вспомогательные методы) проходят к обёрнутому объекту как есть."""

    def __init__(self, inner, name: str, methods: set[str]):
        self._inner = inner
        self._name = name
        self._methods = methods

    def __getattr__(self, attr):
        value = getattr(self._inner, attr)
        if attr not in self._methods or not callable(value):
            return value

        def call(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = value(*args, **kwargs)
            except Exception as e:
                log.warning(
                    "%s.%s failed %.0fms: %s", self._name, attr, (time.perf_counter() - start) * 1000, type(e).__name__
                )
                raise
            log.info("%s.%s %.0fms -> %s", self._name, attr, (time.perf_counter() - start) * 1000, _summary(result))
            return result

        return call
