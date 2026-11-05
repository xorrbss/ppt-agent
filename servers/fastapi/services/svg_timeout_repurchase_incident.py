"""Read-only verifier for the one registered svg timeout-repurchase incident.

Revision three of the timeout repurchase was only ever approved for a single
frozen twelve-page sample. Every operation id, ledger count, slide-index
layout, budget and provider/model pin of that sample is data in
``svg_timeout_repurchase_incident_registry.json``; this module is the single
verifier that consumes it. The registry is pinned by its canonical digest, so
an edited registry fails closed instead of widening what the stored approval
manifests admit.

The checks below reproduce the original consent contract exactly: stored
operations of the incident still build, resume and fence the same manifests,
and no other operation is admitted by them.
"""
from __future__ import annotations

from copy import deepcopy
from functools import cache
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.public_admission import (
    PublicOperationAdmission, PublicProviderLogicalCall, PublicResourceLease,
)
from models.sql.provider_runtime_reservation import ProviderRuntimeBudget, ProviderRuntimeReservation
from services.svg_timeout_repurchase import (
    SVG_TIMEOUT_REPURCHASE_V3, TimeoutRepurchaseRecovery, _digest, _live, _reject,
)
from utils.datetime_utils import get_current_utc_datetime
from utils.hashing import canonical_json_sha256, sha256_hex


REGISTRY_SCHEMA = "svg-timeout-repurchase-incident-registry/v1"
REGISTRY_PATH = Path(__file__).with_name("svg_timeout_repurchase_incident_registry.json")
# Canonical JSON digest of the registry. It is immutable evidence: changing a
# value changes what stored approval manifests admit, so it is never re-pinned.
REGISTRY_DIGEST = "8c4c6cc747cc8a05ebfb8e28ce199bc974162f478e3cee4c02526644bf3b4d6f"


@cache
def _verified_registry_text() -> str:
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _reject("svg_timeout_repurchase_incident_registry_invalid")
    if (not isinstance(data, dict) or data.get("schema") != REGISTRY_SCHEMA
            or canonical_json_sha256(data) != REGISTRY_DIGEST):
        _reject("svg_timeout_repurchase_incident_registry_invalid")
    return json.dumps(data)


def incident_registry() -> dict:
    """A fresh copy of the digest-checked registry; callers may not share it."""
    return json.loads(_verified_registry_text())


def _stage(name: str) -> dict:
    return incident_registry()["stages"][name]


def _terms_match(manifest: dict, terms: dict, keys=None) -> bool:
    return all(manifest.get(key) == terms[key] for key in (terms if keys is None else keys))


def _pinned_author(registry: dict, provider_id, model_id) -> bool:
    return provider_id == registry["author_provider"] and model_id == registry["author_model"]


async def _build_zero_dispatch_batch_replay(session, operation, *, presentation_revision, lock=True, recovery_revision=SVG_TIMEOUT_REPURCHASE_V3):
    """Lock and bind a terminal local snapshot, also usable for read-only review.

The returned hash is the approval contract. It is not permission to retry;
``verify_timeout_repurchase`` requires that exact hash in the action request.
"""
    registry = incident_registry()
    batch = registry["stages"]["interrupted_batch"]
    replay = registry["stages"]["zero_dispatch_batch_replay"]
    total_slides = registry["total_slides"]
    now = get_current_utc_datetime()
    mixed_recovery = True

    def locked(statement):
        return statement.with_for_update() if lock else statement
    if operation.kind != 'presentation.generate' or operation.state not in {'failed', 'dead_letter'} or operation.error_code != 'http_error_500' or (operation.request_payload.get('recovery_revision') != SVG_TIMEOUT_REPURCHASE_V3) or _live(operation.lease_expires_at, now):
        _reject()
    job = await session.scalar(locked(select(PresentationGenerationJob).where(PresentationGenerationJob.workspace_operation_id == operation.id, PresentationGenerationJob.presentation_id == operation.presentation_id)))
    if job is None or job.state != 'failed' or job.request_payload.get('strategy') != 'svg' or (not job.svg_style_snapshot) or _live(job.lease_expires_at, now):
        _reject()
    linked_id = operation.request_payload.get('job_id')
    if linked_id is not None and str(job.id) != str(linked_id):
        _reject()
    if mixed_recovery and (job.total_slides < 1 or job.svg_style_snapshot.get('authoring_profile') != registry['authoring_profile']):
        _reject()
    admission = await session.scalar(locked(select(PublicOperationAdmission).where(PublicOperationAdmission.operation_id == operation.id)))
    if admission is None or admission.state not in {'failed', 'deadline_exceeded'} or admission.terminal_at is None or (admission.owner_id != operation.owner_id) or admission.estimated_reserved_microunits or admission.actual_reserved_microunits:
        _reject()
    leases = list(await session.scalars(locked(select(PublicResourceLease).where(PublicResourceLease.admission_id == admission.id).order_by(PublicResourceLease.scope_type))))
    if any((lease.state not in {'released', 'cancelled', 'expired'} for lease in leases)):
        _reject('svg_timeout_repurchase_active_lease')
    calls = list(await session.scalars(locked(select(PublicProviderLogicalCall).where(PublicProviderLogicalCall.admission_id == admission.id).order_by(PublicProviderLogicalCall.stage, PublicProviderLogicalCall.slide_index, PublicProviderLogicalCall.logical_call_ordinal))))
    if calls:
        _reject('svg_timeout_repurchase_active_call')
    budgets = list(await session.scalars(locked(select(ProviderRuntimeBudget).where(ProviderRuntimeBudget.job_id == job.id).order_by(ProviderRuntimeBudget.id))))
    reservations = list(await session.scalars(locked(select(ProviderRuntimeReservation).join(ProviderRuntimeBudget, ProviderRuntimeBudget.id == ProviderRuntimeReservation.budget_id).where(ProviderRuntimeBudget.job_id == job.id).order_by(ProviderRuntimeReservation.id))))
    if any((row.state in {'reserved', 'uncertain'} for row in reservations)):
        _reject('svg_timeout_repurchase_active_call')
    if any((row.estimated_reserved for row in budgets)):
        _reject('svg_timeout_repurchase_active_call')
    rows = list(await session.scalars(locked(select(AuthoredSlideCheckpoint).where(AuthoredSlideCheckpoint.job_id == job.id).order_by(AuthoredSlideCheckpoint.slide_index))))
    if not rows or len(rows) != job.total_slides or [row.slide_index for row in rows] != list(range(job.total_slides)):
        _reject()
    from services.svg_presentation_checkpoint import _read_png
    from models.sql.provider_usage_receipt import ProviderUsageReceipt
    parent = deepcopy(operation.request_payload.get('svg_timeout_repurchase_manifest') or {})
    parent_hash = operation.request_payload.get('svg_timeout_repurchase_manifest_hash')
    if not parent_hash or _digest(parent) != parent_hash or parent.get('recovery_revision') != SVG_TIMEOUT_REPURCHASE_V3 or (parent.get('owner_id') != str(operation.owner_id)) or (parent.get('tenant_id') != admission.tenant_id) or (parent.get('operation_id') != str(operation.retry_of_operation_id)) or (parent.get('presentation_id') != str(operation.presentation_id)) or (parent.get('presentation_revision') != presentation_revision) or (parent.get('job_id') != str(job.id)) or (parent.get('frozen_request_digest') != _digest(job.request_payload)) or (parent.get('frozen_style_digest') != _digest(job.svg_style_snapshot)) or (parent.get('previous_consumed_author_calls') != batch['terms']['previous_consumed_author_calls']) or (parent.get('remaining_author_call_limit') != batch['terms']['remaining_author_call_limit']) or (parent.get('total_author_call_limit') != batch['terms']['total_author_call_limit']) or calls or (admission.initial_request_count != 0) or (admission.retry_request_count != 0):
        _reject()
    origin = await session.scalar(locked(select(PublicOperationAdmission).where(PublicOperationAdmission.id == UUID(parent['old_admission']['id']))))
    if origin is None or _digest(origin.model_dump(mode='json')) != _digest(parent['old_admission']):
        _reject()
    origin_calls = list((await session.scalars(locked(select(PublicProviderLogicalCall).where(PublicProviderLogicalCall.admission_id == origin.id)))).all())
    original_calls = {item['id']: _digest(item) for item in parent.get('old_logical_calls', [])}
    if len(origin_calls) != batch['logical_calls'] or any((original_calls.get(str(call.id)) != _digest(call.model_dump(mode='json')) for call in origin_calls)):
        _reject()
    usage = list((await session.scalars(locked(select(ProviderUsageReceipt).where(ProviderUsageReceipt.job_id == job.id)))).all())
    if len(usage) != replay['usage_receipts'] or sum((item.request_count for item in usage)) != replay['usage_receipts'] or any((item.model != registry['author_model'] for item in usage)):
        _reject()
    origin_approval_hash = parent.get('origin_approval_manifest_hash', parent.get('approval_source_hash') if parent.get('zero_dispatch_replay') else parent_hash)
    groups = [(name, item) for name in ('completed_checkpoints', 'returned_checkpoints', 'unknown_checkpoints', 'unstarted_checkpoints') for item in parent.get(name, [])]
    if job.total_slides != total_slides or len(groups) != total_slides or len({item['slide_index'] for _, item in groups}) != total_slides:
        _reject()
    by_index = {item['slide_index']: (name, item) for name, item in groups}
    replay_rows = []
    for row in rows:
        name, item = by_index[row.slide_index]
        payload = row.svg_checkpoint or {}
        if name == 'unknown_checkpoints':
            fence = payload.get('retained_provider_attempt_fence') or {}
            if payload.get('recovery_revision') != SVG_TIMEOUT_REPURCHASE_V3 or payload.get('inflight') is not None or payload.get('svg_content') or payload.get('attempts') or payload.get('provider_failures') or (row.author_status != 'pending') or (payload.get('input_digest') != item['input_digest']) or (fence.get('consumed_attempt_count') != 1) or (fence.get('previous_ordinal') != 1) or not ((fence.get('approval_manifest_hash') == parent_hash and payload.get('recovery_operation_id') == str(operation.id)) or (fence.get('approval_manifest_hash') == origin_approval_hash and payload.get('recovery_operation_id') == str(operation.retry_of_operation_id))) or (fence.get('logical_call_ids') != item['logical_call_ids']) or (_digest(payload.get('previous_checkpoint_row') or {}) != item['row_digest']):
                _reject()
        elif name in {'completed_checkpoints', 'returned_checkpoints'}:
            if (str(row.id) != item['id'] or payload.get('input_digest') != item['input_digest']
                    or sha256_hex((payload.get('svg_content') or '').encode('utf-8')) != item['source_digest']
                    or _digest(payload.get('attempts') or []) != item['author_digest']
                    or payload.get('inflight') is not None or row.author_status != 'completed'):
                _reject()
            if name == 'completed_checkpoints' and (payload.get('png_sha256') != item['png_digest']
                    or (payload.get('quality') or {}).get('passed') is not True or _read_png(payload) is None):
                _reject()
            if name == 'returned_checkpoints' and (payload.get('png_sha256') or payload.get('quality')):
                _reject()
        elif name == 'unstarted_checkpoints':
            if (str(row.id) != item['id'] or payload.get('input_digest') != item['input_digest']
                    or payload.get('svg_content') or payload.get('inflight') is not None
                    or payload.get('attempts') or payload.get('provider_failures') or payload.get('model_attempt_count')
                    or payload.get('png_sha256') or row.author_status != 'pending' or row.render_status != 'pending'):
                _reject()
        else:
            _reject()
        replay_rows.append({'id': str(row.id), 'slide_index': row.slide_index, 'row_digest': _digest(row.model_dump(mode='json'))})
    manifest = deepcopy(parent)
    manifest.update(operation_id=str(operation.id), operation_version=operation.version, operation_fence=operation.fencing_token, operation_attempt=operation.attempt_number, operation_attempt_token_digest=_digest(operation.attempt_token), operation_request_digest=_digest(operation.request_payload), operation_error_digest=_digest(operation.error_code), job_attempt=job.attempt_number, job_attempt_token_digest=_digest(job.attempt_token), origin_operation_id=parent.get('origin_operation_id', parent['operation_id']), approval_source_hash=parent_hash, origin_approval_manifest_hash=origin_approval_hash, zero_dispatch_replay=True, zero_dispatch_admission=admission.model_dump(mode='json'), replay_checkpoints=replay_rows)
    return TimeoutRepurchaseRecovery(rows=[row for row in rows if by_index[row.slide_index][0] == 'unknown_checkpoints'], manifest=manifest)


