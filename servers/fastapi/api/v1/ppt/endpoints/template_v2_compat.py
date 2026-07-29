"""Non-conflicting upstream Template V2 core CRUD compatibility routes.

The legacy router already owns GET ``/template/all`` and GET
``/template/{id}``, with incompatible response models. This adapter therefore
mounts only the upstream PATCH and DELETE contracts whose method/path pairs do
not collide.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Response, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.template_v2 import TemplateV2
from services.database import get_async_session
from services.template_v2_service import (
    TemplateV2NotFoundError,
    TemplateV2PersistenceConflictError,
    TemplateV2RevisionConflictError,
    TemplateV2Service,
    TemplateV2ServiceError,
)
from templates.v2.models.layouts import MergedComponents, SlideLayouts
from templates.v2.policy import (
    StructuredTemplatePolicyError,
    get_structured_template_policy,
)


TEMPLATE_V2_COMPAT_ROUTER = APIRouter(prefix="/template", tags=["Templates"])
IconType = Literal["bold", "duotone", "fill", "light", "regular", "thin"]


class UpdateTemplateMetadataRequest(BaseModel):
    """Request surface retained from the upstream Template V2 PATCH contract."""

    id: str | None = None
    name: str | None = None
    description: str | None = None
    layout_count: int | None = Field(default=None, ge=0)
    thumbnail: str | None = None
    is_default: bool | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    merged_components: dict[str, Any] | None = None
    layouts: dict[str, Any] | None = None
    fonts: dict[str, str] | None = None
    icon_type: IconType | None = None


class TemplateListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None = None
    layout_count: int = 0
    thumbnail: str | None = None
    is_default: bool = False
    created_at: datetime
    updated_at: datetime


class TemplateResponse(TemplateListItem):
    merged_components: dict[str, Any] | None = None
    layouts: dict[str, Any] | None = None
    fonts: dict[str, str] = Field(default_factory=dict)


def _count_layouts(layouts_json: Any) -> int:
    if isinstance(layouts_json, dict):
        layouts = layouts_json.get("layouts")
        return len(layouts) if isinstance(layouts, list) else 0
    if isinstance(layouts_json, list):
        return len(layouts_json)
    return 0


def _coerce_font_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        name.strip(): url.strip()
        for name, url in value.items()
        if isinstance(name, str)
        and isinstance(url, str)
        and name.strip()
        and url.strip()
    }


def _template_fonts(template: TemplateV2) -> dict[str, str]:
    if not isinstance(template.assets, dict):
        return {}
    return _coerce_font_map(template.assets.get("fonts"))


def _template_thumbnail(template: TemplateV2) -> str | None:
    if not isinstance(template.assets, dict):
        return None
    thumbnail = template.assets.get("thumbnail")
    if isinstance(thumbnail, str) and thumbnail.strip():
        return thumbnail.strip()
    slide_image_urls = template.assets.get("slide_image_urls")
    if isinstance(slide_image_urls, list):
        for url in slide_image_urls:
            if isinstance(url, str) and url.strip():
                return url.strip()
    return None


def _response(template: TemplateV2) -> TemplateResponse:
    return TemplateResponse(
        id=template.id,
        name=template.name,
        description=template.description,
        layout_count=_count_layouts(template.layouts),
        thumbnail=_template_thumbnail(template),
        is_default=template.is_default,
        created_at=template.created_at,
        updated_at=template.updated_at,
        merged_components=deepcopy(template.merged_components),
        layouts=deepcopy(template.layouts),
        fonts=_template_fonts(template),
    )


def _require_write(template_id: str) -> None:
    try:
        get_structured_template_policy().require_write_enabled(template_id)
    except StructuredTemplatePolicyError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error.code,
        ) from error


def _service_http_error(error: TemplateV2ServiceError) -> HTTPException:
    if isinstance(error, TemplateV2NotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found",
        )
    if isinstance(
        error,
        (TemplateV2PersistenceConflictError, TemplateV2RevisionConflictError),
    ):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Template persistence conflict",
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Template service failure",
    )


def _validated_layouts(value: dict[str, Any]) -> dict[str, Any]:
    return SlideLayouts.model_validate(value).model_dump(
        mode="json",
        exclude_none=True,
    )


@TEMPLATE_V2_COMPAT_ROUTER.patch(
    "/{template_id}",
    response_model=TemplateResponse,
)
async def update_template_metadata(
    template_id: str = Path(...),
    request: UpdateTemplateMetadataRequest = Body(...),
    sql_session: AsyncSession = Depends(get_async_session),
):
    if request.id is not None and request.id != template_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Template ID in path does not match request body ID",
        )
    _require_write(template_id)

    service = TemplateV2Service(sql_session)
    try:
        template = await service.get(template_id)
    except TemplateV2ServiceError as error:
        raise _service_http_error(error) from error

    changes: dict[str, Any] = {}
    if "name" in request.model_fields_set:
        name = (request.name or "").strip()
        if not name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Template name is required",
            )
        changes["name"] = name

    if "description" in request.model_fields_set:
        description = (request.description or "").strip()
        changes["description"] = description or None

    if "merged_components" in request.model_fields_set:
        if request.merged_components is None:
            changes["merged_components"] = None
        else:
            try:
                changes["merged_components"] = MergedComponents.model_validate(
                    request.merged_components
                ).model_dump(mode="json", exclude_none=True)
            except ValidationError as error:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Template merged components are invalid",
                ) from error

    assets = (
        deepcopy(template.assets)
        if isinstance(template.assets, dict)
        else {}
    )
    assets_changed = False
    if "layouts" in request.model_fields_set:
        if request.layouts is None:
            changes["layouts"] = None
            assets["layout_indexes"] = []
        else:
            try:
                layouts = _validated_layouts(request.layouts)
            except ValidationError as error:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Template layouts are invalid",
                ) from error
            changes["layouts"] = layouts
            assets["layout_indexes"] = list(
                range(_count_layouts(layouts))
            )
        assets_changed = True

    if "thumbnail" in request.model_fields_set:
        thumbnail = (request.thumbnail or "").strip()
        if thumbnail:
            assets["thumbnail"] = thumbnail
        else:
            assets.pop("thumbnail", None)
        assets_changed = True

    if "fonts" in request.model_fields_set:
        assets["fonts"] = _coerce_font_map(request.fonts)
        assets_changed = True

    if "icon_type" in request.model_fields_set:
        icon_type = request.icon_type or "bold"
        assets["icon_type"] = icon_type
        assets["icon_weight"] = icon_type
        assets_changed = True

    if assets_changed:
        changes["assets"] = assets

    try:
        updated = await service.update(
            template_id,
            changes=changes,
            expected_revision=template.revision,
        )
    except TemplateV2ServiceError as error:
        raise _service_http_error(error) from error
    return _response(updated)


@TEMPLATE_V2_COMPAT_ROUTER.delete(
    "/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_template(
    template_id: str = Path(...),
    sql_session: AsyncSession = Depends(get_async_session),
):
    _require_write(template_id)
    try:
        await TemplateV2Service(sql_session).delete(template_id)
    except TemplateV2ServiceError as error:
        raise _service_http_error(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
