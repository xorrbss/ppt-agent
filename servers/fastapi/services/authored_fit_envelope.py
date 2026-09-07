"""Reseal a slide's scene envelope over the HTML the in-render fit produced.

The draft pipeline seals a slide's scene envelope before its first render and
takes ``html_projection`` as the document of record.  When the in-render fit
changes the document, the fitted string becomes what the ledger, the
checkpoint and the metrics cache hold, and the envelope must follow it the
same way a re-authored slide is resealed.  Bench run 43 (2026-09-06) is what
happens otherwise: every fitted slide reads as stale at the final metrics
readback and the deck fails ``authored_composition_safety_gate_failed``.
"""

from __future__ import annotations

import uuid

from services.authored_checkpoint_errors import AuthoredStageError
from services.authored_vnext.generation.scene_checkpoint import persist_scene_envelope
from services.authored_vnext.generation.scene_envelope import (
    AuthoredSlideSceneEnvelope,
    seal_slide_scene_envelope,
)


def reseal_scene_envelope_after_fit(
    *,
    fitted_html: str | None,
    scene_envelope: AuthoredSlideSceneEnvelope,
    slide_index: int,
    generation_job_id: uuid.UUID | None,
) -> AuthoredSlideSceneEnvelope:
    """Return the envelope that matches ``fitted_html``; persist it when the job is durable."""

    if fitted_html is None or fitted_html == scene_envelope.html_projection:
        return scene_envelope
    try:
        resealed = seal_slide_scene_envelope(fitted_html, slide_index=slide_index)
    except (TypeError, ValueError) as exc:
        raise AuthoredStageError(
            slide_index=slide_index,
            stage="render",
            code="authored_scene_envelope_invalid",
            cause=exc,
            retryable=False,
        ) from exc
    if generation_job_id is not None:
        persist_scene_envelope(generation_job_id, resealed)
    return resealed


__all__ = ["reseal_scene_envelope_after_fit"]
