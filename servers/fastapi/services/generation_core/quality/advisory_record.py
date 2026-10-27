"""The durable record of one deck's contact-sheet vision advisory.

``vision_advisory.run_vision_advisory`` answers about the renders it was
handed; a later reader (the export quality receipt) must be able to tell
whether that answer still describes the deck it is exporting. The record
therefore binds the advisory's own result to the source digest of every page
in the deck, in order, and carries one content digest over both. Reviewed PNG
coverage is separate: read it through ``review_coverage``, since a valid record
can cover only a subset of the deck. A page
remade after publication changes its render digest, so the record stops
matching and the receipt reports the advisory as stale instead of passing a
review that never saw the page.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any
from utils.hashing import sha256_prefixed
from services.generation_core.quality.review_coverage import review_coverage

ADVISORY_RECORD_SCHEMA = "deck-vision-advisory/v1"


def content_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return sha256_prefixed(payload.encode())


def advisory_record(vision: Mapping[str, Any], page_digests: Sequence[str]) -> dict[str, Any]:
    """Bind an advisory result to the complete source-page identity vector."""

    body = {
        "schemaVersion": ADVISORY_RECORD_SCHEMA,
        "vision": dict(vision),
        "pageDigests": [str(digest) for digest in page_digests],
    }
    return {**body, "digest": content_digest(body)}


def advisory_ran(record: object) -> bool:
    """Whether the record holds a real advisory answer, not a skip."""

    if not isinstance(record, Mapping):
        return False
    vision = record.get("vision")
    return isinstance(vision, Mapping) and review_coverage(vision).ran


def record_matches(record: Mapping[str, Any], page_digests: Sequence[str]) -> bool:
    """Whether the record is intact and belongs to these source pages (not coverage)."""

    body = {key: record.get(key) for key in ("schemaVersion", "vision", "pageDigests")}
    return (record.get("schemaVersion") == ADVISORY_RECORD_SCHEMA
            and record.get("digest") == content_digest(body)
            and list(record.get("pageDigests") or []) == [str(d) for d in page_digests])


def advisory_flagged_pages(record: Mapping[str, Any]) -> list[int]:
    """0-based indices the advisory named at least one issue on."""

    slides = review_coverage(record.get("vision")).findings()
    return sorted({int(entry["index"]) for entry in slides
                   if isinstance(entry, Mapping) and isinstance(entry.get("index"), int)
                   and entry.get("issues")})


__all__ = [
    "ADVISORY_RECORD_SCHEMA",
    "advisory_flagged_pages",
    "advisory_ran",
    "advisory_record",
    "content_digest",
    "record_matches",
]
