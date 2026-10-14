"""Turn one author attempt's SVG into a measured, quality-scored page: the
``render_svg_with_metrics`` call, PNG write, quality-dict assembly (including
the canonical ``sourceImages`` omitted check), and validation-error handling
that decides whether the deck's attempt ``while`` loop retries or terminates.

Split out of ``services/svg_presentation_generate.py`` (P0 split, see
``docs/generation-core-architecture-20260925.md``); ``collect.py`` calls
:func:`attempt_render` once per author attempt and interprets the ``RETRY``
sentinel by continuing its own loop.
"""
from __future__ import annotations

import json
import math

from services.authored_checkpoint_errors import AuthoredStageError
from services.svg_presentation_checkpoint import _required_numeric_claims
from utils.hashing import sha256_hex

RETRY = object()


def _apply_common_header_quality(quality, svg, contract, binding, furniture_receipt):
    """Use the existing render's owned glyph ink and visible paint boxes."""
    if binding is None or not binding.get("applied"):
        return
    import xml.etree.ElementTree as ET
    from services.svg_common_header import GROUP_ID, TITLE_ID
    from services.svg_text_overlap import _ink_rect, comparison_fragments

    root = ET.fromstring(svg)
    nodes = {f"svg-{i}": node for i, node in enumerate(root.iter())}
    node_qids = {node: qid for qid, node in nodes.items()}
    parents = {child: parent for parent in root.iter() for child in parent}
    elements = (quality.get("browser_metrics") or {}).get("elements") or []
    by_qid = {item.get("qid"): item for item in elements}
    reserved = set((furniture_receipt or {}).get("element_ids") or ())
    title_box, body_box = contract["title_box"], contract["body_box"]
    failures, measured_titles = [], []
    canvas_backgrounds = 0
    tolerance = .5

    def inside(box, bounds, tolerance_px=tolerance):
        return (box["x"] >= bounds["x"] - tolerance_px and box["y"] >= bounds["y"] - tolerance_px
                and box["x"] + box["width"] <= bounds["x"] + bounds["width"] + tolerance_px
                and box["y"] + box["height"] <= bounds["y"] + bounds["height"] + tolerance_px)

    def inherited(node, attribute, default):
        current = node
        while current is not None:
            if attribute in current.attrib:
                return current.get(attribute)
            current = parents.get(current)
        return default

    def untransformed(node):
        current = node
        while current is not None:
            style = (by_qid.get(node_qids[current]) or {}).get("computed_style") or {}
            if ("transform" in current.attrib
                    or any(style.get(key, "none") != "none" for key in ("transform", "translate", "rotate", "scale"))
                    or str(style.get("zoom", "1")) not in {"1", "normal"}):
                return False
            current = parents.get(current)
        return True

    def overlaps(first, second):
        return (min(first["x"] + first["width"], second["x"] + second["width"]) > max(first["x"], second["x"])
                and min(first["y"] + first["height"], second["y"] + second["height"]) > max(first["y"], second["y"]))

    for qid, node in nodes.items():
        tag, node_id = node.tag.rsplit("}", 1)[-1], node.get("id", "")
        if tag in {"svg", "g"}:
            continue
        item = by_qid.get(qid)
        if item is None:
            failures.append({"kind": "svg_common_header_measurement_unavailable", "qid": qid})
            continue
        if node_id.startswith(TITLE_ID + "-line-"):
            measured_titles.append(node_id)
            boxes = [(_ink_rect(box), basis) for box, basis in comparison_fragments(item)]
            # Chromium's actual-size glyph ink can quantize a bearing to one
            # CSS pixel where Pillow's 1000px reference scales to a fraction.
            # Only title ink bounds get this allowance; other gates stay strict.
            if (not boxes or any(box is None or basis != "ink" or not inside(box, title_box, tolerance_px=1.0)
                                 for box, basis in boxes)
                    or item.get("glyph_clipped") or item.get("font_loaded") is not True
                    or str(item.get("resolved_font_family", "")).strip("'\"") != contract["font"]["family"]
                    or abs(float(item.get("font_size", 0)) - contract["font"]["size_px"]) > tolerance
                    or not item.get("css_visible", False) or item.get("effective_opacity", 0) < .9):
                failures.append({"kind": "svg_common_header_title_overflow", "qid": qid})
            continue
        if node_id in reserved or node_id == "svg-system-page-number":
            continue
        if not item.get("css_visible", False) or item.get("effective_opacity", 0) <= 0:
            continue
        stroke_margin_safe = False
        if item.get("has_owned_text"):
            boxes = [_ink_rect(box) for box, _basis in comparison_fragments(item)]
        elif tag != "tspan":
            box = _ink_rect(item.get("bbox"))
            # A single canvas background is independent of body reservations.
            if (tag == "rect" and box and inherited(node, "stroke", "none") == "none"
                    and abs(box["x"]) <= tolerance and abs(box["y"]) <= tolerance
                    and abs(box["width"] - contract["canvas"]["width"]) <= tolerance
                    and abs(box["height"] - contract["canvas"]["height"]) <= tolerance):
                canvas_backgrounds += 1
                earlier_body = any(previous.get("qid") in nodes
                    and nodes[previous["qid"]].get("id", "") not in reserved
                    and previous.get("tag") not in {"svg", "g"}
                    and previous.get("css_visible", False)
                    for previous in elements[:elements.index(item)])
                if canvas_backgrounds == 1 and not earlier_body:
                    continue
            stroke = inherited(node, "stroke", "none")
            fill = inherited(node, "fill", "black")
            if float(inherited(node, "stroke-opacity", "1")) <= 0:
                stroke = "none"
            if float(inherited(node, "fill-opacity", "1")) <= 0:
                fill = "none"
            if stroke == "none" and (fill == "none" or tag in {"line", "polyline"}):
                continue
            # getBoundingClientRect excludes the SVG stroke. Include its ink.
            if stroke != "none":
                if box is None:
                    raw = item.get("bbox") or {}
                    try:
                        box = {key: float(raw[key]) for key in ("x", "y", "width", "height")}
                        if not all(math.isfinite(value) for value in box.values()):
                            box = None
                    except (KeyError, TypeError, ValueError):
                        box = None
                half = float(inherited(node, "stroke-width", "1")) / 2
            if box and stroke != "none":
                layout_box = box
                box = {"x": box["x"] - half, "y": box["y"] - half,
                       "width": box["width"] + half * 2, "height": box["height"] + half * 2}
                # A centered stroke can extend into the side margin even when
                # its layout is inside the body. Keep vertical reservations and
                # canvas/brand paint checks strict; never extend text/image bounds.
                canvas_box = {"x": 0, "y": 0, **contract["canvas"]}
                stroke_margin_safe = (
                    tag in {"rect", "circle", "ellipse"} and math.isfinite(half) and half > 0
                    and untransformed(node) and inside(layout_box, body_box) and inside(box, canvas_box)
                    and box["y"] >= body_box["y"] - tolerance
                    and box["y"] + box["height"] <= body_box["y"] + body_box["height"] + tolerance
                    and all(not overlaps(box, reservation) for reservation in contract.get("brand_reservations", [])))
            boxes = [box]
        else:
            continue
        if not boxes or any(box is None for box in boxes):
            failures.append({"kind": "svg_common_header_measurement_unavailable", "qid": qid})
        elif any(not inside(box, body_box) for box in boxes) and not stroke_margin_safe:
            text = "".join(str(item.get("owned_text") or "").split())
            duplicate = (text and text == "".join(binding["title"].split())
                         and any(box["y"] < body_box["y"] for box in boxes))
            failures.append({"kind": "svg_common_header_duplicate" if duplicate
                             else "svg_common_header_body_intrusion", "qid": qid})
    if len(measured_titles) != len(binding["lines"]):
        failures.append({"kind": "svg_common_header_digest_mismatch"})
    quality["common_header"] = {"version": "svg-common-header-render/v1",
        "origin": "generated-common-header", "header_digest": contract["digest"],
        "title_digest": binding["title_digest"], "page_digest": binding["digest"],
        "title_element_ids": measured_titles, "body_box": body_box,
        "basis": "existing_browser_glyph_ink_and_paint", "passed": not failures}
    if failures:
        quality["passed"] = False
        quality["geometry_passed"] = False
        quality.setdefault("failures", []).extend(failures)


