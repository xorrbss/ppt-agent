"""SVG candidates on the existing preview-task and revision-CAS workflow."""
from __future__ import annotations

from copy import deepcopy
from collections import Counter
import re
import xml.etree.ElementTree as ET

from sqlalchemy import select, update
from constants.user_messages import UserMessageKey, user_message
from models.sql.slide import SlideModel
from services.authored_canvas import presentation_canvas_contract, presentation_canvas_metadata
from services.authored_presentation_artifacts import _save_slide_pngs
from services.svg_contract import finalize_svg, source_digest
from services.svg_renderer import render_svg_with_metrics
from services.generation_core.authoring.author_slide_svg import author_slide_svg
from utils.hashing import sha256_hex


def _v7_source_profile(snapshot):
    from services.svg_authoring_profile import read_authoring_profile

    profile = read_authoring_profile(snapshot)
    return bool(profile and profile["version"] in {7, 8})


def _local_svg_supported(presentation, target):
    from services.svg_document_mutations import _raster_pin

    receipt = (target.content or {}).get("svgReceipt") or {}
    if (presentation.theme.get("svg", {}).get("sourcePreservation") is not None
            or receipt.get("source_preservation") is not None or _raster_pin(target) is not None):
        raise ValueError("svg_local_edit_source_preservation_unsupported")


def _local_svg_content(svg, baseline):
    """A local layout correction retains the current page's text and images."""
    from services.svg_contract import PAGE_NUMBER_ID

    if len(svg.encode("utf-8")) > 2 * 1024 * 1024 or re.search(r"<!|<\?", svg):
        raise ValueError("svg_local_edit_input_invalid")
    root, previous = ET.fromstring(svg), ET.fromstring(baseline)

    def text_nodes(tree):
        return Counter(" ".join("".join(node.itertext()).split()) for node in tree.iter()
            if node.tag == "{http://www.w3.org/2000/svg}text"
            and node.get("id") != PAGE_NUMBER_ID
            and not node.get("id", "").startswith("svg-system-master-"))

    def images(tree):
        return Counter(node.get("href") for node in tree.iter()
                       if node.tag == "{http://www.w3.org/2000/svg}image")

    if text_nodes(root) != text_nodes(previous) or images(root) != images(previous):
        raise ValueError("svg_local_edit_content_changed")
    return root


def _local_native_projection(svg, native, presentation, target):
    objects, summary = _native_projection(svg, native, presentation_canvas_metadata(presentation.theme),
        (target.content or {}).get("source", ""))
    from services.generation_core.native_objects import SLIDE_CONTENT_KEY
    previous = (target.content or {}).get(SLIDE_CONTENT_KEY) or {}
    previous_ids = {(item.get("id"), item.get("kind")) for item in previous.get("objects", [])}
    current_ids = {(item.get("id"), item.get("kind")) for item in (objects or {}).get("objects", [])}
    if (summary and summary.get("dropped")
            or not previous_ids.issubset(current_ids)):
        raise ValueError("svg_local_edit_native_evidence_required")
    return objects, summary


def _source_candidate_quality(quality, svg, source):
    """Apply the same measured source guards used before generation publication."""
    from services.generation_core.drivers.svg.timeline_ink_guard import (
        apply_timeline_ink_quality, source_timeline_rows,
    )
    from services.generation_core.drivers.svg.source_classification_association import apply_source_classification_association
    from services.generation_core.drivers.svg.source_paint_occlusion import apply_source_paint_occlusion

    if source_timeline_rows(source):
        apply_timeline_ink_quality(quality, svg, source, allow_unlabeled=True)
    apply_source_classification_association(quality, svg, source)
    apply_source_paint_occlusion(quality, svg)
    paint = quality.get("source_paint_occlusion", {}).get("status")
    if paint in {"occluded", "unproven"}:
        return "svg_source_paint_occluded" if paint == "occluded" else "svg_source_paint_unproven"
    if quality.get("source_classification_association", {}).get("status") == "unproven":
        return "svg_source_classification_missing"
    if quality.get("timeline_caption_ink", {}).get("collisions"):
        return "svg_timeline_caption_ink_failed"
    if quality.get("passed") is not True:
        return "svg_source_quality_failed"
    return None


def _native_candidate(svg, authored_svg, declarations):
    from services.generation_core.drivers.svg.native_declarations import bind_native_declarations, native_binding_digest
    payload = {"svgDigest": source_digest(svg), "nativeBindingDigest": native_binding_digest(svg)}
    bind_native_declarations(payload, authored_svg, declarations)
    return payload


