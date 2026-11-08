"""Bounded recovery orchestration using the existing author/render workers."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import json

from pydantic import BaseModel

from services.authored_checkpoint_errors import AuthoredStageError
from services.svg_authoring_profile import read_quality_recovery_plan
from services.svg_authoring_profile import read_quality_recovery_execution_plan
from services.svg_presentation_checkpoint import _digest, _provider_attempt_count, _provider_attempt_limit
from services.generation_core.quality.vision_advisory_prompt import (
    DESIGN_PRINCIPLES_VERSION, baseline_design_principles_digest,
)
from utils.hashing import sha256_hex


_RETAINABLE_REPAIR_CODES = frozenset({"svg_validation_failed", "svg_source_quality_failed",
                                      "svg_repair_budget_exhausted"})


def _repair_candidate_retainable(code):
    # A repair candidate that breaks the source's native table/chart structure is
    # discarded in favour of the already-accepted prior. Deck-level source identity,
    # digest and plan codes are not candidate rejections and stay fatal.
    return code in _RETAINABLE_REPAIR_CODES or (
        isinstance(code, str) and code.startswith("svg_source_native_"))


def _quality_json_value(value):
    """Copy typed quality inputs into JSON values without changing render inputs."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {key: _quality_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_quality_json_value(item) for item in value]
    return value


def actual_provider_attempt_count(payload):
    return _provider_attempt_count(payload) + len(payload.get("provider_failures", []))


def _review_scope_principles(scope):
    """An existing scope without a marker keeps its original legacy criteria."""
    baseline_design_principles_digest(scope)
    return scope.get("reviewPrinciplesVersion")


@dataclass
class ScreenRecoveryPending:
    payload: dict
    error: AuthoredStageError


def recovery_content(ctx, index, content):
    """Read existing explanation-group membership without changing its selection."""
    groups = {}
    offer = getattr(ctx, "asset_offer", None)
    if offer is not None and offer.slots[index]:
        return None
    planned = ctx.planned[index]
    if set(planned) != set(ctx.manifests[index]):
        return None
    for slot, candidate in planned.items():
        group = (candidate.source_locator or {}).get("contentGroup")
        if (not isinstance(group, dict) or group.get("version") != "source-content-group/v1"
                or not isinstance(group.get("groupId"), str) or not group["groupId"]
                or group.get("referenceStatus") == "ambiguous"):
            return None
        groups.setdefault(group["groupId"], []).append(slot)
    return {"text": content, "image_member_count": len(planned),
            "image_groups": [sorted(groups[key]) for key in sorted(groups)]}


def recovery_pool(ctx, index, content):
    from services.generation_core.drivers.svg.fallback import validated_recovery_candidates
    if (ctx.source_reflow or ctx.raster_reconstruction
            or getattr(ctx, "quality_recovery_checkpoint_reuse_eligible", False) is not True):
        return []
    source = recovery_content(ctx, index, content)
    if source is None:
        return []
    return validated_recovery_candidates(ctx.snapshot, source, ctx.canvas,
        current_validation_context=ctx.quality_recovery_validation_context,
        allowed_source_evidence=getattr(ctx, "quality_recovery_source_evidence", None))


def reserved_recovery_candidate(ctx, index, content, payload):
    marker = payload.get("quality_recovery_fallback") or {}
    plan = read_quality_recovery_plan(ctx.snapshot)
    if (not plan or plan["page_attempt_limit"] != 3 or marker.get("status") != "reserved"
            or marker.get("binding") != ctx.quality_recovery_input_binding
            or marker.get("input_digest") != payload.get("input_digest")
            or marker.get("consumed_attempt_count") != 2
            or actual_provider_attempt_count(payload) not in (2, 3)
            or payload.get("correction_mode") != "validated_svg_adaptation"):
        raise ValueError("svg_validated_recovery_reservation_invalid")
    candidate = next((value for value in recovery_pool(ctx, index, content)
        if (value["id"], value["revision"], value["svg_digest"], value["png_digest"])
        == (marker.get("candidate_id"), marker.get("candidate_revision"),
            marker.get("svg_digest"), marker.get("png_digest"))), None)
    if candidate is None:
        raise ValueError("svg_validated_recovery_reservation_stale")
    return candidate


