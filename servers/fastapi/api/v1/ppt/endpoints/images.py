from typing import Annotated, List
from fastapi import APIRouter, File, UploadFile

IMAGES_ROUTER = APIRouter(prefix="/images", tags=["Images"])


@IMAGES_ROUTER.post("/upload")
async def upload_images(images: Annotated[List[UploadFile], File()]):
    pass