async def _build_interrupted_batch_manifest(session, operation, *, presentation_revision, lock=True, recovery_revision=SVG_TIMEOUT_REPURCHASE_V3):
    """Lock and bind a terminal local snapshot, also usable for read-only review.

The returned hash is the approval contract. It is not permission to retry;
``verify_timeout_repurchase`` requires that exact hash in the action request.
"""
    registry = incident_registry()
    batch = registry["stages"]["interrupted_batch"]
    now = get_current_utc_datetime()
    mixed_recovery = True

    def locked(statement):
        return statement.with_for_update() if lock else statement
    if operation.kind != 'presentation.generate' or operation.state != 'failed' or operation.error_code != 'svg_provider_attempt_uncertain' or _live(operation.lease_expires_at, now):
        _reject()
    job = await session.scalar(locked(select(PresentationGenerationJob).where(PresentationGenerationJob.workspace_operation_id == operation.id, PresentationGenerationJob.presentation_id == operation.presentation_id)))
    if job is None or job.state != 'failed' or job.request_payload.get('strategy') != 'svg' or (not job.svg_style_snapshot) or _live(job.lease_expires_at, now):
        _reject()
    linked_id = operation.request_payload.get('job_id')
    if linked_id is not None and str(job.id) != str(linked_id):
        _reject()
    if mixed_recovery and (job.total_slides < 1 or job.svg_style_snapshot.get('authoring_profile') != registry['authoring_profile']):
        _reject()
    admission = await session.scalar(locked(select(PublicOperationAdmission).where(PublicOperationAdmission.operation_id == operation.id)))
    if admission is None or admission.state not in {'failed', 'deadline_exceeded'} or admission.terminal_at is None or (admission.owner_id != operation.owner_id) or admission.estimated_reserved_microunits or admission.actual_reserved_microunits:
        _reject()
    leases = list(await session.scalars(locked(select(PublicResourceLease).where(PublicResourceLease.admission_id == admission.id).order_by(PublicResourceLease.scope_type))))
    if any((lease.state not in {'released', 'cancelled', 'expired'} for lease in leases)):
        _reject('svg_timeout_repurchase_active_lease')
    calls = list(await session.scalars(locked(select(PublicProviderLogicalCall).where(PublicProviderLogicalCall.admission_id == admission.id).order_by(PublicProviderLogicalCall.stage, PublicProviderLogicalCall.slide_index, PublicProviderLogicalCall.logical_call_ordinal))))
    if not calls or any((call.state == 'reserved' or call.reconciled_at is None or call.outcome not in {'succeeded', 'failed', 'cancelled'} for call in calls)):
        _reject('svg_timeout_repurchase_active_call')
    budgets = list(await session.scalars(locked(select(ProviderRuntimeBudget).where(ProviderRuntimeBudget.job_id == job.id).order_by(ProviderRuntimeBudget.id))))
    reservations = list(await session.scalars(locked(select(ProviderRuntimeReservation).join(ProviderRuntimeBudget, ProviderRuntimeBudget.id == ProviderRuntimeReservation.budget_id).where(ProviderRuntimeBudget.job_id == job.id).order_by(ProviderRuntimeReservation.id))))
    if any((row.state in {'reserved', 'uncertain'} for row in reservations)):
        _reject('svg_timeout_repurchase_active_call')
    if any((row.estimated_reserved for row in budgets)):
        _reject('svg_timeout_repurchase_active_call')
    rows = list(await session.scalars(locked(select(AuthoredSlideCheckpoint).where(AuthoredSlideCheckpoint.job_id == job.id).order_by(AuthoredSlideCheckpoint.slide_index))))
    if not rows or len(rows) != job.total_slides or [row.slide_index for row in rows] != list(range(job.total_slides)):
        _reject()
    from services.svg_presentation_checkpoint import _read_png
    unknown, completed, returned, unstarted = ([], [], [], [])
    for row in rows:
        payload = row.svg_checkpoint or {}
        if payload.get('retained_provider_attempt_fence') or payload.get('recovery_revision') or (not payload.get('input_digest') and (payload.get('svg_content') or payload.get('inflight') or payload.get('attempts'))):
            _reject()
        witnesses = [call for call in calls if call.slide_index == row.slide_index]
        item = {'id': str(row.id), 'slide_index': row.slide_index, 'row_digest': _digest(row.model_dump(mode='json')), 'input_digest': payload.get('input_digest')}
        svg = payload.get('svg_content')
        attempts = payload.get('attempts') or []
        if svg:
            if payload.get('inflight') or payload.get('model_attempt_count') != 1 or len(attempts) != 1 or (len(witnesses) != 1):
                _reject()
            author_receipt = attempts[0].get('receipt') or {}
            witness = witnesses[0]
            if attempts[0].get('kind') != 'provider' or not attempts[0].get('raw_response') or author_receipt.get('validation_error') is not None or (author_receipt.get('attempt_ordinal') != 1) or (author_receipt.get('raw_digest') != 'sha256:' + sha256_hex(attempts[0]['raw_response'].encode('utf-8'))) or (not author_receipt.get('source_digest')) or (author_receipt.get('requested_model') != witness.model_id) or (witness.stage != 'author') or (witness.state != 'reported') or (witness.outcome != 'succeeded') or (witness.provider_id != registry['author_provider']) or (witness.model_id != registry['author_model']) or (row.author_status != 'completed'):
                _reject()
            item.update(source_digest=sha256_hex(svg.encode('utf-8')), author_digest=_digest(attempts), logical_call_ids=[str(witness.id)], consumed_attempt_count=1, previous_ordinal=1)
            quality = payload.get('quality') or {}
            if quality.get('passed') is True and payload.get('png_sha256') and (_read_png(payload) is not None) and (row.render_status == 'completed'):
                item['png_digest'] = payload['png_sha256']
                completed.append(item)
            elif not quality and (not payload.get('png_sha256')) and (row.render_status == 'pending'):
                returned.append(item)
            else:
                _reject()
        elif payload.get('inflight') == 1:
            if attempts or payload.get('provider_failures') or payload.get('model_attempt_count') or (len(witnesses) != 1):
                _reject()
            witness = witnesses[0]
            if witness.stage != 'author' or witness.state not in {'cancelled', 'unavailable'} or witness.outcome != 'cancelled' or (witness.provider_id != registry['author_provider']) or (witness.model_id != registry['author_model']):
                _reject()
            item.update(previous_ordinal=1, consumed_attempt_count=1, next_provider_ordinal=2, max_additional_author_calls=1, previous_source_digest=None, logical_call_ids=[str(witness.id)])
            unknown.append(item)
        else:
            if payload.get('inflight') is not None or attempts or witnesses or payload.get('provider_failures') or payload.get('model_attempt_count') or (row.author_status != 'pending') or (row.render_status != 'pending') or payload.get('png_sha256'):
                _reject()
            item.update(consumed_attempt_count=0, next_provider_ordinal=1, author_attempt_budget_per_page=2)
            unstarted.append(item)
    layout = batch['layout']
    if job.total_slides != registry['total_slides'] or len(completed) != layout['completed'] or len(returned) != layout['returned'] or (len(unknown) != layout['unknown']) or (len(unstarted) != layout['unstarted']) or (len(calls) != batch['logical_calls']) or (admission.initial_request_count != batch['initial_request_count']) or (admission.retry_request_count != batch['retry_request_count']):
        _reject()
    from utils.llm_provider import get_llm_provider, get_model
    from utils.llm_calls.author_prompt_profile import authoring_model
    manifest = {'recovery_revision': recovery_revision, 'operation_id': str(operation.id), 'operation_version': operation.version, 'operation_fence': operation.fencing_token, 'operation_attempt': operation.attempt_number, 'operation_attempt_token_digest': _digest(operation.attempt_token), 'operation_request_digest': _digest(operation.request_payload), 'operation_error_digest': _digest(operation.error_detail), 'presentation_id': str(operation.presentation_id), 'presentation_revision': presentation_revision, **({'owner_id': str(operation.owner_id), 'tenant_id': admission.tenant_id} if mixed_recovery else {}), 'job_id': str(job.id), 'job_attempt': job.attempt_number, 'job_attempt_token_digest': _digest(job.attempt_token), 'frozen_request_digest': _digest(job.request_payload), 'admitted_request_digest': job.request_sha256, 'frozen_style_digest': _digest(job.svg_style_snapshot), 'frozen_profile': job.svg_style_snapshot.get('authoring_profile'), 'completed_checkpoints': completed, 'unknown_checkpoints': unknown, 'old_admission': admission.model_dump(mode='json'), 'old_logical_calls': [call.model_dump(mode='json') for call in calls], 'old_resource_leases': [lease.model_dump(mode='json') for lease in leases], 'old_runtime_budgets': [row.model_dump(mode='json') for row in budgets], 'old_runtime_reservations': [row.model_dump(mode='json') for row in reservations], 'retry_max_attempts': operation.max_attempts, 'max_additional_author_calls': len(unknown), 'author_attempt_budget_per_page': 2, **({'max_additional_vqa_calls': 1, 'additional_purchase_budget_per_target': 1, 'previous_attempts_remain_consumed': True} if mixed_recovery else {}), 'new_author_provider': get_llm_provider().value, 'new_author_model': authoring_model(get_model()), 'deck_vqa_uses_existing_admission_budget': True, 'remote_termination_confirmed': False, 'duplicate_billing_possible': True, 'old_ledger_writes_permitted': False}
    manifest.update(returned_checkpoints=returned, unstarted_checkpoints=unstarted, **batch['terms'])
    if not _pinned_author(registry, manifest['new_author_provider'], manifest['new_author_model']):
        _reject()
    target_ids = {item['id'] for item in unknown}
    return TimeoutRepurchaseRecovery([row for row in rows if str(row.id) in target_ids], manifest)