async def recover_screen_pages(ctx, request, presentation_id, outline, job, pages, state, hooks, attempt_loop):
    """Reserve verified candidates in page order, then use the existing workers."""
    from services.generation_core.drivers.svg.fallback import select_validated_recovery_candidate
    recent = [page for page in pages if not isinstance(page, ScreenRecoveryPending)]
    recent_ids = [page[0].get("quality_recovery_fallback", {}).get("candidate_id") for page in recent]
    reserved = []
    for index, page in enumerate(pages):
        if not isinstance(page, ScreenRecoveryPending):
            continue
        await ensure_recovery_authority(job, hooks)
        await refresh_recovery_source_authority(ctx, hooks)
        payload = page.payload
        candidate = select_validated_recovery_candidate(recovery_pool(ctx, index, outline.slides[index].content),
            recent_pages=recent, recent_candidate_ids=recent_ids,
            allowed_source_evidence=ctx.quality_recovery_source_evidence)
        if candidate is None:
            payload["quality_recovery_fallback"] = {"status": "blocked", "reason": "validated_candidate_unavailable"}
            await hooks.checkpoint(job, index, payload=payload)
            raise page.error
        payload["quality_recovery_fallback"] = {"status": "reserved",
            "binding": ctx.quality_recovery_input_binding, "input_digest": payload.get("input_digest"),
            "consumed_attempt_count": 2,
            "candidate_id": candidate["id"], "candidate_revision": candidate["revision"],
            "svg_digest": candidate["svg_digest"], "png_digest": candidate["png_digest"],
            "selection_evidence": candidate["selection_evidence"]}
        payload.update(svg_content=None, last_valid_svg=candidate["svg_content"],
            correction_mode="validated_svg_adaptation", repair_hint=json.dumps({
                "kind": "validated_svg_adaptation", "screen_failure": page.error.code,
                "instruction": "Adapt this verified SVG to every current source word, relation, image explanation group and native requirement. Preserve current content and validate the complete resulting page."}, ensure_ascii=False))
        await hooks.checkpoint(job, index, payload=payload)
        reserved.append((index, payload))
        recent.append({"svg_content": candidate["svg_content"]})
        recent_ids.append(candidate["id"])

    async def adapt(index, payload):
        async with ctx.semaphore:
            result = await attempt_loop(ctx, request, presentation_id, index,
                outline.slides[index].content, payload, job, state, hooks)
            result[0]["quality_recovery_fallback"]["status"] = "adapted"
            await hooks.checkpoint(job, index, payload=result[0])
            return index, result

    tasks = [asyncio.create_task(adapt(index, payload)) for index, payload in reserved]
    try:
        results = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    for index, result in results:
        pages[index] = result
    return pages


async def refresh_recovery_source_authority(ctx, hooks):
    """Revalidate private source references after the caller checks its own fence."""
    from utils.authored_style_svg_recovery import verified_recovery_source_evidence

    candidates = ctx.snapshot.get("validated_recovery_candidates") or []
    if not candidates:
        ctx.quality_recovery_source_evidence = []
        return
    async with hooks.async_session_maker() as session:
        ctx.quality_recovery_source_evidence = await verified_recovery_source_evidence(candidates, session)


async def ensure_recovery_authority(job, hooks):
    from sqlalchemy import select
    from models.sql.public_admission import PublicOperationAdmission
    from models.sql.presentation_generation_job import PresentationGenerationJob
    from services.public_operation_admission import _require_phase2_provider_contract
    from utils.owner_context import get_current_owner_id, get_current_tenant_id
    if job is None or getattr(job, "workspace_operation_id", None) is None:
        raise ValueError("svg_quality_recovery_durable_job_required")
    from services.svg_presentation_checkpoint import _fence_checkpoint_authority
    # The full fenced CAS, without transferring the page-0 seed payload.
    await _fence_checkpoint_authority(job, 0)
    async with hooks.async_session_maker() as session:
        # Only the fields compared below; the admitted request payload can
        # carry a whole recovery archive.
        current_job = (await session.execute(select(
            PresentationGenerationJob.attempt_token, PresentationGenerationJob.workspace_operation_id,
            PresentationGenerationJob.svg_style_snapshot, PresentationGenerationJob.authored_outline,
            PresentationGenerationJob.request_payload["_internal_svg_style_snapshot"].label("admitted_snapshot"),
            PresentationGenerationJob.request_payload["common_header_mode"].label("common_header_mode"),
        ).where(PresentationGenerationJob.id == job.id))).one_or_none()
        expected_snapshot = getattr(hooks, "recovery_snapshot", None)
        admitted_snapshot = current_job.admitted_snapshot if current_job is not None else None
        snapshot_bound = admitted_snapshot == expected_snapshot
        if (not snapshot_bound and current_job is not None and expected_snapshot is not None
                and expected_snapshot.get("common_header") is not None
                and current_job.common_header_mode == "uniform"):
            from services.svg_common_header import header_snapshot_base, validate_common_header_outline
            validate_common_header_outline(expected_snapshot, current_job.authored_outline)
            snapshot_bound = admitted_snapshot == header_snapshot_base(expected_snapshot)
        if (current_job is None or current_job.attempt_token != job.attempt_token
                or current_job.workspace_operation_id != job.workspace_operation_id
                or expected_snapshot is None
                or not snapshot_bound
                or current_job.svg_style_snapshot != expected_snapshot):
            raise ValueError("svg_quality_recovery_admission_snapshot_conflict")
        admission = await session.scalar(select(PublicOperationAdmission).where(
            PublicOperationAdmission.operation_id == job.workspace_operation_id))
        if (admission is None or str(admission.owner_id) != str(get_current_owner_id())
                or admission.tenant_id != str(get_current_tenant_id())):
            raise ValueError("svg_quality_recovery_authority_mismatch")
        await _require_phase2_provider_contract(session, admission,
            expected_attempt_token=job.attempt_token,
            expected_fencing_token=getattr(job, "_workspace_fencing_token", None))
        if (admission.estimated_consumed_microunits + admission.estimated_reserved_microunits
                > admission.estimated_cost_limit_microunits
                or admission.actual_consumed_microunits + admission.actual_reserved_microunits
                > admission.actual_cost_limit_microunits):
            raise ValueError("svg_quality_recovery_budget_exhausted")


