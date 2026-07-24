"""Authenticated core CRUD for native structured Template V2 definitions."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.template_v2 import TemplateV2
from services.database import get_async_session
from services.template_v2_rollout import TemplateV2RolloutService
from services.template_v2_service import (
    TemplateV2AlreadyExistsError,
    TemplateV2NotFoundError,
    TemplateV2PersistenceConflictError,
    TemplateV2RevisionConflictError,
    TemplateV2Service,
    TemplateV2ServiceError,
    TemplateV2SourceIdentityError,
    TemplateV2SourceNotFoundError,
)
from templates.v2.constants import TEMPLATE_V2_VERSION
from templates.v2.policy import (
    StructuredTemplatePolicy,
    StructuredTemplatePolicyError,
    get_structured_template_policy,
)
from templates.v2.wire_codec import (
    TemplateV2WireCodecError,
    decode_wire_layouts,
)


STRUCTURED_TEMPLATES_ROUTER = APIRouter(
    prefix="/structured-templates",
    tags=["Structured Templates"],
)


class StructuredTemplateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    presentation_id: uuid.UUID
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    layouts: dict[str, Any]
    assets: dict[str, Any] | None = None
    is_default: bool = False

    @field_validator("layouts", mode="before")
    @classmethod
    def validate_layouts(cls, value: Any) -> dict[str, Any]:
        return _validated_wire_layouts(value)


class StructuredTemplateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str | None = Field(default=None, min_length=1, max_length=128)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    layouts: dict[str, Any] | None = None
    assets: dict[str, Any] | None = None
    is_default: bool | None = None
    expected_revision: int = Field(ge=1)

    @field_validator("layouts", mode="before")
    @classmethod
    def validate_layouts(cls, value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        return _validated_wire_layouts(value)

    @model_validator(mode="after")
    def require_a_change(self) -> "StructuredTemplateUpdate":
        if not self.model_fields_set - {"id", "expected_revision"}:
            raise ValueError("at least one mutable field is required")
        for required_field in ("name", "layouts", "is_default"):
            if (
                required_field in self.model_fields_set
                and getattr(self, required_field) is None
            ):
                raise ValueError(f"{required_field} cannot be null")
        return self


class StructuredTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    presentation_id: uuid.UUID
    name: str
    description: str | None
    layouts: dict[str, Any] | None
    assets: dict[str, Any] | None
    is_default: bool
    revision: int
    created_at: datetime
    updated_at: datetime


def _validated_wire_layouts(value: Any) -> dict[str, Any]:
    try:
        wire_layouts = decode_wire_layouts(value)
        wire_layouts.validate_strict()
    except (TemplateV2WireCodecError, ValidationError) as error:
        raise ValueError("template_v2_layouts_invalid") from error
    return wire_layouts.to_wire_value()


def _policy_http_error(error: StructuredTemplatePolicyError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error.code)


def _require_write(
    policy: StructuredTemplatePolicy,
    template_id: str | None = None,
) -> None:
    try:
        policy.require_write_enabled(template_id)
    except StructuredTemplatePolicyError as error:
        raise _policy_http_error(error) from error


def _response(template: TemplateV2) -> StructuredTemplateResponse:
    layouts = template.layouts
    if layouts is not None:
        try:
            layouts = _validated_wire_layouts(layouts)
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="template_v2_layouts_invalid",
            ) from error
    response = StructuredTemplateResponse.model_validate(template)
    return response.model_copy(update={"layouts": deepcopy(layouts)})


def _service_http_error(error: TemplateV2ServiceError) -> HTTPException:
    if isinstance(error, TemplateV2AlreadyExistsError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Structured template already exists",
        )
    if isinstance(error, TemplateV2SourceNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source presentation not found",
        )
    if isinstance(error, TemplateV2SourceIdentityError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Source presentation is not Template V2",
        )
    if isinstance(error, TemplateV2NotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Structured template not found",
        )
    if isinstance(error, TemplateV2RevisionConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "template_v2_revision_conflict",
                "expected_revision": error.expected_revision,
                "current_revision": error.current_revision,
            },
        )
    if isinstance(error, TemplateV2PersistenceConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Structured template persistence conflict",
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Structured template service failure",
    )


def _telemetry(policy: StructuredTemplatePolicy) -> TemplateV2RolloutService:
    return TemplateV2RolloutService(
        policy,
        format_marker=TEMPLATE_V2_VERSION,
    )


@STRUCTURED_TEMPLATES_ROUTER.get("", response_model=list[StructuredTemplateResponse])
async def list_structured_templates(
    offset: int = 0,
    limit: int = 100,
    sql_session: AsyncSession = Depends(get_async_session),
):
    policy = get_structured_template_policy()
    if not policy.creation_enabled or not policy.allowed_template_ids:
        return []
    if offset < 0 or limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="invalid_pagination")

    templates = await TemplateV2Service(sql_session).list(
        template_ids=policy.allowed_template_ids,
        offset=offset,
        limit=limit,
    )
    observer = _telemetry(policy)
    for template in templates:
        observer.can_discover(template.id)
    return [_response(template) for template in templates]


@STRUCTURED_TEMPLATES_ROUTER.post(
    "",
    response_model=StructuredTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_structured_template(
    payload: StructuredTemplateCreate,
    sql_session: AsyncSession = Depends(get_async_session),
):
    policy = get_structured_template_policy()
    _require_write(policy, payload.id)
    try:
        template = await TemplateV2Service(sql_session).create(
            presentation_id=payload.presentation_id,
            template_id=payload.id,
            name=payload.name,
            description=payload.description,
            layouts=payload.layouts,
            assets=payload.assets,
            is_default=payload.is_default,
        )
    except TemplateV2ServiceError as error:
        raise _service_http_error(error) from error
    _telemetry(policy).record_outcome(
        operation="create",
        outcome="success",
        template_id=template.id,
    )
    return _response(template)


@STRUCTURED_TEMPLATES_ROUTER.get(
    "/{template_id}",
    response_model=StructuredTemplateResponse,
)
async def get_structured_template(
    template_id: str,
    sql_session: AsyncSession = Depends(get_async_session),
):
    try:
        template = await TemplateV2Service(sql_session).get(template_id)
    except TemplateV2ServiceError as error:
        raise _service_http_error(error) from error
    _telemetry(get_structured_template_policy()).record_outcome(
        operation="read",
        outcome="success",
        template_id=template.id,
    )
    return _response(template)


@STRUCTURED_TEMPLATES_ROUTER.patch(
    "/{template_id}",
    response_model=StructuredTemplateResponse,
)
async def update_structured_template(
    template_id: str,
    payload: StructuredTemplateUpdate,
    sql_session: AsyncSession = Depends(get_async_session),
):
    if payload.id is not None and payload.id != template_id:
        raise HTTPException(status_code=400, detail="Template id mismatch")
    policy = get_structured_template_policy()
    _require_write(policy, template_id)
    changes = payload.model_dump(
        exclude_unset=True,
        exclude={"id", "expected_revision"},
    )
    layouts = changes.pop("layouts", None)
    if layouts is not None:
        changes["layouts"] = deepcopy(layouts)
    if "assets" in changes:
        changes["assets"] = deepcopy(changes["assets"])

    try:
        template = await TemplateV2Service(sql_session).update(
            template_id,
            changes=changes,
            expected_revision=payload.expected_revision,
        )
    except TemplateV2ServiceError as error:
        raise _service_http_error(error) from error
    _telemetry(policy).record_outcome(
        operation="save",
        outcome="success",
        template_id=template.id,
    )
    return _response(template)


@STRUCTURED_TEMPLATES_ROUTER.delete(
    "/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_structured_template(
    template_id: str,
    sql_session: AsyncSession = Depends(get_async_session),
):
    policy = get_structured_template_policy()
    _require_write(policy, template_id)
    try:
        template = await TemplateV2Service(sql_session).delete(template_id)
    except TemplateV2ServiceError as error:
        raise _service_http_error(error) from error
    _telemetry(policy).record_outcome(
        operation="delete",
        outcome="success",
        template_id=template.id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
