"""Versioned original-preserving policy for newly authored HTML only.

The shared reconstruction capability floor deliberately remains unchanged.
Physical output dimensions, never capture DPR, determine the font conversion.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from typing import Any
from utils.hashing import canonical_json_digest, sha256_prefixed
from services.authored_canvas import DEFAULT_CANVAS_HEIGHT_PX, DEFAULT_CANVAS_WIDTH_PX


@dataclass(frozen=True, slots=True)
class AuthoredLocalFitFontPolicy:
    revision: str
    floor_pt: Decimal
    max_shrink: Decimal
    allow_extended_shrink: bool = False


_FONT_POLICIES = {
    policy.revision: policy
    for policy in (
        AuthoredLocalFitFontPolicy(
            revision="authored-local-fit/v1.3",
            floor_pt=Decimal("9.5"),
            max_shrink=Decimal("0.10"),
        ),
        AuthoredLocalFitFontPolicy(
            revision="authored-local-fit/v1.4",
            floor_pt=Decimal("9.0"),
            max_shrink=Decimal("0.10"),
        ),
        AuthoredLocalFitFontPolicy(
            revision="authored-local-fit/v1.5",
            floor_pt=Decimal("9.0"),
            max_shrink=Decimal("0.10"),
            allow_extended_shrink=True,
        ),
    )
}
POLICY_REVISION = "authored-local-fit/v1.5"
BASELINE_SCHEMA_REVISION = "authored-original-runs/v1"
IMAGE_TEXT_SCOPE_REVISION = "all-text-strict/v1"
FONT_FLOOR_PT = float(_FONT_POLICIES[POLICY_REVISION].floor_pt)
MAX_SHRINK = float(_FONT_POLICIES[POLICY_REVISION].max_shrink)
GEOMETRY_TOLERANCE_PX = 0.5


def digest(value: Any) -> str:
    return canonical_json_digest(value)


def html_digest(html: str) -> str:
    return sha256_prefixed(html.encode("utf-8"))


def _positive(value: float) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError("unverifiable_font_size")
    return result


def font_policy(policy_revision: str = POLICY_REVISION) -> AuthoredLocalFitFontPolicy:
    custom = re.fullmatch(r"authored-local-fit/user-font-v1/([0-9]+(?:\.[0-9]+)?)", policy_revision)
    if custom:
        floor = Decimal(custom.group(1))
        if not floor.is_finite() or floor <= 0:
            raise ValueError("unsupported_policy_revision")
        # Encode the request-specific floor in the existing durable policy
        # identity so renderer, verification and export use the same value.
        return AuthoredLocalFitFontPolicy(
            revision=policy_revision, floor_pt=floor, max_shrink=Decimal("0.10"),
        )
    try:
        return _FONT_POLICIES[policy_revision]
    except KeyError as exc:
        raise ValueError("unsupported_policy_revision") from exc


def requested_font_policy_revision(
    instructions: str | None, *, conversion: float = 0.75,
    policy_revision: str = POLICY_REVISION,
) -> str:
    from utils.authored_font_policy import requested_font_floor_px

    requested = requested_font_floor_px(instructions)
    if requested is None:
        return policy_revision
    if Decimal(str(requested)) * Decimal(str(_positive(conversion))) >= font_policy(policy_revision).floor_pt:
        return policy_revision
    floor = min(Decimal(str(requested)) * Decimal(str(_positive(conversion))),
                font_policy(policy_revision).floor_pt)
    return f"authored-local-fit/user-font-v1/{format(floor.normalize(), 'f')}"


def policy_floor_pt(policy_revision: str = POLICY_REVISION) -> float:
    return float(font_policy(policy_revision).floor_pt)


def minimum_allowed_pt(
    original_pt: float, *, policy_revision: str = POLICY_REVISION
) -> float:
    policy = font_policy(policy_revision)
    if policy.allow_extended_shrink:
        return float(policy.floor_pt)
    retained = Decimal("1") - policy.max_shrink
    return float(max(Decimal(str(_positive(original_pt))) * retained, policy.floor_pt))


def ceil_output_pt(value: float) -> float:
    return float(Decimal(str(_positive(value))).quantize(Decimal("0.01"),
                                                        rounding=ROUND_CEILING))


def pt_per_css_px(*, width_pt: float, height_pt: float,
                  viewport_width: float = DEFAULT_CANVAS_WIDTH_PX,
                  viewport_height: float = DEFAULT_CANVAS_HEIGHT_PX) -> float:
    horizontal = _positive(width_pt) / _positive(viewport_width)
    vertical = _positive(height_pt) / _positive(viewport_height)
    if not math.isclose(horizontal, vertical, rel_tol=1e-10, abs_tol=1e-12):
        raise ValueError("unverifiable_font_size")
    return horizontal


def effective_pt(font_px: float, effective_text_scale: float,
                 conversion: float) -> float:
    return _positive(font_px) * _positive(effective_text_scale) * _positive(conversion)


def required_font_px(
    original_pt: float,
    effective_text_scale: float,
    conversion: float,
    *,
    policy_revision: str = POLICY_REVISION,
) -> float:
    required = Decimal(str(minimum_allowed_pt(
        original_pt, policy_revision=policy_revision,
    ))) / (
        Decimal(str(_positive(effective_text_scale))) * Decimal(str(_positive(conversion))))
    return float(required.quantize(Decimal("0.0001"), rounding=ROUND_CEILING))


def font_size_allowed(
    original_pt: float, final_pt: float, *, policy_revision: str = POLICY_REVISION
) -> bool:
    original, final = _positive(original_pt), _positive(final_pt)
    policy = font_policy(policy_revision)
    # No geometry epsilon is permitted at a font policy boundary.
    return final >= minimum_allowed_pt(
        original, policy_revision=policy_revision,
    ) and (original < float(policy.floor_pt) or final <= original)


def local_fit_applies(
    *, reference_mode: str, source_reflow: bool, settings: Any | None = None
) -> bool:
    """Whether this deck runs the deterministic local fit at all.

    One predicate for every entry point in an attempt. The pipeline decides
    whether to fit, and generation requires one receipt per slide when it did;
    if those two disagree a fitted deck fails with
    ``authored_local_fit_evidence_incomplete`` and an unfitted one publishes
    without evidence. Exact reconstruction never fits -- reproducing a source
    slide may not move its geometry. Source reflow is off by default behind
    ``AUTHORED_LOCAL_FIT_SOURCE_REFLOW``.
    """
    from utils.get_env_programme import is_authored_local_fit_source_reflow_enabled
    from utils.llm_calls.author_slide_effort import EXACT_RECONSTRUCTION

    if reference_mode == EXACT_RECONSTRUCTION:
        return False
    if source_reflow:
        return is_authored_local_fit_source_reflow_enabled(settings)
    return True


def local_fit_policy(*, author_revision: str, original_html_digest: str,
                     export_width_pt: float = 960, export_height_pt: float = 540,
                     viewport_width: int = DEFAULT_CANVAS_WIDTH_PX, viewport_height: int = DEFAULT_CANVAS_HEIGHT_PX,
                     baseline: Mapping[str, Any] | None = None,
                     slide_index: int = 0, fit_budget_ms: float = 6000,
                     policy_revision: str = POLICY_REVISION) -> dict[str, Any]:
    selected_font_policy = font_policy(policy_revision)
    scale = {
        "width_pt": _positive(export_width_pt), "height_pt": _positive(export_height_pt),
        "viewport_width": int(_positive(viewport_width)),
        "viewport_height": int(_positive(viewport_height)), "dpr": 1,
    }
    return {
        "policy_revision": selected_font_policy.revision,
        "floor_pt": float(selected_font_policy.floor_pt),
        "max_shrink": float(selected_font_policy.max_shrink),
        "preferred_max_shrink": float(selected_font_policy.max_shrink),
        "allow_extended_shrink": selected_font_policy.allow_extended_shrink,
        "pt_per_css_px": pt_per_css_px(
            width_pt=export_width_pt, height_pt=export_height_pt,
            viewport_width=viewport_width, viewport_height=viewport_height),
        "export_scale_manifest": scale, "author_revision": author_revision,
        "original_html_digest": original_html_digest,
        "baseline_schema_revision": BASELINE_SCHEMA_REVISION,
        "image_text_scope_revision": IMAGE_TEXT_SCOPE_REVISION,
        "baseline": dict(baseline) if baseline is not None else None,
        "slide_index": slide_index, "fit_budget_ms": max(0, min(6000, fit_budget_ms)),
        "page_number_slot_contract": {
            "detector_revision": "authored-footer-number/v1",
            "expected_number": slide_index + 1,
        },
        "max_regions": 16, "max_measurements": 160,
        "parent_reflow_max_depth": 4,
        "geometry_tolerance_px": GEOMETRY_TOLERANCE_PX,
    }


def baseline_digest(baseline: Mapping[str, Any]) -> str:
    # Hash every recorded run, group and measurement identity, not only minima.
    return digest({k: v for k, v in baseline.items() if k != "baseline_digest"})


def acceptance_key(render_key: str, baseline: Mapping[str, Any],
                   policy: Mapping[str, Any]) -> str:
    policy_revision = str(policy.get("policy_revision") or POLICY_REVISION)
    font_policy(policy_revision)
    return digest({
        "render_key": render_key, "baseline_digest": baseline_digest(baseline),
        "policy_revision": policy_revision,
        "export_scale_manifest": policy["export_scale_manifest"],
        "image_text_scope_revision": IMAGE_TEXT_SCOPE_REVISION,
    })


def slide_budget(renderer_timeout: float, remaining_job_seconds: float) -> dict[str, float]:
    total = min(_positive(remaining_job_seconds), 2 * _positive(renderer_timeout))
    verify = min(renderer_timeout, total / 2)
    return {"total_seconds": total, "verify_seconds": verify,
            "first_seconds": total - verify,
            "fit_seconds": min(renderer_timeout * .1, 6, total - verify)}


def image_text_evidence_for_html(
    html: str, *, app_data: str | None = None
) -> list[dict[str, Any]]:
    """``image_text_evidence`` records for the pack icons a slide inlines.

    One record per ``<img>`` whose ``src`` is the data URI of an icon-pack
    entry that passed the OCR text check, keyed by the qid the browser
    resolves (``closest('[data-qid]')``). No new judgment is made here: the
    record restates the pack index, and every other picture -- cutouts,
    illustrations, uploads, heuristic-only or failed checks -- stays
    unverifiable so the fit still fails closed. Never raises; malformed
    markup or an unconfigured pack root yields ``[]``.
    """
    from services.authored_local_fit_image_text import image_text_evidence

    try:
        return image_text_evidence(html, app_data=app_data)
    except Exception:  # noqa: BLE001 - evidence is advisory, never a fit blocker
        return []
