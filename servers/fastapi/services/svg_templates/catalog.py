"""One owner-validated capability boundary for catalogue and generation."""
from __future__ import annotations

from copy import deepcopy
import hmac
import uuid
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import select

from models.document_canvas import DocumentCanvasMetadata
from models.sql.svg_template import SvgTemplate, SvgTemplateVersion, SvgTemplateFrameAdaptation
from services.references import admit_reference_snapshot
from services.references import ReferenceProviderError
from utils.authored_styles import load_authored_styles
from utils.hashing import canonical_json_digest
from utils.owner_context import get_current_owner_id, get_current_tenant_id


def principal() -> tuple[uuid.UUID, str]:
    owner, tenant = get_current_owner_id(), get_current_tenant_id()
    if owner is None or not tenant:
        raise HTTPException(401, "svg_template_principal_required")
    return owner, tenant


def scoped(model):
    owner, tenant = principal()
    return model.owner_id == owner, model.tenant_id == tenant


def normalized_canvas(value) -> dict:
    if isinstance(value, DocumentCanvasMetadata):
        return value.model_dump(mode="json", by_alias=True)
    if not isinstance(value, dict):
        raise ValueError("svg_template_canvas_invalid")
    if value.get("contractVersion", value.get("contract_version")) == "document-canvas/v1":
        return DocumentCanvasMetadata.model_validate(value).model_dump(mode="json", by_alias=True)
    if value.get("contractVersion", value.get("contract_version")) == "authored-canvas/v1":
        from services.authored_canvas import AuthoredCanvasMetadata
        legacy = AuthoredCanvasMetadata.model_validate(value)
        from models.document_canvas import LogicalSize, PhysicalSize, SafeArea
        return DocumentCanvasMetadata(contract_version="document-canvas/v1",
            logical_size=LogicalSize(width=legacy.width_px, height=legacy.height_px),
            physical_size=PhysicalSize(width_emu=legacy.width_px * 9525, height_emu=legacy.height_px * 9525),
            safe_area=SafeArea(**legacy.safe_area.model_dump())).model_dump(mode="json", by_alias=True)
    from services.svg_canvas import selected_svg_canvas
    allowed = {"aspect_ratio", "aspectRatio", "page_size", "pageSize", "orientation", "svg_canvas",
               "widthMm", "heightMm", "widthEmu", "heightEmu"}
    if set(value) - allowed:
        raise ValueError("svg_template_canvas_invalid")
    custom = value.get("svg_canvas")
    if custom is None and ("widthMm" in value or "heightMm" in value):
        custom = {"width_mm": value.get("widthMm"), "height_mm": value.get("heightMm")}
    if custom is None and ("widthEmu" in value or "heightEmu" in value):
        custom = {"width_emu": value.get("widthEmu"), "height_emu": value.get("heightEmu")}
    selected = selected_svg_canvas(SimpleNamespace(svg_canvas=custom,
        page_size=value.get("page_size", value.get("pageSize")), orientation=value.get("orientation", "landscape"),
        aspect_ratio=value.get("aspect_ratio", value.get("aspectRatio", "16:9"))))
    return selected.model_dump(mode="json", by_alias=True)


def builtin_card(style) -> dict:
    identity = {"style": style.public_dict(), "brief": style.brief}
    return {"templateId": "builtin:" + style.id, "name": style.name, "provider": "builtin",
            "kind": "style", "supportedModes": ["free-design"], "defaultMode": "free-design",
            "registrationState": "ready", "fontState": "legacy-profile", "sourceCanvas": None,
            "styleId": style.id, "preview": style.public_dict()["preview"], "issues": [],
            "pin": {"templateId": "builtin:" + style.id, "revision": 1,
                    "digest": canonical_json_digest(identity)}}


