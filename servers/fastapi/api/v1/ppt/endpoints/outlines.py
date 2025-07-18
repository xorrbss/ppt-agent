import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from models.presentation_outline_model import PresentationOutlineModel
from models.sql.presentation import PresentationModel
from models.sse_response import SSECompleteResponse, SSEResponse, SSEStatusResponse
from services.database import get_sql_session
from utils.llm_calls.generate_presentation_outlines import generate_ppt_outline

OUTLINES_ROUTER = APIRouter(prefix="/outlines", tags=["Outlines"])


@OUTLINES_ROUTER.get("/stream")
async def stream_outlines(presentation_id: str):
    with get_sql_session() as sql_session:
        presentation = sql_session.get(PresentationModel, presentation_id)

    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")

    async def inner():
        yield SSEStatusResponse(
            status="Generating presentation outlines..."
        ).to_string()

        presentation_content_text = ""
        async for chunk in generate_ppt_outline(
            presentation.prompt,
            presentation.n_slides,
            presentation.language,
            presentation.summary,
        ):
            yield SSEResponse(
                event="response",
                data=json.dumps({"type": "chunk", "chunk": chunk}),
            ).to_string()
            presentation_content_text += chunk

        presentation_content_json = json.loads(presentation_content_text)

        presentation_content = PresentationOutlineModel(**presentation_content_json)
        presentation_content.slides = presentation_content.slides[
            : presentation.n_slides
        ]

        presentation.title = presentation_content.title
        presentation.outlines = [
            each.model_dump() for each in presentation_content.slides
        ]
        presentation.notes = presentation_content.notes

        with get_sql_session() as sql_session:
            sql_session.add(presentation)
            sql_session.commit()
            sql_session.refresh(presentation)

        yield SSECompleteResponse(
            key="presentation", value=presentation.model_dump_json()
        ).to_string()

    return StreamingResponse(inner(), media_type="text/event-stream")
