"""Witness digests shared by the svg recovery paths.

Moved verbatim from the retired ``svg_cached_author_render_recovery`` module.
Stored approval manifests, page witnesses and archive proofs carry their
values, so their bytes must not change.
"""

from __future__ import annotations

from utils.hashing import canonical_json_sha256


def digest(value: object) -> str:
    return canonical_json_sha256(value, allow_nan=True)


def author_witness(payload: dict) -> str:
    from services.svg_classification_remeasurement import author_witness_digest

    return author_witness_digest(payload)


def unknown_provider(value: object) -> bool:
    if isinstance(value, dict):
        return any(unknown_provider(item) for item in value.values())
    if isinstance(value, list):
        return any(unknown_provider(item) for item in value)
    return value in {
        "public_provider_result_unknown", "public_provider_timeout",
        "provider_outcome:public_provider_result_unknown",
    } if isinstance(value, str) else False
