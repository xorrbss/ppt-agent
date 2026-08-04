"""Production adapter from authored quality review to the canonical SlideSpec path.

Only slides with a verified active artifact pointer are claimed.  The authored
review orchestrator first runs the lossless importer for eligible slides; anything
that fails that boundary remains a safe mixed-deck legacy owner.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass
import logging
import math
import re
import time
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.slidespec_v1 import (
    AcceptanceDecision,
    LayoutConstraints,
    LayoutNode,
    RenderRequest,
    RepairPlan,
    SlideSpec,
)
from models.sql.slide import SlideModel
from models.sql.slidespec_artifact_bundle import SlideSpecArtifactBundle
from services import presentation_version_service as version_service
from services.authored_quality_acceptance import (
    local_acceptance_decision,
    style_comparison_improvement_decision,
    style_comparison_quality_gain,
    style_comparison_quality_interval,
    style_comparison_has_regression,
)
from services.slidespec_acceptance import AcceptanceEvidence, GateEvidence, decide_acceptance
from services.slidespec_artifact_store import (
    ACTIVE_POINTER_KEY,
    SlideSpecArtifactError,
    active_pointer,
    build_slidespec_artifact_bundle,
    load_active_slidespec_bundle,
    publish_slidespec_bundle_cas,
    record_publication_attempt,
    rollback_active_slide_cas,
)
from services.slidespec_repair import (
    build_deterministic_cascade_report,
    realized_repair_cost,
)
from services.slidespec_repair_pipeline import (
    DomDefect,
    DomEvidence,
    PublicationReceipt,
    RenderedCandidate,
    SensorResult,
    SlideSpecInput,
    SlideSpecRepairDependencies,
    SlideSpecRepairPipeline,
)
from services.slidespec_rollout_policy import resolve_slidespec_repair_rollout
from utils.authored_dom_quality import (
    DomQualityReport,
    has_dom_quality_regression,
    inspect_authored_dom,
    normalize_browser_metrics,
)
from utils.authored_ocr_quality import (
    OcrQualityResult,
    compare_expected_candidate,
    inspect_authored_ocr,
    normalize_ocr_text,
)
from utils.llm_calls.author_vision_qa import critique_authored_style_comparison
from utils.llm_calls.propose_slidespec_repair import propose_slidespec_repair
from utils.slide_capture import render_html_to_png_with_metrics
from utils.slidespec_constraints import (
    ConstraintReport,
    constraint_targets,
    evaluate_constraints,
    has_soft_constraint_regression,
    imported_projection_roundtrip_errors,
    observations_from_browser_metrics,
)
from utils.slidespec_core import SlideSpecError, apply_repair_plan, render_slidespec, validate_slidespec
from utils.slidespec_solver import build_constraint_repair_plan, solve_constraints


LOGGER = logging.getLogger(__name__)

_DOM_CODE = {
    "clipping": "dom.clipping",
    "off_canvas": "dom.off_canvas",
    "overflow": "dom.overflow",
    "overlap": "dom.unintended_overlap",
}

_DEFECT_PRIORITY = {
    ("high", "dom.clipping"): 0,
    ("high", "dom.overflow"): 1,
    ("high", "dom.unintended_overlap"): 2,
    ("high", "dom.off_canvas"): 3,
    ("medium", "dom.unintended_overlap"): 4,
    ("medium", "dom.clipping"): 5,
    ("medium", "dom.overflow"): 6,
    ("medium", "dom.off_canvas"): 7,
    ("high", "constraint.containment.violated"): 0,
    ("high", "constraint.readability.violated"): 1,
}
_TEXT_KINDS = frozenset({"title", "text", "list", "metric", "table"})


@dataclass(frozen=True, slots=True)
class CanonicalReviewExecution:
    claimed_positions: tuple[int, ...]
    data: dict[str, Any]


@dataclass(slots=True)
class _CandidateState:
    request: RenderRequest
    image: bytes
    dom: DomQualityReport
    plan: RepairPlan
    constraints: ConstraintReport
    decision: AcceptanceDecision | None = None
    ocr_non_regression: bool = False
    token_recall: float = 0.0
    vqa_non_regression: bool = False
    vqa_improved: bool = False


def _render_request(bundle: Any) -> RenderRequest:
    manifest = bundle.manifest if isinstance(bundle.manifest, Mapping) else {}
    return RenderRequest.model_validate(
        {
            "spec": bundle.spec,
            "renderer": manifest.get("renderer"),
            "fonts": manifest.get("fonts"),
            "assets": manifest.get("assets"),
            "source": manifest.get("source"),
        }
    )


def _semantic_text(spec: SlideSpec) -> dict[str, str]:
    return {
        node.semanticId: node.text
        for node in sorted(spec.content, key=lambda item: item.readingOrder)
        if node.text
    }


def _expected_text(spec: SlideSpec) -> str:
    return "\n".join(_semantic_text(spec).values())


def _important_tokens(spec: SlideSpec) -> tuple[str, ...]:
    values = [str(token.value) for token in spec.tokens if token.important]
    by_name = {token.name: str(token.value) for token in spec.tokens}
    for node in spec.content:
        values.extend(by_name[name] for name in node.importantTokens if name in by_name)
    return tuple(dict.fromkeys(value for value in values if value))


def _walk_layout(node: LayoutNode) -> Sequence[LayoutNode]:
    result = [node]
    for child in node.children:
        result.extend(_walk_layout(child))
    return result


def _qid_to_semantic(request: RenderRequest) -> dict[str, str]:
    rendered = render_slidespec(request)
    return {entry.qid: entry.semanticId for entry in rendered.semanticDom}


def _dom_evidence(
    report: DomQualityReport,
    qids: Mapping[str, str],
    *,
    diagnostic_refs: Sequence[str] | None = None,
) -> DomEvidence:
    allowed = frozenset(diagnostic_refs) if diagnostic_refs is not None else None
    # One browser issue can be repeated through nested DOM nodes.  Keep a single,
    # strongest defect per canonical target so noisy authored markup cannot create
    # an over-budget repair plan.
    ranked = {"medium": 1, "high": 2}
    defects_by_key: dict[tuple[str, str], DomDefect] = {}
    for issue in report.issues:
        code = _DOM_CODE.get(issue.category)
        if code is None or issue.severity not in {"high", "medium"}:
            continue
        semantic_ids = tuple(dict.fromkeys(qids[qid] for qid in issue.qids if qid in qids))
        if allowed is not None:
            semantic_ids = tuple(
                semantic_id
                for semantic_id in semantic_ids
                if f"{code}:{semantic_id}" in allowed
            )
        if code == "dom.unintended_overlap" and semantic_ids:
            semantic_id, *related = semantic_ids
            key = (code, semantic_id)
            current = defects_by_key.get(key)
            candidate = DomDefect(
                code,
                issue.severity,
                semantic_id,
                tuple(related),
            )
            if current is None or ranked[issue.severity] > ranked[current.severity]:
                defects_by_key[key] = candidate
            continue
        for semantic_id in semantic_ids:
            key = (code, semantic_id)
            current = defects_by_key.get(key)
            if current is None or ranked[issue.severity] > ranked[current.severity]:
                defects_by_key[key] = DomDefect(code, issue.severity, semantic_id)
    return DomEvidence(
        defects=tuple(defects_by_key.values()),
        telemetry_complete=report.telemetry_status == "available",
    )


def _constraint_evidence(
    request: RenderRequest,
    report: ConstraintReport,
) -> DomEvidence:
    """Convert hard relational failures into the pipeline's cheap DOM gate."""

    semantic_by_layout = {
        node.layoutId: node.contentSemanticId
        for node in _walk_layout(request.spec.layout)
        if node.contentSemanticId is not None
    }
    fallback_semantic = next(iter(_semantic_text(request.spec)), request.spec.slideId)
    defects: list[DomDefect] = []
    for violation in report.violations:
        if violation.strength != "hard":
            continue
        semantic_ids = tuple(
            dict.fromkeys(
                semantic_by_layout[target]
                for target in violation.targets
                if target in semantic_by_layout
            )
        )
        semantic_id = semantic_ids[0] if semantic_ids else fallback_semantic
        defects.append(
            DomDefect(
                violation.code,
                "high",
                semantic_id,
                semantic_ids[1:],
            )
        )
    for constraint_id in report.unknown_hard:
        defects.append(
            DomDefect(
                "constraint.evidence.missing",
                "high",
                fallback_semantic,
                (constraint_id,),
            )
        )
    return DomEvidence(tuple(defects), telemetry_complete=not report.unknown_hard)


