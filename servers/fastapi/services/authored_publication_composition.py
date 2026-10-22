"""Company-standard composition projection for an Authored publication.

Company-standard furniture identity is validated before the publication
transaction opens, so a mismatch fails the persist stage instead of publishing
a deck whose composition differs from the one the request approved.
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


__all__ = [
    "_authored_company_standard_projection",
]
