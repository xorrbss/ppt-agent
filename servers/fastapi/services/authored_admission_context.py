"""Immutable source and attempt identity for authored HTML admission."""

from __future__ import annotations

from services.authored_admission_policy import (
    MANIFEST_DIGEST_MISMATCH,
    PROJECTION_DIGEST_MISMATCH,
    REPRESENTATION_INVALID,
    SOURCE_READBACK_FAILED,
)

from dataclasses import asdict, dataclass, replace
import hashlib
import json
from typing import Any


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def digest_html(value: str) -> str:
    return digest_bytes(value.encode("utf-8"))


def canonical_json(value: Any) -> str:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        value = dump(mode="json", by_alias=True)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True, slots=True)
class AdmissionContext:
    presentation_id: str
    operation_id: str
    slide_index: int
    source_unit_id: str
    manifest_digest: str
    projection_digest: str
    policy_revision: str
    fenced_revision: int
    attempt_token: str
    reference_mode: str
    source_reflow: bool
    terminal_fallback_eligible: bool
    already_admitted_flags: tuple[str, ...] = ()
    state: str = "expanded"
    capture_profile: str = "authored/default"
    allow_illustration_slots: bool = False
    finalized: bool = False
    # JSON strings prevent mutation of a caller-owned manifest after admission.
    manifest_json: str = "null"
    projection_json: str = "null"
    bindings: tuple[tuple[str, str], ...] = ()
    image_bindings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.state not in {"token", "expanded", "captured"}:
            raise ValueError(REPRESENTATION_INVALID)
        object.__setattr__(self, "already_admitted_flags", tuple(self.already_admitted_flags))
        object.__setattr__(self, "bindings", tuple(tuple(pair) for pair in self.bindings))
        object.__setattr__(self, "image_bindings", tuple(self.image_bindings))
        if self.manifest_json != "null" and digest_html(self.manifest_json) != self.manifest_digest:
            raise ValueError(MANIFEST_DIGEST_MISMATCH)
        if self.projection_json != "null" and digest_html(self.projection_json) != self.projection_digest:
            raise ValueError(PROJECTION_DIGEST_MISMATCH)

    @property
    def identity_digest(self) -> str:
        return digest_html(canonical_json(asdict(self)))

    def for_state(self, state: str, **changes: Any) -> AdmissionContext:
        return replace(self, state=state, **changes)

    def readback_documents(self) -> tuple[dict, dict]:
        """Select one source unit without manufacturing a replacement manifest."""
        manifest = json.loads(self.manifest_json)
        projection = json.loads(self.projection_json)
        if not isinstance(manifest, dict) or not isinstance(projection, dict):
            raise ValueError(SOURCE_READBACK_FAILED)
        units, slides = manifest.get("units", ()), projection.get("slides", ())
        if len(units) != len(slides) or not 0 <= self.slide_index < len(slides):
            raise ValueError(SOURCE_READBACK_FAILED)
        slide = slides[self.slide_index]
        unit = units[self.slide_index]
        unit_id = str(unit.get("sourceUnitId") or unit.get("source_unit_id") or "")
        slide_id = str(slide.get("sourceUnitId") or slide.get("source_unit_id") or "")
        if unit_id != self.source_unit_id or slide_id != unit_id:
            raise ValueError(SOURCE_READBACK_FAILED)
        return {"units": [unit]}, {"slides": [slide]}


def context_from_source(*, manifest: Any, projection: Any, **kwargs: Any) -> AdmissionContext:
    manifest_json, projection_json = canonical_json(manifest), canonical_json(projection)
    return AdmissionContext(
        **kwargs,
        manifest_json=manifest_json,
        projection_json=projection_json,
        manifest_digest=digest_html(manifest_json),
        projection_digest=digest_html(projection_json),
    )
