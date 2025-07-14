from http.client import HTTPException
from typing import List, Optional
import uuid
from fastapi import UploadFile

from api.v1.ppt.router import API_V1_PPT_ROUTER
from constants.documents import UPLOAD_ACCEPTED_DOCUMENTS, UPLOAD_ACCEPTED_IMAGES
from services import TEMP_FILE_SERVICE
from utils.validators import validate_files


@API_V1_PPT_ROUTER.post("/files/upload")
async def upload_files(files: Optional[List[UploadFile]]):
    if not files:
        raise HTTPException(400, "Files are required")

    temp_dir = TEMP_FILE_SERVICE.create_temp_dir(str(uuid.uuid4()))

    validate_files(files, True, True, 50, UPLOAD_ACCEPTED_DOCUMENTS)
    validate_files(files, True, True, 10, UPLOAD_ACCEPTED_IMAGES)

    temp_files: List[str] = []
    if files:
        for each_file in files:
            temp_path = TEMP_FILE_SERVICE.create_temp_file_path(
                each_file.filename, temp_dir
            )
            with open(temp_path, "wb") as f:
                content = await each_file.read()
                f.write(content)

            temp_files.append(temp_path)

    return temp_files
