from fastapi import APIRouter

from models.image_prompt import ImagePrompt
from services.image_generation_service import ImageGenerationService
from utils.asset_directory_utils import get_images_directory

IMAGES_ROUTER = APIRouter(prefix="/images", tags=["Images"])


@IMAGES_ROUTER.get("/generate")
async def generate_image(prompt: str):
    images_directory = get_images_directory()
    image_prompt = ImagePrompt(prompt=prompt)
    image_generation_service = ImageGenerationService(images_directory)

    return await image_generation_service.generate_image(image_prompt)
