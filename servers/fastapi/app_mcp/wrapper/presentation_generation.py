import random
from typing import List, Dict, Any, Optional
from models.presentation_outline_model import SlideOutlineModel
from models.presentation_layout import PresentationLayoutModel
from models.presentation_structure_model import PresentationStructureModel
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from utils.get_layout_by_name import get_layout_by_name
from utils.llm_calls.generate_presentation_structure import generate_presentation_structure
from utils.llm_calls.generate_slide_content import get_slide_content_from_type_and_outline
from services.image_generation_service import ImageGenerationService
from services.icon_finder_service import IconFinderService
from utils.asset_directory_utils import get_images_directory
from utils.process_slides import process_slide_and_fetch_assets
from models.presentation_outline_model import PresentationOutlineModel
from utils.randomizers import get_random_uuid
import asyncio
from services.database import get_async_session
from sqlalchemy.ext.asyncio import AsyncSession


# Standalone function for workflow orchestrator
async def process_post_outline_workflow(
    title: str,
    outlines: List[Dict[str, Any]],
    notes: Optional[str]=[],
    layout: str = "general",
    language: str = "English",
    prompt: str = "",
    n_slides: int = 8,
    sql_session: Optional[AsyncSession] = None,
) -> Dict[str, Any]:
    """
    Process the workflow after outlines are generated: layout, structure, slides, assets, save, and ask for export.
    """
    # 1. Parse Layout
    layout_model: PresentationLayoutModel = await get_layout_by_name(layout)
    total_slide_layouts = len(layout_model.slides)

    # 2. Generate Structure
    if layout_model.ordered:
        presentation_structure = layout_model.to_presentation_structure()
    else:
        presentation_structure: PresentationStructureModel = (
            await generate_presentation_structure(
                presentation_outline=PresentationOutlineModel(
                    title=title,
                    slides=outlines,
                    notes=notes,
                ),
                presentation_layout=layout_model,
            )
        )
    presentation_structure.slides = presentation_structure.slides[:n_slides]
    for index in range(n_slides):
        random_slide_index = random.randint(0, total_slide_layouts - 1)
        if index >= n_slides:
            presentation_structure.slides.append(random_slide_index)
            continue
        if presentation_structure.slides[index] >= total_slide_layouts:
            presentation_structure.slides[index] = random_slide_index

    # 3. Create PresentationModel
    presentation_id = get_random_uuid()
    presentation = PresentationModel(
        id=presentation_id,
        title=title,
        n_slides=n_slides,
        language=language,
        outlines=outlines,
        prompt=prompt,
        notes=notes,
        layout=layout_model.model_dump(),
        structure=presentation_structure.model_dump(),
    )

    image_generation_service = ImageGenerationService(get_images_directory())
    icon_finder_service = IconFinderService()
    async_asset_generation_tasks = []

    # 4. Generate slide content and save slides
    slides: List[SlideModel] = []
    for i, slide_layout_index in enumerate(presentation_structure.slides):
        slide_layout = layout_model.slides[slide_layout_index]
        slide_content = await get_slide_content_from_type_and_outline(
            slide_layout, SlideOutlineModel(**outlines[i]), language
        )
        slide = SlideModel(
            presentation=presentation_id,
            layout_group=layout_model.name,
            layout=slide_layout.id,
            index=i,
            content=slide_content,
        )
        async_asset_generation_tasks.append(
            process_slide_and_fetch_assets(
                image_generation_service, icon_finder_service, slide
            )
        )
        slides.append(slide)

    generated_assets_lists = await asyncio.gather(*async_asset_generation_tasks)
    generated_assets = []
    for assets_list in generated_assets_lists:
        generated_assets.extend(assets_list)

    # 5. Save PresentationModel and Slides
    if sql_session is None:
        from services.database import get_async_session
        async for session in get_async_session():
            session.add(presentation)
            session.add_all(slides)
            session.add_all(generated_assets)
            await session.commit()
    else:
        sql_session.add(presentation)
        sql_session.add_all(slides)
        sql_session.add_all(generated_assets)
        await sql_session.commit()


    # 6. Ask user if they want to export and in which format
    return {
        "presentation_id": presentation_id,
        "title": title,
        "message": "Presentation is ready. Would you like to export? (pdf or pptx)",
        "export_options": ["pdf", "pptx"]
    }
