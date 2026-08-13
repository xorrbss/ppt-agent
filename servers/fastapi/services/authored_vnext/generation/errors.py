"""Stable fail-closed errors for authored vNext generation."""

from __future__ import annotations


class GenerationPipelineError(RuntimeError):
    code = "authored_vnext_generation_failed"


class PublishedMasterRequiredError(GenerationPipelineError):
    code = "authored_vnext_published_master_required"


class StaleMasterError(GenerationPipelineError):
    code = "authored_vnext_stale_master"


class GenerationConflictError(GenerationPipelineError):
    code = "authored_vnext_generation_conflict"


class GenerationNotFoundError(GenerationPipelineError):
    code = "authored_vnext_generation_not_found"


class CandidateRejectedError(GenerationPipelineError):
    code = "authored_vnext_candidate_rejected"


class RollbackConflictError(GenerationPipelineError):
    code = "authored_vnext_rollback_conflict"


class CoreBridgeExportError(GenerationPipelineError):
    code = "authored_vnext_core_bridge_export_failed"
