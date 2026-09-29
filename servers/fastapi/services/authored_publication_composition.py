"""Composition checks that seal an Authored publication to one pin.

Company-standard furniture identity and the preview/publication/native-export
composition pin are validated before the publication transaction opens, so a
mismatch fails the persist stage instead of publishing a deck whose exported
composition differs from the one the request approved.
"""

from __future__ import annotations

from models.generate_presentation_request import GeneratePresentationRequest
from services.authored_checkpoint_service import AuthoredStageError
from services.presentation_export_attempt_service import sha256_canonical_json


def _authored_company_standard_projection(
    request: GeneratePresentationRequest,
    htmls: list[str],
) -> dict[str, object] | None:
    """Keep company furniture identity separate from the Authored HTML body.

    New Authored generation no longer consumes the heuristic Scene compiler.
    Until a bounded furniture overlay can preserve arbitrary Authored fidelity,
    the selected standard is persisted as a sealed, explicitly unapplied
    compatibility constraint instead of being presented as visual output.
    """

    resolved = request._authored_master_resolution
    composition = request._authored_composition_pin
    if resolved is None and composition is None:
        return None
    if resolved is None or composition is None:
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_company_standard_checkpoint_incomplete",
            retryable=False,
        )
    if resolved.pin.master_digest != composition.master_digest:
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_company_standard_digest_mismatch",
            retryable=False,
        )
    return {
        "schemaVersion": "authored-company-standard-projection/v1",
        "body": {
            "kind": "authored-html",
            "sourceDigest": "sha256:" + sha256_canonical_json(htmls),
        },
        "furniture": {
            "digest": composition.company_standard_pin.digest,
            "masterDigest": composition.master_digest,
            "applied": False,
            "fallbackReason": "arbitrary_authored_html_furniture_overlay_unsupported",
        },
        "masterPin": resolved.pin.model_dump(mode="json", by_alias=True),
        "compositionPin": composition.model_dump(mode="json", by_alias=True),
        "safeArea": resolved.contract.canvas.safe_area.model_dump(
            mode="json", by_alias=True
        ),
        "safeAreaEnforced": False,
        "visualParity": "not-verified",
    }


def _require_immutable_composition_publication(
    *,
    request: GeneratePresentationRequest,
    master_generation,
    native_export_evidence: dict[str, object] | None,
) -> None:
    """Seal preview, publication, and native export to one composition pin."""

    if master_generation is None:
        return
    deck = master_generation.result.deck
    composition = deck.composition
    scene = deck.scene
    requested = request._authored_composition_pin
    if (
        scene is None
        or composition is None
        or requested is None
        or requested != composition
        or master_generation.composition_pin != composition
        or scene.composition_digest != composition.digest
    ):
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_composition_publication_mismatch",
            retryable=False,
            message="The company-standard/authored style composition of the request and the preview do not match.",
        )

    if request.export_as != "pptx":
        return
    preview = (
        native_export_evidence.get("preview_snapshot")
        if isinstance(native_export_evidence, dict)
        else None
    )
    native = (
        native_export_evidence.get("native_pptx_snapshot")
        if isinstance(native_export_evidence, dict)
        else None
    )
    if (
        not isinstance(preview, dict)
        or not isinstance(native, dict)
        or preview != native
        or preview.get("composition_digest") != composition.digest
        or native_export_evidence.get("resolved_composition_digest")
        != composition.digest
        or native_export_evidence.get("scene_digest") != scene.scene_digest
        or native_export_evidence.get("scene_composition_digest")
        != scene.composition_digest
        or native_export_evidence.get("scene_slide_digests")
        != [slide.slide_digest for slide in scene.slides]
        or native_export_evidence.get("readback_verified") is not True
    ):
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_composition_export_evidence_mismatch",
            retryable=False,
            message="The composition verification of the preview and the PowerPoint export do not match.",
        )


__all__ = [
    "_authored_company_standard_projection",
    "_require_immutable_composition_publication",
]
