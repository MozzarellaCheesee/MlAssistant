"""Общее для роутеров: текущий пользователь, чтение аудио, сериализация записей и ответов."""

from collections.abc import Callable

from fastapi import Request, UploadFile

from app.application.auth import AuthService
from app.application.errors import AssistantError, ErrorCode
from app.application.ports.inbound import Reply
from app.domain.item import Item

MAX_AUDIO = 2 * 1024 * 1024
DEFAULT_USER = "default"  # без входа (auth не настроен) все запросы -- от одного пользователя


def make_current_user(auth: AuthService | None) -> Callable[[Request], str]:
    """Зависимость FastAPI: id пользователя по cookie sid, иначе 401."""

    def current_user(request: Request) -> str:
        if auth is None:
            return DEFAULT_USER
        user = auth.user_for(request.cookies.get("sid"))
        if not user:
            raise AssistantError(ErrorCode.UNAUTHORIZED)
        request.state.user_id = user.id
        return user.id

    return current_user


async def read_audio(file: UploadFile) -> bytes:
    data = await file.read(MAX_AUDIO + 1)
    if len(data) > MAX_AUDIO:
        raise AssistantError(ErrorCode.AUDIO_TOO_LONG, f"{len(data)} bytes")
    return data


def item_to_dict(item: Item) -> dict:
    return {
        "id": item.id,
        "kind": str(item.kind),
        "title": item.title,
        "starts_at": item.starts_at.isoformat() if item.starts_at else None,
        "ends_at": item.ends_at.isoformat() if item.ends_at else None,
        "due_date": item.due_date.isoformat() if item.due_date else None,
        "status": str(item.status),
        "rrule": item.rrule,
    }


def reply_to_dict(reply: Reply) -> dict:
    result = {
        "reply": reply.reply,
        "items": [item_to_dict(i) for i in reply.items],
        "clarify": reply.clarify,
        "error": None,
    }
    if reply.message_ids:
        result["message_ids"] = reply.message_ids
    if reply.transcript is not None:
        result["transcript"] = reply.transcript
    return result