def recovery_binding(ctx, request, job):
    from utils.owner_context import get_current_owner_id, get_current_tenant_id
    from dataclasses import asdict, is_dataclass
    from pathlib import Path
    from services import svg_renderer
    from services.svg_trusted_fonts import FONT_DIRECTORY, BUNDLED_FACES
    from utils.svg_template_fonts import FONT_PATH
    from services.public_render_runtime_identity import public_render_runtime_binding
    font_paths = [FONT_PATH, *[FONT_DIRECTORY / filename for _, _, filename in BUNDLED_FACES]]
    runtime_evidence = {"renderer_version": svg_renderer.SVG_RENDERER_VERSION,
        "renderer_digest": sha256_hex(Path(svg_renderer.__file__).read_bytes()),
        "font_digests": [sha256_hex(path.read_bytes()) for path in font_paths]}
    runtime_evidence["worker_binding"] = public_render_runtime_binding()
    from services.svg_contract import SVG_CONTRACT_VERSION
    ctx.quality_recovery_validation_context = {
        "contract_version": SVG_CONTRACT_VERSION,
        "renderer_digest": "sha256:" + _digest({"version": runtime_evidence["renderer_version"],
            "code": runtime_evidence["renderer_digest"]}),
        "fonts_digest": "sha256:" + _digest(runtime_evidence["font_digests"]),
        "runtime_digest": "sha256:" + _digest(runtime_evidence["worker_binding"]),
    }
    ctx.quality_recovery_checkpoint_reuse_eligible = (
        runtime_evidence["worker_binding"].get("checkpointReuseEligible") is True)
    runtime = (ctx.runtime_profile if isinstance(ctx.runtime_profile, BaseModel)
        else asdict(ctx.runtime_profile) if is_dataclass(ctx.runtime_profile)
        else vars(ctx.runtime_profile))
    return _digest(_quality_json_value({"style": ctx.snapshot, "contents": ctx.quality_review_contents,
        "roles": ctx.roles, "assets": ctx.manifests, "physical": ctx.physical_kwargs,
        "instructions": request.instructions, "font_profile": ctx.source_font_profile,
        "owner": str(get_current_owner_id()), "tenant": str(get_current_tenant_id()),
        "operation": str(getattr(job, "workspace_operation_id", None)),
        "renderer": runtime_evidence, "runtime": runtime}))


def _candidate_namespace(job):
    from utils.owner_context import get_current_owner_id, get_current_tenant_id
    owner, tenant = get_current_owner_id(), get_current_tenant_id()
    operation = getattr(job, "workspace_operation_id", None)
    if not owner or not tenant or not operation:
        raise ValueError("svg_quality_recovery_authority_mismatch")
    return _digest({"owner": str(owner), "tenant": str(tenant), "operation": str(operation)})


def _archive_candidate(payload, job):
    from services.authored_checkpoint_artifacts import atomic_write, checkpoint_root
    from pathlib import Path
    svg = payload["svg_content"].encode("utf-8")
    digest = sha256_hex(svg)
    namespace = _candidate_namespace(job)
    path = Path("svg-candidates") / namespace / digest[:2] / f"{digest}.svg"
    atomic_write(checkpoint_root() / path, svg)
    evidence = {key: deepcopy(value) for key, value in payload.items()
                if key not in {"svg_content", "last_valid_svg", "attempts", "quality_recovery", "quality_recovery_execution"}}
    return {"svg_path": path.as_posix(), "svg_digest": digest, "namespace": namespace, "evidence": evidence}


