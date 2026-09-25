"""Pure helpers for the authored QualityReceipt V3 pipeline facade."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.authored_vnext.capability_registry import CAPABILITY_REGISTRY_DIGEST
from services.authored_vnext.master.digests import canonical_digest


def mapping(value: object) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json", by_alias=True)
        return payload if isinstance(payload, Mapping) else None
    if hasattr(value, "as_dict"):
        payload = value.as_dict()
        return payload if isinstance(payload, Mapping) else None
    return None


def consumer(name: str, *, status: str, required: bool, reason_code: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"status": status, "required": required, "digest": CAPABILITY_REGISTRY_DIGEST}
    if reason_code is not None:
        payload["reasonCode"] = reason_code
    return payload


def prefixed_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 71 and value.startswith("sha256:") and all(
        character in "0123456789abcdef" for character in value[7:]
    )


def digest_bound_check(value: object, *, observed: bool, reason_code: str) -> dict[str, object]:
    if not observed:
        return {"status": "not_assessed", "observed": False, "reasonCode": reason_code}
    payload = mapping(value)
    if payload is None:
        return {"status": "failed", "observed": False, "reasonCode": reason_code}
    return {"status": "passed", "observed": True, "evidenceDigest": canonical_digest(dict(payload))}


def capacity_checks_pass(capacity_evidence: Mapping[str, object], *names: str) -> bool:
    checks = mapping(capacity_evidence.get("checks"))
    return capacity_evidence.get("browserObserved") is True and checks is not None and all(
        checks.get(name) is True for name in names
    ) and not capacity_evidence.get("violations", ())
