import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from models.chat import ChatMessageRequest, ChatMessageResponse
from models.sse_response import (
    SSECompleteResponse,
    SSEErrorResponse,
    SSEStatusResponse,
    SSETraceResponse,
    SSEResponse,
)
from services.chat import ChatTurnResult, PresentationChatService
from services.database import get_async_session

CHAT_ROUTER = APIRouter(prefix="/chat", tags=["Chat"])


@CHAT_ROUTER.post("/message", response_model=ChatMessageResponse)
async def chat_message(
    payload: ChatMessageRequest,
    sql_session: AsyncSession = Depends(get_async_session),
):
    service = PresentationChatService(
        sql_session=sql_session,
        presentation_id=payload.presentation_id,
        conversation_id=payload.conversation_id,
    )
    result = await service.generate_reply(payload.message)
    return ChatMessageResponse(
        conversation_id=result.conversation_id,
        response=result.response_text,
        tool_calls=result.tool_calls,
    )


@CHAT_ROUTER.post("/message/stream")
async def chat_message_stream(
    payload: ChatMessageRequest,
    sql_session: AsyncSession = Depends(get_async_session),
):
    service = PresentationChatService(
        sql_session=sql_session,
        presentation_id=payload.presentation_id,
        conversation_id=payload.conversation_id,
    )

    async def inner():
        try:
            async for event_type, value in service.stream_reply(payload.message):
                if event_type == "chunk" and isinstance(value, str):
                    yield SSEResponse(
                        event="response",
                        data=json.dumps({"type": "chunk", "chunk": value}),
                    ).to_string()
                elif event_type == "status" and isinstance(value, str):
                    yield SSEStatusResponse(status=value).to_string()
                elif event_type == "trace" and isinstance(value, dict):
                    yield SSETraceResponse(trace=value).to_string()
                elif event_type == "complete" and isinstance(value, ChatTurnResult):
                    result = value
                    complete_payload = ChatMessageResponse(
                        conversation_id=result.conversation_id,
                        response=result.response_text,
                        tool_calls=result.tool_calls,
                    )
                    yield SSECompleteResponse(
                        key="chat",
                        value=complete_payload.model_dump(mode="json"),
                    ).to_string()
        except HTTPException as exc:
            yield SSEErrorResponse(detail=exc.detail).to_string()

    return StreamingResponse(inner(), media_type="text/event-stream")
