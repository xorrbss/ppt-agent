from fastapi import UploadFile
import os
import uuid

from models.sql.image_asset import ImageAsset
from utils.asset_directory_utils import get_uploads_directory


class ImageUploadService:
    """Handles saving uploaded images to disk and returning ImageAsset models."""

    def __init__(self, output_directory: str | None = None):
        # Prefer provided directory, otherwise resolve from app data directory
        self.uploads_directory = output_directory or get_uploads_directory()
        os.makedirs(self.uploads_directory, exist_ok=True)

    async def upload_image(self, file: UploadFile) -> ImageAsset:
        """Save the uploaded file to disk and return an ImageAsset (not committed).

        The caller is responsible for adding the returned ImageAsset to the
        database session and committing.
        """
        file_extension = os.path.splitext(file.filename)[1] if file.filename else ""
        unique_filename = f"{uuid.uuid4()}{file_extension}"
        file_path = os.path.join(self.uploads_directory, unique_filename)

        content = await file.read()
        with open(file_path, "wb") as buffer:
            buffer.write(content)

        image_asset = ImageAsset(
            path=file_path,
            is_uploaded=True,
            extras={
                "original_filename": file.filename,
                "content_type": file.content_type,
                "file_size": len(content),
            },
        )

        return image_asset

    async def delete_image(self, file_path: str) -> bool:
        """Delete an image file from disk by its absolute path."""
        if not file_path:
            return False
        if not os.path.isabs(file_path):
            # Ensure we only operate on absolute paths that we generated
            file_path = os.path.join(self.uploads_directory, file_path)
        if not os.path.exists(file_path):
            return False
        os.remove(file_path)
        return True