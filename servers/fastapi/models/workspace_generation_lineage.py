"""Exact legacy child-attempt eligibility, shared by writes and future backfill."""

from __future__ import annotations

import json
import uuid
from typing import Any


def previous_attempt_from_raw_payload(kind: str, raw_payload: str) -> uuid.UUID | None:
    """Match the old raw-substring prefilter followed by the decoded-key check.

    PostgreSQL JSON text can contain escaped NULs in unrelated fields; parse it
    in Python rather than applying PostgreSQL JSON scalar extraction.
    """
    if kind != "presentation.generate":
        return None
    payload: Any = json.loads(raw_payload)
    if not isinstance(payload, dict):
        return None
    value = payload.get("previous_presentation_id")
    if not isinstance(value, str):
        return None
    try:
        parent = uuid.UUID(value)
    except (ValueError, AttributeError):
        return None
    canonical = str(parent)
    return parent if value == canonical and canonical in raw_payload else None


def previous_attempt_from_payload(kind: str, payload: Any) -> uuid.UUID | None:
    """Use SQLAlchemy JSON's default serialization for a new/updated payload."""
    return previous_attempt_from_raw_payload(kind, json.dumps(payload))
