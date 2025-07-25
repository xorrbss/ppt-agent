import asyncio
import json
import os
import random
from typing import Annotated, List, Optional
import uuid, aiohttp
from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import delete
from sqlmodel import select
from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
from models.pptx_models import PptxPresentationModel
from models.presentation_layout import PresentationLayoutModel
from models.presentation_structure_model import PresentationStructureModel
from models.presentation_with_slides import PresentationWithSlides
from models.generate_presentation_api import (
    GeneratePresentationRequest,
    PresentationAndPath,
    PresentationPathAndEditPath,
)
from services.get_layout_by_name import get_layout_by_name
from utils.llm_calls.generate_presentation_outlines import generate_ppt_outline
from models.sql.slide import SlideModel
from models.sse_response import SSECompleteResponse, SSEResponse
from services import TEMP_FILE_SERVICE
from services.database import get_sql_session
from services.documents_loader import DocumentsLoader
from models.sql.presentation import PresentationModel
from services.pptx_presentation_creator import PptxPresentationCreator
from utils.asset_directory_utils import get_exports_directory
from utils.llm_calls.generate_document_summary import generate_document_summary
from utils.llm_calls.generate_presentation_structure import (
    generate_presentation_structure,
)
from utils.llm_calls.generate_slide_content import (
    get_slide_content_from_type_and_outline,
)
from utils.process_slides import process_slide_and_fetch_assets
from utils.randomizers import get_random_uuid

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
        presentation.outlines = [each.model_dump() for each in outlines]
        presentation.title = title or presentation.title
        presentation.set_layout(layout)
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

            # This will mutate slide
            async_assets_generation_tasks.append(process_slide_and_fetch_assets(slide))

            # Give control to the event loop
            await asyncio.sleep(0)

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


@PRESENTATION_ROUTER.post("/export/pptx", response_model=str)
async def create_pptx(pptx_model: Annotated[PptxPresentationModel, Body()]):
    temp_dir = TEMP_FILE_SERVICE.create_temp_dir()

    pptx_creator = PptxPresentationCreator(pptx_model, temp_dir)
    await pptx_creator.create_ppt()

    export_directory = get_exports_directory()
    pptx_path = os.path.join(
        export_directory, f"{pptx_model.name or get_random_uuid()}.pptx"
    )
    pptx_creator.save(pptx_path)

    return pptx_path

