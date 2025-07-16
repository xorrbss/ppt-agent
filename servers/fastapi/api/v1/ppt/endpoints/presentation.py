import json
import random
from typing import Annotated, List, Optional
import uuid
from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse

from models.presentation_outline_model import SlideOutlineModel
from models.presentation_layout import PresentationLayoutModel
from models.presentation_structure_model import PresentationStructureModel
from models.presentation_with_slides import PresentationWithSlides
from models.sql.slide import SlideModel
from models.sse_response import SSECompleteResponse, SSEResponse, SSEStatusResponse
from services import TEMP_FILE_SERVICE
from services.database import get_sql_session
from services.documents_loader import DocumentsLoader
from models.sql.presentation import PresentationModel
from utils.llm_calls.generate_document_summary import generate_document_summary
from utils.llm_calls.generate_presentation_structure import (
    generate_presentation_structure,
)
from utils.llm_calls.generate_slide_content import (
    get_slide_content_from_type_and_outline,
)

PRESENTATION_ROUTER = APIRouter(prefix="/presentation", tags=["Presentation"])


@PRESENTATION_ROUTER.post("/create", response_model=PresentationModel)
async def create_presentation(
    prompt: Annotated[str, Body()],
    n_slides: Annotated[int, Body()],
    language: Annotated[str, Body()],
    layout: Annotated[PresentationLayoutModel, Body()],
    file_paths: Annotated[Optional[List[str]], Body()] = None,
):
    presentation_id = str(uuid.uuid4())

    summary = None
    if file_paths:
        temp_dir = TEMP_FILE_SERVICE.create_temp_dir(presentation_id)
        documents_loader = DocumentsLoader(file_paths=file_paths)
        await documents_loader.load_documents(temp_dir)

        summary = await generate_document_summary(documents_loader.documents)

    presentation = PresentationModel(
        id=presentation_id,
        prompt=prompt,
        n_slides=n_slides,
        language=language,
        layout=layout.model_dump(),
        summary=summary,
    )

    with get_sql_session() as sql_session:
        sql_session.add(presentation)
        sql_session.commit()
        sql_session.refresh(presentation)

    return presentation


@PRESENTATION_ROUTER.post("/prepare", response_model=PresentationModel)
async def prepare_presentation(
    presentation_id: Annotated[str, Body()],
    outlines: Annotated[List[SlideOutlineModel], Body()],
    title: Annotated[Optional[str], Body()] = None,
):
    if not outlines:
        raise HTTPException(status_code=400, detail="Outlines are required")

    with get_sql_session() as sql_session:
        presentation = sql_session.get(PresentationModel, presentation_id)
        presentation.outlines = [each.model_dump() for each in outlines]
        presentation.title = title or presentation.title
        sql_session.commit()
        sql_session.refresh(presentation)

    layout = presentation.get_layout()
    total_slide_layouts = len(layout.slides)
    total_outlines = len(outlines)

    if layout.ordered:
        presentation_structure = layout.to_presentation_structure()
    else:
        presentation_structure: PresentationStructureModel = (
            await generate_presentation_structure(
                presentation_outline=presentation.get_presentation_outline(),
                presentation_layout=layout,
            )
        )
        presentation_structure.slides = presentation_structure.slides[: len(outlines)]
        for index in range(total_outlines):
            random_slide_index = random.randint(0, total_slide_layouts - 1)
            if index >= total_outlines:
                presentation_structure.slides.append(random_slide_index)
                continue
            if presentation_structure.slides[index] >= total_slide_layouts:
                presentation_structure.slides[index] = random_slide_index

    with get_sql_session() as sql_session:
        sql_session.add(presentation)
        presentation.set_structure(presentation_structure)
        sql_session.commit()
        sql_session.refresh(presentation)

    return presentation


@PRESENTATION_ROUTER.get("/stream", response_model=PresentationWithSlides)
async def stream_presentation(presentation_id: str):
    with get_sql_session() as sql_session:
        presentation = sql_session.get(PresentationModel, presentation_id)
        if not presentation:
            raise HTTPException(status_code=404, detail="Presentation not found")
        if not presentation.structure:
            raise HTTPException(
                status_code=400,
                detail="Presentation not prepared for stream",
            )
        if not presentation.outlines:
            raise HTTPException(
                status_code=400,
                detail="Outlines can not be empty",
            )

    async def inner():
        structure = presentation.get_structure()
        layout = presentation.get_layout()
        outline = presentation.get_presentation_outline()

        slides: List[SlideModel] = []
        yield SSEResponse(
            event="response",
            data=json.dumps({"type": "chunk", "chunk": '{ "slides": [ '}),
        ).to_string()
        for i, slide_layout_index in enumerate(structure.slides):
            slide_layout = layout.slides[slide_layout_index]
            slide_content = await get_slide_content_from_type_and_outline(
                slide_layout, outline.slides[i]
            )
            print(slide_content)
            slide = SlideModel(
                presentation=presentation_id,
                layout=slide_layout.id,
                content=slide_content,
            )
            slides.append(slide)
            yield SSEResponse(
                event="response",
                data=json.dumps({"type": "chunk", "chunk": slide.model_dump_json()}),
            ).to_string()

        yield SSEResponse(
            event="response",
            data=json.dumps({"type": "chunk", "chunk": " ] }"}),
        ).to_string()

        with get_sql_session() as sql_session:
            sql_session.add(presentation)
            sql_session.add_all(slides)
            sql_session.commit()
            sql_session.refresh(presentation)
            for each_slide in slides:
                sql_session.refresh(each_slide)

        response = PresentationWithSlides(
            **presentation.model_dump(),
            slides=slides,
        )

        yield SSECompleteResponse(
            key="presentation",
            value=response.model_dump(mode="json"),
        ).to_string()

    return StreamingResponse(inner(), media_type="text/event-stream")
