from app.adapters.inbound.http import HTTP_STATUS
from app.application.errors import ERRORS, AssistantError, ErrorCode


def test_every_code_has_message_and_status():
    assert set(ERRORS) == set(ErrorCode)
    assert set(HTTP_STATUS) == set(ErrorCode)  # статусы живут в HTTP-адаптере, но покрывают все коды ядра
    assert AssistantError(ErrorCode.STT_EMPTY).user_message == "Не расслышал, повторите."
