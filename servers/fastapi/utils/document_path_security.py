import os
import uuid
from pathlib import PurePosixPath, PureWindowsPath
from typing import Iterable, List, Optional, Sequence

from fastapi import HTTPException

from utils.get_env import get_app_data_directory_env
from utils.owner_context import get_current_owner_id, get_current_owner_is_admin


INVALID_DOCUMENT_PATH_DETAIL = "Invalid document file path."
INVALID_UPLOAD_FILENAME_DETAIL = "Invalid upload filename."
_PRIVATE_APP_DATA_ROOTS = (
    "images",
    "exports",
    "uploads",
    "pptx-to-html",
    "pptx-to-json",
    "workspace-artifacts",
)


def _allowed_document_roots() -> List[str]:
    # Imported lazily to avoid a module-import cycle:
    # TempFileService itself imports environment helpers.
    from services.temp_file_service import TEMP_FILE_SERVICE

    owner_id = get_current_owner_id()
    temp_root = TEMP_FILE_SERVICE.base_dir
    roots = [
        os.path.join(temp_root, str(owner_id))
        if owner_id is not None
        else temp_root
    ]
    app_data = get_app_data_directory_env()
    if app_data:
        if owner_id is None:
            roots.append(app_data)
        else:
            roots.extend(
                os.path.join(app_data, root_name, "users", str(owner_id))
                for root_name in _PRIVATE_APP_DATA_ROOTS
            )
            # Canonical conversation attachments use the asset gateway's
            # owner-first layout rather than the legacy ``users/<owner>``
            # layout used by the private artifact stores above.
            roots.append(os.path.join(app_data, "workspace_assets", str(owner_id)))
    return [os.path.realpath(root) for root in roots]


def _validated_admin_legacy_roots(
    roots: Optional[Sequence[str]],
) -> List[str]:
    """Accept an explicitly audited global migration root for an admin only."""

    if not roots:
        return []
    if not get_current_owner_is_admin() or get_current_owner_id() is None:
        raise HTTPException(status_code=403, detail="admin_migration_required")
    try:
        return [os.path.realpath(root) for root in roots if root]
    except (OSError, ValueError, TypeError):
        raise HTTPException(
            status_code=400, detail=INVALID_DOCUMENT_PATH_DETAIL
        ) from None


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
    admin_legacy_roots: Optional[Sequence[str]] = None,
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
        roots.extend(_validated_admin_legacy_roots(admin_legacy_roots))
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
    admin_legacy_roots: Optional[Sequence[str]] = None,
) -> List[str]:
    validated: List[str] = []
    seen = set()

    for path in paths:
        real_path = validate_document_path(
            path,
            allowed_roots=allowed_roots,
            admin_legacy_roots=admin_legacy_roots,
        )
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