async def _build_bounded_overload_manifest(session, operation, *, presentation_revision, lock=True):
    """One signed exception for the frozen TPLAYS failure, never a general retry."""
    from models.sql.provider_usage_receipt import ProviderUsageReceipt
    from services.svg_presentation_checkpoint import _read_png
    from utils.llm_provider import get_llm_provider, get_model
    from utils.llm_calls.author_prompt_profile import authoring_model

    registry = incident_registry()
    stage = registry['stages']['bounded_overload']
    batch_terms = registry['stages']['interrupted_batch']['terms']
    total_slides = registry['total_slides']
    target_specs = {spec['slide_index']: spec for spec in stage['targets']}
    call_stages = {spec['slide_index']: spec['stage'] for spec in stage['logical_calls']}
    now = get_current_utc_datetime()
    def locked(statement):
        return statement.with_for_update() if lock else statement

    if (str(operation.id) != stage['operation_id']
            or str(operation.presentation_id) != registry['presentation_id']
            or operation.kind != 'presentation.generate' or operation.state != 'failed'
            or operation.error_code != 'svg_repair_budget_exhausted'
            or operation.request_payload.get('recovery_revision') != SVG_TIMEOUT_REPURCHASE_V3
            or _live(operation.lease_expires_at, now)):
        _reject()
    job = await session.scalar(locked(select(PresentationGenerationJob).where(
        PresentationGenerationJob.workspace_operation_id == operation.id,
        PresentationGenerationJob.presentation_id == operation.presentation_id)))
    admission = await session.scalar(locked(select(PublicOperationAdmission).where(
        PublicOperationAdmission.operation_id == operation.id)))
    if (job is None or admission is None or job.state != 'failed' or job.total_slides != total_slides
            or job.completed_slides != stage['completed_slides'] or str(job.id) != registry['job_id']
            or str(operation.request_payload.get('job_id')) != str(job.id)
            or _live(job.lease_expires_at, now) or admission.owner_id != operation.owner_id
            or admission.state != 'failed' or admission.terminal_at is None
            or admission.estimated_reserved_microunits or admission.actual_reserved_microunits
            or admission.initial_request_count != stage['initial_request_count']
            or admission.retry_request_count != stage['retry_request_count']):
        _reject()
    parent = deepcopy(operation.request_payload.get('svg_timeout_repurchase_manifest') or {})
    parent_hash = operation.request_payload.get('svg_timeout_repurchase_manifest_hash')
    if (not parent_hash or _digest(parent) != parent_hash or parent.get('bounded_overload_exception')
            or parent.get('recovery_revision') != SVG_TIMEOUT_REPURCHASE_V3
            or parent.get('owner_id') != str(operation.owner_id) or parent.get('tenant_id') != admission.tenant_id
            or parent.get('operation_id') != str(operation.retry_of_operation_id)
            or parent.get('presentation_id') != str(operation.presentation_id)
            or parent.get('presentation_revision') != presentation_revision or parent.get('job_id') != str(job.id)
            or parent.get('previous_consumed_author_calls') != batch_terms['previous_consumed_author_calls']
            or parent.get('remaining_author_call_limit') != batch_terms['remaining_author_call_limit']
            or parent.get('total_author_call_limit') != batch_terms['total_author_call_limit']
            or parent.get('frozen_request_digest') != _digest(job.request_payload)
            or parent.get('frozen_style_digest') != _digest(job.svg_style_snapshot)
            or _digest(job.request_payload) != registry['frozen_request_digest']
            or _digest(job.svg_style_snapshot) != registry['frozen_style_digest']):
        _reject()
    leases = list(await session.scalars(locked(select(PublicResourceLease).where(
        PublicResourceLease.admission_id == admission.id).order_by(PublicResourceLease.scope_type))))
    budgets = list(await session.scalars(locked(select(ProviderRuntimeBudget).where(
        ProviderRuntimeBudget.job_id == job.id).order_by(ProviderRuntimeBudget.id))))
    reservations = list(await session.scalars(locked(select(ProviderRuntimeReservation).join(
        ProviderRuntimeBudget, ProviderRuntimeBudget.id == ProviderRuntimeReservation.budget_id).where(
        ProviderRuntimeBudget.job_id == job.id).order_by(ProviderRuntimeReservation.id))))
    if any(lease.state not in {'released', 'cancelled', 'expired'} for lease in leases):
        _reject('svg_timeout_repurchase_active_lease')
    if any(row.estimated_reserved for row in budgets) or any(row.state in {'reserved', 'uncertain'} for row in reservations):
        _reject('svg_timeout_repurchase_active_call')
    calls = list(await session.scalars(locked(select(PublicProviderLogicalCall).where(
        PublicProviderLogicalCall.admission_id == admission.id).order_by(PublicProviderLogicalCall.slide_index))))
    if (len(calls) != len(stage['logical_calls'])
            or [call.slide_index for call in calls] != [spec['slide_index'] for spec in stage['logical_calls']]
            or any(call.stage != call_stages[call.slide_index]
                   or call.state != 'unavailable' or call.outcome != 'failed' or call.reconciled_at is None
                   or not _pinned_author(registry, call.provider_id, call.model_id) for call in calls)):
        _reject('svg_timeout_repurchase_active_call')
    origin = await session.get(PublicOperationAdmission, UUID(parent['old_admission']['id']))
    origin_calls = list(await session.scalars(locked(select(PublicProviderLogicalCall).where(
        PublicProviderLogicalCall.admission_id == UUID(parent['old_admission']['id'])))))
    archived = {item['id']: _digest(item) for item in parent.get('old_logical_calls', [])}
    batch_calls = registry['stages']['interrupted_batch']['logical_calls']
    if (origin is None or _digest(origin.model_dump(mode='json')) != _digest(parent['old_admission'])
            or len(origin_calls) != batch_calls or len(archived) != batch_calls
            or any(archived.get(str(call.id)) != _digest(call.model_dump(mode='json')) for call in origin_calls)):
        _reject()
    usage = list(await session.scalars(locked(select(ProviderUsageReceipt).where(
        ProviderUsageReceipt.job_id == job.id).order_by(ProviderUsageReceipt.id))))
    if (len(usage) != len(stage['usage_outcomes']) or sum(item.request_count for item in usage) != len(stage['usage_outcomes'])
            or sorted(item.outcome for item in usage) != stage['usage_outcomes']
            or any(not _pinned_author(registry, item.provider, item.model) for item in usage)):
        _reject()
    rows = list(await session.scalars(locked(select(AuthoredSlideCheckpoint).where(
        AuthoredSlideCheckpoint.job_id == job.id).order_by(AuthoredSlideCheckpoint.slide_index))))
    groups = [(name, item) for name in ('completed_checkpoints', 'returned_checkpoints', 'unknown_checkpoints', 'unstarted_checkpoints')
              for item in parent.get(name, [])]
    if len(rows) != total_slides or len(groups) != total_slides or {item['slide_index'] for _, item in groups} != set(range(total_slides)):
        _reject()
    by_index = {item['slide_index']: (name, item) for name, item in groups}
    completed, failed, unstarted = [], [], []
    for row in rows:
        payload = row.svg_checkpoint or {}
        name, previous = by_index[row.slide_index]
        item = {'id': str(row.id), 'slide_index': row.slide_index,
                'row_digest': _digest(row.model_dump(mode='json')), 'input_digest': payload.get('input_digest')}
        if str(row.id) != previous['id'] or payload.get('inflight') is not None:
            _reject()
        if row.slide_index in stage['completed_slide_indexes']:
            author_evidence = payload.get('attempts') or []
            if name == 'returned_checkpoints':
                # Rendering appends local quality to an already returned
                # attempt. Bind its original provider receipt and raw response.
                author_evidence = [{key: value for key, value in attempt.items() if key != 'quality'}
                                   for attempt in author_evidence]
            if (name not in {'completed_checkpoints', 'returned_checkpoints'}
                    or payload.get('input_digest') != previous['input_digest']
                    or sha256_hex((payload.get('svg_content') or '').encode()) != previous['source_digest']
                    or _digest(author_evidence) != previous['author_digest']
                    or (name == 'completed_checkpoints' and payload.get('png_sha256') != previous.get('png_digest'))
                    or payload.get('provider_failures') or (payload.get('quality') or {}).get('passed') is not True
                    or row.author_status != 'completed' or row.render_status != 'completed' or _read_png(payload) is None):
                _reject('svg_timeout_repurchase_completed_cache_missing')
            item.update(source_digest=previous['source_digest'], author_digest=_digest(payload.get('attempts') or []),
                        png_digest=payload['png_sha256'], consumed_attempt_count=1, previous_ordinal=1)
            completed.append(item)
        elif row.slide_index in target_specs:
            spec = target_specs[row.slide_index]
            consumed = spec['consumed_attempt_count']
            if (not payload.get('input_digest') or payload.get('svg_content') or payload.get('png_sha256')
                    or payload.get('attempts') or payload.get('model_attempt_count')
                    or payload.get('provider_failures') != [{'ordinal': consumed, 'code': 'public_provider_overload'}]):
                _reject('svg_timeout_repurchase_attempt_budget_exhausted')
            if spec['user_exception']:
                fence = payload.get('retained_provider_attempt_fence') or {}
                if (name != 'unknown_checkpoints' or payload.get('recovery_revision') != SVG_TIMEOUT_REPURCHASE_V3
                        or payload.get('recovery_operation_id') != str(operation.id)
                        or fence.get('approval_manifest_hash') != parent_hash
                        or fence.get('consumed_attempt_count') != 1 or fence.get('previous_ordinal') != 1
                        or fence.get('logical_call_ids') != previous['logical_call_ids']
                        or payload.get('input_digest') != previous['input_digest']
                        or _digest(payload.get('previous_checkpoint_row') or {}) != previous['row_digest']):
                    _reject()
            elif name != 'unstarted_checkpoints' or payload.get('recovery_revision') or payload.get('retained_provider_attempt_fence'):
                _reject()
            item.update(consumed_attempt_count=consumed, previous_ordinal=consumed,
                        next_provider_ordinal=consumed + 1, max_additional_author_calls=1,
                        previous_source_digest=None, logical_call_ids=[str(call.id) for call in calls if call.slide_index == row.slide_index],
                        known_local_failure='public_provider_overload', user_exception=spec['user_exception'])
            failed.append(item)
        else:
            if (name != 'unstarted_checkpoints' or payload.get('input_digest') != previous['input_digest']
                    or payload.get('svg_content') or payload.get('attempts') or payload.get('provider_failures')
                    or payload.get('model_attempt_count') or payload.get('png_sha256') or payload.get('recovery_revision')
                    or payload.get('retained_provider_attempt_fence') or row.author_status != 'pending' or row.render_status != 'pending'):
                _reject()
            item.update(consumed_attempt_count=0, next_provider_ordinal=1, author_attempt_budget_per_page=2)
            unstarted.append(item)
    manifest = {**deepcopy(parent), 'bounded_overload_exception': True, 'zero_dispatch_replay': False,
        'approval_source_hash': parent_hash, 'previous_approval_manifest': parent,
        'operation_id': str(operation.id), 'operation_version': operation.version,
        'operation_fence': operation.fencing_token, 'operation_attempt': operation.attempt_number,
        'operation_attempt_token_digest': _digest(operation.attempt_token),
        'operation_request_digest': _digest(operation.request_payload), 'operation_error_digest': _digest(operation.error_detail),
        'job_attempt': job.attempt_number, 'job_attempt_token_digest': _digest(job.attempt_token),
        'completed_checkpoints': completed, 'returned_checkpoints': [], 'unknown_checkpoints': failed,
        'unstarted_checkpoints': unstarted, 'old_admission': admission.model_dump(mode='json'),
        'old_logical_calls': [call.model_dump(mode='json') for call in calls],
        'old_resource_leases': [lease.model_dump(mode='json') for lease in leases],
        'old_runtime_budgets': [row.model_dump(mode='json') for row in budgets],
        'old_runtime_reservations': [row.model_dump(mode='json') for row in reservations],
        'old_usage_receipts': [row.model_dump(mode='json') for row in usage],
        **stage['terms'], 'retry_max_attempts': operation.max_attempts,
        'new_author_provider': get_llm_provider().value, 'new_author_model': authoring_model(get_model())}
    if not _bounded_overload_shape(manifest):
        _reject()
    return TimeoutRepurchaseRecovery([row for row in rows if row.slide_index in target_specs], manifest)


def _bounded_overload_shape(manifest):
    targets = manifest.get('unknown_checkpoints', [])
    if manifest.get('bounded_overload_exception') is not True:
        return False
    registry = incident_registry()
    stage = registry['stages']['bounded_overload']
    target_specs = {spec['slide_index']: spec for spec in stage['targets']}
    return (manifest.get('operation_id') == stage['operation_id']
        and manifest.get('presentation_id') == registry['presentation_id']
        and manifest.get('job_id') == registry['job_id']
        and _terms_match(manifest, stage['terms']) and _terms_match(manifest, stage['inherited_terms'])
        and _pinned_author(registry, manifest.get('new_author_provider'), manifest.get('new_author_model'))
        and [item.get('slide_index') for item in manifest.get('completed_checkpoints', [])] == stage['completed_slide_indexes']
        and not manifest.get('returned_checkpoints')
        and [item.get('slide_index') for item in manifest.get('unstarted_checkpoints', [])] == stage['unstarted_slide_indexes']
        and [item.get('slide_index') for item in targets] == [spec['slide_index'] for spec in stage['targets']]
        and all(item.get('consumed_attempt_count') == target_specs[item['slide_index']]['consumed_attempt_count']
                and item.get('previous_ordinal') == item['consumed_attempt_count']
                and item.get('next_provider_ordinal') == item['consumed_attempt_count'] + 1
                and item.get('max_additional_author_calls') == 1
                and item.get('known_local_failure') == 'public_provider_overload'
                and item.get('user_exception') is target_specs[item['slide_index']]['user_exception'] for item in targets)
        and _digest(manifest.get('previous_approval_manifest') or {}) == manifest.get('approval_source_hash'))


