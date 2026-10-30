from enum import StrEnum


class ErrorCode(StrEnum):
    LLM_UNAVAILABLE = "llm_unavailable"
    LLM_QUOTA = "llm_quota"
    LLM_RATE_LIMIT = "llm_rate_limit"
    LLM_BAD_RESPONSE = "llm_bad_response"
    CERT_MISMATCH = "cert_mismatch"
    STT_EMPTY = "stt_empty"
    STT_FAILED = "stt_failed"
    AUDIO_TOO_LONG = "audio_too_long"
    UNAUTHORIZED = "unauthorized"
    CALENDAR_FAILED = "calendar_failed"
    AUTH_FAILED = "auth_failed"


_UNAVAILABLE = "Сервис понимания временно недоступен, попробуйте позже."
# только текст для пользователя; HTTP-статус -- забота HTTP-адаптера (HTTP_STATUS в adapters/inbound/http)
ERRORS: dict[ErrorCode, str] = {
    ErrorCode.LLM_UNAVAILABLE: _UNAVAILABLE,
    ErrorCode.LLM_QUOTA: "Закончился лимит запросов к языковой модели.",
    ErrorCode.LLM_RATE_LIMIT: "Слишком много запросов к модели. Повторите через пару секунд.",
    ErrorCode.LLM_BAD_RESPONSE: "Не понял запрос. Попробуйте сформулировать иначе.",
    ErrorCode.CERT_MISMATCH: _UNAVAILABLE,
    ErrorCode.STT_EMPTY: "Не расслышал, повторите.",
    ErrorCode.STT_FAILED: "Не получилось обработать запись. Попробуйте ещё раз или напишите текстом.",
    ErrorCode.AUDIO_TOO_LONG: "Запись слишком длинная или большая: до 30 секунд и 2 МБ. Скажите короче.",
    ErrorCode.UNAUTHORIZED: "Войдите, чтобы продолжить.",
    ErrorCode.CALENDAR_FAILED: "Запись сохранена, но в календарь не попала.",
    ErrorCode.AUTH_FAILED: "Не получилось войти через Яндекс. Попробуйте ещё раз.",
}


class AssistantError(Exception):
    def __init__(self, code: ErrorCode, detail: str = ""):
        super().__init__(code, detail)
        self.code = code
        self.detail = detail

    @property
    def user_message(self) -> str:
        return ERRORS[self.code]


class FormError(Exception):
    """Форма панели: handler вернул вопрос или отказ; текст показываем под формой.
    not_found -- чужая или удалённая запись."""

    def __init__(self, text: str, not_found: bool = False):
        super().__init__(text)
        self.text = text
        self.not_found = not_found
