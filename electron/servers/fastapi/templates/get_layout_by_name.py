import aiohttp
from fastapi import HTTPException

from templates.presentation_layout import PresentationLayoutModel


async def get_layout_by_name(layout_name: str) -> PresentationLayoutModel:
    url = f"http://localhost/api/template?group={layout_name}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                error_text = await response.text()
                raise HTTPException(
                    status_code=404,
                    detail=f"Template '{layout_name}' not found: {error_text}",
                )
            layout_json = await response.json()
    return PresentationLayoutModel(**layout_json)
