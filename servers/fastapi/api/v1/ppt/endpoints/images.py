from typing import Annotated
from fastapi import APIRouter, Body

from models.image_prompt import ImagePrompt
from services import TEMP_FILE_SERVICE
from services.image_generation_service import ImageGenerationService

IMAGES_ROUTER = APIRouter(prefix="/images", tags=["Images"])


@IMAGES_ROUTER.get("/generate")
async def generate_image(prompt: str):
    temp_dir = TEMP_FILE_SERVICE.create_temp_dir()
    image_prompt = ImagePrompt(prompt=prompt)
    image_generation_service = ImageGenerationService(temp_dir)

    return await image_generation_service.generate_image(image_prompt)
