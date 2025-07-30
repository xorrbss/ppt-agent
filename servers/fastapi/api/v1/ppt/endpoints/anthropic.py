from typing import Annotated, List, Optional
import anthropic
from fastapi import APIRouter, Body, HTTPException

from utils.get_env import get_anthropic_api_key_env

ANTHROPIC_ROUTER = APIRouter(prefix="/anthropic", tags=["Anthropic"])


@ANTHROPIC_ROUTER.post("/models/available", response_model=List[str])
async def get_available_models(
    api_key: Annotated[Optional[str], Body(embed=True)] = None,
):
    anthropic_api_key = api_key or get_anthropic_api_key_env()
    if not anthropic_api_key:
        raise HTTPException(status_code=400, detail="Anthropic API key is required")

    client = anthropic.Anthropic(api_key=anthropic_api_key)
    models = client.models.list(limit=20)
    return [model.id for model in models]
