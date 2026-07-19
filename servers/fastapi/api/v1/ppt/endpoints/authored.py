from pydantic import BaseModel
from fastapi import APIRouter

from utils.authored_styles import load_authored_styles


class AuthoredStylePreview(BaseModel):
    bg: str
    accent: str


class AuthoredStyleSummary(BaseModel):
    id: str
    name: str
    description: str
    preview: AuthoredStylePreview


AUTHORED_ROUTER = APIRouter(prefix="/authored", tags=["Authored"])


@AUTHORED_ROUTER.get("/styles", response_model=list[AuthoredStyleSummary])
def get_authored_styles() -> list[dict]:
    """List selectable authored styles without exposing their generation briefs."""
    return [style.public_dict() for style in load_authored_styles()]