def template_card(template, version=None, *, draft=None) -> dict:
    package = (version.package if version else (draft or {}).get("package")) or {}
    manifest = package.get("font_manifest", {})
    issues = package.get("issues", [])
    fonts_ready = bool(manifest.get("assets")) and not any(
        "font" in item.get("reason_code", "") for item in issues)
    draft_kind = "style" if (draft or {}).get("registration_mode") == "style-only" else (
        "framed" if (draft or {}).get("registration_mode") == "framed" else template.kind)
    framed = (version.kind if version else draft_kind) == "framed"
    result = {"templateId": str(template.id), "name": template.name, "provider": "user",
              "kind": "framed" if framed else "style", "supportedModes": ["free-design"] + (["template-redesign"] if framed else []),
              "defaultMode": "template-redesign" if framed else "free-design",
              "registrationState": "archived" if template.archived else template.registration_state,
              "fontState": "ready" if fonts_ready else "missing", "sourceCanvas": package.get("source_canvas"),
              "issues": issues, "operationId": str(template.draft_operation_id) if template.draft_operation_id else None,
              "pin": {"templateId": str(template.id), "revision": version.revision, "digest": version.digest} if version else None}
    profile = version.style_projection if version else (draft or {}).get("style_projection", {})
    examples = profile.get("references", {}).get("content", [])
    if examples:
        result["previewUrl"] = examples[0].get("data_url")
    elif version is not None:
        result["previewUrl"] = f"/api/v1/ppt/svg-templates/v1/{template.id}/versions/{version.revision}/preview"
    return result


async def get_template(session, template_id):
    try:
        identifier = uuid.UUID(str(template_id))
    except ValueError as error:
        raise HTTPException(404, "svg_template_not_found") from error
    row = await session.scalar(select(SvgTemplate).where(SvgTemplate.id == identifier, *scoped(SvgTemplate)))
    if row is None:
        raise HTTPException(404, "svg_template_not_found")
    return row


async def list_templates(session) -> list[dict]:
    cards = [builtin_card(style) for style in load_authored_styles()]
    from services.svg_templates.availability import user_templates_enabled
    if not user_templates_enabled():
        return cards
    rows = (await session.scalars(select(SvgTemplate).where(*scoped(SvgTemplate), SvgTemplate.archived.is_(False)).order_by(SvgTemplate.created_at.desc()))).all()
    for row in rows:
        version = await session.scalar(select(SvgTemplateVersion).where(SvgTemplateVersion.template_id == row.id,
                    SvgTemplateVersion.revision == row.head_revision, *scoped(SvgTemplateVersion)))
        card = template_card(row, version)
        if row.draft_operation_id:
            from models.sql.workspace import WorkspaceOperation
            operation = await session.scalar(select(WorkspaceOperation).where(
                WorkspaceOperation.id == row.draft_operation_id, WorkspaceOperation.owner_id == principal()[0]))
            if operation and operation.state in {"failed", "cancelled"}:
                card["registrationState"] = "failed"
        cards.append(card)
    return cards


def require_source_canvas(package: dict, canvas_json: dict) -> None:
    """A ``template-redesign`` deck is generated only at the template's own
    canvas, exactly (design D-4, docs/svg-template-real-deck-registration-design-20261103.md).
    A source size the millimetre selector cannot express is requested in EMU."""
    source = DocumentCanvasMetadata.model_validate(package.get("source_canvas"))
    if DocumentCanvasMetadata.model_validate(canvas_json) != source:
        raise HTTPException(422, "svg_template_canvas_locked")


