"""Legacy inline source-image manifest for SVG generation.

Builds the pre-canonical-assets inline ``data:`` URL manifest that pages use
when the slot-writer / canonical asset pipeline is not in play.

Extracted out of ``svg_presentation_service.py`` (kept at/under the file-size
ratchet); the facade there re-exports this for importers.
"""
from __future__ import annotations

import base64
from hashlib import sha256


def _legacy_source_image_manifest(offered: dict) -> dict:
    """Build and validate the existing inline format without provider state."""
    from services.svg_asset_manifest import decide_preservation_policy
    from services.svg_image_derivative import build_model_preview
    from utils.llm_calls.author_slide_svg import preflight_svg_source_images

    manifest = {}
    for slot, candidate in offered.items():
        if candidate.path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("svg_asset_source_byte_budget_exceeded")
        data = candidate.path.read_bytes()
        if sha256(data).hexdigest() != candidate.digest.removeprefix("sha256:"):
            raise ValueError("svg_source_image_digest_mismatch")
        # Apply the old raster limit to a bounded derivative, never directly
        # to a valid original. The source file and source digest stay intact.
        preview = build_model_preview(data, max_bytes=min(1536 * 1024, (2 * 1024 * 1024) // max(1, len(offered))),
                                      max_edge=4096)
        data, mime = preview.payload, preview.media_type
        # Preserve the legacy required-image behavior through the central
        # policy. The future slot writer will supply occurrence decisions.
        policy = decide_preservation_policy(selected=True, preservation_class="supporting",
                                            source_preservation_required=True)
        manifest[slot] = {"data_url": f"data:{mime};base64," + base64.b64encode(data).decode(),
                          "description": candidate.semantic_description or candidate.source_heading or "Source image",
                          "required": policy.required, "allowOmit": policy.allow_omit,
                          "allowExcludeOnFailure": policy.allow_exclude_on_failure,
                          "policyVersion": policy.policy_version, "asset_id": candidate.asset_id,
                          "source_digest": candidate.digest,
                          "model_preview": preview.metadata(),
                          "digest": "sha256:" + sha256(data).hexdigest()}
    return preflight_svg_source_images(manifest)