def _soft_constraint_regression_evidence(
    request: RenderRequest,
    baseline: ConstraintReport,
    candidate: ConstraintReport,
) -> DomEvidence:
    """Represent weighted soft-constraint regression as a cheap hard gate."""

    if not has_soft_constraint_regression(baseline, candidate):
        return DomEvidence()
    semantic_by_layout = {
        node.layoutId: node.contentSemanticId
        for node in _walk_layout(request.spec.layout)
        if node.contentSemanticId is not None
    }
    fallback_semantic = next(iter(_semantic_text(request.spec)), request.spec.slideId)
    regressed = sorted(
        (
            violation
            for violation in candidate.violations
            if violation.strength == "soft"
        ),
        key=lambda item: (-item.weighted_cost, item.constraint_id),
    )
    semantic_ids = tuple(
        dict.fromkeys(
            semantic_by_layout[target]
            for violation in regressed
            for target in violation.targets
            if target in semantic_by_layout
        )
    )
    return DomEvidence(
        (
            DomDefect(
                "constraint.soft_regression",
                "high",
                semantic_ids[0] if semantic_ids else fallback_semantic,
                tuple(item.constraint_id for item in regressed[:8]),
            ),
        )
    )


def _merge_dom_evidence(*items: DomEvidence) -> DomEvidence:
    defects: dict[tuple[str, str, tuple[str, ...]], DomDefect] = {}
    for item in items:
        for defect in item.defects:
            defects[(defect.code, defect.semantic_id, defect.related_semantic_ids)] = defect
    return DomEvidence(
        defects=tuple(defects.values()),
        telemetry_complete=all(item.telemetry_complete for item in items),
    )


def _constraint_report_payload(report: ConstraintReport) -> dict[str, Any]:
    return {
        **asdict(report),
        "hard_failure": report.hard_failure,
        "soft_cost": report.soft_cost,
    }