def bounded_completion_shape(manifest):
    """Validate only this inactive, consent-bound final sample recovery shape."""
    if not isinstance(manifest, dict):
        return False
    if manifest.get('bounded_quality_continuation') is True:
        return _bounded_quality_continuation_shape(manifest)
    if manifest.get('bounded_continuation') is True:
        return _bounded_continuation_shape(manifest)
    targets = manifest.get('unknown_checkpoints')
    parent = manifest.get('previous_approval_manifest')
    if not isinstance(targets, list) or not isinstance(parent, dict) or not _bounded_overload_shape(parent):
        return False
    if manifest.get('bounded_completion_recovery') is not True:
        return False
    registry = incident_registry()
    stage = registry['stages']['bounded_completion']
    return (manifest.get('operation_id') == stage['operation_id']
        and manifest.get('presentation_id') == registry['presentation_id']
        and manifest.get('job_id') == registry['job_id']
        and manifest.get('owner_id') == parent.get('owner_id') and manifest.get('tenant_id') == parent.get('tenant_id')
        and manifest.get('frozen_request_digest') == parent.get('frozen_request_digest')
        and manifest.get('frozen_style_digest') == parent.get('frozen_style_digest')
        and _terms_match(manifest, stage['terms']) and _terms_match(manifest, stage['inherited_terms'])
        and _pinned_author(registry, manifest.get('new_author_provider'), manifest.get('new_author_model'))
        and [item.get('slide_index') for item in manifest.get('completed_checkpoints', [])] == stage['completed_slide_indexes']
        and [item.get('slide_index') for item in manifest.get('returned_checkpoints', [])] == [stage['returned_slide_index']]
        and [item.get('slide_index') for item in manifest.get('unstarted_checkpoints', [])] == stage['unstarted_slide_indexes']
        and len(targets) == len(stage['target_slide_indexes'])
        and [item.get('slide_index') for item in targets] == stage['target_slide_indexes']
        and all(item.get('consumed_attempt_count') == stage['target_consumed_attempt_count']
                and item.get('previous_ordinal') == stage['target_consumed_attempt_count']
                and item.get('next_provider_ordinal') == stage['target_consumed_attempt_count'] + 1
                and item.get('max_additional_author_calls') == 1
                and item.get('remote_outcome') == 'unknown' for item in targets)
        and _digest(parent) == manifest.get('approval_source_hash')
        and len(manifest.get('historical_logical_calls', [])) == stage['historical_logical_calls'])


def _continuation_historical_normal_second_purchases(manifest):
    parent = manifest.get('previous_approval_manifest') or {}
    indexes = [item.get('slide_index') for item in parent.get('unstarted_checkpoints', [])]
    calls = manifest.get('historical_logical_calls', [])
    return sum(max(0, sum(call.get('slide_index') == index
        and call.get('stage') in {'author', 'repair'} for call in calls) - 1) for index in indexes)


def _continuation_second_purchases(pages, stage):
    return len(pages[stage['target_slide_index']]) + sum(
        max(0, len(pages[index]) - 1) for index in stage['unstarted_slide_indexes'])


def _unstarted_pages_fresh(manifest):
    return all(item.get('consumed_attempt_count') == 0
        and item.get('next_provider_ordinal') == 1
        and item.get('author_attempt_budget_per_page') == 2
        for item in manifest.get('unstarted_checkpoints', []))


def _bounded_continuation_shape(manifest):
    """The single signed continuation of the accepted twelve-page sample."""
    parent = manifest.get('previous_approval_manifest')
    targets = manifest.get('unknown_checkpoints', [])
    if not (isinstance(parent, dict) and not parent.get('bounded_continuation')
            and bounded_completion_shape(parent)):
        return False
    registry = incident_registry()
    stage = registry['stages']['bounded_continuation']
    serial_plan = registry['stages']['bounded_completion']['terms']['serial_execution_plan']
    return (_digest(parent) == manifest.get('approval_source_hash') == stage['approval_source_hash']
        and manifest.get('bounded_completion_recovery') is True
        and manifest.get('bounded_continuation') is True
        and manifest.get('recovery_revision') == SVG_TIMEOUT_REPURCHASE_V3
        and manifest.get('operation_id') == stage['operation_id']
        and all(manifest.get(key) == parent.get(key) for key in
            ('presentation_id', 'job_id', 'owner_id', 'tenant_id', 'presentation_revision',
             'frozen_request_digest', 'frozen_style_digest'))
        and _terms_match(manifest, stage['terms'], ('previous_consumed_author_calls', 'remaining_author_call_limit',
                                                    'remaining_all_provider_call_limit'))
        and _terms_match(manifest, stage['inherited_terms'], ('total_all_provider_call_limit',))
        and _continuation_historical_normal_second_purchases(manifest) == stage['historical_normal_second_purchases']
        and _terms_match(manifest, stage['terms'], ('previous_normal_second_purchases',))
        and manifest.get('max_normal_second_purchases') == parent.get('max_normal_second_purchases') - 1
            == stage['terms']['max_normal_second_purchases']
        and _terms_match(manifest, stage['terms'], ('second_purchase_page_indexes', 'max_normal_author_calls',
                                                    'max_additional_author_calls'))
        and _terms_match(manifest, stage['inherited_terms'], ('max_additional_vqa_calls', 'max_returned_repair_calls',
            'additional_purchase_budget_per_target', 'retry_max_attempts'))
        and _pinned_author(registry, manifest.get('new_author_provider'), manifest.get('new_author_model'))
        and manifest.get('serial_execution_plan') == serial_plan
        and _terms_match(manifest, stage['terms'], ('execution_profile',))
        and [item.get('slide_index') for item in manifest.get('completed_checkpoints', [])]
            == list(range(stage['cached_page_count']))
        and not manifest.get('returned_checkpoints')
        and [item.get('slide_index') for item in manifest.get('unstarted_checkpoints', [])] == stage['unstarted_slide_indexes']
        and _unstarted_pages_fresh(manifest)
        and len(targets) == 1 and targets[0].get('slide_index') == stage['target_slide_index']
        and targets[0].get('consumed_attempt_count') == stage['target_consumed_attempt_count']
        and targets[0].get('previous_ordinal') == stage['target_consumed_attempt_count']
        and targets[0].get('next_provider_ordinal') == stage['target_consumed_attempt_count'] + 1
        and targets[0].get('max_additional_author_calls') == 1
        and targets[0].get('remote_outcome') == 'unknown'
        and len(manifest.get('historical_logical_calls', [])) == stage['historical_logical_calls'])


def _bounded_quality_continuation_shape(manifest):
    parent = manifest.get('previous_approval_manifest')
    if not (isinstance(parent, dict) and _bounded_continuation_shape(parent)):
        return False
    registry = incident_registry()
    stage = registry['stages']['bounded_quality_continuation']
    serial_plan = registry['stages']['bounded_completion']['terms']['serial_execution_plan']
    calls = manifest.get('historical_logical_calls', [])
    return (_digest(parent) == manifest.get('approval_source_hash') == stage['approval_source_hash']
        and manifest.get('bounded_completion_recovery') is True
        and manifest.get('bounded_continuation') is False
        and manifest.get('bounded_quality_continuation') is True
        and manifest.get('recovery_revision') == SVG_TIMEOUT_REPURCHASE_V3
        and manifest.get('operation_id') == stage['operation_id']
        and all(manifest.get(key) == parent.get(key) for key in
            ('presentation_id', 'job_id', 'owner_id', 'tenant_id', 'presentation_revision',
             'frozen_request_digest', 'frozen_style_digest'))
        and _terms_match(manifest, stage['terms']) and _terms_match(manifest, stage['inherited_terms'])
        and _pinned_author(registry, manifest.get('new_author_provider'), manifest.get('new_author_model'))
        and manifest.get('serial_execution_plan') == serial_plan
        and [item.get('slide_index') for item in manifest.get('completed_checkpoints', [])]
            == list(range(stage['cached_page_count']))
        and not manifest.get('unknown_checkpoints') and not manifest.get('returned_checkpoints')
        and [item.get('slide_index') for item in manifest.get('unstarted_checkpoints', [])] == stage['unstarted_slide_indexes']
        and _unstarted_pages_fresh(manifest)
        and len(calls) == stage['historical_logical_calls']
        and sum(max(0, sum(call.get('slide_index') == index
            and call.get('stage') in {'author', 'repair'}
            for call in calls) - 1)
            for index in stage['historical_second_purchase_slide_indexes'])
            == stage['historical_normal_second_purchases'])


