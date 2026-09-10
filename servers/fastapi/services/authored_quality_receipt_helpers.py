"""Pure payload helpers shared by the authored quality receipt facade."""

from __future__ import annotations

from collections.abc import Mapping


def read(value: object, *names: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    else:
        for name in names:
            if hasattr(value, name):
                return getattr(value, name)
    return default


def status(value: object) -> str:
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


def domain(evidence: object, key: str) -> object:
    aliases = {
        "sourceReadback": ("sourceReadback", "source_readback", "sourceVerbatim", "source_verbatim", "sourceReadbackStatus", "source_readback_status"),
        "visualReview": ("visualReview", "visual_review", "visualReviewStatus", "visual_review_status"),
        "nativeCoverage": ("nativeCoverage", "native_coverage", "nativeCoverageStatus", "native_coverage_status"),
        "fontPreflight": ("fontPreflight", "font_preflight", "fontPreflightStatus", "font_preflight_status"),
        "powerpointQa": ("powerpointQa", "powerpointQA", "powerpoint_qa"),
    }.get(key, (key, f"{key}Status", f"{key}_status"))
    return read(evidence, *aliases, default=None)


def domain_status(evidence: object, key: str) -> str:
    value = domain(evidence, key)
    if isinstance(value, Mapping) or (value is not None and not isinstance(value, (str, bool, int, float))):
        value = read(value, "status", "verdict", "state", "passed", default=None)
    return status(value)


def mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, Mapping):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        result = dump(mode="python", by_alias=True)
        return result if isinstance(result, Mapping) else None
    return None


def append_unique(blockers: list[str], value: str) -> None:
    if value not in blockers:
        blockers.append(value)
