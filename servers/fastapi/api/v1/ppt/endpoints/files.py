import os
from typing import Annotated, List, Optional
from fastapi import APIRouter, Body, UploadFile, HTTPException

from constants.documents import TEXT_EXTENSIONS, UPLOAD_ACCEPTED_FILE_TYPES
from models.decomposed_file_info import DecomposedFileInfo
from services.document_conversion_service import DocumentConversionError
from services.temp_file_service import TEMP_FILE_SERVICE
from services.documents_loader import DocumentsLoader
import uuid
from utils.document_path_security import (
    create_server_upload_filename,
    validate_document_paths,
    validated_upload_extension,
)
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

_FASOO_DRM_PROBE_BYTES = 512


def _is_fasoo_drm_protected(file_path: str) -> bool:
    """Return True for Fasoo-wrapped files before an office converter sees them."""

    try:
        with open(file_path, "rb") as uploaded_file:
            header = uploaded_file.read(_FASOO_DRM_PROBE_BYTES)
    except OSError:
        return False

    return b"DRMONE" in header and b"Fasoo DRM" in header


def _reject_known_drm_file(file_path: str, original_filename: str) -> None:
    if not _is_fasoo_drm_protected(file_path):
        return

    raise HTTPException(
        status_code=422,
        detail={
            "code": "DOCUMENT_DRM_PROTECTED",
            "message": (
                f"'{original_filename}' 파일은 Fasoo DRM으로 보호되어 내용을 읽을 수 없습니다. "
                "DRM을 해제하거나 PowerPoint에서 DRM 없는 .pptx 파일로 다시 저장한 뒤 "
                "업로드해 주세요."
            ),
            "filename": original_filename,
            "retryable": False,
        },
    )

@FILES_ROUTER.get("/upload-limits")
async def get_upload_limits():
    """Expose effective limits so clients can validate and explain them."""

    return upload_limits_payload()


@FILES_ROUTER.post("/upload", response_model=List[str])
async def upload_files(files: Optional[List[UploadFile]]):
    if not files:
        raise HTTPException(400, "Documents are required")

    single_limit = get_single_upload_limit_bytes()
    total_limit = get_total_upload_limit_bytes()
    upload_extensions = [
        validated_upload_extension(each_file.filename, UPLOAD_ACCEPTED_FILE_TYPES)
        for each_file in files
    ]
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

    temp_dir = TEMP_FILE_SERVICE.create_temp_dir(str(uuid.uuid4()))
    temp_files: List[str] = []
    actual_total = 0
    try:
        for each_file, extension in zip(files, upload_extensions):
            temp_path = TEMP_FILE_SERVICE.create_temp_file_path(
                create_server_upload_filename(extension), temp_dir
            )
            # Track the destination before streaming so a mid-stream size or
            # I/O failure cannot leave a partial upload behind.
            temp_files.append(temp_path)
            actual_total += await stream_upload_to_file(
                each_file,
                temp_path,
                limit_bytes=single_limit,
                label="Document",
            )
            _reject_known_drm_file(
                temp_path,
                each_file.filename or "uploaded document",
            )
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
    file_paths = validate_document_paths(file_paths)
    temp_dir = TEMP_FILE_SERVICE.create_temp_dir(str(uuid.uuid4()))

    # Run text files through DocumentsLoader as well. It reads TXT/Markdown
    # directly, normalizes their encoding/newlines, and preserves their body.
    documents_loader = DocumentsLoader(
        file_paths=file_paths,
        presentation_language=language,
    )
    try:
        await documents_loader.load_documents(temp_dir)
    except DocumentConversionError as exc:
        # Unconvertible/unsupported document — surface an actionable message
        # instead of a raw 500 (the frontend shows `detail` directly).
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    parsed_documents = documents_loader.documents

    response = []
    for index, parsed_doc in enumerate(parsed_documents):
        file_path = TEMP_FILE_SERVICE.create_temp_file_path(
            f"{uuid.uuid4()}.txt", temp_dir
        )
        source_extension = os.path.splitext(file_paths[index])[1].lower()
        if source_extension not in TEXT_EXTENSIONS:
            parsed_doc = parsed_doc.replace("<br>", "\n")
        with open(file_path, "w", encoding="utf-8", newline="\n") as text_file:
            text_file.write(parsed_doc)
        response.append(
            DecomposedFileInfo(
                name=os.path.basename(file_paths[index]), file_path=file_path
            )
        )

    return response