async def _build_bounded_continuation_manifest(session, operation, *, presentation_revision, lock=True,
                                               quality_continuation=False):
    from models.sql.workspace import WorkspaceOperation
    from models.sql.provider_usage_receipt import ProviderUsageReceipt
    from services.svg_presentation_checkpoint import _read_png
    from utils.llm_provider import get_llm_provider, get_model
    from utils.llm_calls.author_prompt_profile import authoring_model

    def locked(statement):
        return statement.with_for_update() if lock else statement
    now = get_current_utc_datetime()
    parent = deepcopy(operation.request_payload.get('svg_timeout_repurchase_manifest') or {})
    parent_hash = operation.request_payload.get('svg_timeout_repurchase_manifest_hash')
    quality = quality_continuation
    registry = incident_registry()
    stage = registry['stages']['bounded_quality_continuation' if quality else 'bounded_continuation']
    total_slides = registry['total_slides']
    target_index = stage['target_slide_index']
    if (str(operation.id) != stage['operation_id']
            or operation.kind != 'presentation.generate' or operation.state != 'failed'
            or operation.error_code != stage['source_error_code']
            or operation.max_attempts != 1 or operation.attempt_number != 1
            or _live(operation.lease_expires_at, now)
            or (not _bounded_continuation_shape(parent) if quality
                else parent.get('bounded_continuation') or not bounded_completion_shape(parent))
            or _digest(parent) != parent_hash
            or parent_hash != stage['approval_source_hash']
            or parent['operation_id'] != str(operation.retry_of_operation_id)
            or parent['owner_id'] != str(operation.owner_id)
            or parent['presentation_id'] != str(operation.presentation_id)
            or parent['presentation_revision'] != presentation_revision):
        _reject()
    job = await session.scalar(locked(select(PresentationGenerationJob).where(
        PresentationGenerationJob.workspace_operation_id == operation.id,
        PresentationGenerationJob.id == UUID(parent['job_id']))))
    admission = await session.scalar(locked(select(PublicOperationAdmission).where(
        PublicOperationAdmission.operation_id == operation.id)))
    if (job is None or admission is None or job.state != 'failed' or job.total_slides != total_slides
            or job.completed_slides != stage['completed_slides'] or job.attempt_number != 1 or _live(job.lease_expires_at, now)
            or admission.state != 'failed' or admission.terminal_at is None
            or admission.owner_id != operation.owner_id or admission.tenant_id != parent['tenant_id']
            or admission.initial_request_count + admission.retry_request_count != stage['admission_request_count']
            or _digest(job.request_payload) != parent['frozen_request_digest']
            or _digest(job.svg_style_snapshot) != parent['frozen_style_digest']):
        _reject()
    operations = list(await session.scalars(locked(select(WorkspaceOperation).where(
        WorkspaceOperation.presentation_id == operation.presentation_id,
        WorkspaceOperation.kind == 'presentation.generate').order_by(WorkspaceOperation.id))))
    expected_ids = set(parent['historical_operation_ids']) | {parent['operation_id'], str(operation.id)}
    if {str(row.id) for row in operations} != expected_ids:
        _reject()
    admissions = list(await session.scalars(locked(select(PublicOperationAdmission).where(
        PublicOperationAdmission.operation_id.in_([row.id for row in operations])).order_by(PublicOperationAdmission.id))))
    calls = list(await session.scalars(locked(select(PublicProviderLogicalCall).where(
        PublicProviderLogicalCall.admission_id.in_([row.id for row in admissions])).order_by(PublicProviderLogicalCall.id))))
    if (len(admissions) != stage['admissions'] or len(calls) != stage['logical_calls']
            or sum(row.initial_request_count + row.retry_request_count for row in admissions) != stage['logical_calls']
            or any(row.owner_id != operation.owner_id or row.tenant_id != admission.tenant_id
                or row.state not in {'failed', 'deadline_exceeded'} or row.terminal_at is None
                or row.estimated_reserved_microunits or row.actual_reserved_microunits for row in admissions)
            or any(row.stage not in {'author', 'repair'} or row.reconciled_at is None
                or row.outcome not in {'succeeded', 'failed', 'cancelled'} or row.state not in {'reported', 'unavailable'}
                or not _pinned_author(registry, row.provider_id, row.model_id) for row in calls)
            or [sum(row.slide_index == index for row in calls) for index in range(total_slides)]
                != stage['per_slide_calls']):
        _reject('svg_timeout_repurchase_active_call')
    old_admissions = [row for row in admissions if str(row.id) in {item['id'] for item in parent['historical_admissions']}]
    old_calls = [row for row in calls if str(row.admission_id) in {item['id'] for item in parent['historical_admissions']}]
    if (_digest([row.model_dump(mode='json') for row in old_admissions]) != _digest(parent['historical_admissions'])
            or _digest([row.model_dump(mode='json') for row in old_calls]) != _digest(parent['historical_logical_calls'])):
        _reject('svg_timeout_repurchase_manifest_conflict')
    current_calls = [row for row in calls if row.admission_id == admission.id]
    unknown_calls = [row for row in current_calls if row.slide_index == target_index]
    if (len(unknown_calls) != 1 or unknown_calls[0].stage != stage['target_call']['stage']
            or unknown_calls[0].state != stage['target_call']['state']
            or unknown_calls[0].outcome != stage['target_call']['outcome']
            or any(row.outcome != 'succeeded' for row in current_calls if row.slide_index != target_index)):
        _reject()
    leases = list(await session.scalars(locked(select(PublicResourceLease).where(
        PublicResourceLease.admission_id.in_([row.id for row in admissions])).order_by(PublicResourceLease.id))))
    budgets = list(await session.scalars(locked(select(ProviderRuntimeBudget).where(
        ProviderRuntimeBudget.job_id == job.id).order_by(ProviderRuntimeBudget.id))))
    reservations = list(await session.scalars(locked(select(ProviderRuntimeReservation).join(
        ProviderRuntimeBudget, ProviderRuntimeBudget.id == ProviderRuntimeReservation.budget_id).where(
        ProviderRuntimeBudget.job_id == job.id).order_by(ProviderRuntimeReservation.id))))
    if any(row.state not in {'released', 'cancelled', 'expired'} for row in leases):
        _reject('svg_timeout_repurchase_active_lease')
    if any(row.estimated_reserved for row in budgets) or any(row.state in {'reserved', 'uncertain'} for row in reservations):
        _reject('svg_timeout_repurchase_active_call')
    usage = list(await session.scalars(locked(select(ProviderUsageReceipt).where(
        ProviderUsageReceipt.job_id == job.id).order_by(ProviderUsageReceipt.id))))
    if sum(row.request_count for row in usage) != stage['usage_requests'] or any(not _pinned_author(registry, row.provider, row.model) for row in usage):
        _reject()
    rows = list(await session.scalars(locked(select(AuthoredSlideCheckpoint).where(
        AuthoredSlideCheckpoint.job_id == job.id).order_by(AuthoredSlideCheckpoint.slide_index))))
    if [row.slide_index for row in rows] != list(range(total_slides)):
        _reject()
    previous_caches = {item['slide_index']: item for item in parent['completed_checkpoints'] + parent['returned_checkpoints']}
    completed, unknown, unstarted = [], [], []
    for row in rows:
        payload = row.svg_checkpoint or {}
        item = {'id': str(row.id), 'slide_index': row.slide_index,
            'row_digest': _digest(row.model_dump(mode='json')), 'input_digest': payload.get('input_digest')}
        if row.slide_index < stage['cached_page_count']:
            source_digest = sha256_hex((payload.get('svg_content') or '').encode())
            if (payload.get('inflight') is not None or not payload.get('svg_content')
                    or row.author_status != 'completed' or row.render_status != 'completed'
                    or (payload.get('quality') or {}).get('passed') is not True or _read_png(payload) is None
                    or (row.slide_index in previous_caches and
                        source_digest != previous_caches[row.slide_index]['source_digest'])):
                _reject('svg_timeout_repurchase_completed_cache_missing')
            if quality and row.slide_index == target_index:
                from services.svg_presentation_checkpoint import _provider_attempt_count
                if ((payload.get('quality') or {}).get('source_digest') != 'sha256:' + source_digest
                        or _provider_attempt_count(payload) != stage['target_provider_attempt_count']):
                    _reject('svg_timeout_repurchase_completed_cache_missing')
            item.update(source_digest=source_digest, author_digest=_digest(payload.get('attempts') or []),
                png_digest=payload.get('png_sha256'))
            completed.append(item)
        elif row.slide_index == target_index:
            if (payload.get('inflight') != 1 or payload.get('svg_content') or payload.get('attempts')
                    or payload.get('provider_failures') or payload.get('recovery_revision')
                    or len(payload.get('provider_diagnostics') or []) != 1
                    or row.author_status != 'running' or row.render_status != 'pending'):
                _reject()
            item.update(consumed_attempt_count=1, previous_ordinal=1, next_provider_ordinal=2,
                max_additional_author_calls=1, logical_call_ids=[str(unknown_calls[0].id)],
                remote_outcome='unknown', previous_source_digest=None)
            unknown.append(item)
        else:
            if (payload.get('svg_content') or payload.get('inflight') is not None or payload.get('attempts')
                    or payload.get('provider_failures') or payload.get('recovery_revision') or payload.get('png_sha256')
                    or row.author_status != 'pending' or row.render_status != 'pending'):
                _reject()
            item.update(consumed_attempt_count=0, next_provider_ordinal=1, author_attempt_budget_per_page=2)
            unstarted.append(item)
    manifest = {**deepcopy(parent), 'bounded_continuation': not quality, 'previous_approval_manifest': parent,
        'approval_source_hash': parent_hash, 'operation_id': str(operation.id),
        'operation_version': operation.version, 'operation_fence': operation.fencing_token,
        'operation_attempt': operation.attempt_number, 'operation_attempt_token_digest': _digest(operation.attempt_token),
        'operation_request_digest': _digest(operation.request_payload), 'operation_error_digest': _digest(operation.error_detail),
        'job_attempt': job.attempt_number, 'job_attempt_token_digest': _digest(job.attempt_token),
        'completed_checkpoints': completed, 'returned_checkpoints': [], 'unknown_checkpoints': unknown,
        'unstarted_checkpoints': unstarted, 'old_admission': admission.model_dump(mode='json'),
        'old_logical_calls': [row.model_dump(mode='json') for row in current_calls],
        'historical_operation_ids': sorted(expected_ids),
        'historical_admissions': [row.model_dump(mode='json') for row in admissions],
        'historical_logical_calls': [row.model_dump(mode='json') for row in calls],
        'old_usage_receipts': [row.model_dump(mode='json') for row in usage],
        'old_resource_leases': [row.model_dump(mode='json') for row in leases],
        'old_runtime_budgets': [row.model_dump(mode='json') for row in budgets],
        'old_runtime_reservations': [row.model_dump(mode='json') for row in reservations],
        **stage['terms'],
        'new_author_provider': get_llm_provider().value, 'new_author_model': authoring_model(get_model())}
    if quality:
        manifest['bounded_quality_continuation'] = True
    if not bounded_completion_shape(manifest):
        _reject()
    return TimeoutRepurchaseRecovery([] if quality else [rows[target_index]], manifest)


