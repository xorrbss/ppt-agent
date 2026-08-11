from pathlib import Path
from typing import List
from functools import wraps
from urllib.parse import unquote, urlsplit
from fastapi import APIRouter, Depends, File, UploadFile, HTTPException, Query, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.image_prompt import ImagePrompt
from models.sql.image_asset import ImageAsset
from models.sql.workspace import WorkspaceAsset
from models.workspace import WorkspaceAssetTransitionRequest
from services.database import get_async_session
from services.image_generation_service import ImageGenerationService
from services.assets import AssetGatewayError, canonicalize_legacy_image_asset
from services.workspace_asset_service import transition_workspace_asset
from services.workspace_cleanup_journal_service import (
    WorkspaceCleanupDeleteError,
    enqueue_workspace_cleanup_targets,
    legacy_gallery_image_cleanup_target,
    workspace_asset_cleanup_target,
)
from services.workspace_service import WorkspaceServiceError
from utils.asset_directory_utils import (
    filesystem_image_path_to_app_data_url,
    get_images_directory,
    normalize_slide_asset_url,
)
from utils.get_env import (
    get_app_data_directory_env,
    get_pexels_api_key_env,
    get_pixabay_api_key_env,
)
from utils.image_provider import get_selected_image_provider
from enums.image_provider import ImageProvider
import os
import uuid
from utils.file_utils import get_file_name_with_random_uuid
from utils.upload_limits import get_image_upload_limit_bytes, stream_upload_to_file
from utils.owner_context import get_current_owner_is_admin

IMAGES_ROUTER = APIRouter(prefix="/images", tags=["Images"])

_IMAGE_TYPES = {
    ".png": ("image/png", lambda value: value.startswith(b"\x89PNG\r\n\x1a\n")),
    ".jpg": ("image/jpeg", lambda value: value.startswith(b"\xff\xd8\xff")),
    ".jpeg": ("image/jpeg", lambda value: value.startswith(b"\xff\xd8\xff")),
    ".gif": (
        "image/gif",
        lambda value: value.startswith((b"GIF87a", b"GIF89a")),
    ),
    ".webp": (
        "image/webp",
        lambda value: value.startswith(b"RIFF") and value[8:12] == b"WEBP",
    ),
}
REDACTED_SECRET_PLACEHOLDER = "__configured__"


def _stable_image_errors(code: str):
    def decorate(endpoint):
        @wraps(endpoint)
        async def wrapped(*args, **kwargs):
            try:
                return await endpoint(*args, **kwargs)
            except HTTPException:
                raise
            except Exception as error:
                raise HTTPException(status_code=500, detail=code) from error

        return wrapped

    return decorate


def _image_owner_predicate():
    from utils.owner_context import get_current_owner_id

    owner_id = get_current_owner_id()
    if owner_id is None:
        return ImageAsset.owner_id.is_(None)
    return ImageAsset.owner_id == owner_id


def _validate_uploaded_image(file: UploadFile, image_path: str) -> None:
    suffix = os.path.splitext(file.filename or "")[1].lower()
    expected = _IMAGE_TYPES.get(suffix)
    media_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    if expected is None or media_type != expected[0]:
        raise HTTPException(
            status_code=415,
            detail="Unsupported image type. Use PNG, JPEG, GIF, or WebP.",
        )
    with open(image_path, "rb") as image_stream:
        prefix = image_stream.read(12)
    if not expected[1](prefix):
        raise HTTPException(
            status_code=415,
            detail="Image content does not match its declared type.",
        )


def _normalize_stock_provider(provider: str | None) -> str:
    normalized_provider = (provider or "").strip().lower()
    if normalized_provider in {"pixels", "pixel", "pexel"}:
        normalized_provider = "pexels"

    if normalized_provider:
        if normalized_provider in {"pexels", "pixabay"}:
            return normalized_provider
        raise HTTPException(
            status_code=400,
            detail="provider must be either 'pexels' or 'pixabay'",
        )

    selected_provider = get_selected_image_provider()
    if selected_provider == ImageProvider.PIXABAY:
        return "pixabay"
    return "pexels"


def _resolve_stock_api_key(
    request_api_key: str | None, configured_api_key: str | None
) -> str:
    normalized_request_key = (request_api_key or "").strip()
    if normalized_request_key == REDACTED_SECRET_PLACEHOLDER:
        normalized_request_key = ""
    return (normalized_request_key or configured_api_key or "").strip()


