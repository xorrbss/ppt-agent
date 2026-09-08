"""Recognise a Hybrid export attempt across both lanes that record one.

Two lanes write export attempts and they name the mode differently. The direct
attempt service writes ``capability_evidence.export_mode``; the isolated public
lane -- the one the product's own export command uses -- writes the requested
and delivered modes instead, and its compiler is the worker's rather than
``authored-hybrid``. Matching only the direct lane's key meant a real user's
successful Hybrid export never appeared in the history dialog, which reported
"no Hybrid exports recorded" for a deck that had just produced one.

A fallback counts: a request for Hybrid that delivered the fidelity raster is
exactly the outcome the history is there to show.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


HYBRID_ATTEMPT_EVIDENCE_MARKERS: tuple[tuple[str, str], ...] = (
    ("export_mode", "hybrid"),
    ("requested_pptx_mode", "hybrid"),
    ("pptx_mode", "hybrid"),
    ("delivered_export_mode", "editable-hybrid"),
)


def provenance_is_hybrid_attempt(provenance: Mapping[str, Any] | None) -> bool:
    """True when this attempt's provenance describes a Hybrid export."""

    payload = provenance or {}
    evidence = payload.get("capability_evidence") or {}
    if isinstance(evidence, Mapping) and any(
        evidence.get(key) == value for key, value in HYBRID_ATTEMPT_EVIDENCE_MARKERS
    ):
        return True
    runtime = payload.get("compiler_runtime") or {}
    compiler = runtime.get("compiler") if isinstance(runtime, Mapping) else None
    return compiler == "authored-hybrid" or payload.get("coverage_manifest") is not None
