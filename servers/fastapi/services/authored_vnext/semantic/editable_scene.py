"""Build the shadow editable-Hybrid Scene from declared semantics and telemetry."""

from __future__ import annotations

import base64
import binascii
import hashlib
from html.parser import HTMLParser
from io import BytesIO
import math
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from PIL import Image

from models.authored_vnext.deck import (
    SceneAssetManifestEntry,
    SceneCapability,
    SceneChartElement,
    SceneDataManifestEntry,
    SceneEditability,
    SceneImageElement,
    SceneOwnerDecision,
    SceneRasterIsland,
    SceneRasterZBand,
    SceneShapeElement,
    SceneTableElement,
    SceneTextElement,
    SceneTextRun,
    SceneTextStyle,
    SlideHybridExportPlan,
    scene_digest,
    seal_deck_scene,
    seal_slide_scene,
)
from models.authored_vnext.deck.scene import SCENE_OWNER_CAPABILITY_KINDS
from models.authored_vnext.master.contract import Rect
from models.sql.slide import SlideModel
from services.authored_vnext.generation.scene_compiler import default_scene_capabilities
from utils.authored_dom_quality import build_compact_dom_map, normalize_browser_metrics
from utils.slide_capture import get_cached_browser_metrics
from utils.asset_directory_utils import resolve_image_path_to_filesystem


PROFILE_ID = "authored-editable-hybrid-ko-en-basic-v1"
_TEXT_KINDS = {
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "span", "li", "label",
    "dt", "dd", "strong", "em", "small",
}
_CONTAINER_KINDS = {"main", "section", "article", "aside", "header", "footer", "nav", "ul", "ol"}
_ATOMIC_RASTER_FEATURES = {
    "filter", "mask", "clip-path", "gradient", "box-shadow", "transform",
    "svg", "canvas", "chart",
}
_MAX_INLINE_IMAGE_BYTES = 8 * 1024 * 1024
_REVIEWED_FONTS = (
    "Noto Sans KR",
    "DM Sans",
    "Source Serif 4",
    "IBM Plex Mono",
    "Malgun Gothic",
    "Batang",
    "Consolas",
    "Arial",
)

_JA_ZH_TEXT_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")
_CSS_CAPABILITY_BY_FEATURE = (
    (frozenset({"paint-closure", "z-band", "paint-order", "collision"}), "css.paint-closure.v1"),
    (frozenset({"background-plane", "background", "p:bg"}), "css.background-plane.v1"),
    (frozenset({"grid", "flex", "grid-flex"}), "css.grid-flex.v1"),
    (frozenset({"filter", "blend", "mix-blend-mode", "filter-blend"}), "css.filter-blend.v1"),
    (frozenset({"clip-path"}), "css.clip-path.v1"),
    (frozenset({"box-shadow", "shadow"}), "css.shadow.v1"),
    (frozenset({"gradient"}), "css.gradient.v1"),
)

_CHART_PALETTE = ("#2563EB", "#0EA5E9", "#7C3AED", "#059669")


def _bounded_native_chart_payload(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """Normalize the exact line/pie v1 semantic subset or reject to raster."""

    if not set(payload).issubset(
        {"chartType", "categories", "series", "chartStyle", "legend", "title"}
    ):
        return None
    chart_type = str(payload.get("chartType") or "").lower()
    categories = payload.get("categories")
    series = payload.get("series")
    if (
        chart_type not in {"line", "pie"}
        or not isinstance(categories, list)
        or not categories
        or len(categories) > 100
        or any(not isinstance(item, str) or not item or len(item) > 500 for item in categories)
        or not isinstance(series, list)
        or not series
        or len(series) > 30
        or chart_type == "pie" and len(series) != 1
        or payload.get("title") not in {None, ""}
    ):
        return None
    normalized_series: list[dict[str, Any]] = []
    colors: list[str] = []
    for index, raw_series in enumerate(series):
        if not isinstance(raw_series, Mapping) or not set(raw_series).issubset(
            {"name", "values", "color"}
        ):
            return None
        name = raw_series.get("name")
        values = raw_series.get("values")
        color = raw_series.get("color", _CHART_PALETTE[index % len(_CHART_PALETTE)])
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 500
            or not isinstance(values, list)
            or len(values) != len(categories)
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                for value in values
            )
            or not isinstance(color, str)
            or re.fullmatch(r"#[0-9A-Fa-f]{6}", color) is None
        ):
            return None
        normalized_series.append(
            {"name": name, "values": [float(value) for value in values]}
        )
        colors.append(color.upper())
    if len({item["name"] for item in normalized_series}) != len(normalized_series):
        return None
    chart_style = payload.get("chartStyle", 2)
    if isinstance(chart_style, bool) or not isinstance(chart_style, int) or not 1 <= chart_style <= 48:
        return None
    legend_value = payload.get("legend")
    if legend_value is None:
        legend = chart_type == "pie" or len(normalized_series) > 1
    elif isinstance(legend_value, bool):
        legend = legend_value
    elif isinstance(legend_value, Mapping) and set(legend_value) == {"visible"} and isinstance(legend_value.get("visible"), bool):
        legend = bool(legend_value["visible"])
    else:
        return None
    return {
        "categories": list(categories),
        "series": normalized_series,
        "style": {
            "chartStyle": chart_style,
            "seriesColors": colors,
            "legend": legend,
        },
    }


