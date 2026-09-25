"""Pure-function ExportEvidenceDigests builder for the SVG driver's export step.

Reuses ``services.public_export_identity.ExportEvidenceDigests`` (the shape
authored's single-flight export identity already stands on) rather than
inventing a parallel one. This module only *builds* the digests; it does not
join ``services.public_export_identity``'s single-flight authority
(``claim_export_identity``/``complete_export_identity``) -- svg does not use
that authority yet. N1 (native-objects, Phase 2) wires the actual join once
svg exports start using it; this builder exists so today's receipt already
records the same digest shape N1 will bind to
(agreed with ppt-agent-fe 2026-09-26).

``semantic_digest`` currently equals ``scene_digest``: svg has no separate
semantic-extraction pass distinct from its rendered scene (unlike authored's
``editable_scene.py``). Revisit if/when one is introduced.

``font_manifest_digest`` identifies the *trusted font registry* the render
pipeline trusts (``services.svg_trusted_fonts``), not whether this particular
export requested pptx font embedding -- that per-request choice already lives
in the bundle payload's own ``fontEmbedding`` flag and is covered by
``exporter_digest`` through the converter identity.
"""

from __future__ import annotations

import hashlib
import json


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_svg_export_evidence_digests(
    *,
    svgs,
    notes,
    source_assets=None,
    source_preservation=None,
    quality_receipt: dict,
    converter_version: str,
    converter_digest: str,
):
    """Build the five evidence digests for one svg export attempt.

    ``quality_receipt`` is the already-built receipt dict (from
    ``services.svg_export_quality.source_svg_public_quality_receipt`` or the
    legacy-fidelity fallback) -- its content, not its identity, is what binds
    ``quality_digest``.
    """
    from services.public_export_identity import ExportEvidenceDigests
    from services.svg_trusted_fonts import read_trusted_pretendard_faces

    scene_digest = _digest({
        "svgs": list(svgs), "notes": list(notes),
        "assets": source_assets, "sourcePreservation": source_preservation,
    })
    font_manifest_digest = _digest({
        "family": "Pretendard",
        "faces": [{"weight": weight, "digest": digest} for weight, digest, _payload in read_trusted_pretendard_faces()],
    })
    return ExportEvidenceDigests(
        scene_digest=scene_digest,
        semantic_digest=scene_digest,
        quality_digest=_digest(quality_receipt),
        exporter_digest=_digest({"converterVersion": converter_version, "converterDigest": converter_digest}),
        font_manifest_digest=font_manifest_digest,
    )


__all__ = ["build_svg_export_evidence_digests"]
