import hashlib
import hmac
import re
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import func, or_, update
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
from utils.datetime_utils import get_current_utc_datetime
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
    expires_at: Optional[datetime] = None
    view_count: int = 0
    expired: bool = False


class ShareSettings(BaseModel):
    expires_at: Optional[datetime] = None

    @field_validator("expires_at")
    @classmethod
    def require_timezone_and_normalize_utc(
        cls, value: Optional[datetime]
    ) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must include a timezone")
        return value.astimezone(timezone.utc)


def _validate_share_token(share_token: str) -> None:
    if not share_token or len(share_token) < _MIN_TOKEN_LEN:
        raise HTTPException(status_code=404, detail="Shared presentation not found")


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Return an aware UTC timestamp, including values loaded from SQLite."""

    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _share_is_expired(
    presentation: PresentationModel,
    now: Optional[datetime] = None,
) -> bool:
    expires_at = _as_utc(presentation.expires_at)
    return expires_at is not None and expires_at <= (now or get_current_utc_datetime())


def _share_info(presentation: PresentationModel, *, shared: Optional[bool] = None) -> ShareInfo:
    expired = bool(presentation.share_token) and _share_is_expired(presentation)
    return ShareInfo(
        shared=(bool(presentation.share_token) and not expired)
        if shared is None
        else shared,
        share_token=presentation.share_token,
        expires_at=_as_utc(presentation.expires_at),
        view_count=int(presentation.view_count or 0),
        expired=expired,
    )


def _active_share_predicates(share_token: str, now: datetime):
    return (
        PresentationModel.share_token == share_token,
        PresentationModel.lifecycle_status == PRESENTATION_LIFECYCLE_PUBLISHED,
        or_(
            PresentationModel.expires_at.is_(None),
            PresentationModel.expires_at > now,
        ),
    )


async def _shared_presentation(
    share_token: str,
    sql_session: AsyncSession,
    *,
    not_found_detail: str = "Shared presentation not found",
) -> PresentationModel:
    try:
        _validate_share_token(share_token)
    except HTTPException as error:
        raise HTTPException(status_code=404, detail=not_found_detail) from error
    now = get_current_utc_datetime()
    result = await sql_session.scalars(
        select(PresentationModel).where(*_active_share_predicates(share_token, now))
    )
    presentation = result.first()
    if presentation is None or not presentation.share_token:
        raise HTTPException(status_code=404, detail=not_found_detail)
    return presentation


async def _increment_shared_presentation_view(
    share_token: str,
    sql_session: AsyncSession,
) -> PresentationModel:
    """Validate the live token and increment exactly once in one SQL write."""

    _validate_share_token(share_token)
    now = get_current_utc_datetime()
    result = await sql_session.execute(
        update(PresentationModel)
        .where(*_active_share_predicates(share_token, now))
        .values(
            view_count=func.coalesce(PresentationModel.view_count, 0) + 1,
            # Sharing metrics are not deck edits and must not reorder the
            # dashboard through PresentationModel.updated_at's onupdate hook.
            updated_at=PresentationModel.updated_at,
        )
        .returning(PresentationModel)
    )
    presentation = result.scalar_one_or_none()
    if presentation is None:
        await sql_session.rollback()
        raise HTTPException(status_code=404, detail="Shared presentation not found")
    await sql_session.commit()
    return presentation


def _owner_predicate(presentation: PresentationModel):
    if presentation.owner_id is None:
        return PresentationModel.owner_id.is_(None)
    return PresentationModel.owner_id == presentation.owner_id


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
    return _share_info(presentation)


@PRESENTATION_SHARE_ROUTER.post("/{id}/share", response_model=ShareInfo)
async def enable_share(
    id: uuid.UUID,
    regenerate: bool = False,
    settings: Annotated[Optional[ShareSettings], Body()] = None,
    sql_session: AsyncSession = Depends(get_async_session),
):
    """Enable read-only sharing (idempotent). `regenerate=true` rotates the token,
    which immediately voids the previous link."""
    # Sharing flips a token on the deck row; it never touches slides or the
    # revision the operation runner fences, and there is no workspace command
    # for it. Routing it through the runner would only delete the feature.
    presentation = await sql_session.get(PresentationModel, id)
    if (
        not presentation
        or presentation.lifecycle_status != PRESENTATION_LIFECYCLE_PUBLISHED
    ):
        raise HTTPException(status_code=404, detail="Presentation not found")

    now = get_current_utc_datetime()
    if settings is not None and settings.expires_at is not None:
        if settings.expires_at <= now:
            raise HTTPException(
                status_code=422,
                detail="share_expiry_must_be_in_future",
            )

    expired = bool(presentation.share_token) and _share_is_expired(presentation, now)
    rotate = regenerate or not presentation.share_token or expired
    values = {}
    if rotate:
        values.update(
            share_token=secrets.token_urlsafe(32),
            view_count=0,
        )
        # An expired link is never silently reactivated.  With no explicit
        # settings, its replacement follows the legacy permanent-link default.
        if expired and settings is None:
            values["expires_at"] = None
    if settings is not None:
        values["expires_at"] = settings.expires_at

    if values:
        values["updated_at"] = PresentationModel.updated_at
        await sql_session.execute(
            update(PresentationModel)
            .where(
                PresentationModel.id == id,
                _owner_predicate(presentation),
                PresentationModel.lifecycle_status
                == PRESENTATION_LIFECYCLE_PUBLISHED,
            )
            .values(**values)
        )
        await sql_session.commit()
        await sql_session.refresh(presentation)
    return _share_info(presentation)


@PRESENTATION_SHARE_ROUTER.delete("/{id}/share", response_model=ShareInfo)
async def disable_share(
    id: uuid.UUID, sql_session: AsyncSession = Depends(get_async_session)
):
    presentation = await sql_session.get(PresentationModel, id)
    if (
        not presentation
        or presentation.lifecycle_status != PRESENTATION_LIFECYCLE_PUBLISHED
    ):
        raise HTTPException(status_code=404, detail="Presentation not found")

    if presentation.share_token is not None or presentation.expires_at is not None:
        await sql_session.execute(
            update(PresentationModel)
            .where(
                PresentationModel.id == id,
                _owner_predicate(presentation),
                PresentationModel.lifecycle_status
                == PRESENTATION_LIFECYCLE_PUBLISHED,
            )
            .values(
                share_token=None,
                expires_at=None,
                view_count=0,
                updated_at=PresentationModel.updated_at,
            )
        )
        await sql_session.commit()
        await sql_session.refresh(presentation)
    return _share_info(presentation, shared=False)


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
    presentation = await _increment_shared_presentation_view(
        share_token, sql_session
    )
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
    presentation = await _shared_presentation(
        share_token,
        sql_session,
        not_found_detail="Shared asset not found",
    )
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