async def _build_bounded_completion_manifest(session, operation, *, presentation_revision, lock=True):
    """Prepare exact unknown-outcome consent; preparation never purchases or resets."""
    from models.sql.workspace import WorkspaceOperation
    from models.sql.provider_usage_receipt import ProviderUsageReceipt
    from services.svg_presentation_checkpoint import _read_png
    from utils.llm_provider import get_llm_provider, get_model
    from utils.llm_calls.author_prompt_profile import authoring_model

    def locked(statement):
        return statement.with_for_update() if lock else statement
    registry = incident_registry()
    stage = registry['stages']['bounded_completion']
    total_slides = registry['total_slides']
    target_indexes = stage['target_slide_indexes']
    returned_index = stage['returned_slide_index']
    cache_proof = stage['terms']['returned_cache_proof']
    now = get_current_utc_datetime()
    if (str(operation.id) != stage['operation_id']
            or operation.kind != 'presentation.generate' or operation.state != 'failed'
            or operation.error_code != 'svg_provider_attempt_uncertain' or _live(operation.lease_expires_at, now)):
        _reject()
    parent = deepcopy(operation.request_payload.get('svg_timeout_repurchase_manifest') or {})
    parent_hash = operation.request_payload.get('svg_timeout_repurchase_manifest_hash')
    if (not _bounded_overload_shape(parent) or _digest(parent) != parent_hash
            or parent.get('operation_id') != str(operation.retry_of_operation_id)
            or parent.get('presentation_id') != str(operation.presentation_id)
            or parent.get('owner_id') != str(operation.owner_id)
            or parent.get('presentation_revision') != presentation_revision):
        _reject()
    job = await session.scalar(locked(select(PresentationGenerationJob).where(
        PresentationGenerationJob.workspace_operation_id == operation.id,
        PresentationGenerationJob.id == UUID(parent['job_id']))))
    admission = await session.scalar(locked(select(PublicOperationAdmission).where(
        PublicOperationAdmission.operation_id == operation.id)))
    if (job is None or admission is None or job.state != 'failed' or job.total_slides != total_slides
            or job.completed_slides != stage['completed_slides'] or _live(job.lease_expires_at, now)
            or admission.state != 'failed' or admission.terminal_at is None
            or admission.owner_id != operation.owner_id or admission.tenant_id != parent['tenant_id']
            or admission.initial_request_count + admission.retry_request_count != stage['admission_request_count']
            or _digest(job.request_payload) != parent['frozen_request_digest']
            or _digest(job.svg_style_snapshot) != parent['frozen_style_digest']):
        _reject()
    operations = list(await session.scalars(locked(select(WorkspaceOperation).where(
        WorkspaceOperation.presentation_id == operation.presentation_id,
        WorkspaceOperation.kind == 'presentation.generate').order_by(WorkspaceOperation.id))))
    expected_ids = set(stage['historical_operation_ids']) | {str(operation.id)}
    if {str(row.id) for row in operations} != expected_ids:
        _reject()
    admissions = list(await session.scalars(locked(select(PublicOperationAdmission).where(
        PublicOperationAdmission.operation_id.in_([row.id for row in operations])).order_by(PublicOperationAdmission.id))))
    if (len(admissions) != stage['admissions']
            or sum(row.initial_request_count + row.retry_request_count for row in admissions) != stage['historical_request_count']
            or any(row.owner_id != operation.owner_id or row.tenant_id != admission.tenant_id
                or row.state not in {'failed', 'deadline_exceeded'} or row.terminal_at is None
                or row.estimated_reserved_microunits or row.actual_reserved_microunits for row in admissions)):
        _reject('svg_timeout_repurchase_active_call')
    calls = list(await session.scalars(locked(select(PublicProviderLogicalCall).where(
        PublicProviderLogicalCall.admission_id.in_([row.id for row in admissions])).order_by(PublicProviderLogicalCall.id))))
    if (len(calls) != stage['logical_calls'] or any(row.stage not in {'author', 'repair'} or row.reconciled_at is None
            or row.outcome not in {'succeeded', 'failed', 'cancelled'} or row.state == 'reserved'
            or not _pinned_author(registry, row.provider_id, row.model_id) for row in calls)):
        _reject('svg_timeout_repurchase_active_call')
    current_calls = {row.slide_index: row for row in calls if row.admission_id == admission.id}
    current_specs = {spec['slide_index']: spec for spec in stage['current_calls']}
    if (set(current_calls) != set(current_specs)
            or any(current_calls[index].stage != current_specs[index]['stage']
                or current_calls[index].outcome != current_specs[index]['outcome']
                or current_calls[index].state != current_specs[index]['state'] for index in current_calls)):
        _reject()
    leases = list(await session.scalars(locked(select(PublicResourceLease).where(
        PublicResourceLease.admission_id.in_([row.id for row in admissions])).order_by(PublicResourceLease.id))))
    budgets = list(await session.scalars(locked(select(ProviderRuntimeBudget).where(
        ProviderRuntimeBudget.job_id == job.id).order_by(ProviderRuntimeBudget.id))))
    reservations = list(await session.scalars(locked(select(ProviderRuntimeReservation).join(
        ProviderRuntimeBudget, ProviderRuntimeBudget.id == ProviderRuntimeReservation.budget_id).where(
        ProviderRuntimeBudget.job_id == job.id).order_by(ProviderRuntimeReservation.id))))
    if any(row.state not in {'released', 'cancelled', 'expired'} for row in leases):
        _reject('svg_timeout_repurchase_active_lease')
    if any(row.estimated_reserved for row in budgets) or any(row.state in {'reserved', 'uncertain'} for row in reservations):
        _reject('svg_timeout_repurchase_active_call')
    usage = list(await session.scalars(locked(select(ProviderUsageReceipt).where(
        ProviderUsageReceipt.job_id == job.id).order_by(ProviderUsageReceipt.id))))
    if (sum(row.request_count for row in usage) != stage['usage_requests']
            or any(not _pinned_author(registry, row.provider, row.model) for row in usage)):
        _reject()
    rows = list(await session.scalars(locked(select(AuthoredSlideCheckpoint).where(
        AuthoredSlideCheckpoint.job_id == job.id).order_by(AuthoredSlideCheckpoint.slide_index))))
    if len(rows) != total_slides or [row.slide_index for row in rows] != list(range(total_slides)):
        _reject()
    previous_targets = {item['slide_index']: item for item in parent['unknown_checkpoints']}
    previous_completed = {item['slide_index']: item for item in parent['completed_checkpoints']}
    completed, returned, unknown, unstarted = [], [], [], []
    for row in rows:
        payload = row.svg_checkpoint or {}
        item = {'id': str(row.id), 'slide_index': row.slide_index,
            'row_digest': _digest(row.model_dump(mode='json')), 'input_digest': payload.get('input_digest')}
        if row.slide_index in stage['completed_slide_indexes']:
            previous = previous_completed[row.slide_index]
            if (payload.get('inflight') is not None or payload.get('provider_failures')
                    or sha256_hex((payload.get('svg_content') or '').encode()) != previous['source_digest']
                    or _digest(payload.get('attempts') or []) != previous['author_digest']
                    or payload.get('png_sha256') != previous['png_digest']
                    or (payload.get('quality') or {}).get('passed') is not True or _read_png(payload) is None):
                _reject('svg_timeout_repurchase_completed_cache_missing')
            item.update(source_digest=previous['source_digest'], author_digest=previous['author_digest'], png_digest=previous['png_digest'])
            completed.append(item)
        elif row.slide_index in target_indexes:
            previous = previous_targets[row.slide_index]
            fence = payload.get('retained_provider_attempt_fence') or {}
            if (payload.get('inflight') != stage['target_inflight_ordinal'] or payload.get('svg_content') or payload.get('attempts')
                    or payload.get('provider_failures') or payload.get('provider_diagnostics')
                    or payload.get('recovery_revision') != SVG_TIMEOUT_REPURCHASE_V3
                    or payload.get('recovery_operation_id') != str(operation.id)
                    or payload.get('input_digest') != previous['input_digest']
                    or fence.get('approval_manifest_hash') != parent_hash
                    or fence.get('consumed_attempt_count') != stage['target_retained_attempt_count']
                    or fence.get('previous_ordinal') != stage['target_retained_attempt_count']
                    or fence.get('logical_call_ids') != previous['logical_call_ids']
                    or _digest(payload.get('previous_checkpoint_row') or {}) != previous['row_digest']):
                _reject()
            consumed = stage['target_consumed_attempt_count']
            item.update(consumed_attempt_count=consumed, previous_ordinal=consumed, next_provider_ordinal=consumed + 1,
                max_additional_author_calls=1, logical_call_ids=[str(current_calls[row.slide_index].id)],
                remote_outcome='unknown', previous_source_digest=None)
            unknown.append(item)
        elif row.slide_index == returned_index:
            attempts = payload.get('attempts') or []
            receipt = attempts[0].get('receipt') or {} if len(attempts) == 1 else {}
            if (payload.get('inflight') is not None or not payload.get('svg_content') or len(attempts) != 1
                    or attempts[0].get('kind') != 'provider' or not attempts[0].get('raw_response')
                    or receipt.get('attempt_ordinal') != stage['returned_attempt_ordinal']
                    or receipt.get('requested_model') != registry['author_model']
                    or receipt.get('validation_error') is not None
                    or receipt.get('raw_digest') != 'sha256:' + sha256_hex(attempts[0]['raw_response'].encode())
                    or payload.get('provider_failures') or payload.get('quality') or payload.get('png_sha256')
                    or row.author_status != 'completed' or row.render_status != 'pending'):
                _reject()
            consumed = stage['returned_consumed_attempt_count']
            item.update(source_digest=sha256_hex(payload['svg_content'].encode()), author_digest=_digest(attempts),
                consumed_attempt_count=consumed, previous_ordinal=consumed, max_additional_author_calls=0)
            if (item['source_digest'] != cache_proof['candidate_digest']
                    or item['input_digest'] != cache_proof['input_digest']):
                _reject()
            returned.append(item)
        else:
            if (payload.get('svg_content') or payload.get('inflight') is not None or payload.get('attempts')
                    or payload.get('provider_failures') or payload.get('recovery_revision') or payload.get('png_sha256')
                    or row.author_status != 'pending' or row.render_status != 'pending'):
                _reject()
            item.update(consumed_attempt_count=0, next_provider_ordinal=1, author_attempt_budget_per_page=2)
            unstarted.append(item)
    manifest = {**deepcopy(parent), 'bounded_completion_recovery': True, 'bounded_overload_exception': False,
        'zero_dispatch_replay': False, 'previous_approval_manifest': parent, 'approval_source_hash': parent_hash,
        'operation_id': str(operation.id), 'operation_version': operation.version,
        'operation_fence': operation.fencing_token, 'operation_attempt': operation.attempt_number,
        'operation_attempt_token_digest': _digest(operation.attempt_token),
        'operation_request_digest': _digest(operation.request_payload), 'operation_error_digest': _digest(operation.error_detail),
        'job_attempt': job.attempt_number, 'job_attempt_token_digest': _digest(job.attempt_token),
        'completed_checkpoints': completed, 'returned_checkpoints': returned, 'unknown_checkpoints': unknown,
        'unstarted_checkpoints': unstarted, 'old_admission': admission.model_dump(mode='json'),
        'old_logical_calls': [row.model_dump(mode='json') for row in current_calls.values()],
        'historical_operation_ids': sorted(expected_ids),
        'historical_admissions': [row.model_dump(mode='json') for row in admissions],
        'historical_logical_calls': [row.model_dump(mode='json') for row in calls],
        'old_usage_receipts': [row.model_dump(mode='json') for row in usage],
        'old_resource_leases': [row.model_dump(mode='json') for row in leases],
        'old_runtime_budgets': [row.model_dump(mode='json') for row in budgets],
        'old_runtime_reservations': [row.model_dump(mode='json') for row in reservations],
        **stage['terms'],
        'new_author_provider': get_llm_provider().value, 'new_author_model': authoring_model(get_model())}
    if not bounded_completion_shape(manifest):
        _reject()
    return TimeoutRepurchaseRecovery([row for row in rows if row.slide_index in {*target_indexes, returned_index}], manifest)


_STAGE_BUILDERS = {
    'bounded_quality_continuation': lambda session, operation, **kwargs: _build_bounded_continuation_manifest(
        session, operation, presentation_revision=kwargs['presentation_revision'], lock=kwargs['lock'],
        quality_continuation=True),
    'bounded_continuation': lambda session, operation, **kwargs: _build_bounded_continuation_manifest(
        session, operation, presentation_revision=kwargs['presentation_revision'], lock=kwargs['lock']),
    'bounded_completion': lambda session, operation, **kwargs: _build_bounded_completion_manifest(
        session, operation, presentation_revision=kwargs['presentation_revision'], lock=kwargs['lock']),
    'bounded_overload': lambda session, operation, **kwargs: _build_bounded_overload_manifest(
        session, operation, presentation_revision=kwargs['presentation_revision'], lock=kwargs['lock']),
    'zero_dispatch_batch_replay': lambda session, operation, **kwargs: _build_zero_dispatch_batch_replay(
        session, operation, **kwargs),
    'interrupted_batch': lambda session, operation, **kwargs: _build_interrupted_batch_manifest(
        session, operation, **kwargs),
}


def _route_matches(route, operation):
    if 'operation_id' in route:
        return str(operation.id) == route['operation_id']
    if 'error_code' in route:
        return operation.error_code == route['error_code']
    if 'request_recovery_revision' in route:
        return operation.request_payload.get('recovery_revision') == route['request_recovery_revision']
    return True


async def build_incident_manifest(session, operation, *, presentation_revision, lock, recovery_revision):
    """Route a revision-three repurchase to the registered incident stage."""
    route = next(route for route in incident_registry()['dispatch'] if _route_matches(route, operation))
    return await _STAGE_BUILDERS[route['stage']](session, operation, presentation_revision=presentation_revision,
                                                 lock=lock, recovery_revision=recovery_revision)


def classification_repair_hint(previous_source):
    """The frozen repair instruction of the revision-two classification recovery."""
    return _stage('classification_mixed_recovery')['repair_hint_prefix'] + (previous_source or '')


async def completion_operation_manifest(session, operation, admission=None, *, lock=True):
    from models.sql.workspace import WorkspaceOperation
    manifest = operation.request_payload.get('svg_timeout_repurchase_manifest') or {}
    if (not bounded_completion_shape(manifest)
            or _digest(manifest) != operation.request_payload.get('svg_timeout_repurchase_manifest_hash')
            or manifest['operation_id'] != str(operation.retry_of_operation_id)
            or manifest['presentation_id'] != str(operation.presentation_id)
            or manifest['owner_id'] != str(operation.owner_id)
            or operation.max_attempts != 1 or operation.attempt_number != 1
            or operation.request_payload.get('recovery_revision') != SVG_TIMEOUT_REPURCHASE_V3
            or str(operation.request_payload.get('job_id')) != manifest['job_id']
            or (admission is not None and (admission.owner_id != operation.owner_id
                or admission.tenant_id != manifest['tenant_id'] or admission.operation_id != operation.id))):
        _reject('svg_timeout_repurchase_provider_target_not_approved')
    job = await session.get(PresentationGenerationJob, UUID(manifest['job_id']))
    if (job is None or job.workspace_operation_id != operation.id or job.attempt_number != 1
            or _digest(job.request_payload) != manifest['frozen_request_digest']
            or _digest(job.svg_style_snapshot) != manifest['frozen_style_digest']):
        _reject('svg_timeout_repurchase_provider_target_not_approved')
    if manifest.get('bounded_continuation') or manifest.get('bounded_quality_continuation'):
        source = await session.get(WorkspaceOperation, operation.retry_of_operation_id)
        if (source is None or source.owner_id != operation.owner_id or source.state != 'failed'
                or source.error_code != ('svg_validation_failed' if manifest.get('bounded_quality_continuation')
                                         else 'public_provider_result_unknown')
                or source.version != manifest['operation_version']
                or source.fencing_token != manifest['operation_fence']
                or source.attempt_number != manifest['operation_attempt']
                or _digest(source.attempt_token) != manifest['operation_attempt_token_digest']
                or _digest(source.request_payload) != manifest['operation_request_digest']
                or _digest(source.error_detail) != manifest['operation_error_digest']):
            _reject('svg_timeout_repurchase_manifest_conflict')
    def locked(statement):
        return statement.with_for_update() if lock else statement
    locked_admission = await session.scalar(locked(select(PublicOperationAdmission).where(
        PublicOperationAdmission.operation_id == operation.id)))
    if (locked_admission is None or locked_admission.owner_id != operation.owner_id
            or locked_admission.tenant_id != manifest['tenant_id']
            or (admission is not None and locked_admission.id != admission.id)):
        _reject('svg_timeout_repurchase_provider_target_not_approved')
    admissions = list(await session.scalars(locked(select(PublicOperationAdmission).where(
        PublicOperationAdmission.id.in_([UUID(row['id']) for row in manifest['historical_admissions']]))
        .order_by(PublicOperationAdmission.id))))
    calls = list(await session.scalars(locked(select(PublicProviderLogicalCall).where(
        PublicProviderLogicalCall.admission_id.in_([row.id for row in admissions]))
        .order_by(PublicProviderLogicalCall.id))))
    if (_digest([row.model_dump(mode='json') for row in admissions]) != _digest(manifest['historical_admissions'])
            or _digest([row.model_dump(mode='json') for row in calls]) != _digest(manifest['historical_logical_calls'])):
        _reject('svg_timeout_repurchase_manifest_conflict')
    operation_ids = set(await session.scalars(select(WorkspaceOperation.id).where(
        WorkspaceOperation.presentation_id == operation.presentation_id,
        WorkspaceOperation.kind == 'presentation.generate')))
    if {str(value) for value in operation_ids} != set(manifest['historical_operation_ids']) | {str(operation.id)}:
        _reject('svg_timeout_repurchase_manifest_conflict')
    return manifest, job


