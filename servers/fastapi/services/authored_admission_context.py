"""Immutable source and attempt identity for authored HTML admission."""

from __future__ import annotations

from utils.hashing import canonical_json_bytes, sha256_prefixed
from services.authored_admission_policy import (
    HTML_REPRESENTATIONS,
    MANIFEST_DIGEST_MISMATCH,
    PROJECTION_DIGEST_MISMATCH,
    REPRESENTATION_INVALID,
    SOURCE_READBACK_FAILED,
)

from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
import json
from typing import Any


def digest_bytes(value: bytes) -> str:
    return sha256_prefixed(value)


def digest_html(value: str) -> str:
    return digest_bytes(value.encode("utf-8"))


def json_value(value: Any) -> Any:
    """Render mappings and sequences as the JSON types ``json.dumps`` accepts.

    Producers hand the gate telemetry in whatever shape their own stage holds
    it.  The per-slide render checkpoint passes Chromium's plain ``dict``, while
    publication passes the deck evidence ledger's *retained* metrics, which that
    ledger freezes into nested ``MappingProxyType``
    (``services/authored_evidence_ledger.py``).  ``json.dumps`` serializes the
    first and raises ``TypeError`` on the second, so the digest that keys every
    admission used to depend on which stage produced the metrics: the
    publication gate raised an uncoded ``TypeError`` on a deck whose slides had
    each been admitted at their render checkpoint moments earlier.

    Normalizing first makes the two the same bytes.  For a document that already
    serialized this rewrite is the identity -- mappings keep their keys and
    sequences stay arrays -- so digests minted before it remain valid, and
    ``allow_nan=False`` below still fails closed on non-finite telemetry.
    """

    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        value = dump(mode="json", by_alias=True)
    return canonical_json_bytes(json_value(value)).decode("utf-8")


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
    #: Which representation of the slide these bytes are: the model's own output
    #: with placeholders unexpanded (``token``), the substituted and statically
    #: checked document (``expanded``), or that document plus the Chromium
    #: metrics and PNG taken from it (``captured``).  Design section 2.3 names
    #: this ``html_repr`` and versions a checkpoint by it; the receipt publishes
    #: it as ``htmlRepr`` so two receipts over the same slide are not confused
    #: for two judgements of the same bytes.
    html_repr: str = "expanded"
    capture_profile: str = "authored/default"
    allow_illustration_slots: bool = False
    finalized: bool = False
    # JSON strings prevent mutation of a caller-owned manifest after admission.
    manifest_json: str = "null"
    projection_json: str = "null"
    bindings: tuple[tuple[str, str], ...] = ()
    image_bindings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.html_repr not in HTML_REPRESENTATIONS:
            raise ValueError(REPRESENTATION_INVALID)
        object.__setattr__(self, "already_admitted_flags", tuple(self.already_admitted_flags))
        object.__setattr__(self, "bindings", tuple(tuple(pair) for pair in self.bindings))
        object.__setattr__(self, "image_bindings", tuple(self.image_bindings))
        if self.manifest_json != "null" and digest_html(self.manifest_json) != self.manifest_digest:
            raise ValueError(MANIFEST_DIGEST_MISMATCH)
        if self.projection_json != "null" and digest_html(self.projection_json) != self.projection_digest:
            raise ValueError(PROJECTION_DIGEST_MISMATCH)

    @property
    def state(self) -> str:
        """Read-only alias; ``html_repr`` is the field consumers may set."""
        return self.html_repr

    @property
    def identity_digest(self) -> str:
        return digest_html(canonical_json(asdict(self)))

    def for_state(self, state: str, **changes: Any) -> AdmissionContext:
        return replace(self, html_repr=state, **changes)

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
