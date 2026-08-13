"""Public owner-scoped authored-vNext PPTX import/package endpoints."""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Path as ApiPath,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse

from services.authored_vnext.ooxml.errors import OoxmlPackageError
from services.authored_vnext.ooxml.service import (
    MAX_UPLOAD_BYTES,
    OoxmlPackageService,
)
from utils.get_env import get_app_data_directory_env
from utils.owner_context import get_current_owner_id

from .models import (
    EditableForkRequest,
    ImportPackageReceipt,
    PackageOperationCommand,
    PackageOperationReceipt,
    PackageReceiptReadBack,
    PackageRollbackRequest,
    PackageVersionReceipt,
)


PACKAGE_ID_PATTERN = r"^package:[a-f0-9]{32}$"
OPERATION_ID_PATTERN = r"^operation:[a-f0-9]{32}$"


def _enabled() -> bool:
    return os.getenv("AUTHORED_VNEXT_OOXML_PACKAGE_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _require_enabled() -> None:
    if not _enabled():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="authored_vnext_ooxml_package_disabled",
        )


@lru_cache(maxsize=64)
def _service_for_owner(root: str, owner_id: str) -> OoxmlPackageService:
    return OoxmlPackageService(Path(root), owner_id=owner_id)


def get_ooxml_package_service() -> OoxmlPackageService:
    owner_id = get_current_owner_id()
    if owner_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authored_vnext_owner_required",
        )
    app_data = (get_app_data_directory_env() or "").strip()
    if not app_data:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authored_vnext_ooxml_storage_unavailable",
        )
    return _service_for_owner(str(Path(app_data).resolve()), str(owner_id))


Service = Annotated[OoxmlPackageService, Depends(get_ooxml_package_service)]


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, OoxmlPackageError):
        return HTTPException(
            status_code=error.status_code,
            detail={"code": error.code},
        )
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": "authored_vnext_ooxml_operation_failed"},
    )


AUTHORED_VNEXT_PACKAGE_ROUTER = APIRouter(
    prefix="/authored-vnext/packages",
    tags=["Authored vNext OOXML Packages"],
    dependencies=[Depends(_require_enabled)],
)


@AUTHORED_VNEXT_PACKAGE_ROUTER.post(
    "/imports",
    response_model=ImportPackageReceipt,
    status_code=status.HTTP_201_CREATED,
)
async def import_pptx_package(
    service: Service,
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=200)
    ],
    expected_account_generation: Annotated[
        int, Header(alias="X-Account-Deletion-Generation", ge=0)
    ] = 0,
) -> ImportPackageReceipt:
    try:
        filename = file.filename or ""
        if not filename.lower().endswith(".pptx"):
            raise OoxmlPackageError("authored_vnext_ooxml_pptx_required")
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if await file.read(1):
            raise OoxmlPackageError("authored_vnext_ooxml_upload_size_invalid")
        return ImportPackageReceipt.model_validate(
            service.import_package(
                content,
                idempotency_key=idempotency_key,
                expected_account_generation=expected_account_generation,
            )
        )
    except Exception as error:
        raise _http_error(error) from error


