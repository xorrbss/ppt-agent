"""The durable record of one deck's contact-sheet vision advisory.

``vision_advisory.run_vision_advisory`` answers about the renders it was
handed; a later reader (the export quality receipt) must be able to tell
whether that answer still describes the deck it is exporting. The record
therefore binds the advisory's own result to the digest of every page render
it looked at, in order, and carries one content digest over both. A page
remade after publication changes its render digest, so the record stops
matching and the receipt reports the advisory as stale instead of passing a
review that never saw the page.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any

ADVISORY_RECORD_SCHEMA = "deck-vision-advisory/v1"


def content_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + sha256(payload.encode()).hexdigest()


def advisory_record(vision: Mapping[str, Any], page_digests: Sequence[str]) -> dict[str, Any]:
    """Bind an advisory result to the page renders it reviewed."""

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
    return (isinstance(vision, Mapping) and "skipped" not in vision
            and isinstance(vision.get("slides"), list))


def record_matches(record: Mapping[str, Any], page_digests: Sequence[str]) -> bool:
    """Whether the record is intact and reviewed exactly these page renders."""

    body = {key: record.get(key) for key in ("schemaVersion", "vision", "pageDigests")}
    return (record.get("schemaVersion") == ADVISORY_RECORD_SCHEMA
            and record.get("digest") == content_digest(body)
            and list(record.get("pageDigests") or []) == [str(d) for d in page_digests])


def advisory_flagged_pages(record: Mapping[str, Any]) -> list[int]:
    """0-based indices the advisory named at least one issue on."""

    slides = (record.get("vision") or {}).get("slides") or []
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
