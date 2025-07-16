import os
from typing import List, Tuple
from models.presentation_layout import SlideLayoutModel
from models.sql.asset import ImageAsset
from models.sql.slide import SlideModel
from services import SCHEMA_PROCESSOR
from services.icon_finder_service import IconFinderService
from services.image_generation_service import ImageGenerationService
from utils.get_env import get_app_data_directory_env


async def process_slide_and_fetch_assets(
    slide: SlideModel, layout: SlideLayoutModel
) -> SlideModel:
    image_directory = os.path.join(get_app_data_directory_env(), "images")

    image_generation_service = ImageGenerationService(image_directory)
    icon_finder_service = IconFinderService()

    image_type_paths = SCHEMA_PROCESSOR.find_dict_with_key(
        slide.content, "__image_type__"
    )
    for path in image_type_paths:
        image_dict = SCHEMA_PROCESSOR.get_dict_at_path(slide.content, path)
        image_prompt = image_dict["prompt"]
        if image_dict["__image_type__"] == "image":
            image_path = await image_generation_service.generate_image(image_prompt)
            image_dict["url"] = image_path
        else:
            icon_path = await icon_finder_service.search_icons(image_prompt)
            image_dict["url"] = icon_path[0]

        SCHEMA_PROCESSOR.set_dict_at_path(slide.content, path, image_dict)
