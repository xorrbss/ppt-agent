"""Checkpoint payload primitives for durable SVG generation.

Digesting page inputs, persisting/reading rendered PNGs by content hash, and
recovering a page's progress from its durable checkpoint row all live here.

Extracted out of ``svg_presentation_service.py`` (kept at/under the file-size
ratchet); the facade there re-exports every name importers use.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
import re

from sqlalchemy import func, select, update

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from services.authored_checkpoint_artifacts import atomic_write, checkpoint_root
from services.authored_checkpoint_fence import owned_job
from services.authored_presentation_artifacts import _save_slide_pngs
from services.database import async_session_maker
from utils.datetime_utils import as_utc, get_current_utc_datetime
from utils.hashing import canonical_json_sha256, sha256_hex


def _digest(value: object) -> str:
    return canonical_json_sha256(value, allow_nan=True)


def _preserve_provider_terminal_responses(saved: dict, payload: dict, *, job=None) -> dict:
    """Keep committed terminal evidence when a worker writes its earlier payload."""
    records = saved.get("provider_terminal_responses") or {}
    incoming = payload.get("provider_terminal_responses") or {}
    if not isinstance(records, dict) or not isinstance(incoming, dict):
        raise ValueError("svg_provider_terminal_checkpoint_conflict")
    for key, value in incoming.items():
        current = records.get(key)
        if current == value:
            continue
        if (not isinstance(value, dict) or not isinstance(current, dict)
                or value.get("terminal_event") != "response.created"
                or current.get("terminal_event") != "response.completed"
                or any(value.get(field) != current.get(field) for field in (
                    "schema", "ordinal", "job_id", "operation_id", "attempt_token", "fencing_token", "input_digest"))
                or value.get("response_id") is not None and value.get("response_id") != current.get("response_id")):
            raise ValueError("svg_provider_terminal_checkpoint_conflict")
    if not records:
        return payload
    merged = {**payload, "provider_terminal_responses": deepcopy(records)}
    terminal = records.get(str(payload.get("inflight"))) or {}
    if (job is not None and terminal.get("terminal_event") in {"response.completed", "dispatch.stopped"}
            and terminal.get("job_id") == str(job.id)
            and terminal.get("operation_id") == (str(job.workspace_operation_id) if job.workspace_operation_id else None)
            and terminal.get("attempt_token") == job.attempt_token
            and terminal.get("fencing_token") == getattr(job, "_workspace_fencing_token", None)
            and terminal.get("input_digest") == payload.get("input_digest")):
        merged["inflight"] = None
    return merged


async def _require_svg_provider_event_write(session, job, index, event):
    """Recheck the current operation's live authority at the terminal write."""
    if job.workspace_operation_id is None:
        return
    from models.sql.workspace import WorkspaceOperation
    from services.authored_checkpoint_errors import AuthoredStageError
    from utils.owner_context import get_current_owner_id

    now = get_current_utc_datetime()
    result = await session.execute(update(WorkspaceOperation).where(
        WorkspaceOperation.id == job.workspace_operation_id,
        WorkspaceOperation.owner_id == get_current_owner_id(),
        WorkspaceOperation.state == "running",
        WorkspaceOperation.attempt_token == job.attempt_token,
        WorkspaceOperation.fencing_token == getattr(job, "_workspace_fencing_token", None),
        WorkspaceOperation.lease_expires_at > now,
        WorkspaceOperation.deadline_at > now,
        WorkspaceOperation.cancel_requested_at.is_(None),
    ).values(heartbeat_at=WorkspaceOperation.heartbeat_at))
    if result.rowcount != 1:
        raise AuthoredStageError(slide_index=index, stage="author", retryable=False,
            code="svg_provider_completed_checkpoint_authority_expired" if event == "response.completed"
                else "svg_provider_checkpoint_authority_expired")


