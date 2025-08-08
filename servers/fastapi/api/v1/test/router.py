from fastapi import APIRouter
from pydantic import BaseModel, Field

from models.llm_message import LLMUserMessage
from models.llm_tools import GetCurrentDatetimeTool, SearchWebTool
from services.llm_client import LLMClient
from utils.llm_calls.generate_presentation_outlines import generate_ppt_outline
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

    response = await client._search_anthropic("Trending AI tool now")
    # print(response)

    return {"data": ""}
