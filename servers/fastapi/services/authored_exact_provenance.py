"""Build and verify private exact-reconstruction source manifests.

Only content digests and stable, path-free preset metadata are persisted.  Review
rebuilds the manifest from the currently installed preset and source bytes before
any render or model call, and rejects every mismatch instead of falling back to a
style-only comparison.
"""

from __future__ import annotations

import hashlib
import json
import string
from pathlib import Path
from typing import Any, Mapping, Sequence

from utils.authored_styles import AuthoredStyle
from utils.llm_calls.author_slide import EXACT_RECONSTRUCTION, normalize_reference_mode


PROVENANCE_SCHEMA_VERSION = 1


class ExactProvenanceError(ValueError):
    """Saved exact provenance is unavailable, invalid, or no longer reproducible."""


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source_sha256(source: bytes) -> str:
    return hashlib.sha256(source).hexdigest()


def _preset_revision_payload(style: AuthoredStyle) -> dict[str, Any]:
    """Return every behavior-relevant preset field without filesystem identities."""
    return {
        "id": style.id,
        "reference_mode": normalize_reference_mode(style.reference_mode),
        "brief": style.brief,
        "category": style.category,
        "tags": list(style.tags),
        "use_cases": list(style.use_cases),
        "preview": {
            "background": style.preview_bg,
            "accent": style.preview_accent,
            "palette": style.preview_palette,
            "variant": style.preview_variant,
        },
        "tokens": {
            "primary": style.primary_color,
            "background": style.background_color,
            "heading_font": style.heading_font,
            "body_font": style.body_font,
            "mono_font": style.mono_font,
        },
        "illustration_prompt": style.illustration_prompt,
        "slide_reference_indices": list(style.slide_reference_indices),
    }


def _read_sources(paths: Sequence[Path]) -> tuple[bytes, ...]:
    sources: list[bytes] = []
    for path in paths:
        try:
            source = path.read_bytes()
        except OSError:
            raise ExactProvenanceError(
                "Exact reconstruction source is unavailable"
            ) from None
        if not source:
            raise ExactProvenanceError("Exact reconstruction source is empty")
        sources.append(source)
    return tuple(sources)


def build_exact_provenance(
    style: AuthoredStyle,
    *,
    source_images: Sequence[bytes] | None = None,
    source_snapshot_id: str | None = None,
) -> tuple[dict[str, Any], tuple[bytes, ...]]:
    """Build a path-free manifest and return the exact bytes it describes."""
    if normalize_reference_mode(style.reference_mode) != EXACT_RECONSTRUCTION:
        raise ExactProvenanceError(
            "Exact provenance requires an exact-reconstruction style"
        )
    indices = tuple(style.slide_reference_indices)
    expected_indices = tuple(range(len(style.slide_reference_images)))
    if not indices or indices != expected_indices:
        raise ExactProvenanceError(
            "Exact reconstruction source order is invalid"
        )
    sources = (
        tuple(source_images)
        if source_images is not None
        else _read_sources(style.slide_reference_images)
    )
    if len(sources) != len(indices) or any(
        not isinstance(item, bytes) for item in sources
    ):
        raise ExactProvenanceError(
            "Exact reconstruction source count does not match its mapping"
        )
    ordered_sources = [
        {"slide_index": index, "sha256": _source_sha256(source)}
        for index, source in zip(indices, sources)
    ]
    preset_revision = _canonical_sha256(_preset_revision_payload(style))
    manifest_body = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "reference_mode": EXACT_RECONSTRUCTION,
        "style_id": style.id,
        "preset_revision": preset_revision,
        "sources": ordered_sources,
    }
    if source_snapshot_id is not None:
        if len(source_snapshot_id) != 32 or any(
            character not in string.hexdigits for character in source_snapshot_id
        ):
            raise ExactProvenanceError(
                "Exact reconstruction source snapshot identity is invalid"
            )
        manifest_body["source_snapshot_id"] = source_snapshot_id.lower()
    return (
        {
            **manifest_body,
            "manifest_fingerprint": _canonical_sha256(manifest_body),
        },
        sources,
    )


def _validate_saved_shape(
    provenance: Mapping[str, Any] | None,
    *,
    expected_slide_count: int,
) -> tuple[str, list[Mapping[str, Any]]]:
    if not isinstance(provenance, Mapping):
        raise ExactProvenanceError(
            "Saved exact reconstruction provenance is missing"
        )
    if provenance.get("schema_version") != PROVENANCE_SCHEMA_VERSION:
        raise ExactProvenanceError(
            "Saved exact reconstruction provenance is unsupported"
        )
    saved_mode = provenance.get("reference_mode")
    if (
        not isinstance(saved_mode, str)
        or saved_mode.strip().lower() != EXACT_RECONSTRUCTION
    ):
        raise ExactProvenanceError("Saved exact reconstruction mode is invalid")
    style_id = provenance.get("style_id")
    if not isinstance(style_id, str) or not style_id:
        raise ExactProvenanceError(
            "Saved exact reconstruction style identity is missing"
        )
    sources = provenance.get("sources")
    if not isinstance(sources, list) or len(sources) != expected_slide_count:
        raise ExactProvenanceError("Saved exact reconstruction source count changed")
    for expected_index, source in enumerate(sources):
        if (
            not isinstance(source, Mapping)
            or source.get("slide_index") != expected_index
        ):
            raise ExactProvenanceError(
                "Saved exact reconstruction source order changed"
            )
        digest = source.get("sha256")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in string.hexdigits for character in digest)
        ):
            raise ExactProvenanceError(
                "Saved exact reconstruction source digest is invalid"
            )
    snapshot_id = provenance.get("source_snapshot_id")
    if snapshot_id is not None and (
        not isinstance(snapshot_id, str)
        or len(snapshot_id) != 32
        or any(character not in string.hexdigits for character in snapshot_id)
    ):
        raise ExactProvenanceError(
            "Saved exact reconstruction source snapshot identity is invalid"
        )
    return style_id, sources


def verify_exact_provenance(
    provenance: Mapping[str, Any] | None,
    style: AuthoredStyle,
    *,
    expected_slide_count: int,
) -> tuple[bytes, ...]:
    """Revalidate a saved manifest against the installed preset and source bytes."""
    saved_style_id, saved_sources = _validate_saved_shape(
        provenance,
        expected_slide_count=expected_slide_count,
    )
    if style.id != saved_style_id:
        raise ExactProvenanceError("Saved exact reconstruction style changed")
    snapshot_id = provenance.get("source_snapshot_id")
    current, source_images = build_exact_provenance(
        style,
        source_snapshot_id=snapshot_id if isinstance(snapshot_id, str) else None,
    )
    if len(current["sources"]) != expected_slide_count:
        raise ExactProvenanceError("Exact reconstruction source count changed")
    if [item["slide_index"] for item in current["sources"]] != [
        item["slide_index"] for item in saved_sources
    ]:
        raise ExactProvenanceError("Exact reconstruction source order changed")
    if [item["sha256"] for item in current["sources"]] != [
        item["sha256"] for item in saved_sources
    ]:
        raise ExactProvenanceError("Exact reconstruction source bytes changed")
    if current["preset_revision"] != provenance.get("preset_revision"):
        raise ExactProvenanceError("Exact reconstruction preset revision changed")
    if current["manifest_fingerprint"] != provenance.get("manifest_fingerprint"):
        raise ExactProvenanceError("Exact reconstruction manifest changed")
    return source_images
