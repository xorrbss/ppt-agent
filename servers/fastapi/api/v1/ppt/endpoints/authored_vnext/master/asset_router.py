"""Owner-scoped master logo asset upload boundary."""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Path as ApiPath,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, Field

from services.authored_vnext.master.assets import (
    MAX_MASTER_ASSET_BYTES,
    MasterAssetError,
    OwnerMasterAssetStore,
)
from utils.owner_context import get_current_owner_id


class MasterAssetReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    asset_id: str = Field(alias="assetId")
    digest: str
    media_type: str = Field(alias="mediaType")
    byte_length: int = Field(alias="byteLength")
    width: int
    height: int


@lru_cache(maxsize=64)
def _store_for_owner(root: str, owner_id: str) -> OwnerMasterAssetStore:
    return OwnerMasterAssetStore(
        Path(root) / "authored_vnext" / "master-assets" / "users" / owner_id
    )


def get_master_asset_store() -> OwnerMasterAssetStore:
    owner_id = get_current_owner_id()
    if owner_id is None:
        raise HTTPException(status_code=401, detail="authored_vnext_owner_required")
    root = str(Path(os.getenv("APP_DATA_DIRECTORY", "app_data")).resolve())
    return _store_for_owner(root, str(owner_id))


AssetStore = Annotated[OwnerMasterAssetStore, Depends(get_master_asset_store)]
AUTHORED_VNEXT_MASTER_ASSET_ROUTER = APIRouter()


@AUTHORED_VNEXT_MASTER_ASSET_ROUTER.post(
    "/assets/logo",
    response_model=MasterAssetReceipt,
    status_code=status.HTTP_201_CREATED,
)
async def upload_master_logo(
    store: AssetStore,
    file: Annotated[UploadFile, File()],
) -> MasterAssetReceipt:
    try:
        if not (file.filename or "").lower().endswith(".png"):
            raise MasterAssetError("authored_vnext_master_asset_png_required")
        value = await file.read(MAX_MASTER_ASSET_BYTES + 1)
        if len(value) > MAX_MASTER_ASSET_BYTES or await file.read(1):
            raise MasterAssetError("authored_vnext_master_asset_size_invalid")
        return MasterAssetReceipt.model_validate(store.publish_png(value))
    except MasterAssetError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": error.code},
        ) from error


@AUTHORED_VNEXT_MASTER_ASSET_ROUTER.get(
    "/assets/{asset_id}",
    response_class=Response,
    responses={
        200: {"content": {"image/png": {}}},
        404: {"description": "Asset does not exist in the current owner's store."},
        422: {"description": "Asset reference is malformed or inconsistent."},
    },
)
def get_master_asset(
    store: AssetStore,
    asset_id: Annotated[str, ApiPath(min_length=7, max_length=128)],
    digest: Annotated[str, Query(min_length=71, max_length=71)],
) -> Response:
    """Return one immutable, digest-pinned PNG from the current owner's store."""

    try:
        value = store.open_verified(asset_id, digest)
    except MasterAssetError as error:
        status_code = (
            status.HTTP_404_NOT_FOUND
            if error.code == "authored_vnext_master_asset_not_found"
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(
            status_code=status_code,
            detail={"code": error.code},
        ) from error
    return Response(
        content=value,
        media_type="image/png",
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "ETag": f'"{digest}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


__all__ = [
    "AUTHORED_VNEXT_MASTER_ASSET_ROUTER",
    "get_master_asset_store",
]