async def attempt_render(*, ctx, request, presentation_id, index, content, payload, svg, ordinal,
                         job, checkpoint, async_session_maker, write_png,
                         recovery_readability_policy=None, allow_existing_common_header=False):
    from services.svg_contract import finalize_svg
    from services.svg_renderer import render_svg_with_metrics
    from services.svg_source_layout import verify_source_receipt
    from services.svg_authoring_profile import read_authoring_profile, read_quality_recovery_policy
    from services.generation_core.drivers.svg.timeline_ink_guard import (
        apply_timeline_ink_quality, source_timeline_rows,
    )

    profile = read_authoring_profile(ctx.snapshot)
    header_pages = getattr(ctx, "header_pages", None)
    header_page = header_pages[index] if header_pages else None
    readability_policy = recovery_readability_policy or ctx.snapshot.get("readability_policy")
    if payload.get("cached_author_render"):
        from services.document_canvas_generation import validate_svg_readability_policy
        if (recovery_readability_policy is None
                or validate_svg_readability_policy(recovery_readability_policy).get("version")
                != "svg-physical-readability/v2"
                or recovery_readability_policy.get("quality_target_font_pt", 0) < 9.5):
            raise AuthoredStageError(slide_index=index, stage="render",
                                     code="svg_cached_author_render_quality_policy_invalid",
                                     retryable=False)
    if readability_policy is not None:
        payload["readability_policy"] = readability_policy
    cache_remeasurement = payload.get("cache_only_remeasurement")
    timeline_source = content if profile and profile["version"] in (5, 6, 7, 8) and source_timeline_rows(content) else None

    try:
        if payload["attempts"][-1]["receipt"].get("validation_error"):
            raise ValueError(payload["attempts"][-1]["receipt"]["validation_error"])
        if ctx.source_reflow or ctx.raster_reconstruction:
            final = svg
        else:
            finalize_source_manifest = ({slot: {"required": item["policy"]["required"],
                    "allowOmit": item["policy"]["allowOmit"]}
                    for slot, item in ctx.manifests[index].items()}
                if ctx.canonical_assets else None)
            allow_image_only = bool(
                ctx.raster_reconstruction and ctx.raster_analysis is not None and ctx.raster_pin is not None
                and ctx.raster_analysis.pages[index].render_mode == 'full_page_preserve'
                and ctx.raster_analysis.pages[index].page_number in ctx.raster_pin.full_page_preserve_pages)
            final = finalize_svg(svg, index=index, n=ctx.count, width=ctx.canvas.width_px,
                                 height=ctx.canvas.height_px, style_snapshot=ctx.snapshot,
                                 source_manifest=finalize_source_manifest,
                                 allow_image_only=allow_image_only, header_page=header_page,
                                 allow_existing_common_header=bool(cache_remeasurement
                                     or payload.get("cached_author_render") or allow_existing_common_header))
        if cache_remeasurement and sha256_hex(final.encode()) != cache_remeasurement.get("source_sha256"):
            raise AuthoredStageError(slide_index=index, stage="render", code="svg_cache_remeasurement_changed", retryable=False)
        if ctx.source_reflow:
            verify_source_receipt(final, payload.get("source_preservation"), ctx.source_document, index)
        from services.svg_master_furniture import master_furniture_receipt
        furniture_contract = ({slot: {"required": item["policy"]["required"], "allowOmit": item["policy"]["allowOmit"]}
                               for slot, item in ctx.manifests[index].items()} if ctx.canonical_assets else None)
        preserved_raster = bool(ctx.raster_reconstruction and index + 1 in ctx.raster_pin.full_page_preserve_pages)
        if ctx.raster_reconstruction and not preserved_raster:
            import xml.etree.ElementTree as ET
            furniture_contract = {node.get("href"): {"required": True, "allowOmit": False}
                                  for node in ET.fromstring(final).iter()
                                  if node.tag.rsplit("}", 1)[-1] == "image" and node.get("href", "").startswith("source-slot-")}
        if ctx.source_reflow:
            furniture_contract = {binding["slot_id"]: {"required": True} for binding in payload["source_preservation"]["bindings"]
                                  if binding.get("kind") == "image"}
        furniture_receipt = (None if preserved_raster else master_furniture_receipt(final, ctx.snapshot,
            index=index, n=ctx.count, width=ctx.canvas.width_px, height=ctx.canvas.height_px,
            source_manifest=furniture_contract, header_page=header_page))
        if furniture_receipt is not None:
            payload["system_furniture"] = furniture_receipt
        materialized = None
        if ctx.raster_reconstruction:
            from services.artifact_store import configured_public_artifact_service
            from services.raster_source_assets import prepare_raster_page_assets
            from services.svg_generation_assets import publication_context

            async with async_session_maker() as asset_session:
                raster_asset_context, raster_asset_revision = await publication_context(asset_session, job)
                raster_assets = await prepare_raster_page_assets(
                    asset_session, configured_public_artifact_service(),
                    approval_pin=ctx.raster_pin,
                    owner_id=raster_asset_context.owner_id,
                    tenant_id=raster_asset_context.tenant_id,
                    publication_context=raster_asset_context,
                    presentation_id=presentation_id,
                    source_revision=raster_asset_revision,
                    slide_revision=raster_asset_revision,
                    page_number=index + 1,
                    output_page_index=index,
                    canonical_svg=final,
                    width=ctx.canvas.width_px,
                    height=ctx.canvas.height_px,
                    **({"system_furniture": furniture_receipt} if furniture_receipt is not None else {}),
                )
                await asset_session.commit()
            payload["svgAssetManifest"] = raster_assets.screen_envelope
            payload["rasterCropLineage"] = list(raster_assets.crop_lineage)
            payload["rasterAnalysisDigest"] = ctx.raster_pin.analysis_digest
            payload["rasterApprovalPin"] = ctx.raster_pin.model_dump(mode="json", by_alias=True)
            payload["rasterSourcePageNumber"] = index + 1
            materialized = raster_assets.materialized_svg
        if ctx.canonical_assets:
            from services.svg_generation_assets import screen_placement
            # Candidate SVG is durable before image rendering. A
            # derivative failure resumes here without rebuying it.
            payload["svg_content"] = final
            await checkpoint(job, index, payload=payload)
            try:
                async with async_session_maker() as asset_session:
                    envelope, materialized = await screen_placement(asset_session, ctx.service,
                        context=ctx.asset_context, revision=ctx.asset_revision, manifest=ctx.source_manifest,
                        source_pointer=ctx.source_pointer, svg=final, page_index=index,
                        candidates=ctx.planned[index], width=ctx.canvas.width_px, height=ctx.canvas.height_px,
                        saved_envelope=payload.get("svgAssetManifest"),
                        **({"system_furniture": furniture_receipt} if furniture_receipt is not None else {}))
                    await asset_session.commit()
                payload["svgAssetManifest"] = envelope
                await checkpoint(job, index, payload=payload)
            except (ValueError, OSError) as error:
                raise AuthoredStageError(slide_index=index, stage="render",
                    code="svg_asset_screen_derivative_failed", retryable=False) from error
        try:
            required_text = (
                [binding["text"] for binding in payload["source_preservation"]["bindings"]
                 if binding.get("kind") in {"text", "table_cell"} and binding.get("text", "").strip()]
                if ctx.source_reflow else _required_numeric_claims(
                    content, quality_policy=read_quality_recovery_policy(ctx.snapshot))
            )
            font_profile = ctx.source_font_profile if ctx.source_reflow else None
            raster_authorized_measurement = False
            if ctx.raster_reconstruction:
                from services.raster_redesign_generation import raster_measurement_inputs
                required_text, raster_authorized_measurement, font_profile = raster_measurement_inputs(
                    ctx.raster_analysis, ctx.raster_pin, index + 1, final)
            async with ctx.render_semaphore:
                source_font_kwargs = {}
                if ctx.source_reflow:
                    from services.document_canvas_generation import source_restyle_font_floor

                    source_font_kwargs["minimum_font_px"] = source_restyle_font_floor(
                        request.instructions, getattr(request, "_document_canvas", None)
                    )
                    if readability_policy is not None:
                        from services.document_canvas_generation import svg_quality_font_floor

                        source_font_kwargs["minimum_font_px"] = max(source_font_kwargs["minimum_font_px"],
                            svg_quality_font_floor(readability_policy))
                rendered = await render_svg_with_metrics(final, width=ctx.canvas.width_px,
                                                     height=ctx.canvas.height_px,
                                                     # Source instructions are resolved into the hard
                                                     # minimum above, including the 7px lower bound.
                                                     instructions=None if ctx.source_reflow else request.instructions,
                                                     **source_font_kwargs,
                                                     **({"readability_policy": readability_policy} if readability_policy is not None else {}),
                                                     required_text=required_text,
                                                     **({"allow_image_only": True}
                                                        if raster_authorized_measurement else {}),
                                                     **({"source_font_profile": font_profile}
                                                        if font_profile else {}),
                                                     **({"system_furniture": furniture_receipt} if furniture_receipt is not None else {}),
                                                      **({"timeline_ink_probe": True} if timeline_source or profile and profile["version"] in (7, 8) else {}),
                                                     materialized_svg=materialized, **ctx.physical_kwargs)
        except ValueError as error:
            if ctx.canonical_assets:
                raise AuthoredStageError(slide_index=index, stage="render",
                    code="svg_asset_capture_failed", retryable=False) from error
            raise
    except ValueError as error:
        hint = str(error)[:600]
        payload["repair_hint"] = hint
        payload["attempts"][-1]["validation_error"] = hint
        if ordinal == 0:
            payload["initial_quality"] = {
                "stage": "first_response", "passed": False, "verified": False,
                "failures": [{"kind": "validation_failed", "detail": hint}],
            }
        payload["svg_content"] = None
        await checkpoint(job, index, payload=payload)
        if ordinal == 1 or (ctx.raster_reconstruction and payload.get("rasterCompositionMode") != "free-svg"):
            code = str(error).split(":", 1)[0]
            source_plan_failure = (code == "svg_source_classification_missing"
                                   or ctx.source_reflow and code.startswith("svg_source_"))
            raise AuthoredStageError(slide_index=index,
                stage="author" if source_plan_failure else "render",
                code=code if source_plan_failure or code.startswith("svg_common_header_") else "svg_validation_failed",
                retryable=False) from error
        return RETRY
    if cache_remeasurement and (
            sha256_hex(rendered.png) != cache_remeasurement.get("png_sha256")
            or str(rendered.quality.get("source_digest", "")).removeprefix("sha256:") != cache_remeasurement.get("source_sha256")
            or str(rendered.quality.get("png_digest", "")).removeprefix("sha256:") != cache_remeasurement.get("png_sha256")
            or rendered.quality.get("measurement_complete") is not True):
        raise AuthoredStageError(slide_index=index, stage="render", code="svg_cache_remeasurement_changed", retryable=False)
    _apply_common_header_quality(rendered.quality, final, ctx.snapshot.get("common_header"),
                                 header_page, furniture_receipt)
    if not (ctx.source_reflow or ctx.raster_reconstruction):
        from services.authored_canvas import request_canvas_metadata
        from services.generation_core.drivers.svg.native_publish import canvas_point_per_px
        from services.generation_core.drivers.svg.native_requirements import apply_requested_native_quality
        source_pages = getattr(request, "slides_markdown", None)
        native_source = source_pages[index] if source_pages and len(source_pages) == ctx.count else content
        apply_requested_native_quality(rendered.quality, payload, final, native_source,
            pt_per_px=canvas_point_per_px(request_canvas_metadata(request)))
    elif ctx.source_reflow and payload.get("source_preservation", {}).get("whole_page_native_groups"):
        from services.authored_canvas import request_canvas_metadata
        from services.generation_core.drivers.svg.native_publish import canvas_point_per_px, page_native_objects

        from services.svg_cached_author_render_recovery import approved_sidecars
        objects, summary = page_native_objects(
            payload, pt_per_px=canvas_point_per_px(request_canvas_metadata(request)),
            verified_fixed_axis_migrations=approved_sidecars(payload))
        required = {item["element_id"] for item in payload["source_preservation"]["whole_page_native_groups"]}
        actual = {item["id"] for item in (objects or {}).get("objects", [])}
        if not required.issubset(actual):
            rendered.quality["passed"] = False
            rendered.quality.setdefault("failures", []).append({"kind": "source_native_chart_unprojectable",
                "missing_count": len(required - actual), "native_summary": summary})
    if timeline_source:
        apply_timeline_ink_quality(rendered.quality, final, timeline_source,
                                  **({"allow_unlabeled": True} if profile["version"] in (6, 7, 8) else {}))
    if profile and profile["version"] in (7, 8):
        from services.generation_core.drivers.svg.source_classification_association import (
            apply_source_classification_association,
        )
        apply_source_classification_association(rendered.quality, final, content)
        from services.generation_core.drivers.svg.source_paint_occlusion import apply_source_paint_occlusion
        apply_source_paint_occlusion(rendered.quality, final)
    writing_hint = None
    if getattr(request, "writing_policy", None) is not None:
        from services.ppt_writing_visible import (
            assess_visible_nodes, literal_source_values, svg_text_nodes,
        )

        preservation = payload.get("source_preservation") or {}
        source_values = literal_source_values(
            getattr(request, "content", None),
            payload.get("nativeRequirements"),
            preservation.get("bindings") or (),
            payload.get("rasterContentLedger"),
        )
        writing = assess_visible_nodes(svg_text_nodes(final), source_values=source_values)
        rendered.quality["writing_policy"] = writing
        if writing["hard_violation_counts"]:
            rendered.quality["passed"] = False
            rendered.quality.setdefault("failures", []).append({
                "kind": "writing_policy_visible_symbols",
                "counts": writing["hard_violation_counts"],
            })
            writing_hint = json.dumps({
                "kind": "writing_policy_visible_symbols",
                "counts": writing["hard_violation_counts"],
                "instruction": "Revise only newly written visible wording that uses decorative symbols. Preserve all source-backed text, native data, numbers, formulas, official names and quotations exactly. Keep facts and slide layout intact.",
            }, ensure_ascii=False, separators=(",", ":"))
    payload["attempts"][-1]["quality"] = rendered.quality
    if payload.get("fontAdvisories"):
        rendered.quality.setdefault("advisories", []).extend(payload["fontAdvisories"])
    if ordinal == 0 and (readability_policy is None or "initial_quality" not in payload):
        payload["initial_quality"] = {
            **rendered.quality, "stage": "first_response",
        }
    payload["last_valid_svg"] = final
    if (readability_policy is not None and rendered.quality.get("passed") is False
            and not payload.get("local_readability_repair") and not (header_page and header_page.get("applied"))):
        from services.document_canvas_generation import minimal_svg_readability_repair
        from services.generation_core.drivers.svg.native_declarations import PAYLOAD_KEY

        native = payload.get(PAYLOAD_KEY) or {}
        if native.get("objects"):
            local = {"status": "page_recomposition_required", "reason": "native_data_requires_recomposition",
                     "provider_call_authorized": False}
        else:
            local = minimal_svg_readability_repair(final, rendered.quality)
        candidate = local.pop("svg", None)
        payload["local_readability_repair"] = local
        if candidate is not None:
            if ctx.source_reflow:
                from services.svg_contract import source_digest

                # Existing exact text/image/native-table verification remains
                # authoritative after a local typography-only candidate.
                payload["source_preservation"] = {**payload["source_preservation"],
                    "svg_digest": source_digest(candidate)}
            payload["svg_content"] = candidate
            await checkpoint(job, index, payload=payload)
            result = await attempt_render(ctx=ctx, request=request, presentation_id=presentation_id,
                index=index, content=content, payload=payload, svg=candidate, ordinal=ordinal,
                job=job, checkpoint=checkpoint, async_session_maker=async_session_maker, write_png=write_png)
            local["remeasured"] = True
            local["remeasurement_passed"] = bool(payload.get("quality", {}).get("passed")) if result is not RETRY else False
            await checkpoint(job, index, payload=payload)
            return result
    if rendered.quality.get("passed") is False and ordinal == 0 and not payload.get("repair_unavailable"):
        timeline_collisions = rendered.quality.get("timeline_caption_ink", {}).get("collisions")
        classification = rendered.quality.get("source_classification_association", {})
        paint_occlusion = rendered.quality.get("source_paint_occlusion", {})
        if paint_occlusion.get("status") in {"occluded", "unproven"}:
            from services.generation_core.drivers.svg.source_paint_occlusion import source_paint_repair_hint

            hint = source_paint_repair_hint(rendered.quality, content)
        elif classification.get("status") == "unproven":
            from services.generation_core.drivers.svg.source_classification_association import source_classifications

            geometry_failures = [failure for failure in rendered.quality.get("failures", [])
                                 if failure.get("kind") != "source_classification_unproven"]
            floor = rendered.quality.get("body_font_floor_px", 0)
            floor = floor if isinstance(floor, (int, float)) and math.isfinite(floor) and floor > 0 else 0
            hint = json.dumps({"kind": "svg_source_classification_missing",
                "instruction": "Preserve every original entity, relation and sentence. Preserve each required class label literally, including every word, and every member; do not shorten or alias labels. Make membership visible with complete lists, dedicated enclosures, or a solid-color legend matching each member. Keep all required text visible without clipping or shrinking.",
                "classes": classification.get("unproven_labels", []),
                "required_classes": source_classifications(content),
                "geometry": {"failure_counts": {kind: sum(failure.get("kind") == kind for failure in geometry_failures)
                    for kind in sorted({str(failure.get("kind")) for failure in geometry_failures})},
                    "minimum_body_px": floor, "safe_body_px": math.ceil(floor),
                    "sample_qids": list(dict.fromkeys(failure["qid"] for failure in geometry_failures
                        if failure.get("qid")))[:4]},
                "geometry_instruction": "Resolve all geometry failures; keep body text at least safe_body_px."}, ensure_ascii=False, separators=(",", ":"))
        elif timeline_collisions:
            hint = json.dumps({"kind": "timeline_caption_bar_ink_overlap",
                "instruction": "Preserve every source date, duration and dependency. Wrap the task caption in its explanation area and adjust the explanation and bars together until all caption glyphs are clear of the bars. Do not hide, clip or shrink required text.",
                "witnesses": timeline_collisions[:3]}, ensure_ascii=False)[:3000]
        elif readability_policy is not None:
            hint = json.dumps({"kind": "svg_readability_repair", "failures": rendered.quality.get("failures", []),
                "physical_page": {key: readability_policy[key] for key in ("purpose", "width_emu", "height_emu", "minimum_font_pt")},
                **({"quality_target_font_pt": readability_policy["quality_target_font_pt"]}
                   if "quality_target_font_pt" in readability_policy else {}),
                "local_repair": payload.get("local_readability_repair"),
                "instruction": "Repair only the smallest failing region, preserving every word, number, unit, relation and page count. Enlarge space or reflow related objects together. Never shrink fonts, hide or delete content. If local repair cannot fit, freely recompose this same page on the exact canvas; if still impossible, fail with physical_space_insufficient."}, ensure_ascii=False)[:3000]
        else:
            hint = json.dumps(rendered.quality, ensure_ascii=False)[:3000]
        if writing_hint:
            hint = (writing_hint + "\n" + hint)[:3000]
        payload["repair_hint"] = hint
        payload["svg_content"] = None
        await checkpoint(job, index, payload=payload)
        return RETRY
    if payload.get("repair_unavailable") and not ctx.raster_reconstruction:
        rendered.quality["passed"] = False
        rendered.quality.setdefault("failures", []).append({"kind": "provider_repair_unavailable"})
    if ctx.canonical_assets:
        import xml.etree.ElementTree as ET
        used = {node.get("href") for node in ET.fromstring(final).iter()
                if node.tag.rsplit("}", 1)[-1] == "image" and node.get("href") in ctx.manifests[index]}
        omitted = sorted(set(ctx.manifests[index]) - used)
        rendered.quality["sourceImages"] = {
            "offered": sorted(ctx.manifests[index]), "used": sorted(used), "omitted": omitted,
        }
        required_omitted = []
        for slot in omitted:
            item = ctx.manifests[index][slot]
            policy = item.get("policy") if isinstance(item, dict) else None
            # Only an explicit optional policy permits omission. Missing or
            # malformed flags retain the source-preservation failure boundary.
            if (not isinstance(policy, dict) or policy.get("required") is not False
                    or policy.get("allowOmit") is not True):
                required_omitted.append(slot)
        if required_omitted:
            rendered.quality["passed"] = False
            rendered.quality.setdefault("failures", []).append({
                "kind": "source_image_omitted", "slotIds": required_omitted,
            })
    from services.generation_core.drivers.svg.embed_assets import apply_vector_icon_quality
    apply_vector_icon_quality(rendered.quality, payload)
    payload.update(svg_content=final, source_digest=sha256_hex(final.encode()),
                   quality=rendered.quality, **write_png(rendered.png))
    header_failures = [failure for failure in rendered.quality.get("failures", [])
                       if str(failure.get("kind", "")).startswith("svg_common_header_")]
    if header_failures:
        await checkpoint(job, index, payload=payload)
        raise AuthoredStageError(slide_index=index, stage="render",
                                code=header_failures[0]["kind"], retryable=False)
    if cache_remeasurement:
        from services.svg_classification_remeasurement import remeasurement_digest
        cache_remeasurement.update(status="completed" if rendered.quality.get("passed") is True else "failed",
            remeasured_metrics_digest=remeasurement_digest(rendered.quality.get("browser_metrics")))
    if rendered.quality.get("source_paint_occlusion", {}).get("status") in {"occluded", "unproven"}:
        status = rendered.quality["source_paint_occlusion"]["status"]
        await checkpoint(job, index, payload=payload)
        raise AuthoredStageError(slide_index=index, stage="render",
            code="svg_source_paint_occluded" if status == "occluded" else "svg_source_paint_unproven", retryable=False)
    if rendered.quality.get("source_classification_association", {}).get("status") == "unproven":
        await checkpoint(job, index, payload=payload)
        raise AuthoredStageError(slide_index=index, stage="author",
            code="svg_source_classification_missing", retryable=False)
    if timeline_source and rendered.quality.get("timeline_caption_ink", {}).get("collisions"):
        # The first failure already took the bounded repair path above. Persist
        # the second measured witness, then reject this candidate page.
        await checkpoint(job, index, payload=payload)
        raise AuthoredStageError(slide_index=index, stage="render",
            code="svg_timeline_caption_ink_failed", retryable=False)
    if profile and profile["version"] in (7, 8) and rendered.quality.get("passed") is not True:
        await checkpoint(job, index, payload=payload)
        raise AuthoredStageError(slide_index=index, stage="render",
            code="svg_source_quality_failed", retryable=False)
    if ctx.source_reflow:
        if rendered.quality.get("passed") is not True:
            raise AuthoredStageError(slide_index=index, stage="render",
                code="svg_source_quality_failed", retryable=False)
        rendered.quality.update(verified=True, semantic_review="source_preservation_verified")
        payload["source_preservation"]["rendering_verified"] = True
    await checkpoint(job, index, payload=payload)
    return payload, None if ctx.source_reflow else rendered.png
