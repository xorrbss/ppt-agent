"""Bounded scalar field validators shared by every generation timing receipt.

Split out of ``services/authored_generation_timing_receipt.py`` to keep that
module under the file-size ratchet, and to give the observability vocabulary a
dependency it can import without reaching back into the receipt module.

These two validators are the only things the v1 receipt, the v2 attempt
receipt and the observability metrics all agree on: a duration is a finite
non-negative number clamped to a sane ceiling, and an identity digest is a
``sha256:``-prefixed hex string that must be present.
"""

from __future__ import annotations

from services.authored_quality_receipt_v2 import _optional_prefixed_sha256


MAX_GENERATION_STAGE_TIMING_MS = 30 * 24 * 60 * 60 * 1000


def _bounded_timing_ms(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"generation_timing_{field_name}_invalid")
    numeric = float(value)
    if not (numeric >= 0.0 and numeric < float("inf")):
        raise ValueError(f"generation_timing_{field_name}_invalid")
    return min(round(numeric), MAX_GENERATION_STAGE_TIMING_MS)


def _required_sha256(value: object, field_name: str) -> str:
    digest = _optional_prefixed_sha256(value, field_name)
    if digest is None:
        raise ValueError(f"generation_timing_{field_name}_invalid")
    return digest
