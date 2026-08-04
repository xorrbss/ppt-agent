"""Lossless authored-HTML importer for the default-on SlideSpec review path."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
import math
import re
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.slidespec_v1 import (
    SLIDESPEC_SCHEMA,
    AlignmentConstraint,
    AnchorConstraint,
    AssetManifest,
    Bounds,
    ContainmentConstraint,
    ContentNode,
    FontManifest,
    FocalPoint,
    GapConstraint,
    GroupConstraint,
    GridPlacement,
    ImportedHtmlBinding,
    ImportedHtmlSource,
    ImportedPixelBounds,
    Insets,
    LayoutConstraints,
    LayoutIntent,
    LayoutNode,
    NonOverlapConstraint,
    OrderConstraint,
    Provenance,
    ReadabilityConstraint,
    RenderRequest,
    RendererManifest,
    SlideDigests,
    SlideSpec,
)
from models.sql.slide import SlideModel
from services import presentation_version_service as version_service
from services.slidespec_artifact_store import (
    ACTIVE_POINTER_KEY,
    SlideSpecArtifactError,
    build_slidespec_artifact_bundle,
    load_active_slidespec_bundle,
    publish_slidespec_bundle_cas,
)
from services.slidespec_rollout_policy import resolve_slidespec_repair_rollout
from utils.authored_dom_quality import (
    build_compact_dom_map,
    ensure_authored_qids,
    inspect_authored_dom,
)
from utils.slide_capture import render_html_to_png_with_metrics
from utils.slidespec_core import ZERO_DIGEST, digest, seal_slidespec, validate_slidespec
from utils.slidespec_deck_constraints import evaluate_deck_consistency
from utils.slidespec_solver import solve_constraints


_BLOCKED_TAGS = frozenset({"script", "iframe", "object", "embed", "canvas"})
_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
)
_PX = re.compile(r"^\s*(-?(?:\d+(?:\.\d*)?|\.\d+))px\s*$", re.IGNORECASE)
_GRID_LINE = re.compile(r"^\s*(\d+)\s*(?:/\s*(?:span\s+)?(\d+))?\s*$", re.IGNORECASE)


class SlideSpecImportError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _flow_value(value: object, *, alignment: bool) -> str:
    normalized = str(value or "").strip().lower()
    mapping = {
        "flex-start": "start",
        "start": "start",
        "flex-end": "end",
        "end": "end",
        "center": "center",
        "stretch": "stretch",
        "space-between": "space-between",
    }
    parsed = mapping.get(normalized, "stretch" if alignment else "start")
    if alignment and parsed == "space-between":
        return "stretch"
    if not alignment and parsed == "stretch":
        return "start"
    return parsed


def _grid_line(value: object) -> tuple[int, int] | None:
    match = _GRID_LINE.fullmatch(str(value or ""))
    if match is None:
        return None
    start = max(0, int(match.group(1)) - 1)
    second = int(match.group(2) or 1)
    return start, max(
        1, second if "span" in str(value).lower() else second - (start + 1)
    )


def _grid_placement(style: Mapping[str, Any]) -> GridPlacement | None:
    column = _grid_line(style.get("gridColumn"))
    row = _grid_line(style.get("gridRow"))
    if column is None or row is None:
        return None
    return GridPlacement(
        row=row[0], column=column[0], rowSpan=row[1], columnSpan=column[1]
    )


def _column_ratio(value: object) -> tuple[float, ...]:
    parts = str(value or "").strip().split()
    pixels = [_px(part) for part in parts]
    if not pixels or any(item is None or item <= 0 for item in pixels):
        return ()
    minimum = min(item for item in pixels if item is not None)
    return tuple(round(item / minimum, 6) for item in pixels if item is not None)


def _text_fit_policy(style: Mapping[str, Any]) -> str:
    white_space = str(style.get("whiteSpace", "normal")).lower()
    overflow = str(style.get("overflow", "visible")).lower()
    text_overflow = str(style.get("textOverflow", "clip")).lower()
    if text_overflow == "ellipsis" and overflow in {"hidden", "clip"}:
        return "truncate"
    if white_space == "nowrap":
        return "none"
    return "wrap"


def _focal_point(value: object) -> tuple[float, float]:
    parts = str(value or "").strip().lower().split()
    keywords = {"left": 0.0, "top": 0.0, "center": 0.5, "right": 1.0, "bottom": 1.0}
    parsed: list[float] = []
    for part in parts[:2]:
        if part in keywords:
            parsed.append(keywords[part])
        elif part.endswith("%"):
            try:
                parsed.append(max(0, min(1, float(part[:-1]) / 100)))
            except ValueError:
                parsed.append(0.5)
        else:
            parsed.append(0.5)
    values = (parsed + [0.5, 0.5])[:2]
    return values[0], values[1]


@dataclass(frozen=True, slots=True)
class AuthoredImportExecution:
    slides: tuple[SlideModel, ...]
    imported_positions: tuple[int, ...]
    failures: dict[int, str]


class _AuthoredSourceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str | None]] = []
        self.text: dict[str, list[str]] = {}
        self.intentional_overlap: set[str] = set()
        self.blocked_tags: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in _BLOCKED_TAGS:
            self.blocked_tags.add(lowered)
        qid = next(
            (
                str(value or "").strip()
                for name, value in attrs
                if name.lower() == "data-qid"
            ),
            "",
        )
        if qid:
            self.text.setdefault(qid, [])
            if any(name.lower() == "data-allow-overlap" for name, _ in attrs):
                self.intentional_overlap.add(qid)
        if lowered not in _VOID_TAGS:
            self.stack.append((lowered, qid or None))

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        lowered = tag.lower()
        if lowered in _BLOCKED_TAGS:
            self.blocked_tags.add(lowered)
        qid = next(
            (
                str(value or "").strip()
                for name, value in attrs
                if name.lower() == "data-qid"
            ),
            "",
        )
        if qid:
            self.text.setdefault(qid, [])
            if any(name.lower() == "data-allow-overlap" for name, _ in attrs):
                self.intentional_overlap.add(qid)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == lowered:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        for tag, qid in reversed(self.stack):
            if tag in {"style", "script", "template", "noscript"}:
                return
            if qid:
                self.text[qid].append(data)
                return


def _stable_id(prefix: str, slide_id: uuid.UUID, qid: str) -> str:
    token = sha256(f"{slide_id}:{qid}".encode("utf-8")).hexdigest()[:24]
    return f"{prefix}import-{token}"


def _number(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _px(value: object) -> float | None:
    match = _PX.match(str(value or ""))
    return float(match.group(1)) if match else None


def _normalized_bounds(
    *, x: float, y: float, width: float, height: float, viewport_width: float,
    viewport_height: float,
) -> Bounds:
    minimum = 1e-6
    normalized_x = min(1 - minimum, max(0.0, x / viewport_width))
    normalized_y = min(1 - minimum, max(0.0, y / viewport_height))
    normalized_width = min(
        1 - normalized_x,
        max(minimum, width / viewport_width),
    )
    normalized_height = min(
        1 - normalized_y,
        max(minimum, height / viewport_height),
    )
    return Bounds(
        x=normalized_x,
        y=normalized_y,
        width=normalized_width,
        height=normalized_height,
    )


def _content_kind(tag: str, text: str) -> str:
    if tag in {"h1", "h2", "h3"}:
        return "title"
    if tag in {"ul", "ol", "li"}:
        return "list"
    if tag in {"table", "thead", "tbody", "tfoot", "tr", "th", "td"}:
        return "table"
    if tag == "img":
        return "image"
    if tag in {"svg", "path", "circle", "rect", "line", "polygon", "polyline"}:
        return "shape"
    return "text" if text else "group"


def _constraint_id(
    slide_id: uuid.UUID, kind: str, *parts: str
) -> str:
    token = sha256(
        f"{slide_id}:{kind}:{':'.join(parts)}".encode("utf-8")
    ).hexdigest()[:24]
    return f"con:import-{kind.lower()}-{token}"


def _infer_relational_constraints(
    *,
    slide_id: uuid.UUID,
    root_layout_id: str,
    contents: Sequence[ContentNode],
    bindings: Sequence[ImportedHtmlBinding],
    metric_by_qid: Mapping[str, Any],
    intentional_overlap: set[str],
    viewport_width: float,
    viewport_height: float,
) -> tuple[Any, ...]:
    """Infer only baseline-supported relationships from browser telemetry."""

    constraints: list[Any] = []
    binding_by_qid = {binding.qid: binding for binding in bindings}
    layout_by_semantic = {
        binding.semanticId: binding.layoutId for binding in bindings
    }
    content_by_layout = {
        layout_by_semantic[content.semanticId]: content
        for content in contents
        if content.semanticId in layout_by_semantic
    }
    protected = tuple(
        binding.layoutId
        for binding in bindings
        if content_by_layout[binding.layoutId].kind
        in {"title", "text", "list", "metric", "table"}
        and binding.qid not in intentional_overlap
    )
    if protected:
        constraints.append(
            ContainmentConstraint(
                constraintId=_constraint_id(slide_id, "containment", "canvas"),
                kind="containment",
                targets=protected,
                container=root_layout_id,
                tolerance=max(0.002, 2 / min(viewport_width, viewport_height)),
                source="imported",
                confidence=1,
            )
        )

    for binding in bindings[:256]:
        content = content_by_layout[binding.layoutId]
        if (
            content.kind not in {"title", "text", "list", "metric", "table"}
            or binding.fontSizePx is None
        ):
            continue
        minimum_scale = 0.9 if content.kind == "title" else 0.85
        constraints.append(
            ReadabilityConstraint(
                constraintId=_constraint_id(
                    slide_id, "readability", binding.qid
                ),
                kind="readability",
                targets=(binding.layoutId,),
                minFontSizePx=round(binding.fontSizePx * minimum_scale, 4),
                noTruncation=True,
                requireVisible=True,
                source="imported",
                confidence=1,
            )
        )

    ordered_layout_ids = tuple(
        layout_by_semantic[content.semanticId]
        for content in sorted(contents, key=lambda item: item.readingOrder)
        if content.semanticId in layout_by_semantic
    )
    for before, after in zip(ordered_layout_ids[:256], ordered_layout_ids[1:256]):
        constraints.append(
            OrderConstraint(
                constraintId=_constraint_id(slide_id, "order", before, after),
                kind="order",
                before=before,
                after=after,
                orderBy="reading",
                source="imported",
                confidence=1,
            )
        )

    children_by_parent: dict[str, list[ImportedHtmlBinding]] = {}
    for binding in bindings:
        metric = metric_by_qid.get(binding.qid)
        if not isinstance(metric, Mapping):
            continue
        parent_qid = str(metric.get("parent_qid") or "").strip()
        parent_metric = metric_by_qid.get(parent_qid)
        parent_style = (
            parent_metric.get("computed_style", parent_metric.get("style", {}))
            if isinstance(parent_metric, Mapping)
            else {}
        )
        parent_display = (
            str(parent_style.get("display", "block")).strip().lower()
            if isinstance(parent_style, Mapping)
            else "block"
        )
        if parent_qid:
            children_by_parent.setdefault(parent_qid, []).append(binding)

    relationship_budget = 256
    relationship_count = 0

    def append_relationship(constraint: Any) -> None:
        nonlocal relationship_count
        if relationship_count >= relationship_budget:
            return
        constraints.append(constraint)
        relationship_count += 1

    for parent_qid, siblings in sorted(children_by_parent.items()):
        siblings = [
            binding
            for binding in siblings
            if binding.qid not in intentional_overlap
        ]
        if len(siblings) < 2:
            continue
        members = tuple(binding.layoutId for binding in siblings[:128])
        append_relationship(
            GroupConstraint(
                constraintId=_constraint_id(slide_id, "group", parent_qid),
                kind="group",
                members=members,
                strength="soft",
                priority=70,
                confidence=0.85,
                source="imported",
            )
        )
        for edge, values in (
            ("left", [binding.sourceBoundsPx.x for binding in siblings]),
            ("top", [binding.sourceBoundsPx.y for binding in siblings]),
        ):
            scale = viewport_width if edge == "left" else viewport_height
            if max(values) - min(values) <= 2 and len(values) >= 2:
                append_relationship(
                    AlignmentConstraint(
                        constraintId=_constraint_id(
                            slide_id, "alignment", parent_qid, edge
                        ),
                        kind="alignment",
                        targets=members,
                        edge=edge,
                        tolerance=max(0.002, 2 / scale),
                        strength="soft",
                        priority=60,
                        confidence=0.9,
                        source="imported",
                    )
                )

        for binding in siblings:
            if binding.position not in {"absolute", "fixed", "sticky"}:
                continue
            if binding.leftPx is not None and 0 <= binding.bounds.x <= 1:
                append_relationship(
                    AnchorConstraint(
                        constraintId=_constraint_id(
                            slide_id, "anchor", binding.qid, "left"
                        ),
                        kind="anchor",
                        target=binding.layoutId,
                        container=root_layout_id,
                        targetEdge="left",
                        containerEdge="left",
                        offset=binding.bounds.x,
                        tolerance=max(0.002, 2 / viewport_width),
                        strength="soft",
                        priority=75,
                        confidence=0.9,
                        source="imported",
                    )
                )
            if binding.topPx is not None and 0 <= binding.bounds.y <= 1:
                append_relationship(
                    AnchorConstraint(
                        constraintId=_constraint_id(
                            slide_id, "anchor", binding.qid, "top"
                        ),
                        kind="anchor",
                        target=binding.layoutId,
                        container=root_layout_id,
                        targetEdge="top",
                        containerEdge="top",
                        offset=binding.bounds.y,
                        tolerance=max(0.002, 2 / viewport_height),
                        strength="soft",
                        priority=75,
                        confidence=0.9,
                        source="imported",
                    )
                )

        candidate_siblings = [
            binding
            for binding in siblings
            if content_by_layout[binding.layoutId].kind
            in {"title", "text", "list", "metric", "table", "image"}
        ]
        for index, left in enumerate(candidate_siblings):
            for right in candidate_siblings[index + 1 :]:
                first = left.sourceBoundsPx
                second = right.sourceBoundsPx
                vertical_overlap = min(
                    first.y + first.height, second.y + second.height
                ) - max(first.y, second.y)
                horizontal_overlap = min(
                    first.x + first.width, second.x + second.width
                ) - max(first.x, second.x)
                if vertical_overlap > min(first.height, second.height) * 0.5:
                    leading, trailing = (
                        (left, right) if first.x <= second.x else (right, left)
                    )
                    axis = "horizontal"
                    gap_px = (
                        trailing.sourceBoundsPx.x
                        - leading.sourceBoundsPx.x
                        - leading.sourceBoundsPx.width
                    )
                    scale = viewport_width
                    order_by = "x"
                elif horizontal_overlap > min(first.width, second.width) * 0.5:
                    leading, trailing = (
                        (left, right) if first.y <= second.y else (right, left)
                    )
                    axis = "vertical"
                    gap_px = (
                        trailing.sourceBoundsPx.y
                        - leading.sourceBoundsPx.y
                        - leading.sourceBoundsPx.height
                    )
                    scale = viewport_height
                    order_by = "y"
                else:
                    continue
                if gap_px < 0:
                    continue
                pair = (leading.layoutId, trailing.layoutId)
                append_relationship(
                    NonOverlapConstraint(
                        constraintId=_constraint_id(
                            slide_id, "nonoverlap", *pair
                        ),
                        kind="nonOverlap",
                        targets=pair,
                        tolerance=max(0.002, 2 / scale),
                        source="imported",
                        confidence=0.98,
                    )
                )
                append_relationship(
                    OrderConstraint(
                        constraintId=_constraint_id(
                            slide_id, "visual-order", *pair
                        ),
                        kind="order",
                        before=pair[0],
                        after=pair[1],
                        orderBy=order_by,
                        tolerance=max(0.002, 2 / scale),
                        source="imported",
                        confidence=0.98,
                    )
                )
                normalized_gap = gap_px / scale
                append_relationship(
                    GapConstraint(
                        constraintId=_constraint_id(slide_id, "gap", *pair),
                        kind="gap",
                        leading=pair[0],
                        trailing=pair[1],
                        axis=axis,
                        minimum=0,
                        maximum=min(1, normalized_gap * 2 + 8 / scale),
                        preferred=normalized_gap,
                        tolerance=max(0.002, 2 / scale),
                        strength="soft",
                        priority=55,
                        confidence=0.9,
                        source="imported",
                    )
                )

    return tuple(constraints[:1024])


async def build_authored_import_request(
    slide: SlideModel,
) -> tuple[RenderRequest, bytes, dict[str, Any]]:
    """Create a canonical spec whose baseline renderer is byte-identical HTML."""

    if not slide.html_content or not slide.html_content.strip():
        raise SlideSpecImportError("import.source.empty")
    enriched = ensure_authored_qids(slide.html_content)
    parser = _AuthoredSourceParser()
    parser.feed(enriched)
    parser.close()
    if parser.blocked_tags:
        raise SlideSpecImportError("import.source.active_content")

    image, metrics = await render_html_to_png_with_metrics(enriched)
    if not isinstance(metrics, Mapping):
        raise SlideSpecImportError("import.telemetry.incomplete")
    nodes = build_compact_dom_map(enriched, metrics)
    if not nodes:
        raise SlideSpecImportError("import.dom.empty")
    # Older authored documents can contain qids on non-editable void elements
    # (notably ``br``).  Chrome legitimately reports those elements, but they do
    # not belong to SlideSpec's editable target set.  Exclude only that known
    # superset before applying the strict completeness/uniqueness check; missing
    # or duplicated editable targets still fail closed.
    editable_qids = {node.qid for node in nodes}
    raw_elements = metrics.get("elements", metrics.get("nodes", ()))
    if not isinstance(raw_elements, Sequence) or isinstance(
        raw_elements, (str, bytes)
    ):
        raise SlideSpecImportError("import.telemetry.incomplete")
    review_metrics = dict(metrics)
    review_metrics["elements"] = [
        item
        for item in raw_elements
        if isinstance(item, Mapping)
        and str(item.get("qid", "")).strip() in editable_qids
    ]
    report = inspect_authored_dom(enriched, review_metrics)
    if report.telemetry_status != "available":
        raise SlideSpecImportError("import.telemetry.incomplete")

    viewport_width = _number(metrics.get("width"), 1280.0)
    viewport_height = _number(metrics.get("height"), 720.0)
    if viewport_width <= 0 or viewport_height <= 0:
        raise SlideSpecImportError("import.viewport.invalid")
    raw_metrics = metrics.get("elements", metrics.get("nodes", ()))
    metric_by_qid = {
        str(item.get("qid", "")).strip(): item
        for item in raw_metrics
        if isinstance(item, Mapping) and str(item.get("qid", "")).strip()
    }
    node_qids = {node.qid for node in nodes}
    semantic_by_qid = {
        node.qid: _stable_id("sem:", slide.id, node.qid) for node in nodes
    }
    layout_by_qid = {
        node.qid: _stable_id("lay:", slide.id, node.qid) for node in nodes
    }
    contents: list[ContentNode] = []
    layouts: list[LayoutNode] = []
    bindings: list[ImportedHtmlBinding] = []
    font_families: set[str] = set()

    for order, node in enumerate(nodes):
        metric = metric_by_qid.get(node.qid)
        if not isinstance(metric, Mapping) or node.bbox is None:
            raise SlideSpecImportError("import.metric.binding_missing")
        bbox = node.bbox
        bounds = _normalized_bounds(
            x=bbox.x,
            y=bbox.y,
            width=bbox.width,
            height=bbox.height,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
        )
        direct_text = " ".join("".join(parser.text.get(node.qid, ())).split())
        style = metric.get("computed_style", metric.get("style", {}))
        style = style if isinstance(style, Mapping) else {}
        display = str(style.get("display", "block")).strip().lower()
        position = str(style.get("position", "static")).strip().lower()
        if position not in {"static", "relative", "absolute", "fixed", "sticky"}:
            position = "static"
        font_size = _number(metric.get("font_size"), 0.0) or _px(style.get("fontSize"))
        font_family = str(style.get("fontFamily", "")).strip()
        if font_family:
            font_families.add(font_family)
        parent_qid = str(metric.get("parent_qid") or "").strip()
        parent_metric = metric_by_qid.get(parent_qid, {})
        parent_style = (
            parent_metric.get("computed_style", parent_metric.get("style", {}))
            if isinstance(parent_metric, Mapping)
            else {}
        )
        parent_style = parent_style if isinstance(parent_style, Mapping) else {}
        parent_display = str(parent_style.get("display", "block")).strip().lower()
        parent_semantic_id = (
            semantic_by_qid[parent_qid]
            if parent_qid in node_qids and parent_qid != node.qid
            else None
        )
        kind = _content_kind(node.tag, direct_text)
        gap_px = max(0, _px(style.get("gap")) or _px(style.get("rowGap")) or 0)
        padding_top = max(0, _px(style.get("paddingTop")) or 0)
        padding_right = max(0, _px(style.get("paddingRight")) or 0)
        padding_bottom = max(0, _px(style.get("paddingBottom")) or 0)
        padding_left = max(0, _px(style.get("paddingLeft")) or 0)
        alignment = _flow_value(style.get("alignItems"), alignment=True)
        justification = _flow_value(style.get("justifyContent"), alignment=False)
        column_ratio = (
            _column_ratio(style.get("gridTemplateColumns"))
            if display in {"grid", "inline-grid"}
            else ()
        )
        placement = (
            _grid_placement(style)
            if parent_display in {"grid", "inline-grid"}
            else None
        )
        text_fit = _text_fit_policy(style)
        static_probe = metric.get("static_translation_probe", {})
        static_probe = static_probe if isinstance(static_probe, Mapping) else {}
        static_translation_safe = (
            position == "static"
            and str(style.get("transform", "none")).strip() == "none"
            and static_probe.get("source") == "chrome"
            and static_probe.get("telemetryComplete") is True
            and static_probe.get("position") == "static"
            and static_probe.get("transformComposable") is True
            and static_probe.get("layoutIsolation") is True
            and abs(_number(static_probe.get("flowImpactPx"), float("inf"))) <= 0.5
            and _number(static_probe.get("maxSiblingDeltaPx"), float("inf")) <= 0.5
        )
        object_fit = str(style.get("objectFit", "fill")).strip().lower()
        if object_fit not in {"contain", "cover", "fill"}:
            object_fit = "fill"
        focal_x, focal_y = _focal_point(style.get("objectPosition"))
        allowed_operations = [
            "setBounds",
            "translateWithinParent",
            "resizeWithinBounds",
            "setPadding",
        ]
        if display in {"flex", "inline-flex", "grid", "inline-grid"}:
            allowed_operations.extend(
                ("setSpacing", "setAlignment", "setJustification")
            )
        if display in {"grid", "inline-grid"}:
            allowed_operations.extend(("setTrackSize", "setColumnRatio"))
        if parent_display in {"grid", "inline-grid"}:
            allowed_operations.append("setGridPlacement")
        if kind in {"title", "text", "list", "metric", "table"}:
            allowed_operations.extend(
                ("setFontScale", "setLineHeightToken", "setTextFitPolicy")
            )
        if kind == "image" and node.tag == "img":
            allowed_operations.extend(("setImageFit", "setCropFocalPoint"))
        contents.append(
            ContentNode(
                semanticId=semantic_by_qid[node.qid],
                kind=kind,
                text=direct_text or None,
                payload={"sourceTag": node.tag, "sourceQid": node.qid},
                parentSemanticId=parent_semantic_id,
                readingOrder=order,
            )
        )
        layouts.append(
            LayoutNode(
                layoutId=layout_by_qid[node.qid],
                kind="content",
                contentSemanticId=semantic_by_qid[node.qid],
                bounds=bounds,
                spacing=gap_px,
                padding=Insets(
                    top=min(1, padding_top / max(1, bbox.height)),
                    right=min(1, padding_right / max(1, bbox.width)),
                    bottom=min(1, padding_bottom / max(1, bbox.height)),
                    left=min(1, padding_left / max(1, bbox.width)),
                ),
                alignment=alignment,
                justification=justification,
                columnRatio=column_ratio,
                gridPlacement=placement,
                textFitPolicy=text_fit,
                imageFit=object_fit,
                cropFocalPoint=FocalPoint(x=focal_x, y=focal_y),
                intent=LayoutIntent(
                    role="primary" if kind == "title" else (
                        "decoration" if kind in {"image", "shape"} else "supporting"
                    ),
                    priority=100 if kind == "title" else 50,
                    allowIntentionalOverlap=node.qid in parser.intentional_overlap,
                ),
                constraints=LayoutConstraints(
                    allowedOperations=tuple(allowed_operations),
                    preserveAspectRatio=kind in {"image", "shape"},
                    minWidth=max(1e-6, bounds.width * 0.5),
                    minHeight=max(1e-6, bounds.height * 0.5),
                    maxWidth=min(1, bounds.width + 0.25),
                    maxHeight=min(1, bounds.height + 0.25),
                    minFontScale=0.9 if kind == "title" else 0.85,
                    maxFontShrink=0.1 if kind == "title" else 0.15,
                ),
            )
        )
        bindings.append(
            ImportedHtmlBinding(
                semanticId=semantic_by_qid[node.qid],
                layoutId=layout_by_qid[node.qid],
                qid=node.qid,
                tag=node.tag,
                bounds=bounds,
                sourceBoundsPx=ImportedPixelBounds(
                    x=bbox.x, y=bbox.y, width=bbox.width, height=bbox.height
                ),
                fontSizePx=font_size if font_size and font_size > 0 else None,
                position=position,
                transform=str(style.get("transform", "none")).strip() or "none",
                staticTranslationSafe=static_translation_safe,
                leftPx=_px(style.get("left")),
                topPx=_px(style.get("top")),
                display=display,
                gapPx=gap_px,
                paddingTopPx=padding_top,
                paddingRightPx=padding_right,
                paddingBottomPx=padding_bottom,
                paddingLeftPx=padding_left,
                alignItems=str(style.get("alignItems", "normal")),
                justifyContent=str(style.get("justifyContent", "normal")),
                gridTemplateColumns=str(style.get("gridTemplateColumns", "none")),
                gridColumn=str(style.get("gridColumn", "auto")),
                gridRow=str(style.get("gridRow", "auto")),
                columnRatio=column_ratio,
                gridPlacement=placement,
                lineHeight=str(style.get("lineHeight", "normal")),
                whiteSpace=str(style.get("whiteSpace", "normal")),
                overflow=str(style.get("overflow", "visible")),
                overflowWrap=str(style.get("overflowWrap", "normal")),
                textOverflow=str(style.get("textOverflow", "clip")),
                objectFit=str(style.get("objectFit", "fill")),
                objectPosition=str(style.get("objectPosition", "50% 50%")),
                textFitPolicy=text_fit,
                cropFocalPoint=FocalPoint(x=focal_x, y=focal_y),
            )
        )

    root_layout_id = _stable_id("lay:", slide.id, "$root")
    relational_constraints = _infer_relational_constraints(
        slide_id=slide.id,
        root_layout_id=root_layout_id,
        contents=contents,
        bindings=bindings,
        metric_by_qid=metric_by_qid,
        intentional_overlap=parser.intentional_overlap,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
    )
    spec = seal_slidespec(
        SlideSpec(
            schemaUri=SLIDESPEC_SCHEMA,
            specRevision=1,
            slideId=str(slide.id),
            content=tuple(contents),
            layout=LayoutNode(
                layoutId=root_layout_id,
                kind="canvas",
                children=tuple(layouts),
            ),
            constraints=relational_constraints,
            provenance=Provenance(
                source="authored-html-import",
                adapterVersion="authored-html-import/3",
            ),
            digests=SlideDigests(
                content=ZERO_DIGEST,
                hierarchy=ZERO_DIGEST,
                importantTokens=ZERO_DIGEST,
                spec=ZERO_DIGEST,
            ),
        )
    )
    validate_slidespec(spec)
    solver_result = solve_constraints(spec)
    fonts = tuple(sorted(font_families))
    source = ImportedHtmlSource(
        html=enriched,
        htmlHash=digest(enriched),
        viewportWidth=viewport_width,
        viewportHeight=viewport_height,
        bindings=tuple(bindings),
        importerVersion="authored-html-import/3",
    )
    request = RenderRequest(
        spec=spec,
        renderer=RendererManifest(
            name="slidespec-authored-html", version="1", runtime="browser"
        ),
        fonts=FontManifest(digest=digest(fonts), fonts=fonts),
        assets=AssetManifest(digest=digest(())),
        source=source,
    )
    constraint_kinds: dict[str, int] = {}
    for constraint in relational_constraints:
        constraint_kinds[constraint.kind] = constraint_kinds.get(constraint.kind, 0) + 1
    return request, image, {
        "confidence": 1.0,
        "telemetry_status": report.telemetry_status,
        "binding_count": len(bindings),
        "source_html_hash": source.htmlHash,
        "render_parity": "byte_identical_baseline",
        "constraint_count": len(relational_constraints),
        "constraint_kinds": constraint_kinds,
        "solver": solver_result.metrics(),
    }


async def ensure_authored_slidespec_imports(
    *,
    presentation_id: uuid.UUID,
    slides: Sequence[SlideModel],
    positions: Sequence[int],
    opt_in: bool,
    owner_id: object | None,
    session: AsyncSession,
) -> AuthoredImportExecution:
    """Activate all eligible authored slides; only explicit rollback skips it."""

    prepared: list[tuple[int, SlideModel, RenderRequest, bytes, dict[str, Any]]] = []
    failures: dict[int, str] = {}
    for position in positions:
        slide = slides[position]
        properties = slide.properties if isinstance(slide.properties, Mapping) else {}
        if ACTIVE_POINTER_KEY in properties:
            try:
                active_bundle = await load_active_slidespec_bundle(session, slide)
                source_manifest = (
                    active_bundle.manifest.get("source", {})
                    if active_bundle is not None
                    and isinstance(active_bundle.manifest, Mapping)
                    else {}
                )
                if (
                    isinstance(source_manifest, Mapping)
                    and source_manifest.get("importerVersion")
                    == "authored-html-import/3"
                ):
                    continue
            except SlideSpecArtifactError as error:
                # Presentation-version restore replaces slide rows and can
                # cascade-delete the immutable bundle while restoring the old
                # pointer JSON with the slide.  A pointer whose bundle is gone
                # cannot own the slide; re-import the exact live HTML as a new
                # baseline.  Other integrity failures remain fail-closed.
                if error.code != "artifact.pointer.missing":
                    failures[position] = error.code
                    continue
        rollout = resolve_slidespec_repair_rollout(
            presentation_id,
            slide_index=position,
            opt_in=opt_in,
            owner_id=owner_id,
        )
        if rollout.route != "slidespec_active":
            failures[position] = rollout.reason_code or "import.route.legacy"
            continue
        try:
            request, image, evidence = await build_authored_import_request(slide)
            prepared.append((position, slide, request, image, evidence))
        except Exception as error:
            failures[position] = getattr(error, "code", "import.failed")

    if prepared:
        deck_report = evaluate_deck_consistency(
            tuple((position, request) for position, _slide, request, _image, _evidence in prepared)
        )
        if deck_report.hard_failure:
            for position, *_rest in prepared:
                failures[position] = "import.deck_consistency.failed"
            prepared = []
        else:
            deck_violations = tuple(item.as_dict() for item in deck_report.violations)
            prepared = [
                (
                    position,
                    slide,
                    request,
                    image,
                    {
                        **evidence,
                        "deck_consistency": {
                            **deck_report.metrics(),
                            "violations": deck_violations,
                        },
                    },
                )
                for position, slide, request, image, evidence in prepared
            ]

    if prepared:
        try:
            await version_service.snapshot_slides(
                session,
                presentation_id,
                [slide.model_dump(mode="json") for slide in slides],
                label="SlideSpec 자동 활성화 전",
                force=True,
            )
            for position, slide, request, image, evidence in prepared:
                bundle = build_slidespec_artifact_bundle(
                    presentation_id=presentation_id,
                    slide=slide,
                    route="slidespec_active",
                    status="imported",
                    render_request=request,
                    evidence={"import": evidence, "slide_index": position},
                    image_bytes=image,
                )
                await publish_slidespec_bundle_cas(
                    session, slide=slide, bundle=bundle
                )
            await session.commit()
        except Exception:
            await session.rollback()
            for position, *_rest in prepared:
                failures[position] = "import.publish.failed"
            prepared = []

    refreshed = tuple(
        (
            await session.execute(
                select(SlideModel)
                .where(SlideModel.presentation == presentation_id)
                .order_by(SlideModel.index)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    return AuthoredImportExecution(
        slides=refreshed,
        imported_positions=tuple(position for position, *_rest in prepared),
        failures=failures,
    )


__all__ = [
    "AuthoredImportExecution",
    "SlideSpecImportError",
    "build_authored_import_request",
    "ensure_authored_slidespec_imports",
]