async def record_svg_provider_event(job, index: int, record: dict) -> dict:
    """Commit a fenced provider terminal response before parsing or rendering it."""
    if job is None or type(index) is not int or index < 0 or not isinstance(record, dict):
        raise ValueError("svg_provider_terminal_binding_invalid")
    from utils.provider_overload import safe_provider_diagnostics

    if set(record) - {"schema", "ordinal", "terminal_event", "job_id", "operation_id", "attempt_token",
            "fencing_token", "input_digest", "response_id", "raw_response", "output_digest", "usage", "diagnostics"}:
        raise ValueError("svg_provider_terminal_binding_invalid")
    ordinal = record.get("ordinal")
    event = record.get("terminal_event")
    identity = dict(job_id=str(job.id), operation_id=str(job.workspace_operation_id)
                    if job.workspace_operation_id is not None else None,
                    attempt_token=job.attempt_token,
                    fencing_token=getattr(job, "_workspace_fencing_token", None))
    if (record.get("schema") != "svg-provider-terminal/v1" or type(ordinal) is not int or ordinal < 1
            or event not in {"response.created", "response.completed", "dispatch.stopped"}
            or any(record.get(key) != value for key, value in identity.items())):
        raise ValueError("svg_provider_terminal_binding_invalid")
    response_id = record.get("response_id")
    if response_id is not None and safe_provider_diagnostics(diagnostics={"responseId": response_id}).get("responseId") != response_id:
        raise ValueError("svg_provider_terminal_binding_invalid")
    if event != "response.completed" and any(key in record for key in ("raw_response", "output_digest", "usage")):
        raise ValueError("svg_provider_terminal_binding_invalid")
    if event == "dispatch.stopped" and response_id is not None:
        raise ValueError("svg_provider_terminal_binding_invalid")
    if event == "response.completed":
        raw = record.get("raw_response")
        if (not isinstance(raw, str) or record.get("output_digest") != sha256_hex(raw.encode("utf-8"))
                or not isinstance(record.get("usage"), dict)):
            raise ValueError("svg_provider_terminal_output_invalid")
        if safe_provider_diagnostics(diagnostics={"usage": record["usage"]}).get("usage", {}) != record["usage"]:
            raise ValueError("svg_provider_terminal_output_invalid")
    value = deepcopy(record)
    if "diagnostics" in value:
        value["diagnostics"] = safe_provider_diagnostics(diagnostics=value["diagnostics"])
    async with async_session_maker() as session:
        active = await owned_job(session, job.id, job.attempt_token,
                                 fencing_token=identity["fencing_token"])
        if active.workspace_operation_id != job.workspace_operation_id:
            raise ValueError("svg_provider_terminal_binding_invalid")
        if job.workspace_operation_id is not None:
            from services.authored_checkpoint_fence import public_checkpoint_binding
            from utils.owner_context import get_current_owner_id, get_current_tenant_id

            binding = await public_checkpoint_binding(job.id, job.attempt_token,
                fencing_token=identity["fencing_token"], session=session)
            if (binding is None or binding[0] != get_current_owner_id()
                    or binding[1] != get_current_tenant_id() or binding[2] != job.workspace_operation_id):
                raise ValueError("svg_provider_terminal_binding_invalid")
            value.update(owner_id=str(binding[0]), tenant_id=binding[1])
        value["receipt_digest"] = canonical_json_sha256(value, allow_nan=False)
        row = await session.scalar(select(AuthoredSlideCheckpoint).where(
            AuthoredSlideCheckpoint.job_id == job.id, AuthoredSlideCheckpoint.slide_index == index))
        if row is None or row.attempt_token != job.attempt_token:
            raise RuntimeError("svg_checkpoint_missing")
        payload = deepcopy(row.svg_checkpoint or {})
        if not record.get("input_digest") or record.get("input_digest") != payload.get("input_digest"):
            raise ValueError("svg_provider_terminal_binding_invalid")
        records = payload.setdefault("provider_terminal_responses", {})
        if not isinstance(records, dict):
            raise ValueError("svg_provider_terminal_checkpoint_conflict")
        previous = records.get(str(ordinal))
        if previous is not None:
            if not isinstance(previous, dict):
                raise ValueError("svg_provider_terminal_checkpoint_conflict")
            if previous.get("terminal_event") in {"response.completed", "dispatch.stopped"}:
                if previous != value:
                    raise ValueError("svg_provider_terminal_checkpoint_conflict")
                await _require_svg_provider_event_write(session, job, index, event)
                await session.commit()
                return deepcopy(previous)
            if event == "dispatch.stopped":
                raise ValueError("svg_provider_terminal_checkpoint_conflict")
            if (any(previous.get(key) != record.get(key) for key in (*identity, "input_digest", "ordinal"))
                    or previous.get("response_id") is not None and previous.get("response_id") != response_id):
                raise ValueError("svg_provider_terminal_checkpoint_conflict")
        if payload.get("inflight") != ordinal:
            raise ValueError("svg_provider_terminal_binding_invalid")
        await _require_svg_provider_event_write(session, job, index, event)
        records[str(ordinal)] = value
        if event in {"response.completed", "dispatch.stopped"}:
            payload["inflight"] = None
        row.svg_checkpoint = payload
        session.add(row)
        await session.commit()
        return deepcopy(value)


