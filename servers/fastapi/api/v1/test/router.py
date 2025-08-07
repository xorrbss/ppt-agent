from datetime import datetime
import json
from fastapi import APIRouter
from pydantic import BaseModel

from models.llm_message import LLMUserMessage
from models.llm_tools import LLMDynamicTool, SearchWebTool
from services.llm_client import LLMClient

API_V1_TEST_ROUTER = APIRouter(prefix="/api/v1/test", tags=["test"])


class ResponseContent(BaseModel):
    trending_ai_tool: str
    current_date_time: str


@API_V1_TEST_ROUTER.get("")
async def test():
    client = LLMClient()

    async def get_current_datetime_tool_handler(_) -> str:
        return datetime.now().isoformat()

    get_current_datetime_tool = LLMDynamicTool(
        name="GetDateTimeDynamicTool",
        description="Get the current date and time",
        parameters=None,
        handler=get_current_datetime_tool_handler,
    )

    accumulated_content = ""

    async for chunk in client.stream_structured(
        model="gpt-4.1-mini",
        messages=[
            LLMUserMessage(
                content="What is the current date and time ? What is the trending AI tool now ?"
            ),
        ],
        response_format=ResponseContent.model_json_schema(),
        tools=[
            SearchWebTool,
            get_current_datetime_tool,
        ],
    ):
        accumulated_content += chunk

    return {"data": json.loads(accumulated_content)}
