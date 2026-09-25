"""Pre-capture source-native placement on a canonical document canvas."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Mapping

from lxml import html as lxml_html

from services.document_canvas import canvas_digest, parse_canvas_metadata
from services.document_canvas_contain import CanvasRect, ContainPlan, build_contain_plan
from services.document_canvas_contain import (
    validate_contain_css_typography,
    validate_contain_typography,
)
from .source_native_target_fit import SOURCE_ROLE_FONT_EXCEPTIONS


TRANSFORM_VERSION = "source-native-css-matrix/v1"
RECEIPT_VERSION = "document-canvas-contain-receipt/v1"
_VIEWPORT_DEPENDENT_CSS = re.compile(
    r"(?:@media\b|@container\b|(?<![A-Za-z0-9_-])(?:\d+(?:\.\d+)?|\.\d+)\s*(?:vw|vh|vmin|vmax)\b)",
    re.IGNORECASE,
)
DENSE_SOURCE_MIN_FONT_CSS_PX = 7.0
DENSE_SOURCE_FONT_POLICY_ID = "composer-source-preservation-v2/dense-7-css-px"
SOURCE_NATIVE_MIN_BODY_FONT_PT = 12.0
SOURCE_NATIVE_FONT_POLICY_ID = "source-native/min-body-12-physical-pt"


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class SourceNativeCanvasPlacement:
    plan: ContainPlan
    target_canvas_digest: str
    source_geometry_digest: str


@dataclass(frozen=True, slots=True)
class SourceNativeFontPolicy:
    policy_id: str
    unit: str
    minimum_target_css_px: float
    minimum_physical_pt: float


@dataclass(frozen=True, slots=True)
class SourceNativeCanvasSession:
    placement: SourceNativeCanvasPlacement
    policy: SourceNativeFontPolicy

    @classmethod
    def from_request(
        cls, *, request: object, design_canvas: object
    ) -> "SourceNativeCanvasSession | None":
        target = getattr(request, "_document_canvas", None)
        if target is None:
            return None
        placement = build_source_native_canvas_placement(
            design_canvas=design_canvas, target_canvas=target
        )
        return cls(
            placement,
            source_native_font_policy(
                source_mode=getattr(request, "source_mode", None),
                placement=placement,
            ),
        )

    @property
    def capture_kwargs(self) -> dict[str, int]:
        plan = self.placement.plan
        return {"width_px": plan.target_width, "height_px": plan.target_height}

    @property
    def expected_viewport(self) -> dict[str, int]:
        plan = self.placement.plan
        return {"width": plan.target_width, "height": plan.target_height}

    @property
    def font_point_scale(self) -> float:
        plan = self.placement.plan
        return plan.scale * plan.point_per_unit

    @property
    def typography_kwargs(self) -> dict[str, object]:
        return {"contain_scale": self.placement.plan.scale,
                "minimum_effective_font_px": self.policy.minimum_target_css_px,
                "policy_id": self.policy.policy_id}

    @property
    def capacity_kwargs(self) -> dict[str, object]:
        return {
            "target_contain_scale": self.placement.plan.scale,
            "minimum_effective_font_px": self.policy.minimum_target_css_px,
            "target_font_policy_id": self.policy.policy_id,
            "target_canvas_digest": self.placement.target_canvas_digest,
        }

    def apply(self, html: str) -> tuple[str, dict[str, object]]:
        final, receipt = apply_source_native_canvas_placement(
            html,
            placement=self.placement,
            font_policy_id=self.policy.policy_id,
        )
        return final, dict(receipt)

    async def prepare(self, html: str) -> tuple[str, dict[str, object]]:
        from utils.source_canvas_normalization import normalize_source_viewport

        source = self.placement.plan.source_rect
        resolved, normalization = await normalize_source_viewport(
            html, width=source.width, height=source.height,
        )
        final, receipt = self.apply(resolved)
        receipt["inputHtmlDigest"] = _sha256_text(html)
        receipt["sourceNormalization"] = normalization
        return final, receipt

    def validate_capture(
        self,
        *,
        html: str,
        capture_html_digest: object,
        normalized_metrics: Mapping[str, object],
        receipt: Mapping[str, object],
        text_expected: bool,
    ) -> dict[str, object]:
        return validate_source_native_canvas_capture(
            html=html,
            capture_html_digest=capture_html_digest,
            normalized_metrics=normalized_metrics,
            placement=self.placement,
            policy=self.policy,
            receipt=receipt,
            text_expected=text_expected,
        )

    def map_bounds(self, bounds: Mapping[str, float]) -> dict[str, float]:
        return map_source_bounds(self.placement, bounds)


def source_native_font_policy(
    *, source_mode: object, placement: SourceNativeCanvasPlacement
) -> SourceNativeFontPolicy:
    point_per_unit = placement.plan.point_per_unit
    if source_mode == "source_content_restyle":
        return SourceNativeFontPolicy(
            DENSE_SOURCE_FONT_POLICY_ID,
            "css-px",
            DENSE_SOURCE_MIN_FONT_CSS_PX,
            DENSE_SOURCE_MIN_FONT_CSS_PX * point_per_unit,
        )
    return SourceNativeFontPolicy(
        SOURCE_NATIVE_FONT_POLICY_ID,
        "physical-pt",
        SOURCE_NATIVE_MIN_BODY_FONT_PT / point_per_unit,
        SOURCE_NATIVE_MIN_BODY_FONT_PT,
    )


def build_source_native_canvas_placement(
    *, design_canvas: object, target_canvas: object
) -> SourceNativeCanvasPlacement:
    unit = str(getattr(design_canvas, "unit", "") or "")
    unit_scale = {"px": 1.0, "emu": 1.0 / 9525.0}.get(unit)
    if unit_scale is None:
        raise ValueError("source_native_canvas_geometry_unit_unsupported")
    try:
        source_rect = CanvasRect(
            0,
            0,
            float(getattr(design_canvas, "width")) * unit_scale,
            float(getattr(design_canvas, "height")) * unit_scale,
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("source_native_canvas_geometry_invalid") from error
    parsed_target = parse_canvas_metadata(target_canvas)
    plan = build_contain_plan(source_rect=source_rect, target_canvas=parsed_target)
    geometry = {
        "unit": "css-px",
        "x": source_rect.x,
        "y": source_rect.y,
        "width": source_rect.width,
        "height": source_rect.height,
    }
    return SourceNativeCanvasPlacement(
        plan=plan,
        target_canvas_digest=canvas_digest(parsed_target),
        source_geometry_digest=_canonical_digest(geometry),
    )


def apply_source_native_canvas_placement(
    html: str,
    *,
    placement: SourceNativeCanvasPlacement,
    font_policy_id: str,
) -> tuple[str, Mapping[str, object]]:
    if not isinstance(font_policy_id, str) or not font_policy_id.strip():
        raise ValueError("document_canvas_contain_font_policy_required")
    if _VIEWPORT_DEPENDENT_CSS.search(html):
        raise ValueError("source_native_canvas_viewport_dependent_geometry")
    try:
        document = lxml_html.document_fromstring(html)
    except (TypeError, ValueError) as error:
        raise ValueError("source_native_canvas_html_invalid") from error
    html_nodes = document.xpath("//html")
    body_nodes = document.xpath("//body")
    if len(html_nodes) != 1 or len(body_nodes) != 1:
        raise ValueError("source_native_canvas_html_structure_invalid")
    if document.xpath('//*[@data-document-canvas-contain="v1"]'):
        raise ValueError("source_native_canvas_transform_already_applied")
    head_nodes = document.xpath("//head")
    if head_nodes:
        head = head_nodes[0]
    else:
        head = lxml_html.Element("head")
        html_nodes[0].insert(0, head)
    plan = placement.plan
    source = plan.source_rect
    style = lxml_html.Element("style")
    style.set("data-document-canvas-contain", "v1")
    style.text = (
        f"html{{width:{plan.target_width}px!important;height:{plan.target_height}px!important;"
        "margin:0!important;overflow:hidden!important;background:#fff!important;}"
        f"body{{width:{source.width}px!important;height:{source.height}px!important;"
        "margin:0!important;position:absolute!important;left:0!important;top:0!important;"
        "overflow:hidden!important;transform-origin:0 0!important;"
        f"transform:matrix({plan.scale:.12g},0,0,{plan.scale:.12g},"
        f"{plan.offset_x:.12g},{plan.offset_y:.12g})!important;}}"
    )
    head.append(style)
    body_nodes[0].set("data-document-canvas-coordinate-space", "target-transformed")
    final_html = lxml_html.tostring(document, encoding="unicode", method="html")
    receipt = {
        "schemaVersion": RECEIPT_VERSION,
        "transformVersion": TRANSFORM_VERSION,
        "sourceRect": {
            "x": source.x,
            "y": source.y,
            "width": source.width,
            "height": source.height,
            "unit": "css-px",
        },
        "targetCanvasDigest": placement.target_canvas_digest,
        "scale": plan.scale,
        "offset": {"x": plan.offset_x, "y": plan.offset_y},
        "inputGeometryDigest": placement.source_geometry_digest,
        "measurementSpace": "target-transformed",
        "inputHtmlDigest": _sha256_text(html),
        "finalHtmlDigest": _sha256_text(final_html),
        "fontPolicyId": font_policy_id,
    }
    return final_html, receipt


def map_source_bounds(
    placement: SourceNativeCanvasPlacement, bounds: Mapping[str, float]
) -> dict[str, float]:
    plan = placement.plan
    x, y = plan.map_point(float(bounds["x"]), float(bounds["y"]))
    return {
        "x": x,
        "y": y,
        "width": float(bounds["width"]) * plan.scale,
        "height": float(bounds["height"]) * plan.scale,
    }


def validate_source_native_canvas_capture(
    *,
    html: str,
    capture_html_digest: object,
    normalized_metrics: Mapping[str, object],
    placement: SourceNativeCanvasPlacement,
    policy: SourceNativeFontPolicy,
    receipt: Mapping[str, object],
    text_expected: bool,
) -> dict[str, object]:
    expected_digest = _sha256_text(html)
    if capture_html_digest != expected_digest or receipt.get(
        "finalHtmlDigest"
    ) != expected_digest:
        raise ValueError("source_native_final_html_digest_mismatch")
    elements = normalized_metrics.get("elements")
    source_text = [
        item for item in elements
        if isinstance(item, Mapping)
        and str(item.get("qid") or "").startswith("source-flow:")
        and str(item.get("tag") or "").lower() not in {"img", "svg", "image"}
        and ("owned_text_length" not in item or item.get("owned_text_length", 0) > 0)
    ] if isinstance(elements, list) else []
    applicable = [item for item in source_text
                  if policy.unit == "css-px" or item.get("semantic_role") not in SOURCE_ROLE_FONT_EXCEPTIONS]
    finalized = dict(receipt)
    if not applicable:
        exempt_only = bool(source_text) and all(
            item.get("semantic_role") in SOURCE_ROLE_FONT_EXCEPTIONS for item in source_text)
        if text_expected and (policy.unit == "css-px" or not exempt_only):
            raise ValueError("source_native_canvas_font_measurement_missing")
        finalized["typography"] = {
            "status": "notApplicable", "reason": "explicit-role-policy" if exempt_only else "no-text-content",
            "unit": policy.unit,
        }
        return finalized
    effective_count = sum("effective_font_size" in item for item in applicable)
    if effective_count not in (0, len(applicable)):
        raise ValueError("source_native_canvas_font_measurement_space_mixed")
    field = "effective_font_size" if effective_count else "font_size"
    if any(type(item.get(field)) not in (int, float) or not math.isfinite(item[field])
           or item[field] <= 0 for item in applicable):
        raise ValueError("source_native_canvas_font_measurement_missing")
    measured = min(float(item[field]) for item in applicable)
    space = "target-transformed" if effective_count else "source-local"
    if policy.unit == "css-px":
        typography = validate_contain_css_typography(
            placement.plan,
            measured_min_font_px=measured,
            minimum_css_px=policy.minimum_target_css_px,
            policy_id=policy.policy_id,
            measurement_space=space,
        )
        effective = typography.effective_font_px
        minimum = typography.minimum_font_px
        scale_applied = typography.contain_scale_applied
    else:
        typography = validate_contain_typography(
            placement.plan,
            measured_min_font_units=measured,
            minimum_physical_pt=policy.minimum_physical_pt,
            policy_id=policy.policy_id,
            measurement_space=space,
        )
        effective = typography.effective_font_pt
        minimum = typography.minimum_font_pt
        scale_applied = typography.contain_scale_applied
    finalized["typography"] = {
        "status": "passed",
        "unit": policy.unit,
        "effective": effective,
        "minimum": minimum,
        "measurementSpace": space,
        "containScaleApplied": scale_applied,
    }
    return finalized


__all__ = [
    "SourceNativeCanvasSession",
    "SourceNativeFontPolicy",
    "SourceNativeCanvasPlacement",
    "apply_source_native_canvas_placement",
    "build_source_native_canvas_placement",
    "map_source_bounds",
    "source_native_font_policy",
    "validate_source_native_canvas_capture",
]