def _candidate_digest(svg, image, native=None, *, mode="author", source_binding=None):
    from services.authored_page_remake_service import _canonical_digest
    payload = {"svg": svg, "image": image, **({"native": native} if native is not None else {})}
    if mode in {"remeasure_source", "local_svg"}:
        payload.update(mode=mode, source_binding=source_binding)
    return _canonical_digest(payload)


def _native_projection(svg, native, metadata, source):
    from services.generation_core.drivers.svg.native_declarations import bound_declarations, native_binding_digest
    from services.generation_core.drivers.svg.native_publish import canvas_point_per_px, page_native_objects
    from services.generation_core.drivers.svg.native_requirements import apply_requested_native_quality
    if native is not None and (not isinstance(native, dict) or native.get("svgDigest") != source_digest(svg)
            or native.get("nativeBindingDigest") != native_binding_digest(svg)):
        raise ValueError("svg_page_remake_native_binding_invalid")
    payload = {**(native or {}), "svg_content": svg}
    record, current = bound_declarations(payload, svg)
    if record is not None and not current:
        raise ValueError("svg_page_remake_native_binding_invalid")
    scale = canvas_point_per_px(metadata)
    quality = {"passed": True, "failures": []}
    if native is not None:
        apply_requested_native_quality(quality, payload, svg, source, pt_per_px=scale)
        if quality.get("passed") is False:
            raise ValueError("svg_page_remake_requested_native_missing")
    return page_native_objects(payload, pt_per_px=scale)


