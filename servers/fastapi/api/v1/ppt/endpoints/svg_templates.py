"""Path-free, version-pinned SVG template catalogue and registration API."""
from __future__ import annotations

from typing import Literal
import base64
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select, update

from models.sql.svg_template import SvgTemplate, SvgTemplateVersion
from models.sql.workspace import WorkspaceOperation
from services.database import get_async_session
from services.references import ReferenceProviderError
from services.assets import AssetGatewayError
from services.artifact_store import ArtifactStoreError
from services.svg_templates.catalog import (capability, list_templates, get_template, principal, scoped,
                                           template_card, resolve_template_selection)
from services.svg_templates.registration import register_template, draft_detail, publish_template, new_operation, revise_draft
from services.svg_templates.availability import user_templates_enabled, require_new_template_admission
from models.svg_template_frame import FrameRect
from services.workspace_service import WorkspaceServiceError

SVG_TEMPLATES_ROUTER = APIRouter(prefix="/svg-templates/v1", tags=["SVG Templates"])


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TemplatePin(Wire):
    template_id: str = Field(alias="templateId", min_length=1, max_length=255)
    revision: int = Field(ge=1)
    digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")


class FontSubstitution(Wire):
    """An explicit, recorded replacement for a source font the server cannot use."""
    source_family: str = Field(alias="sourceFamily", min_length=1, max_length=128)
    target_family: str = Field(alias="targetFamily", min_length=1, max_length=128)
    target_weight: int = Field(alias="targetWeight", ge=100, le=900)


class RegistrationRequest(Wire):
    name: str = Field(min_length=1, max_length=160)
    reference_snapshot_id: uuid.UUID = Field(alias="referenceSnapshotId")
    registration_mode: Literal["framed", "style-only"] = Field(default="framed", alias="registrationMode")
    template_id: uuid.UUID | None = Field(default=None, alias="templateId")
    expected_head_revision: int = Field(default=0, ge=0, alias="expectedHeadRevision")
    conversation_id: uuid.UUID | None = Field(default=None, alias="conversationId")
    native_fixed_text_layout: bool = Field(default=False, alias="nativeFixedTextLayout", strict=True)
    fixed_glyph_preview: bool = Field(default=False, alias="fixedGlyphPreview", strict=True)
    # 1-based source pages to analyse; omitted: page 1 plus the first page of each layout.
    source_pages: list[int] | None = Field(default=None, alias="sourcePages", min_length=1, max_length=100)
    font_substitutions: list[FontSubstitution] | None = Field(default=None, alias="fontSubstitutions",
                                                              max_length=32)

    @model_validator(mode="after")
    def validate_fixed_glyph_selector(self):
        if self.fixed_glyph_preview and (self.registration_mode != "framed" or not self.native_fixed_text_layout):
            raise ValueError("svg_template_fixed_glyph_selector_invalid")
        return self

    @field_validator("name")
    @classmethod
    def validate_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("template name must not be blank")
        return value


class CapabilityRequest(Wire):
    template_pin: TemplatePin = Field(alias="templatePin")
    mode: Literal["free-design", "template-redesign"]
    canvas: dict
    source_mode: str | None = Field(default=None, alias="sourceMode")
    has_company_master: bool = Field(default=False, alias="hasCompanyMaster")
    has_common_title_design: bool = Field(default=False, alias="hasCommonTitleDesign")


class PublicationRequest(Wire):
    operation_id: uuid.UUID = Field(alias="operationId")
    expected_head_revision: int = Field(ge=0, alias="expectedHeadRevision")
    selected_frame_ids: list[str] = Field(default_factory=list, alias="selectedFrameIds", max_length=200)
    confirm_review: bool = Field(alias="confirmReview")
    style_only: bool = Field(default=False, alias="styleOnly")


class PreparationRequest(Wire):
    template_pin: TemplatePin = Field(alias="templatePin")
    canvas: dict
    frame_ids: list[str] = Field(default_factory=list, alias="frameIds", max_length=200)


class FrameReview(Wire):
    frame_id: str = Field(alias="frameId", min_length=1, max_length=128)
    fixed_shape_ids: list[str] = Field(alias="fixedShapeIds", max_length=500)
    body_region: FrameRect = Field(alias="bodyRegion")
    role: Literal["cover", "section", "body"] = "body"


