import asyncio
from typing import Annotated
from fastapi import APIRouter, Body, HTTPException

from models.image_prompt import ImagePrompt
from models.presentation_outline_model import SlideOutlineModel
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.database import get_sql_session
from services.icon_finder_service import IconFinderService
from services.image_generation_service import ImageGenerationService
from utils.asset_directory_utils import get_images_directory
from utils.dict_utils import get_dict_at_path, get_dict_paths_with_key, set_dict_at_path
from utils.llm_calls.edit_slide import get_edited_slide_content
from utils.llm_calls.select_slide_type_on_edit import get_slide_layout_from_prompt


SLIDE_ROUTER = APIRouter(prefix="/slide", tags=["Slide"])


@SLIDE_ROUTER.post("/edit")
async def edit_slide(id: Annotated[str, Body()], prompt: Annotated[str, Body()]):

    with get_sql_session() as sql_session:
        slide = sql_session.get(SlideModel, id)
        if not slide:
            raise HTTPException(status_code=404, detail="Slide not found")
        presentation = sql_session.get(PresentationModel, slide.presentation)
        if not presentation:
            raise HTTPException(status_code=404, detail="Presentation not found")

    presentation_layout = presentation.get_layout()

    slide_layout = await get_slide_layout_from_prompt(
        prompt, presentation_layout, slide
    )
    edited_slide_content = await get_edited_slide_content(
        prompt, slide_layout, slide, presentation.language
    )

    # Get old image and icon dicts
    old_image_dict_paths = get_dict_paths_with_key(slide.content, "__image_prompt__")
    old_image_dicts = [
        get_dict_at_path(slide.content, path) for path in old_image_dict_paths
    ]
    old_image_prompts = [
        old_image_dict["__image_prompt__"] for old_image_dict in old_image_dicts
    ]
    old_icon_dict_paths = get_dict_paths_with_key(slide.content, "__icon_query__")
    old_icon_dicts = [
        get_dict_at_path(slide.content, path) for path in old_icon_dict_paths
    ]
    old_icon_queries = [
        old_icon_dict["__icon_query__"] for old_icon_dict in old_icon_dicts
    ]

    # Get new image and icon dicts
    new_image_dict_paths = get_dict_paths_with_key(
        edited_slide_content, "__image_prompt__"
    )
    new_image_dicts = [
        get_dict_at_path(edited_slide_content, path) for path in new_image_dict_paths
    ]
    new_icon_dict_paths = get_dict_paths_with_key(
        edited_slide_content, "__icon_query__"
    )
    new_icon_dicts = [
        get_dict_at_path(edited_slide_content, path) for path in new_icon_dict_paths
    ]

    image_generation_service = ImageGenerationService(get_images_directory())
    icon_finder_service = IconFinderService()

    async_image_fetch_tasks = []
    new_images_fetch_status = []

    async_icon_fetch_tasks = []
    new_icons_fetch_status = []

    for new_image in new_image_dicts:
        # Use old image url if prompt is same
        if new_image["__image_prompt__"] in old_image_prompts:
            old_image_url = old_image_dicts[
                old_image_prompts.index(new_image["__image_prompt__"])
            ]["__image_url__"]
            new_image["__image_url__"] = old_image_url
            new_images_fetch_status.append(False)
            continue

        async_image_fetch_tasks.append(
            image_generation_service.generate_image(
                ImagePrompt(
                    prompt=new_image["__image_prompt__"],
                )
            )
        )
        new_images_fetch_status.append(True)

    for new_icon in new_icon_dicts:
        if new_icon["__icon_query__"] in old_icon_queries:
            old_icon_url = old_icon_dicts[
                old_icon_queries.index(new_icon["__icon_query__"])
            ]["__icon_url__"]
            new_icon["__icon_url__"] = old_icon_url
            new_icons_fetch_status.append(False)
            continue

        async_icon_fetch_tasks.append(
            icon_finder_service.search_icons(new_icon["__icon_query__"])
        )
        new_icons_fetch_status.append(True)

    new_images = await asyncio.gather(*async_image_fetch_tasks)
    new_icons = await asyncio.gather(*async_icon_fetch_tasks)

    for i, new_image in enumerate(new_images):
        if new_images_fetch_status[i]:
            new_image_dicts[i]["__image_url__"] = new_images[i]

    for i, new_icon in enumerate(new_icons):
        if new_icons_fetch_status[i]:
            new_icon_dicts[i]["__icon_url__"] = new_icons[i]

    for i, new_image_dict in enumerate(new_image_dicts):
        set_dict_at_path(edited_slide_content, new_image_dict_paths[i], new_image_dict)

    for i, new_icon_dict in enumerate(new_icon_dicts):
        set_dict_at_path(edited_slide_content, new_icon_dict_paths[i], new_icon_dict)

    with get_sql_session() as sql_session:
        sql_session.add(slide)
        slide.content = edited_slide_content
        sql_session.commit()
        sql_session.refresh(slide)

    return slide
