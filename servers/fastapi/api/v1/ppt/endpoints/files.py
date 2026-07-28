import os
from typing import Annotated, List, Optional
from fastapi import APIRouter, Body, UploadFile, HTTPException

from constants.documents import UPLOAD_ACCEPTED_FILE_TYPES
from models.decomposed_file_info import DecomposedFileInfo
from services.document_conversion_service import DocumentConversionError
from services.temp_file_service import TEMP_FILE_SERVICE
from services.documents_loader import DocumentsLoader
import uuid
from utils.get_env import get_app_data_directory_env
from utils.upload_limits import (
    MIB,
    format_limit,
    get_single_upload_limit_bytes,
    get_total_upload_limit_bytes,
    stream_upload_to_file,
    upload_limits_payload,
)
from utils.validators import validate_files

FILES_ROUTER = APIRouter(prefix="/files", tags=["Files"])


def _ensure_within_allowed(path: str) -> str:
    """Reject a client-supplied path that escapes the temp / app-data roots.

    /files/decompose reads whatever paths the client sends; without this an
    authenticated caller (or anyone in DISABLE_AUTH mode) could read arbitrary
    files. Legit paths come from /files/upload (under the temp base dir); uploads
    may also live under app_data. Compared via realpath so symlinks/.. can't escape.
    """
    roots = [os.path.realpath(TEMP_FILE_SERVICE.base_dir)]
    app_data = get_app_data_directory_env()
    if app_data:
        roots.append(os.path.realpath(app_data))
    real = os.path.realpath(path)
    for root in roots:
        if real == root or real.startswith(root + os.sep):
            return real
    raise HTTPException(400, "file_path is outside the allowed directories")


@FILES_ROUTER.get("/upload-limits")
async def get_upload_limits():
    """Expose effective limits so clients can validate and explain them."""

    return upload_limits_payload()


@FILES_ROUTER.post("/upload", response_model=List[str])
async def upload_files(files: Optional[List[UploadFile]]):
    if not files:
        raise HTTPException(400, "Documents are required")

    temp_dir = TEMP_FILE_SERVICE.create_temp_dir(str(uuid.uuid4()))

    single_limit = get_single_upload_limit_bytes()
    total_limit = get_total_upload_limit_bytes()
    validate_files(
        files,
        True,
        True,
        single_limit // MIB,
        UPLOAD_ACCEPTED_FILE_TYPES,
    )
    declared_total = sum(each_file.size or 0 for each_file in files)
    if declared_total > total_limit:
        raise HTTPException(
            status_code=413,
            detail=(
                "Combined upload size exceeds the "
                f"{format_limit(total_limit)} request limit."
            ),
        )

    temp_files: List[str] = []
    actual_total = 0
    try:
        for each_file in files:
            temp_path = TEMP_FILE_SERVICE.create_temp_file_path(
                each_file.filename, temp_dir
            )
            actual_total += await stream_upload_to_file(
                each_file,
                temp_path,
                limit_bytes=single_limit,
                label="Document",
            )
            temp_files.append(temp_path)
            if actual_total > total_limit:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        "Combined upload size exceeds the "
                        f"{format_limit(total_limit)} request limit."
                    ),
                )
    except Exception:
        for temp_path in temp_files:
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise

    return temp_files


@FILES_ROUTER.post("/decompose", response_model=List[DecomposedFileInfo])
async def decompose_files(
    file_paths: Annotated[List[str], Body(embed=True)],
    language: Annotated[Optional[str], Body()] = None,
):
    file_paths = [_ensure_within_allowed(p) for p in file_paths]
    temp_dir = TEMP_FILE_SERVICE.create_temp_dir(str(uuid.uuid4()))

    txt_files = []
    other_files = []
    for file_path in file_paths:
        if file_path.endswith(".txt"):
            txt_files.append(file_path)
        else:
            other_files.append(file_path)

    documents_loader = DocumentsLoader(file_paths=other_files, presentation_language=language)
    try:
        await documents_loader.load_documents(temp_dir)
    except DocumentConversionError as exc:
        # Unconvertible/unsupported document — surface an actionable message
        # instead of a raw 500 (the frontend shows `detail` directly).
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    parsed_documents = documents_loader.documents

    response = []
    for index, parsed_doc in enumerate(parsed_documents):
        file_path = TEMP_FILE_SERVICE.create_temp_file_path(
            f"{uuid.uuid4()}.txt", temp_dir
        )
        parsed_doc = parsed_doc.replace("<br>", "\n")
        with open(file_path, "w", encoding="utf-8") as text_file:
            text_file.write(parsed_doc)
        response.append(
            DecomposedFileInfo(
                name=os.path.basename(other_files[index]), file_path=file_path
            )
        )

    # Return the txt documents as it is
    for each_file in txt_files:
        response.append(
            DecomposedFileInfo(name=os.path.basename(each_file), file_path=each_file)
        )

    return response