async def sample_completion_admission_deadline(session, operation, *, owner_id, tenant_id,
        policy, now, deadline, auth_expires_at=None, server_hard_deadline_at=None):
    """Pin the signed sample's serial budget inside unchanged authority ceilings."""
    from datetime import timedelta
    from models.sql.workspace import WorkspaceOperation
    from services.public_operation_deadline import _utc
    manifest = operation.request_payload.get('svg_timeout_repurchase_manifest') or {}
    if not manifest.get('bounded_completion_recovery'):
        return deadline
    if (not bounded_completion_shape(manifest)
            or _digest(manifest) != operation.request_payload.get('svg_timeout_repurchase_manifest_hash')
            or operation.request_payload.get('recovery_revision') != SVG_TIMEOUT_REPURCHASE_V3
            or operation.owner_id != owner_id or str(owner_id) != manifest['owner_id']
            or tenant_id != manifest['tenant_id']
            or str(operation.retry_of_operation_id) != manifest['operation_id']
            or str(operation.presentation_id) != manifest['presentation_id']
            or str(operation.request_payload.get('job_id')) != manifest['job_id']
            or operation.max_attempts != 1 or operation.attempt_number != 0):
        _reject('svg_timeout_repurchase_provider_target_not_approved')
    job = await session.get(PresentationGenerationJob, UUID(manifest['job_id']))
    source = await session.get(WorkspaceOperation, operation.retry_of_operation_id)
    existing = await session.scalar(select(PublicOperationAdmission).where(
        PublicOperationAdmission.operation_id == operation.id))
    if (job is None or source is None or existing is not None
            or job.workspace_operation_id != operation.id or job.attempt_number != 0
            or job.state != 'queued' or _digest(job.request_payload) != manifest['frozen_request_digest']
            or _digest(job.svg_style_snapshot) != manifest['frozen_style_digest']
            or source.owner_id != owner_id or source.state != 'failed'
            or source.error_code != ('svg_validation_failed' if manifest.get('bounded_quality_continuation')
                else 'public_provider_result_unknown' if manifest.get('bounded_continuation')
                else 'svg_provider_attempt_uncertain')
            or source.version != manifest['operation_version']
            or _digest(source.request_payload) != manifest['operation_request_digest']
            or ((manifest.get('bounded_continuation') or manifest.get('bounded_quality_continuation')) and
                (source.fencing_token != manifest['operation_fence']
                 or source.attempt_number != manifest['operation_attempt']
                 or _digest(source.attempt_token) != manifest['operation_attempt_token_digest']
                 or _digest(source.error_detail) != manifest['operation_error_digest']))):
        _reject('svg_timeout_repurchase_manifest_conflict')
    plan = manifest['serial_execution_plan']
    seconds = (plan['provider_purchases'] * plan['sdk_request_seconds']
        + plan['render_passes'] * plan['render_guard_seconds']
        + plan['cooldown_slots'] * plan['cooldown_seconds'] + plan['setup_reserve_seconds'])
    if seconds != plan['required_duration_seconds'] or seconds > policy.generation_deadline_seconds:
        _reject('svg_timeout_repurchase_serial_deadline_insufficient')
    target = _utc(now, 'admission_now') + timedelta(seconds=seconds)
    if any(_utc(bound, 'authority_deadline') < target
           for bound in (auth_expires_at, server_hard_deadline_at) if bound is not None):
        _reject('svg_timeout_repurchase_serial_deadline_insufficient')
    pinned = max(_utc(deadline, 'admission_deadline'), target)
    upper_bounds = [_utc(now, 'admission_now') + timedelta(seconds=policy.generation_deadline_seconds)]
    upper_bounds.extend(_utc(bound, 'authority_deadline')
        for bound in (auth_expires_at, server_hard_deadline_at) if bound is not None)
    if pinned > min(upper_bounds):
        _reject('svg_timeout_repurchase_serial_deadline_insufficient')
    return pinned


async def _verify_interrupted_batch_provider_target(session, operation, admission, *, stage, slide_index, logical_call_key, provider_id, model_id):
    if operation.request_payload.get('recovery_revision') != SVG_TIMEOUT_REPURCHASE_V3:
        return
    manifest = operation.request_payload.get('svg_timeout_repurchase_manifest') or {}
    if manifest.get('recovery_revision') != SVG_TIMEOUT_REPURCHASE_V3 or _digest(manifest) != operation.request_payload.get('svg_timeout_repurchase_manifest_hash') or manifest.get('owner_id') != str(operation.owner_id) or (manifest.get('owner_id') != str(admission.owner_id)) or (manifest.get('tenant_id') != admission.tenant_id) or (manifest.get('operation_id') != str(operation.retry_of_operation_id)) or (manifest.get('presentation_id') != str(operation.presentation_id)) or (str(operation.request_payload.get('job_id')) != manifest.get('job_id')) or (type(manifest.get('max_additional_author_calls')) is not int) or (manifest.get('max_additional_author_calls', 0) < 1) or (manifest.get('max_additional_author_calls') != len(manifest.get('unknown_checkpoints', []))) or (manifest.get('max_additional_vqa_calls') != 1):
        _reject('svg_timeout_repurchase_provider_target_not_approved')
    job = await session.get(PresentationGenerationJob, UUID(manifest['job_id']))
    if job is None or job.workspace_operation_id != operation.id or _digest(job.request_payload) != manifest.get('frozen_request_digest') or (_digest(job.svg_style_snapshot) != manifest.get('frozen_style_digest')):
        _reject('svg_timeout_repurchase_provider_target_not_approved')
    suffix = f'.resume-{job.attempt_number}' if job.attempt_number > 1 else ''
    bounded_overload = manifest.get('bounded_overload_exception') is True
    if (bounded_overload and not _bounded_overload_shape(manifest)) or (not bounded_overload and not _terms_match(manifest, {
            key: _stage('interrupted_batch')['terms'][key]
            for key in ('total_author_call_limit', 'previous_consumed_author_calls', 'remaining_author_call_limit')})):
        _reject('svg_timeout_repurchase_provider_target_not_approved')
    if stage in {'author', 'repair'}:
        groups = [(name, item) for name in ('unknown_checkpoints', 'unstarted_checkpoints', 'returned_checkpoints') for item in manifest.get(name, []) if item.get('slide_index') == slide_index]
        if len(groups) != 1 or provider_id != manifest['new_author_provider'] or model_id != manifest['new_author_model']:
            _reject('svg_timeout_repurchase_provider_target_not_approved')
        group, target = groups[0]
        existing = list((await session.scalars(select(PublicProviderLogicalCall).where(PublicProviderLogicalCall.admission_id == admission.id, PublicProviderLogicalCall.stage.in_(['author', 'repair'])).with_for_update())).all())
        if len(existing) >= manifest['remaining_author_call_limit']:
            _reject('svg_timeout_repurchase_purchase_already_consumed')
        page_calls = [call for call in existing if call.slide_index == slide_index]
        if group == 'unknown_checkpoints':
            ordinal = target['next_provider_ordinal'] if bounded_overload else 2
            if stage != 'repair' or page_calls or target.get('consumed_attempt_count') != (ordinal - 1) or (target.get('max_additional_author_calls') != 1):
                _reject('svg_timeout_repurchase_purchase_already_consumed')
        elif group == 'returned_checkpoints':
            ordinal = 2
            if stage != 'repair' or page_calls or target.get('consumed_attempt_count') != 1:
                _reject('svg_timeout_repurchase_purchase_already_consumed')
        else:
            ordinal = len(page_calls) + 1
            if target.get('consumed_attempt_count') != 0 or len(page_calls) != ordinal - 1:
                _reject('svg_timeout_repurchase_purchase_already_consumed')
            if ordinal not in {1, 2} or (ordinal == 1 and stage != 'author'):
                _reject('svg_timeout_repurchase_provider_target_not_approved')
            if ordinal == 2 and (page_calls[0].stage != 'author' or page_calls[0].outcome not in {'succeeded', 'failed'} or page_calls[0].reconciled_at is None):
                _reject('svg_timeout_repurchase_provider_target_not_approved')
            if ordinal == 2 and stage != ('repair' if page_calls[0].outcome == 'succeeded' else 'author'):
                _reject('svg_timeout_repurchase_provider_target_not_approved')
        if logical_call_key != f'svg-author-{slide_index}-{ordinal}{suffix}':
            _reject('svg_timeout_repurchase_provider_target_not_approved')
    elif stage == 'vqa':
        if slide_index not in {None, -1} or logical_call_key != f'authored.vision-advisory{suffix}':
            _reject('svg_timeout_repurchase_provider_target_not_approved')
        existing_vqa = await session.scalar(select(PublicProviderLogicalCall).where(
            PublicProviderLogicalCall.admission_id == admission.id,
            PublicProviderLogicalCall.stage == 'vqa').with_for_update().limit(1))
        if existing_vqa is not None:
            _reject('svg_timeout_repurchase_purchase_already_consumed')
    else:
        _reject('svg_timeout_repurchase_provider_target_not_approved')


async def verify_incident_provider_target(session, operation, admission, *, stage, slide_index,
                                          logical_call_key, provider_id, model_id):
    """Pre-SDK refusal for revision-three purchases of the registered incident."""
    if (operation.request_payload.get('svg_timeout_repurchase_manifest') or {}).get('bounded_completion_recovery'):
        return await _verify_bounded_completion_provider_target(session, operation, admission,
            stage=stage, slide_index=slide_index, logical_call_key=logical_call_key,
            provider_id=provider_id, model_id=model_id)
    return await _verify_interrupted_batch_provider_target(session, operation, admission, stage=stage, slide_index=slide_index, logical_call_key=logical_call_key, provider_id=provider_id, model_id=model_id)


def reset_incident_checkpoints(session, recovery, *, operation_id, now):
    """Archive the incident's cached witness; return the rows left to reset, or None."""
    if recovery.manifest.get('bounded_quality_continuation'):
        if not _bounded_quality_continuation_shape(recovery.manifest) or recovery.rows:
            _reject()
        return None
    if recovery.manifest.get('bounded_continuation'):
        continuation = _stage('bounded_continuation')
        if (not _bounded_continuation_shape(recovery.manifest) or len(recovery.rows) != 1
                or recovery.rows[0].slide_index != continuation['target_slide_index']
                or _digest(recovery.rows[0].model_dump(mode='json'))
                    != recovery.manifest['unknown_checkpoints'][0]['row_digest']):
            _reject()
    if recovery.manifest.get('bounded_completion_recovery') and not recovery.manifest.get('bounded_continuation'):
        completion = _stage('bounded_completion')
        targets = set(completion['target_slide_indexes'])
        returned_index = completion['returned_slide_index']
        if (not bounded_completion_shape(recovery.manifest)
                or {row.slide_index for row in recovery.rows} != targets | {returned_index}):
            _reject()
        cached = next(row for row in recovery.rows if row.slide_index == returned_index)
        witness = recovery.manifest['returned_checkpoints'][0]
        archive = cached.model_dump(mode='json')
        payload = deepcopy(cached.svg_checkpoint or {})
        if (_digest(archive) != witness['row_digest']
                or _digest(payload.get('attempts', [])) != witness['author_digest']
                or sha256_hex(payload.get('svg_content', '').encode('utf-8')) != witness['source_digest']):
            _reject()
        payload['completion_cache_only'] = True
        payload['completion_cache_archive'] = deepcopy(archive)
        payload['recovery_operation_id'] = str(operation_id)
        payload['timeout_repurchase_approval_manifest'] = deepcopy(recovery.manifest)
        payload['retained_provider_attempt_fence']['approval_manifest_hash'] = recovery.manifest_hash
        cached.svg_checkpoint = payload
        cached.updated_at = now
        session.add(cached)
        recovery = TimeoutRepurchaseRecovery(
            [row for row in recovery.rows if row.slide_index in targets], recovery.manifest)
    if recovery.manifest.get("zero_dispatch_replay"):
        targets = {item["id"]: item for item in recovery.manifest["unknown_checkpoints"]}
        if (len(recovery.rows) != _stage('interrupted_batch')['layout']['unknown']
                or {str(row.id) for row in recovery.rows} != set(targets)):
            _reject()
        for row in recovery.rows:
            payload = deepcopy(row.svg_checkpoint or {})
            fence = deepcopy(payload.get("retained_provider_attempt_fence") or {})
            if (fence.get("consumed_attempt_count") != 1 or payload.get("inflight") is not None
                    or payload.get("svg_content") or payload.get("attempts") or payload.get("provider_failures")):
                _reject()
            payload["recovery_operation_id"] = str(operation_id)
            payload["timeout_repurchase_approval_manifest"] = deepcopy(recovery.manifest)
            fence["approval_manifest_hash"] = recovery.manifest_hash
            payload["retained_provider_attempt_fence"] = fence
            row.svg_checkpoint = payload
            row.updated_at = now
        return None
    return recovery


