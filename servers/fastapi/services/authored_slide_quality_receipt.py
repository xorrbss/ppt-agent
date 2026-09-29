"""Per-slide infographic quality receipts, written beside QualityReceipt v2.

QualityReceipt v2 (``services/authored_quality_receipt_v2.py``) is a sealed,
digest-bound deck verdict: its field set is part of a manifest schema that
refuses unknown shapes and its digest is recomputed on every load.  Per-slide
figure outcomes are measurement, not release authority, and they arrive from
two different stages -- generation knows the archetype and the chosen variant,
hybrid export knows how many elements fell back to raster.  Widening the sealed
receipt for either would break its digest and its manifest validation.

So this module writes a sidecar in the same durable directory, exactly as the
generation timing receipt already does (``<stem>-timing.json``).  The sidecar
is keyed by PRESENTATION id rather than generation-job id because it is written
from two stages that do not share a job: the synchronous local generation route
has no job at all, and export completion runs long after the job is gone.

Nothing here blocks publication.  Every writer fails open: a measurement file
must never be the reason a deck does not ship.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.authored_quality_receipt_v2 import _manifest_lock, quality_receipt_root
from utils.hashing import sha256_hex, sha256_prefixed

LOGGER = logging.getLogger(__name__)

SLIDE_QUALITY_MANIFEST_SCHEMA = "authored-slide-quality-manifest/v1"
SLIDE_QUALITY_RECORD_SCHEMA = "authored-slide-quality/v1"


def slide_quality_receipt_path(presentation_id: object) -> Path:
    """Return the sidecar path for one presentation's per-slide quality file.

    The identifier is hashed before it reaches the filesystem for the same
    reason ``quality_receipt_manifest_path`` hashes a job id.
    """

    value = str(presentation_id or "").strip()
    if not value:
        raise ValueError("slide_quality_presentation_id_missing")
    digest = sha256_hex(value.encode("utf-8"))[:32]
    return quality_receipt_root() / f"deck-{digest}-slide-quality.json"


@dataclass(frozen=True, slots=True)
class AuthoredSlideQualityRecord:
    """One slide's measured figure outcome.

    ``variant_chosen`` and ``critic_score`` are produced by the best-of-N and
    design-critic stage.  Until that stage lands they are written as ``None``
    rather than invented, which keeps the aggregate honest: the weekly report
    counts them as "not measured", not as a zero.
    """

    slide_index: int
    #: ``None`` marks a partial record written by a later stage that knows only
    #: some fields; the merge then leaves the recorded archetype alone.
    archetype: str | None = None
    density: str | None = None
    weight: str | None = None
    node_count: int | None = None
    variant_chosen: str | None = None
    critic_score: float | None = None
    fallback_count: int | None = None
    #: E4 (docs/generation-programme-master-design-20260904.md section 8):
    #: this slide's fired A2 admission normalisation rules (possibly an empty
    #: tuple, meaning "measured, none fired"). ``None`` means "not measured by
    #: this write" -- the generation stage is the only writer that knows it,
    #: so a later export-only write must not erase it.
    normalizations: tuple[dict[str, Any], ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": SLIDE_QUALITY_RECORD_SCHEMA,
            "slideIndex": self.slide_index,
            "archetype": self.archetype,
            "density": self.density,
            "weight": self.weight,
            "nodeCount": self.node_count,
            "variantChosen": self.variant_chosen,
            "criticScore": self.critic_score,
            "fallbackCount": self.fallback_count,
            "normalizations": (
                [dict(entry) for entry in self.normalizations]
                if self.normalizations is not None
                else None
            ),
        }

    as_dict = to_dict


def build_slide_quality_records(
    *,
    slide_count: int,
    infographic_plan: object = None,
    variants: Mapping[int, str] | None = None,
    critic_scores: Mapping[int, float] | None = None,
    fallback_counts: Mapping[int, int] | None = None,
    normalizations: Mapping[int, Iterable[Mapping[str, Any]]] | None = None,
) -> tuple[AuthoredSlideQualityRecord, ...]:
    """Project the frozen infographic plan onto one record per slide.

    A slide the plan does not cover -- a deck authored before the plan existed,
    or a slide added after the plan was frozen -- still gets a record, with the
    archetype ``none``.  A missing plan therefore degrades to a receipt that
    says "no figure was planned", never to a missing receipt.

    ``normalizations`` (E4) is the deck's per-slide map of fired A2 admission
    rules, keyed the same way as ``variants``/``critic_scores``. A slide
    absent from the map is left ``None`` (not measured by this write) rather
    than an invented empty list.
    """

    variants = variants or {}
    critic_scores = critic_scores or {}
    fallback_counts = fallback_counts or {}
    normalizations = normalizations or {}
    records: list[AuthoredSlideQualityRecord] = []
    for index in range(max(int(slide_count), 0)):
        entry = None
        if infographic_plan is not None:
            try:
                entry = infographic_plan.at(index)
            except Exception:  # pragma: no cover - a foreign plan shape
                entry = None
        slide_normalizations = normalizations.get(index)
        records.append(
            AuthoredSlideQualityRecord(
                slide_index=index,
                archetype=str(getattr(entry, "archetype", "none") or "none"),
                density=(
                    str(entry.density) if getattr(entry, "density", None) else None
                ),
                weight=(str(entry.weight) if getattr(entry, "weight", None) else None),
                node_count=(
                    len(entry.nodes) if getattr(entry, "nodes", None) is not None else None
                ),
                variant_chosen=_optional_text(variants.get(index)),
                critic_score=_optional_number(critic_scores.get(index)),
                fallback_count=_optional_count(fallback_counts.get(index)),
                normalizations=(
                    tuple(dict(entry) for entry in slide_normalizations)
                    if slide_normalizations is not None
                    else None
                ),
            )
        )
    return tuple(records)


def _optional_text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _optional_number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_count(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    return count if count >= 0 else None


def _manifest_digest(slides: Mapping[str, Any]) -> str:
    payload = json.dumps(slides, sort_keys=True, separators=(",", ":"))
    return sha256_prefixed(payload.encode("utf-8"))


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if (
        not isinstance(payload, Mapping)
        or payload.get("schemaVersion") != SLIDE_QUALITY_MANIFEST_SCHEMA
        or not isinstance(payload.get("slides"), Mapping)
    ):
        return {}
    digest = payload.get("manifestDigest")
    if digest is not None and digest != _manifest_digest(payload["slides"]):
        # A torn or hand-edited measurement file is discarded, not repaired.
        return {}
    return dict(payload)


def _write_manifest(
    path: Path,
    *,
    presentation_id: str,
    slides: Mapping[str, Any],
    attempt_number: int | None,
) -> None:
    document = {
        "schemaVersion": SLIDE_QUALITY_MANIFEST_SCHEMA,
        "presentationId": presentation_id,
        "attemptNumber": attempt_number,
        "updatedAt": int(time.time()),
        "slides": dict(slides),
        "manifestDigest": _manifest_digest(slides),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def record_slide_quality_receipt(
    presentation_id: object,
    records: Iterable[AuthoredSlideQualityRecord],
    *,
    attempt_number: int | None = None,
) -> Path | None:
    """Write (or merge into) the per-slide receipt for one presentation.

    Merging is field-wise and non-destructive: a later stage that knows only
    ``fallback_count`` must not erase the archetype generation recorded.
    """

    try:
        path = slide_quality_receipt_path(presentation_id)
        with _manifest_lock(path):
            existing = _read_manifest(path)
            slides = dict(existing.get("slides") or {})
            for record in records:
                key = str(record.slide_index)
                merged = dict(slides.get(key) or {})
                for field, value in record.to_dict().items():
                    if value is not None or field not in merged:
                        merged[field] = value
                slides[key] = merged
            _write_manifest(
                path,
                presentation_id=str(presentation_id),
                slides=slides,
                attempt_number=(
                    attempt_number
                    if attempt_number is not None
                    else existing.get("attemptNumber")
                ),
            )
        return path
    except Exception:  # pragma: no cover - measurement never blocks a deck
        LOGGER.warning(
            "[authored] slide quality receipt not written for %s",
            presentation_id,
            exc_info=True,
        )
        return None


def merge_slide_quality_fallbacks(
    presentation_id: object, fallback_counts: Mapping[int, int]
) -> Path | None:
    """Fold hybrid export's per-slide raster-fallback counts into the receipt.

    Export coverage numbers slides from 1; the receipt indexes them from 0, the
    same way the infographic plan does. Callers convert.
    """

    records = tuple(
        AuthoredSlideQualityRecord(slide_index=int(index), fallback_count=count)
        for index, count in sorted(fallback_counts.items())
        if _optional_count(count) is not None
    )
    if not records:
        return None
    return record_slide_quality_receipt(presentation_id, records)


def load_slide_quality_receipt(presentation_id: object) -> dict[str, Any]:
    """Read one presentation's receipt back, or ``{}`` when there is none."""

    try:
        return _read_manifest(slide_quality_receipt_path(presentation_id))
    except Exception:
        return {}


__all__ = [
    "SLIDE_QUALITY_MANIFEST_SCHEMA",
    "SLIDE_QUALITY_RECORD_SCHEMA",
    "AuthoredSlideQualityRecord",
    "build_slide_quality_records",
    "load_slide_quality_receipt",
    "merge_slide_quality_fallbacks",
    "record_slide_quality_receipt",
    "slide_quality_receipt_path",
]
