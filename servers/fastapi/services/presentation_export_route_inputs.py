"""Pure input derivations shared by the presentation export router.

These helpers own the deterministic, side-effect-free parts of routing an
export: hashing a finished artifact, reading template provenance off a
presentation theme, and pairing persisted slides with their template layouts.
They were split out of ``services/presentation_export_router.py`` so the router
module keeps only the async orchestration; each helper here is synchronous,
depends on nothing the router owns, and is therefore unit-testable on its own.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path

from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.presentation_export_capabilities import (
    PresentationExportContractError,
    PresentationExportFreshnessError,
    PresentationExportSlideInput,
)
from services.authored_local_fit_policy import html_digest
from services.template_v2_generation_service import TemplateV2GenerationTarget


def hash_export_artifact(path: Path) -> tuple[str, int]:
    digest = sha256()
    size_bytes = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size_bytes += len(chunk)
    return digest.hexdigest(), size_bytes


def resolve_template_identity(
    presentation: PresentationModel,
) -> tuple[str, int, str] | None:
    theme = presentation.theme
    if not isinstance(theme, Mapping):
        return None
    raw = theme.get("template_v2")
    if not isinstance(raw, Mapping):
        return None
    template_id = raw.get("template_v2_id")
    revision = raw.get("template_v2_revision")
    digest = raw.get("template_snapshot_sha256")
    if (
        not isinstance(template_id, str)
        or not template_id.strip()
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 1
        or not isinstance(digest, str)
        or not digest.startswith("sha256:")
        or len(digest) != 71
    ):
        raise PresentationExportContractError(
            "presentation_export_template_provenance_invalid"
        )
    normalized_digest = digest.removeprefix("sha256:").lower()
    if any(character not in "0123456789abcdef" for character in normalized_digest):
        raise PresentationExportContractError(
            "presentation_export_template_provenance_invalid"
        )
    return template_id.strip(), revision, normalized_digest


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


def build_slide_inputs(
    target: TemplateV2GenerationTarget,
    slides: Sequence[SlideModel],
) -> tuple:
    layouts = {item.layout.id: item.layout for item in target.layouts}
    if len(layouts) != len(target.layouts):
        raise PresentationExportContractError(
            "presentation_export_template_layout_duplicate"
        )
    result = []
    for index, slide in enumerate(sorted(slides, key=lambda item: item.index)):
        layout = layouts.get(slide.layout or "")
        if layout is None or not isinstance(slide.content, Mapping):
            raise PresentationExportContractError(
                "presentation_export_slide_contract_invalid",
                path=f"slides.{index}",
            )
        source_part = (
            target.native_source.layout_source_parts.get(layout.id)
            if target.native_source is not None
            else None
        )
        result.append(
            PresentationExportSlideInput(
                layout=layout,
                content=dict(slide.content),
                source_part=source_part,
            )
        )
    return tuple(result)