_NUMERIC_CLAIM = re.compile(
    r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+|\d+\.\d+|\d{2,})(?:\s?(?:%|년|월|일|억|만|개|명|건|원|배|점|GB|MB|KB))?"
    r"|(?<![\w.])\d+(?:\s?(?:%|년|월|일|억|만|개|명|건|원|배|점|GB|MB|KB))"
)
_SOURCE_REFERENCE_PREFIX = re.compile(
    r"\A# 원본 [0-9]+페이지\r?\n(?:[ \t]*\r?\n)*"
    r"## 원문 전체 텍스트 \(읽기 순서, 모든 수치와 정책 조건 보존\)(?:\r?\n|$)"
)


def _required_numeric_claims(content: str) -> list[str]:
    """Protect concrete figures without requiring verbatim prose or bullet labels."""
    # The source extractor's exact wrapper identifies a reference page, not a
    # business figure to display. Keep every number in the actual source body.
    content = _SOURCE_REFERENCE_PREFIX.sub("", content, count=1)
    content = re.sub(
        r"(?m)^[ \t]*<!--[ \t]*sourcePage[ \t]*:[ \t]*[0-9]+[ \t]*;[^\r\n]*?-->[ \t]*(?:\r?\n|$)",
        "",
        content,
    )
    return list(dict.fromkeys(match.group(0).strip() for match in _NUMERIC_CLAIM.finditer(content)))


def _provider_attempt_count(payload: dict) -> int:
    count = sum(attempt.get("kind") != "local" for attempt in payload.get("attempts", []))
    if payload.get("recovery_revision") not in {'svg-provider-timeout-repurchase/v1', 'svg-provider-timeout-repurchase/v2', 'svg-provider-timeout-repurchase/v3'}:
        return count
    fence = payload.get("retained_provider_attempt_fence")
    if not isinstance(fence, dict):
        raise ValueError("svg_timeout_repurchase_attempt_fence_invalid")
    consumed = fence.get("consumed_attempt_count")
    ordinal = fence.get("previous_ordinal")
    if (type(consumed) is not int or type(ordinal) is not int
            or consumed < 0 or ordinal < 0 or ordinal != consumed):
        raise ValueError("svg_timeout_repurchase_attempt_fence_invalid")
    # A fresh overload also spends the remaining purchase. It must not silently
    # cause a third call merely because no provider response was appended.
    return consumed + count + len(payload.get("provider_failures", []))


