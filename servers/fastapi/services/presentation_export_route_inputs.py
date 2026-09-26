"""Pure input derivations shared by the presentation export paths.

These helpers own the deterministic, side-effect-free parts of preparing an
export: hashing a finished artifact, reading template provenance off a
presentation theme, and pairing persisted slides with their template layouts.
They are kept apart from the async orchestration that consumes them
(``services/presentation_export_direct_attempt_service.py`` and
``services/public_export_operation.py``); each helper here is synchronous,
owns no state, and is therefore unit-testable on its own.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path

from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.presentation_export_capabilities import (
    PresentationExportFreshnessError,
)
from services.authored_local_fit_policy import html_digest


def hash_export_artifact(path: Path) -> tuple[str, int]:
    digest = sha256()
    size_bytes = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size_bytes += len(chunk)
    return digest.hexdigest(), size_bytes


def resolve_authored_local_fit_receipts(
    presentation: PresentationModel,
    slides: Sequence[SlideModel],
) -> tuple[Mapping[str, object], ...]:
    """Return only receipts still bound to the persisted authored HTML."""

    if not isinstance(presentation.theme, Mapping):
        return ()
    quality = presentation.theme.get("authoredQualityGate")
    local_fit = quality.get("localFit") if isinstance(quality, Mapping) else None
    raw = local_fit.get("slides") if isinstance(local_fit, Mapping) else None
    if raw is None:
        # Pre-policy presentations remain exportable without being silently
        # described as compliant with the new font-floor contract.
        return ()
    ordered = sorted(slides, key=lambda item: (item.index, item.id))
    if not isinstance(raw, list) or len(raw) != len(ordered):
        raise PresentationExportFreshnessError(
            "authored_local_fit_export_evidence_stale"
        )
    receipts: list[Mapping[str, object]] = []
    for index, (receipt, slide) in enumerate(zip(raw, ordered, strict=True)):
        if (
            not isinstance(receipt, Mapping)
            or not isinstance(slide.html_content, str)
            or receipt.get("slideIndex") != index
            or receipt.get("finalHtmlDigest") != html_digest(slide.html_content)
        ):
            raise PresentationExportFreshnessError(
                "authored_local_fit_export_evidence_stale",
                path=f"slides.{index}",
            )
        receipts.append(dict(receipt))
    return tuple(receipts)
