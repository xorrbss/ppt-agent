from typing import List, Any
from api.v1.ppt.endpoints.layouts import get_layouts

async def list_layouts() -> List[Any]:
    """
    Retrieve and return a list of all available presentation layouts.
    """
    return await get_layouts()