def local_evidence_binding(binding, payload):
    return _digest({"binding": binding, "input": payload.get("input_digest"),
        "svg": sha256_hex((payload.get("svg_content") or "").encode()),
        "png": payload.get("png_sha256"), "source": payload.get("source_preservation"),
        "native": payload.get("nativeDeclarations"), "assets": payload.get("svgAssetManifest"),
        "quality": {key: payload.get("quality", {}).get(key) for key in
            ("passed", "measurement_complete", "source_digest", "png_digest")}})


def _restore_candidate(archive, current, job):
    from services.authored_checkpoint_artifacts import checkpoint_root
    from pathlib import Path
    digest = archive.get("svg_digest", "")
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("svg_quality_recovery_candidate_invalid")
    namespace = _candidate_namespace(job)
    if archive.get("namespace") != namespace:
        raise ValueError("svg_quality_recovery_candidate_authority_mismatch")
    relative = Path("svg-candidates") / namespace / digest[:2] / f"{digest}.svg"
    if archive.get("svg_path") != relative.as_posix():
        raise ValueError("svg_quality_recovery_candidate_invalid")
    raw = (checkpoint_root() / relative).read_bytes()
    if sha256_hex(raw) != digest:
        raise ValueError("svg_quality_recovery_candidate_invalid")
    restored = deepcopy(archive["evidence"])
    restored.update(svg_content=raw.decode("utf-8"), last_valid_svg=raw.decode("utf-8"))
    # Keep provider history and its correction authority when the older drawing wins.
    for key in ("attempts", "model_attempt_count", "provider_failures", "provider_diagnostics", "quality_recovery_execution", "correction_mode"):
        if key in current:
            restored[key] = deepcopy(current[key])
    restored["quality_recovery"] = deepcopy(current.get("quality_recovery", {}))
    return restored


