from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from models.chat import ChatMessageRequest, ChatMessageResponse
from services.chat import PresentationChatService
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
