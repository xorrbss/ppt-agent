import os
from fastapi import APIRouter, Body

from models.image_prompt import ImagePrompt
from services.image_generation_service import ImageGenerationService
from utils.get_env import get_app_data_directory_env

IMAGES_ROUTER = APIRouter(prefix="/images", tags=["Images"])


@IMAGES_ROUTER.get("/generate")
async def generate_image(prompt: str):
    images_directory = os.path.join(get_app_data_directory_env(), "images")
    image_prompt = ImagePrompt(prompt=prompt)
    image_generation_service = ImageGenerationService(images_directory)

    return await image_generation_service.generate_image(image_prompt)