async def recover_deck(ctx, request, presentation_id, outline, job, pages, state, hooks,
                       run_advisory, attempt_loop, quality):
    """Two sealed reviews at most, one existing repair attempt per affected page."""
    plan = read_quality_recovery_execution_plan(ctx.snapshot, manifest=getattr(ctx, "partial_recovery_manifest", None))
    if plan is None:
        raise ValueError("svg_quality_recovery_plan_missing")
    if plan["slide_count"] != len(pages) or job is None:
        raise ValueError("svg_quality_recovery_plan_conflict")
    await ensure_recovery_authority(job, hooks)
    binding = recovery_binding(ctx, request, job)
    if getattr(ctx, "quality_recovery_input_binding", binding) != binding:
        raise ValueError("svg_quality_recovery_runtime_changed")
    ctx.quality_recovery_runtime_binding = binding
    await _prepare_final_selected_source(ctx, job, hooks)
    seed = pages[0][0]
    execution = deepcopy(seed.get("quality_recovery_execution") or {})
    if execution and execution.get("binding") != binding:
        raise ValueError("svg_quality_recovery_evidence_conflict")
    if not execution:
        execution["reviewPrinciplesVersion"] = DESIGN_PRINCIPLES_VERSION
    ctx.quality_review_principles_version = _review_scope_principles(execution)
    execution.setdefault("schema", "svg-quality-recovery-execution/v1")
    execution.setdefault("binding", binding)
    execution.setdefault("rounds", [])
    group_checkpoint_lock = asyncio.Lock()

    async def persist():
        pages[0][0]["quality_recovery_execution"] = deepcopy(execution)
        await hooks.checkpoint(job, 0, payload=pages[0][0])

    async def review(round_ordinal):
        await ensure_recovery_authority(job, hooks)
        digests = [_digest({"svg": p.get("svg_content"), "png": p.get("png_sha256")})
                   for p, _ in pages]
        rounds = execution["rounds"]
        prior = rounds[round_ordinal] if round_ordinal < len(rounds) else None
        if prior:
            if prior.get("page_digests") != digests:
                raise ValueError("svg_quality_recovery_review_conflict")
            if prior.get("status") != "completed":
                raise AuthoredStageError(slide_index=None, stage="render",
                    code="public_provider_result_unknown", retryable=False)
            return prior["advisory"]
        if round_ordinal >= plan["review_round_limit"] or len(rounds) != round_ordinal:
            raise ValueError("svg_quality_recovery_review_limit")
        marker = {"status": "review_inflight", "page_digests": digests}
        continuation_initial = round_ordinal == 0 and getattr(ctx, "quality_final_selected_source", None) is not None
        final_marker = None
        if continuation_initial:
            from services.svg_final_selected_continuation import INITIAL_NAMESPACE
            final_marker = execution.setdefault("initial_continuation_review", {
                "namespace": INITIAL_NAMESPACE, "status": "review_inflight", "groups": {},
                "source_proof_digest": ctx.quality_final_selected_source["proof"]["digest"],
                "producer_proof": deepcopy(ctx.quality_final_selected_source["proof"]),
                "initial_continuation": True,
            })
            marker["namespace"] = INITIAL_NAMESPACE
            marker["physical_purpose"] = "initial-continuation"
        rounds.append(marker)
        await persist()
        async def group_checkpoint(group_ordinal, group_marker=None):
            async with group_checkpoint_lock:
                await ensure_recovery_authority(job, hooks)
                group_limit = plan["phase_group_limit"]
                if type(group_ordinal) is not int or not 0 <= group_ordinal < group_limit:
                    raise ValueError("svg_quality_recovery_review_limit")
                groups = marker.setdefault("groups", {})
                key = str(group_ordinal)
                if group_marker is not None:
                    groups[key] = deepcopy(group_marker)
                    await persist()
                return deepcopy(groups.get(key))
        ctx.quality_recovery_review_group_checkpoint = group_checkpoint
        try:
            advisory = await quality.deck_advisory(ctx, pages, job=job, read_png=hooks.read_png,
                run_advisory=run_advisory, round_ordinal=round_ordinal,
                source_evidence=getattr(ctx, "quality_final_selected_source", None))
        except BaseException:
            marker["status"] = "review_unknown"
            await persist()
            raise
        marker.update(status="completed", advisory=advisory)
        await persist()
        if final_marker is not None:
            final_marker.update(status="completed", advisory=deepcopy(advisory))
            await persist()
        return advisory

    # A resumed second review must recover the saved baselines, never buy repairs again.
    pending = {i: p.get("quality_recovery", {}) for i, (p, _) in enumerate(pages)
               if p.get("quality_recovery", {}).get("status") in {"candidate_ready", "selected"}}
    if pending:
        first_advisory = execution["rounds"][0]["advisory"]
        decisions = {i: m.get("decision", {}) for i, m in pending.items()}
        priors = {}
        for i, marker in pending.items():
            await ensure_recovery_authority(job, hooks)
            priors[i] = _restore_candidate(marker["prior_candidate"], pages[i][0], job)
    else:
        first_advisory = await review(0)
        decisions = quality.quality_recovery_decisions(ctx, pages, first_advisory)
        priors = {}
        for index in sorted(decisions):
            payload = pages[index][0]
            if payload.get("quality_recovery", {}).get("status") == "retained":
                continue
            if plan["review_round_limit"] <= 1:
                payload["quality_recovery"] = {"status": "unresolved", "reason": "review_attempt_budget_exhausted"}
                await hooks.checkpoint(job, index, payload=payload)
                continue
            limit = min(2, plan["page_attempt_limit"], _provider_attempt_limit(payload, index=index, job=job))
            from services.generation_core.drivers.svg.partial_quality_recovery import current_episode_attempt_count
            if current_episode_attempt_count(ctx, payload, index) >= limit:
                payload["quality_recovery"] = {"status": "unresolved", "reason": "page_attempt_budget_exhausted"}
                await hooks.checkpoint(job, index, payload=payload)
                continue
            priors[index] = deepcopy(payload)
            await ensure_recovery_authority(job, hooks)
            archive = _archive_candidate(payload, job)
            payload["quality_recovery"] = {"status": "repair_pending", "prior_candidate": archive,
                "decision": deepcopy(decisions[index])}
            await hooks.checkpoint(job, index, payload=payload)

        async def repair(index):
            async with ctx.semaphore:
                payload = pages[index][0]
                decision = decisions[index]
                payload["last_valid_svg"] = priors[index]["svg_content"]
                await ensure_recovery_authority(job, hooks)
                ctx.quality_recovery_previous_renders[index] = (hooks.read_png(priors[index]),
                    priors[index].get("png_sha256"), sha256_hex(priors[index]["svg_content"].encode()))
                payload["svg_content"] = None
                payload["correction_mode"] = decision["repair_scope"]
                payload["repair_hint"] = json.dumps({"kind": decision["repair_scope"],
                    "issues": decision.get("issues", []),
                    **({"design_targets": decision["design_targets"]} if decision.get("design_targets") else {}), "instruction":
                    "Preserve every source word, number, relation, image group and native binding. "
                    "Improve the reported design axes without shrinking or hiding content."}, ensure_ascii=False)
                await hooks.checkpoint(job, index, payload=payload)
                try:
                    result = await attempt_loop(ctx, request, presentation_id, index,
                        outline.slides[index].content, payload, job, state, hooks)
                except AuthoredStageError as error:
                    if not _repair_candidate_retainable(error.code):
                        state.fatal_error = state.fatal_error or error
                        raise
                    await ensure_recovery_authority(job, hooks)
                    restored = _restore_candidate(payload["quality_recovery"]["prior_candidate"], payload, job)
                    restored["quality_recovery"].update(status="retained", reason=error.code)
                    await hooks.checkpoint(job, index, payload=restored)
                    await ensure_recovery_authority(job, hooks)
                    return index, (restored, hooks.read_png(restored))
                except BaseException as error:
                    state.fatal_error = state.fatal_error or error
                    raise
                result[0]["quality_recovery"]["status"] = "candidate_ready"
                await hooks.checkpoint(job, index, payload=result[0])
                return index, result

        tasks = [asyncio.create_task(repair(i)) for i in sorted(priors)]
        try:
            results = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        for index, result in results:
            pages[index] = result
        priors = {i: p for i, p in priors.items()
                  if pages[i][0].get("quality_recovery", {}).get("status") == "candidate_ready"}
    if not priors:
        if (ctx.snapshot.get("quality_recovery_policy") or {}).get("version") == 2:
            await ensure_recovery_authority(job, hooks)
            return pages, quality.reusable_final_deck_advisory(
                ctx, pages, first_advisory, job=job, read_png=hooks.read_png)
        return pages, first_advisory
    await quality.classify_pages(pages, job=job, checkpoint=hooks.checkpoint, snapshot=ctx.snapshot)
    ctx.quality_review_prior_pages = {}
    for i, p in priors.items():
        await ensure_recovery_authority(job, hooks)
        ctx.quality_review_prior_pages[i] = (p, hooks.read_png(p))
    ctx.quality_review_target_axes = {i: decisions.get(i, {}).get("target_axes", []) for i in priors}
    ctx.quality_review_target_goals = {i: decisions.get(i, {}).get("design_targets", []) for i in priors}
    ctx.quality_recovery_first_advisory = first_advisory
    candidate_advisory = await review(1)
    for index in sorted(priors):
        current = pages[index][0]
        selection = quality.compare_quality_recovery_candidates(ctx, priors[index], current,
            first_advisory, candidate_advisory, index)
        if selection.get("selected") != "candidate":
            await ensure_recovery_authority(job, hooks)
            restored = _restore_candidate(current["quality_recovery"]["prior_candidate"], current, job)
            restored["quality_recovery"].update(status="retained", selection=selection)
            pages[index] = restored, hooks.read_png(restored)
        else:
            current["quality_recovery"].update(status="selected", selection=selection)
        await hooks.checkpoint(job, index, payload=pages[index][0])
    # A mixed deck invalidates global evidence; publication must fail closed.
    from services.svg_authoring_profile import read_quality_recovery_policy
    if (read_quality_recovery_policy(ctx.snapshot) or {}).get("version") == 2:
        await ensure_recovery_authority(job, hooks)
        for producer in (candidate_advisory, first_advisory):
            reused = quality.reusable_final_deck_advisory(ctx, pages, producer, job=job, read_png=hooks.read_png)
            if reused is not None:
                return pages, reused
        if plan.get("final_selected_review") is not None:
            return pages, await _review_final_selected(ctx, request, pages, job, hooks, run_advisory, execution, seed, persist)
        return pages, None
    if any(p.get("quality_recovery", {}).get("status") == "retained" for p, _ in pages):
        if all((selection.get('selected') != 'candidate' for selection in [p.get('quality_recovery', {}).get('selection', {}) for p, _ in pages] if selection)):
            return pages, first_advisory
        if plan.get("final_selected_review") is not None:
            return pages, await _review_final_selected(ctx, request, pages, job, hooks, run_advisory, execution, seed, persist)
        return pages, None
    return pages, candidate_advisory