def _repair_context(
    request: RenderRequest | SlideSpec,
    evidence: DomEvidence,
    browser_metrics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    spec = request.spec if isinstance(request, RenderRequest) else request
    source = request.source if isinstance(request, RenderRequest) else None
    layout_by_semantic = {
        node.contentSemanticId: node
        for node in _walk_layout(spec.layout)
        if node.contentSemanticId is not None
    }
    content_by_semantic = {node.semanticId: node for node in spec.content}
    binding_by_semantic = {
        binding.semanticId: binding for binding in (source.bindings if source else ())
    }
    normalized_metrics = normalize_browser_metrics(browser_metrics)
    metric_by_qid = {
        str(item.get("qid")): item for item in normalized_metrics.get("elements", ())
    }
    native_qid_by_semantic: dict[str, str] = {}
    if source is None and isinstance(request, RenderRequest):
        native_qid_by_semantic = {
            entry.semanticId: entry.qid
            for entry in render_slidespec(request).semanticDom
        }

    def metric_for_semantic(semantic_id: str) -> Mapping[str, Any] | None:
        imported = binding_by_semantic.get(semantic_id)
        qid = imported.qid if imported is not None else native_qid_by_semantic.get(semantic_id)
        metric = metric_by_qid.get(qid) if qid else None
        return metric if isinstance(metric, Mapping) else None

    def finite_number(value: object, default: float = 0.0) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) else default

    def glyph_measurement(
        metric: Mapping[str, Any], imported: Any
    ) -> dict[str, Any] | None:
        fragments = metric.get("text_fragments")
        if not isinstance(fragments, Sequence) or isinstance(fragments, (str, bytes)):
            return None
        rects = [item for item in fragments if isinstance(item, Mapping)]
        if not rects:
            return None
        ys = [finite_number(rect.get("y")) for rect in rects]
        bottoms = [y + finite_number(rect.get("height")) for y, rect in zip(ys, rects)]
        measured_width = max(
            max((finite_number(rect.get("width")) for rect in rects), default=0.0),
            finite_number(metric.get("scroll_width")),
        )
        measured_height = max(
            max(bottoms, default=0.0) - min(ys, default=0.0),
            finite_number(metric.get("scroll_height")),
        )
        available_width = finite_number(metric.get("client_width"))
        available_height = finite_number(metric.get("client_height"))
        if min(measured_width, measured_height, available_width, available_height) <= 0:
            return None
        return {
            "source": "chrome",
            "telemetryComplete": bool(normalized_metrics.get("fonts_ready"))
            and metric.get("font_loaded") is True,
            "fontsReady": bool(normalized_metrics.get("fonts_ready"))
            and metric.get("font_loaded") is True,
            "kind": "range-bounds",
            "measuredWidthPx": measured_width,
            "measuredHeightPx": measured_height,
            "availableWidthPx": available_width,
            "availableHeightPx": available_height,
            "fontSizePx": finite_number(
                metric.get("font_size"), finite_number(getattr(imported, "fontSizePx", 0))
            ),
        }

    def precise_overflow_bounds(
        imported: Any, current: Mapping[str, float]
    ) -> dict[str, float]:
        """Expand only by the measured overflow plus a small render safety pad."""

        candidate = dict(current)
        if source is None or imported is None:
            candidate["height"] = min(1.0 - current["y"], current["height"] + 0.02)
            return candidate
        metric = metric_by_qid.get(imported.qid)
        if not isinstance(metric, Mapping):
            candidate["height"] = min(1.0 - current["y"], current["height"] + 0.02)
            return candidate
        horizontal_excess = max(
            0.0,
            float(metric.get("scroll_width", 0.0))
            - float(metric.get("client_width", 0.0)),
        )
        vertical_excess = max(
            0.0,
            float(metric.get("scroll_height", 0.0))
            - float(metric.get("client_height", 0.0)),
        )
        if horizontal_excess > 1.0:
            candidate["width"] = min(
                1.0 - current["x"],
                current["width"] + (horizontal_excess + 4.0) / source.viewportWidth,
            )
        if vertical_excess > 1.0:
            candidate["height"] = min(
                1.0 - current["y"],
                current["height"] + (vertical_excess + 4.0) / source.viewportHeight,
            )
        return candidate

    def point_segment_distance(
        point: tuple[float, float],
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> float:
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length_sq = dx * dx + dy * dy
        if length_sq <= 1e-12:
            return math.hypot(point[0] - start[0], point[1] - start[1])
        projection = max(
            0.0,
            min(
                1.0,
                ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy)
                / length_sq,
            ),
        )
        nearest = (start[0] + projection * dx, start[1] + projection * dy)
        return math.hypot(point[0] - nearest[0], point[1] - nearest[1])

    def segment_intersects_rect(
        start: tuple[float, float],
        end: tuple[float, float],
        rect: tuple[float, float, float, float],
    ) -> bool:
        """Liang-Barsky intersection, inclusive of touching the rectangle."""

        left, top, right, bottom = rect
        dx, dy = end[0] - start[0], end[1] - start[1]
        lower, upper = 0.0, 1.0
        for p, q in (
            (-dx, start[0] - left),
            (dx, right - start[0]),
            (-dy, start[1] - top),
            (dy, bottom - start[1]),
        ):
            if abs(p) <= 1e-12:
                if q < 0:
                    return False
                continue
            ratio = q / p
            if p < 0:
                lower = max(lower, ratio)
            else:
                upper = min(upper, ratio)
            if lower > upper:
                return False
        return True

    def segment_rect_clearance(
        start: tuple[float, float],
        end: tuple[float, float],
        rect: tuple[float, float, float, float],
    ) -> float:
        if segment_intersects_rect(start, end, rect):
            return 0.0
        left, top, right, bottom = rect
        corners = ((left, top), (right, top), (right, bottom), (left, bottom))

        def point_rect_distance(point: tuple[float, float]) -> float:
            x_delta = max(left - point[0], 0.0, point[0] - right)
            y_delta = max(top - point[1], 0.0, point[1] - bottom)
            return math.hypot(x_delta, y_delta)

        return min(
            point_rect_distance(start),
            point_rect_distance(end),
            *(point_segment_distance(corner, start, end) for corner in corners),
        )

    def connector_candidate(
        node: LayoutNode,
        metric: Mapping[str, Any] | None,
        obstacle_metrics: Sequence[Mapping[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return the shortest deterministic Chrome-grounded safe dogleg.

        The route is canonical in the connector node's normalized coordinate
        space.  Obstacles and endpoint observations come from the real Chrome
        frame, so a candidate is never labeled safe from schema geometry alone.
        """

        current = node.connectorRoute
        if current is None:
            return {}, {"source": "chrome", "telemetryComplete": False}
        current_route = {
            "points": [point.model_dump(mode="json") for point in current.points]
        }
        box = metric.get("bbox") if metric is not None else None
        box = box if isinstance(box, Mapping) else {}
        box_x = finite_number(box.get("x"))
        box_y = finite_number(box.get("y"))
        box_width = finite_number(box.get("width"))
        box_height = finite_number(box.get("height"))
        geometry = metric.get("connector_geometry") if metric is not None else None
        geometry = geometry if isinstance(geometry, Mapping) else {}
        measured_points = geometry.get("pointsPx")
        if not isinstance(measured_points, Sequence) or isinstance(
            measured_points, (str, bytes)
        ):
            measured_points = ()

        obstacle_rects: list[tuple[float, float, float, float]] = []
        for obstacle_metric in obstacle_metrics:
            obstacle_box = obstacle_metric.get("bbox")
            obstacle_box = obstacle_box if isinstance(obstacle_box, Mapping) else {}
            x = finite_number(obstacle_box.get("x"))
            y = finite_number(obstacle_box.get("y"))
            width = finite_number(obstacle_box.get("width"))
            height = finite_number(obstacle_box.get("height"))
            if width > 0 and height > 0:
                obstacle_rects.append((x, y, x + width, y + height))

        expected = [
            (
                box_x + point.x * box_width,
                box_y + point.y * box_height,
            )
            for point in (current.points[0], current.points[-1])
        ]
        actual: list[tuple[float, float]] = []
        if len(measured_points) >= 2:
            for raw in (measured_points[0], measured_points[-1]):
                if isinstance(raw, Mapping):
                    actual.append((finite_number(raw.get("x")), finite_number(raw.get("y"))))
        endpoint_error = (
            max(
                abs(observed[axis] - desired[axis])
                for observed, desired in zip(actual, expected)
                for axis in (0, 1)
            )
            if len(actual) == 2 and min(box_width, box_height) > 0
            else float("inf")
        )

        def route_pixels(points: Sequence[Mapping[str, float]]) -> list[tuple[float, float]]:
            return [
                (
                    box_x + finite_number(point.get("x")) * box_width,
                    box_y + finite_number(point.get("y")) * box_height,
                )
                for point in points
            ]

        def clearance(points: Sequence[Mapping[str, float]]) -> float:
            pixels = route_pixels(points)
            if len(pixels) < 2 or not obstacle_rects:
                return float("inf") if len(pixels) >= 2 else 0.0
            return min(
                segment_rect_clearance(start, end, rect)
                for start, end in zip(pixels, pixels[1:])
                for rect in obstacle_rects
            )

        start = current_route["points"][0]
        end = current_route["points"][-1]
        candidates: list[list[dict[str, float]]] = []
        if obstacle_rects and min(box_width, box_height) > 0:
            clearance_px = 4.0
            top = (min(rect[1] for rect in obstacle_rects) - clearance_px - box_y) / box_height
            bottom = (max(rect[3] for rect in obstacle_rects) + clearance_px - box_y) / box_height
            left = (min(rect[0] for rect in obstacle_rects) - clearance_px - box_x) / box_width
            right = (max(rect[2] for rect in obstacle_rects) + clearance_px - box_x) / box_width
            if 0 <= top <= 1:
                candidates.append([start, {"x": start["x"], "y": top}, {"x": end["x"], "y": top}, end])
            if 0 <= bottom <= 1:
                candidates.append([start, {"x": start["x"], "y": bottom}, {"x": end["x"], "y": bottom}, end])
            if 0 <= left <= 1:
                candidates.append([start, {"x": left, "y": start["y"]}, {"x": left, "y": end["y"]}, end])
            if 0 <= right <= 1:
                candidates.append([start, {"x": right, "y": start["y"]}, {"x": right, "y": end["y"]}, end])

        def route_length(points: Sequence[Mapping[str, float]]) -> float:
            return sum(
                math.hypot(
                    finite_number(right_point.get("x")) - finite_number(left_point.get("x")),
                    finite_number(right_point.get("y")) - finite_number(left_point.get("y")),
                )
                for left_point, right_point in zip(points, points[1:])
            )

        safe_candidates = [points for points in candidates if clearance(points) >= 1.0]
        safe_candidates.sort(
            key=lambda points: (
                round(route_length(points), 9),
                tuple((round(point["x"], 9), round(point["y"], 9)) for point in points),
            )
        )
        selected = safe_candidates[0] if safe_candidates else current_route["points"]
        minimum_clearance = clearance(selected)
        telemetry_complete = bool(
            metric is not None
            and geometry.get("measured") is True
            and len(actual) == 2
            and obstacle_rects
            and min(box_width, box_height) > 0
        )
        return {"points": selected}, {
            "source": "chrome",
            "telemetryComplete": telemetry_complete,
            "endpointAnchors": {
                "startResolved": len(actual) == 2,
                "endResolved": len(actual) == 2,
                "maxErrorPx": endpoint_error,
            },
            "obstacleFree": bool(safe_candidates and minimum_clearance >= 1.0),
            "minimumClearancePx": minimum_clearance,
            "obstacleCount": len(obstacle_rects),
        }
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
            _DEFECT_PRIORITY.get((defect.severity, defect.code), 99),
            defect.semantic_id,
        ),
    )
    affected_targets: set[str] = set()
    for defect in ordered_defects:
        diagnostic_semantic_ids = tuple(
            dict.fromkeys((defect.semantic_id, *defect.related_semantic_ids))
        )
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
        if node.layoutId not in affected_targets and len(affected_targets) >= 4:
            continue
        diagnostic_refs = tuple(
            f"{defect.code}:{semantic_id}" for semantic_id in diagnostic_semantic_ids
        )
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
        is_text_target = content is not None and content.kind in _TEXT_KINDS
        # Offer geometry first and a small font-scale alternative for text.  The
        # cascade evaluates them as separate candidates, so it never combines both
        # changes unless a later bounded proposal explicitly justifies that scope.
        use_font = (
            defect.code in {"dom.clipping", "dom.overflow"}
            and is_text_target
            and node.fontScale > 0.85
        )
        added = False
        static_position = imported is not None and imported.position == "static"
        if candidate != current and not static_position:
            existing = next(
                (item for item in bounds if item["target"] == node.layoutId), None
            )
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
            glyph_measurement(metric, imported)
            if use_font and metric is not None and imported is not None
            else None
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
            if imported.objectFit == "fill" and natural_ratio and box_ratio and abs(
                math.log(natural_ratio / box_ratio)
            ) > 0.05:
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
                            "telemetryComplete": bool(
                                image.get("complete") and natural_width and natural_height
                            ),
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
                "x": (
                    finite_number(target.get("x"))
                    - finite_number(parent.get("x"))
                    + dx * source.viewportWidth
                )
                / parent_width
                if source is not None and parent_width > 0
                else -1,
                "y": (
                    finite_number(target.get("y"))
                    - finite_number(parent.get("y"))
                    + dy * source.viewportHeight
                )
                / parent_height
                if source is not None and parent_height > 0
                else -1,
                "width": finite_number(target.get("width")) / parent_width
                if parent_width > 0
                else -1,
                "height": finite_number(target.get("height")) / parent_height
                if parent_height > 0
                else -1,
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
                            imported.staticTranslationSafe
                            and probe.get("telemetryComplete") is True
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
    return {
        "slideId": spec.slideId,
        "baseSpecRevision": spec.specRevision,
        "baseSpecHash": spec.digests.spec,
        "expectedContentDigest": spec.digests.content,
        "diagnosticRefs": tuple(dict.fromkeys(refs)),
        "diagnosticRefsByTarget": {
            target: tuple(dict.fromkeys(target_refs))
            for target, target_refs in refs_by_target.items()
        },
        "containerBounds": bounds,
        "fontScale": fonts,
        "glyphFit": glyph_fits,
        "imageCrop": image_crops,
        "staticTranslation": static_translations,
        "connectorRoute": connector_routes,
        "relationalConstraints": tuple(
            constraint.model_dump(mode="json")
            for constraint in spec.constraints
            if affected_targets.intersection(constraint_targets(constraint))
        ),
        "limits": {"maxOperations": 8, "maxAffectedBlocks": 4},
    }


def _token_recall(tokens: Sequence[str], text: str | None) -> float:
    if not tokens:
        return 1.0
    normalized = re.sub(r"\s+", "", normalize_ocr_text(text or "")).casefold()
    found = sum(
        re.sub(r"\s+", "", normalize_ocr_text(token)).casefold() in normalized
        for token in tokens
    )
    return found / len(tokens)


def _passed_evidence(
    *,
    baseline_dom: DomQualityReport,
    candidate_dom: DomQualityReport,
    ocr_non_regression: bool,
    token_recall: float,
    vqa_non_regression: bool,
    vqa_improved: bool,
    target_resolved: bool,
    constraint_report: ConstraintReport | None = None,
    constraint_non_regression: bool = True,
) -> AcceptanceEvidence:
    severity_rank = {"low": 1, "medium": 2, "high": 3}

    def issue_buckets(report: DomQualityReport) -> dict[tuple[str, tuple[str, ...]], list[int]]:
        grouped: dict[tuple[str, tuple[str, ...]], list[int]] = {}
        for issue in report.issues:
            if issue.severity not in {"high", "medium"}:
                continue
            grouped.setdefault((issue.category, issue.qids), []).append(
                severity_rank[issue.severity]
            )
        return {key: sorted(values, reverse=True) for key, values in grouped.items()}

    baseline_buckets = issue_buckets(baseline_dom)
    new_high_medium_defects = 0
    for key, candidate_ranks in issue_buckets(candidate_dom).items():
        baseline_ranks = baseline_buckets.get(key, [])
        new_high_medium_defects += max(0, len(candidate_ranks) - len(baseline_ranks))
        new_high_medium_defects += sum(
            candidate_rank > baseline_rank
            for candidate_rank, baseline_rank in zip(candidate_ranks, baseline_ranks)
        )
    dom_improved, _dom_reason = local_acceptance_decision(
        baseline_dom, candidate_dom
    )
    return AcceptanceEvidence(
        schema=GateEvidence("passed", evidence_ref="candidate.spec"),
        invariant=GateEvidence(
            "passed"
            if (
                constraint_non_regression
                and (constraint_report is None or not constraint_report.hard_failure)
            )
            else "failed",
            reason_codes=(
                ()
                if (
                    constraint_non_regression
                    and (constraint_report is None or not constraint_report.hard_failure)
                )
                else ("constraint.soft_regression",)
                if not constraint_non_regression
                else (
                    constraint_report.primary_code
                    or "constraint.invariant.violated",
                )
            ),
            evidence_ref=(
                "candidate.spec.digests"
                if constraint_report is None
                else "candidate.constraints"
            ),
        ),
        render=GateEvidence("passed", evidence_ref="candidate.render"),
        dom=GateEvidence(
            "passed",
            evidence_ref="candidate.dom",
            target_resolved=target_resolved,
            improved=dom_improved,
            non_regression=not has_dom_quality_regression(baseline_dom, candidate_dom),
            coverage=1.0 if candidate_dom.telemetry_status == "available" else 0.0,
            new_high_medium_defects=new_high_medium_defects,
        ),
        ocr=GateEvidence(
            "passed",
            evidence_ref="candidate.ocr",
            important_token_recall=token_recall,
            non_regression=ocr_non_regression,
        ),
        vqa=GateEvidence(
            "passed",
            evidence_ref="candidate.vqa",
            improved=vqa_improved,
            non_regression=vqa_non_regression,
        ),
    )


async def _run_one(
    *,
    presentation_id: uuid.UUID,
    slide: SlideModel,
    deck_snapshot: Sequence[dict[str, Any]],
    position: int,
    opt_in: bool,
    owner_id: object | None,
    session: AsyncSession,
) -> dict[str, Any]:
    started = time.perf_counter()
    # Keep the identifier outside SQLAlchemy's expiration lifecycle.  A rollback
    # expires ORM attributes, and fail-closed telemetry must never trigger a
    # second database access while handling the original error.
    slide_id = slide.id
    rollout = resolve_slidespec_repair_rollout(
        presentation_id, slide_index=position, opt_in=opt_in, owner_id=owner_id
    )

    def empty_metrics() -> dict[str, Any]:
        return {
            "solver": {
                "elapsed_ms": 0.0,
                "branches": 0,
                "unknown": 0,
                "unsat_core_size": 0,
                "search_complete": True,
            },
            "candidates": {
                "proposed": 0,
                "rendered": 0,
                "accepted": 0,
                "rejected": 0,
                "projection_rejected": 0,
            },
            "transaction": {"rollback_count": 0},
            "rollout": {"kill_switch_enabled": rollout.kill_switch_active},
        }
    try:
        bundle = await load_active_slidespec_bundle(session, slide)
        if bundle is None:  # defensive; callers claim only pointer-bearing slides
            return {"slide_index": position, "route": rollout.route, "applied": False,
                    "reason_codes": ["artifact.pointer.missing"], "metrics": empty_metrics()}
        request = _render_request(bundle)
    except (SlideSpecArtifactError, ValueError, TypeError) as error:
        code = error.code if isinstance(error, SlideSpecArtifactError) else "artifact.manifest.invalid"
        return {"slide_index": position, "route": rollout.route, "applied": False,
                "reason_codes": [code, "legacy_preserved"], "metrics": empty_metrics()}

    if not rollout.should_run_repair:
        return {
            "slide_index": position,
            "route": rollout.route,
            "applied": False,
            "reason_codes": [rollout.reason_code or "legacy_preserved", "legacy_preserved"],
            "spec_revision": request.spec.specRevision,
            "metrics": empty_metrics(),
        }

    try:
        baseline_png, baseline_metrics = await render_html_to_png_with_metrics(bundle.html)
        baseline_report = inspect_authored_dom(bundle.html, baseline_metrics)
    except Exception:  # renderer boundary is deliberately fail closed
        return {"slide_index": position, "route": rollout.route, "applied": False,
                "reason_codes": ["render.failed", "legacy_preserved"], "metrics": empty_metrics()}

    qids = _qid_to_semantic(request)
    baseline_observations = observations_from_browser_metrics(
        request, baseline_metrics
    )
    baseline_constraints = evaluate_constraints(
        request.spec,
        observations=baseline_observations,
        baseline_observations=baseline_observations,
        source=request.source,
    )
    solver_started = time.perf_counter()
    solver_result = solve_constraints(request.spec)
    solver_elapsed_ms = round((time.perf_counter() - solver_started) * 1000, 3)
    solver_metrics: dict[str, Any] = {
        **solver_result.metrics(),
        "elapsed_ms": solver_elapsed_ms,
        "branches": solver_result.branches_evaluated,
        "unknown": int(not solver_result.search_complete),
    }
    baseline_evidence = _merge_dom_evidence(
        _dom_evidence(baseline_report, qids),
        _constraint_evidence(request, baseline_constraints),
    )
    expected = _expected_text(request.spec)
    tokens = _important_tokens(request.spec)
    baseline_ocr: OcrQualityResult | None = None
    candidates: dict[str, _CandidateState] = {}
    projection_rejections: list[dict[str, str]] = []

    async def deterministic(_spec: SlideSpecInput, dom: DomEvidence) -> Sequence[RepairPlan]:
        solver_plan = build_constraint_repair_plan(
            request.spec, feasibility=solver_result
        )
        cascade = build_deterministic_cascade_report(
            _repair_context(request, dom, baseline_metrics)
        )
        projection_rejections[:] = [asdict(item) for item in cascade.rejections]
        return ((solver_plan,) if solver_plan is not None else ()) + cascade.plans

    async def validate(_spec: SlideSpecInput, plan: RepairPlan) -> SensorResult:
        try:
            applied = apply_repair_plan(request.spec, plan)
            validate_slidespec(applied.spec)
            return SensorResult(True)
        except (SlideSpecError, ValueError) as error:
            return SensorResult(False, getattr(error, "code", "schema.invalid_value"))

    async def render(plan_spec: SlideSpecInput, plan: RepairPlan) -> RenderedCandidate:
        del plan_spec
        applied = apply_repair_plan(request.spec, plan)
        candidate_request = request.model_copy(update={"spec": applied.spec})
        rendered = render_slidespec(candidate_request)
        image, metrics = await render_html_to_png_with_metrics(rendered.html)
        projection_errors = imported_projection_roundtrip_errors(
            candidate_request, metrics
        )
        if projection_errors:
            raise SlideSpecError(
                "render.source_projection.roundtrip_failed",
                ",".join(projection_errors[:8]),
            )
        report = inspect_authored_dom(rendered.html, metrics)
        constraint_report = evaluate_constraints(
            candidate_request.spec,
            observations=observations_from_browser_metrics(candidate_request, metrics),
            baseline_observations=baseline_observations,
            source=candidate_request.source,
        )
        candidates[rendered.renderInputHash] = _CandidateState(
            candidate_request, image, report, plan, constraint_report
        )
        return RenderedCandidate(
            rendered.renderInputHash,
            rendered.renderInputHash,
            realized_repair_cost(request.spec, applied.spec, plan).sort_key,
            constraint_report.soft_cost,
        )

    async def inspect_dom(candidate: RenderedCandidate) -> DomEvidence:
        state = candidates[candidate.html_hash]
        return _merge_dom_evidence(
            _dom_evidence(
                state.dom,
                _qid_to_semantic(state.request),
                diagnostic_refs=state.plan.diagnostic_refs,
            ),
            _constraint_evidence(state.request, state.constraints),
            _soft_constraint_regression_evidence(
                state.request, baseline_constraints, state.constraints
            ),
        )

    async def run_ocr(candidate: RenderedCandidate, _inventory: Any) -> SensorResult:
        nonlocal baseline_ocr
        state = candidates[candidate.html_hash]
        if baseline_ocr is None:
            baseline_ocr = await inspect_authored_ocr(baseline_png, expected_text=expected)
        result = await inspect_authored_ocr(state.image, expected_text=expected)
        if not baseline_ocr.available or not result.available or baseline_ocr.text is None or result.text is None:
            return SensorResult(False, "ocr.unavailable", conclusive=False)
        comparison = compare_expected_candidate(expected, baseline_ocr, result)
        recall = _token_recall(tokens, result.text)
        state.ocr_non_regression = comparison.no_regression
        state.token_recall = recall
        if not comparison.no_regression:
            return SensorResult(False, "ocr.recall_regressed")
        if recall < 1.0:
            return SensorResult(False, "ocr.important_token_missing")
        return SensorResult(True)

    async def run_vqa(candidate: RenderedCandidate) -> SensorResult:
        state = candidates[candidate.html_hash]
        critiques = await critique_authored_style_comparison(
            [baseline_png], [state.image], contexts=[expected]
        )
        critique = critiques[0] if critiques else None
        accepted, _reason, conclusive = style_comparison_improvement_decision(critique)
        state.vqa_non_regression = (
            critique is not None and not style_comparison_has_regression(critique)
        )
        state.vqa_improved = accepted
        return SensorResult(
            state.vqa_non_regression,
            None if state.vqa_non_regression else "vqa.regressed",
            conclusive=conclusive,
            quality_gain=style_comparison_quality_gain(critique),
            quality_gain_interval=style_comparison_quality_interval(critique),
        )

    async def publish(
        _spec: SlideSpecInput, plan: RepairPlan, candidate: RenderedCandidate
    ) -> PublicationReceipt:
        state = candidates[candidate.html_hash]
        evidence = _passed_evidence(
            baseline_dom=baseline_report,
            candidate_dom=state.dom,
            ocr_non_regression=state.ocr_non_regression,
            token_recall=state.token_recall,
            vqa_non_regression=state.vqa_non_regression,
            vqa_improved=state.vqa_improved,
            target_resolved=not _dom_evidence(
                state.dom, _qid_to_semantic(state.request),
                diagnostic_refs=plan.diagnostic_refs,
            ).required_geometry_defect
            and not state.constraints.hard_failure,
            constraint_report=state.constraints,
            constraint_non_regression=not has_soft_constraint_regression(
                baseline_constraints, state.constraints
            ),
        )
        decision = decide_acceptance(
            "geometry", evidence, base_spec_hash=request.spec.digests.spec,
            candidate_spec_hash=state.request.spec.digests.spec,
            decision_id=f"decision:{plan.plan_id}", plan_id=plan.plan_id,
            base_spec_revision=request.spec.specRevision,
        )
        state.decision = decision
        evidence_payload = {
            "baseline_dom": baseline_report.to_dict(),
            "candidate_dom": state.dom.to_dict(),
            "baseline_constraints": _constraint_report_payload(
                baseline_constraints
            ),
            "candidate_constraints": _constraint_report_payload(
                state.constraints
            ),
            "constraint_non_regression": not has_soft_constraint_regression(
                baseline_constraints, state.constraints
            ),
            "rollout": {
                "route": rollout.route,
                "cohort_bucket": rollout.cohort_bucket,
            },
        }
        attempt_id = uuid.uuid4()
        original_html = slide.html_content
        original_content = deepcopy(slide.content)
        original_properties = deepcopy(slide.properties)

        # Persist accepted evidence before touching the live slide. A failed CAS
        # or verification can therefore always be audited after the transaction.
        try:
            candidate_artifact = (
                await session.execute(
                    select(SlideSpecArtifactBundle).where(
                        SlideSpecArtifactBundle.slide_id == slide.id,
                        SlideSpecArtifactBundle.candidate_spec_hash
                        == state.request.spec.digests.spec,
                        SlideSpecArtifactBundle.status == "candidate",
                        SlideSpecArtifactBundle.route == "slidespec_active",
                    )
                )
            ).scalar_one_or_none()
            if candidate_artifact is None:
                candidate_artifact = build_slidespec_artifact_bundle(
                    presentation_id=presentation_id,
                    slide=slide,
                    route="slidespec_active",
                    status="candidate",
                    render_request=state.request,
                    repair_plan=plan,
                    acceptance=decision,
                    evidence=evidence_payload,
                    image_bytes=state.image,
                )
                session.add(candidate_artifact)
                await session.flush()
            await record_publication_attempt(
                session,
                attempt_id=attempt_id,
                bundle=candidate_artifact,
                state="staged",
                evidence={
                    "base_spec_hash": request.spec.digests.spec,
                    "candidate_spec_hash": state.request.spec.digests.spec,
                },
            )
            await session.commit()
        except Exception as error:
            await session.rollback()
            reason = getattr(error, "code", "artifact.publish.stage_failed")
            return PublicationReceipt(
                False,
                False,
                False,
                rolled_back=False,
                reason_code=reason,
            )

        # A concurrent edit after candidate generation invalidates the candidate.
        try:
            live_slide = await session.get(SlideModel, slide.id, populate_existing=True)
            if live_slide is None:
                raise SlideSpecArtifactError("artifact.publish.target_missing")
            if (
                live_slide.html_content != original_html
                or live_slide.content != original_content
                or live_slide.properties != original_properties
            ):
                raise SlideSpecArtifactError("artifact.publish.cas_conflict")

            artifact = (
                await session.execute(
                    select(SlideSpecArtifactBundle).where(
                        SlideSpecArtifactBundle.slide_id == slide.id,
                        SlideSpecArtifactBundle.candidate_spec_hash
                        == state.request.spec.digests.spec,
                        SlideSpecArtifactBundle.status == "published",
                        SlideSpecArtifactBundle.route == "slidespec_active",
                    )
                )
            ).scalar_one_or_none()
            if artifact is None:
                artifact = build_slidespec_artifact_bundle(
                    presentation_id=presentation_id,
                    slide=live_slide,
                    route="slidespec_active",
                    status="published",
                    render_request=state.request,
                    repair_plan=plan,
                    acceptance=decision,
                    evidence=evidence_payload,
                    publication_receipt={
                        "attempt_id": str(attempt_id),
                        "candidate_bundle_id": str(candidate_artifact.id),
                        "commit_state": "activation_pending",
                        "cas": "required",
                        "post_commit_readback": "required",
                    },
                    image_bytes=state.image,
                )
            await version_service.snapshot_slides(
                session,
                presentation_id,
                list(deck_snapshot),
                label="SlideSpec automatic repair",
                force=True,
            )
            await publish_slidespec_bundle_cas(
                session, slide=live_slide, bundle=artifact
            )
            await record_publication_attempt(
                session,
                attempt_id=attempt_id,
                bundle=artifact,
                state="activation_committed",
                evidence={
                    "html_hash": artifact.html_hash,
                    "render_hash": artifact.render_hash,
                },
            )
            await session.commit()
        except Exception as error:
            await session.rollback()
            reason = getattr(error, "code", "artifact.publish.transaction_failed")
            try:
                await record_publication_attempt(
                    session,
                    attempt_id=attempt_id,
                    bundle=candidate_artifact,
                    state="failed",
                    reason_code=reason,
                )
                await session.commit()
            except Exception:
                await session.rollback()
            return PublicationReceipt(
                False,
                False,
                False,
                rolled_back=False,
                reason_code=reason,
            )

        # Verify using a fresh session so identity-map state cannot masquerade as
        # a durable publication visible to later requests.
        verification_reason: str | None = None
        bind = session.bind
        if bind is None:
            verification_reason = "artifact.publish.verification_bind_missing"
        else:
            verify_sessions = async_sessionmaker(bind, expire_on_commit=False)
            try:
                async with verify_sessions() as verify_session:
                    refreshed = await verify_session.get(SlideModel, slide.id)
                    if refreshed is None:
                        raise SlideSpecArtifactError(
                            "artifact.publish.readback_mismatch"
                        )
                    loaded = await load_active_slidespec_bundle(
                        verify_session, refreshed
                    )
                    if (
                        loaded is None
                        or loaded.id != artifact.id
                        or loaded.html_hash != artifact.html_hash
                        or loaded.render_hash != artifact.render_hash
                    ):
                        raise SlideSpecArtifactError(
                            "artifact.publish.readback_mismatch"
                        )
                    await record_publication_attempt(
                        verify_session,
                        attempt_id=attempt_id,
                        bundle=artifact,
                        state="verified",
                        evidence={
                            "html_hash": loaded.html_hash,
                            "render_hash": loaded.render_hash,
                        },
                    )
                    await verify_session.commit()
            except Exception as error:
                verification_reason = getattr(
                    error, "code", "artifact.publish.postcommit_verify_failed"
                )

        if verification_reason is None:
            return PublicationReceipt(
                True,
                True,
                True,
                receipt_id=str(artifact.id),
            )

        # Post-commit failure restores only this exact publication. If another
        # writer changed the slide, its edit wins and we report a rollback conflict.
        expected_properties = {
            **(original_properties if isinstance(original_properties, dict) else {}),
            ACTIVE_POINTER_KEY: active_pointer(artifact),
        }
        rollback_succeeded = False
        recovery_reason = verification_reason
        try:
            if bind is None:
                raise SlideSpecArtifactError(
                    "artifact.publish.verification_bind_missing"
                )
            recovery_sessions = async_sessionmaker(bind, expire_on_commit=False)
            async with recovery_sessions() as recovery_session:
                await record_publication_attempt(
                    recovery_session,
                    attempt_id=attempt_id,
                    bundle=artifact,
                    state="failed",
                    reason_code=verification_reason,
                )
                rollback_succeeded = await rollback_active_slide_cas(
                    recovery_session,
                    slide_id=slide.id,
                    expected_html=artifact.html,
                    expected_properties=expected_properties,
                    restore_html=original_html,
                    restore_properties=original_properties,
                )
                if rollback_succeeded:
                    await record_publication_attempt(
                        recovery_session,
                        attempt_id=attempt_id,
                        bundle=artifact,
                        state="rolled_back",
                        reason_code=verification_reason,
                    )
                else:
                    recovery_reason = "artifact.publish.rollback_conflict"
                    await record_publication_attempt(
                        recovery_session,
                        attempt_id=attempt_id,
                        bundle=artifact,
                        state="rollback_conflict",
                        reason_code=recovery_reason,
                        evidence={"verification_reason": verification_reason},
                    )
                await recovery_session.commit()
        except Exception as error:
            recovery_reason = getattr(
                error, "code", "artifact.publish.rollback_failed"
            )

        return PublicationReceipt(
            True,
            True,
            False,
            receipt_id=str(artifact.id),
            rolled_back=rollback_succeeded,
            reason_code=recovery_reason,
        )

    async def propose(spec_input: SlideSpecInput, dom: DomEvidence) -> Sequence[RepairPlan]:
        diagnostics = [
            {"code": defect.code, "severity": defect.severity, "semanticId": defect.semantic_id}
            for defect in dom.defects
        ]
        result = await propose_slidespec_repair(
            request.spec.model_dump(mode="json", by_alias=True), diagnostics,
            slide_id=request.spec.slideId,
            base_spec_revision=request.spec.specRevision,
            base_spec_hash=request.spec.digests.spec,
            expected_content_digest=request.spec.digests.content,
        )
        if not result.ok:
            if "proposal.model_timeout" in result.reason_codes:
                raise TimeoutError("proposal.model_timeout")
            return ()
        return result.plans

    pipeline = SlideSpecRepairPipeline(
        SlideSpecRepairDependencies(
            deterministic_cascade=deterministic,
            validate_plan=validate,
            render_plan=render,
            inspect_dom=inspect_dom,
            run_ocr=run_ocr,
            run_vqa=run_vqa,
            publish=publish,
            propose=propose,
        )
    )
    spec_input = SlideSpecInput(
        revision_id=f"{request.spec.slideId}:{request.spec.specRevision}",
        spec_hash=request.spec.digests.spec,
        semantic_text=_semantic_text(request.spec),
        important_tokens=tokens,
    )
    try:
        result = await pipeline.run(
            spec=spec_input, baseline_dom=baseline_evidence, rollout=rollout
        )
    except Exception as error:
        await session.rollback()
        LOGGER.exception(
            "SlideSpec repair pipeline failed closed for slide %s at position %s",
            slide_id,
            position,
        )
        return {
            "slide_index": position,
            "route": rollout.route,
            "trigger": "geometry" if baseline_evidence.required_geometry_defect else None,
            "applied": False,
            "rolled_back": False,
            "reason_codes": ["acceptance.sensor_failed", "legacy_preserved"],
            "outcomes": [],
            "funnel": {"legacy_preserved": 1},
            "metrics": {
                **empty_metrics(),
                "solver": solver_metrics,
                "sensor_failure": {
                    "stage": "repair_pipeline",
                    "exception_type": type(error).__name__,
                },
            },
            "telemetry": {},
            "total_ms": round((time.perf_counter() - started) * 1000, 3),
        }

    shadow_artifact_id: str | None = None
    shadow_error: str | None = None
    if rollout.shadow_mode:
        accepted = next(
            (
                outcome
                for outcome in result.outcomes
                if outcome.validation_status == "accepted"
            ),
            None,
        )
        state = next(
            (
                candidate_state
                for candidate_state in candidates.values()
                if accepted is not None
                and candidate_state.plan.plan_id == accepted.plan_id
            ),
            None,
        )
        if accepted is not None and state is not None:
            evidence = _passed_evidence(
                baseline_dom=baseline_report,
                candidate_dom=state.dom,
                ocr_non_regression=state.ocr_non_regression,
                token_recall=state.token_recall,
                vqa_non_regression=state.vqa_non_regression,
                vqa_improved=state.vqa_improved,
                target_resolved=not _dom_evidence(
                    state.dom,
                    _qid_to_semantic(state.request),
                    diagnostic_refs=state.plan.diagnostic_refs,
                ).required_geometry_defect
                and not state.constraints.hard_failure,
                constraint_report=state.constraints,
                constraint_non_regression=not has_soft_constraint_regression(
                    baseline_constraints, state.constraints
                ),
            )
            decision = decide_acceptance(
                "geometry",
                evidence,
                base_spec_hash=request.spec.digests.spec,
                candidate_spec_hash=state.request.spec.digests.spec,
                decision_id=f"decision:{state.plan.plan_id}",
                plan_id=state.plan.plan_id,
                base_spec_revision=request.spec.specRevision,
            )
            state.decision = decision
            artifact = build_slidespec_artifact_bundle(
                presentation_id=presentation_id,
                slide=slide,
                route="repair_shadow",
                status="shadow",
                render_request=state.request,
                repair_plan=state.plan,
                acceptance=decision,
                evidence={
                    "baseline_dom": baseline_report.to_dict(),
                    "candidate_dom": state.dom.to_dict(),
                    "baseline_constraints": _constraint_report_payload(
                        baseline_constraints
                    ),
                    "candidate_constraints": _constraint_report_payload(
                        state.constraints
                    ),
                    "constraint_non_regression": not has_soft_constraint_regression(
                        baseline_constraints, state.constraints
                    ),
                    "rollout": {
                        "route": rollout.route,
                        "cohort_bucket": rollout.cohort_bucket,
                    },
                },
                image_bytes=state.image,
            )
            try:
                existing = await session.execute(
                    select(SlideSpecArtifactBundle).where(
                        SlideSpecArtifactBundle.slide_id == slide.id,
                        SlideSpecArtifactBundle.candidate_spec_hash
                        == artifact.candidate_spec_hash,
                        SlideSpecArtifactBundle.status == artifact.status,
                        SlideSpecArtifactBundle.route == artifact.route,
                    )
                )
                persisted = existing.scalar_one_or_none()
                if persisted is None:
                    session.add(artifact)
                    await session.commit()
                    persisted = artifact
                shadow_artifact_id = str(persisted.id)
            except Exception:
                await session.rollback()
                shadow_error = "artifact.shadow.persist_failed"

    reason_codes = list(result.reason_codes)
    if shadow_error is not None:
        reason_codes.insert(0, shadow_error)
    operational_metrics = result.metrics()
    operational_metrics["solver"] = solver_metrics
    operational_metrics["candidates"]["projection_rejected"] = len(
        projection_rejections
    )
    return {
        "slide_index": position,
        "route": result.route,
        "trigger": result.trigger,
        "applied": bool(result.funnel.applied),
        "rolled_back": bool(result.funnel.rolled_back),
        "reason_codes": reason_codes,
        "shadow_artifact_id": shadow_artifact_id,
        "outcomes": [asdict(outcome) for outcome in result.outcomes],
        "projection_rejections": projection_rejections,
        "funnel": result.funnel.metrics(),
        "metrics": operational_metrics,
        "telemetry": {
            "stage_timings_ms": dict(result.telemetry.stage_timings_ms),
            "llm_calls": result.telemetry.llm_calls,
            "llm_candidates": result.telemetry.llm_candidates,
            "render_calls": result.telemetry.render_calls,
            "dom_calls": result.telemetry.dom_calls,
            "ocr_calls": result.telemetry.ocr_calls,
            "vqa_calls": result.telemetry.vqa_calls,
            "cache_hits": result.telemetry.cache_hits,
            "cache_misses": result.telemetry.cache_misses,
            "repair_costs": [list(cost) for cost in result.telemetry.repair_costs],
        },
        "total_ms": round((time.perf_counter() - started) * 1000, 3),
    }


async def execute_slidespec_authored_review(
    *,
    presentation_id: uuid.UUID,
    slides: Sequence[SlideModel],
    positions: Sequence[int],
    opt_in: bool,
    owner_id: object | None,
    session: AsyncSession,
) -> CanonicalReviewExecution:
    """Claim and execute pointer-backed slides, leaving all others to legacy."""

    claimed = tuple(
        position
        for position in positions
        if isinstance(slides[position].properties, Mapping)
        and ACTIVE_POINTER_KEY in slides[position].properties
    )
    slide_ids = {position: slides[position].id for position in claimed}
    deck_snapshot = tuple(slide.model_dump(mode="json") for slide in slides)
    results = []
    for position in claimed:
        slide = await session.get(
            SlideModel, slide_ids[position], populate_existing=True
        )
        if slide is None:
            results.append(
                {
                    "slide_index": position,
                    "route": "slidespec_active",
                    "applied": False,
                    "reason_codes": ["slide.missing", "legacy_preserved"],
                }
            )
            continue
        result = await _run_one(
                presentation_id=presentation_id,
                slide=slide,
                deck_snapshot=deck_snapshot,
                position=position,
                opt_in=opt_in,
                owner_id=owner_id,
                session=session,
            )
        outcomes_for_slide = [
            outcome
            for outcome in result.get("outcomes", [])
            if isinstance(outcome, Mapping)
        ]
        terminal = outcomes_for_slide[-1] if outcomes_for_slide else {}
        triggered = bool(result.get("trigger"))
        applied_for_slide = bool(result.get("applied"))
        accepted_for_slide = any(
            outcome.get("validation_status") == "accepted"
            for outcome in outcomes_for_slide
        )
        reasons = [str(code) for code in result.get("reason_codes", [])]
        terminal_reasons = [
            str(code) for code in terminal.get("reason_codes", []) if str(code)
        ]
        candidate_rejections = [
            {
                "plan_id": str(outcome.get("plan_id", "")),
                "source": str(outcome.get("source", "")),
                "render_status": str(outcome.get("render_status", "")),
                "validation_status": str(outcome.get("validation_status", "")),
                "reason_codes": [
                    str(code) for code in outcome.get("reason_codes", []) if str(code)
                ],
            }
            for outcome in outcomes_for_slide
            if outcome.get("validation_status") in {"rejected", "inconclusive"}
        ]
        result.update(
            {
                "attempted": triggered or bool(outcomes_for_slide),
                "accepted": accepted_for_slide,
                "apply_status": terminal.get("apply_status", "not_started"),
                "publish_status": terminal.get("publish_status", "not_started"),
                "post_verify_status": terminal.get(
                    "post_verify_status", "not_started"
                ),
                "rejection_reason": None
                if applied_for_slide
                else (
                    terminal_reasons[0]
                    if terminal_reasons
                    else (reasons[0] if reasons else None)
                ),
                "candidate_rejections": candidate_rejections,
                "element_changes": [],
                "resolved_issues": ["geometry"] if applied_for_slide else [],
                "remaining_issues": ["geometry"]
                if triggered and not applied_for_slide
                else [],
                # Keep the established authored-review API contract so the
                # frontend can distinguish a canonical SlideSpec patch from a
                # full-slide fallback instead of normalizing it to ``none``.
                "path": "local_patch" if outcomes_for_slide else "none",
            }
        )
        results.append(result)
    funnel: dict[str, Any] = {}
    for result in results:
        for key, value in result.get("funnel", {}).items():
            funnel[key] = funnel.get(key, 0) + int(value)
    telemetry_keys = (
        "llm_calls",
        "llm_candidates",
        "render_calls",
        "dom_calls",
        "ocr_calls",
        "vqa_calls",
        "cache_hits",
        "cache_misses",
    )
    for key in telemetry_keys:
        funnel[key] = sum(
            int(result.get("telemetry", {}).get(key, 0)) for result in results
        )
    stage_timings_ms: dict[str, float] = {}
    for result in results:
        for stage, duration in result.get("telemetry", {}).get(
            "stage_timings_ms", {}
        ).items():
            stage_timings_ms[str(stage)] = stage_timings_ms.get(str(stage), 0.0) + float(
                duration
            )
    funnel["stage_timings_ms"] = {
        stage: round(duration, 3) for stage, duration in sorted(stage_timings_ms.items())
    }
    funnel["total_ms"] = round(
        sum(float(result.get("total_ms", 0.0)) for result in results), 3
    )
    funnel["deterministic_resolved"] = sum(
        bool(result.get("applied"))
        and any(
            outcome.get("source") == "deterministic"
            and outcome.get("validation_status") == "accepted"
            for outcome in result.get("outcomes", [])
        )
        for result in results
    )
    funnel["llm_resolved"] = sum(
        bool(result.get("applied"))
        and any(
            outcome.get("source") == "llm"
            and outcome.get("validation_status") == "accepted"
            for outcome in result.get("outcomes", [])
        )
        for result in results
    )
    def metric_number(value: object) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return number if math.isfinite(number) else 0.0

    operational_metrics: dict[str, Any] = {
        "solver": {
            "elapsed_ms": 0.0,
            "branches": 0,
            "unknown": 0,
            "unsat_core_size": 0,
            "search_complete": True,
        },
        "candidates": {
            "proposed": 0,
            "rendered": 0,
            "accepted": 0,
            "rejected": 0,
            "projection_rejected": 0,
        },
        "transaction": {"rollback_count": 0},
        "rollout": {"kill_switch_enabled": False},
    }
    for result in results:
        item_metrics = result.get("metrics")
        if not isinstance(item_metrics, Mapping):
            continue
        solver = item_metrics.get("solver")
        if isinstance(solver, Mapping):
            for key in ("elapsed_ms", "branches", "unknown", "unsat_core_size"):
                operational_metrics["solver"][key] += metric_number(solver.get(key))
            operational_metrics["solver"]["search_complete"] = bool(
                operational_metrics["solver"]["search_complete"]
            ) and bool(solver.get("search_complete", False))
        candidate_metrics = item_metrics.get("candidates")
        if isinstance(candidate_metrics, Mapping):
            for key in (
                "proposed",
                "rendered",
                "accepted",
                "rejected",
                "projection_rejected",
            ):
                operational_metrics["candidates"][key] += int(
                    metric_number(candidate_metrics.get(key))
                )
        transaction = item_metrics.get("transaction")
        if isinstance(transaction, Mapping):
            operational_metrics["transaction"]["rollback_count"] += int(
                metric_number(transaction.get("rollback_count"))
            )
        rollout_metrics = item_metrics.get("rollout")
        if isinstance(rollout_metrics, Mapping):
            operational_metrics["rollout"]["kill_switch_enabled"] = bool(
                operational_metrics["rollout"]["kill_switch_enabled"]
            ) or bool(rollout_metrics.get("kill_switch_enabled"))
    operational_metrics["solver"]["elapsed_ms"] = round(
        operational_metrics["solver"]["elapsed_ms"], 3
    )
    applied = [result["slide_index"] for result in results if result.get("applied")]
    outcomes = [
        outcome
        for result in results
        for outcome in result.get("outcomes", [])
        if isinstance(outcome, Mapping)
    ]
    candidate_count = len(outcomes)
    accepted_candidate_count = sum(
        outcome.get("validation_status") == "accepted" for outcome in outcomes
    )
    rejected_candidate_count = sum(
        outcome.get("validation_status") in {"rejected", "inconclusive"}
        for outcome in outcomes
    )
    rejection_reasons: dict[str, int] = {}
    for result in results:
        for code in result.get("reason_codes", []):
            rejection_reasons[str(code)] = rejection_reasons.get(str(code), 0) + 1
    for outcome in outcomes:
        for code in outcome.get("reason_codes", []):
            rejection_reasons[str(code)] = rejection_reasons.get(str(code), 0) + 1
    return CanonicalReviewExecution(
        claimed_positions=claimed,
        data={
            "reviewed_count": len(results),
            "issue_slide_count": sum(bool(item.get("trigger")) for item in results),
            "issues": [],
            "fixed_count": len(applied),
            "candidate_count": candidate_count,
            "accepted_candidate_count": accepted_candidate_count,
            "rejected_candidate_count": rejected_candidate_count,
            "fixed_slide_indices": applied,
            "remaining_issue_count": sum(
                bool(item.get("trigger")) and not item.get("applied") for item in results
            ),
            "remaining_issue_indices": [
                item["slide_index"]
                for item in results
                if item.get("trigger") and not item.get("applied")
            ],
            "remaining_issues": [],
            "candidate_decisions": outcomes,
            "rejection_reasons": rejection_reasons,
            "version_saved": bool(applied),
            "slide_results": results,
            "routing": {"slidespec": True, "claimed_positions": list(claimed)},
            "metrics": {**funnel, **operational_metrics},
        },
    )


__all__ = ["CanonicalReviewExecution", "execute_slidespec_authored_review"]
