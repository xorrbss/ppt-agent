"""Fail closed source-verbatim readback against Chromium DOM telemetry.

The source-content manifest identifies immutable blocks.  This module binds each
block to an explicit ``data-source-block-id`` (or its metrics equivalent) and
checks the *rendered* Chromium observation, rather than trusting HTML strings.
The resulting evidence keeps geometry and visibility facts attached to the
source block for publication gates and later audit.

A block may be bound as a whole element or as a partition of projected runs
(``<blockId>:run:<n>``, see :mod:`services.source_content.source_runs`).  Run
bindings are verified individually and folded into one evidence entry per
manifest block, so receipts keyed by block ID are unchanged.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import logging
import math
import re
from typing import Any

from services.authored_vnext.capability_registry import capability_metadata
from services.source_content.source_runs import (
    SourceRunProjection,
    parse_source_binding_id,
    project_source_runs,
    source_run_binding_id,
)
from services.source_content.source_stamp_dom import (
    BrowserSourceReadbackError,
    deterministic_source_qid,
    insert_start_tag_attributes,
    parse_stamp_dom,
    replace_source_qid,
    source_block_data_attribute,
)
from services.source_content.text_stamping import stamp_source_block_ids


LOGGER = logging.getLogger(__name__)
_FAILURE_ID_LIMIT = 8
_FAILURE_ID_LENGTH = 96


def _bounded_failure_ids(values: Sequence[object]) -> str:
    identifiers = [
        re.sub(r"[\x00-\x1f\x7f]+", "?", str(value))[:_FAILURE_ID_LENGTH]
        for value in values[:_FAILURE_ID_LIMIT]
    ]
    suffix = f"+{len(values) - len(identifiers)}" if len(values) > len(identifiers) else ""
    return "[" + ",".join(identifiers) + "]" + suffix


def log_browser_source_readback_failure(result: "BrowserSourceReadbackResult") -> str:
    """Warn with bounded identifiers and return the same operation-safe summary."""

    summary = " ".join((
        f"slides={_bounded_failure_ids(result.failed_slide_indices)}",
        f"missing={_bounded_failure_ids(result.missing_block_ids)}",
        f"order={int(result.order_violations)}",
        f"integrity={_bounded_failure_ids(result.integrity_failures)}",
        f"telemetry={_bounded_failure_ids(result.telemetry_failures)}",
        f"visibility={_bounded_failure_ids(result.visibility_failures)}",
        f"diagnostic={bool(result.diagnostic_error)}",
    ))
    LOGGER.warning("browser source readback rejected: %s", summary)
    return summary


@dataclass(frozen=True, slots=True)
class BrowserSourceBlockEvidence:
    source_block_id: str
    qid: str
    rendered_text: str
    bbox: dict[str, float]
    visible: bool
    clipped: bool
    occluded_fraction: float | None
    continuous_occluded_fraction: float | None
    kind: str = "text"
    natural_width: float | None = None
    natural_height: float | None = None
    semantic_role: str = ""
    run_bindings: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "sourceBlockId": self.source_block_id,
            "qid": self.qid,
            "renderedText": self.rendered_text,
            "bbox": dict(self.bbox),
            "visible": self.visible,
            "clipped": self.clipped,
            "occludedFraction": self.occluded_fraction,
            "continuousOccludedFraction": self.continuous_occluded_fraction,
            "kind": self.kind,
            "naturalWidth": self.natural_width,
            "naturalHeight": self.natural_height,
            "semanticRole": self.semantic_role,
        }
        if self.run_bindings:
            payload["runs"] = [dict(item) for item in self.run_bindings]
        return payload


@dataclass(frozen=True, slots=True)
class BrowserSourceReadbackResult:
    checked_slide_count: int
    checked_block_count: int
    evidence: tuple[BrowserSourceBlockEvidence, ...] = ()
    missing_block_ids: tuple[str, ...] = ()
    order_violations: int = 0
    visibility_failures: tuple[str, ...] = ()
    # Blocks bound to a shared or out-of-order owner: measured and reported,
    # never a failure.  Where a template places a block's own items is layout.
    binding_deviations: int = 0
    integrity_failures: tuple[str, ...] = ()
    telemetry_failures: tuple[str, ...] = ()
    failed_slide_indices: tuple[int, ...] = ()
    diagnostic_error: str = ""

    @property
    def passed(self) -> bool:
        return not (
            self.missing_block_ids
            or self.order_violations
            or self.visibility_failures
            or self.integrity_failures
            or self.telemetry_failures
            or self.diagnostic_error
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "checkedSlideCount": self.checked_slide_count,
            "checkedBlockCount": self.checked_block_count,
            "evidence": [item.as_dict() for item in self.evidence],
            "missingBlockIds": list(self.missing_block_ids),
            "orderViolations": self.order_violations,
            "bindingDeviations": self.binding_deviations,
            "visibilityFailures": list(self.visibility_failures),
            "passed": self.passed,
            "integrityFailures": list(self.integrity_failures),
            "telemetryFailures": list(self.telemetry_failures),
            "failedSlideIndices": list(self.failed_slide_indices),
            "diagnosticError": self.diagnostic_error,
            # Only this browser verifier can mint the explicit admission bit
            # consumed by QualityReceipt v2.  Keeping it separate from
            # ``passed`` prevents arbitrary caller dictionaries from being
            # treated as Chromium-verified evidence.
            "verified": self.passed,
            "capabilityRegistry": capability_metadata(consumer="readback"),
        }


def deterministic_source_block_id(source_unit_id: str, order: int, text: str) -> str:
    """Return a stable fallback ID for newly materialized source blocks.

    Existing manifest IDs remain authoritative.  This helper is for adapters
    that need to stamp a block before a manifest is persisted; it is deliberately
    content addressed so retries cannot produce a different binding.
    """

    canonical = re.sub(r"\s+", " ", str(text)).strip()
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"source-block:{source_unit_id}:{int(order)}:{digest}"


def deterministic_source_image_block_id(
    source_unit_id: str,
    order: int,
    asset_digest: str,
    occurrence: int = 1,
) -> str:
    """Return a stable identity for one source-image occurrence.

    Image bytes may be deduplicated in the manifest, so the occurrence ordinal
    is part of the identity.  Including the asset digest prevents two images
    at the same source order from silently aliasing during retries.
    """

    unit = str(source_unit_id).strip()
    digest = str(asset_digest).strip()
    if not unit or not digest or int(occurrence) < 1:
        raise ValueError("source image occurrence identity is invalid")
    canonical = f"{unit}|image|{int(order)}|{digest}|{int(occurrence)}"
    suffix = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"source-block:{unit}:image:{int(order)}:{int(occurrence)}:{suffix}"


def stamp_source_image_block_ids(
    html_source: str,
    image_block_ids: Sequence[str],
) -> str:
    """Bind source-image occurrence IDs to actual ``<img>`` elements.

    Binding follows DOM order and only touches previously unbound images.  A
    cardinality mismatch fails closed; this keeps duplicate asset bytes from
    collapsing multiple occurrences into one browser owner.
    """

    parser = parse_stamp_dom(html_source)
    ids = [str(value).strip() for value in image_block_ids]
    if not ids and not any(
        parser.tokens[node.start].lstrip().lower().startswith("<img")
        and not node.hidden
        and not _is_decorative_image_tag(parser.tokens[node.start])
        for node in parser.nodes
    ):
        return str(html_source)
    if len(ids) != len(set(ids)) or any(not value for value in ids):
        raise BrowserSourceReadbackError(
            "source_content_browser_readback_invalid",
            detail="source image block IDs must be unique and non-empty",
        )
    image_nodes = [
        node for node in parser.nodes
        if parser.tokens[node.start].lstrip().lower().startswith("<img")
        and not node.hidden
    ]
    # Decorative/template images are outside the source occurrence contract.
    # They must be explicitly marked so a source binding can never silently
    # consume them.  Unknown unmarked images remain eligible; if a caller has
    # mixed unmarked decorative and source images it must fail by cardinality
    # rather than guessing.
    available = [
        node for node in image_nodes
        if not node.stamped and not _is_decorative_image_tag(parser.tokens[node.start])
    ]
    bound_nodes = [node for node in image_nodes if node.stamped and node.source_id]
    existing = [node.source_id or "" for node in bound_nodes]
    if not available and existing == ids:
        # A durable retry may reopen HTML stamped before canonical source QID
        # ownership was enforced. Reuse the same occurrence owners and repair
        # only their QID value in place.
        target_nodes = bound_nodes
    elif len(available) != len(ids):
        raise BrowserSourceReadbackError(
            "source_content_browser_readback_failed",
            detail="source image occurrence count does not match DOM images",
            block_ids=tuple(ids),
        )
    else:
        target_nodes = available
    for node, block_id in zip(target_nodes, ids, strict=True):
        if node.qid_count > 1:
            raise BrowserSourceReadbackError(
                "source_content_browser_readback_failed",
                detail="source image has duplicate authored QID attributes",
                block_ids=(block_id,),
            )
        raw_tag = parser.tokens[node.start]
        source_qid = deterministic_source_qid(block_id)
        if node.has_qid:
            raw_tag = replace_source_qid(raw_tag, source_qid)
        attrs = "" if node.stamped else source_block_data_attribute(block_id)
        if not node.has_qid:
            attrs = (attrs + " " if attrs else "") + f'data-qid="{source_qid}"'
        # Source occurrences participate in the same semantic capacity gate as
        # authored text.  Stamp the role at the DOM boundary so Chromium
        # readback can prove that this is an intentional image element rather
        # than merely an HTML string containing an <img> tag.
        if not node.has_semantic_role:
            attrs = (attrs + " " if attrs else "") + 'data-semantic-role="image"'
        parser.tokens[node.start] = insert_start_tag_attributes(
            raw_tag, attrs, block_id=block_id, label="image"
        )
        node.stamped = True
        node.source_id = block_id
    return "".join(parser.tokens)


def _is_decorative_image_tag(raw_tag: str) -> bool:
    """Return whether an image is explicitly outside source content."""

    attrs: dict[str, str] = {}
    for match in re.finditer(
        r"([:\w-]+)(?:\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+)))?",
        raw_tag,
        flags=re.IGNORECASE,
    ):
        name = match.group(1).lower()
        # Ignore the tag name itself, which the expression also matches.
        if name == "img":
            continue
        attrs[name] = (match.group(2) or match.group(3) or match.group(4) or "").strip().lower()
    if attrs.get("aria-hidden") == "true" or attrs.get("role") in {"presentation", "none"}:
        return True
    if attrs.get("data-source-image") in {"false", "0", "no"}:
        return True
    return attrs.get("data-decorative") in {"true", "1", "yes"} or attrs.get("data-decoration") in {"true", "1", "yes"}


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True)
    if not isinstance(value, Mapping):
        raise BrowserSourceReadbackError("source_content_browser_readback_invalid", detail=f"{label} must be an object")
    return value


def _field(value: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in value:
            return value[name]
    return default


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _finite_bbox(value: Any) -> dict[str, float] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, float] = {}
    for key in ("x", "y", "width", "height"):
        try:
            number = float(value[key])
        except (KeyError, TypeError, ValueError):
            return None
        if not math.isfinite(number) or (key in {"width", "height"} and number <= 0):
            return None
        result[key] = number
    return result


def _metric_visible(metric: Mapping[str, Any], bbox: Mapping[str, float]) -> bool | None:
    explicit = _field(metric, "visible", "css_visible", "cssVisible", default=None)
    style = _field(metric, "computed_style", "computedStyle", default={})
    if not isinstance(style, Mapping):
        return None
    # The element's own opacity does not expose a transparent ancestor.  The
    # probe must report the cumulative ancestor opacity and CSS visibility.
    effective_opacity = _field(metric, "effective_opacity", "effectiveOpacity", default=None)
    if not isinstance(explicit, bool) or effective_opacity is None or not all(key in style for key in ("display", "visibility")):
        return None
    display = str(style.get("display", "")).lower()
    visibility = str(style.get("visibility", "")).lower()
    try:
        opacity = float(effective_opacity)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(opacity):
        return None
    return explicit is not False and display not in {"none", "contents"} and visibility not in {"hidden", "collapse"} and opacity > 0 and bbox["width"] > 0 and bbox["height"] > 0


def _block_raw_text(block: Mapping[str, Any]) -> str:
    payload = _mapping(_field(block, "payload", default={}), "source block payload")
    if isinstance(payload.get("text"), str):
        return payload["text"]
    runs = payload.get("runs", ())
    if isinstance(runs, Sequence) and not isinstance(runs, (str, bytes)):
        return "".join(str(run.get("text", "")) if isinstance(run, Mapping) else str(run) for run in runs)
    return ""


def _block_projection(block: Mapping[str, Any]) -> SourceRunProjection | None:
    try:
        return project_source_runs(_block_raw_text(block))
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _slides_metrics(browser_metrics: Any) -> list[Mapping[str, Any]]:
    if isinstance(browser_metrics, Sequence) and not isinstance(browser_metrics, (str, bytes)):
        return [_mapping(item, "browser slide metrics") for item in browser_metrics]
    root = _mapping(browser_metrics, "browser metrics")
    slides = _field(root, "slides", "slideMetrics", default=None)
    if isinstance(slides, Sequence) and not isinstance(slides, (str, bytes)):
        return [_mapping(item, "browser slide metrics") for item in slides]
    return [root]


@dataclass(frozen=True, slots=True)
class _Observation:
    """Measured facts about one bound DOM element, independent of the block."""

    tag: str
    qid: str
    rendered: str
    semantic_role: str
    bbox: dict[str, float] | None
    in_canvas: bool
    visible: bool | None
    clipped: bool
    clipping_known: bool
    occluded: float | None
    continuous: float | None
    occlusion_known: bool
    font_loaded: bool
    image_payload: Mapping[str, Any] | None
    natural_width: float | None
    natural_height: float | None
    occlusion_measured: bool

    def integrity_failed(self, accepts, expected_qid, qid_counts, seen_qids) -> bool:
        return (
            accepts is None or not accepts(self.rendered)
            or not self.qid or self.qid in seen_qids
            or self.qid != expected_qid or qid_counts.get(self.qid, 0) != 1
            or not self.semantic_role
        )

    def telemetry_failed(self, *, text: bool = True) -> bool:
        return (
            self.bbox is None or self.visible is None
            or not self.clipping_known or not self.occlusion_measured
            or (text and not self.font_loaded)
        )

    def text_failed(
        self,
        accepts: Callable[[str], bool] | None,
        expected_qid: str,
        qid_counts: Counter[str],
        seen_qids: set[str],
    ) -> bool:
        return (
            accepts is None
            or not self.qid
            or self.qid in seen_qids
            or self.qid != expected_qid
            or qid_counts.get(self.qid, 0) != 1
            or not self.in_canvas
            or self.visible is not True
            or not self.font_loaded
            or not self.clipping_known
            or self.clipped
            or not self.occlusion_known
            or not accepts(self.rendered)
            or not self.semantic_role
        )


def _observe(metric: Mapping[str, Any], canvas_width: float, canvas_height: float) -> _Observation:
    bbox = _finite_bbox(_field(metric, "bbox", default=None))
    qid = str(_field(metric, "qid", default="") or "").strip()
    rendered = _text(_field(metric, "rendered_text", "renderedText", "innerText", "text", default=""))
    visible = _metric_visible(metric, bbox or {}) if bbox is not None else None
    clip_values = [
        _field(metric, "clipped", default=None),
        _field(metric, "glyph_clipped", "glyphClipped", default=None),
        _field(metric, "line_box_clipped", "lineBoxClipped", default=None),
    ]
    clipped = any(value is True for value in clip_values)
    clipping_known = all(isinstance(value, bool) for value in clip_values)
    occluded = _number(_field(metric, "occluded_fraction", "occludedFraction", default=None))
    continuous = _number(_field(metric, "continuous_occluded_fraction", "continuousOccludedFraction", default=None))
    occlusion_known = (
        occluded is not None
        and continuous is not None
        and 0 <= occluded <= 1
        and 0 <= continuous <= 1
    )
    measurement = _field(metric, "measurement", default={})
    occlusion_known = (
        occlusion_known
        and isinstance(measurement, Mapping)
        and measurement.get("occlusion") is True
    )
    occlusion_evidence = _field(
        metric, "occlusion_measurement_evidence", "occlusionMeasurementEvidence", default={}
    )
    occlusion_known = (
        occlusion_known
        and isinstance(occlusion_evidence, Mapping)
        and occlusion_evidence.get("status") == "measured"
    )
    in_canvas = (
        bbox is not None
        and bbox["x"] >= 0
        and bbox["y"] >= 0
        and bbox["x"] + bbox["width"] <= canvas_width
        and bbox["y"] + bbox["height"] <= canvas_height
    )
    tag = str(_field(metric, "tag", default="")).lower()
    image_payload = _field(metric, "image", default=None)
    if not isinstance(image_payload, Mapping) and tag == "img":
        image_payload = metric
    if not isinstance(image_payload, Mapping):
        image_payload = None
    if tag == "img":
        # Images have no glyph ranges, so the text clipping flags can all be
        # false even when an ancestor hides most of the source occurrence.
        # New captures provide Chromium's intersection of every clip ancestor.
        # Older captures retain their existing explicit clipping contract.
        clip_bounds = _field(metric, "source_clip_bounds", "sourceClipBounds", default=None)
        if clip_bounds is not None:
            edges = (
                [_number(clip_bounds.get(key)) for key in ("left", "top", "right", "bottom")]
                if isinstance(clip_bounds, Mapping) else []
            )
            if len(edges) != 4 or any(value is None for value in edges) or bbox is None:
                clipping_known = False
            else:
                left, top, right, bottom = edges
                # Match the existing browser range-clipping/intrinsic-image
                # tolerance rather than treating subpixel paint noise as loss.
                clipped = clipped or (
                    right <= left or bottom <= top
                    or bbox["x"] < left - 0.5
                    or bbox["y"] < top - 0.5
                    or bbox["x"] + bbox["width"] > right + 0.5
                    or bbox["y"] + bbox["height"] > bottom + 0.5
                )
    natural_width = natural_height = None
    if image_payload is not None:
        natural_width = _number(_field(image_payload, "natural_width", "naturalWidth", default=None))
        natural_height = _number(_field(image_payload, "natural_height", "naturalHeight", default=None))
    return _Observation(
        tag=tag,
        qid=qid,
        rendered=rendered,
        semantic_role=str(_field(metric, "semantic_role", "semanticRole", "role", default="") or "").strip(),
        bbox=bbox,
        in_canvas=in_canvas,
        visible=visible,
        clipped=clipped,
        clipping_known=clipping_known,
        occluded=occluded,
        continuous=continuous,
        occlusion_known=occlusion_known and occluded == 0 and continuous == 0,
        font_loaded=_field(metric, "font_loaded", "fontLoaded", default=None) is True,
        image_payload=image_payload,
        natural_width=natural_width,
        natural_height=natural_height,
        occlusion_measured=bool(occlusion_known),
    )


def _image_failed(observation: _Observation, payload: Mapping[str, Any]) -> bool:
    image = observation.image_payload or {}
    complete = _field(image, "complete", default=None)
    image_visible = _field(image, "cssVisible", "css_visible", default=None)
    role = observation.semantic_role.lower()
    expected_width = _number(
        _field(payload, "naturalWidth", "natural_width", "intrinsicWidth", "intrinsic_width", default=None)
    )
    expected_height = _number(
        _field(payload, "naturalHeight", "natural_height", "intrinsicHeight", "intrinsic_height", default=None)
    )
    width, height = observation.natural_width, observation.natural_height
    return (
        observation.tag != "img"
        or complete is not True
        or width is None or width <= 0
        or height is None or height <= 0
        or image_visible is False
        or not role
        or not (role.startswith("image") or role.endswith("image"))
        or (expected_width is not None and abs(width - expected_width) > 0.5)
        or (expected_height is not None and abs(height - expected_height) > 0.5)
    )


def _max_or_none(values: Sequence[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return max(present) if len(present) == len(values) and present else None


def _fold_run_evidence(block_id: str, runs: Sequence[tuple[int, int, int, _Observation]]) -> BrowserSourceBlockEvidence:
    """Fold verified run observations into one block-level evidence entry."""

    observations = [item[3] for item in runs]
    boxes = [item.bbox for item in observations if item.bbox is not None]
    bbox: dict[str, float] = {}
    if boxes and len(boxes) == len(observations):
        left = min(box["x"] for box in boxes)
        top = min(box["y"] for box in boxes)
        bbox = {
            "x": left,
            "y": top,
            "width": max(box["x"] + box["width"] for box in boxes) - left,
            "height": max(box["y"] + box["height"] for box in boxes) - top,
        }
    return BrowserSourceBlockEvidence(
        block_id,
        observations[0].qid,
        " ".join(item.rendered for item in observations),
        bbox,
        all(item.visible is True for item in observations),
        any(item.clipped for item in observations),
        _max_or_none([item.occluded for item in observations]),
        _max_or_none([item.continuous for item in observations]),
        "text",
        None,
        None,
        observations[0].semantic_role,
        tuple(
            {
                "sourceBlockId": source_run_binding_id(block_id, start, end, run_count),
                "qid": item.qid,
                "renderedText": item.rendered,
                "bbox": dict(item.bbox or {}),
                "visible": item.visible is True,
                "clipped": item.clipped,
                "semanticRole": item.semantic_role,
            }
            for (start, end, run_count, item) in runs
        ),
    )


def _dom_order_follows_source(
    projection: SourceRunProjection,
    source_ordered: Sequence[tuple[int, int, int, Mapping[str, Any]]],
) -> bool:
    """Mirror the stamping binder's order rule for one block's own runs.

    Bullets, arrows and lines enumerate a sequence the source author chose, so
    their elements must appear in that order in the DOM.  A span that begins
    right after a pipe is an independent label whose slot the new template
    owns -- a brand beside a course title, regularly emitted second -- so it
    may be rendered anywhere.  That is exactly the exemption ``_bind_block``
    applies when it searches the document from the start again, and this gate
    has to grant it too or it rejects the decks stamping accepted.
    """

    last_dom = -1
    for dom_index, start, _end, _metric in source_ordered:
        if not 0 <= start < projection.run_count:
            return False
        if not projection.runs[start].order_free_before and dom_index <= last_dom:
            return False
        last_dom = max(last_dom, dom_index)
    return True


def _span_matcher(
    projection: SourceRunProjection, start: int, end: int
) -> Callable[[str], bool]:
    """Accept an element that owns the span, or one that merely carries it.

    Exclusive ownership is the better measurement and stays the first test.
    An element carrying the span among other text is what the binder falls back
    to, and rejecting it here would fail a slide whose source text is entirely
    present -- the opposite of what the content contract promises.
    """

    def matches(rendered: str) -> bool:
        return projection.accepts(start, end, rendered) or projection.contains(
            start, end, rendered
        )

    return matches


def _verify_run_partition(
    block_id: str,
    projection: SourceRunProjection | None,
    runs: Sequence[tuple[int, int, int, Mapping[str, Any]]],
    *,
    canvas: tuple[float, float],
    qid_counts: Counter[str],
    seen_qids: set[str],
    integrity_failures: list[str] | None = None,
    telemetry_failures: list[str] | None = None,
) -> tuple[BrowserSourceBlockEvidence | None, bool, bool]:
    """Return ``(evidence, complete, ordered)`` for a run-bound block.

    Runs must partition the projection exactly (every run once, no overlap).
    Each run element is held to the same geometry/visibility/QID/text contract
    as a whole-block binding.  DOM order is still measured and returned so the
    receipt can carry it, but the caller records it rather than failing on it.
    """

    if projection is None or projection.run_count < 2:
        return None, False, True
    # Completeness is a property of the projection, not of the page: sort by
    # source span so a block whose template placed a later run first can still
    # prove every run is bound exactly once.  Ordering is checked separately,
    # against the same exemption the binder applies.
    source_ordered = sorted(runs, key=lambda item: (item[1], item[2]))
    spans = [(start, end) for _, start, end, _ in source_ordered]
    expected_cursor = 0
    complete = True
    for start, end in spans:
        if start != expected_cursor or end < start:
            complete = False
            break
        expected_cursor = end + 1
    complete = complete and expected_cursor == projection.run_count
    ordered = _dom_order_follows_source(projection, source_ordered)
    if not complete:
        return None, False, ordered
    observed: list[tuple[int, int, int, _Observation]] = []
    failed = False
    for _, start, end, metric in source_ordered:
        observation = _observe(metric, *canvas)
        run_id = source_run_binding_id(block_id, start, end, projection.run_count)
        accepts = _span_matcher(projection, start, end)
        if integrity_failures is not None and observation.integrity_failed(
            accepts, deterministic_source_qid(run_id), qid_counts, seen_qids
        ):
            integrity_failures.append(block_id)
        if telemetry_failures is not None and observation.telemetry_failed():
            telemetry_failures.append(block_id)
        if observation.text_failed(accepts, deterministic_source_qid(run_id), qid_counts, seen_qids):
            failed = True
        seen_qids.add(observation.qid)
        observed.append((start, end, projection.run_count, observation))
    evidence = _fold_run_evidence(block_id, observed)
    return evidence, not failed, ordered


def _manifest_index(units: Sequence[Any]) -> tuple[dict[str, dict[str, Mapping[str, Any]]], dict[str, list[str]], list[str]]:
    blocks_by_unit: dict[str, dict[str, Mapping[str, Any]]] = {}
    ordered_ids_by_unit: dict[str, list[str]] = {}
    unit_order: list[tuple[int, str]] = []
    all_block_ids: set[str] = set()
    for raw_unit in units:
        unit = _mapping(raw_unit, "source unit")
        unit_id = str(_field(unit, "sourceUnitId", "source_unit_id", default=""))
        if not unit_id or unit_id in blocks_by_unit:
            raise BrowserSourceReadbackError("source_content_browser_readback_invalid", detail="source unit identity is missing or duplicated")
        blocks_by_unit[unit_id] = {}
        ordered_blocks: list[tuple[int, str]] = []
        try:
            unit_order.append((int(_field(unit, "sourceIndex", "source_index", default=0)), unit_id))
            raw_blocks = _field(unit, "blocks", default=())
            if not isinstance(raw_blocks, Sequence) or isinstance(raw_blocks, (str, bytes)):
                raise ValueError("source blocks must be a sequence")
            for raw_block in raw_blocks:
                block = _mapping(raw_block, "source block")
                block_id = str(_field(block, "sourceBlockId", "source_block_id", default=""))
                if not block_id or block_id in all_block_ids:
                    raise ValueError("source block identity is missing or duplicated")
                all_block_ids.add(block_id)
                blocks_by_unit[unit_id][block_id] = block
                if str(block.get("kind", "")) in {"text", "image"}:
                    # Images are occurrences, not merely unique assets. Keep
                    # each occurrence in deterministic source reading order.
                    ordered_blocks.append((int(_field(block, "order", default=0)), block_id))
        except (TypeError, ValueError) as error:
            raise BrowserSourceReadbackError("source_content_browser_readback_invalid", detail=str(error)) from error
        ordered_ids_by_unit[unit_id] = [item[1] for item in sorted(ordered_blocks)]
    return blocks_by_unit, ordered_ids_by_unit, [item[1] for item in sorted(unit_order)]


def _check_slide_capture(metrics: Mapping[str, Any], html_source: str | None, index: int) -> tuple[float, float]:
    if html_source is not None:
        expected_html_digest = "sha256:" + hashlib.sha256(html_source.encode("utf-8")).hexdigest()
        admitted_source_digest = _field(metrics, "source_digest", "sourceDigest", default=None)
        admitted_render_digest = _field(metrics, "render_digest", "renderDigest", default=None)
        if (
            admitted_source_digest != expected_html_digest
            or not isinstance(admitted_render_digest, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", admitted_render_digest) is None
        ):
            raise BrowserSourceReadbackError(
                "source_content_browser_readback_failed",
                detail="browser capture identity does not match the rendered HTML",
                slide_indices=(index,),
            )
    if _field(metrics, "probe_error", "probeError", default=None) or _field(metrics, "fonts_ready", "fontsReady", default=None) is not True:
        raise BrowserSourceReadbackError("source_content_browser_readback_failed", detail="browser font/probe telemetry is incomplete", slide_indices=(index,))
    canvas_width, canvas_height = _number(metrics.get("width")), _number(metrics.get("height"))
    if canvas_width is None or canvas_height is None or canvas_width <= 0 or canvas_height <= 0:
        raise BrowserSourceReadbackError("source_content_browser_readback_failed", detail="browser canvas geometry is unavailable", slide_indices=(index,))
    return canvas_width, canvas_height


def _inspect_browser_source_readback(
    *, manifest: Any, outline: Any, browser_metrics: Any,
    htmls: Sequence[str] | None = None,
) -> BrowserSourceReadbackResult:
    # Import lazily so the implementation can reuse the facade's established
    # evidence types and low-level validators without an import cycle.
    from services.source_content.browser_readback_inspection import (
        inspect_browser_source_readback as inspect_impl,
    )

    return inspect_impl(
        manifest=manifest,
        outline=outline,
        browser_metrics=browser_metrics,
        htmls=htmls,
    )


def assert_deck_source_cardinality(*, manifest: Any, outline: Any, browser_metrics: Any, htmls: Sequence[str] | None = None) -> None:
    """Raise unless the deck has one rendered slide per source unit, in order.

    The per-slide admission gate can never see this: it inspects a one-unit
    manifest.  Publication therefore asserts the deck predicate once, through
    the same implementation the slide inspector uses.
    """
    from services.source_content.browser_readback_inspection import (
        assert_deck_source_cardinality as assert_impl,
    )

    assert_impl(manifest=manifest, outline=outline, browser_metrics=browser_metrics, htmls=htmls)


def inspect_browser_source_readback(*, manifest: Any, outline: Any, browser_metrics: Any, htmls: Sequence[str] | None = None) -> BrowserSourceReadbackResult:
    """Return diagnostics without turning damaged input into admission evidence.

    Visibility defects are retained separately from immutable source defects and
    incomplete telemetry. Structural/capture errors cannot be compared as an
    improving candidate and are represented by ``diagnostic_error``.
    """
    try:
        return _inspect_browser_source_readback(
            manifest=manifest, outline=outline, browser_metrics=browser_metrics, htmls=htmls
        )
    except BrowserSourceReadbackError as error:
        return BrowserSourceReadbackResult(
            0, 0, missing_block_ids=tuple(error.block_ids),
            order_violations=error.order_violations,
            failed_slide_indices=tuple(error.slide_indices), diagnostic_error=error.detail,
        )


def verify_browser_source_readback(*, manifest: Any, outline: Any, browser_metrics: Any, htmls: Sequence[str] | None = None) -> BrowserSourceReadbackResult:
    """Keep the existing strict browser verifier and its exception semantics."""
    result = _inspect_browser_source_readback(
        manifest=manifest, outline=outline, browser_metrics=browser_metrics, htmls=htmls
    )
    if not result.passed:
        summary = log_browser_source_readback_failure(result)
        raise BrowserSourceReadbackError("source_content_browser_readback_failed", detail=f"browser source text/geometry/visibility contract failed; {summary}", block_ids=tuple(dict.fromkeys((*result.missing_block_ids, *result.visibility_failures))), slide_indices=result.failed_slide_indices, order_violations=result.order_violations)
    return result


# Descriptive alias for callers that use the contract name in prose.
verify_source_verbatim_browser_readback = verify_browser_source_readback


__all__ = [
    "BrowserSourceBlockEvidence",
    "BrowserSourceReadbackError",
    "BrowserSourceReadbackResult",
    "assert_deck_source_cardinality",
    "deterministic_source_block_id",
    "deterministic_source_image_block_id",
    "deterministic_source_qid",
    "source_block_data_attribute",
    "stamp_source_block_ids",
    "stamp_source_image_block_ids",
    "verify_browser_source_readback",
    "inspect_browser_source_readback",
    "log_browser_source_readback_failure",
    "verify_source_verbatim_browser_readback",
]
