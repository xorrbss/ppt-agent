import hashlib
import hmac
import re
import secrets
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from api.v1.auth.assets import normalized_app_data_parts
from api.v1.ppt.endpoints.presentation_helpers import resolve_presentation_fonts
from models.presentation_with_slides import PublicPresentationWithSlides
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_PUBLISHED,
    PresentationModel,
)
from models.sql.slide import SlideModel
from services.database import get_async_session
from services.workspace_legacy_write_gate import (
    require_legacy_presentation_write_path,
)
from utils.get_env import get_app_data_directory_env

PRESENTATION_SHARE_ROUTER = APIRouter(
    prefix="/presentation", tags=["Presentation Sharing"]
)

# Minimum plausible length for a real share token (secrets.token_urlsafe(32) ~ 43
# chars); short/blank values are rejected up front rather than hitting the DB.
_MIN_TOKEN_LEN = 16
_APP_DATA_IMAGE_RE = re.compile(
    r"(?:(?:https?://[^/\s\"'<>()[\]]+))?"
    r"/app_data/images/(?P<asset>[^\s\"'<>()[\]?#]+)",
    flags=re.IGNORECASE,
)
_PUBLIC_IMAGE_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}


class ShareInfo(BaseModel):
    shared: bool
    share_token: Optional[str] = None


def _validate_share_token(share_token: str) -> None:
    if not share_token or len(share_token) < _MIN_TOKEN_LEN:
        raise HTTPException(status_code=404, detail="Shared presentation not found")


async def _shared_presentation(
    share_token: str,
    sql_session: AsyncSession,
) -> PresentationModel:
    _validate_share_token(share_token)
    result = await sql_session.scalars(
        select(PresentationModel).where(
            PresentationModel.share_token == share_token,
            PresentationModel.lifecycle_status
            == PRESENTATION_LIFECYCLE_PUBLISHED,
        )
    )
    presentation = result.first()
    if presentation is None or not presentation.share_token:
        raise HTTPException(status_code=404, detail="Shared presentation not found")
    return presentation


async def _presentation_slides(
    presentation: PresentationModel,
    sql_session: AsyncSession,
) -> list[SlideModel]:
    return list(
        await sql_session.scalars(
            select(SlideModel)
            .where(SlideModel.presentation == presentation.id)
            .order_by(SlideModel.index)
        )
    )