async def _prepare_final_selected_source(ctx, job, hooks):
    manifest = getattr(ctx, "partial_recovery_manifest", None)
    if isinstance(manifest, dict) and manifest.get("source_local_continuation") is not None:
        from services.svg_source_local_review_continuation import prepare_source_local_source
        return await prepare_source_local_source(ctx, job, hooks)
    if not isinstance(manifest, dict) or manifest.get("final_selected_continuation") is None:
        return None
    from models.sql.workspace import WorkspaceOperation
    from services.svg_partial_recovery import ARCHIVE_KEY, verify_episode, _frozen_payload, frozen_episode_payload
    from services.svg_final_selected_continuation import archived_producer_payloads
    await ensure_recovery_authority(job, hooks)
    async with hooks.async_session_maker() as session:
        operation = await session.get(WorkspaceOperation, job.workspace_operation_id)
        current = await verify_episode(session, operation, job)
        if _digest(current) != _digest(manifest):
            raise ValueError("svg_final_selected_evidence_unproven")
        producer_job, payloads = archived_producer_payloads(
            operation.request_payload[ARCHIVE_KEY], current["final_selected_continuation"],
        )
        await session.rollback()
    equivalent = (
        bool(getattr(ctx, "quality_recovery_checkpoint_reuse_eligible", False))
        and _digest(frozen_episode_payload(job.request_payload, current)) == _digest(_frozen_payload(producer_job["request_payload"]))
        and _digest(job.svg_style_snapshot) == _digest(producer_job["svg_style_snapshot"])
    )
    from services.svg_final_selected_continuation import INITIAL_NAMESPACE
    evidence = dict(proof=current["final_selected_continuation"], consumer_namespace=INITIAL_NAMESPACE,
                    execution=payloads[0]["quality_recovery_execution"],
                    producer_job=producer_job, producer_payloads=payloads,
                    consumer_runtime_equivalent=equivalent)
    if current.get("append_only_recovery") is True:
        evidence["consumer_binding"] = dict(jobId=str(job.id),
            operationId=str(job.workspace_operation_id), presentationId=str(job.presentation_id),
            ownerId=current["owner_id"], tenantId=current["tenant_id"])
        evidence["producer_presentation_id"] = producer_job["presentation_id"]
    ctx.quality_final_selected_source = evidence
    return evidence


async def _review_final_selected(ctx, request, pages, job, hooks, run_advisory, execution, seed, persist):
    from services.generation_core.drivers.svg import quality as deck_quality
    from services.svg_final_selected_continuation import build_selected_proof, NAMESPACE
    from utils.owner_context import get_current_owner_id, get_current_tenant_id
    manifest = getattr(ctx, "partial_recovery_manifest", None)
    if manifest is None:
        from services.svg_authoring_profile import read_quality_recovery_policy
        if (read_quality_recovery_policy(ctx.snapshot) or {}).get("version") == 2:
            return await _review_live_final_selected(ctx, pages, job, hooks, run_advisory, execution, persist)
    if not isinstance(manifest, dict) or manifest.get("final_selected_review_control") is None:
        raise ValueError("svg_final_selected_review_not_admitted")
    await ensure_recovery_authority(job, hooks)
    proof = build_selected_proof(
        operation_id=str(job.workspace_operation_id), job_id=str(job.id),
        owner_id=str(get_current_owner_id()), tenant_id=get_current_tenant_id(),
        archive_digest=manifest["archive_digest"], parent_manifest_hash=_digest(manifest),
        payloads=[p for p, _ in pages], execution=execution,
        source_snapshot=job.svg_style_snapshot,
    )
    if proof is None:
        raise ValueError("svg_final_selected_evidence_unproven")
    for index, (_, png) in enumerate(pages):
        if sha256_hex(png) != proof["source_pages"][index]["png_digest"]:
            raise ValueError("svg_final_selected_evidence_unproven")
    marker = execution.get("final_selected_review")
    if marker is not None:
        _review_scope_principles(marker)
        if marker.get("namespace") != NAMESPACE or marker.get("source_proof_digest") != proof["digest"]:
            raise ValueError("svg_final_selected_review_source_changed")
        if marker.get("status") == "completed":
            return marker["advisory"]
        if marker.get("status") != "review_inflight":
            raise ValueError("svg_final_selected_review_unavailable")
    else:
        marker = dict(namespace=NAMESPACE, status="review_inflight",
                      source_proof_digest=proof["digest"], producer_proof=proof, groups={})
        if "reviewPrinciplesVersion" in execution:
            marker["reviewPrinciplesVersion"] = execution["reviewPrinciplesVersion"]
        execution["final_selected_review"] = marker
        await persist()
    checkpoint_lock = asyncio.Lock()
    async def final_checkpoint(key, value=None):
        async with checkpoint_lock:
            await ensure_recovery_authority(job, hooks)
            key = str(key)
            if value is None:
                return deepcopy(marker["groups"].get(key))
            marker["groups"][key] = deepcopy(value)
            await persist()
            return value
    original_producer = getattr(ctx, "quality_final_selected_source", None)
    evidence = dict(proof=proof, execution=execution, consumer_namespace=NAMESPACE,
                    additional_producers=[original_producer] if original_producer else [],
                    consumer_runtime_equivalent=bool(getattr(ctx, "quality_recovery_checkpoint_reuse_eligible", False)))
    original_checkpoint = getattr(ctx, "quality_recovery_review_group_checkpoint", None)
    original_principles = getattr(ctx, "quality_review_principles_version", DESIGN_PRINCIPLES_VERSION)
    scope_principles = _review_scope_principles(marker)
    ctx.quality_recovery_review_group_checkpoint = final_checkpoint
    ctx.quality_review_principles_version = scope_principles
    try:
        advisory = await deck_quality.deck_advisory(
            ctx, pages, job=job, read_png=hooks.read_png, run_advisory=run_advisory,
            round_ordinal=0, final_selected=True, source_evidence=evidence,
        )
    except BaseException:
        marker["status"] = "review_unknown"
        await persist()
        raise
    finally:
        ctx.quality_recovery_review_group_checkpoint = original_checkpoint
        ctx.quality_review_principles_version = original_principles
    await ensure_recovery_authority(job, hooks)
    marker.update(status="completed", advisory=advisory)
    await persist()
    return advisory


async def _review_live_final_selected(ctx, pages, job, hooks, run_advisory, execution, persist):
    """Review a fresh v2 mixed deck under its immutable admitted final control."""
    from services.generation_core.drivers.svg import quality as deck_quality
    from services.svg_final_selected_continuation import NAMESPACE
    plan = read_quality_recovery_execution_plan(ctx.snapshot)
    if plan is None or plan.get("final_selected_review") is None:
        return None
    await ensure_recovery_authority(job, hooks)
    producers, proofs = [], []
    for ordinal, round_marker in enumerate(execution.get("rounds", [])):
        advisory = round_marker.get("advisory")
        gate = deck_quality._gate_intact(advisory)
        if round_marker.get("status") != "completed" or gate is None:
            raise ValueError("svg_final_selected_evidence_unproven")
        producers.append(advisory)
        proofs.append(dict(round_ordinal=ordinal, producer_gate_digest=gate["digest"],
            producer_input_digest=gate["inputDigest"], producer_binding=deepcopy(gate["binding"])))
    if not producers:
        raise ValueError("svg_final_selected_evidence_unproven")
    identity = dict(selected=[dict(svg=sha256_hex(p["svg_content"].encode()), png=sha256_hex(png),
                                     input_digest=p.get("input_digest")) for p, png in pages],
        producer_proofs=proofs, runtime_binding=ctx.quality_recovery_runtime_binding,
        snapshot_digest=_digest(ctx.snapshot), control=plan["final_selected_review"])
    source_digest = _digest(identity)
    marker = execution.get("final_selected_review")
    if marker is not None:
        _review_scope_principles(marker)
        if marker.get("namespace") != NAMESPACE or marker.get("source_proof_digest") != source_digest:
            raise ValueError("svg_final_selected_review_source_changed")
        if marker.get("status") == "completed":
            reused = deck_quality.reusable_final_deck_advisory(ctx, pages, marker.get("advisory"),
                                                             job=job, read_png=hooks.read_png)
            if reused is None:
                raise ValueError("svg_final_selected_review_source_changed")
            return reused
        if marker.get("status") != "review_inflight":
            raise ValueError("svg_final_selected_review_unavailable")
    else:
        marker = dict(namespace=NAMESPACE, status="review_inflight", source_proof_digest=source_digest,
                      live_producer_proofs=proofs, groups={})
        if "reviewPrinciplesVersion" in execution:
            marker["reviewPrinciplesVersion"] = execution["reviewPrinciplesVersion"]
        execution["final_selected_review"] = marker
        await persist()
    checkpoint_lock = asyncio.Lock()
    async def final_checkpoint(key, value=None):
        async with checkpoint_lock:
            await ensure_recovery_authority(job, hooks)
            if type(key) is not int or not 0 <= key < plan["final_selected_review"]["review_call_limit"]:
                raise ValueError("svg_quality_recovery_review_limit")
            key = str(key)
            if value is None:
                return deepcopy(marker["groups"].get(key))
            marker["groups"][key] = deepcopy(value)
            await persist()
            return value
    original_checkpoint = getattr(ctx, "quality_recovery_review_group_checkpoint", None)
    original_principles = getattr(ctx, "quality_review_principles_version", DESIGN_PRINCIPLES_VERSION)
    scope_principles = _review_scope_principles(marker)
    ctx.quality_recovery_review_group_checkpoint = final_checkpoint
    ctx.quality_review_principles_version = scope_principles
    try:
        advisory = await deck_quality.deck_advisory(ctx, pages, job=job, read_png=hooks.read_png,
            run_advisory=run_advisory, round_ordinal=0, final_selected=True,
            source_evidence=dict(liveReviewProducers=producers))
    except BaseException:
        marker["status"] = "review_unknown"
        await persist()
        raise
    finally:
        ctx.quality_recovery_review_group_checkpoint = original_checkpoint
        ctx.quality_review_principles_version = original_principles
    await ensure_recovery_authority(job, hooks)
    marker.update(status="completed", advisory=advisory)
    await persist()
    return advisory