def _requested_capability_id(
    *,
    kind: str,
    tag: str,
    text: str,
    payload: Mapping[str, Any],
    required_features: set[str],
) -> str:
    """Select one exact versioned owner capability, most restrictive first."""

    for aliases, capability_id in _CSS_CAPABILITY_BY_FEATURE:
        if aliases.intersection(required_features):
            return capability_id
    if kind == "chart" or "chart" in required_features:
        chart_type = str(payload.get("chartType") or payload.get("type") or "").lower()
        return (
            "editable.chart.line-pie.v1"
            if chart_type in {"line", "pie"}
            else "editable.raster-island-integrity.v1"
        )
    if tag in _TEXT_KINDS:
        if _JA_ZH_TEXT_RE.search(text):
            return "editable.text.ja-zh.v1"
        if payload.get("runs") is not None or payload.get("autoFit") == "shrink" or required_features.intersection(
            {"shrink", "autofit", "zero-length-run", "rich-text", "run-style"}
        ):
            return "editable.text.advanced.v1"
        return "editable.text.ko-en.v1"
    if tag == "img":
        if required_features.intersection({"mask", "complex-crop", "image-mask"}):
            return "editable.image.advanced-crop-mask.v1"
        return "editable.image.rect-crop.v1"
    if tag == "table":
        if required_features.intersection(
            {"merged-cells", "row-height", "cell-border", "cell-fill", "cell-alignment"}
        ):
            return "editable.table.advanced.v1"
        return "editable.table.basic.v1"
    if required_features.intersection(
        {"group", "rotation", "clipping", "effect", "advanced-geometry", "transform"}
    ):
        return "editable.shape.advanced.v1"
    return "editable.shape.solid-basic.v1"


def _classes_mark_atomic_raster(classes: set[str]) -> bool:
    """Recognize the reviewed owner-level markers emitted by Authored HTML.

    Generated HTML uses descriptive root classes (``chart-panel`` and
    ``complex-css-owner``) instead of the shorter design-doc examples.  Keep
    the admission list narrow so labels such as ``swatch-chart`` stay native.
    Descendants are removed later through the declared parent chain.
    """

    return bool(
        {
            "chart",
            "chart-panel",
            "data-chart",
            "complex-island",
            "complex-css",
            "complex-css-owner",
        }.intersection(classes)
    )


def _is_declared_atomic_owner(
    owner_id: str,
    raw: Mapping[str, Any],
    attrs: Mapping[str, str],
) -> bool:
    required_features = {
        str(item).lower()
        for item in raw.get("requiredFeatures", [])
        if isinstance(item, str)
    }
    classes = set(attrs.get("class", "").lower().split())
    parent_id = str(raw.get("parentId") or "")
    owner_parts = set(owner_id.lower().split("."))
    parent_parts = set(parent_id.lower().split("."))
    chart_namespace_root = "chart" in owner_parts and "chart" not in parent_parts
    chart_root = chart_namespace_root and (
        owner_id.lower().startswith("chart.")
        or any(class_name.startswith("chart-") for class_name in classes)
    )
    complex_root = any(
        class_name == "complex" or class_name.startswith("complex-")
        for class_name in classes
    )
    explicit_raster = str(attrs.get("data-raster-fallback") or "").strip().lower()
    # A marker is an admission hint, not authority to rasterize an arbitrary
    # decorative owner.  The owner namespace/class must agree with the marker;
    # otherwise a model-added marker on an ordinary backplate would silently
    # turn a supported native slide into an unexpected raster fallback.
    explicit_marker_matches_owner = (
        explicit_raster == "chart" and (chart_root or "chart" in owner_parts)
    ) or (
        explicit_raster == "complex-css"
        and (
            complex_root
            or "complex" in owner_parts
            or "complex-css" in owner_id.lower()
            # The authoring contract names the bounded wrapper itself as a
            # raster island.  Require the explicit complex-css marker as well;
            # the class alone is never raster authority.
            or "raster-island" in classes
        )
    )
    return bool(
        required_features.intersection(_ATOMIC_RASTER_FEATURES)
        or _classes_mark_atomic_raster(classes)
        or chart_root
        or complex_root
        or explicit_marker_matches_owner
    )