def _public_asset_id(share_token: str, asset_parts: tuple[str, ...]) -> str:
    return hmac.new(
        share_token.encode("utf-8"),
        "/".join(asset_parts).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _public_asset_url(share_token: str, asset_parts: tuple[str, ...]) -> str:
    return (
        f"/api/v1/ppt/presentation/public/{quote(share_token, safe='')}"
        f"/assets/{_public_asset_id(share_token, asset_parts)}"
    )


def _relative_image_reference_parts(value: str) -> tuple[str, ...] | None:
    candidate = value.strip()
    if (
        not candidate
        or candidate.startswith(("/", "data:", "blob:", "http://", "https://"))
        or any(char.isspace() for char in candidate)
    ):
        return None
    parts = normalized_app_data_parts(f"/app_data/images/{candidate}")
    if (
        not parts
        or parts[0] != "images"
        or Path(parts[-1]).suffix.lower() not in _PUBLIC_IMAGE_EXTENSIONS
    ):
        return None
    return parts


def _rewrite_public_image_references(value, share_token: str):
    if isinstance(value, dict):
        return {
            key: _rewrite_public_image_references(child, share_token)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [
            _rewrite_public_image_references(child, share_token) for child in value
        ]

    if isinstance(value, str):
        relative_parts = _relative_image_reference_parts(value)
        if relative_parts is not None:
            return _public_asset_url(share_token, relative_parts)

        def replace(match: re.Match) -> str:
            parts = normalized_app_data_parts(
                f"/app_data/images/{match.group('asset')}"
            )
            if not parts or parts[0] != "images":
                return ""
            return _public_asset_url(share_token, parts)

        return _APP_DATA_IMAGE_RE.sub(replace, value)
    return value


def _referenced_image_paths(value) -> set[tuple[str, ...]]:
    references: set[tuple[str, ...]] = set()
    if isinstance(value, dict):
        for child in value.values():
            references.update(_referenced_image_paths(child))
    elif isinstance(value, list):
        for child in value:
            references.update(_referenced_image_paths(child))
    elif isinstance(value, str):
        relative_parts = _relative_image_reference_parts(value)
        if relative_parts is not None:
            references.add(relative_parts)
        for match in _APP_DATA_IMAGE_RE.finditer(value):
            parts = normalized_app_data_parts(
                f"/app_data/images/{match.group('asset')}"
            )
            if parts and parts[0] == "images":
                references.add(parts)
    return references


def _image_path_belongs_to_presentation(
    parts: tuple[str, ...],
    presentation: PresentationModel,
) -> bool:
    if (
        len(parts) < 2
        or Path(parts[-1]).suffix.lower() not in _PUBLIC_IMAGE_EXTENSIONS
    ):
        return False
    relative = parts[1:]
    owner_id = presentation.owner_id
    if relative[0] == "users":
        return (
            owner_id is not None
            and len(relative) >= 3
            and relative[1] == str(owner_id)
        )
    if owner_id is None:
        # Legacy rows without an owner belong to the migrated primary admin.
        return True
    presentation_id = str(presentation.id)
    return relative[0] == presentation_id or (
        len(relative) >= 2
        and relative[0] == "authored"
        and relative[1] == presentation_id
    )


async def _public_presentation_projection(
    presentation: PresentationModel,
    sql_session: AsyncSession,
) -> PublicPresentationWithSlides:
    slides = await _presentation_slides(presentation, sql_session)
    fonts = await resolve_presentation_fonts(presentation, slides, sql_session)
    return PublicPresentationWithSlides(
        **presentation.model_dump(),
        slides=[slide.model_dump() for slide in slides],
        fonts=fonts,
    )


@PRESENTATION_SHARE_ROUTER.get("/{id}/share", response_model=ShareInfo)
async def get_share_status(
    id: uuid.UUID, sql_session: AsyncSession = Depends(get_async_session)
):
    presentation = await sql_session.get(PresentationModel, id)
    if (
        not presentation
        or presentation.lifecycle_status != PRESENTATION_LIFECYCLE_PUBLISHED
    ):
        raise HTTPException(status_code=404, detail="Presentation not found")
    return ShareInfo(
        shared=bool(presentation.share_token), share_token=presentation.share_token
    )


@PRESENTATION_SHARE_ROUTER.post("/{id}/share", response_model=ShareInfo)
async def enable_share(
    id: uuid.UUID,
    regenerate: bool = False,
    sql_session: AsyncSession = Depends(get_async_session),
):
    """Enable read-only sharing (idempotent). `regenerate=true` rotates the token,
    which immediately voids the previous link."""
    require_legacy_presentation_write_path(id)
    presentation = await sql_session.get(PresentationModel, id)
    if (
        not presentation
        or presentation.lifecycle_status != PRESENTATION_LIFECYCLE_PUBLISHED
    ):
        raise HTTPException(status_code=404, detail="Presentation not found")

    if regenerate or not presentation.share_token:
        presentation.share_token = secrets.token_urlsafe(32)
        sql_session.add(presentation)
        await sql_session.commit()
    return ShareInfo(shared=True, share_token=presentation.share_token)


@PRESENTATION_SHARE_ROUTER.delete("/{id}/share", response_model=ShareInfo)
async def disable_share(
    id: uuid.UUID, sql_session: AsyncSession = Depends(get_async_session)
):
    require_legacy_presentation_write_path(id)
    presentation = await sql_session.get(PresentationModel, id)
    if (
        not presentation
        or presentation.lifecycle_status != PRESENTATION_LIFECYCLE_PUBLISHED
    ):
        raise HTTPException(status_code=404, detail="Presentation not found")

    if presentation.share_token is not None:
        presentation.share_token = None
        sql_session.add(presentation)
        await sql_session.commit()
    return ShareInfo(shared=False, share_token=None)


@PRESENTATION_SHARE_ROUTER.get(
    "/public/{share_token}", response_model=PublicPresentationWithSlides
)
async def get_shared_presentation(
    share_token: str, sql_session: AsyncSession = Depends(get_async_session)
):
    """Public, read-only view of a shared deck. Reached ONLY by an unguessable
    token (never by presentation id), and exempt from the admin session in
    SessionAuthMiddleware. Returns 404 for any token that is blank, too short, or
    not currently shared — so nothing about other decks is observable."""
    presentation = await _shared_presentation(share_token, sql_session)
    projection = await _public_presentation_projection(presentation, sql_session)
    rewritten = _rewrite_public_image_references(
        projection.model_dump(mode="json"),
        share_token,
    )
    return PublicPresentationWithSlides.model_validate(rewritten)


@PRESENTATION_SHARE_ROUTER.get("/public/{share_token}/assets/{asset_id}")
async def get_shared_presentation_asset(
    share_token: str,
    asset_id: str,
    sql_session: AsyncSession = Depends(get_async_session),
):
    """Serve only an image actually referenced by this live public projection."""

    if re.fullmatch(r"[0-9a-f]{64}", asset_id) is None:
        raise HTTPException(status_code=404, detail="Shared asset not found")
    presentation = await _shared_presentation(share_token, sql_session)
    projection = await _public_presentation_projection(presentation, sql_session)
    allowed_paths = {
        path
        for path in _referenced_image_paths(projection.model_dump(mode="json"))
        if _image_path_belongs_to_presentation(path, presentation)
    }
    requested_parts = next(
        (
            path
            for path in allowed_paths
            if hmac.compare_digest(_public_asset_id(share_token, path), asset_id)
        ),
        None,
    )
    if requested_parts is None:
        raise HTTPException(status_code=404, detail="Shared asset not found")

    app_data_value = (get_app_data_directory_env() or "").strip()
    if not app_data_value:
        raise HTTPException(status_code=404, detail="Shared asset not found")
    app_data_root = Path(app_data_value).resolve()
    asset_file = app_data_root.joinpath(*requested_parts).resolve()
    if not asset_file.is_relative_to(app_data_root) or not asset_file.is_file():
        raise HTTPException(status_code=404, detail="Shared asset not found")
    return FileResponse(
        asset_file,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )
