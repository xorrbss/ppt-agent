"""Code identity for the source-proved whole-page export path."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_FILES = (
    "services/svg_export_whole_page_verifier.py",
    "services/svg_pptx_font_embedding.py",
    "services/svg_trusted_fonts.py",
    "services/authored_vnext/semantic/typography.py",
    "utils/svg_template_fonts.py",
    "services/svg_text_advance.py",
    "services/svg_export_whole_page_adapter.py",
    "services/svg_export_source_preservation.py",
    "services/svg_export_operation.py",
    "services/svg_export_bundle.py",
    "services/svg_export_identity.py",
    "services/svg_export_quality.py",
    "services/svg_final_pptx_review.py",
    "services/svg_quality_review_service.py",
    "models/svg_quality_review.py",
    "services/generation_core/quality/vision_advisory.py",
    "services/generation_core/quality/vision_advisory_prompt.py",
    "services/svg_source_layout.py",
    "services/svg_source_charts.py",
    "services/svg_source_native_tables.py",
    "services/generation_core/drivers/svg/native_chart_visual.py",
    "services/generation_core/drivers/svg/export_chart_style.py",
    "services/generation_core/drivers/svg/native_chart.py",
    "services/generation_core/drivers/svg/native_geometry.py",
    "services/svg_contract.py",
    "services/source_content/manifest_store.py",
    "services/source_content/contracts.py",
    "services/source_content/digests.py",
    "services/source_content/pptx_chart_provenance.py",
    "services/source_content/pptx_package_parts.py",
    "services/source_content/pptx_extraction_constants.py",
    "services/source_content/security.py",
    "workers/public_svg_export_worker.py",
    "workers/public_svg_whole_page_convert.py",
    "workers/public_svg_pptx_readback.py",
    "workers/public_svg_pptx_render.py",
)


def whole_page_verifier_digest() -> str:
    """The whole-page verifier identity that bundles, coverage proofs and export identities carry.

    It is the reviewed revision's identity (``services/verifier_revisions``) while
    the running source is a reviewed state of that revision, and the raw
    ``whole_page_verifier_source_digest()`` otherwise.
    """
    from services.verifier_revisions import WHOLE_PAGE_VERIFIER_LEDGER

    return WHOLE_PAGE_VERIFIER_LEDGER.resolve(whole_page_verifier_source_state())


def whole_page_verifier_source_digest() -> str:
    """The pre-migration identity: the verifier files' bytes as they are on disk."""
    return whole_page_verifier_source_state().raw


def whole_page_verifier_source_state():
    from services.verifier_revisions import WHOLE_PAGE_VERIFIER_LEDGER

    return WHOLE_PAGE_VERIFIER_LEDGER.source_state(
        {name: _ROOT / name for name in _FILES}, whole_page_verifier_identity)


def whole_page_verifier_identity(hashes) -> str:
    """Identity formula over ``{file: SHA-256}``: the SHA-256 of the sorted compact JSON."""
    payload = json.dumps(dict(hashes), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()
