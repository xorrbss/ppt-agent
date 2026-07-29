import os
import uuid
from pathlib import PurePosixPath, PureWindowsPath
from typing import Iterable, List, Optional, Sequence

from fastapi import HTTPException

from utils.get_env import get_app_data_directory_env


INVALID_DOCUMENT_PATH_DETAIL = "Invalid document file path."
INVALID_UPLOAD_FILENAME_DETAIL = "Invalid upload filename."


def _allowed_document_roots() -> List[str]:
    # Imported lazily to avoid a module-import cycle:
    # TempFileService itself imports environment helpers.
    from services.temp_file_service import TEMP_FILE_SERVICE

    roots = [TEMP_FILE_SERVICE.base_dir]
    app_data = get_app_data_directory_env()
    if app_data:
        roots.append(app_data)
    return [os.path.realpath(root) for root in roots]


def _path_is_below_root(path: str, root: str) -> bool:
    try:
        normalized_path = os.path.normcase(path)
        normalized_root = os.path.normcase(root)
        return (
            os.path.commonpath([normalized_path, normalized_root])
            == normalized_root
        )
    except (OSError, ValueError):
        # ValueError includes paths on different Windows drives.
        return False


def validate_document_path(
    path: str,
    *,
    allowed_roots: Optional[Sequence[str]] = None,
) -> str:
    """Return a canonical, allowed regular-file path.

    Paths are resolved before the containment check, so ``..`` and symlink
    escapes cannot leave the server-owned temporary or app-data directories.
    Client-supplied values are deliberately omitted from errors.
    """

    if not isinstance(path, str) or not path or "\x00" in path:
        raise HTTPException(status_code=400, detail=INVALID_DOCUMENT_PATH_DETAIL)

    try:
        real_path = os.path.realpath(path)
        roots = [
            os.path.realpath(root)
            for root in (allowed_roots or _allowed_document_roots())
            if root
        ]
    except (OSError, ValueError, TypeError):
        raise HTTPException(
            status_code=400, detail=INVALID_DOCUMENT_PATH_DETAIL
        ) from None

    if not any(_path_is_below_root(real_path, root) for root in roots):
        raise HTTPException(status_code=400, detail=INVALID_DOCUMENT_PATH_DETAIL)

    if not os.path.isfile(real_path):
        raise HTTPException(status_code=400, detail=INVALID_DOCUMENT_PATH_DETAIL)

    return real_path


def validate_document_paths(
    paths: Iterable[str],
    *,
    allowed_roots: Optional[Sequence[str]] = None,
) -> List[str]:
    validated: List[str] = []
    seen = set()

    for path in paths:
        real_path = validate_document_path(path, allowed_roots=allowed_roots)
        identity = os.path.normcase(real_path)
        if identity in seen:
            raise HTTPException(
                status_code=400,
                detail="Duplicate document file path.",
            )
        seen.add(identity)
        validated.append(real_path)

    return validated


def validated_upload_extension(
    filename: str,
    accepted_types: Sequence[str],
) -> str:
    """Validate an untrusted display filename and return its safe extension."""

    if not isinstance(filename, str) or not filename or "\x00" in filename:
        raise HTTPException(status_code=400, detail=INVALID_UPLOAD_FILENAME_DETAIL)

    posix_path = PurePosixPath(filename)
    windows_path = PureWindowsPath(filename)
    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or len(posix_path.parts) != 1
        or len(windows_path.parts) != 1
        or filename in {".", ".."}
    ):
        raise HTTPException(status_code=400, detail=INVALID_UPLOAD_FILENAME_DETAIL)

    extension = os.path.splitext(filename)[1].lower()
    accepted_extensions = {
        item.lower() for item in accepted_types if item.startswith(".")
    }
    if not extension or extension not in accepted_extensions:
        raise HTTPException(status_code=400, detail="File type not accepted.")

    return extension


def create_server_upload_filename(extension: str) -> str:
    return f"{uuid.uuid4().hex}{extension}"
