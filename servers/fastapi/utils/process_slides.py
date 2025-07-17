import asyncio
from typing import List, Tuple
from models.image_prompt import ImagePrompt
from models.sql.image_asset import ImageAsset
from models.sql.slide import SlideModel
from services.icon_finder_service import IconFinderService
from services.image_generation_service import ImageGenerationService
from utils.asset_directory_utils import get_images_directory
from utils.dict_utils import get_dict_at_path, get_dict_paths_with_key, set_dict_at_path


async def process_slide_and_fetch_assets(
    slide: SlideModel,
) -> List[ImageAsset]:
    image_directory = get_images_directory()

    image_generation_service = ImageGenerationService(image_directory)
    icon_finder_service = IconFinderService()

    async_tasks = []

    image_paths = get_dict_paths_with_key(slide.content, "__image_prompt__")
    icon_paths = get_dict_paths_with_key(slide.content, "__icon_query__")

    for image_path in image_paths:
        image_prompt_parent = get_dict_at_path(slide.content, image_path)
        async_tasks.append(
            image_generation_service.generate_image(
                ImagePrompt(
                    prompt=image_prompt_parent["__image_prompt__"],
                )
            )
        )

    for icon_path in icon_paths:
        icon_query_parent = get_dict_at_path(slide.content, icon_path)
        async_tasks.append(
            icon_finder_service.search_icons(icon_query_parent["__icon_query__"])
        )

    results = await asyncio.gather(*async_tasks)
    results.reverse()

    return_assets = []
    for image_path in image_paths:
        image_dict = get_dict_at_path(slide.content, image_path)
        result = results.pop()
        if isinstance(result, ImageAsset):
            return_assets.append(result)
            image_dict["__image_url__"] = result.path
        else:
            image_dict["__image_url__"] = result
        set_dict_at_path(slide.content, image_path, image_dict)

    for icon_path in icon_paths:
        icon_dict = get_dict_at_path(slide.content, icon_path)
        icon_dict["__icon_url__"] = results.pop()[0]
        set_dict_at_path(slide.content, icon_path, icon_dict)

    return return_assets
