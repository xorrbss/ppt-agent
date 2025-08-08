from datetime import datetime
import json
from fastapi import APIRouter
from pydantic import BaseModel, Field

from models.llm_message import LLMUserMessage
from models.llm_tools import LLMDynamicTool, SearchWebTool
from services.llm_client import LLMClient
from utils.llm_provider import get_model

API_V1_TEST_ROUTER = APIRouter(prefix="/api/v1/test", tags=["test"])


class ResponseContent(BaseModel):
    trending_ai_tool: str = Field(
        description="The summary of the trending AI tool in about 50 words",
        min_length=50,
        max_length=100,
    )
    current_date_time: str


@API_V1_TEST_ROUTER.get("")
async def test():
    client = LLMClient()

    async def get_current_datetime_tool_handler(_) -> str:
        return datetime.now().isoformat()

    get_current_datetime_tool = LLMDynamicTool(
        name="GetDateTimeDynamicTool",
        description="Get the current date and time",
        handler=get_current_datetime_tool_handler,
    )

    text_content = ""

    async for chunk in client.stream_structured(
        model=get_model(),
        messages=[
            LLMUserMessage(
                content="What is the current date and time ? What is the trending AI tool now ? Use Available tools to get the information."
            ),
        ],
        response_format=ResponseContent.model_json_schema(),
        tools=[
            SearchWebTool,
            get_current_datetime_tool,
        ],
    ):
        text_content += chunk

    return {"data": text_content}
