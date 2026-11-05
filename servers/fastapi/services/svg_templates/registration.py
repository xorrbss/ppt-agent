"""Durable draft registration, review publication and canvas preparation."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import uuid

from fastapi import HTTPException
from sqlalchemy import select, update

from models.canvas_defaults import canvas_or_default
from models.sql.presentation import PresentationModel
from models.sql.reference_snapshot import ReferenceSnapshot
from models.sql.svg_template import SvgTemplate, SvgTemplateVersion, SvgTemplateAssetBinding
from models.sql.workspace import WorkspaceConversation, WorkspaceOperation
from models.workspace import WorkspaceBootstrapRequest
from services.assets import AssetGateway
from services.references import admit_reference_snapshot
from services.references.service import _snapshot_content, _source_fingerprint
from services.references.contracts import NormalizedReference
from services.svg_templates.catalog import principal, scoped, get_template, template_card, normalized_canvas, persist_frame_adaptation
from services.svg_templates.availability import require_new_template_admission
from services.workspace_asset_service import import_workspace_document_asset
from services.workspace_service import bootstrap_workspace
from utils.datetime_utils import get_current_utc_datetime
from utils.hashing import canonical_json_digest, canonical_json_bytes

MAX_DRAFT_PACKAGE_BYTES = 64 * 1024 * 1024
MAX_DRAFT_CHECKPOINT_BYTES = 128 * 1024 * 1024


async def new_operation(session, *, kind: str, payload: dict, name: str, conversation_id=None):
    require_new_template_admission()
    owner, tenant = principal()
    if conversation_id:
        conversation = await session.scalar(select(WorkspaceConversation).where(
            WorkspaceConversation.id == conversation_id, WorkspaceConversation.owner_id == owner))
        if conversation is None:
            raise HTTPException(404, "svg_template_conversation_not_found")
        # Workspace conversations predate tenant columns. Reuse only an
        # existing timeline whose captured principals all match this tenant.
        tenant_field = WorkspaceOperation.request_payload["principal_tenant_id"].as_string()
        matching = await session.scalar(select(WorkspaceOperation.id).where(
            WorkspaceOperation.conversation_id == conversation.id, WorkspaceOperation.owner_id == owner,
            tenant_field == tenant).limit(1))
        conflicting = await session.scalar(select(WorkspaceOperation.id).where(
            WorkspaceOperation.conversation_id == conversation.id, WorkspaceOperation.owner_id == owner,
            tenant_field.is_distinct_from(tenant)).limit(1))
        if matching is None or conflicting is not None:
            raise HTTPException(404, "svg_template_conversation_not_found")
        presentation_id = conversation.presentation_id
    else:
        now = get_current_utc_datetime()
        presentation = PresentationModel(owner_id=owner, content=name, title=name, n_slides=0,
            language="ko", mode="svg", created_at=now, updated_at=now, lifecycle_status="staging")
        session.add(presentation)
        await session.flush()
        presentation_id = presentation.id
    conversation, _, operation, _ = await bootstrap_workspace(session, presentation_id=presentation_id,
        request=WorkspaceBootstrapRequest(idempotency_key=str(uuid.uuid4()), prompt=name,
            operation_type=kind, generation_settings={"svg_template_registration": payload}), commit=False)
    operation.max_attempts = 1
    return conversation, operation


async def register_template(session, *, name, snapshot_id, registration_mode,
                            template_id=None, expected_head_revision=0, conversation_id=None,
                            native_fixed_text_layout=False, fixed_glyph_preview=False,
                            source_pages=None, font_substitutions=None):
    if type(native_fixed_text_layout) is not bool or (native_fixed_text_layout and registration_mode != "framed"):
        raise ValueError("svg_template_native_layout_selector_invalid")
    if type(fixed_glyph_preview) is not bool or (fixed_glyph_preview and
            (registration_mode != "framed" or not native_fixed_text_layout)):
        raise ValueError("svg_template_fixed_glyph_selector_invalid")
    require_new_template_admission()
    owner, tenant = principal()
    admitted = await admit_reference_snapshot(session, snapshot_id=snapshot_id)
    if admitted.provider_id != "uploaded_document" or len(admitted.asset_bindings) != 1:
        raise HTTPException(422, "svg_template_single_uploaded_document_required")
    binding = admitted.asset_bindings[0]
    gateway = AssetGateway()
    confirmed, source_path = await gateway.resolve_for_processing(session,
        asset_id=binding.asset_id, kind=binding.kind, conversation_id=admitted.conversation_id)
    if confirmed != binding:
        raise HTTPException(409, "svg_template_source_changed")
    if registration_mode == "framed" and source_path.suffix.lower() != ".pptx":
        raise HTTPException(422, "svg_template_pptx_required")
    if template_id:
        template = await get_template(session, template_id)
        if template.head_revision != expected_head_revision or template.archived:
            raise HTTPException(409, "svg_template_head_conflict")
        if template.registration_state == "analyzing":
            active = await session.get(WorkspaceOperation, template.draft_operation_id)
            if active is None or active.state not in {"succeeded", "failed", "cancelled"}:
                raise HTTPException(409, "svg_template_analysis_in_progress")
    else:
        if expected_head_revision != 0:
            raise HTTPException(409, "svg_template_head_conflict")
        template = SvgTemplate(owner_id=owner, tenant_id=tenant, name=name,
            kind="framed" if registration_mode == "framed" else "style")
        session.add(template)
        await session.flush()
    prior_draft = template.draft_operation_id
    # Copy admitted source into the EXISTING canonical store. Original upload,
    # snapshot and conversation lifetimes remain untouched.
    asset, copied_path = await import_workspace_document_asset(session,
        source_path=str(source_path), original_name=source_path.name)
    try:
        retained_binding = await gateway.bind(session, asset_id=asset.id, kind=binding.kind)
        normalized_payload = admitted.payload()
        for document in normalized_payload.get("documents", []):
            if document.get("assetId") == str(binding.asset_id):
                document["assetId"] = str(asset.id)
        normalized = NormalizedReference(provider_id=admitted.provider_id,
            source_revision=canonical_json_digest([retained_binding.as_json()]), normalized_payload=normalized_payload,
            asset_bindings=(retained_binding,))
        _, content_digest, bindings = _snapshot_content(normalized)
        snapshot = ReferenceSnapshot(owner_id=owner, provider_id=normalized.provider_id,
            source_revision=normalized.source_revision, source_fingerprint=_source_fingerprint(normalized),
            normalized_payload=normalized.normalized_payload, asset_bindings=bindings,
            content_sha256=content_digest, expires_at=None)
        session.add(snapshot)
        await session.flush()
        session.add(SvgTemplateAssetBinding(template_id=template.id, owner_id=owner, tenant_id=tenant,
            asset_id=asset.id, snapshot_id=snapshot.id, binding=retained_binding.as_json()))
        asset.expires_at = None
        payload = {"template_id": str(template.id), "source_snapshot_id": str(snapshot.id),
                   "registration_mode": registration_mode, "expected_head_revision": expected_head_revision}
        if native_fixed_text_layout:
            payload["native_fixed_text_layout"] = True
        if fixed_glyph_preview:
            payload["fixed_glyph_preview"] = True
        if source_pages is not None:
            payload["source_pages"] = list(source_pages)
        if font_substitutions:
            payload["font_substitutions"] = [dict(item) for item in font_substitutions]
        conversation, operation = await new_operation(session, kind="svg.template.analyze", payload=payload,
            name=name, conversation_id=conversation_id)
        changed = (await session.execute(update(SvgTemplate).where(SvgTemplate.id == template.id,
            *scoped(SvgTemplate), SvgTemplate.head_revision == expected_head_revision,
            SvgTemplate.draft_operation_id == prior_draft, SvgTemplate.archived.is_(False)).values(
            draft_operation_id=operation.id, registration_state="analyzing").returning(SvgTemplate.id))).scalar_one_or_none()
        if changed is None:
            raise HTTPException(409, "svg_template_draft_conflict")
        await session.commit()
    except Exception:
        await session.rollback()
        copied_path.unlink(missing_ok=True)
        raise
    return {"template": template_card(template), "operationId": str(operation.id),
            "conversationId": str(conversation.id), "presentationId": str(operation.presentation_id),
            "registrationState": "analyzing"}


async def draft_detail(session, template_id):
    template = await get_template(session, template_id)
    operation = await session.scalar(select(WorkspaceOperation).where(
        WorkspaceOperation.id == template.draft_operation_id,
        WorkspaceOperation.owner_id == principal()[0],
        WorkspaceOperation.request_payload["principal_tenant_id"].as_string() == principal()[1])) if template.draft_operation_id else None
    result = deepcopy(operation.result or {}) if operation else {}
    card = template_card(template, draft=result)
    if operation and operation.state in {"failed", "cancelled"}:
        card["registrationState"] = "failed"
    return {"template": card, "operationId": str(operation.id) if operation else None,
            "state": operation.state if operation else None, "package": result.get("package"),
            "source_previews": result.get("source_previews", {}), "reviewEvidence": result.get("review_evidence", {}),
            "errorCode": operation.error_code if operation else None}


def revised_registration_payload(old_payload: dict, *, frame_reviews, expected_head_revision,
                                 representative_frame_id=None, font_substitutions=None) -> dict:
    """The analysis payload of a revision: reviews merge per frame; a given
    substitution map replaces the previous one (``[]`` clears it)."""
    payload = {**old_payload, "frame_reviews": {**old_payload.get("frame_reviews", {}), **frame_reviews},
               "expected_head_revision": expected_head_revision,
               "representative_frame_id": representative_frame_id or old_payload.get("representative_frame_id")}
    if font_substitutions is not None:
        payload.pop("font_substitutions", None)
        if font_substitutions:
            payload["font_substitutions"] = [dict(item) for item in font_substitutions]
    return payload


async def revise_draft(session, *, template_id, operation_id, expected_head_revision,
                       frame_reviews, representative_frame_id=None, font_substitutions=None):
    """Re-analyse a draft with corrected frame reviews and/or a new explicit
    font substitution map (``None`` keeps the previous map, ``[]`` clears it)."""
    require_new_template_admission()
    template = await get_template(session, template_id)
    owner, _ = principal()
    previous = await session.scalar(select(WorkspaceOperation).where(
        WorkspaceOperation.id == operation_id, WorkspaceOperation.owner_id == owner,
        WorkspaceOperation.request_payload["principal_tenant_id"].as_string() == principal()[1]))
    if (previous is None or previous.id != template.draft_operation_id or previous.state != "succeeded"
            or template.head_revision != expected_head_revision or template.archived):
        raise HTTPException(409, "svg_template_draft_conflict")
    prior = previous.result or {}
    package = prior.get("package") or {}
    known = {frame["frame_id"] for frame in package.get("frames", [])}
    if (not frame_reviews and font_substitutions is None or not set(frame_reviews).issubset(known)
            or representative_frame_id is not None and representative_frame_id not in known):
        raise HTTPException(422, "svg_template_frame_selection_invalid")
    old_payload = previous.request_payload.get("generation_settings", {}).get("svg_template_registration", {})
    payload = revised_registration_payload(old_payload, frame_reviews=frame_reviews,
        expected_head_revision=expected_head_revision, representative_frame_id=representative_frame_id,
        font_substitutions=font_substitutions)
    conversation, operation = await new_operation(session, kind="svg.template.analyze", payload=payload,
        name=template.name, conversation_id=previous.conversation_id)
    changed = (await session.execute(update(SvgTemplate).where(SvgTemplate.id == template.id,
        *scoped(SvgTemplate), SvgTemplate.head_revision == expected_head_revision,
        SvgTemplate.draft_operation_id == previous.id).values(draft_operation_id=operation.id,
            registration_state="analyzing").returning(SvgTemplate.id))).scalar_one_or_none()
    if changed is None:
        raise HTTPException(409, "svg_template_draft_conflict")
    await session.commit()
    return {"operationId": str(operation.id), "conversationId": str(conversation.id),
            "registrationState": "analyzing"}


async def publish_template(session, *, template_id, operation_id, expected_head_revision,
                           selected_frame_ids, confirm_review, style_only=False):
    require_new_template_admission()
    owner, tenant = principal()
    template = await get_template(session, template_id)
    operation = await session.scalar(select(WorkspaceOperation).where(
        WorkspaceOperation.id == operation_id, WorkspaceOperation.owner_id == owner,
        WorkspaceOperation.request_payload["principal_tenant_id"].as_string() == tenant))
    if (operation is None or operation.id != template.draft_operation_id or operation.state != "succeeded"
            or not confirm_review or template.archived):
        raise HTTPException(409, "svg_template_review_not_ready")
    draft = deepcopy(operation.result or {})
    original = operation.request_payload.get("generation_settings", {}).get("svg_template_registration", {})
    if original.get("expected_head_revision") != expected_head_revision:
        raise HTTPException(409, "svg_template_head_conflict")
    if not draft.get("style_projection"):
        raise HTTPException(409, "svg_template_design_reference_missing")
    await admit_reference_snapshot(session, snapshot_id=uuid.UUID(draft["source_snapshot_id"]))
    package = draft.get("package") or {}
    kind = "style" if style_only or original.get("registration_mode") == "style-only" else "framed"
    if kind == "framed":
        from models.svg_template_frame import SvgTemplatePackage
        from services.svg_templates.frame_review import validate_frame_review, review_image_pair_digest
        validated = SvgTemplatePackage.model_validate(package)
        from services.svg_templates.font_admission import validate_template_export_fonts
        selected = set(selected_frame_ids)
        for frame in validated.frames:
            if frame.frame_id in selected and frame.font_manifest.assets:
                validate_template_export_fonts(frame.font_manifest)
        evidence = draft.get("review_evidence", {})
        from services.svg_templates.source_review import verify_source_review_assets
        await verify_source_review_assets(session, {**evidence,
            "frames": [item for item in evidence.get("frames", []) if item.get("frame_id") in selected]})
        from services.svg_templates.frame_fonts import artifact_digest
        for entry in evidence.get("frames", []):
            if entry.get("frame_id") not in selected:
                continue
            entry["review"] = {"method": "human-source-frame-comparison",
                "classification_approved": True, "preservation_approved": True,
                "reviewed_image_pair_digest": review_image_pair_digest(evidence, entry),
                "decision_id": str(uuid.uuid4()), "reviewed_by_owner_id": str(owner),
                "reviewed_at": get_current_utc_datetime().isoformat()}
        evidence["digest"] = artifact_digest({key: value for key, value in evidence.items() if key != "digest"})
        try:
            validate_frame_review(validated, evidence, selected_frame_ids=tuple(selected_frame_ids))
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        if not selected or len(selected) != len(selected_frame_ids):
            raise HTTPException(422, "svg_template_frame_selection_required")
        from services.svg_templates.frame_review import select_reviewed_package
        package = select_reviewed_package(validated, selected_frame_ids=tuple(selected_frame_ids)).model_dump(mode="json", by_alias=True)
        validate_template_export_fonts(package["font_manifest"])
        representative = draft.get("representative_frame_id")
        if representative is not None and representative not in selected:
            raise HTTPException(422, "svg_template_representative_frame_unselected")
        evidence["analysis_font_manifest_digest"] = validated.font_manifest.digest
        evidence["analysis_package_digest"] = evidence["package_digest"]
        evidence["package_digest"] = package["digest"]
        evidence["digest"] = artifact_digest({key: value for key, value in evidence.items() if key != "digest"})
    payload = {"package": package, "style_projection": draft["style_projection"],
               "source_snapshot_id": draft["source_snapshot_id"], "kind": kind,
               "review_evidence": draft.get("review_evidence", {}), "selected_frame_ids": selected_frame_ids}
    revision = expected_head_revision + 1
    digest = canonical_json_digest({**payload, "template_id": str(template.id), "revision": revision})
    changed = (await session.execute(update(SvgTemplate).where(SvgTemplate.id == template.id,
        *scoped(SvgTemplate), SvgTemplate.head_revision == expected_head_revision,
        SvgTemplate.draft_operation_id == operation.id, SvgTemplate.archived.is_(False)).values(
        head_revision=revision, registration_state="ready", kind=kind, draft_operation_id=None).returning(SvgTemplate.id))).scalar_one_or_none()
    if changed is None:
        raise HTTPException(409, "svg_template_head_conflict")
    from services.svg_templates.storage import persist_material
    package_metadata, profile_metadata = await persist_material(session, package=package,
        style_projection=draft["style_projection"], operation_id=operation.id)
    version = SvgTemplateVersion(template_id=template.id, owner_id=owner, tenant_id=tenant,
        revision=revision, digest=digest, kind=kind, source_snapshot_id=uuid.UUID(draft["source_snapshot_id"]),
        package=package_metadata, style_projection=profile_metadata, review_evidence=payload["review_evidence"])
    session.add(version)
    await session.flush()
    retained = (await session.scalars(select(SvgTemplateAssetBinding).where(
        SvgTemplateAssetBinding.template_id == template.id, SvgTemplateAssetBinding.version_id.is_(None),
        SvgTemplateAssetBinding.snapshot_id == version.source_snapshot_id, *scoped(SvgTemplateAssetBinding)))).all()
    if not retained:
        raise HTTPException(409, "svg_template_source_retention_missing")
    for binding in retained:
        session.add(SvgTemplateAssetBinding(template_id=template.id, version_id=version.id,
            owner_id=owner, tenant_id=tenant, asset_id=binding.asset_id,
            snapshot_id=binding.snapshot_id, binding=deepcopy(binding.binding)))
    from services.svg_templates.retention import retain_artifacts
    artifact_ids = [package_metadata["artifact_ref"]["artifactId"]]
    for entry in payload["review_evidence"].get("frames", []):
        for role in ("source_image_ref", "frame_image_ref"):
            if isinstance(entry.get(role), dict) and entry[role].get("artifact_id"):
                artifact_ids.append(entry[role]["artifact_id"])
    await retain_artifacts(session, template_id=template.id, version_id=version.id, artifact_ids=artifact_ids)
    if kind == "framed":
        from services.svg_templates.frame import adapt_template_frame
        from models.svg_template_frame import SvgTemplatePackage
        published = SvgTemplatePackage.model_validate(package)
        for frame in published.frames:
            adaptation = adapt_template_frame(frame, frame.source_canvas)
            await persist_frame_adaptation(session, version_id=version.id,
                adaptation=adaptation, operation_id=operation.id)
    await session.commit()
    await session.refresh(template)
    return {"template": template_card(template, version)}


class SvgTemplateOperationHandler:
    async def execute(self, context):
        try:
            return await self._execute(context)
        except (ValueError, HTTPException) as error:
            from services.workspace_operation_handler_registry import NonRetryableWorkspaceOperationError
            code = str(getattr(error, "detail", str(error)))
            if not code.startswith("svg_template_"):
                code = "svg_template_analysis_failed"
            raise NonRetryableWorkspaceOperationError(code) from error

    async def _execute(self, context):
        from services.database import async_session_maker
        from services.workspace_operation_handler_registry import WorkspaceOperationResult, WorkspaceOperationLeaseLost
        claim = context.claim
        settings = claim.request_payload.get("generation_settings", {})
        payload = settings.get("svg_template_registration", {})
        await context.raise_if_cancelled()
        await context.progress(stage="template-analysis", percent=10, message="템플릿 자료를 확인하고 있습니다.")
        async with async_session_maker() as session:
            if claim.kind == "svg.template.prepare":
                from models.document_canvas import DocumentCanvasMetadata
                from models.svg_template_frame import SvgTemplatePackage
                from services.svg_templates.catalog import resolve_template_selection
                from services.svg_templates.frame import adapt_template_frame
                selection = await resolve_template_selection(session, pin=payload["template_pin"],
                    mode="template-redesign", canvas=payload["canvas"])
                package = SvgTemplatePackage.model_validate(selection["package"])
                selected = payload.get("frame_ids") or [f.frame_id for f in package.frames]
                if not set(selected).issubset({f.frame_id for f in package.frames}):
                    raise ValueError("svg_template_frame_selection_invalid")
                adaptations = {}
                for frame in package.frames:
                    if frame.frame_id in selected:
                        adapted = adapt_template_frame(frame, DocumentCanvasMetadata.model_validate(normalized_canvas(payload["canvas"])))
                        adaptations[frame.frame_id] = await persist_frame_adaptation(session,
                            version_id=uuid.UUID(selection["version_id"]), adaptation=adapted, operation_id=claim.operation_id)
                await context.raise_if_cancelled()
                live = await session.scalar(select(WorkspaceOperation).where(
                    WorkspaceOperation.id == claim.operation_id, WorkspaceOperation.attempt_token == claim.attempt_token,
                    WorkspaceOperation.fencing_token == claim.fencing_token, WorkspaceOperation.state == "running",
                    WorkspaceOperation.lease_expires_at > get_current_utc_datetime()).with_for_update())
                if live is None:
                    raise WorkspaceOperationLeaseLost()
                live.result = {"adaptations": adaptations}
                await session.commit()
                return WorkspaceOperationResult(payload={"adaptations": adaptations})
            template = await get_template(session, payload["template_id"])
            if template.draft_operation_id != claim.operation_id:
                raise WorkspaceOperationLeaseLost()
            snapshot_id = uuid.UUID(payload["source_snapshot_id"])
            admitted = await admit_reference_snapshot(session, snapshot_id=snapshot_id)
            binding = admitted.asset_bindings[0]
            gateway = AssetGateway()
            before, source_path = await gateway.resolve_for_processing(session,
                asset_id=binding.asset_id, kind=binding.kind)
            package = None
            if payload["registration_mode"] == "framed":
                from services.svg_templates.frame import extract_template_package
                from services.svg_templates.raster_preservation import libreoffice_raster_renderer
                # Unsupported fixed decoration becomes a reviewed image when a
                # source renderer is installed; otherwise it keeps blocking.
                package_model = await asyncio.to_thread(extract_template_package, source_path,
                    frame_reviews=payload.get("frame_reviews"),
                    native_fixed_text_layout=payload.get("native_fixed_text_layout", False),
                    source_pages=payload.get("source_pages"),
                    font_substitutions=payload.get("font_substitutions"),
                    raster_renderer=libreoffice_raster_renderer())
                captured_pdf = capture_receipt = None
                if payload.get("fixed_glyph_preview"):
                    if payload.get("fixed_glyph_preview") is not True or payload.get("native_fixed_text_layout") is not True:
                        raise ValueError("svg_template_fixed_glyph_selector_invalid")
                    from services.svg_templates.source_pdf_worker import capture_source_pdf
                    from services.svg_templates.frame_glyphs import build_preview_package
                    owner_id, tenant_id = principal()
                    captured_pdf, capture_receipt = await asyncio.to_thread(capture_source_pdf,
                        source_path, package_model, operation_id=claim.operation_id,
                        owner_id=owner_id, tenant_id=tenant_id)
                    package_model = await asyncio.to_thread(build_preview_package,
                        package_model, source_path, captured_pdf, capture_receipt)
                package = package_model.model_dump(mode="json", by_alias=True)
                if len(canonical_json_bytes(package)) > MAX_DRAFT_PACKAGE_BYTES:
                    raise ValueError("svg_template_package_size_limit")
            from services.svg_uploaded_design_reference import project_uploaded_design_reference
            canvas = package_model.source_canvas if package else None
            profile = await asyncio.to_thread(project_uploaded_design_reference, source_path,
                expected_sha256=binding.sha256, media_type=binding.media_type,
                snapshot_id=snapshot_id, snapshot_content_sha256=admitted.content_sha256,
                width=canvas_or_default(canvas)[0], height=canvas_or_default(canvas)[1])
            evidence, previews = {}, {}
            if package:
                from services.svg_templates.source_review import prepare_source_review
                evidence, previews = await prepare_source_review(session, package_model, source_path,
                    operation_id=claim.operation_id, captured_pdf=captured_pdf, capture_receipt=capture_receipt)
            after = await gateway.bind(session, asset_id=binding.asset_id, kind=binding.kind)
            if before != after:
                raise ValueError("svg_template_source_changed")
            await context.raise_if_cancelled()
            operation = await session.scalar(select(WorkspaceOperation).where(
                WorkspaceOperation.id == claim.operation_id, WorkspaceOperation.attempt_token == claim.attempt_token,
                WorkspaceOperation.fencing_token == claim.fencing_token, WorkspaceOperation.state == "running",
                WorkspaceOperation.lease_expires_at > get_current_utc_datetime()).with_for_update())
            if operation is None:
                raise WorkspaceOperationLeaseLost()
            from services.svg_templates.retention import retain_artifacts
            artifact_ids = []
            for entry in evidence.get("frames", []):
                for role in ("source_image_ref", "frame_image_ref"):
                    if isinstance(entry.get(role), dict) and entry[role].get("artifact_id"):
                        artifact_ids.append(entry[role]["artifact_id"])
            await retain_artifacts(session, template_id=template.id, artifact_ids=artifact_ids)
            result = {"package": package, "style_projection": profile,
                "source_snapshot_id": str(snapshot_id), "review_evidence": evidence, "source_previews": previews,
                "representative_frame_id": payload.get("representative_frame_id"),
                "registration_mode": payload["registration_mode"]}
            if len(canonical_json_bytes(result)) > MAX_DRAFT_CHECKPOINT_BYTES:
                raise ValueError("svg_template_checkpoint_size_limit")
            operation.result = result
            template.registration_state = "failed" if package and package.get("registration_state") == "failed" else "review-required"
            await session.commit()
            return WorkspaceOperationResult(payload=result)
