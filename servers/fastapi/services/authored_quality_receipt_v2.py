"""QualityReceipt v2 and the unified, fail-closed publication gate.

This module is intentionally independent of the generation pipeline.  It accepts
mapping or attribute based evidence, which lets Author/Illustrate/RenderCapture
publish one ledger snapshot without making the receipt depend on a particular
renderer implementation.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any


def quality_receipt_root() -> Path:
    """Return the durable root for QualityReceipt v2 manifests.

    Receipt manifests intentionally live outside the transient checkpoint
    directory: published checkpoint cleanup must not erase the idempotency
    record that protects a retry after a worker restart.  Deployments may
    provide ``AUTHORED_QUALITY_RECEIPT_DIRECTORY``; otherwise use the same
    app-data fallback as authored checkpoints.
    """

    configured = os.getenv("AUTHORED_QUALITY_RECEIPT_DIRECTORY", "").strip()
    if configured:
        root = Path(configured)
    else:
        # Keep this import lazy so the receipt contract remains usable by small
        # unit-test/CLI consumers that do not initialise the full settings
        # registry at import time.
        from utils.get_env import get_app_data_directory_env

        app_data = (get_app_data_directory_env() or "").strip()
        root = Path(app_data or os.path.join(tempfile.gettempdir(), "presenton")) / "authored-quality-receipts"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def quality_receipt_manifest_path(job_id: object) -> Path:
    """Build a stable, path-safe manifest location for one generation job.

    The job identifier is hashed before it reaches the filesystem.  The
    manifest itself stores only public attempt digests, never a worker lease
    token or other private execution credential.
    """

    value = str(job_id or "").strip()
    if not value:
        raise ValueError("quality_receipt_job_id_missing")
    job_digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]
    return quality_receipt_root() / f"job-{job_digest}.json"


def public_attempt_id(value: object) -> str:
    """Return the opaque attempt identifier used on all public contracts.

    Worker lease tokens and generation attempt UUIDs are intentionally never
    emitted in receipts, themes, or sidecars.  Values already carrying the
    canonical digest form are preserved; every other value is reduced to a
    stable 128-bit hexadecimal digest suitable for filenames and wire payloads.
    """

    text = str(value or "").strip()
    if not text:
        raise ValueError("attempt_id must not be empty")
    candidate = text[7:] if text.startswith("sha256:") else text
    if len(candidate) == 64 and all(char in "0123456789abcdefABCDEF" for char in candidate):
        return "sha256:" + candidate.lower()
    if len(candidate) == 32 and all(char in "0123456789abcdefABCDEF" for char in candidate):
        return candidate.lower()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _read(value: object, *names: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    else:
        for name in names:
            if hasattr(value, name):
                return getattr(value, name)
    return default


def _status(value: object) -> str:
    if isinstance(value, bool):
        return "passed" if value else "failed"
    if value is None:
        return "pending"
    text = str(value).strip().lower().replace("-", "_")
    if text in {"pass", "passed", "ok", "verified", "ready", "publishable", "published", "true"}:
        return "passed"
    if text in {"fail", "failed", "error", "invalid", "false"}:
        return "failed"
    if text in {"blocked", "rejected", "unsafe"}:
        return "blocked"
    if text in {"pending", "unknown", "unverified", "not_run", "not_verified"}:
        return "pending"
    return text or "pending"


def _domain(evidence: object, key: str) -> object:
    aliases = {
        "sourceReadback": ("sourceReadback", "source_readback", "sourceVerbatim", "source_verbatim", "sourceReadbackStatus", "source_readback_status"),
        "visualReview": ("visualReview", "visual_review", "visualReviewStatus", "visual_review_status"),
        "nativeCoverage": ("nativeCoverage", "native_coverage", "nativeCoverageStatus", "native_coverage_status"),
        "fontPreflight": ("fontPreflight", "font_preflight", "fontPreflightStatus", "font_preflight_status"),
        "powerpointQa": ("powerpointQa", "powerpointQA", "powerpoint_qa"),
    }.get(key, (key, f"{key}Status", f"{key}_status"))
    return _read(evidence, *aliases, default=None)


def _domain_status(evidence: object, key: str) -> str:
    value = _domain(evidence, key)
    if isinstance(value, Mapping) or (value is not None and not isinstance(value, (str, bool, int, float))):
        value = _read(value, "status", "verdict", "state", "passed", default=None)
    return _status(value)


def _mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, Mapping):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        result = dump(mode="python", by_alias=True)
        return result if isinstance(result, Mapping) else None
    return None


def _append(blockers: list[str], value: str) -> None:
    if value not in blockers:
        blockers.append(value)


@dataclass(frozen=True, slots=True)
class ReleaseQualityPolicy:
    profile_name: str = "development"
    require_source_readback: bool = False
    require_visual_review: bool = False
    require_performance: bool = False
    require_native_coverage: bool = False
    require_font_preflight: bool = False
    minimum_native_coverage_ratio: float = 0.0


def release_quality_policy(profile: object = None) -> ReleaseQualityPolicy:
    """Resolve the additive development/release quality contract.

    Named release profiles cannot be weakened by mapping flags.  Custom
    mappings may opt individual domains in, but every explicit boolean is
    parsed strictly so values such as ``"false"`` are not truthy by accident.
    """

    values = profile if isinstance(profile, Mapping) else {}
    raw_name = values.get("id", values.get("profileId", "custom")) if isinstance(profile, Mapping) else profile
    name = str("default" if raw_name is None else raw_name).strip().lower().replace("-", "_")
    if name in {"dev", "development"}:
        name = "development"
    elif name == "":
        name = "default"
    release_named = name in {"release", "strict", "production", "release_strict"}
    if not release_named and name not in {
        "default", "development", "custom", "visual", "visual_review", "performance", "perf"
    }:
        raise ValueError("quality_receipt_release_profile_unknown")

    def flag(camel: str, snake: str, default: bool = False) -> bool:
        if camel in values or snake in values:
            return _coerce_bool(values.get(camel, values.get(snake)), camel)
        return default

    require_source = release_named or flag("requireSourceReadback", "require_source_readback")
    require_visual = release_named or name in {"visual", "visual_review"} or flag(
        "requireVisualReview", "require_visual_review"
    )
    require_performance = release_named or name in {"performance", "perf"} or flag(
        "requirePerformance", "require_performance"
    )
    require_native = release_named or flag("requireNativeCoverage", "require_native_coverage")
    # The generation receipt is sealed before OOXML exists. Keep package/font
    # embedding evidence in the export QA boundary; callers may still opt in a
    # pre-export browser/glyph domain explicitly.
    require_font = flag("requireFontPreflight", "require_font_preflight")
    raw_ratio = values.get(
        "minimumNativeCoverageRatio",
        values.get("minimum_native_coverage_ratio", 0.70 if require_native else 0.0),
    )
    if isinstance(raw_ratio, bool) or not isinstance(raw_ratio, (int, float)):
        raise ValueError("quality_receipt_native_coverage_ratio_invalid")
    minimum_ratio = float(raw_ratio)
    if not 0.0 <= minimum_ratio <= 1.0:
        raise ValueError("quality_receipt_native_coverage_ratio_invalid")
    if release_named:
        minimum_ratio = max(0.70, minimum_ratio)
    return ReleaseQualityPolicy(
        profile_name=name,
        require_source_readback=require_source,
        require_visual_review=require_visual,
        require_performance=require_performance,
        require_native_coverage=require_native,
        require_font_preflight=require_font,
        minimum_native_coverage_ratio=minimum_ratio,
    )


def release_profile_requirements(profile: object = None) -> tuple[bool, bool, str]:
    """Resolve release policy for optional visual and performance evidence.

    The default profile keeps these domains explicitly ``not_assessed`` when
    omitted.  ``strict`` (or a mapping with ``requireVisualReview`` /
    ``requirePerformance``) promotes them to mandatory release gates.
    """

    policy = release_quality_policy(profile)
    return policy.require_visual_review, policy.require_performance, policy.profile_name


def _coerce_bool(value: object, field_name: str) -> bool:
    """Parse policy flags without accepting truthy strings such as ``'false'``."""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off", ""}:
            return False
    raise ValueError(f"quality_receipt_policy_boolean_invalid:{field_name}")


@dataclass(frozen=True, slots=True)
class UnifiedGateEvaluation:
    """Independent quality statuses evaluated over one evidence snapshot."""

    passed: bool
    blockers: tuple[str, ...] = ()
    safety_status: str = "pending"
    capacity_status: str = "pending"
    source_readback_status: str = "pending"
    visual_review_status: str = "pending"
    performance_status: str = "pending"
    native_coverage_status: str = "pending"
    font_preflight_status: str = "pending"
    native_coverage_ratio: float | None = None
    native_coverage_denominator: int = 0
    native_element_count: int = 0
    raster_fallbacks: tuple[tuple[str, str, bool], ...] = ()
    quality_status: str = "blocked"
    publication_status: str = "pending"

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "blockers": list(self.blockers),
            "safetyStatus": self.safety_status,
            "capacityStatus": self.capacity_status,
            "sourceReadbackStatus": self.source_readback_status,
            "visualReviewStatus": self.visual_review_status,
            "performanceStatus": self.performance_status,
            "nativeCoverageStatus": self.native_coverage_status,
            "fontPreflightStatus": self.font_preflight_status,
            "nativeCoverageRatio": self.native_coverage_ratio,
            "nativeCoverageDenominator": self.native_coverage_denominator,
            "nativeElementCount": self.native_element_count,
            "rasterFallbacks": [
                {"elementId": element_id, "reason": reason, "eligible": eligible, "explicit": True}
                for element_id, reason, eligible in self.raster_fallbacks
            ],
            "qualityStatus": self.quality_status,
            "publicationStatus": self.publication_status,
        }

    as_dict = to_dict

    def model_dump(self, *, mode: str = "python", by_alias: bool = True) -> dict[str, object]:
        return self.to_dict()


def _evaluate_native_coverage(
    evidence: object,
    *,
    required: bool,
    minimum_ratio: float,
) -> tuple[str, float | None, int, int, tuple[tuple[str, str, bool], ...], tuple[str, ...]]:
    payload = _mapping(_domain(evidence, "nativeCoverage"))
    if payload is None:
        return (
            "blocked" if required else "not_assessed",
            None,
            0,
            0,
            (),
            ("nativeCoverage.evidence_missing",) if required else (),
        )

    status = _domain_status(evidence, "nativeCoverage")
    if not required and status in {
        "pending",
        "unknown",
        "not_assessed",
        "not_applicable",
    }:
        return "not_assessed", None, 0, 0, (), ()
    denominator = _read(
        payload,
        "eligibleElementCount",
        "eligible_element_count",
        "supportedElementCount",
        "supported_element_count",
        default=None,
    )
    native_count = _read(payload, "nativeElementCount", "native_element_count", default=None)
    raw_fallbacks = _read(payload, "rasterFallbacks", "raster_fallbacks", default=None)
    blockers: list[str] = []
    fallbacks: list[tuple[str, str, bool]] = []
    if (
        isinstance(denominator, bool)
        or not isinstance(denominator, int)
        or denominator <= 0
        or isinstance(native_count, bool)
        or not isinstance(native_count, int)
        or native_count < 0
        or native_count > denominator
    ):
        _append(blockers, "nativeCoverage.denominator_invalid")
        denominator = denominator if isinstance(denominator, int) and not isinstance(denominator, bool) else 0
        native_count = native_count if isinstance(native_count, int) and not isinstance(native_count, bool) else 0

    if not isinstance(raw_fallbacks, Sequence) or isinstance(raw_fallbacks, (str, bytes)):
        _append(blockers, "nativeCoverage.fallback_audit_missing")
        raw_fallbacks = ()
    seen_ids: set[str] = set()
    eligible_fallbacks = 0
    for item in raw_fallbacks:
        if not isinstance(item, Mapping):
            _append(blockers, "nativeCoverage.fallback_invalid")
            continue
        element_id = str(item.get("elementId", item.get("element_id", "")) or "").strip()
        reason = str(item.get("reason", "") or "").strip()
        explicit = item.get("explicit") is True
        eligible = item.get("eligible", True)
        if not isinstance(eligible, bool):
            _append(blockers, "nativeCoverage.fallback_eligibility_invalid")
            continue
        if not element_id or not reason or not explicit or element_id in seen_ids:
            _append(blockers, "nativeCoverage.fallback_invalid")
            continue
        seen_ids.add(element_id)
        if not eligible:
            _append(blockers, "nativeCoverage.denominator_shrunk")
        eligible_fallbacks += 1
        fallbacks.append((element_id, reason, eligible))

    if denominator > 0 and native_count + eligible_fallbacks != denominator:
        _append(blockers, "nativeCoverage.denominator_mismatch")
    ratio = (native_count / denominator) if denominator > 0 else None
    reported_ratio = _read(payload, "ratio", "nativeRatio", "native_ratio", default=None)
    if reported_ratio is not None:
        if (
            isinstance(reported_ratio, bool)
            or not isinstance(reported_ratio, (int, float))
            or ratio is None
            or abs(float(reported_ratio) - ratio) > 1e-9
        ):
            _append(blockers, "nativeCoverage.ratio_mismatch")
    if ratio is not None and ratio < minimum_ratio:
        _append(blockers, "nativeCoverage.below_minimum")
    if status in {"failed", "blocked"}:
        _append(blockers, "nativeCoverage.failed")
    elif required and status in {"pending", "unknown", "not_assessed", "not_applicable"}:
        _append(blockers, "nativeCoverage.unverified")
    if blockers:
        status = "blocked"
    elif status == "pending":
        status = "passed"
    return status, ratio, int(denominator), int(native_count), tuple(fallbacks), tuple(blockers)


def _evaluate_font_preflight(
    evidence: object,
    *,
    required: bool,
) -> tuple[str, tuple[str, ...]]:
    payload = _mapping(_domain(evidence, "fontPreflight"))
    if payload is None:
        powerpoint = _mapping(_domain(evidence, "powerpointQa"))
        if powerpoint is not None:
            payload = _mapping(_read(powerpoint, "fontPreflight", "font_preflight", default=None))
    if payload is None:
        return (
            "blocked" if required else "not_assessed",
            ("fontPreflight.evidence_missing",) if required else (),
        )
    status = _status(_read(payload, "status", default=None))
    if not required and status in {"pending", "unknown", "not_assessed", "not_applicable"}:
        return "not_assessed", ()
    blockers: list[str] = []
    cjk_runs = _read(payload, "cjkTextRuns", "cjk_text_runs", default=None)
    digest = str(
        _read(payload, "ooxmlReadbackSha256", "ooxml_readback_sha256", default="") or ""
    ).lower()
    if isinstance(cjk_runs, bool) or not isinstance(cjk_runs, int) or cjk_runs < 0:
        _append(blockers, "fontPreflight.cjk_run_count_invalid")
        cjk_runs = 0
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        _append(blockers, "fontPreflight.ooxml_digest_invalid")
    if cjk_runs > 0:
        typefaces = _read(
            payload,
            "declaredEastAsianTypefaces",
            "declared_east_asian_typefaces",
            default=None,
        )
        if not isinstance(typefaces, Sequence) or isinstance(typefaces, (str, bytes)) or not typefaces:
            _append(blockers, "fontPreflight.east_asian_typeface_missing")
        for camel, snake, blocker in (
            ("missingEastAsianTypefaceRuns", "missing_east_asian_typeface_runs", "fontPreflight.east_asian_typeface_missing"),
            ("replacementGlyphs", "replacement_glyphs", "fontPreflight.glyph_readback_failed"),
        ):
            value = _read(payload, camel, snake, default=None)
            if isinstance(value, bool) or not isinstance(value, int) or value != 0:
                _append(blockers, blocker)
        embedded = _read(payload, "embeddedFontFiles", "embedded_font_files", default=None)
        verified = _read(payload, "embeddingVerified", "embedding_verified", default=None)
        if isinstance(embedded, bool) or not isinstance(embedded, int) or embedded <= 0 or verified is not True:
            _append(blockers, "fontPreflight.embedding_failed")
    if status in {"failed", "blocked"}:
        _append(blockers, "fontPreflight.failed")
    elif required and status not in {"passed", "not_applicable", "not_required"}:
        _append(blockers, "fontPreflight.unverified")
    return ("blocked" if blockers else ("not_applicable" if cjk_runs == 0 else "passed"), tuple(blockers))


def evaluate_unified_gate(
    evidence: object,
    *,
    require_visual_review: bool = False,
    require_performance: bool = False,
    release_profile: object = None,
) -> UnifiedGateEvaluation:
    """Evaluate capacity, safety, source readback and review states fail closed.

    ``publication_status`` is derived from blockers and does not represent task
    completion.  Performance and visual review remain separate statuses; a
    caller can make either a blocker with the explicit keyword above.
    """
    policy = release_quality_policy(release_profile)
    require_visual_review = _coerce_bool(require_visual_review, "require_visual_review") or policy.require_visual_review
    require_performance = _coerce_bool(require_performance, "require_performance") or policy.require_performance
    statuses = {key: _domain_status(evidence, key) for key in (
        "safety", "capacity", "sourceReadback", "visualReview", "performance", "publication"
    )}
    blockers: list[str] = []

    safety = _domain(evidence, "safety")
    residual = _read(safety, "residual", "residuals", "safetyResidual", "safetyResiduals", default=None)
    if statuses["safety"] == "pending" and safety is not None:
        telemetry = _read(safety, "telemetryStatus", "telemetry_status", default=None)
        issues = _read(safety, "issues", default=None)
        if telemetry in {"available", "ready"} and not issues:
            statuses["safety"] = "passed"
    if residual not in (None, False, 0, "", [], {}):
        statuses["safety"] = "blocked"
        _append(blockers, "safety.residual")
    if statuses["safety"] in {"failed", "blocked"}:
        _append(blockers, "safety.failed")
    elif statuses["safety"] in {"pending", "not_assessed"}:
        _append(blockers, "safety.unverified")

    capacity = _domain(evidence, "capacity")
    cap = _mapping(capacity)
    if cap is None:
        statuses["capacity"] = "blocked"
        _append(blockers, "capacity.evidence_missing")
    else:
        if _read(cap, "browserObserved", "browser_observed", default=None) is not True:
            _append(blockers, "capacity.browser_observation_missing")
        checks = cap.get("checks") if isinstance(cap.get("checks"), Mapping) else cap
        required = ("text", "title", "body", "font", "qid", "image")
        missing = [name for name in required if name not in checks]
        failed = [name for name in required if name in checks and checks[name] is not True]
        if missing:
            _append(blockers, "capacity.required_missing:" + ",".join(missing))
        if failed:
            _append(blockers, "capacity.required_failed:" + ",".join(failed))
        if missing or failed or statuses["capacity"] in {"failed", "blocked"}:
            statuses["capacity"] = "blocked"
            _append(blockers, "capacity.failed")
        else:
            statuses["capacity"] = "passed"

    source = _mapping(_domain(evidence, "sourceReadback"))
    source_required = policy.require_source_readback or not (source and source.get("required") is False)
    if source_required:
        source_failed = source is None or (
            source.get("verified") is not True
            or source.get("passed") is False
            or bool(source.get("failures"))
            or bool(source.get("failedBlocks"))
            or bool(source.get("clipped"))
            or bool(source.get("occluded"))
            or statuses["sourceReadback"] in {"failed", "blocked"}
        )
        if source_failed:
            statuses["sourceReadback"] = "blocked"
            _append(blockers, "sourceReadback.failed")
        elif source and source.get("verified") is True:
            statuses["sourceReadback"] = "passed"
        elif statuses["sourceReadback"] in {"pending", "not_assessed", "not_applicable"}:
            _append(blockers, "sourceReadback.unverified")
    else:
        statuses["sourceReadback"] = "not_applicable"

    for key, required in (("visualReview", require_visual_review), ("performance", require_performance)):
        if statuses[key] in {"pending", "unknown"} and not required:
            statuses[key] = "not_assessed"
        if statuses[key] in {"failed", "blocked"} or (required and statuses[key] in {"pending", "not_assessed", "not_applicable"}):
            _append(blockers, key + (".failed" if statuses[key] in {"failed", "blocked"} else ".unverified"))
    native_status, native_ratio, native_denominator, native_count, raster_fallbacks, native_blockers = _evaluate_native_coverage(
        evidence,
        required=policy.require_native_coverage,
        minimum_ratio=policy.minimum_native_coverage_ratio,
    )
    for blocker in native_blockers:
        _append(blockers, blocker)
    font_status, font_blockers = _evaluate_font_preflight(
        evidence,
        required=policy.require_font_preflight,
    )
    for blocker in font_blockers:
        _append(blockers, blocker)

    publication_value = _domain(evidence, "publication")
    raw_publication = _read(
        publication_value,
        "status",
        "state",
        default=publication_value,
    )
    publication_text = str(raw_publication or "pending").strip().lower().replace("-", "_")
    if publication_text in {"complete", "completed", "published"}:
        statuses["publication"] = "completed"
    else:
        statuses["publication"] = _status(raw_publication)
    quality_status = "blocked" if blockers else "passed"
    return UnifiedGateEvaluation(
        passed=not blockers,
        blockers=tuple(blockers),
        safety_status=statuses["safety"],
        capacity_status=statuses["capacity"],
        source_readback_status=statuses["sourceReadback"],
        visual_review_status=statuses["visualReview"],
        performance_status=statuses["performance"],
        native_coverage_status=native_status,
        font_preflight_status=font_status,
        native_coverage_ratio=native_ratio,
        native_coverage_denominator=native_denominator,
        native_element_count=native_count,
        raster_fallbacks=raster_fallbacks,
        quality_status=quality_status,
        publication_status=statuses["publication"],
    )


def evaluate_capacity_browser_metrics(
    metrics: object,
    limits: object,
    *,
    expected_title: str | None = None,
    expected_body: str | None = None,
) -> dict[str, object]:
    """Derive the six hard capacity checks from Chromium telemetry.

    Missing or malformed telemetry returns all checks false.  No HTML string is
    used as a substitute for browser visibility.
    """
    payload = _mapping(metrics)
    elements = payload.get("elements") if payload else None
    if not isinstance(elements, Sequence) or isinstance(elements, (str, bytes)):
        return {"browserObserved": False, "checks": {name: False for name in ("text", "title", "body", "font", "qid", "image")}, "violations": ["metrics.elements_missing"]}
    max_qids = _read(limits, "max_qids", "maxQids", default=None)
    max_images = _read(limits, "max_images", "maxImages", default=None)
    max_text = _read(limits, "max_visible_text_chars", "maxVisibleTextChars", default=None)
    min_font = _read(limits, "min_font_px", "minFontPx", default=None)
    if not all(isinstance(v, (int, float)) for v in (max_qids, max_images, max_text, min_font)):
        return {"browserObserved": False, "checks": {name: False for name in ("text", "title", "body", "font", "qid", "image")}, "violations": ["limits_missing"]}
    violations: list[str] = []
    qids: set[str] = set()
    qid_values: list[str] = []
    capacity_qids: list[str] = []
    visible_text: list[str] = []
    role_text: dict[str, list[str]] = {"title": [], "body": []}
    image_ok = True
    font_ok = True
    text_ok = True
    for item in elements:
        if not isinstance(item, Mapping):
            return {"browserObserved": False, "checks": {name: False for name in ("text", "title", "body", "font", "qid", "image")}, "violations": ["metrics.element_invalid"]}
        qid = item.get("qid")
        semantic_role = str(item.get("semantic_role", item.get("semanticRole", item.get("role", ""))) or "").strip().lower()
        is_title_role = semantic_role == "title" or semantic_role.startswith("title") or semantic_role.endswith("title")
        is_body_role = semantic_role == "body" or semantic_role.startswith("body") or semantic_role.endswith("body")
        is_supporting_role = semantic_role == "supporting" or semantic_role.startswith("supporting") or semantic_role.endswith("supporting")
        is_image_element = str(item.get("tag", "")).lower() == "img" or isinstance(item.get("image"), Mapping)
        text = str(item.get("rendered_text", item.get("renderedText", "")) or "").strip()
        owned_value = item.get("has_owned_text", item.get("hasOwnedText"))
        owns_text = owned_value is True if isinstance(owned_value, bool) else bool(text)
        if is_image_element:
            image = item.get("image") if isinstance(item.get("image"), Mapping) else item
            if image.get("complete") is not True or float(image.get("naturalWidth", image.get("natural_width", 0)) or 0) <= 0 or float(image.get("naturalHeight", image.get("natural_height", 0)) or 0) <= 0 or image.get("cssVisible", image.get("css_visible", True)) is False:
                image_ok = False
            image_role = semantic_role or str(image.get("semantic_role", image.get("semanticRole", image.get("role", ""))) or "").strip().lower()
            role_is_image = (
                image_role == "image"
                or image_role.startswith("image")
                or image_role.endswith("image")
                or "-image" in image_role
                or "image-" in image_role
            )
            if not role_is_image:
                image_ok = False
                violations.append(f"image:semantic_role_missing:{qid}")
        if not isinstance(qid, str) or not qid.strip():
            qids.add("")
            text_ok = False
            continue
        qids.add(qid)
        qid_values.append(qid)
        if (owns_text and (is_title_role or is_body_role)) or is_image_element:
            capacity_qids.append(qid)
        style = item.get("computed_style") if isinstance(item.get("computed_style"), Mapping) else {}
        visible = (
            item.get("css_visible", item.get("cssVisible", True)) is not False
            and item.get("visibility", style.get("visibility", "visible")) not in {"hidden", "collapse"}
        )
        clipped = item.get("clipped", item.get("glyph_clipped", item.get("glyphClipped", False))) is True
        if owns_text and text and visible and not clipped:
            visible_text.append(text)
            if is_title_role:
                role_text["title"].append(text)
            elif is_body_role:
                role_text["body"].append(text)
        if owns_text and text and (not visible or clipped):
            text_ok = False
        font_loaded = item.get("font_loaded", item.get("fontLoaded")) is True
        font_size = item.get("font_size", item.get("fontSize"))
        if owns_text and text:
            font_floor = 12.0 if is_supporting_role else float(min_font)
            if not font_loaded or not isinstance(font_size, (int, float)) or float(font_size) < font_floor:
                font_ok = False
    # Each entry is an independently owned Chromium text run. Joining with a
    # synthetic separator would charge characters that were never rendered.
    text_value = "".join(visible_text)
    if len(capacity_qids) > int(max_qids):
        violations.append(f"qids:{len(capacity_qids)}>{int(max_qids)}")
    image_count = sum(1 for item in elements if isinstance(item, Mapping) and (str(item.get("tag", "")).lower() == "img" or "image" in item))
    if image_count > int(max_images):
        image_ok = False
        violations.append(f"images:{image_count}>{int(max_images)}")
    if len(text_value) > int(max_text):
        text_ok = False
        violations.append(f"visible_text:{len(text_value)}>{int(max_text)}")
    # Expected title/body values are checked against their declared semantic
    # role only.  Falling back to the aggregate visible text lets a slide pass
    # with a title string rendered in an arbitrary footer or decoration.
    title_ok = bool(role_text["title"]) and (
        expected_title is None or expected_title.strip() in role_text["title"]
    )
    body_ok = bool(role_text["body"]) and (
        expected_body is None or expected_body.strip() in role_text["body"]
    )
    if not role_text["title"]:
        violations.append("title:semantic_role_missing")
    if not role_text["body"]:
        violations.append("body:semantic_role_missing")
    if len(qid_values) != len(set(qid_values)):
        violations.append("qids:duplicate")
    checks = {"text": text_ok, "title": title_ok, "body": body_ok, "font": font_ok, "qid": bool(qids) and "" not in qids and len(capacity_qids) <= int(max_qids) and len(qid_values) == len(set(qid_values)), "image": image_ok}
    return {"browserObserved": True, "checks": checks, "visibleText": text_value, "violations": violations}


@dataclass(frozen=True, slots=True)
class QualityReceiptV2:
    schema_version: str
    attempt_id: str
    revision: int
    ledger_digest: str
    deck_html_digest: str
    safety_status: str
    capacity_status: str
    source_readback_status: str
    visual_review_status: str
    performance_status: str
    publication_status: str
    quality_status: str = "blocked"
    native_coverage_status: str = "not_assessed"
    font_preflight_status: str = "not_assessed"
    native_coverage_ratio: float | None = None
    native_coverage_denominator: int = 0
    native_element_count: int = 0
    raster_fallbacks: tuple[tuple[str, str, bool], ...] = ()
    blockers: tuple[str, ...] = ()
    gate_passed: bool = False
    receipt_digest: str = ""
    release_profile: str = "default"
    require_visual_review: bool = False
    require_performance: bool = False
    require_source_readback: bool = False
    require_native_coverage: bool = False
    require_font_preflight: bool = False
    minimum_native_coverage_ratio: float = 0.0

    @property
    def statuses(self) -> dict[str, str]:
        return {
            "safety": self.safety_status,
            "capacity": self.capacity_status,
            "sourceReadback": self.source_readback_status,
            "visualReview": self.visual_review_status,
            "performance": self.performance_status,
            "nativeCoverage": self.native_coverage_status,
            "fontPreflight": self.font_preflight_status,
            "quality": self.quality_status,
            "publication": self.publication_status,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "attemptId": self.attempt_id,
            "revision": self.revision,
            "ledgerDigest": self.ledger_digest,
            "deckHtmlDigest": self.deck_html_digest,
            "safetyStatus": self.safety_status,
            "capacityStatus": self.capacity_status,
            "sourceReadbackStatus": self.source_readback_status,
            "visualReviewStatus": self.visual_review_status,
            "performanceStatus": self.performance_status,
            "nativeCoverageStatus": self.native_coverage_status,
            "fontPreflightStatus": self.font_preflight_status,
            "nativeCoverageRatio": self.native_coverage_ratio,
            "nativeCoverageDenominator": self.native_coverage_denominator,
            "nativeElementCount": self.native_element_count,
            "rasterFallbacks": [
                {"elementId": element_id, "reason": reason, "eligible": eligible, "explicit": True}
                for element_id, reason, eligible in self.raster_fallbacks
            ],
            "qualityStatus": self.quality_status,
            "publicationStatus": self.publication_status,
            "blockers": list(self.blockers),
            "gatePassed": self.gate_passed,
            "receiptDigest": self.receipt_digest,
            "releaseProfile": self.release_profile,
            "requireSourceReadback": self.require_source_readback,
            "requireVisualReview": self.require_visual_review,
            "requirePerformance": self.require_performance,
            "requireNativeCoverage": self.require_native_coverage,
            "requireFontPreflight": self.require_font_preflight,
            "minimumNativeCoverageRatio": self.minimum_native_coverage_ratio,
        }

    as_dict = to_dict

    def model_dump(self, *, mode: str = "python", by_alias: bool = True) -> dict[str, object]:
        return self.to_dict()


def _receipt_digest(receipt: QualityReceiptV2) -> str:
    """Recompute the canonical digest used when a receipt was sealed."""

    payload = {
        "schemaVersion": receipt.schema_version,
        "attemptId": receipt.attempt_id,
        "revision": receipt.revision,
        "ledgerDigest": receipt.ledger_digest,
        "deckHtmlDigest": receipt.deck_html_digest,
        "passed": receipt.gate_passed,
        "blockers": list(receipt.blockers),
        "safetyStatus": receipt.safety_status,
        "capacityStatus": receipt.capacity_status,
        "sourceReadbackStatus": receipt.source_readback_status,
        "visualReviewStatus": receipt.visual_review_status,
        "performanceStatus": receipt.performance_status,
        "nativeCoverageStatus": receipt.native_coverage_status,
        "fontPreflightStatus": receipt.font_preflight_status,
        "nativeCoverageRatio": receipt.native_coverage_ratio,
        "nativeCoverageDenominator": receipt.native_coverage_denominator,
        "nativeElementCount": receipt.native_element_count,
        "rasterFallbacks": [list(item) for item in receipt.raster_fallbacks],
        "qualityStatus": receipt.quality_status,
        "publicationStatus": receipt.publication_status,
        "releaseProfile": receipt.release_profile,
        "requireSourceReadback": receipt.require_source_readback,
        "requireVisualReview": receipt.require_visual_review,
        "requirePerformance": receipt.require_performance,
        "requireNativeCoverage": receipt.require_native_coverage,
        "requireFontPreflight": receipt.require_font_preflight,
        "minimumNativeCoverageRatio": receipt.minimum_native_coverage_ratio,
    }
    return "sha256:" + hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _receipt_digest_pre_font(receipt: QualityReceiptV2) -> str:
    """Digest used before CJK/OOXML font preflight became a release domain."""

    payload = {
        "schemaVersion": receipt.schema_version,
        "attemptId": receipt.attempt_id,
        "revision": receipt.revision,
        "ledgerDigest": receipt.ledger_digest,
        "deckHtmlDigest": receipt.deck_html_digest,
        "passed": receipt.gate_passed,
        "blockers": list(receipt.blockers),
        "safetyStatus": receipt.safety_status,
        "capacityStatus": receipt.capacity_status,
        "sourceReadbackStatus": receipt.source_readback_status,
        "visualReviewStatus": receipt.visual_review_status,
        "performanceStatus": receipt.performance_status,
        "nativeCoverageStatus": receipt.native_coverage_status,
        "nativeCoverageRatio": receipt.native_coverage_ratio,
        "nativeCoverageDenominator": receipt.native_coverage_denominator,
        "nativeElementCount": receipt.native_element_count,
        "rasterFallbacks": [list(item) for item in receipt.raster_fallbacks],
        "qualityStatus": receipt.quality_status,
        "publicationStatus": receipt.publication_status,
        "releaseProfile": receipt.release_profile,
        "requireSourceReadback": receipt.require_source_readback,
        "requireVisualReview": receipt.require_visual_review,
        "requirePerformance": receipt.require_performance,
        "requireNativeCoverage": receipt.require_native_coverage,
        "minimumNativeCoverageRatio": receipt.minimum_native_coverage_ratio,
    }
    return "sha256:" + hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _receipt_digest_legacy(receipt: QualityReceiptV2) -> str:
    """Digest used by manifests written before policy flags were persisted."""

    payload = {
        "schemaVersion": receipt.schema_version,
        "attemptId": receipt.attempt_id,
        "revision": receipt.revision,
        "ledgerDigest": receipt.ledger_digest,
        "deckHtmlDigest": receipt.deck_html_digest,
        "passed": receipt.gate_passed,
        "blockers": list(receipt.blockers),
        "safetyStatus": receipt.safety_status,
        "capacityStatus": receipt.capacity_status,
        "sourceReadbackStatus": receipt.source_readback_status,
        "visualReviewStatus": receipt.visual_review_status,
        "performanceStatus": receipt.performance_status,
        "publicationStatus": receipt.publication_status,
        "releaseProfile": receipt.release_profile,
    }
    return "sha256:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _receipt_digest_policy_v1(receipt: QualityReceiptV2) -> str:
    """Digest used before release source/native/quality fields were added."""

    payload = {
        "schemaVersion": receipt.schema_version,
        "attemptId": receipt.attempt_id,
        "revision": receipt.revision,
        "ledgerDigest": receipt.ledger_digest,
        "deckHtmlDigest": receipt.deck_html_digest,
        "passed": receipt.gate_passed,
        "blockers": list(receipt.blockers),
        "safetyStatus": receipt.safety_status,
        "capacityStatus": receipt.capacity_status,
        "sourceReadbackStatus": receipt.source_readback_status,
        "visualReviewStatus": receipt.visual_review_status,
        "performanceStatus": receipt.performance_status,
        "publicationStatus": receipt.publication_status,
        "releaseProfile": receipt.release_profile,
        "requireVisualReview": receipt.require_visual_review,
        "requirePerformance": receipt.require_performance,
    }
    return "sha256:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _manifest_digest(records: Mapping[str, object]) -> str:
    """Return the digest for the canonical manifest envelope.

    ``manifestDigest`` is calculated without itself in the payload so a
    partially written or hand-edited records map cannot be accepted after a
    worker restart.  Individual receipt digests remain the second integrity
    boundary for each record.
    """

    canonical = {
        "schemaVersion": "quality-receipt-manifest/v1",
        "receipts": records,
    }
    return "sha256:" + hashlib.sha256(
        json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


@contextmanager
def _manifest_lock(path: Path, *, timeout_seconds: float = 5.0):
    """Serialize read/modify/write cycles across worker processes.

    A lock file is deliberately separate from the manifest so replacing the
    JSON file remains atomic.  The file is retained after release; only the
    OS advisory lock is transient and therefore a crashed worker cannot leave
    a permanent stale lock artifact.
    """

    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with lock_path.open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        locked = False
        try:
            if os.name == "nt":
                import msvcrt

                while not locked:
                    try:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        locked = True
                    except OSError:
                        if time.monotonic() - started >= timeout_seconds:
                            raise ValueError("quality_receipt_manifest_lock_timeout")
                        time.sleep(0.01)
            else:
                import fcntl

                while not locked:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        locked = True
                    except OSError:
                        if time.monotonic() - started >= timeout_seconds:
                            raise ValueError("quality_receipt_manifest_lock_timeout")
                        time.sleep(0.01)
            yield
        finally:
            if locked:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass(slots=True)
class QualityReceiptLedger:
    _receipts: MutableMapping[tuple[str, int], QualityReceiptV2] = field(default_factory=dict)
    storage_path: str | Path | None = None
    _load_failed: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        # Migrate callers that supplied a legacy in-memory mapping keyed by a
        # raw token before durable storage was introduced.  The migration is
        # one way and ensures a subsequent manifest write cannot leak it.
        migrated: dict[tuple[str, int], QualityReceiptV2] = {}
        for key, receipt in list(self._receipts.items()):
            if not isinstance(receipt, QualityReceiptV2) or not isinstance(key, tuple) or len(key) != 2:
                continue
            public_id = public_attempt_id(receipt.attempt_id or key[0])
            if public_id != receipt.attempt_id or public_id != key[0]:
                migrated_receipt = replace(receipt, attempt_id=public_id)
                migrated[(public_id, int(key[1]))] = replace(
                    migrated_receipt,
                    receipt_digest=_receipt_digest(migrated_receipt),
                )
                del self._receipts[key]
        self._receipts.update(migrated)
        if self.storage_path is not None:
            self.storage_path = Path(self.storage_path)
            with _manifest_lock(self.storage_path):
                self._load(replace_existing=True, lock_held=True)

    def _load(self, *, replace_existing: bool = False, lock_held: bool = False) -> None:
        path = Path(self.storage_path) if self.storage_path is not None else None
        if path is None or not path.is_file():
            if replace_existing:
                self._receipts.clear()
            self._load_failed = False
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping) or payload.get("schemaVersion") != "quality-receipt-manifest/v1":
                raise ValueError("quality_receipt_manifest_schema_invalid")
            records = payload.get("receipts")
            if not isinstance(records, Mapping):
                raise ValueError("quality_receipt_manifest_records_invalid")
            manifest_digest = payload.get("manifestDigest")
            if manifest_digest is not None and manifest_digest != _manifest_digest(records):
                raise ValueError("quality_receipt_manifest_digest_mismatch")
            migrated = manifest_digest is None
            loaded: dict[tuple[str, int], QualityReceiptV2] = {}
            for key, item in records.items():
                if not isinstance(item, Mapping):
                    raise ValueError("quality_receipt_manifest_record_invalid")
                attempt, separator, revision_text = str(key).partition("#")
                if not separator or not attempt or not revision_text.isdigit():
                    raise ValueError("quality_receipt_manifest_key_invalid")
                revision = int(revision_text)
                if revision < 0:
                    raise ValueError("quality_receipt_manifest_revision_invalid")
                raw_attempt = item.get("attemptId")
                if not isinstance(raw_attempt, str) or not raw_attempt.strip():
                    raise ValueError("quality_receipt_manifest_attempt_invalid")
                public_id = public_attempt_id(attempt)
                if public_attempt_id(raw_attempt) != public_id:
                    raise ValueError("quality_receipt_manifest_attempt_mismatch")
                raw_revision = item.get("revision")
                if not isinstance(raw_revision, int) or isinstance(raw_revision, bool) or raw_revision != revision:
                    raise ValueError("quality_receipt_manifest_revision_mismatch")
                schema_version = str(item.get("schemaVersion", "quality-receipt/v2"))
                if schema_version != "quality-receipt/v2":
                    raise ValueError("quality_receipt_manifest_receipt_schema_invalid")
                blockers = item.get("blockers", ())
                if not isinstance(blockers, (list, tuple)) or not all(isinstance(value, str) for value in blockers):
                    raise ValueError("quality_receipt_manifest_blockers_invalid")
                profile_name = str(item.get("releaseProfile", "development")).strip() or "development"
                profile_visual = item.get("requireVisualReview", profile_name in {"strict", "production", "release_strict", "visual", "visual_review"})
                profile_performance = item.get("requirePerformance", profile_name in {"strict", "production", "release_strict", "performance", "perf"})
                raw_fallbacks = item.get("rasterFallbacks", ())
                parsed_fallbacks: list[tuple[str, str, bool]] = []
                if not isinstance(raw_fallbacks, (list, tuple)):
                    raise ValueError("quality_receipt_manifest_fallbacks_invalid")
                for fallback in raw_fallbacks:
                    if isinstance(fallback, Mapping):
                        element_id = str(fallback.get("elementId", ""))
                        reason = str(fallback.get("reason", ""))
                        eligible = fallback.get("eligible", True)
                    elif isinstance(fallback, (list, tuple)) and len(fallback) == 3:
                        element_id, reason, eligible = fallback
                    else:
                        raise ValueError("quality_receipt_manifest_fallbacks_invalid")
                    if not isinstance(element_id, str) or not isinstance(reason, str) or not isinstance(eligible, bool):
                        raise ValueError("quality_receipt_manifest_fallbacks_invalid")
                    parsed_fallbacks.append((element_id, reason, eligible))
                receipt = QualityReceiptV2(
                    schema_version=schema_version,
                    attempt_id=raw_attempt,
                    revision=revision,
                    ledger_digest=str(item.get("ledgerDigest", "")),
                    deck_html_digest=str(item.get("deckHtmlDigest", "")),
                    safety_status=str(item.get("safetyStatus", "pending")),
                    capacity_status=str(item.get("capacityStatus", "pending")),
                    source_readback_status=str(item.get("sourceReadbackStatus", "pending")),
                    visual_review_status=str(item.get("visualReviewStatus", "not_assessed")),
                    performance_status=str(item.get("performanceStatus", "not_assessed")),
                    publication_status=str(item.get("publicationStatus", "pending")),
                    quality_status=str(item.get("qualityStatus", "passed" if item.get("gatePassed") is True else "blocked")),
                    native_coverage_status=str(item.get("nativeCoverageStatus", "not_assessed")),
                    font_preflight_status=str(item.get("fontPreflightStatus", "not_assessed")),
                    native_coverage_ratio=(
                        float(item["nativeCoverageRatio"])
                        if isinstance(item.get("nativeCoverageRatio"), (int, float))
                        and not isinstance(item.get("nativeCoverageRatio"), bool)
                        else None
                    ),
                    native_coverage_denominator=int(item.get("nativeCoverageDenominator", 0)),
                    native_element_count=int(item.get("nativeElementCount", 0)),
                    raster_fallbacks=tuple(parsed_fallbacks),
                    blockers=tuple(blockers),
                    gate_passed=bool(item.get("gatePassed", False)),
                    receipt_digest=str(item.get("receiptDigest", "")),
                    release_profile=profile_name,
                    require_source_readback=_coerce_bool(item.get("requireSourceReadback", False), "requireSourceReadback"),
                    require_visual_review=_coerce_bool(profile_visual, "requireVisualReview"),
                    require_performance=_coerce_bool(profile_performance, "requirePerformance"),
                    require_native_coverage=_coerce_bool(item.get("requireNativeCoverage", False), "requireNativeCoverage"),
                    require_font_preflight=_coerce_bool(item.get("requireFontPreflight", False), "requireFontPreflight"),
                    minimum_native_coverage_ratio=float(item.get("minimumNativeCoverageRatio", 0.0)),
                )
                expected_digest = _receipt_digest(receipt)
                legacy_digest = False
                if receipt.receipt_digest != expected_digest:
                    if receipt.receipt_digest not in {
                        _receipt_digest_pre_font(receipt),
                        _receipt_digest_policy_v1(receipt),
                        _receipt_digest_legacy(receipt),
                    }:
                        raise ValueError("quality_receipt_manifest_digest_mismatch")
                    migrated = True
                    legacy_digest = True
                if public_id != raw_attempt:
                    migrated = True
                if legacy_digest or public_id != raw_attempt:
                    receipt = replace(receipt, attempt_id=public_id, receipt_digest="")
                    receipt = replace(receipt, receipt_digest=_receipt_digest(receipt))
                canonical_key = (public_id, revision)
                if canonical_key in loaded:
                    raise ValueError("quality_receipt_manifest_key_collision")
                loaded[canonical_key] = receipt
            if replace_existing:
                self._receipts.clear()
            self._receipts.update(loaded)
            if migrated:
                if lock_held:
                    self._persist_unlocked()
                else:
                    self._persist()
            self._load_failed = False
        except (AttributeError, OSError, ValueError, TypeError, json.JSONDecodeError):
            # A corrupt manifest must fail closed at the next create call, but
            # should not prevent process startup.  Keep the in-memory ledger
            # empty so no untrusted record can be published.
            self._receipts.clear()
            self._load_failed = True

    def _persist_unlocked(self) -> None:
        path = Path(self.storage_path) if self.storage_path is not None else None
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        records = {
            f"{attempt}#{revision}": receipt.to_dict()
            for (attempt, revision), receipt in sorted(self._receipts.items())
        }
        envelope = {"schemaVersion": "quality-receipt-manifest/v1", "receipts": records}
        payload = json.dumps(
            {**envelope, "manifestDigest": _manifest_digest(records)},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
            try:
                directory_fd = os.open(str(path.parent), os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except (AttributeError, OSError):
                # Directory fsync is unavailable on some Windows filesystems;
                # the file itself was already flushed before atomic replace.
                pass
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _persist(self) -> None:
        path = Path(self.storage_path) if self.storage_path is not None else None
        if path is None:
            return
        with _manifest_lock(path):
            self._persist_unlocked()

    def create_once(self, *, attempt_id: str, revision: int, ledger_digest: str, deck_html_digest: str, evidence: object, require_visual_review: bool = False, require_performance: bool = False, release_profile: object = None) -> QualityReceiptV2:
        attempt_id = public_attempt_id(attempt_id)
        if self._load_failed:
            raise ValueError("quality_receipt_manifest_corrupt")
        if not isinstance(revision, int) or revision < 0:
            raise ValueError("quality_receipt_v2_revision_invalid")
        require_visual_review = _coerce_bool(require_visual_review, "require_visual_review")
        require_performance = _coerce_bool(require_performance, "require_performance")

        def create_unlocked() -> QualityReceiptV2:
            # Refresh from the durable source while holding the lock.  This
            # closes the read/modify/write race between independent workers.
            if self.storage_path is not None:
                self._load(replace_existing=True, lock_held=True)
                if self._load_failed:
                    raise ValueError("quality_receipt_manifest_corrupt")
            policy = release_quality_policy(release_profile)
            profile_name = policy.profile_name
            effective_visual = require_visual_review or policy.require_visual_review
            effective_performance = require_performance or policy.require_performance
            key = (attempt_id, revision)
            existing = self._receipts.get(key)
            if existing is not None:
                if existing.ledger_digest != ledger_digest or existing.deck_html_digest != deck_html_digest:
                    raise ValueError("quality_receipt_v2_binding_changed")
                if (
                    existing.release_profile != profile_name
                    or existing.require_source_readback != policy.require_source_readback
                    or existing.require_visual_review != effective_visual
                    or existing.require_performance != effective_performance
                    or existing.require_native_coverage != policy.require_native_coverage
                    or existing.require_font_preflight != policy.require_font_preflight
                    or existing.minimum_native_coverage_ratio
                    != policy.minimum_native_coverage_ratio
                ):
                    raise ValueError("quality_receipt_v2_policy_changed")
                return existing
            evaluation = evaluate_unified_gate(
                evidence,
                require_visual_review=effective_visual,
                require_performance=effective_performance,
                release_profile=release_profile,
            )
            receipt = QualityReceiptV2(
                schema_version="quality-receipt/v2",
                attempt_id=attempt_id,
                revision=revision,
                ledger_digest=ledger_digest,
                deck_html_digest=deck_html_digest,
                safety_status=evaluation.safety_status,
                capacity_status=evaluation.capacity_status,
                source_readback_status=evaluation.source_readback_status,
                visual_review_status=evaluation.visual_review_status,
                performance_status=evaluation.performance_status,
                native_coverage_status=evaluation.native_coverage_status,
                font_preflight_status=evaluation.font_preflight_status,
                native_coverage_ratio=evaluation.native_coverage_ratio,
                native_coverage_denominator=evaluation.native_coverage_denominator,
                native_element_count=evaluation.native_element_count,
                raster_fallbacks=evaluation.raster_fallbacks,
                quality_status=evaluation.quality_status,
                publication_status=evaluation.publication_status,
                blockers=evaluation.blockers,
                gate_passed=evaluation.passed,
                release_profile=profile_name,
                require_source_readback=policy.require_source_readback,
                require_visual_review=effective_visual,
                require_performance=effective_performance,
                require_native_coverage=policy.require_native_coverage,
                require_font_preflight=policy.require_font_preflight,
                minimum_native_coverage_ratio=policy.minimum_native_coverage_ratio,
            )
            receipt = replace(receipt, receipt_digest=_receipt_digest(receipt))
            self._receipts[key] = receipt
            if self.storage_path is not None:
                self._persist_unlocked()
            return receipt

        if self.storage_path is None:
            return create_unlocked()
        with _manifest_lock(Path(self.storage_path)):
            return create_unlocked()


def create_quality_receipt_v2(ledger: QualityReceiptLedger | MutableMapping[tuple[str, int], QualityReceiptV2] | Mapping[str, object], *, attempt_id: str | None = None, revision: int | None = None, ledger_digest: str | None = None, deck_html_digest: str | None = None, evidence: object | None = None, receipt_store: MutableMapping[tuple[str, int], QualityReceiptV2] | None = None, require_visual_review: bool = False, require_performance: bool = False, release_profile: object = None, storage_path: str | Path | None = None) -> QualityReceiptV2:
    """Create one immutable receipt per attempt/revision (idempotent retry)."""
    # A ledger projection from authored_evidence_ledger.as_dict(attempt_id) is
    # accepted directly; explicit arguments still win when supplied.
    attempt_id = attempt_id or _read(ledger, "attemptId", "attempt_id", default=None)
    revision = revision if revision is not None else _read(ledger, "revision", default=None)
    ledger_digest = ledger_digest or _read(ledger, "ledgerDigest", "ledger_digest", default=None)
    deck_html_digest = deck_html_digest or _read(ledger, "deckHtmlDigest", "deck_html_digest", default=None)
    if callable(revision) and isinstance(attempt_id, str):
        revision = revision(attempt_id)
    if callable(ledger_digest) and isinstance(attempt_id, str):
        ledger_digest = ledger_digest(attempt_id)
    if callable(deck_html_digest) and isinstance(attempt_id, str):
        deck_html_digest = deck_html_digest(attempt_id)
    evidence = evidence if evidence is not None else ledger
    if not isinstance(attempt_id, str) or not attempt_id or not isinstance(revision, int):
        raise ValueError("quality_receipt_v2_binding_missing")
    if not isinstance(ledger_digest, str) or not ledger_digest or not isinstance(deck_html_digest, str) or not deck_html_digest:
        raise ValueError("quality_receipt_v2_digest_missing")
    if receipt_store is None:
        candidate_store = _read(ledger, "receipt_store", "receiptStore", default=None)
        if isinstance(candidate_store, MutableMapping):
            receipt_store = candidate_store
    if receipt_store is not None:
        ledger = receipt_store
    if isinstance(ledger, QualityReceiptLedger):
        if storage_path is not None:
            requested_path = Path(storage_path)
            if ledger.storage_path is None:
                ledger.storage_path = requested_path
                with _manifest_lock(requested_path):
                    ledger._load(lock_held=True)
            elif Path(ledger.storage_path) != requested_path:
                raise ValueError("quality_receipt_storage_changed")
        return ledger.create_once(attempt_id=attempt_id, revision=revision, ledger_digest=ledger_digest, deck_html_digest=deck_html_digest, evidence=evidence, require_visual_review=require_visual_review, require_performance=require_performance, release_profile=release_profile)
    helper = QualityReceiptLedger(ledger if isinstance(ledger, MutableMapping) else {}, storage_path=storage_path)
    return helper.create_once(attempt_id=attempt_id, revision=revision, ledger_digest=ledger_digest, deck_html_digest=deck_html_digest, evidence=evidence, require_visual_review=require_visual_review, require_performance=require_performance, release_profile=release_profile)


# Compatibility aliases make the versioned wire type discoverable to callers
# that use the unversioned product name.
QualityReceipt = QualityReceiptV2
build_quality_receipt_v2 = create_quality_receipt_v2
evaluate_capacity = evaluate_capacity_browser_metrics
evaluate_unified_quality_gate = evaluate_unified_gate

__all__ = ["QualityReceipt", "QualityReceiptLedger", "QualityReceiptV2", "ReleaseQualityPolicy", "UnifiedGateEvaluation", "build_quality_receipt_v2", "create_quality_receipt_v2", "evaluate_capacity", "evaluate_capacity_browser_metrics", "evaluate_unified_gate", "evaluate_unified_quality_gate", "public_attempt_id", "quality_receipt_manifest_path", "quality_receipt_root", "release_profile_requirements", "release_quality_policy"]