class DraftRevisionRequest(Wire):
    operation_id: uuid.UUID = Field(alias="operationId")
    expected_head_revision: int = Field(alias="expectedHeadRevision", ge=0)
    frame_reviews: list[FrameReview] = Field(alias="frameReviews", default_factory=list, max_length=200)
    representative_frame_id: str | None = Field(alias="representativeFrameId", default=None)
    #: Replaces the draft's explicit font substitutions; omitted keeps them.
    font_substitutions: list[FontSubstitution] | None = Field(default=None, alias="fontSubstitutions",
                                                              max_length=32)


def public_error(error):
    if isinstance(error, ArtifactStoreError):
        return HTTPException(409, "svg_template_artifact_unavailable")
    if isinstance(error, (ReferenceProviderError, AssetGatewayError, WorkspaceServiceError)):
        return HTTPException(error.status_code, error.code)
    if isinstance(error, ValueError):
        return HTTPException(422, str(error) if str(error).startswith("svg_template_") else "svg_template_validation_failed")
    return error


@SVG_TEMPLATES_ROUTER.get("")
async def catalog(session=Depends(get_async_session)):
    return {"templates": await list_templates(session)}


@SVG_TEMPLATES_ROUTER.get("/availability")
async def availability():
    return {"enabled": user_templates_enabled()}


@SVG_TEMPLATES_ROUTER.post("/register", status_code=202)
async def register(request: RegistrationRequest, session=Depends(get_async_session)):
    try:
        return await register_template(session, name=request.name.strip(), snapshot_id=request.reference_snapshot_id,
            registration_mode=request.registration_mode, template_id=request.template_id,
            expected_head_revision=request.expected_head_revision, conversation_id=request.conversation_id,
            native_fixed_text_layout=request.native_fixed_text_layout, fixed_glyph_preview=request.fixed_glyph_preview,
            source_pages=request.source_pages,
            font_substitutions=[item.model_dump() for item in request.font_substitutions]
                if request.font_substitutions is not None else None)
    except (ReferenceProviderError, AssetGatewayError, WorkspaceServiceError, ValueError) as error:
        raise public_error(error) from error


@SVG_TEMPLATES_ROUTER.post("/capability")
async def check_capability(request: CapabilityRequest, session=Depends(get_async_session)):
    try:
        return await capability(session, template_pin=request.template_pin.model_dump(by_alias=True),
            mode=request.mode, canvas=request.canvas, source_mode=request.source_mode,
            has_company_master=request.has_company_master, has_common_title_design=request.has_common_title_design)
    except (ReferenceProviderError, AssetGatewayError, ArtifactStoreError, ValueError) as error:
        return {"state": "blocked", "reasonCode": getattr(error, "code", "svg_template_validation_failed"),
                "nextAction": "review-template", "templatePin": request.template_pin.model_dump(by_alias=True)}


@SVG_TEMPLATES_ROUTER.get("/operations/{operation_id}")
async def operation_detail(operation_id: uuid.UUID, session=Depends(get_async_session)):
    owner, tenant = principal()
    operation = await session.scalar(select(WorkspaceOperation).where(WorkspaceOperation.id == operation_id,
        WorkspaceOperation.owner_id == owner, WorkspaceOperation.kind.in_(["svg.template.analyze", "svg.template.prepare"])))
    if operation is None or operation.request_payload.get("principal_tenant_id") != tenant:
        raise HTTPException(404, "svg_template_operation_not_found")
    return {"operationId": str(operation.id), "state": operation.state, "progress": operation.progress,
            "result": operation.result, "errorCode": operation.error_code}


@SVG_TEMPLATES_ROUTER.post("/adaptations", status_code=202)
async def prepare(request: PreparationRequest, session=Depends(get_async_session)):
    require_new_template_admission()
    if len(request.frame_ids) != len(set(request.frame_ids)):
        raise HTTPException(422, "svg_template_duplicate_frame_selection")
    pin = request.template_pin.model_dump(by_alias=True)
    try:
        selection = await resolve_template_selection(session, pin=pin, mode="template-redesign", canvas=request.canvas)
    except (ReferenceProviderError, AssetGatewayError, ArtifactStoreError, ValueError) as error:
        raise public_error(error) from error
    allowed = {frame["frame_id"] for frame in selection["package"].get("frames", [])}
    if not set(request.frame_ids).issubset(allowed):
        raise HTTPException(422, "svg_template_frame_selection_invalid")
    # The operation's timeline is titled with the template's own name, as analysis is.
    template = await get_template(session, pin["templateId"])
    _, operation = await new_operation(session, kind="svg.template.prepare", name=template.name,
        payload={"template_pin": pin, "canvas": request.canvas,
                 "frame_ids": request.frame_ids})
    await session.commit()
    return {"operationId": str(operation.id), "state": operation.state}


