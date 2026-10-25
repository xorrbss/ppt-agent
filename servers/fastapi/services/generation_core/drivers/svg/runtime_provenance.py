"""Allowlisted, digest-neutral diagnostics for one SVG runtime attempt."""
from __future__ import annotations

from collections.abc import Mapping
import re


_INTEGER_KNOBS = (
    "author_concurrency", "render_concurrency", "illustration_budget",
    "illustration_timeout_seconds",
)


def runtime_provenance_field(value: object) -> dict[str, object] | None:
    """Project only known scalar knobs; arbitrary context/env values never escape."""
    if not isinstance(value, Mapping) or value.get("schemaVersion") != "svg-runtime-provenance/v1":
        return None
    fingerprint = value.get("codeFingerprint")
    knobs = value.get("effectiveKnobs")
    if (not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
            or not isinstance(knobs, Mapping)):
        return None
    effective = {}
    for name in _INTEGER_KNOBS:
        number = knobs.get(name)
        if type(number) is not int or not 0 <= number <= 2**31 - 1:
            return None
        effective[name] = number
    policy = knobs.get("terminal_policy")
    advisory = knobs.get("vision_advisory")
    if policy not in ("fail", "fallback", "flag_only") or type(advisory) is not bool:
        return None
    effective.update(terminal_policy=str(policy), vision_advisory=advisory)
    result = {"schemaVersion": "svg-runtime-provenance/v1", "codeFingerprint": fingerprint,
              "effectiveKnobs": effective}
    author = value.get("authorConcurrency")
    if isinstance(author, Mapping) and author.get("source") in ("default", "env"):
        projected = {"source": author["source"]}
        for name in ("requested", "resolved", "effective", "executor_capacity"):
            number = author.get(name)
            if type(number) is int and 0 < number <= 2**31 - 1:
                projected[name] = number
            elif name == "executor_capacity" and number is None:
                projected[name] = None
        if type(author.get("clamped_to_executor")) is bool:
            projected["clamped_to_executor"] = author["clamped_to_executor"]
        if author.get("override") == "repurchase":
            projected["override"] = "repurchase"
        result["authorConcurrency"] = projected
    return result


def runtime_provenance_snapshot(ctx: object) -> dict[str, object] | None:
    from services.svg_partial_recovery import _code_digest

    profile = getattr(ctx, "runtime_profile", None)
    if profile is None:
        return None
    try:
        knobs = {name: getattr(profile, name) for name in _INTEGER_KNOBS}
        policy = profile.terminal_policy
        knobs.update(terminal_policy=getattr(policy, "value", policy),
                     vision_advisory=profile.vision_advisory)
        metadata = getattr(ctx, "runtime_profile_provenance", None) or {}
        return runtime_provenance_field({
            "schemaVersion": "svg-runtime-provenance/v1", "codeFingerprint": _code_digest(),
            "effectiveKnobs": knobs, "authorConcurrency": metadata.get("author_concurrency"),
        })
    except (AttributeError, OSError, TypeError, ValueError):
        # Optional diagnostics must not turn a generation outcome into a failure.
        return None