async def resolve_template_selection(session, *, pin: dict, mode: str, canvas,
                                     source_mode: str | None = None, has_company_master=False,
                                     has_common_title_design=False, frame_map: dict | None = None,
                                     allow_archived: bool = False) -> dict:
    """Resolve exact immutable revision; never select a mutable catalogue head."""
    if mode not in {"free-design", "template-redesign"}:
        raise HTTPException(422, "svg_template_mode_invalid")
    canvas_json = normalized_canvas(canvas)
    template_id = str(pin.get("templateId", ""))
    if template_id.startswith("builtin:"):
        for style in load_authored_styles():
            card = builtin_card(style)
            if card["templateId"] == template_id:
                if pin != card["pin"]:
                    raise HTTPException(409, "svg_template_pin_stale")
                if mode != "free-design":
                    raise HTTPException(422, "svg_template_style_mode_unsupported")
                return {"template_pin": pin, "mode": mode, "style_id": style.id,
                        "package": {}, "style_projection": {}, "adaptations": {}}
        raise HTTPException(404, "svg_template_not_found")
    template = await get_template(session, template_id)
    if template.archived and not allow_archived:
        raise HTTPException(409, "svg_template_archived")
    version = await session.scalar(select(SvgTemplateVersion).where(SvgTemplateVersion.template_id == template.id,
                    SvgTemplateVersion.revision == pin.get("revision"), *scoped(SvgTemplateVersion)))
    if version is None or not hmac.compare_digest(version.digest, str(pin.get("digest", ""))):
        raise HTTPException(409, "svg_template_pin_stale")
    await admit_reference_snapshot(session, snapshot_id=version.source_snapshot_id)
    if source_mode in {"source_content_restyle", "raster_reconstruction"}:
        raise HTTPException(422, "svg_template_source_mode_unsupported")
    from services.svg_templates.storage import load_material
    material = await load_material(session, version)
    package = deepcopy(material["package"])
    adaptations = {}
    if mode == "template-redesign":
        if version.kind != "framed":
            raise HTTPException(422, "svg_template_style_mode_unsupported")
        if source_mode not in {None, "", "prompt", "prompt-only", "generate", "source-content", "new-content", "prompt_only"}:
            raise HTTPException(422, "svg_template_source_mode_unsupported")
        if has_company_master or has_common_title_design:
            raise HTTPException(422, "svg_template_common_frame_conflict")
        from models.svg_template_frame import SvgTemplatePackage
        from services.svg_templates.frame_fonts import resolve_font_manifest
        from services.svg_templates.font_admission import validate_template_export_fonts
        validated = SvgTemplatePackage.model_validate(package)
        if validated.schema_version.endswith("/v3"):
            from services.svg_templates.frame_glyphs import validate_frame_glyph_authority
            for frame in validated.frames:
                validate_frame_glyph_authority(frame)
        resolve_font_manifest(validated.font_manifest)
        validate_template_export_fonts(validated.font_manifest)
        rows = (await session.scalars(select(SvgTemplateFrameAdaptation).where(
            SvgTemplateFrameAdaptation.version_id == version.id,
            SvgTemplateFrameAdaptation.canvas_digest == canonical_json_digest(canvas_json),
            *scoped(SvgTemplateFrameAdaptation)))).all()
        from services.svg_templates.storage import load_adaptation_material
        from models.svg_template_frame import SvgTemplateFrameAdaptation as FrameAdaptation
        from models.svg_template_codec import pack_adaptation_fonts, unpack_adaptation_fonts
        from services.svg_templates.frame_fonts import artifact_digest
        originals = {frame.frame_id: frame for frame in validated.frames}
        for row in rows:
            stored = await load_adaptation_material(session, row.payload)
            adaptation = FrameAdaptation.model_validate(unpack_adaptation_fonts(stored, package))
            original = originals.get(row.frame_id)
            if (adaptation.digest != row.digest or artifact_digest(adaptation) != adaptation.digest
                    or normalized_canvas(adaptation.target_canvas) != canvas_json
                    or adaptation.frame.frame_id != row.frame_id or original is None
                    or adaptation.source_frame_digest != original.digest):
                raise HTTPException(409, "svg_template_adaptation_conflict")
            if adaptation.schema_version.endswith("/v3"):
                from services.svg_templates.frame_glyphs import validate_frame_glyph_authority
                validate_frame_glyph_authority(adaptation.frame)
                if adaptation.frame != original or adaptation.target_canvas != original.source_canvas:
                    raise HTTPException(409, "svg_template_adaptation_conflict")
            adaptations[row.frame_id] = pack_adaptation_fonts(
                adaptation.model_dump(mode="json", by_alias=True), package)
        allowed = {frame.frame_id for frame in validated.frames}
        if frame_map and not set(frame_map.values()).issubset(allowed):
            raise HTTPException(422, "svg_template_frame_map_invalid")
    return {"template_pin": deepcopy(pin), "mode": mode, "package": package,
            "style_projection": deepcopy(material["style_projection"]), "adaptations": adaptations,
            "source_snapshot_id": str(version.source_snapshot_id), "version_id": str(version.id)}