async def _verify_bounded_completion_provider_target(
    session, operation, admission, *, stage, slide_index, logical_call_key,
    provider_id, model_id,
):
    """Fence the signed completion purchase pools across all admitted stages."""
    manifest, job = await completion_operation_manifest(session, operation, admission)
    if manifest.get('bounded_continuation') or manifest.get('bounded_quality_continuation'):
        return await _verify_bounded_continuation_provider_target(session, operation, admission,
            manifest=manifest, job=job, stage=stage, slide_index=slide_index,
            logical_call_key=logical_call_key, provider_id=provider_id, model_id=model_id)
    registry = incident_registry()
    completion = registry['stages']['bounded_completion']
    terms = completion['terms']
    target_indexes = completion['target_slide_indexes']
    unstarted_indexes = completion['unstarted_slide_indexes']
    approved_indexes = (*target_indexes, *unstarted_indexes)
    if not _pinned_author(registry, provider_id, model_id):
        _reject('svg_timeout_repurchase_provider_target_not_approved')
    existing = list(await session.scalars(select(PublicProviderLogicalCall).where(
        PublicProviderLogicalCall.admission_id == admission.id).with_for_update()))
    author_calls = [call for call in existing if call.stage in {'author', 'repair'}]
    vqa_calls = [call for call in existing if call.stage == 'vqa']
    if (len(author_calls) + len(vqa_calls) != len(existing)
            or any(not _pinned_author(registry, call.provider_id, call.model_id)
                   for call in existing)
            or any(call.slide_index not in approved_indexes
                   for call in author_calls)
            or any(call.slide_index != -1 for call in vqa_calls)):
        _reject('svg_timeout_repurchase_provider_target_not_approved')
    page_calls = {index: [call for call in author_calls if call.slide_index == index]
                  for index in approved_indexes}
    if (any(len(page_calls[index]) > 1 or any(call.stage != 'repair'
                                            for call in page_calls[index])
            for index in target_indexes)
            or any(len(page_calls[index]) > 2 for index in unstarted_indexes)
            or len(vqa_calls) > 1):
        _reject('svg_timeout_repurchase_provider_target_not_approved')
    second_purchases = sum(max(0, len(page_calls[index]) - 1)
                           for index in unstarted_indexes)
    if (len(existing) >= terms['remaining_all_provider_call_limit']
            or len(author_calls) > terms['remaining_author_call_limit']
            or second_purchases > terms['max_normal_second_purchases']
            or any(call.outcome not in {'succeeded', 'failed'}
                   or call.reconciled_at is None
                   or call.state not in {'reported', 'unavailable'}
                   for call in existing)):
        _reject('svg_timeout_repurchase_purchase_already_consumed')
    suffix = f'.resume-{job.attempt_number}' if job.attempt_number > 1 else ''
    if stage == 'vqa':
        if (slide_index not in {None, -1}
                or logical_call_key != f'authored.vision-advisory{suffix}'):
            _reject('svg_timeout_repurchase_provider_target_not_approved')
        if vqa_calls:
            _reject('svg_timeout_repurchase_purchase_already_consumed')
        return
    if stage not in {'author', 'repair'} or slide_index not in page_calls:
        _reject('svg_timeout_repurchase_provider_target_not_approved')
    if len(author_calls) >= terms['remaining_author_call_limit']:
        _reject('svg_timeout_repurchase_purchase_already_consumed')
    purchases = page_calls[slide_index]
    if slide_index in target_indexes:
        consumed = completion['target_consumed_attempt_count']
        targets = [item for item in manifest.get('unknown_checkpoints', [])
                   if item.get('slide_index') == slide_index]
        if (len(targets) != 1 or stage != 'repair'
                or targets[0].get('consumed_attempt_count') != consumed
                or targets[0].get('next_provider_ordinal') != consumed + 1
                or targets[0].get('max_additional_author_calls') != 1):
            _reject('svg_timeout_repurchase_provider_target_not_approved')
        if purchases:
            _reject('svg_timeout_repurchase_purchase_already_consumed')
        ordinal = consumed + 1
    else:
        targets = [item for item in manifest.get('unstarted_checkpoints', [])
                   if item.get('slide_index') == slide_index]
        if len(targets) != 1 or targets[0].get('consumed_attempt_count') != 0:
            _reject('svg_timeout_repurchase_provider_target_not_approved')
        ordinal = len(purchases) + 1
        if ordinal == 1:
            if stage != 'author':
                _reject('svg_timeout_repurchase_provider_target_not_approved')
        elif ordinal == 2:
            prior = purchases[0]
            if second_purchases >= terms['max_normal_second_purchases']:
                _reject('svg_timeout_repurchase_purchase_already_consumed')
            if (prior.stage != 'author' or prior.outcome not in {'succeeded', 'failed'}
                    or prior.reconciled_at is None
                    or stage != ('repair' if prior.outcome == 'succeeded' else 'author')):
                _reject('svg_timeout_repurchase_provider_target_not_approved')
        else:
            _reject('svg_timeout_repurchase_purchase_already_consumed')
    if logical_call_key != f'svg-author-{slide_index}-{ordinal}{suffix}':
        _reject('svg_timeout_repurchase_provider_target_not_approved')


async def _verify_bounded_continuation_provider_target(session, operation, admission, *,
        manifest, job, stage, slide_index, logical_call_key, provider_id, model_id):
    """Spend only the five author/repair and one VQA calls left in total24."""
    registry = incident_registry()
    continuation = registry['stages']['bounded_continuation']
    target_index = continuation['target_slide_index']
    unstarted_indexes = continuation['unstarted_slide_indexes']
    if not _pinned_author(registry, provider_id, model_id):
        _reject('svg_timeout_repurchase_provider_target_not_approved')
    existing = list(await session.scalars(select(PublicProviderLogicalCall).where(
        PublicProviderLogicalCall.admission_id == admission.id).with_for_update()))
    author_calls = [call for call in existing if call.stage in {'author', 'repair'}]
    vqa_calls = [call for call in existing if call.stage == 'vqa']
    pages = {index: [call for call in author_calls if call.slide_index == index]
             for index in (target_index, *unstarted_indexes)}
    quality = manifest.get('bounded_quality_continuation') is True
    approved_pages = set(unstarted_indexes) if quality else pages
    if (len(author_calls) + len(vqa_calls) != len(existing)
            or any(not _pinned_author(registry, call.provider_id, call.model_id) for call in existing)
            or any(call.slide_index not in approved_pages for call in author_calls)
            or any(call.slide_index != -1 for call in vqa_calls)
            or len(pages[target_index]) > 1 or any(call.stage != 'repair' for call in pages[target_index])
            or any(len(pages[index]) > 2 for index in unstarted_indexes) or len(vqa_calls) > 1):
        _reject('svg_timeout_repurchase_provider_target_not_approved')
    second_purchases = _continuation_second_purchases(pages, continuation)
    if (len(existing) >= manifest['remaining_all_provider_call_limit']
            or len(author_calls) > manifest['remaining_author_call_limit']
            or second_purchases > manifest['max_normal_second_purchases']
            or any(call.outcome not in {'succeeded', 'failed'} or call.reconciled_at is None
                or call.state not in {'reported', 'unavailable'} for call in existing)):
        _reject('svg_timeout_repurchase_purchase_already_consumed')
    suffix = f'.resume-{job.attempt_number}' if job.attempt_number > 1 else ''
    if stage == 'vqa':
        if slide_index not in {None, -1} or logical_call_key != f'authored.vision-advisory{suffix}':
            _reject('svg_timeout_repurchase_provider_target_not_approved')
        if vqa_calls:
            _reject('svg_timeout_repurchase_purchase_already_consumed')
        return
    if stage not in {'author', 'repair'} or slide_index not in approved_pages:
        _reject('svg_timeout_repurchase_provider_target_not_approved')
    if len(author_calls) >= manifest['remaining_author_call_limit']:
        _reject('svg_timeout_repurchase_purchase_already_consumed')
    purchases = pages[slide_index]
    if slide_index == target_index:
        if stage != 'repair' or purchases:
            _reject('svg_timeout_repurchase_provider_target_not_approved')
        ordinal = continuation['target_consumed_attempt_count'] + 1
    else:
        ordinal = len(purchases) + 1
        if ordinal == 1:
            if stage != 'author':
                _reject('svg_timeout_repurchase_provider_target_not_approved')
        elif ordinal == 2:
            prior = purchases[0]
            if prior.stage != 'author' or stage != ('repair' if prior.outcome == 'succeeded' else 'author'):
                _reject('svg_timeout_repurchase_provider_target_not_approved')
        else:
            _reject('svg_timeout_repurchase_purchase_already_consumed')
    if ordinal == 2 and second_purchases >= manifest['max_normal_second_purchases']:
        _reject('svg_timeout_repurchase_purchase_already_consumed')
    if logical_call_key != f'svg-author-{slide_index}-{ordinal}{suffix}':
        _reject('svg_timeout_repurchase_provider_target_not_approved')


def bounded_completion_attempt_limit(payload, manifest, fence, *, index, job):
    """Attempt allowance of one incident checkpoint row.

    Returns ``(limit, extra_consumed_counts)``: a final limit for the archived
    returned witness, or the consumed counts the general fence check accepts.
    """
    from services import svg_presentation_checkpoint as checkpoint

    if not bounded_completion_shape(manifest):
        raise ValueError("svg_timeout_repurchase_attempt_fence_invalid")
    completion = _stage('bounded_completion')
    returned_index = completion['returned_slide_index']
    if index == returned_index and payload.get("completion_cache_only") is True:
        returned = manifest.get("returned_checkpoints", [])
        svg = payload.get("svg_content")
        if (len(returned) != 1 or returned[0].get("slide_index") != returned_index
                or manifest.get("recovery_revision") != payload.get("recovery_revision")
                or _digest(manifest) != fence.get("approval_manifest_hash")
                or job is None or str(job.id) != manifest.get("job_id")
                or str(job.workspace_operation_id) != payload.get("recovery_operation_id")
                or fence.get("slide_index") != returned_index
                or returned[0].get("input_digest") != payload.get("input_digest")
                or not isinstance(svg, str) or not svg
                or sha256_hex(svg.encode("utf-8")) != returned[0].get("source_digest")
                or _digest(payload.get("attempts", [])) != returned[0].get("author_digest")
                or fence.get("consumed_attempt_count") != completion['cache_only_fence_attempt_count']
                or fence.get("previous_ordinal") != completion['cache_only_fence_attempt_count']
                or payload.get("inflight") is not None
                or returned[0].get("max_additional_author_calls") != 0
                or checkpoint._provider_attempt_count(payload) != completion['cache_only_attempt_limit']):
            raise ValueError("svg_timeout_repurchase_attempt_fence_invalid")
        return completion['cache_only_attempt_limit'], set()
    if index in completion['target_slide_indexes']:
        return None, {completion['target_consumed_attempt_count']}
    return None, set()
