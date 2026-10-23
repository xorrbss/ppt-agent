"""Golden pins for the ``utils.hashing`` migration of inline SHA-256 expressions.

Each digest below was computed with the original inline expression before the
migration; the helper must reproduce it byte for byte.  The module table is a
guard that migrated modules keep routing through the shared helpers.
"""

from __future__ import annotations

import hashlib
from hashlib import sha256
import importlib
import json
from pathlib import Path

import pytest

from utils.hashing import (
    canonical_json_digest,
    canonical_json_sha256,
    sha256_hex,
    sha256_prefixed,
)

_BYTES = "한글 é payload".encode()
_VALUE = {"b": [1, 2.5, "한글"], "a": {"z": "é"}}
_GOLDEN_BYTES_HEX = "23bba76fb07dd2140f1f2b0d71f440e99ebd93d7fc3756368e1f488df87ed5fc"
_GOLDEN_CANON_STRICT = "c507fd6ba021277e2022717bf0d4c5fa829e8b9c507dd22b8000217c9f2394ef"
_GOLDEN_CANON_LEGACY = "c8456e90eb6aa2d3a239c7b69ea066b58aa8759ea2ac1f2bcbb747ce7f4c0fa6"


def test_bytes_shapes_match_the_original_inline_expressions() -> None:
    assert sha256_hex(_BYTES) == _GOLDEN_BYTES_HEX == hashlib.sha256(_BYTES).hexdigest()
    assert sha256_prefixed(_BYTES) == "sha256:" + _GOLDEN_BYTES_HEX == f"sha256:{sha256(_BYTES).hexdigest()}"


