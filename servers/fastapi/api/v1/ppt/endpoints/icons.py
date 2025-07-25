from typing import List
from fastapi import APIRouter
from services.icon_finder_service import IconFinderService

ICONS_ROUTER = APIRouter(prefix="/icons", tags=["Icons"])


@ICONS_ROUTER.get("/search", response_model=List[str])
async def search_icons(query: str, limit: int = 20):
    icon_finder_service = IconFinderService()
    return await icon_finder_service.search_icons(query, limit)
