"""Priority-ordered bounded repair buckets for direct DOM defects."""

from __future__ import annotations

from collections.abc import Mapping
from functools import partial
import math
from typing import Any

from models.slidespec_v1 import LayoutConstraints
from services.slidespec_repair_context.artifacts import RepairArtifacts
from services.slidespec_repair_context.geometry import (
    connector_candidate as _connector_candidate,
    glyph_measurement as _glyph_measurement,
    precise_overflow_bounds as _precise_overflow_bounds,
)
from services.slidespec_repair_context.policy import (
    DEFECT_PRIORITY,
    MAX_AFFECTED_BLOCKS,
    TEXT_KINDS,
)
from services.slidespec_repair_context.state import RepairContextState


def project_base_candidates(state: RepairContextState) -> RepairArtifacts:
    source = state.source
    evidence = state.evidence
    layout_by_semantic = state.layout_by_semantic
    content_by_semantic = state.content_by_semantic
    binding_by_semantic = state.binding_by_semantic
    metric_for_semantic = state.metric_for_semantic
    finite_number = state.finite_number
    glyph_measurement = partial(_glyph_measurement, state)
    precise_overflow_bounds = partial(_precise_overflow_bounds, state)
    connector_candidate = partial(_connector_candidate, state)
    bounds: list[dict[str, Any]] = []
    fonts: list[dict[str, Any]] = []
    glyph_fits: list[dict[str, Any]] = []
    image_crops: list[dict[str, Any]] = []
    static_translations: list[dict[str, Any]] = []
    connector_routes: list[dict[str, Any]] = []
    refs: list[str] = []
    refs_by_target: dict[str, list[str]] = {}
    ordered_defects = sorted(
        evidence.defects,
        key=lambda defect: (
            DEFECT_PRIORITY.get((defect.severity, defect.code), 99),
            defect.semantic_id,
        ),
    )
    affected_targets: set[str] = set()
    for defect in ordered_defects:
        diagnostic_semantic_ids = tuple(dict.fromkeys((defect.semantic_id, *defect.related_semantic_ids)))
        repair_semantic_id = defect.semantic_id
        connector_semantic_id = next(
            (
                semantic_id
                for semantic_id in diagnostic_semantic_ids
                if (candidate_node := layout_by_semantic.get(semantic_id)) is not None
                and candidate_node.connectorRoute is not None
                and (candidate_content := content_by_semantic.get(semantic_id)) is not None
                and candidate_content.kind == "connector"
            ),
            None,
        )
        if defect.code in {"dom.unintended_overlap", "constraint.non_overlap.violated"}:
            if connector_semantic_id is None:
                continue
            repair_semantic_id = connector_semantic_id
        elif defect.code in {
            "constraint.order.violated",
            "constraint.gap.violated",
            "constraint.alignment.violated",
            "constraint.anchor.violated",
            "constraint.group.atomic_translation",
            "constraint.evidence.missing",
            "constraint.soft_regression",
        }:
            # Resizing a common ancestor can push bottom-anchored footers and
            # overlays out of the canvas, making the slide worse.  An overlap has
            # no generally safe geometry direction, so leave it to the single
            # bounded proposal path and the rendered VQA acceptance gate.
            continue
        node = layout_by_semantic.get(repair_semantic_id)
        if node is None or node.bounds is None:
            continue
        imported = binding_by_semantic.get(repair_semantic_id)
        content = content_by_semantic.get(repair_semantic_id)
        metric = metric_for_semantic(repair_semantic_id)
        if node.layoutId not in affected_targets and len(affected_targets) >= MAX_AFFECTED_BLOCKS:
            continue
        diagnostic_refs = tuple(f"{defect.code}:{semantic_id}" for semantic_id in diagnostic_semantic_ids)
        current = node.bounds.model_dump(mode="json")
        # Native SlideSpecs legitimately omit a mutation envelope.  The basic
        # deterministic cascade has always treated that as the schema default;
        # advanced imported-HTML projections still require the importer's
        # explicit allow-list through ``_mutation_allowed``.
        node_constraints = node.constraints or LayoutConstraints()
        mutation_envelope = node_constraints.model_dump(mode="json")
        if connector_semantic_id is not None and node.connectorRoute is not None:
            obstacle_metrics = tuple(
                obstacle_metric
                for semantic_id in diagnostic_semantic_ids
                if semantic_id != connector_semantic_id
                and (obstacle_metric := metric_for_semantic(semantic_id)) is not None
            )
            route, measurement = connector_candidate(node, metric, obstacle_metrics)
            connector_routes.append(
                {
                    "target": node.layoutId,
                    "currentRoute": node.connectorRoute.model_dump(mode="json"),
                    "route": route,
                    "mutationEnvelope": mutation_envelope,
                    "measurement": measurement,
                    "roundTrip": {
                        # Imported HTML still fails closed: without an exact SVG
                        # attribute binding, authored path mutation cannot prove a
                        # lossless source round trip. Native rendering is canonical.
                        "supported": source is None,
                        "verifiedProperties": ["connector-route"],
                    },
                }
            )
            affected_targets.add(node.layoutId)
            refs.extend(diagnostic_refs)
            refs_by_target.setdefault(node.layoutId, []).extend(diagnostic_refs)
            continue
        if defect.code in {"dom.off_canvas", "constraint.containment.violated"}:
            delta_x = delta_y = 0.0
            if imported is not None and source is not None:
                raw = imported.sourceBoundsPx
                width = source.viewportWidth
                height = source.viewportHeight
                if raw.x < 0:
                    delta_x = -raw.x / width
                elif raw.x + raw.width > width:
                    delta_x = (width - raw.x - raw.width) / width
                if raw.y < 0:
                    delta_y = -raw.y / height
                elif raw.y + raw.height > height:
                    delta_y = (height - raw.y - raw.height) / height
            candidate = {
                **current,
                "x": min(
                    max(0.0, current["x"] + delta_x),
                    1.0 - current["width"],
                ),
                "y": min(
                    max(0.0, current["y"] + delta_y),
                    1.0 - current["height"],
                ),
            }
        else:
            candidate = precise_overflow_bounds(imported, current)
        is_text_target = content is not None and content.kind in TEXT_KINDS
        # Offer geometry first and a small font-scale alternative for text.  The
        # cascade evaluates them as separate candidates, so it never combines both
        # changes unless a later bounded proposal explicitly justifies that scope.
        use_font = defect.code in {"dom.clipping", "dom.overflow"} and is_text_target and node.fontScale > 0.85
        added = False
        static_position = imported is not None and imported.position == "static"
        if candidate != current and not static_position:
            existing = next((item for item in bounds if item["target"] == node.layoutId), None)
            if existing is None:
                bounds.append(
                    {
                        "target": node.layoutId,
                        "currentBounds": current,
                        "bounds": candidate,
                    }
                )
            else:
                merged = dict(existing["bounds"])
                merged["x"] = candidate["x"] if candidate["x"] != current["x"] else merged["x"]
                merged["y"] = candidate["y"] if candidate["y"] != current["y"] else merged["y"]
                merged["width"] = max(merged["width"], candidate["width"])
                merged["height"] = max(merged["height"], candidate["height"])
                existing["bounds"] = merged
            added = True
        measured_glyph = (
            glyph_measurement(metric, imported) if use_font and metric is not None and imported is not None else None
        )
        if use_font and metric is not None and imported is not None:
            glyph_fits.append(
                {
                    "target": node.layoutId,
                    "currentScale": node.fontScale,
                    "minimumScale": node_constraints.minFontScale,
                    "mutationEnvelope": mutation_envelope,
                    "measurement": measured_glyph
                    or {
                        "source": "chrome",
                        "telemetryComplete": False,
                        "fontsReady": False,
                        "kind": "range-bounds",
                    },
                    "roundTrip": {
                        "supported": imported.fontSizePx is not None,
                        "verifiedProperties": ["font-size"],
                    },
                }
            )
            added = True
        elif use_font and metric is None:
            # Compatibility path for synthetic/unit contexts that have no
            # browser source. Production imported HTML always uses the measured
            # glyph candidate above and therefore fails closed on missing data.
            fonts.append(
                {
                    "target": node.layoutId,
                    "currentScale": node.fontScale,
                    "scale": max(0.85, round(node.fontScale * 0.95, 4)),
                    "minimumScale": 0.85,
                }
            )
            added = True
        if (
            defect.code in {"dom.clipping", "dom.overflow"}
            and content is not None
            and content.kind == "image"
            and imported is not None
            and imported.tag == "img"
            and metric is not None
        ):
            image = metric.get("image")
            image = image if isinstance(image, Mapping) else {}
            box = metric.get("bbox")
            box = box if isinstance(box, Mapping) else {}
            natural_width = finite_number(image.get("naturalWidth"))
            natural_height = finite_number(image.get("naturalHeight"))
            box_width = finite_number(box.get("width"))
            box_height = finite_number(box.get("height"))
            natural_ratio = natural_width / natural_height if natural_height else 0.0
            box_ratio = box_width / box_height if box_height else 0.0
            if (
                imported.objectFit == "fill"
                and natural_ratio
                and box_ratio
                and abs(math.log(natural_ratio / box_ratio)) > 0.05
            ):
                image_crops.append(
                    {
                        "target": node.layoutId,
                        "currentFit": imported.objectFit,
                        "fit": "contain",
                        "currentFocalPoint": imported.cropFocalPoint.model_dump(mode="json"),
                        "focalPoint": imported.cropFocalPoint.model_dump(mode="json"),
                        "mutationEnvelope": mutation_envelope,
                        "measurement": {
                            "source": "chrome",
                            "telemetryComplete": bool(image.get("complete") and natural_width and natural_height),
                            "decoded": bool(image.get("complete")),
                            "naturalWidthPx": natural_width,
                            "naturalHeightPx": natural_height,
                            "boxWidthPx": box_width,
                            "boxHeightPx": box_height,
                        },
                        "roundTrip": {
                            "supported": True,
                            "verifiedProperties": ["object-fit", "object-position"],
                        },
                    }
                )
                added = True
        if static_position and candidate != current and imported is not None:
            probe = metric.get("static_translation_probe") if metric is not None else {}
            probe = probe if isinstance(probe, Mapping) else {}
            parent = probe.get("parentBoundsPx")
            target = probe.get("targetBoundsPx")
            parent = parent if isinstance(parent, Mapping) else {}
            target = target if isinstance(target, Mapping) else {}
            parent_width = finite_number(parent.get("width"))
            parent_height = finite_number(parent.get("height"))
            dx = candidate["x"] - current["x"]
            dy = candidate["y"] - current["y"]
            candidate_in_parent = {
                "x": (finite_number(target.get("x")) - finite_number(parent.get("x")) + dx * source.viewportWidth)
                / parent_width
                if source is not None and parent_width > 0
                else -1,
                "y": (finite_number(target.get("y")) - finite_number(parent.get("y")) + dy * source.viewportHeight)
                / parent_height
                if source is not None and parent_height > 0
                else -1,
                "width": finite_number(target.get("width")) / parent_width if parent_width > 0 else -1,
                "height": finite_number(target.get("height")) / parent_height if parent_height > 0 else -1,
            }
            static_translations.append(
                {
                    "target": node.layoutId,
                    "currentBounds": current,
                    "delta": {"x": dx, "y": dy},
                    "mutationEnvelope": mutation_envelope,
                    "measurement": {
                        **probe,
                        "source": probe.get("source"),
                        "telemetryComplete": bool(
                            imported.staticTranslationSafe and probe.get("telemetryComplete") is True
                        ),
                        "candidateBoundsInParent": candidate_in_parent,
                    },
                    "roundTrip": {
                        "supported": imported.staticTranslationSafe,
                        "verifiedProperties": ["transform"],
                    },
                }
            )
            added = True
        if not added:
            continue
        affected_targets.add(node.layoutId)
        refs.extend(diagnostic_refs)
        refs_by_target.setdefault(node.layoutId, []).extend(diagnostic_refs)
    return RepairArtifacts(
        container_bounds=bounds,
        font_scale=fonts,
        glyph_fit=glyph_fits,
        image_crop=image_crops,
        static_translation=static_translations,
        connector_route=connector_routes,
        diagnostic_refs=refs,
        diagnostic_refs_by_target=refs_by_target,
        affected_targets=affected_targets,
    )