async def capability(session, *, template_pin: dict, mode: str, canvas, source_mode=None,
                     has_company_master=False, has_common_title_design=False) -> dict:
    identity = {"templatePin": template_pin, "mode": mode, "canvas": normalized_canvas(canvas),
                "sourceMode": source_mode, "hasCompanyMaster": has_company_master,
                "hasCommonTitleDesign": has_common_title_design}
    state, reason, action = "available", None, None
    try:
        selection = await resolve_template_selection(session, pin=template_pin, mode=mode, canvas=canvas,
            source_mode=source_mode, has_company_master=has_company_master, has_common_title_design=has_common_title_design)
        if mode == "template-redesign":
            require_source_canvas(selection["package"], identity["canvas"])
        if mode == "template-redesign" and any(frame["frame_id"] not in selection["adaptations"] for frame in selection["package"].get("frames", [])):
            state, reason, action = "preparation-required", "svg_template_canvas_preparation_required", "prepare-canvas"
    except (HTTPException, ReferenceProviderError, ValueError) as error:
        candidate = str(getattr(error, "detail", getattr(error, "code", str(error))))
        state, reason, action = "blocked", candidate if candidate.startswith(("svg_template_", "reference_", "asset_")) else "svg_template_validation_failed", "review-template"
    return {**identity, "state": state, "reasonCode": reason, "nextAction": action,
            "contractDigest": canonical_json_digest({**identity, "state": state, "reasonCode": reason})}


async def persist_frame_adaptation(session, *, version_id: uuid.UUID, adaptation, operation_id=None) -> dict:
    owner, tenant = principal()
    version = await session.scalar(select(SvgTemplateVersion).where(
        SvgTemplateVersion.id == version_id, *scoped(SvgTemplateVersion)).with_for_update())
    if version is None:
        raise HTTPException(404, "svg_template_version_not_found")
    payload = adaptation.model_dump(mode="json", by_alias=True)
    from models.svg_template_frame import SvgTemplatePackage
    from services.svg_templates.frame_fonts import artifact_digest
    if artifact_digest(adaptation) != adaptation.digest:
        raise HTTPException(409, "svg_template_adaptation_digest_mismatch")
    from services.svg_templates.storage import load_material
    package_payload = (await load_material(session, version))["package"]
    package = SvgTemplatePackage.model_validate(package_payload)
    originals = {frame.frame_id: frame for frame in package.frames}
    frame_id = adaptation.frame.frame_id
    original = originals.get(frame_id)
    if original is None or original.digest != adaptation.source_frame_digest:
        raise HTTPException(409, "svg_template_adaptation_source_mismatch")
    if normalized_canvas(adaptation.frame.source_canvas) != normalized_canvas(adaptation.target_canvas):
        raise HTTPException(409, "svg_template_adaptation_canvas_mismatch")
    canvas_digest = canonical_json_digest(normalized_canvas(adaptation.target_canvas))
    existing = await session.scalar(select(SvgTemplateFrameAdaptation).where(
        SvgTemplateFrameAdaptation.version_id == version_id, SvgTemplateFrameAdaptation.frame_id == frame_id,
        SvgTemplateFrameAdaptation.canvas_digest == canvas_digest, *scoped(SvgTemplateFrameAdaptation)))
    if existing:
        if existing.digest != adaptation.digest:
            raise HTTPException(409, "svg_template_adaptation_conflict")
        from services.svg_templates.storage import load_adaptation_material
        from models.svg_template_codec import pack_adaptation_fonts, unpack_adaptation_fonts
        from models.svg_template_frame import SvgTemplateFrameAdaptation as FrameAdaptation
        stored = await load_adaptation_material(session, existing.payload)
        restored = FrameAdaptation.model_validate(unpack_adaptation_fonts(stored, package_payload))
        if artifact_digest(restored) != existing.digest or restored != adaptation:
            raise HTTPException(409, "svg_template_adaptation_conflict")
        return pack_adaptation_fonts(restored.model_dump(mode="json", by_alias=True), package_payload)
    if operation_id is None:
        operation_id = uuid.UUID(version.package["artifact_ref"]["createdByOperationId"])
    from services.svg_templates.storage import persist_adaptation_material
    from models.svg_template_codec import pack_adaptation_fonts
    compact_payload = pack_adaptation_fonts(payload, package_payload)
    metadata = await persist_adaptation_material(session, payload=compact_payload, operation_id=operation_id)
    session.add(SvgTemplateFrameAdaptation(version_id=version_id, owner_id=owner, tenant_id=tenant,
        frame_id=frame_id, canvas_digest=canvas_digest, digest=adaptation.digest, payload=metadata))
    from services.svg_templates.retention import retain_artifacts
    await retain_artifacts(session, template_id=version.template_id, version_id=version.id,
        artifact_ids=[metadata["artifact_ref"]["artifactId"]])
    await session.flush()
    return compact_payload