@PRESENTATION_ROUTER.post("/generate")
async def generate_presentation_api(data: Annotated[GeneratePresentationRequest, Body()]):
    presentation_id = str(uuid.uuid4())
    print("**" * 40)
    print(f"Generating presentation with ID: {presentation_id}")
    print(f"Received Body as JSON: {data.model_dump_json(indent=2)}")

    # 1. Save uploaded files
    file_paths = []
    if data.documents:
        temp_dir = TEMP_FILE_SERVICE.create_temp_dir()
        for upload in data.documents:
            file_path = os.path.join(temp_dir, upload.filename)
            with open(file_path, "wb") as f:
                f.write(await upload.read())
            file_paths.append(file_path)

    # 2. Create Presentation Summary (if documents are provided)
    summary = None
    if file_paths:
        temp_dir = TEMP_FILE_SERVICE.create_temp_dir(presentation_id)
        documents_loader = DocumentsLoader(file_paths=file_paths)
        await documents_loader.load_documents(temp_dir)
        summary = await generate_document_summary(documents_loader.documents)

    # 3. Generate Outlines
    presentation_content_text = ""
    async for chunk in generate_ppt_outline(
        data.prompt,
        data.n_slides,
        data.language,
        summary,
    ):
        presentation_content_text += chunk

    presentation_content_json = json.loads(presentation_content_text)
    presentation_content = PresentationOutlineModel(**presentation_content_json)
    outlines = presentation_content.slides[:data.n_slides]
    total_outlines = len(outlines)

    print("-" * 40)
    print("Generated Presentation Content:", presentation_content_text)
    print(f"Generated {total_outlines} outlines for the presentation")
    print(f"Presentation Title: {presentation_content.title}")

    # 4. Parse Layouts
    layout = await get_layout_by_name(data.layout)
    total_slide_layouts = len(layout.slides)

    # 5. Generate Structure
    if layout.ordered:
        presentation_structure = layout.to_presentation_structure()
    else:
        presentation_structure: PresentationStructureModel = (
            await generate_presentation_structure(
                presentation_outline=PresentationOutlineModel(
                    title=presentation_content.title,
                    slides=outlines,
                    notes=presentation_content.notes,
                ),
                presentation_layout=layout,
            )
        )

    presentation_structure.slides = presentation_structure.slides[:total_outlines]
    for index in range(total_outlines):
        random_slide_index = random.randint(0, total_slide_layouts - 1)
        if index >= total_outlines:
            presentation_structure.slides.append(random_slide_index)
            continue
        if presentation_structure.slides[index] >= total_slide_layouts:
            presentation_structure.slides[index] = random_slide_index

    # 6. Create and Save PresentationModel
    presentation = PresentationModel(
        id=presentation_id,
        prompt=data.prompt,
        n_slides=data.n_slides,
        language=data.language,
        title=presentation_content.title,
        summary=summary,
        outlines=[each.model_dump() for each in outlines],
        notes=presentation_content.notes,
        layout=layout.model_dump(),
        structure=presentation_structure.model_dump(),
    )
    with get_sql_session() as sql_session:
        sql_session.add(presentation)
        sql_session.commit()
        sql_session.refresh(presentation)

    # 7. Generate slide content and save slides
    slides: List[SlideModel] = []
    slide_contents: List[dict] = []
    for i, slide_layout_index in enumerate(presentation_structure.slides):
        slide_layout = layout.slides[slide_layout_index]
        print(f"Generating content for slide {i} with layout {slide_layout.id}")
        slide_content = await get_slide_content_from_type_and_outline(
            slide_layout, outlines[i]
        )
        print(f"Generated content for slide {i}: {json.dumps(slide_content, indent=2)}")
        slide = SlideModel(
            presentation=presentation_id,
            layout_group=layout.name,
            layout=slide_layout.id,
            index=i,
            content=slide_content,
        )
        slides.append(slide)
        slide_contents.append(slide_content)

    # Process slides to fetch assets (images, icons, etc.)
    print("Processing slides to fetch assets")
    for slide in slides:
        try:
            await process_slide_and_fetch_assets(slide)
            print(f"Processed slide {slide.index} successfully")
        except Exception as e:
            print(f"Error processing slide {slide.index}: {e}")

    with get_sql_session() as sql_session:
        sql_session.add_all(slides)
        sql_session.commit()

    # 8. Export as PPTX
    if data.export_as == "pptx":
        print("-" * 40)
        print("Exporting Presentation as PPTX")

        # Get the converted PPTX model from your existing Next.js service
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://localhost/api/presentation_to_pptx_model?id={presentation_id}"
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    print(f"Failed to get PPTX model: {error_text}")
                    raise HTTPException(status_code=500, detail="Failed to convert presentation to PPTX model")
                pptx_model_data = await response.json()
        print(f"Received PPTX model data: {json.dumps(pptx_model_data, indent=2)}")

        # Create PPTX file using the converted model
        pptx_model = PptxPresentationModel(**pptx_model_data)
        print(f"Creating PPTX with model: {pptx_model.model_dump_json(indent=2)}")
        temp_dir = TEMP_FILE_SERVICE.create_temp_dir()
        pptx_creator = PptxPresentationCreator(pptx_model, temp_dir)
        await pptx_creator.create_ppt()

        export_directory = get_exports_directory()
        pptx_path = os.path.join(
            export_directory, f"{presentation_content.title}.pptx"
        )
        pptx_creator.save(pptx_path)

        presentation_and_path = PresentationAndPath(
            presentation_id=presentation_id,
            path=pptx_path,
        )
    else:
        print("-" * 40)
        print("Exporting Presentation as PDF")

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "http://localhost/api/export-as-pdf",
                json={
                    "id": presentation_id,
                    "title": presentation_content.title,
                },
            ) as response:
                response_json = await response.json()

        print(f"Received PDF export response: {json.dumps(response_json, indent=2)}")

        presentation_and_path = PresentationAndPath(
            presentation_id=presentation_id,
            path=response_json["path"],
        )

    return PresentationPathAndEditPath(
        **presentation_and_path.model_dump(),
        edit_path=f"/presentation?id={presentation_id}",
    )