class _NativeHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.attrs_by_qid: dict[str, dict[str, str]] = {}
        self._table_qid: str | None = None
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None
        self.tables: dict[str, list[list[str]]] = {}

    def handle_starttag(self, tag: str, attrs) -> None:
        values = {str(key).lower(): str(value or "") for key, value in attrs}
        qid = values.get("data-qid")
        if qid:
            self.attrs_by_qid[qid] = {"tag": tag.lower(), **values}
        if tag.lower() == "table" and qid:
            self._table_qid = qid
            self.tables[qid] = []
        elif self._table_qid and tag.lower() == "tr":
            self._row = []
        elif self._table_qid and tag.lower() in {"td", "th"}:
            self._cell_parts = []

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"td", "th"} and self._cell_parts is not None:
            if self._row is not None:
                self._row.append(" ".join("".join(self._cell_parts).split()))
            self._cell_parts = None
        elif lowered == "tr" and self._row is not None:
            if self._table_qid and self._row:
                self.tables[self._table_qid].append(self._row)
            self._row = None
        elif lowered == "table":
            self._table_qid = None


def _number(value: object, default: float = 0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _css_px(value: object) -> float | None:
    text = str(value or "").strip().lower()
    if text.endswith("px"):
        text = text[:-2].strip()
    try:
        result = float(text)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _vertical_text_anchor(style: Mapping[str, object]) -> str:
    display = str(style.get("display") or "").lower()
    direction = str(style.get("flexDirection") or "row").lower()
    if "grid" in display:
        value = str(style.get("alignItems") or "").lower()
    elif "flex" in display:
        value = str(
            style.get("justifyContent")
            if direction.startswith("column")
            else style.get("alignItems")
        ).lower()
    else:
        value = ""
    if value in {"center", "middle"}:
        return "middle"
    if value in {"end", "flex-end"}:
        return "bottom"
    return "top"


def _bounds(node) -> Rect | None:
    bbox = node.bbox
    if bbox is None or bbox.width <= 0 or bbox.height <= 0:
        return None
    # Scene geometry follows the canonical 1280 x 720 CSS-pixel coordinate
    # space.  The native PPTX adapter is the single authority that converts
    # those values to points/EMU.  Converting here as well compresses every
    # object to 75% on export and breaks the authored line wrapping.
    return Rect(
        x=max(0.0, bbox.x),
        y=max(0.0, bbox.y),
        width=bbox.width,
        height=bbox.height,
    )


def _z(node, fallback: int) -> int:
    value = str(node.style.get("zIndex", "")).strip()
    if value.lstrip("-").isdigit():
        return max(-99_999, min(99_999, int(value)))
    return fallback


def _hex_color(value: object) -> str | None:
    text = str(value or "").strip()
    if len(text) == 7 and text.startswith("#"):
        try:
            int(text[1:], 16)
        except ValueError:
            return None
        return text.upper()
    if text.startswith("rgb(") and text.endswith(")"):
        parts = [item.strip() for item in text[4:-1].split(",")]
        if len(parts) == 3 and all(item.isdigit() for item in parts):
            values = [int(item) for item in parts]
            if all(0 <= item <= 255 for item in values):
                return "#" + "".join(f"{item:02X}" for item in values)
    return None


def _semantic_text(element: Mapping[str, Any], fallback: str) -> tuple[str, SceneTextStyle]:
    payload = element.get("payload")
    paragraphs = payload.get("paragraphs") if isinstance(payload, Mapping) else None
    texts: list[str] = []
    first_run: Mapping[str, Any] = {}
    alignment = None
    if isinstance(paragraphs, list):
        for paragraph in paragraphs:
            if not isinstance(paragraph, Mapping):
                continue
            runs = paragraph.get("runs")
            if isinstance(runs, list):
                if not first_run:
                    first_run = next((run for run in runs if isinstance(run, Mapping)), {})
                texts.append("".join(str(run.get("text", "")) for run in runs if isinstance(run, Mapping)))
            alignment = alignment or paragraph.get("alignment")
    text = "\n".join(texts).strip() or fallback.strip()
    return text, SceneTextStyle(
        font_family=str(first_run.get("fontFamily") or "Malgun Gothic"),
        font_size=_number(first_run.get("fontSizePt"), 18),
        font_weight=int(_number(first_run.get("weight"), 400)),
        color=_hex_color(first_run.get("color")) or "#111827",
        text_align=alignment if alignment in {"left", "center", "right", "justify"} else "left",
    )


def _reviewed_text_style(node) -> SceneTextStyle | None:
    family_value = str(node.style.get("fontFamily", ""))
    family = next((item for item in _REVIEWED_FONTS if item.casefold() in family_value.casefold()), None)
    if family is None:
        return None
    text = node.text
    if any(
        (0x3040 <= ord(char) <= 0x30FF)
        or (0x3400 <= ord(char) <= 0x9FFF)
        for char in text
    ):
        return None
    weight = int(_number(node.style.get("fontWeight"), 400))
    weight = min(900, max(100, round(weight / 100) * 100))
    align = str(node.style.get("textAlign", "left"))
    font_size_px = _css_px(node.style.get("fontSize")) or 18
    line_height_px = _css_px(node.style.get("lineHeight"))
    line_height = (
        line_height_px / font_size_px
        if line_height_px is not None and font_size_px > 0
        else None
    )
    return SceneTextStyle(
        font_family=family,
        # CSS px and PowerPoint pt are different physical units.  Conversion
        # happens exactly once here; native_scene consumes sealed pt values.
        font_size=max(1, font_size_px * 0.75),
        font_weight=weight,
        color=_hex_color(node.style.get("color")) or "#111827",
        text_align=align if align in {"left", "center", "right", "justify"} else "left",
        vertical_align=_vertical_text_anchor(node.style),
        line_height=line_height,
        margin_left_pt=(_css_px(node.style.get("paddingLeft")) or 0) * 0.75,
        margin_right_pt=(_css_px(node.style.get("paddingRight")) or 0) * 0.75,
        margin_top_pt=(_css_px(node.style.get("paddingTop")) or 0) * 0.75,
        margin_bottom_pt=(_css_px(node.style.get("paddingBottom")) or 0) * 0.75,
    )


def _resolved_image_asset(src: str) -> tuple[SceneAssetManifestEntry, tuple[int, int]] | None:
    media_type: str | None = None
    content: bytes
    source = str(src or "").strip()
    if source.startswith("data:"):
        header, separator, encoded = source.partition(",")
        accepted = {
            "data:image/png;base64": "image/png",
            "data:image/jpeg;base64": "image/jpeg",
        }
        media_type = accepted.get(header.lower()) if separator else None
        if media_type is None:
            return None
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return None
        if not content or len(content) > _MAX_INLINE_IMAGE_BYTES:
            return None
    else:
        path = resolve_image_path_to_filesystem(source)
        if path is None:
            return None
        try:
            content = Path(path).read_bytes()
        except OSError:
            return None
    try:
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        with Image.open(BytesIO(content)) as image:
            decoded_media_type = {"PNG": "image/png", "JPEG": "image/jpeg"}.get(str(image.format).upper())
            size = image.size
    except (OSError, ValueError):
        return None
    if decoded_media_type is None or (
        media_type is not None and media_type != decoded_media_type
    ):
        return None
    media_type = decoded_media_type
    asset_id = f"asset:{digest.removeprefix('sha256:')[:48]}"
    return SceneAssetManifestEntry(
        asset_id=asset_id,
        asset_digest=digest,
        media_type=media_type,
        storage_reference=f"data:{media_type};base64," + base64.b64encode(content).decode("ascii"),
        size_bytes=len(content),
    ), size


def _cover_crop(source_size: tuple[int, int], bounds: Rect) -> dict[str, float]:
    source_width, source_height = source_size
    source_aspect = source_width / source_height
    box_aspect = bounds.width / bounds.height
    if source_aspect > box_aspect:
        visible = box_aspect / source_aspect
        inset = (1 - visible) / 2
        return {"left": inset, "top": 0, "right": inset, "bottom": 0}
    visible = source_aspect / box_aspect
    inset = (1 - visible) / 2
    return {"left": 0, "top": inset, "right": 0, "bottom": inset}


def _raster_asset(
    png: bytes,
    *,
    slide_id: str,
    owner_id: str,
    bounds: Rect,
) -> tuple[SceneAssetManifestEntry, str]:
    with Image.open(BytesIO(png)) as image:
        left = max(0, int(round(bounds.x)))
        top = max(0, int(round(bounds.y)))
        right = min(image.width, max(left + 1, int(round(bounds.right))))
        bottom = min(image.height, max(top + 1, int(round(bounds.bottom))))
        fragment = image.convert("RGBA").crop((left, top, right, bottom))
        output = BytesIO()
        fragment.save(output, format="PNG")
    content = output.getvalue()
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in owner_id.lower())
    asset_id = f"asset:raster-{slide_id.removeprefix('slide:')[:16]}-{safe[:48]}"
    return SceneAssetManifestEntry(
        asset_id=asset_id,
        asset_digest=digest,
        media_type="image/png",
        storage_reference="data:image/png;base64," + base64.b64encode(content).decode("ascii"),
        size_bytes=len(content),
    ), asset_id