def _provider_attempt_limit(payload: dict, *, index=None, job=None) -> int:
    """An approved target receives one additional call with its consumed count retained."""
    if payload.get("recovery_revision") not in {
        "svg-provider-timeout-repurchase/v2", "svg-provider-timeout-repurchase/v3",
    }:
        return 2
    manifest = payload.get("timeout_repurchase_approval_manifest")
    fence = payload.get("retained_provider_attempt_fence")
    if not isinstance(manifest, dict) or not isinstance(fence, dict):
        raise ValueError("svg_timeout_repurchase_attempt_fence_invalid")
    allowed_consumed_counts = {1, 2}
    if manifest.get("bounded_completion_recovery") is True:
        from services.svg_timeout_repurchase import _bounded_completion_shape

        if not _bounded_completion_shape(manifest):
            raise ValueError("svg_timeout_repurchase_attempt_fence_invalid")
        if index == 4 and payload.get("completion_cache_only") is True:
            returned = manifest.get("returned_checkpoints", [])
            svg = payload.get("svg_content")
            if (len(returned) != 1 or returned[0].get("slide_index") != 4
                    or manifest.get("recovery_revision") != payload.get("recovery_revision")
                    or _digest(manifest) != fence.get("approval_manifest_hash")
                    or job is None or str(job.id) != manifest.get("job_id")
                    or str(job.workspace_operation_id) != payload.get("recovery_operation_id")
                    or fence.get("slide_index") != 4
                    or returned[0].get("input_digest") != payload.get("input_digest")
                    or not isinstance(svg, str) or not svg
                    or sha256_hex(svg.encode("utf-8")) != returned[0].get("source_digest")
                    or _digest(payload.get("attempts", [])) != returned[0].get("author_digest")
                    or fence.get("consumed_attempt_count") != 1
                    or fence.get("previous_ordinal") != 1
                    or payload.get("inflight") is not None
                    or returned[0].get("max_additional_author_calls") != 0
                    or _provider_attempt_count(payload) != 2):
                raise ValueError("svg_timeout_repurchase_attempt_fence_invalid")
            return 2
        if index in {2, 3}:
            allowed_consumed_counts.add(3)
    targets = [p for p in manifest.get("unknown_checkpoints", []) if p.get("slide_index") == index]
    if (len(targets) != 1 or manifest.get("recovery_revision") != payload.get("recovery_revision")
            or _digest(manifest) != fence.get("approval_manifest_hash")
            or manifest.get("additional_purchase_budget_per_target") != 1
            or type(manifest.get("max_additional_author_calls")) is not int
            or manifest.get("max_additional_author_calls", 0) < 1
            or manifest.get("max_additional_author_calls") != len(manifest.get("unknown_checkpoints", []))
            or fence.get("slide_index") != index or fence.get("max_additional_author_calls") != 1
            or targets[0].get("input_digest") != payload.get("input_digest")
            or targets[0].get("consumed_attempt_count") != fence.get("consumed_attempt_count")
            or targets[0].get("previous_ordinal") != fence.get("previous_ordinal")
            or targets[0].get("max_additional_author_calls") != 1
            or targets[0].get("next_provider_ordinal") != fence.get("consumed_attempt_count", -1) + 1
            or fence.get("consumed_attempt_count") not in allowed_consumed_counts
            or job is None or str(job.id) != manifest.get("job_id")
            or str(job.workspace_operation_id) != payload.get("recovery_operation_id")):
        raise ValueError("svg_timeout_repurchase_attempt_fence_invalid")
    _provider_attempt_count(payload)
    return fence["consumed_attempt_count"] + 1


async def _collect_pages_or_fail(tasks):
    """Stop waiting on siblings after the first terminal page failure."""
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        # Cancellation abandons waiting, not a proof that the provider stopped.
        # The pre-dispatch inflight checkpoint remains the recovery witness.
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def initialize_svg_checkpoints(*, job_id, attempt_token, roles, fencing_token=None):
    """Seed only a wholly unstarted job, under the canonical checkpoint CAS.

    A replay keeps every existing row; a missing subset is evidence loss, not
    a request to recreate progress. Attempt ordinals alone do not prove that
    provider work started: an outline-only failure may be safely re-claimed.
    """
    from services import authored_checkpoint_state, svg_presentation_service as facade
    from services.generation_core.drivers.svg.collect import _stage_error, _unknown_provider_checkpoint
    from models.sql.provider_usage_receipt import ProviderUsageReceipt
    from models.sql.public_admission import PublicOperationAdmission, PublicProviderLogicalCall
    from models.sql.slide import SlideModel
    from models.sql.workspace import WorkspaceOperation

    async with facade.async_session_maker() as session:
        job = await facade.owned_job(session, job_id, attempt_token, fencing_token=fencing_token)
        rows = list(await session.scalars(select(AuthoredSlideCheckpoint).where(
            AuthoredSlideCheckpoint.job_id == job_id)))
        if rows:
            if {row.slide_index for row in rows} != set(range(len(roles))):
                raise RuntimeError("svg_checkpoint_missing")
            if any(row.role != roles[row.slide_index] for row in rows):
                raise RuntimeError("svg_initial_checkpoint_input_conflict")
            if any(_unknown_provider_checkpoint(row.svg_checkpoint or {}) for row in rows):
                raise _stage_error("public_provider_result_unknown")
            return
        if _unknown_provider_checkpoint(job.last_error or {}):
            raise _stage_error("public_provider_result_unknown")
        if (job.completed_slides or job.current_stage not in {None, "queued", "outline"}
                or job.total_slides not in {0, len(roles)}):
            raise RuntimeError("svg_checkpoint_missing")
        usage = await session.scalar(select(func.count()).select_from(ProviderUsageReceipt).where(
            ProviderUsageReceipt.job_id == job.id))
        published = await session.scalar(select(func.count()).select_from(SlideModel).where(
            SlideModel.presentation == job.presentation_id))
        if usage or published:
            raise RuntimeError("svg_checkpoint_missing")
        if job.workspace_operation_id is not None:
            from services.svg_generation_assets import publication_context

            context_job = job.model_copy()
            context_job._workspace_fencing_token = fencing_token
            await publication_context(session, context_job)
            operation = await session.get(WorkspaceOperation, job.workspace_operation_id)
            admission = await session.scalar(select(PublicOperationAdmission).where(
                PublicOperationAdmission.operation_id == job.workspace_operation_id))
            calls = await session.scalar(select(func.count()).select_from(PublicProviderLogicalCall).where(
                PublicProviderLogicalCall.admission_id == admission.id))
            if _unknown_provider_checkpoint(operation.error_detail or {}):
                raise _stage_error("public_provider_result_unknown")
            counters = ("initial_request_count", "retry_request_count", "estimated_reserved_microunits",
                        "estimated_consumed_microunits", "actual_reserved_microunits",
                        "actual_consumed_microunits", "actual_cost_unavailable_count")
            if (calls or any(getattr(admission, key) for key in counters)
                    or admission.planned_request_limit < len(roles)
                    or admission.slide_count != len(roles)
                    or as_utc(admission.deadline_at) <= get_current_utc_datetime()
                    or operation.presentation_id != job.presentation_id
                    or operation.result_revision is not None):
                raise RuntimeError("svg_checkpoint_missing")
        # Reuse the canonical initializer in this same fenced transaction. It
        # inserts only absent rows and does not clear old checkpoint outcomes.
        await authored_checkpoint_state.initialize_checkpoints(
            job_id=job_id, attempt_token=attempt_token, roles=roles,
            fencing_token=fencing_token, session=session,
        )
        await session.commit()