def test_canonical_json_shapes_match_the_original_inline_expressions() -> None:
    strict = json.dumps(
        _VALUE, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    assert sha256(strict).hexdigest() == _GOLDEN_CANON_STRICT
    assert canonical_json_sha256(_VALUE) == _GOLDEN_CANON_STRICT
    assert canonical_json_digest(_VALUE) == "sha256:" + _GOLDEN_CANON_STRICT
    # ``ensure_ascii``/``allow_nan`` omitted means True/True in ``json.dumps``.
    legacy = json.dumps(_VALUE, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert sha256(legacy).hexdigest() == _GOLDEN_CANON_LEGACY
    assert canonical_json_sha256(_VALUE, ensure_ascii=True, allow_nan=True) == _GOLDEN_CANON_LEGACY


_MIGRATED_MODULES = [
    "api.v1.auth.secret_store",
    "api.v1.ppt.endpoints.asset_gateway",
    "api.v1.ppt.endpoints.presentation_slide_order",
    "api.v1.ppt.endpoints.raster_reconstruction",
    "models.authored_vnext.deck.composition",
    "models.authored_vnext.deck.scene",
    "models.authored_vnext.deck.scene_digest",
    "models.sql.access_token",
    "models.sql.artifact_keys",
    "models.sql.presentation_export_attempt",
    "models.sql.presentation_export_receipt_models",
    "models.sql.public_principal_binding",
    "services.artifact_store",
    "services.authored_admission_policy",
    "services.authored_admission_shadow",
    "services.authored_author_capacity_recovery",
    "services.authored_canonical.service",
    "services.authored_checkpoint_artifacts",
    "services.authored_checkpoint_errors",
    "services.authored_checkpoint_stage_batch",
    "services.authored_checkpoint_state",
    "services.authored_composition_policy",
    "services.authored_deck_plan_checkpoint",
    "services.authored_decoration_containment",
    "services.authored_exact_provenance",
    "services.authored_exact_source_snapshot",
    "services.authored_final_metrics",
    "services.authored_generation_timing_receipt",
    "services.authored_illustration_reviews",
    "services.authored_local_fit_evidence_receipts",
    "services.authored_local_fit_evidence_status",
    "services.authored_local_fit_export",
    "services.authored_local_fit_export_evidence",
    "services.authored_local_fit_image_text",
    "services.authored_page_remake_service",
    "services.authored_planned_image_visibility",
    "services.authored_presentation_artifacts",
    "services.authored_presentation_generation",
    "services.authored_presentation_pipeline",
    "services.authored_quality_ocr",
    "services.authored_quality_receipt_v2",
    "services.authored_quality_review_service",
    "services.authored_review_asset_store",
    "services.authored_rollout_profile",
    "services.authored_slide_editor_event_service",
    "services.authored_slide_quality_receipt",
    "services.authored_source_image_stage",
    "services.authored_title_recovery_contract",
    "services.authored_vnext.body_grammar",
    "models.authored_vnext.capability_registry",
    "services.authored_vnext.composition_capacity",
    "services.authored_vnext.deck_ir.integration",
    "services.authored_vnext.deck_ir.strategy",
    "services.authored_vnext.generation.ai_text_zones",
    "services.authored_vnext.generation.gate0e_probes",
    "services.authored_vnext.generation.retained_reference",
    "services.authored_vnext.generation.sql_repository",
    "services.authored_vnext.integration.export_payload",
    "services.authored_vnext.integration.sql_repository_types",
    "services.authored_vnext.integration.worker",
    "services.authored_vnext.master.ai_import",
    "services.authored_vnext.master.digests",
    "services.authored_vnext.master.preview",
    "services.authored_vnext.narrative_ir.checkpoint",
    "services.authored_vnext.ooxml.native_scene",
    "services.authored_vnext.ooxml.repository",
    "services.authored_vnext.retained_reference_marker",
    "services.authored_vnext.semantic.canonical",
    "services.authored_vnext.semantic.completion_v3",
    "services.authored_vnext.semantic.editable_scene",
    "services.authored_vnext.semantic.qualification_v3",
    "services.authored_vnext.semantic.readiness",
    "services.authored_vnext.semantic.readiness_v3",
    "services.authored_vnext.semantic.release_authority_v3",
    "services.authored_vnext.semantic.release_evidence_v3",
    "services.authored_vnext.semantic.typography",
    "services.authored_vnext.source_native_generation",
    "services.authored_vnext.source_unitization",
    "services.chat.slide_ui_query",
    "services.compatibility_migration.archive",
    "services.decompose_job_service",
    "services.document_canvas",
    "services.generation_core.assets.icon_pack.library",
    "services.generation_core.assets.offer",
    "services.generation_core.drivers.svg.asset_slots",
    "services.generation_core.drivers.svg.project_style",
    "services.generation_core.drivers.svg.quality",
    "services.generation_core.drivers.svg.render",
    "services.generation_core.drivers.svg.terminal",
    "services.generation_core.export.svg_evidence",
    "services.generation_core.native_objects.model",
    "services.generation_core.quality.advisory_record",
    "services.generation_core.style.master_projection",
    "services.isolated_worker_contracts",
    "services.isolated_worker_process",
    "services.presentation_export_evidence_sidecars",
    "services.presentation_export_powerpoint_qa",
    "services.presentation_export_source_hashes",
    "services.presentation_generation_export_chain",
    "services.presentation_generation_job_service",
    "services.provider_durable_admission",
    "services.provider_durable_runtime",
    "services.provider_usage_idempotency",
    "services.provider_usage_instrumentation",
    "services.public_export_identity",
    "services.public_export_operation",
    "services.public_export_worker_bundle",
    "services.public_generation_route",
    "services.public_generation_worker_lifecycle",
    "services.public_operation_admission",
    "services.public_render_runtime_identity",
    "services.public_v2_orchestrator",
    "services.raster_source_assets",
    "services.references.providers",
    "services.references.service",
    "services.slidespec_authored_executor",
    "services.slidespec_authored_importer",
    "services.slidespec_repair",
    "services.source_content.browser_readback",
    "services.source_content.pptx",
    "services.source_content.source_stamp_dom",
    "services.source_images.analysis",
    "services.source_images.extract",
    "services.source_images.extract_documents",
    "services.source_images.placement",
    "services.source_images.registry",
    "services.source_locked.generation_store",
    "services.source_locked.job_store",
    "services.svg_asset_manifest",
    "services.svg_asset_persistence",
    "services.svg_asset_resolver",
    "services.svg_contract",
    "services.svg_export_assets",
    "services.svg_export_bundle",
    "services.svg_export_operation",
    "services.svg_export_page_evidence",
    "services.svg_export_quality",
    "services.svg_export_visual_review",
    "services.svg_generation_admission",
    "services.svg_generation_assets",
    "services.svg_image_derivative",
    "services.svg_page_remake",
    "services.svg_presentation_checkpoint",
    "services.svg_presentation_legacy_assets",
    "services.svg_page_publication",
    "services.svg_trusted_fonts",
    "services.trusted_executor.dispatch_evidence",
    "services.trusted_executor.wire_bundle_references",
    "services.trusted_executor.worker_dispatcher",
    "services.workspace_artifact_publication_service",
    "services.workspace_chat_memory_outbox",
    "services.workspace_cleanup_journal_service",
    "services.workspace_cleanup_storage",
    "services.workspace_export_quality_evidence",
    "services.workspace_operation_lease",
    "services.workspace_operation_runner",
    "services.workspace_quality_export_operation_handlers",
    "services.workspace_quality_receipt_digest",
    "services.workspace_service",
    "utils.artifact_style_analysis",
    "utils.artifact_style_pdf",
    "utils.artifact_style_pptx",
    "utils.authored_font_identity",
    "services.authored_illustrations",
    "utils.authored_image_slots",
    "utils.authored_ocr_quality",
    "utils.authored_qid_namespace",
    "utils.authored_style_samples",
    "utils.authored_styles",
    "utils.renderer_internal_capability",
    "utils.renderer_isolation",
    "utils.renderer_static_policy",
    "utils.renderer_worker_response",
    "utils.slide_capture",
    "utils.slide_capture_assets",
    "utils.slide_capture_fit",
    "utils.slide_capture_glyph_probe",
    "utils.slide_capture_result",
    "utils.slidespec_core",
    "utils.slidespec_solver",
    "services.generation_core.drivers.svg.svg_style_adapter",
    "utils.svg_template_fonts",
    "utils.svg_template_rules",
]

_FASTAPI_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("module_name", _MIGRATED_MODULES)
def test_migrated_module_routes_through_shared_hashing(module_name: str) -> None:
    source = (_FASTAPI_ROOT / (module_name.replace(".", "/") + ".py")).read_text(encoding="utf-8")
    assert "from utils.hashing import" in source
    assert '"sha256:" + hashlib.sha256(' not in source
    assert 'f"sha256:{hashlib.sha256(' not in source
    importlib.import_module(module_name)