async def preview_svg(presentation, slides, index, request, task, session):
    from services.authored_page_remake_service import (
        AuthoredPageRemakeError, PREVIEW_KIND, _set_task, authored_page_source_digest,
    )
    target = slides[index]
    mode = getattr(request, "mode", "author")
    local_measurement = mode == "remeasure_source"
    local_edit = mode == "local_svg"
    local = local_measurement or local_edit
    if mode not in {"author", "remeasure_source", "local_svg"}:
        raise ValueError("svg_page_remake_mode_invalid")
    if (task.owner_id != presentation.owner_id or target.owner_id != presentation.owner_id
            or target.presentation != presentation.id):
        raise ValueError("svg_page_remake_source_changed")
    if local and getattr(request, "source_digest", None) != source_digest(target.svg_content):
        raise ValueError("svg_page_remake_source_changed")
    if local_edit:
        _local_svg_supported(presentation, target)
    from services.svg_document_mutations import read_svg_slide_assets, rebound_placement, materialize_rebound_svg
    assets = await read_svg_slide_assets(session, presentation, target)
    canvas = presentation_canvas_contract(presentation.theme)
    from services.document_canvas import DocumentCanvasMetadata, parse_canvas_metadata
    metadata = parse_canvas_metadata(presentation_canvas_metadata(presentation.theme))
    physical_kwargs = {"physical_canvas": metadata} if isinstance(metadata, DocumentCanvasMetadata) else {}
    snapshot = presentation.theme["svg"]["styleSnapshot"]
    v7_source = _v7_source_profile(snapshot)
    manifest = {}
    for number, node in enumerate(ET.fromstring(target.svg_content).iter()):
        if node.tag.endswith("}image"):
            manifest[f"source-slot-{number}"] = {"data_url": node.get("href"), "required": True,
                                                "description": "Preserve this existing source image"}
    contract = None
    if assets is not None:
        source_index = target.content["svgAssetManifest"].get("sourcePageIndex", index)
    if assets is not None and local_edit:
        from services.svg_asset_resolver import _contract_manifest
        offered = next(page.slot_ids for page in assets[0].offered_pages if page.page_index == source_index)
        contract = {slot: policy for slot, policy in _contract_manifest(assets[0]).items() if slot in offered}
    if assets is not None and not local:
        from types import SimpleNamespace
        from services.artifact_store import configured_public_artifact_service
        from services.svg_generation_assets import author_images
        from services.svg_asset_resolver import _contract_manifest
        from utils.owner_context import get_current_owner_id, get_current_tenant_id
        manifest = await author_images(session, configured_public_artifact_service(),
            context=SimpleNamespace(owner_id=get_current_owner_id(), tenant_id=get_current_tenant_id()),
            manifest=assets[0], page_index=source_index)
        contract = {key: value for key, value in _contract_manifest(assets[0]).items() if key in manifest}
    source = target.content.get("source") or "\n".join(ET.fromstring(target.svg_content).itertext())
    baseline_digest = authored_page_source_digest(target)
    await _set_task(session, task, status="running",
                    message=user_message(UserMessageKey.REMAKE_SVG_RUNNING, page=index + 1))
    from utils.generation_model import generation_model_context
    instruction = "\n".join(value for value in [presentation.instructions,
        None if local else request.instruction] if value)
    result = None

    def reject(error, stage, *, candidate_svg=None, candidate_native=None, candidate_quality=None):
        code = getattr(error, "code", None)
        if not isinstance(code, str):
            match = re.match(r"(svg_[a-z0-9_]+)(?::|$)", str(error))
            code = match[1] if match else "svg_page_remake_candidate_invalid"
        evidence = {
            "kind": PREVIEW_KIND, "phase": "preview_rejected", "source_format": "svg",
            "presentation_id": str(presentation.id), "slide_id": str(target.id), "slide_index": index,
            "source_digest": baseline_digest, "source_revision": presentation.revision, "mode": mode,
            "original_source_digest": source_digest(target.svg_content),
            "source_content_digest": "sha256:" + sha256_hex(source.encode("utf-8")),
            "provider_result_returned": result is not None,
            "failure_class": type(error).__name__, "failure_detail": str(error)[:600],
            "instruction": instruction,
            "validation": {"candidate_render": "failed" if candidate_quality is not None else "not_measured",
                           "reason_code": code, "stage": stage},
            "publish": {"status": "blocked", "rollback_available": False},
        }
        if result is not None:
            evidence.update(raw_response=result.raw_response, provider_receipt=result.receipt,
                            candidate_svg=result.svg_content)
        if candidate_svg is not None:
            evidence.update(candidate_svg=candidate_svg, canonical_source_digest=source_digest(candidate_svg))
        if candidate_native is not None:
            evidence["candidate_native"] = candidate_native
        if candidate_quality is not None:
            evidence["svg_quality"] = candidate_quality
        raise AuthoredPageRemakeError(code, stage=stage, retryable=False,
                                     preview_evidence=evidence) from error

    if local_measurement:
        final, native = target.svg_content, None
    elif local_edit:
        try:
            from services.generation_core.drivers.svg.native_declarations import extract_native_declarations
            root = _local_svg_content(request.svg_content, target.svg_content)
            declarations = extract_native_declarations(root)
            clean = ET.tostring(root, encoding="unicode")
            final = finalize_svg(clean, index=index, n=len(slides), width=canvas.width_px,
                height=canvas.height_px, style_snapshot=snapshot, source_manifest=contract)
            native = _native_candidate(final, clean, declarations)
            _local_native_projection(final, native, presentation, target)
        except Exception as error:
            reject(error, "local_candidate_validation")
    else:
        try:
            with generation_model_context(presentation.theme["svg"].get("codexGenerationPreset")):
                result = await author_slide_svg(source, snapshot, target.content.get("role", "content"), index, len(slides),
                                                instructions=instruction, source_images=manifest,
                                                canonical_source_images=assets is not None, **physical_kwargs)
        except Exception as error:
            reject(error, "generation")
        if result.receipt.get("validation_error"):
            reject(ValueError(result.receipt["validation_error"]), "author_validation")
        try:
            final = finalize_svg(result.svg_content, index=index, n=len(slides), width=canvas.width_px,
                                 height=canvas.height_px, style_snapshot=snapshot, source_manifest=contract)
        except Exception as error:
            reject(error, "canonical_validation")
        try:
            native = _native_candidate(final, result.svg_content, result.native_declarations)
            _native_projection(final, native, presentation_canvas_metadata(presentation.theme), source)
        except Exception as error:
            reject(error, "native_validation", candidate_svg=final)
    render_source = final
    try:
        from services.svg_presentation_checkpoint import _required_numeric_claims
        if assets is not None:
            placement = rebound_placement(final, assets[0], assets[1], revision=presentation.revision + 1)
            render_source = await materialize_rebound_svg(session, presentation, final, assets[0], placement, source_index)
        render = await render_svg_with_metrics(final, width=canvas.width_px, height=canvas.height_px,
            materialized_svg=render_source if render_source != final else None,
            instructions=instruction, **({"timeline_ink_probe": True} if v7_source else {}),
            **({"required_text": _required_numeric_claims(source)} if local_edit else {}),
            **physical_kwargs)
    except Exception as error:
        reject(error, "candidate_render", candidate_svg=final, candidate_native=native)
    try:
        failure = _source_candidate_quality(render.quality, final, source) if v7_source else None
    except Exception as error:
        reject(error, "source_quality", candidate_svg=final, candidate_native=native,
               candidate_quality=render.quality)
    if not failure and local and (render.quality.get("passed") is not True
            or render.quality.get("measurement_complete") is not True
            or render.quality.get("source_digest") != source_digest(final)):
        failure = "svg_source_quality_failed"
    try:
        image = _save_slide_pngs(presentation.id, [render.png], str(task.id))[0]
    except Exception as error:
        reject(error, "candidate_capture", candidate_svg=final, candidate_native=native,
               candidate_quality=render.quality)
    source_binding = source_digest(target.svg_content) if local else None
    digest = _candidate_digest(final, image, native, mode=mode, source_binding=source_binding)
    if failure:
        evidence = {
            "kind": PREVIEW_KIND, "phase": "preview_rejected", "source_format": "svg",
            "presentation_id": str(presentation.id), "slide_id": str(target.id), "slide_index": index,
            "source_digest": baseline_digest, "source_revision": presentation.revision,
            "mode": mode, "canonical_source_digest": source_digest(final),
            "original_source_digest": source_digest(target.svg_content),
            "source_content_digest": "sha256:" + sha256_hex(source.encode("utf-8")),
            **({"remeasure_source_digest": source_binding} if local_measurement else {}),
            **({"local_source_digest": source_binding} if local_edit else {}),
            "candidate_svg": final, "candidate_image": image, "candidate_digest": digest,
            "svg_quality": render.quality, "candidate_native": native, "instruction": instruction,
            **({"raw_response": result.raw_response, "provider_receipt": result.receipt} if result is not None else {}),
            "validation": {"candidate_render": "failed", "reason_code": failure},
            "publish": {"status": "blocked", "rollback_available": False},
        }
        raise AuthoredPageRemakeError(failure, stage="source_quality", retryable=False,
                                     preview_evidence=evidence)
    ready_message = user_message(UserMessageKey.REMAKE_SVG_READY, page=index + 1)
    await _set_task(session, task, status="completed", message=ready_message, data={
        "kind": PREVIEW_KIND, "phase": "preview_ready", "source_format": "svg",
        "presentation_id": str(presentation.id), "slide_id": str(target.id), "slide_index": index,
        "source_digest": baseline_digest, "candidate_digest": digest,
        "source_revision": presentation.revision,
        "mode": mode,
        **({"remeasure_source_digest": source_binding} if local_measurement else {}),
        **({"local_source_digest": source_binding} if local_edit else {}),
        "candidate_svg": final, "candidate_image": image, "svg_quality": render.quality,
        "candidate_native": native,
        **({"raw_response": result.raw_response, "provider_receipt": result.receipt} if result is not None else {}),
        "instruction": instruction,
        "validation": {"candidate_render": "passed", "candidate_dom": "warning", "candidate_change": "unchanged_source" if local_measurement else "changed",
                       "semantic_review": "required"},
        "publish": {"status": "not_started", "rollback_available": False},
    })