def build_editable_hybrid_scene(
    *,
    presentation_id: str,
    revision: int,
    slides: Sequence[SlideModel] | None,
    htmls: Sequence[str],
    pngs: Sequence[bytes],
    semantic_drafts: Sequence[Mapping[str, Any]],
    composition_digest: str,
    deck_id: str | None = None,
    slide_ids: Sequence[str] | None = None,
    source_digest_override: str | None = None,
):
    """Create an immutable shadow Scene; identity/geometry failures block owners."""

    deck_id = deck_id or f"deck:semantic-{presentation_id.replace('-', '').lower()}"
    if slide_ids is None:
        if slides is None:
            raise ValueError("editable Hybrid Scene requires slide identities")
        slide_ids = [f"slide:{slide.id.hex}" for slide in slides]
    if len(slide_ids) != len(htmls):
        raise ValueError("editable Hybrid Scene slide identities differ from HTML")
    scene_slides = []
    assets: list[SceneAssetManifestEntry] = []
    data: list[SceneDataManifestEntry] = []
    for index, (slide_id, html, png, draft) in enumerate(
        zip(slide_ids, htmls, pngs, semantic_drafts, strict=True)
    ):
        metrics = normalize_browser_metrics(get_cached_browser_metrics(html))
        nodes = {node.qid: node for node in build_compact_dom_map(html, metrics)}
        parser = _NativeHtmlParser()
        parser.feed(html)
        parser.close()
        source = draft.get("semanticSource")
        declared = source.get("elements") if isinstance(source, Mapping) else None
        declared = declared if isinstance(declared, list) else []
        declared_by_id = {
            str(item.get("elementId", "")): item
            for item in declared
            if isinstance(item, Mapping) and item.get("elementId")
        }
        atomic_owner_ids: set[str] = set()
        for owner_id, raw in declared_by_id.items():
            attrs = parser.attrs_by_qid.get(owner_id, {})
            if attrs.get("tag") == "table" or (
                _is_declared_atomic_owner(owner_id, raw, attrs)
            ):
                atomic_owner_ids.add(owner_id)

        # HTML rich text is represented by a parent text owner plus styled
        # descendant spans.  The MVP Scene has one style per text object, so
        # emitting both owners duplicates the span text in PowerPoint.  Keep
        # the parent as the single native object until rich-text runs are part
        # of the supported profile.
        text_owner_ids = {
            owner_id
            for owner_id in declared_by_id
            if parser.attrs_by_qid.get(owner_id, {}).get("tag") in _TEXT_KINDS
        }
        text_descendants: set[str] = set()
        for owner_id in text_owner_ids:
            current = str(declared_by_id.get(owner_id, {}).get("parentId") or "")
            seen: set[str] = set()
            while current and current not in seen:
                if current in text_owner_ids:
                    text_descendants.add(owner_id)
                    break
                seen.add(current)
                current = str(
                    declared_by_id.get(current, {}).get("parentId") or ""
                )

        def inside_other_atomic_owner(owner_id: str) -> bool:
            current = str(declared_by_id.get(owner_id, {}).get("parentId") or "")
            seen: set[str] = set()
            while current and current not in seen:
                if current in atomic_owner_ids:
                    return True
                seen.add(current)
                current = str(
                    declared_by_id.get(current, {}).get("parentId") or ""
                )
            return False

        elements = []
        decisions = []
        islands = []
        for order, raw in enumerate(declared):
            if not isinstance(raw, Mapping):
                continue
            owner_id = str(raw.get("elementId", ""))
            if inside_other_atomic_owner(owner_id):
                continue
            node = nodes.get(owner_id)
            bounds = _bounds(node) if node is not None else None
            kind = str(raw.get("kind", "complexIsland"))
            semantic_role = str(raw.get("semanticRole") or "body")
            z_order = _z(node, order) if node is not None else order
            attrs = parser.attrs_by_qid.get(owner_id, {})
            tag = attrs.get("tag", "")
            if owner_id in text_descendants:
                continue
            if str(attrs.get("data-header-part") or "").strip().lower() == "eyebrow":
                # Authored master furniture already owns the running header;
                # the body eyebrow would otherwise be rendered on top of it.
                continue
            classes = set(attrs.get("class", "").lower().split())
            required_features = {
                str(item).lower()
                for item in raw.get("requiredFeatures", [])
                if isinstance(item, str)
            }
            payload = raw.get("payload") if isinstance(raw.get("payload"), Mapping) else {}
            requested_capability = _requested_capability_id(
                kind=kind,
                tag=tag,
                text=(node.text if node is not None else ""),
                payload=payload,
                required_features=required_features,
            )
            fill = (
                _hex_color(payload.get("fill"))
                or (_hex_color(node.style.get("backgroundColor")) if node is not None else None)
            )
            is_table = tag == "table"
            is_image = tag == "img"
            is_chart = kind == "chart" or requested_capability == "editable.chart.line-pie.v1"
            is_text = tag in _TEXT_KINDS and node is not None and bool(node.text.strip())
            is_atomic_raster = (
                owner_id in atomic_owner_ids
                or requested_capability
                in {
                    "editable.text.ja-zh.v1",
                    "editable.text.advanced.v1",
                    "editable.image.advanced-crop-mask.v1",
                    "editable.table.advanced.v1",
                    "editable.shape.advanced.v1",
                }
            ) and not (
                is_table and requested_capability == "editable.table.basic.v1"
            )
            is_visible_shape = (
                not required_features.intersection(_ATOMIC_RASTER_FEATURES)
                and (kind == "shape" or tag in _CONTAINER_KINDS)
                and fill is not None
            )
            if not (
                is_table
                or is_image
                or is_chart
                or is_text
                or is_atomic_raster
                or is_visible_shape
            ):
                # Containers, borders, labels without owned text, and other
                # decorations are fragments, not logical semantic owners.
                continue
            if (
                bounds is None
                and is_visible_shape
                and not is_atomic_raster
                and not is_text
                and not is_image
                and not is_table
            ):
                # Hairlines and other purely decorative filled fragments can be
                # omitted when the browser probe reports no usable geometry.
                # They are not semantic content and must not block a deck whose
                # real text/image/table owners have complete telemetry.
                continue
            decision = "publicationBlocked" if bounds is None else "rasterIsland"
            capability = requested_capability
            reason = "scene_geometry_unavailable" if bounds is None else str(
                raw.get("rasterReason") or "unsupported_representation"
            )
            element = None
            if (
                bounds is not None
                and node is not None
                and is_text
                and requested_capability == "editable.text.ko-en.v1"
            ):
                text = node.text.strip()
                style = _reviewed_text_style(node)
                if text and style is not None:
                    element = SceneTextElement(
                        kind="text", element_id=owner_id, z_order=z_order, bounds=bounds,
                        semantic_role=semantic_role, editability=SceneEditability(text=True, geometry=True, style=True),
                        text=text, style=style,
                    )
                    decision, capability, reason = "nativeEditable", requested_capability, None
            elif (
                bounds is not None
                and node is not None
                and is_text
                and requested_capability == "editable.text.advanced.v1"
            ):
                text = node.text.strip()
                style = _reviewed_text_style(node)
                raw_runs = payload.get("runs")
                try:
                    if style is None or payload.get("autoFit") not in {None, "shrink"}:
                        raise ValueError("advanced text style is not admitted")
                    style_values = style.model_dump(mode="json", by_alias=False)
                    style_values["auto_fit"] = payload.get("autoFit", "none")
                    advanced_style = SceneTextStyle.model_validate(style_values)
                    if raw_runs is None:
                        runs = [SceneTextRun(run_id=f"run:{owner_id}.0", text=text)]
                    else:
                        if not isinstance(raw_runs, list) or not raw_runs:
                            raise ValueError("advanced text runs are invalid")
                        runs = [SceneTextRun.model_validate(run) for run in raw_runs]
                    if "".join(run.text for run in runs) != text:
                        raise ValueError("advanced text runs differ from visible text")
                except (TypeError, ValueError):
                    runs = []
                if runs:
                    element = SceneTextElement(
                        kind="text",
                        element_id=owner_id,
                        z_order=z_order,
                        bounds=bounds,
                        semantic_role=semantic_role,
                        editability=SceneEditability(text=True, geometry=True, style=True),
                        text=text,
                        style=advanced_style,
                        runs=runs,
                    )
                    decision, capability, reason = "nativeEditable", requested_capability, None
            elif (
                bounds is not None
                and node is not None
                and is_visible_shape
                and not is_atomic_raster
                and requested_capability == "editable.shape.solid-basic.v1"
            ):
                preset = {"rectangle": "rect", "roundedRectangle": "roundRect", "ellipse": "ellipse", "line": "line"}.get(str(payload.get("preset")), "rect")
                stroke = _hex_color(payload.get("stroke")) or _hex_color(node.style.get("borderColor"))
                element = SceneShapeElement(
                    kind="shape", element_id=owner_id, z_order=z_order, bounds=bounds,
                    semantic_role=semantic_role, editability=SceneEditability(geometry=True, style=True),
                    shape=preset, fill=fill, stroke=stroke,
                )
                decision, capability, reason = "nativeEditable", requested_capability, None
            elif (
                bounds is not None
                and node is not None
                and is_image
                and requested_capability == "editable.image.rect-crop.v1"
            ):
                resolved = _resolved_image_asset(attrs.get("src", ""))
                if resolved is not None:
                    asset, source_size = resolved
                    assets.append(asset)
                    element = SceneImageElement(
                        kind="image", element_id=owner_id, z_order=z_order, bounds=bounds,
                        semantic_role=semantic_role, editability=SceneEditability(geometry=True, asset=True),
                        asset_id=asset.asset_id, asset_digest=asset.asset_digest,
                        alt_text=attrs.get("alt") or "Authored image",
                        crop="cover", crop_geometry=_cover_crop(source_size, bounds),
                    )
                    decision, capability, reason = "nativeEditable", requested_capability, None
            elif (
                bounds is not None
                and node is not None
                and is_chart
                and requested_capability == "editable.chart.line-pie.v1"
            ):
                chart_payload = _bounded_native_chart_payload(payload)
                if chart_payload is not None:
                    digest = scene_digest(chart_payload)
                    data_id = f"data:{digest.removeprefix('sha256:')[:48]}"
                    data.append(
                        SceneDataManifestEntry(
                            data_id=data_id,
                            data_digest=digest,
                            kind="chart",
                            payload=chart_payload,
                        )
                    )
                    chart_type = str(payload["chartType"]).lower()
                    element = SceneChartElement(
                        kind="chart",
                        element_id=owner_id,
                        z_order=z_order,
                        bounds=bounds,
                        semantic_role=semantic_role,
                        editability=SceneEditability(geometry=True, data=True),
                        data_id=data_id,
                        data_digest=digest,
                        chart_type=chart_type,
                        accessibility_summary=f"{chart_type} chart with {len(chart_payload['categories'])} categories",
                    )
                    decision, capability, reason = (
                        "nativeEditable",
                        requested_capability,
                        None,
                    )
            elif (
                bounds is not None
                and node is not None
                and is_visible_shape
                and requested_capability == "editable.shape.advanced.v1"
                and not required_features.intersection(
                    {"group", "clipping", "clip-path", "effect", "filter", "blend", "transform"}
                )
            ):
                preset = {
                    "triangle": "triangle",
                    "diamond": "diamond",
                    "chevron": "chevron",
                }.get(str(payload.get("preset")))
                stroke = _hex_color(payload.get("stroke")) or _hex_color(node.style.get("borderColor"))
                if preset is not None:
                    element = SceneShapeElement(
                        kind="shape",
                        element_id=owner_id,
                        z_order=z_order,
                        bounds=bounds,
                        rotation=float(payload.get("rotation", 0)),
                        semantic_role=semantic_role,
                        editability=SceneEditability(geometry=True, style=True),
                        shape=preset,
                        fill=fill,
                        stroke=stroke,
                    )
                    decision, capability, reason = "nativeEditable", requested_capability, None
            elif (
                bounds is not None
                and node is not None
                and is_table
                and requested_capability in {"editable.table.basic.v1", "editable.table.advanced.v1"}
            ):
                cells = parser.tables.get(owner_id)
                if cells and all(len(row) == len(cells[0]) for row in cells):
                    data_payload: dict[str, Any] = {"cells": cells}
                    table_width_pt = bounds.width * 0.75
                    data_payload["columnWidthsPt"] = payload.get(
                        "columnWidthsPt",
                        [table_width_pt / len(cells[0])] * len(cells[0]),
                    )
                    if requested_capability == "editable.table.advanced.v1":
                        for key in ("cellStyles", "rowHeightsPt", "mergedCells"):
                            if key in payload:
                                data_payload[key] = payload[key]
                    try:
                        digest = scene_digest(data_payload)
                        data_id = f"data:{digest.removeprefix('sha256:')[:48]}"
                        entry = SceneDataManifestEntry(
                            data_id=data_id,
                            data_digest=digest,
                            kind="table",
                            payload=data_payload,
                        )
                    except (TypeError, ValueError):
                        entry = None
                    if entry is not None:
                        data.append(entry)
                        element = SceneTableElement(
                            kind="table", element_id=owner_id, z_order=z_order, bounds=bounds,
                            semantic_role=semantic_role, editability=SceneEditability(geometry=True, data=True),
                            data_id=data_id, data_digest=digest, rows=len(cells), columns=len(cells[0]),
                        )
                        decision, capability, reason = "nativeEditable", requested_capability, None
            if element is None:
                placeholder = bounds or Rect(x=0, y=0, width=1, height=1)
                element = SceneShapeElement(
                    kind="shape", element_id=owner_id, z_order=z_order, bounds=placeholder,
                    semantic_role=semantic_role, editability=SceneEditability(), shape="rect",
                    fill=None, stroke=None,
                )
                if "shape" not in SCENE_OWNER_CAPABILITY_KINDS[capability]:
                    # A raster/publication-blocked owner is represented by a
                    # shape placeholder in Scene IR. Preserve the QID, role,
                    # geometry, reason, and raster pixels, but use the closed
                    # raster-integrity capability when the requested native
                    # capability cannot legally describe a shape. Otherwise
                    # seal_slide_scene rejects the owner before publication.
                    capability = "editable.raster-island-integrity.v1"
                if decision == "rasterIsland":
                    asset, asset_id = _raster_asset(png, slide_id=slide_id, owner_id=owner_id, bounds=placeholder)
                    assets.append(asset)
                    islands.append(SceneRasterIsland(
                        island_id=f"island:{slide_id.removeprefix('slide:')}.{order}",
                        source_owner_ids=[owner_id], asset_id=asset_id,
                        asset_digest=asset.asset_digest, bounds=placeholder,
                        z_band=SceneRasterZBand(min_z=z_order, max_z=z_order),
                        raster_plane_digest=asset.asset_digest,
                    ))
            elements.append(element)
            decisions.append(SceneOwnerDecision(
                owner_id=owner_id, decision=decision, capability_id=capability,
                reason_code=reason,
            ))
        if not elements:
            raise ValueError("editable Hybrid Scene requires declared semantic QIDs")
        scene_slides.append(seal_slide_scene(
            slide_id=slide_id,
            revision=revision,
            elements=elements,
            hybrid_export=SlideHybridExportPlan(
                owner_decisions=decisions,
                raster_islands=islands,
            ),
        ))
    source_digest = source_digest_override or scene_digest({
        "presentationId": presentation_id,
        "revision": revision,
        "htmlDigests": [scene_digest({"html": html}) for html in htmls],
    })
    assets_by_id: dict[str, SceneAssetManifestEntry] = {}
    for asset in assets:
        current = assets_by_id.get(asset.asset_id)
        if current is not None and current != asset:
            raise ValueError("editable Hybrid Scene asset identity conflict")
        assets_by_id[asset.asset_id] = asset
    data_by_id: dict[str, SceneDataManifestEntry] = {}
    for entry in data:
        current = data_by_id.get(entry.data_id)
        if current is not None and current != entry:
            raise ValueError("editable Hybrid Scene data identity conflict")
        data_by_id[entry.data_id] = entry
    return seal_deck_scene(
        deck_id=deck_id,
        revision=revision,
        composition_digest=composition_digest,
        source_digest=source_digest,
        hybrid_profile_id=PROFILE_ID,
        slides=scene_slides,
        asset_manifest=sorted(assets_by_id.values(), key=lambda item: item.asset_id),
        data_manifest=sorted(data_by_id.values(), key=lambda item: item.data_id),
        capabilities=list(default_scene_capabilities()),
    )


__all__ = ["PROFILE_ID", "build_editable_hybrid_scene"]
