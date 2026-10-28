"""Content-free witnesses for SVG author dispatches, outside purchase identity."""
from __future__ import annotations

import re

from utils.hashing import canonical_json_digest


def cause(code, evidence=None):
    if not isinstance(code, str) or not re.fullmatch(
        r"initial|unclassified|timeout_repurchase|(?:static_quality|visual_advisory|provider_retry|header_repair):[a-z][a-z0-9_]{0,95}",
        code,
    ):
        return {"code": "unclassified"}
    result = {"code": code}
    if evidence is not None:
        try:
            result["evidence_digest"] = canonical_json_digest(evidence)
        except (TypeError, ValueError):
            pass  # Optional diagnostic evidence must not block authoring.
    return result


def static_cause(failures=(), validation_error=None):
    # Exception details may contain source text. Only the bounded code prefix
    # is eligible; arbitrary messages collapse to the stable failure class.
    kind = next((f.get("kind") for f in failures if isinstance(f, dict) and f.get("kind")), None)
    if validation_error:
        match = re.match(r"(svg_[a-z_]{1,90})(?=:|$)", str(validation_error))
        kind = match[1] if match else "validation_failed"
    if not isinstance(kind, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,95}", kind):
        kind = "validation_failed"
    family = "header_repair" if kind.startswith(("svg_common_header_", "header_")) else "static_quality"
    return cause(f"{family}:{kind}")


def advisory_cause(decision):
    targets = decision.get("target_axes") or [target.get("axis") or target.get("kind")
        for target in decision.get("design_targets", []) if isinstance(target, dict)]
    target = targets[0] if targets else decision.get("repair_scope")
    return cause(f"visual_advisory:{target}", decision)


def dispatch_cause(payload, *, ordinal):
    # Observability cannot reject a generation, including older checkpoints
    # whose cause evidence is incomplete. The author logs this fallback once.
    try:
        return _dispatch_cause(payload, ordinal=ordinal)
    except (KeyError, TypeError, ValueError, AttributeError):
        return cause("unclassified")


def _dispatch_cause(payload, *, ordinal):
    """Consume current transition evidence; never infer from an older failure.

    The fallback reads existing structured fields for checkpoints created
    before cause witnesses shipped. New transitions write next_attempt_cause.
    """
    explicit = payload.get("next_attempt_cause")
    if explicit is not None:
        if not isinstance(explicit, dict) or set(explicit) - {"code", "evidence_digest"}:
            return cause("unclassified")
        result = cause(explicit.get("code"))
        digest = explicit.get("evidence_digest")
        if digest is not None:
            if not isinstance(digest, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
                return cause("unclassified")
            result["evidence_digest"] = digest
        if result["code"] == "initial" and (ordinal or payload.get("provider_failures")):
            return cause("unclassified")
        return result
    attempts = payload.get("attempts") or []
    failures = payload.get("provider_failures") or []
    last = attempts[-1] if attempts else {}
    # Failed transport calls do not append a completed author attempt.
    if failures and failures[-1]["ordinal"] >= last.get("receipt", {}).get("attempt_ordinal", 0):
        return cause("provider_retry:" + failures[-1]["code"])
    if not attempts and (payload.get("retained_provider_attempt_fence")
                         or payload.get("append_only_replacement")):
        return cause("timeout_repurchase")
    recovery = payload.get("quality_recovery") or {}
    if recovery.get("status") == "repair_pending" and recovery.get("decision"):
        return advisory_cause(recovery["decision"])
    if (payload.get("quality_recovery_fallback") or {}).get("status") == "reserved":
        return cause("static_quality:validated_svg_adaptation")
    quality = last.get("quality") or payload.get("quality") or payload.get("initial_quality") or {}
    validation_error = last.get("validation_error") or last.get("receipt", {}).get("validation_error")
    if validation_error or quality.get("failures"):
        return static_cause(quality.get("failures", []), validation_error)
    if ordinal == 0 and not failures:
        return cause("initial")
    return cause("unclassified")