async def publish_svg_candidate(session, presentation, slides, index, task, *, snapshot=True,
                                operation_id=None, attempt_token=None, expected_revision=None):
    from services.authored_page_remake_service import (
        AuthoredPageRemakeError, PREVIEW_KIND, authored_page_source_digest,
        _json_cas_predicate, version_service,
    )
    target = slides[index]
    data = task.data or {}
    from services.svg_document_mutations import read_svg_slide_assets, rebind_svg_assets
    assets = await read_svg_slide_assets(session, presentation, target)
    if (task.status != "completed" or task.owner_id != presentation.owner_id
            or target.owner_id != presentation.owner_id or data.get("kind") != PREVIEW_KIND
            or data.get("presentation_id") != str(presentation.id) or data.get("slide_id") != str(target.id)
            or data.get("slide_index") != index or data.get("source_format") != "svg"):
        raise AuthoredPageRemakeError("svg_page_remake_source_changed", stage="publish_validation")
    current_candidate = (data.get("phase") == "published" and data.get("publish", {}).get("status") == "published"
        and operation_id is not None and attempt_token is not None and expected_revision is not None
        and expected_revision == presentation.revision and target.svg_content == data.get("candidate_svg")
        and (target.properties or {}).get("image") == data.get("candidate_image"))
    if not current_candidate and (data.get("phase") != "preview_ready"
            or data.get("source_revision") != presentation.revision
            or data.get("source_digest") != authored_page_source_digest(target)):
        raise AuthoredPageRemakeError("svg_page_remake_source_changed", stage="publish_validation")
    svg, image = data.get("candidate_svg"), data.get("candidate_image")
    native = data.get("candidate_native")
    mode = data.get("mode", "author")
    local_measurement = mode == "remeasure_source"
    local_edit = mode == "local_svg"
    local = local_measurement or local_edit
    source_binding = data.get("remeasure_source_digest") if local_measurement else data.get("local_source_digest")
    if mode not in {"author", "remeasure_source", "local_svg"} or local_measurement and (
            svg != target.svg_content or source_binding != source_digest(target.svg_content)
            or native is not None):
        raise AuthoredPageRemakeError("svg_page_remake_source_changed", stage="publish_validation")
    if local_edit:
        try:
            _local_svg_supported(presentation, target)
            _local_svg_content(svg, target.svg_content)
            if not current_candidate and source_binding != source_digest(target.svg_content):
                raise ValueError("svg_page_remake_source_changed")
        except (ValueError, TypeError, ET.ParseError) as error:
            raise AuthoredPageRemakeError(str(error), stage="publish_validation") from error
    if not isinstance(svg, str) or not isinstance(image, str) or (
            data.get("candidate_digest") != _candidate_digest(svg, image, native, mode=mode, source_binding=source_binding)):
        raise AuthoredPageRemakeError("svg_page_remake_candidate_invalid", stage="publish_validation")
    from services.generation_core.native_objects import SLIDE_CONTENT_KEY as NATIVE_OBJECTS_KEY
    if local_measurement:
        native_objects = deepcopy((target.content or {}).get(NATIVE_OBJECTS_KEY))
        native_summary = deepcopy((target.content or {}).get("svgReceipt", {}).get("nativeObjects"))
    else:
        native_objects, native_summary = (_local_native_projection(svg, native, presentation, target) if local_edit
            else _native_projection(svg, native,
                presentation_canvas_metadata(presentation.theme), (target.content or {}).get("source", "")))
    if current_candidate and not local_measurement:
        if not isinstance(native, dict) or (target.content or {}).get(NATIVE_OBJECTS_KEY) != native_objects:
            raise AuthoredPageRemakeError("svg_page_remake_candidate_invalid", stage="publish_validation")
    canvas = presentation_canvas_contract(presentation.theme)
    envelope = None
    render_source = svg
    if assets is not None:
        if current_candidate:
            from services.svg_document_mutations import materialize_rebound_svg
            source_index = target.content["svgAssetManifest"].get("sourcePageIndex", index)
            render_source = await materialize_rebound_svg(session, presentation, svg, assets[0], assets[1], source_index)
        else:
            envelope, render_source = await rebind_svg_assets(session, presentation, target, svg, assets,
                page_index=index, operation_id=operation_id, attempt_token=attempt_token,
                expected_revision=expected_revision)
    from services.document_canvas import DocumentCanvasMetadata, parse_canvas_metadata
    metadata = parse_canvas_metadata(presentation_canvas_metadata(presentation.theme))
    physical_kwargs = {"physical_canvas": metadata} if isinstance(metadata, DocumentCanvasMetadata) else {}
    from services.svg_presentation_checkpoint import _required_numeric_claims
    rendered = await render_svg_with_metrics(svg, width=canvas.width_px, height=canvas.height_px,
        materialized_svg=render_source if render_source != svg else None,
        instructions=data.get("instruction"),
        **({"required_text": _required_numeric_claims((target.content or {}).get("source", ""))} if local_edit else {}),
        **({"timeline_ink_probe": True} if _v7_source_profile(presentation.theme.get("svg", {}).get("styleSnapshot", {})) else {}),
        **physical_kwargs)
    if _v7_source_profile(presentation.theme.get("svg", {}).get("styleSnapshot", {})):
        source = (target.content or {}).get("source") or "\n".join(ET.fromstring(target.svg_content).itertext())
        failure = _source_candidate_quality(rendered.quality, svg, source)
        if failure:
            raise AuthoredPageRemakeError(failure, stage="publish_validation")
    if local and (rendered.quality.get("passed") is not True
            or rendered.quality.get("measurement_complete") is not True
            or rendered.quality.get("source_digest") != source_digest(svg)):
        raise AuthoredPageRemakeError("svg_source_quality_failed", stage="publish_validation")
    if not image.endswith(f"-{sha256_hex(rendered.png)}.png"):
        raise AuthoredPageRemakeError("svg_page_remake_capture_changed", stage="publish_validation")
    if snapshot and not current_candidate:
        await version_service.snapshot_slides(session, presentation.id,
            [item.model_dump(mode="json") for item in slides],
            label=user_message(UserMessageKey.VERSION_LABEL_PAGE_REMAKE, page=index + 1), force=True)
    from services.svg_page_publication import classify_svg_page_quality
    measured_quality = classify_svg_page_quality(svg_content=svg, quality=rendered.quality,
        index=index, png_path=image, receipt=((target.content or {}).get("svgReceipt") or {}) if local else {})
    content = deepcopy(target.content or {})
    from services.generation_core.native_objects import SLIDE_CONTENT_KEY as NATIVE_OBJECTS_KEY
    receipt = dict(content.get("svgReceipt") or {})
    if not local_measurement:
        content.pop(NATIVE_OBJECTS_KEY, None)
        receipt.pop("nativeObjects", None)
        for key in ("raw_response", "raw_response_sha256", "attempts", "model_attempt_count", "provider_failures",
                    "inflight", "initial_quality", "last_valid_svg", "last_valid_quality", "terminal",
                    "repair_hint", "repair_unavailable"):
            if key != "terminal" or not local_edit:
                receipt.pop(key, None)
    from services.svg_presentation_checkpoint import _write_png
    if not local_measurement and isinstance(data.get("raw_response"), str):
        receipt["raw_response_sha256"] = sha256_hex(data["raw_response"].encode("utf-8"))
    receipt["remake"] = {"preview_task_id": str(task.id), "source_revision": data["source_revision"],
        **({"mode": mode, "source_digest": source_binding, "provider_calls": 0} if local else
           {"provider_receipt": deepcopy(data.get("provider_receipt") or {})})}
    if native_objects is not None:
        content[NATIVE_OBJECTS_KEY] = native_objects
    if native_summary is not None:
        receipt["nativeObjects"] = native_summary
    content["svgReceipt"] = receipt
    if envelope is not None:
        content["svgAssetManifest"] = envelope
    from services.svg_page_publication import set_svg_page
    fields = {}
    set_svg_page(fields, svg_content=svg, content=content, quality=measured_quality, png=rendered.png)
    fields["content"]["svgReceipt"].update(_write_png(rendered.png))
    properties = {**(target.properties or {}), "image": image}
    statement = update(SlideModel).where(
        SlideModel.id == target.id, SlideModel.presentation == presentation.id,
        SlideModel.owner_id == presentation.owner_id, SlideModel.svg_content == target.svg_content,
        _json_cas_predicate(session, SlideModel.content, target.content),
        _json_cas_predicate(session, SlideModel.properties, target.properties),
    )
    if current_candidate:
        from models.sql.presentation import PresentationModel
        statement = statement.where(SlideModel.presentation.in_(select(PresentationModel.id).where(
            PresentationModel.id == presentation.id, PresentationModel.owner_id == presentation.owner_id,
            PresentationModel.revision == expected_revision)))
    changed = await session.execute(statement.values(**fields, properties=properties)
                                    .execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        raise AuthoredPageRemakeError("svg_page_remake_source_changed", stage="publish")
    await session.refresh(target)
    theme = dict(presentation.theme)
    svg_meta = dict(theme["svg"])
    qualities = list(svg_meta.get("quality", []))
    if index < len(qualities):
        qualities[index] = measured_quality
    from services.generation_core.quality.page_outcome import review_page_indices
    review = review_page_indices(qualities)
    quality_status = "review_required" if review else "passed"
    svg_meta.update(quality=qualities, reviewPages=review, qualityStatus=quality_status)
    theme["svg"] = svg_meta
    presentation.theme = theme
    task.data = {**data, "phase": "published", "publish": {"status": "published", "rollback_available": True}}
    session.add_all([presentation, task])
    return {"status": "published", "slide_index": index, "preview_task_id": str(task.id),
            "source_format": "svg", "quality_status": quality_status}