def _safe_image_api_url(value: str | None) -> str | None:
    """Allow public URLs or the single confined gallery mount, never locators."""

    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or "\\" in candidate or any(ord(char) < 32 for char in candidate):
        return None
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    if scheme in {"http", "https"}:
        return candidate if parsed.netloc else None
    if scheme in {"data", "blob"}:
        return candidate
    if scheme or parsed.netloc or parsed.query or parsed.fragment:
        return None

    decoded = candidate
    for _ in range(3):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    if "\\" in decoded or not decoded.startswith("/app_data/images/"):
        return None
    suffix = decoded.removeprefix("/app_data/images/")
    if not suffix or any(part in {"", ".", ".."} for part in suffix.split("/")):
        return None
    return candidate


@IMAGES_ROUTER.get("/search", response_model=List[str])
@_stable_image_errors("stock_image_search_failed")
async def search_stock_images(
    query: str,
    limit: int = Query(default=12, ge=1, le=30),
    provider: str | None = Query(default=None),
    strict_api_key: bool = Query(default=False),
    x_provider_api_key: str | None = Header(default=None, alias="X-Provider-Api-Key"),
):
    normalized_provider = _normalize_stock_provider(provider)

    image_generation_service = ImageGenerationService(get_images_directory())

    if normalized_provider == "pexels":
        api_key = _resolve_stock_api_key(x_provider_api_key, get_pexels_api_key_env())
        if strict_api_key and not api_key:
            raise HTTPException(status_code=401, detail="Pexels API key is required")

        # Pexels can return cached public responses for common queries.
        # Use a nonce query in strict mode to force a real auth check.
        if strict_api_key:
            validation_query = f"__presenton_auth_check_{uuid.uuid4().hex}"
            await image_generation_service.get_image_from_pexels(
                validation_query,
                api_key=api_key,
                limit=1,
            )

        images = await image_generation_service.get_image_from_pexels(
            query,
            api_key=api_key,
            limit=limit,
        )
        if isinstance(images, str):
            return [images] if images else []
        return images

    api_key = _resolve_stock_api_key(x_provider_api_key, get_pixabay_api_key_env())
    if strict_api_key and not api_key:
        raise HTTPException(status_code=401, detail="Pixabay API key is required")

    images = await image_generation_service.get_image_from_pixabay(
        query,
        api_key=api_key,
        limit=limit,
    )
    if isinstance(images, str):
        return [images] if images else []
    return images


@IMAGES_ROUTER.get("/generate")
@_stable_image_errors("image_generation_failed")
async def generate_image(
    prompt: str, sql_session: AsyncSession = Depends(get_async_session)
):
    images_directory = get_images_directory()
    image_prompt = ImagePrompt(prompt=prompt)
    image_generation_service = ImageGenerationService(images_directory)

    image = await image_generation_service.generate_image(image_prompt)
    if not isinstance(image, ImageAsset):
        return normalize_slide_asset_url(image) if isinstance(image, str) else image

    sql_session.add(image)
    await sql_session.commit()

    try:
        await canonicalize_legacy_image_asset(
            sql_session,
            image=image.id,
            image_root=get_images_directory(),
        )
    except AssetGatewayError as error:
        raise HTTPException(status_code=error.status_code, detail=error.code) from error

    return filesystem_image_path_to_app_data_url(image.path)


def _image_asset_api_dict(asset: ImageAsset) -> dict:
    # Compatibility removal condition: once every pre-AssetGateway image row
    # has a canonical asset id, this facade can return that id directly. Never
    # expose the server filesystem locator during the transition.
    file_url = _safe_image_api_url(
        filesystem_image_path_to_app_data_url(asset.path)
    )
    return {
        "id": asset.id,
        "asset_id": asset.canonical_asset_id,
        "created_at": asset.created_at,
        "is_uploaded": asset.is_uploaded,
        "extras": asset.extras,
        "file_url": file_url,
    }


@IMAGES_ROUTER.get("/generated")
@_stable_image_errors("image_gallery_read_failed")
async def get_generated_images(sql_session: AsyncSession = Depends(get_async_session)):
    try:
        images_result = await sql_session.scalars(
            select(ImageAsset)
            .where(
                ImageAsset.is_uploaded.is_(False),
                _image_owner_predicate(),
            )
            .order_by(ImageAsset.created_at.desc())
        )
        return [_image_asset_api_dict(a) for a in images_result]
    except Exception as error:
        raise HTTPException(status_code=500, detail="image_gallery_read_failed") from error


