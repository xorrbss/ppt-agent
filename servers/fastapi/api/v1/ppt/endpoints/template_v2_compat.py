"""Non-conflicting upstream Template V2 core CRUD compatibility routes.

The legacy router already owns GET ``/template/all`` and GET
``/template/{id}``, with incompatible response models. This adapter therefore
mounts only the upstream PATCH and DELETE contracts whose method/path pairs do
not collide.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
import logging
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Response, status
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.template_v2 import TemplateV2
from services.database import get_async_session
from services.template_v2_service import (
    TemplateV2CatalogMutationError,
    TemplateV2DefaultMutationError,
    TemplateV2NotFoundError,
    TemplateV2PersistenceConflictError,
    TemplateV2RevisionConflictError,
    TemplateV2Service,
    TemplateV2ServiceError,
)
from templates.v2.catalog import (
    get_template_v2_catalog_entry,
    is_template_v2_default_release,
    is_template_v2_release_ready,
)
from templates.v2.generation import generate_prompted_slide_layout
from templates.v2.models.layouts import (
    MergedComponents,
    SlideLayout,
    SlideLayouts,
)
from templates.v2.policy import (
    StructuredTemplatePolicyError,
    get_structured_template_policy,
)
from templates.v2.wire_codec import (
    TemplateV2WireCodecError,
    decode_wire_layouts,
)
from utils.llm_client_error_handler import handle_llm_client_exceptions


TEMPLATE_V2_COMPAT_ROUTER = APIRouter(prefix="/template", tags=["Templates"])
IconType = Literal["bold", "duotone", "fill", "light", "regular", "thin"]
LOGGER = logging.getLogger(__name__)


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


class GenerateTemplateLayoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    template_id: str = Field(
        validation_alias=AliasChoices("template_id", "id")
    )
    prompt: str = Field(min_length=1, max_length=8000)

    @model_validator(mode="after")
    def _normalize_values(self) -> "GenerateTemplateLayoutRequest":
        self.template_id = self.template_id.strip()
        self.prompt = self.prompt.strip()
        if not self.template_id:
            raise ValueError("Template ID cannot be empty")
        if not self.prompt:
            raise ValueError("Prompt cannot be empty")
        return self


class GenerateTemplateLayoutResponse(BaseModel):
    layout: SlideLayout
    response: str


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
        is_default=is_template_v2_default_release(template.id, template.assets),
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
    if isinstance(error, TemplateV2CatalogMutationError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="template_v2_catalog_is_read_only",
        )
    if isinstance(error, TemplateV2DefaultMutationError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="template_v2_default_is_catalog_managed",
        )
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


@TEMPLATE_V2_COMPAT_ROUTER.post(
    "/layouts/generate",
    response_model=GenerateTemplateLayoutResponse,
)
async def generate_template_layout_from_prompt(
    request: GenerateTemplateLayoutRequest = Body(...),
    sql_session: AsyncSession = Depends(get_async_session),
):
    """Return one owner-visible Template V2 layout draft without saving it."""

    service = TemplateV2Service(sql_session)
    try:
        template = await service.get(request.template_id)
    except TemplateV2ServiceError as error:
        raise _service_http_error(error) from error

    # Keep private/custom draft authoring available. Curated PPT Master catalog
    # identities, however, may not bypass their publication and rights gate.
    if (
        get_template_v2_catalog_entry(template.id) is not None
        and not is_template_v2_release_ready(template.id, template.assets)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="template_v2_template_not_published",
        )

    if template.layouts is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Template layouts are unavailable",
        )
    try:
        template_layouts = decode_wire_layouts(
            template.layouts
        ).validate_strict()
    except (TemplateV2WireCodecError, ValidationError) as error:
        LOGGER.exception(
            "[template.layouts.generate] template has invalid layouts "
            "template_id=%s",
            request.template_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Template layouts are invalid",
        ) from error

    merged_components: MergedComponents | None = None
    if template.merged_components is not None:
        try:
            merged_components = MergedComponents.model_validate(
                template.merged_components
            )
        except ValidationError:
            LOGGER.warning(
                "[template.layouts.generate] ignoring invalid merged "
                "components template_id=%s",
                request.template_id,
                exc_info=True,
            )

    try:
        layout = await asyncio.to_thread(
            generate_prompted_slide_layout,
            request.prompt,
            template_layouts,
            merged_components,
            template.name,
            template.description,
            _template_fonts(template),
        )
    except Exception as error:
        LOGGER.exception(
            "[template.layouts.generate] prompted layout generation failed "
            "template_id=%s",
            request.template_id,
        )
        raise handle_llm_client_exceptions(error) from error

    validated_layout = SlideLayout.model_validate(layout)
    return GenerateTemplateLayoutResponse(
        layout=validated_layout,
        response=(
            f"Created the {validated_layout.id.replace('_', ' ')} "
            "template layout."
        ),
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
    if "is_default" in request.model_fields_set:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="template_v2_default_is_catalog_managed",
        )
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
    name="delete_template_v2",
)
async def delete_template_v2(
    template_id: str = Path(...),
    sql_session: AsyncSession = Depends(get_async_session),
):
    _require_write(template_id)
    try:
        await TemplateV2Service(sql_session).delete(template_id)
    except TemplateV2ServiceError as error:
        raise _service_http_error(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
