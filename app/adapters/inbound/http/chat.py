"""Чат: текстовая реплика, голосовое сообщение со страницы, история."""

from collections.abc import Callable

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.adapters.inbound.http.common import read_audio, reply_to_dict
from app.application.ports.inbound import AssistantPort


class MessageIn(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=1000)


def chat_router(service: AssistantPort, current_user: Callable) -> APIRouter:
    router = APIRouter()

    @router.post("/api/message")
    def message(body: MessageIn, request: Request, user: str = Depends(current_user)):
        request.state.session_id = body.session_id
        return reply_to_dict(service.handle_turn(body.session_id, user, body.text))

    @router.post("/api/audio")
    async def audio(
        request: Request,
        session_id: str = Form(min_length=1, max_length=100),
        file: UploadFile = File(),
        user: str = Depends(current_user),
    ):
        request.state.session_id = session_id
        data = await read_audio(file)
        result = reply_to_dict(await run_in_threadpool(service.handle_audio, session_id, user, data))
        if result.get("message_ids"):
            # ответ на голос со страницы: страница озвучивает его через /api/speech
            result["speak_id"] = result["message_ids"][-1]
        return result

    @router.get("/api/history")
    def history(after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200), user: str = Depends(current_user)):
        return service.history(user, after, limit)

    return router