@IMAGES_ROUTER.post("/upload")
@_stable_image_errors("image_upload_failed")
async def upload_image(
    file: UploadFile = File(...), sql_session: AsyncSession = Depends(get_async_session)
):
    image_path = ""
    try:
        new_filename = get_file_name_with_random_uuid(file)
        image_path = os.path.join(
            get_images_directory(), os.path.basename(new_filename)
        )

        await stream_upload_to_file(
            file,
            image_path,
            limit_bytes=get_image_upload_limit_bytes(),
            label="Image",
        )
        _validate_uploaded_image(file, image_path)

        image_asset = ImageAsset(path=image_path, is_uploaded=True)

        sql_session.add(image_asset)
        await sql_session.flush()
        await canonicalize_legacy_image_asset(
            sql_session,
            image=image_asset.id,
            image_root=get_images_directory(),
        )
        await sql_session.refresh(image_asset)

        return _image_asset_api_dict(image_asset)
    except AssetGatewayError as error:
        if image_path:
            try:
                os.remove(image_path)
            except OSError:
                pass
        raise HTTPException(status_code=error.status_code, detail=error.code) from error
    except HTTPException:
        if image_path:
            try:
                os.remove(image_path)
            except OSError:
                pass
        raise
    except Exception as e:
        if image_path:
            try:
                os.remove(image_path)
            except OSError:
                pass
        raise HTTPException(status_code=500, detail="image_upload_failed") from e


@IMAGES_ROUTER.get("/uploaded")
@_stable_image_errors("image_gallery_read_failed")
async def get_uploaded_images(sql_session: AsyncSession = Depends(get_async_session)):
    try:
        images_result = await sql_session.scalars(
            select(ImageAsset)
            .where(
                ImageAsset.is_uploaded.is_(True),
                _image_owner_predicate(),
            )
            .order_by(ImageAsset.created_at.desc())
        )
        return [_image_asset_api_dict(a) for a in images_result]
    except Exception as error:
        raise HTTPException(status_code=500, detail="image_gallery_read_failed") from error


def _legacy_gallery_cleanup_target(
    image: ImageAsset,
    *,
    canonical: WorkspaceAsset,
):
    roots = [Path(get_images_directory())]
    if get_current_owner_is_admin():
        shared_root = Path(get_app_data_directory_env()) / "images"
        if shared_root not in roots:
            roots.append(shared_root)
    last_error: WorkspaceCleanupDeleteError | None = None
    for root in roots:
        try:
            return legacy_gallery_image_cleanup_target(
                image,
                image_root=root,
                canonical_sha256=canonical.sha256 or "",
                canonical_size_bytes=canonical.size_bytes,
            )
        except WorkspaceCleanupDeleteError as error:
            last_error = error
    assert last_error is not None
    raise last_error


@IMAGES_ROUTER.delete("/{id}", status_code=204)
@_stable_image_errors("image_delete_failed")
async def delete_uploaded_image_by_id(
    id: uuid.UUID, sql_session: AsyncSession = Depends(get_async_session)
):
    try:
        # Fetch the asset to get its actual file path
        image = (
            await sql_session.execute(
                select(ImageAsset).where(
                    ImageAsset.id == id,
                    _image_owner_predicate(),
                )
            )
        ).scalar_one_or_none()
        if image is None:
            raise HTTPException(status_code=404, detail="image_not_found")
        if image.canonical_asset_id is None:
            raise HTTPException(status_code=409, detail="image_canonical_asset_missing")
        canonical = (
            await sql_session.execute(
                select(WorkspaceAsset).where(
                    WorkspaceAsset.id == image.canonical_asset_id,
                    WorkspaceAsset.owner_id == image.owner_id,
                )
            )
        ).scalar_one_or_none()
        if (
            canonical is None
            or canonical.state == "deleted"
            or not canonical.sha256
        ):
            raise HTTPException(status_code=409, detail="image_canonical_asset_unready")
        await enqueue_workspace_cleanup_targets(
            sql_session,
            targets=[
                workspace_asset_cleanup_target(canonical),
                _legacy_gallery_cleanup_target(image, canonical=canonical),
            ],
        )
        await transition_workspace_asset(
            sql_session,
            asset_id=canonical.id,
            request=WorkspaceAssetTransitionRequest(
                state="deleted",
                expected_version=canonical.version,
                failure_code="image_deleted",
            ),
            commit=False,
        )
        await sql_session.delete(image)
        await sql_session.commit()

    except HTTPException:
        raise
    except WorkspaceCleanupDeleteError as error:
        await sql_session.rollback()
        raise HTTPException(status_code=409, detail=error.code) from error
    except WorkspaceServiceError as error:
        await sql_session.rollback()
        raise HTTPException(status_code=error.status_code, detail=error.code) from error
    except Exception as error:
        await sql_session.rollback()
        raise HTTPException(status_code=500, detail="image_delete_failed") from error
