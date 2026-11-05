"""Authorize current quality repairs without changing archived source evidence."""
from __future__ import annotations

from copy import deepcopy


def is_current_provider_control_error(error):
    from services.authored_checkpoint_errors import AuthoredStageError
    from services.provider_runtime_primitives import RuntimeControlError
    from services.public_operation_admission_models import PublicAdmissionRejected

    return isinstance(error, (RuntimeControlError, PublicAdmissionRejected)) or (
        isinstance(error, AuthoredStageError) and error.code in {
            "authored_attempt_lost", "authored_workspace_operation_fence_lost",
            "authored_workspace_operation_fencing_token_required",
            "svg_provider_completed_checkpoint_authority_expired", "svg_provider_checkpoint_authority_expired"})


def _history_prefix(ctx, payload, index):
    from services.svg_recovery_witness import author_witness

    manifest = getattr(ctx, "partial_recovery_manifest", None)
    if not manifest or index not in manifest["known_indices"]:
        return None
    attempts = payload.get("attempts") or []
    expected = manifest["pages"][index]["author_receipt_digest"]
    for count in range(1, len(attempts) + 1):
        if author_witness({"attempts": attempts[:count]}) == expected:
            return count
    raise ValueError("svg_partial_recovery_cached_source_changed")


def current_episode_attempt_count(ctx, payload, index):
    from services.generation_core.drivers.svg.quality_recovery import actual_provider_attempt_count

    prefix = _history_prefix(ctx, payload, index)
    if prefix is None:
        return actual_provider_attempt_count(payload)
    current = sum(attempt.get("kind") != "local" for attempt in payload.get("attempts", [])[prefix:])
    prior = (payload.get("quality_recovery") or {}).get("prior_candidate")
    failures = payload.get("provider_failures") or []
    baseline = len((prior.get("evidence") or {}).get("provider_failures") or []) if prior else len(failures)
    if baseline > len(failures):
        raise ValueError("svg_partial_recovery_cached_source_changed")
    return current + len(failures) - baseline


async def current_quality_repair_authorized(ctx, payload, index, job, checkpoint):
    """Read the durable first review, rather than trusting a caller's repair marker."""
    from services.generation_core.drivers.svg.quality import _gate_intact
    from services.generation_core.drivers.svg.quality_recovery import _restore_candidate
    from services.generation_core.quality.advisory_record import content_digest
    from services.svg_authoring_profile import read_quality_recovery_policy
    from services.svg_partial_recovery import verify_cached_page

    manifest = getattr(ctx, "partial_recovery_manifest", None)
    marker = payload.get("quality_recovery") or {}
    if (not manifest or index not in manifest["known_indices"] or job is None
            or marker.get("status") not in {"repair_pending", "candidate_ready", "selected", "retained"}):
        return False
    seed = await checkpoint(job, 0) or {}
    execution = seed.get("quality_recovery_execution") or {}
    rounds = execution.get("rounds") or []
    if not rounds or rounds[0].get("status") != "completed":
        return False
    gate = _gate_intact(rounds[0].get("advisory"))
    runtime = getattr(ctx, "quality_recovery_runtime_binding", None) or getattr(ctx, "quality_recovery_input_binding", None)
    expected_binding = dict(jobId=str(job.id), operationId=str(job.workspace_operation_id),
        presentationId=str(job.presentation_id))
    if (gate is None or gate.get("snapshotDigest") != content_digest(ctx.snapshot)
            or gate.get("policy") != read_quality_recovery_policy(ctx.snapshot)
            or gate.get("runtimeBinding") != runtime or execution.get("binding") != runtime
            or any((gate.get("binding") or {}).get(key) != value for key, value in expected_binding.items())
            or gate.get("pageCoverageComplete") is not True
            or len(gate.get("pageDigests", [])) != ctx.count):
        return False
    page = next((page for page in gate.get("pages", []) if page.get("index") == index), None)
    if not page or page.get("evaluated") is not True:
        return False
    targets = (page.get("designTargets") or []) if gate["policy"]["version"] == 2 else []
    if not page.get("failedAxes") and not targets:
        return False
    failed = {name: axis for name, axis in page["axes"].items() if axis.get("verdict") == "failed"}
    scope = "design_rewrite" if targets or any(axis.get("repairScope") == "design_rewrite" for axis in failed.values()) else "local_repair"
    decision = dict(repair_scope=scope, target_axes=list(dict.fromkeys([*page.get("failedAxes", []), *[target["axis"] for target in targets]])),
        issues=[dict(axis=name, **axis) for name, axis in failed.items()])
    if targets:
        decision["design_targets"] = deepcopy(targets)
    if (not failed and not targets) or marker.get("decision") != decision:
        return False
    if targets and payload.get("correction_mode") != scope:
        return False
    archive = marker.get("prior_candidate")
    witness = manifest["pages"][index]
    if (not isinstance(archive, dict) or archive.get("svg_digest") != witness["svg_sha256"]
            or gate["pageDigests"][index] != witness["svg_sha256"]
            or (payload.get("partial_recovery") or {}).get("operation_id") != str(job.workspace_operation_id)
            or payload.get("input_digest") != witness["input_digest"]):
        return False
    prior = _restore_candidate(archive, payload, job)
    prior["attempts"] = deepcopy(payload["attempts"][:_history_prefix(ctx, payload, index)])
    verify_cached_page(prior, manifest, index, witness["input_digest"])
    return True