# Operation routes precede /{package_id} so Starlette never treats the literal
# "operations" segment as a package identifier.
@AUTHORED_VNEXT_PACKAGE_ROUTER.get(
    "/operations/{operation_id}", response_model=PackageOperationReceipt
)
def get_package_operation(
    service: Service,
    operation_id: str = ApiPath(pattern=OPERATION_ID_PATTERN),
) -> PackageOperationReceipt:
    try:
        return PackageOperationReceipt.model_validate(
            service.get_operation(operation_id)
        )
    except Exception as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_PACKAGE_ROUTER.post(
    "/operations/{operation_id}/run", response_model=PackageOperationReceipt
)
def run_package_operation(
    service: Service,
    operation_id: str = ApiPath(pattern=OPERATION_ID_PATTERN),
) -> PackageOperationReceipt:
    try:
        return PackageOperationReceipt.model_validate(
            service.run_operation(operation_id)
        )
    except Exception as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_PACKAGE_ROUTER.post(
    "/operations/{operation_id}/resume", response_model=PackageOperationReceipt
)
def resume_package_operation(
    service: Service,
    operation_id: str = ApiPath(pattern=OPERATION_ID_PATTERN),
) -> PackageOperationReceipt:
    try:
        return PackageOperationReceipt.model_validate(
            service.resume_operation(operation_id)
        )
    except Exception as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_PACKAGE_ROUTER.get(
    "/{package_id}", response_model=PackageVersionReceipt
)
def inspect_package(
    service: Service,
    package_id: str = ApiPath(pattern=PACKAGE_ID_PATTERN),
) -> PackageVersionReceipt:
    try:
        return PackageVersionReceipt.model_validate(service.get_package(package_id))
    except Exception as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_PACKAGE_ROUTER.post(
    "/{package_id}/forks",
    response_model=ImportPackageReceipt,
    status_code=status.HTTP_201_CREATED,
)
def fork_editable_package(
    request: EditableForkRequest,
    service: Service,
    package_id: str = ApiPath(pattern=PACKAGE_ID_PATTERN),
) -> ImportPackageReceipt:
    try:
        return ImportPackageReceipt.model_validate(
            service.fork_editable(
                package_id,
                request.model_dump(mode="json", by_alias=True, exclude_none=True),
            )
        )
    except Exception as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_PACKAGE_ROUTER.post(
    "/{package_id}/operations",
    response_model=PackageOperationReceipt,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_package_operation(
    request: PackageOperationCommand,
    service: Service,
    package_id: str = ApiPath(pattern=PACKAGE_ID_PATTERN),
) -> PackageOperationReceipt:
    try:
        return PackageOperationReceipt.model_validate(
            service.create_operation(
                package_id,
                request.model_dump(mode="json", by_alias=True, exclude_none=True),
            )
        )
    except Exception as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_PACKAGE_ROUTER.post(
    "/{package_id}/archive",
    response_model=PackageOperationReceipt,
    status_code=status.HTTP_202_ACCEPTED,
)
def archive_package(
    request: PackageOperationCommand,
    service: Service,
    package_id: str = ApiPath(pattern=PACKAGE_ID_PATTERN),
) -> PackageOperationReceipt:
    try:
        payload = request.model_dump(mode="json", by_alias=True, exclude_none=True)
        if payload["operation"] != "ARCHIVE":
            raise OoxmlPackageError("authored_vnext_ooxml_archive_command_required")
        queued = service.create_operation(package_id, payload)
        return PackageOperationReceipt.model_validate(
            service.run_operation(queued["operationId"])
        )
    except Exception as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_PACKAGE_ROUTER.get(
    "/{package_id}/receipt", response_model=PackageReceiptReadBack
)
def read_back_package_receipt(
    service: Service,
    package_id: str = ApiPath(pattern=PACKAGE_ID_PATTERN),
) -> PackageReceiptReadBack:
    try:
        return PackageReceiptReadBack.model_validate(service.read_back(package_id))
    except Exception as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_PACKAGE_ROUTER.post(
    "/{package_id}/rollback", response_model=PackageVersionReceipt
)
def rollback_package(
    request: PackageRollbackRequest,
    service: Service,
    package_id: str = ApiPath(pattern=PACKAGE_ID_PATTERN),
) -> PackageVersionReceipt:
    try:
        return PackageVersionReceipt.model_validate(
            service.rollback(
                package_id,
                request.model_dump(mode="json", by_alias=True),
            )
        )
    except Exception as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_PACKAGE_ROUTER.get(
    "/{package_id}/artifact", response_class=FileResponse
)
def download_package_artifact(
    service: Service,
    package_id: str = ApiPath(pattern=PACKAGE_ID_PATTERN),
) -> FileResponse:
    try:
        path, digest = service.open_artifact(package_id)
        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            filename=f"{package_id.removeprefix('package:')}.pptx",
            headers={"ETag": f'"{digest}"'},
        )
    except Exception as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_PACKAGE_ROUTER.get(
    "/{package_id}/artifacts/{revision}", response_class=FileResponse
)
def download_package_revision(
    service: Service,
    package_id: str = ApiPath(pattern=PACKAGE_ID_PATTERN),
    revision: int = ApiPath(ge=1),
) -> FileResponse:
    try:
        path, digest = service.open_artifact(package_id, revision=revision)
        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            filename=f"{package_id.removeprefix('package:')}-r{revision}.pptx",
            headers={"ETag": f'"{digest}"'},
        )
    except Exception as error:
        raise _http_error(error) from error


__all__ = [
    "AUTHORED_VNEXT_PACKAGE_ROUTER",
    "get_ooxml_package_service",
]
