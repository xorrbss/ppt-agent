import asyncio
import json
import random
from typing import Annotated, List, Optional
import uuid
from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import delete
from sqlmodel import select

from models.pptx_models import PptxPresentationModel
from models.presentation_outline_model import SlideOutlineModel
from models.presentation_layout import PresentationLayoutModel
from models.presentation_structure_model import PresentationStructureModel
from models.presentation_with_slides import PresentationWithSlides
from models.sql.slide import SlideModel
from models.sse_response import SSECompleteResponse, SSEResponse
from services import TEMP_FILE_SERVICE
from services.database import get_sql_session
from services.documents_loader import DocumentsLoader
from models.sql.presentation import PresentationModel
from services.pptx_presentation_creator import PptxPresentationCreator
from utils.llm_calls.generate_document_summary import generate_document_summary
from utils.llm_calls.generate_presentation_structure import (
    generate_presentation_structure,
)
from utils.llm_calls.generate_slide_content import (
    get_slide_content_from_type_and_outline,
)
from utils.process_slides import process_slide_and_fetch_assets

PRESENTATION_ROUTER = APIRouter(prefix="/presentation", tags=["Presentation"])


@PRESENTATION_ROUTER.get("/", response_model=PresentationWithSlides)
def get_presentation(id: str):
    with get_sql_session() as sql_session:
        presentation = sql_session.get(PresentationModel, id)
        if not presentation:
            raise HTTPException(404, "Presentation not found")
        slides = sql_session.exec(
            select(SlideModel)
            .where(SlideModel.presentation == id)
            .order_by(SlideModel.index)
        )
        return PresentationWithSlides(
            **presentation.model_dump(),
            slides=slides,
        )


@PRESENTATION_ROUTER.delete("/", status_code=204)
def delete_presentation(id: str):
    with get_sql_session() as sql_session:
        presentation = sql_session.get(PresentationModel, id)
        if not presentation:
            raise HTTPException(404, "Presentation not found")
        slides = sql_session.exec(
            select(SlideModel).where(SlideModel.presentation == id)
        ).all()
        for slide in slides:
            sql_session.delete(slide)
        sql_session.delete(presentation)
        sql_session.commit()


@PRESENTATION_ROUTER.get("/all", response_model=List[PresentationWithSlides])
def get_all_presentations():
    with get_sql_session() as sql_session:
        presentations_with_slides = []
        presentations = sql_session.exec(select(PresentationModel))
        for presentation in presentations:
            slides = sql_session.exec(
                select(SlideModel)
                .where(SlideModel.presentation == presentation.id)
                .where(SlideModel.index == 0)
            ).all()
            if not slides:
                continue
            presentations_with_slides.append(
                PresentationWithSlides(
                    **presentation.model_dump(),
                    slides=slides,
                )
            )
        return presentations_with_slides


@PRESENTATION_ROUTER.post("/create", response_model=PresentationModel)
async def create_presentation(
    prompt: Annotated[str, Body()],
    n_slides: Annotated[int, Body()],
    language: Annotated[str, Body()],
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
    layout: Annotated[PresentationLayoutModel, Body()],
    title: Annotated[Optional[str], Body()] = None,
):
    if not outlines:
        raise HTTPException(status_code=400, detail="Outlines are required")

    with get_sql_session() as sql_session:
        presentation = sql_session.get(PresentationModel, presentation_id)
        presentation.outlines = [each.model_dump() for each in outlines]
        presentation.title = title or presentation.title
        presentation.layout = layout.model_dump()
        sql_session.commit()
        sql_session.refresh(presentation)

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

        # These tasks will be gathered and awaited after all slides are generated
        async_assets_generation_tasks = []

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
            slide = SlideModel(
                presentation=presentation_id,
                layout_group=layout.name,
                layout=slide_layout.id,
                index=i,
                content=slide_content,
            )
            slides.append(slide)
            async_assets_generation_tasks.append(process_slide_and_fetch_assets(slide))
            yield SSEResponse(
                event="response",
                data=json.dumps({"type": "chunk", "chunk": slide.model_dump_json()}),
            ).to_string()

        yield SSEResponse(
            event="response",
            data=json.dumps({"type": "chunk", "chunk": " ] }"}),
        ).to_string()

        generated_assets_lists = await asyncio.gather(*async_assets_generation_tasks)
        generated_assets = []
        for assets_list in generated_assets_lists:
            generated_assets.extend(assets_list)

        with get_sql_session() as sql_session:
            sql_session.add(presentation)
            sql_session.add_all(slides)
            sql_session.add_all(generated_assets)
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


@PRESENTATION_ROUTER.put("/update", response_model=PresentationWithSlides)
def update_presentation(
    presentation_with_slides: Annotated[PresentationWithSlides, Body()],
):
    updated_presentation = presentation_with_slides.to_presentation_model()
    updated_slides = presentation_with_slides.slides
    with get_sql_session() as sql_session:
        presentation = sql_session.get(PresentationModel, updated_presentation.id)
        if not presentation:
            raise HTTPException(status_code=404, detail="Presentation not found")
        presentation.sqlmodel_update(updated_presentation)

        sql_session.exec(
            delete(SlideModel).where(SlideModel.presentation == updated_presentation.id)
        )
        sql_session.add_all(updated_slides)
        sql_session.commit()
        sql_session.refresh(presentation)
        for slide in updated_slides:
            sql_session.refresh(slide)

    return PresentationWithSlides(
        **presentation.model_dump(),
        slides=updated_slides,
    )


@PRESENTATION_ROUTER.post("/export/pptx")
def create_pptx(pptx_model: Annotated[PptxPresentationModel, Body()]):
    pptx_creator = PptxPresentationCreator(pptx_model)
    pptx_creator.create_ppt()
    pptx_creator.save(pptx_model.id)
