from typing import List
from fastapi import APIRouter, Depends, File, UploadFile, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.image_prompt import ImagePrompt
from models.sql.image_asset import ImageAsset
from services.database import get_async_session
from services.image_generation_service import ImageGenerationService
from utils.asset_directory_utils import get_images_directory
import os
from utils.asset_directory_utils import get_uploads_directory
import uuid
from services.image_upload_service import ImageUploadService

IMAGES_ROUTER = APIRouter(prefix="/images", tags=["Images"])


@IMAGES_ROUTER.get("/generate")
async def generate_image(
    prompt: str, sql_session: AsyncSession = Depends(get_async_session)
):
    images_directory = get_images_directory()
    image_prompt = ImagePrompt(prompt=prompt)
    image_generation_service = ImageGenerationService(images_directory)

    image = await image_generation_service.generate_image(image_prompt)
    if not isinstance(image, ImageAsset):
        return image

    sql_session.add(image)
    await sql_session.commit()

    return image.path


@IMAGES_ROUTER.get("/generated", response_model=List[ImageAsset])
async def get_generated_images(sql_session: AsyncSession = Depends(get_async_session)):
    try:
        images = await sql_session.scalars(
            select(ImageAsset).where(ImageAsset.is_uploaded == False).order_by(ImageAsset.created_at.desc())
        )
        return images
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve generated images: {str(e)}")

@IMAGES_ROUTER.post("/upload-image")
async def upload_image(file: UploadFile = File(...), sql_session: AsyncSession = Depends(get_async_session)):
    try:
        service = ImageUploadService(get_uploads_directory())
        image_asset = await service.upload_image(file)
        sql_session.add(image_asset)
        await sql_session.commit()
        return {
            "message": "Image uploaded successfully",
            "path": image_asset.path,
            "id": str(image_asset.id),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload image: {str(e)}")

@IMAGES_ROUTER.get("/uploaded", response_model=List[ImageAsset])
async def get_uploaded_images(sql_session: AsyncSession = Depends(get_async_session)):
    try:
        images = await sql_session.scalars(
            select(ImageAsset).where(ImageAsset.is_uploaded == True).order_by(ImageAsset.created_at.desc())
        )
        return images
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve uploaded images: {str(e)}")
    
    
@IMAGES_ROUTER.delete("/uploaded-image/{image_id}")
async def delete_image(image_id: uuid.UUID, sql_session: AsyncSession = Depends(get_async_session)):
    try:
        # Fetch the asset to get its actual file path
        image = await sql_session.get(ImageAsset, image_id)
        if not image:
            raise HTTPException(status_code=404, detail="Image not found")

        service = ImageUploadService(get_uploads_directory())
        await service.delete_image(image.path)

        await sql_session.delete(image)
        await sql_session.commit()
        return {"message": "Image deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete image: {str(e)}")