@SVG_TEMPLATES_ROUTER.get("/{template_id}/draft")
async def draft(template_id: uuid.UUID, session=Depends(get_async_session)):
    return await draft_detail(session, template_id)


@SVG_TEMPLATES_ROUTER.patch("/{template_id}/draft", status_code=202)
async def correct_draft(template_id: uuid.UUID, request: DraftRevisionRequest, session=Depends(get_async_session)):
    if len({item.frame_id for item in request.frame_reviews}) != len(request.frame_reviews):
        raise HTTPException(422, "svg_template_duplicate_frame_review")
    return await revise_draft(session, template_id=template_id, operation_id=request.operation_id,
        expected_head_revision=request.expected_head_revision,
        frame_reviews={item.frame_id: item.model_dump(exclude={"frame_id"}) for item in request.frame_reviews},
        representative_frame_id=request.representative_frame_id,
        font_substitutions=[item.model_dump() for item in request.font_substitutions]
            if request.font_substitutions is not None else None)


@SVG_TEMPLATES_ROUTER.get("/{template_id}/versions/{revision}")
async def version(template_id: uuid.UUID, revision: int, session=Depends(get_async_session)):
    template = await get_template(session, template_id)
    version = await session.scalar(select(SvgTemplateVersion).where(SvgTemplateVersion.template_id == template.id,
        SvgTemplateVersion.revision == revision, *scoped(SvgTemplateVersion)))
    if version is None:
        raise HTTPException(404, "svg_template_version_not_found")
    from services.svg_templates.storage import load_material
    material = await load_material(session, version)
    return {"template": template_card(template, version), "package": material["package"],
            "reviewEvidence": version.review_evidence}


@SVG_TEMPLATES_ROUTER.get("/{template_id}/versions/{revision}/preview", name="svg_template_preview")
async def preview(template_id: uuid.UUID, revision: int, session=Depends(get_async_session)):
    template = await get_template(session, template_id)
    version = await session.scalar(select(SvgTemplateVersion).where(SvgTemplateVersion.template_id == template.id,
        SvgTemplateVersion.revision == revision, *scoped(SvgTemplateVersion)))
    if version is None:
        raise HTTPException(404, "svg_template_version_not_found")
    from services.svg_templates.storage import load_material
    material = await load_material(session, version)
    examples = material["style_projection"].get("references", {}).get("content", [])
    if not examples:
        from services.svg_templates.storage import template_frame_preview
        payload = await template_frame_preview(session, version, material)
        if payload is None:
            raise HTTPException(404, "svg_template_preview_unavailable")
        return Response(payload, media_type="image/png",
            headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})
    data_url = examples[0].get("data_url", "")
    if not data_url.startswith(("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,")):
        raise HTTPException(409, "svg_template_preview_invalid")
    header, encoded = data_url.split(",", 1)
    payload = base64.b64decode(encoded, validate=True)
    return Response(payload, media_type=header[5:].split(";", 1)[0],
        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})


@SVG_TEMPLATES_ROUTER.post("/{template_id}/publish")
async def publish(template_id: uuid.UUID, request: PublicationRequest, session=Depends(get_async_session)):
    try:
        return await publish_template(session, template_id=template_id, operation_id=request.operation_id,
            expected_head_revision=request.expected_head_revision, selected_frame_ids=request.selected_frame_ids,
            confirm_review=request.confirm_review, style_only=request.style_only)
    except (ReferenceProviderError, AssetGatewayError, ArtifactStoreError, ValueError) as error:
        raise public_error(error) from error


@SVG_TEMPLATES_ROUTER.post("/{template_id}/archive")
async def archive(template_id: uuid.UUID, session=Depends(get_async_session)):
    template = await get_template(session, template_id)
    await session.execute(update(SvgTemplate).where(SvgTemplate.id == template.id, *scoped(SvgTemplate)).values(
        archived=True, registration_state="archived"))
    await session.commit()
    return {"templateId": str(template.id), "registrationState": "archived"}