async def _checkpoint(job, index, *, payload=None):
    if job is None:
        return None
    async with async_session_maker() as session:
        active = await owned_job(session, job.id, job.attempt_token,
                                 fencing_token=getattr(job, "_workspace_fencing_token", None))
        row = await session.scalar(select(AuthoredSlideCheckpoint).where(
            AuthoredSlideCheckpoint.job_id == job.id,
            AuthoredSlideCheckpoint.slide_index == index,
        ))
        if row is None:
            raise RuntimeError("svg_checkpoint_missing")
        if payload is not None:
            payload = _preserve_provider_terminal_responses(row.svg_checkpoint or {}, payload, job=job)
            row.svg_checkpoint = payload
            row.attempt_token = job.attempt_token
            row.author_status = "completed" if payload.get("svg_content") else "running"
            row.illustrate_status = "skipped"
            row.render_status = "completed" if payload.get("png_sha256") else "pending"
            row.author_completed_at = get_current_utc_datetime() if payload.get("svg_content") else None
            row.render_completed_at = get_current_utc_datetime() if payload.get("png_sha256") else None
            row.failed_stage = None
            row.error = None
            session.add(row)
            await session.flush()
            states = await session.scalars(select(AuthoredSlideCheckpoint.render_status).where(
                AuthoredSlideCheckpoint.job_id == job.id))
            active.completed_slides = sum(state == "completed" for state in states)
            active.current_stage = "render" if payload.get("svg_content") else "author"
            session.add(active)
        result = dict(row.svg_checkpoint or {})
        await session.commit()
        return result


def _write_png(png: bytes) -> dict:
    digest = sha256_hex(png)
    relative = Path("svg") / digest[:2] / f"{digest}.png"
    atomic_write(checkpoint_root() / relative, png)
    return {"png_sha256": digest, "png_path": relative.as_posix()}


def _read_png(payload: dict) -> bytes | None:
    digest = payload.get("png_sha256", "")
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        return None
    path = checkpoint_root() / "svg" / digest[:2] / f"{digest}.png"
    if not path.is_file():
        return None
    png = path.read_bytes()
    return png if sha256_hex(png) == digest else None


def _save_source_page_pngs(presentation_id, pages, publication_token):
    """Publish one verified checkpoint image at a time, not a deck of PNG bytes."""
    references = []
    for payload, _ in pages:
        png = _read_png(payload)
        if png is None:
            raise ValueError("svg_source_checkpoint_image_missing")
        references.extend(_save_slide_pngs(presentation_id, [png], publication_token))
    return references
