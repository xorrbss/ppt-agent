import aiohttp
from urllib.parse import urlencode

from fastapi import HTTPException

from services.export_task_service import EXPORT_TASK_SERVICE
from templates.presentation_layout import PresentationLayoutModel


async def get_layout_by_name(layout_name: str) -> PresentationLayoutModel:
    query = urlencode({"group": layout_name})
    url = f"http://localhost/schema?{query}"

    schema_payload = None
    runtime_error = None

    try:
        schema = await EXPORT_TASK_SERVICE.extract_schema(url)
        schema_payload = schema.model_dump()
    except HTTPException as exc:
        # Backward compatibility: older export runtimes do not implement
        # extract-schema and return "Invalid task type".
        runtime_error = str(exc.detail)

    if schema_payload is None:
        fallback_error = None
        fallback_url = f"http://localhost/api/template?group={layout_name}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(fallback_url) as response:
                    if response.status == 200:
                        schema_payload = await response.json()
                    else:
                        fallback_error = await response.text()
        except aiohttp.ClientError as exc:
            fallback_error = str(exc)

        if schema_payload is None:
            error_detail = runtime_error or fallback_error or "unknown error"
            raise HTTPException(
                status_code=404,
                detail=f"Template '{layout_name}' not found: {error_detail}",
            )

    if not schema_payload.get("slides"):
        raise HTTPException(
            status_code=404,
            detail=f"Template '{layout_name}' not found",
        )

    return PresentationLayoutModel(**schema_payload)
