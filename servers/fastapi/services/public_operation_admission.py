"""Cluster-wide public operation admission and provider budget enforcement.

Only a verified :class:`PrincipalContext` may supply ownership.  This module's
inputs deliberately have no cookie, session, owner override, payload, prompt, or
provider-response fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import re
import uuid

from sqlalchemy import and_, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.sql.public_admission import (
    PublicOperationAdmission,
    PublicPolicyProfile,
    PublicProviderLogicalCall,
    PublicQuotaCounter,
)
from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import (
    get_current_owner_id,
    get_current_tenant_id,
    get_current_trace_id,
)


PUBLIC_DEFAULT_POLICY_VERSION = "public-default/v1"
PUBLIC_PROVIDER_STAGES = frozenset(
    {
        "outline",
        "content_brief",
        "deck_plan",
        "header",
        "author",
        "illustration",
        "repair",
        "vqa",
    }
)
from models.sql.workspace import WorkspaceOperation
from services.provider_pricing import (
    ProviderModelPricing,
    ProviderUsageCost,
    calculate_actual_usage_cost,
)
_TERMINAL_STATES = frozenset(
    {"succeeded", "failed", "cancelled", "budget_exhausted"}
)
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
@dataclass(frozen=True, slots=True)
class PrincipalContext:
    """Identity already verified by the external session trust boundary."""

    owner_id: uuid.UUID
    tenant_id: str
    trace_id: str


@dataclass(frozen=True, slots=True)
class ProviderRequestPlan:
    slide_count: int
    image_budget: int
    repair_budget: int
    vqa_budget: int

    @property
    def initial_request_limit(self) -> int:
        return (
            4  # content brief + outline + chapter/deck plan + header contract
            + self.slide_count
            + min(self.image_budget, self.slide_count)
            + self.repair_budget
            + self.vqa_budget
        )

    @property
    def retry_request_limit(self) -> int:
        """Reserve at most one bounded validation retry per authored slide."""

        return self.slide_count


class PublicAdmissionRejected(RuntimeError):
    def __init__(self, code: str, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after_seconds = retry_after_seconds
        self.retryable = code not in {
            "public_provider_request_limit_exceeded",
            "public_provider_retry_limit_exceeded",
            "public_provider_estimated_cost_limit_exceeded",
            "public_provider_actual_cost_limit_exceeded",
            "public_provider_inflight_replay_unsupported",
            "public_provider_terminal_replay_unsupported",
        }


@dataclass(frozen=True, slots=True)
class PublicProviderCallLease:
    logical_call_id: uuid.UUID
    admission_id: uuid.UUID
    provider_idempotency_key: str
    state: str
    reused: bool = False

    @property
    def is_terminal_reuse(self) -> bool:
        return self.reused and self.state != "reserved"

    @property
    def is_inflight_reuse(self) -> bool:
        return self.reused and self.state == "reserved"


def _logical_call_ordinal(
    *,
    operation_id: uuid.UUID,
    stage: str,
    slide_index: int,
    logical_call_ordinal: int | None,
    logical_call_key: str | None,
) -> int:
    if logical_call_key is None:
        if logical_call_ordinal is None or logical_call_ordinal < 1:
            raise ValueError("logical_call_ordinal_must_be_positive")
        return logical_call_ordinal
    if not _SAFE_ID_RE.fullmatch(logical_call_key):
        raise ValueError("public_provider_logical_call_key_invalid")
    digest = sha256(
        f"public-provider-slot/v1:{operation_id}:{stage}:{slide_index}:"
        f"{logical_call_key}".encode("utf-8")
    ).digest()
    # Positive PostgreSQL INTEGER; zero is invalid by the DB contract. The full
    # semantic identity still feeds the separately persisted SHA-256 key hash.
    return max(1, int.from_bytes(digest[:4], "big") & 0x7FFF_FFFF)


def current_principal_context() -> PrincipalContext:
    """Return only the server-established request principal context.

    The local self-hosted auth adapter derives ``default`` from server config;
    no owner or tenant value is accepted from a command body or query string.
    """

    owner_id = get_current_owner_id()
    if owner_id is None:
        raise PublicAdmissionRejected("verified_principal_required")
    tenant_id = get_current_tenant_id() or "default"
    trace_id = get_current_trace_id() or f"operation-{uuid.uuid4()}"
    return _principal(
        PrincipalContext(
            owner_id=owner_id,
            tenant_id=tenant_id,
            trace_id=trace_id,
        )
    )


def _nonnegative(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field}_must_be_nonnegative")
    return value


def _principal(principal: PrincipalContext) -> PrincipalContext:
    if not isinstance(principal.owner_id, uuid.UUID):
        raise ValueError("principal_owner_id_required")
    if not _SAFE_ID_RE.fullmatch(principal.tenant_id):
        raise ValueError("principal_tenant_id_invalid")
    if not _SAFE_ID_RE.fullmatch(principal.trace_id):
        raise ValueError("principal_trace_id_invalid")
    return principal


def plan_provider_requests(
    *,
    slide_count: int,
    image_budget: int,
    repair_budget: int,
    vqa_budget: int,
) -> ProviderRequestPlan:
    plan = ProviderRequestPlan(
        slide_count=_nonnegative(slide_count, "slide_count"),
        image_budget=_nonnegative(image_budget, "image_budget"),
        repair_budget=_nonnegative(repair_budget, "repair_budget"),
        vqa_budget=_nonnegative(vqa_budget, "vqa_budget"),
    )
    if plan.slide_count < 1:
        raise ValueError("slide_count_must_be_positive")
    return plan


async def load_public_policy(
    session: AsyncSession, version: str = PUBLIC_DEFAULT_POLICY_VERSION
) -> PublicPolicyProfile:
    row = await session.get(PublicPolicyProfile, version)
    if row is None or not row.enabled:
        raise PublicAdmissionRejected("public_policy_unavailable")
    return row


def _quota_limits(
    policy: PublicPolicyProfile, *, scope_type: str, operation_kind: str
) -> tuple[int, int]:
    if scope_type not in {"owner", "tenant"} or operation_kind not in {
        "generation",
        "export",
    }:
        raise ValueError("public_quota_scope_invalid")
    prefix = f"{scope_type}_"
    return (
        int(getattr(policy, f"{prefix}active_{operation_kind}")),
        int(getattr(policy, f"{prefix}queued_{operation_kind}")),
    )


async def _ensure_counter(
    session: AsyncSession,
    *,
    policy_version: str,
    scope_type: str,
    scope_key: str,
    operation_kind: str,
) -> None:
    existing = await session.scalar(
        select(PublicQuotaCounter).where(
            PublicQuotaCounter.policy_version == policy_version,
            PublicQuotaCounter.scope_type == scope_type,
            PublicQuotaCounter.scope_key == scope_key,
            PublicQuotaCounter.operation_kind == operation_kind,
        )
    )
    if existing is not None:
        return
    candidate = PublicQuotaCounter(
        policy_version=policy_version,
        scope_type=scope_type,
        scope_key=scope_key,
        operation_kind=operation_kind,
    )
    try:
        async with session.begin_nested():
            session.add(candidate)
            await session.flush()
    except IntegrityError:
        return


async def _locked_counters(
    session: AsyncSession,
    *,
    policy_version: str,
    owner_id: uuid.UUID,
    tenant_id: str,
    operation_kind: str,
) -> tuple[PublicQuotaCounter, PublicQuotaCounter]:
    identities = (("owner", str(owner_id)), ("tenant", tenant_id))
    for scope_type, scope_key in identities:
        await _ensure_counter(
            session,
            policy_version=policy_version,
            scope_type=scope_type,
            scope_key=scope_key,
            operation_kind=operation_kind,
        )
    rows = list(
        (
            await session.execute(
                select(PublicQuotaCounter)
                .where(
                    PublicQuotaCounter.policy_version == policy_version,
                    PublicQuotaCounter.operation_kind == operation_kind,
                    (
                        (
                            (PublicQuotaCounter.scope_type == "owner")
                            & (PublicQuotaCounter.scope_key == str(owner_id))
                        )
                        | (
                            (PublicQuotaCounter.scope_type == "tenant")
                            & (PublicQuotaCounter.scope_key == tenant_id)
                        )
                    ),
                )
                .order_by(PublicQuotaCounter.scope_type, PublicQuotaCounter.scope_key)
                .with_for_update()
            )
        ).scalars()
    )
    by_type = {row.scope_type: row for row in rows}
    if set(by_type) != {"owner", "tenant"}:
        raise RuntimeError("public_quota_counter_missing")
    return by_type["owner"], by_type["tenant"]


async def admit_public_operation(
    session: AsyncSession,
    *,
    principal: PrincipalContext,
    operation_id: uuid.UUID,
    operation_kind: str,
    request_plan: ProviderRequestPlan | None,
    policy_version: str = PUBLIC_DEFAULT_POLICY_VERSION,
    estimated_cost_limit_microunits: int | None = None,
    actual_cost_limit_microunits: int | None = None,
) -> PublicOperationAdmission:
    """Atomically bind principal/policy and consume one queued quota slot."""

    principal = _principal(principal)
    if operation_kind not in {"generation", "export"}:
        raise ValueError("public_operation_kind_invalid")
    policy = await load_public_policy(session, policy_version)
    if operation_kind == "generation":
        if request_plan is None:
            raise PublicAdmissionRejected("provider_request_plan_required")
        if request_plan.slide_count > policy.max_slides:
            raise PublicAdmissionRejected("public_slide_limit_exceeded")
        request_limit = request_plan.initial_request_limit
        # Planned author corrections and policy-bounded transient/schema
        # recovery are distinct finite allowances. Persist their sum so the
        # static policy cannot truncate a valid N-slide correction plan while
        # every physical retry still consumes one durable hard-cap slot.
        retry_limit = request_plan.retry_request_limit + policy.retry_request_limit
        estimated_limit = min(
            policy.estimated_cost_limit_microunits,
            estimated_cost_limit_microunits
            if estimated_cost_limit_microunits is not None
            else policy.estimated_cost_limit_microunits,
        )
        actual_limit = min(
            policy.actual_cost_limit_microunits,
            actual_cost_limit_microunits
            if actual_cost_limit_microunits is not None
            else policy.actual_cost_limit_microunits,
        )
        if estimated_limit <= 0 or actual_limit <= 0:
            raise PublicAdmissionRejected("provider_cost_cap_required")
        slide_count = request_plan.slide_count
    else:
        if request_plan is not None:
            raise ValueError("export_provider_request_plan_forbidden")
        request_limit = retry_limit = estimated_limit = actual_limit = slide_count = 0

    existing = await session.scalar(
        select(PublicOperationAdmission).where(
            PublicOperationAdmission.operation_id == operation_id
        )
    )
    if existing is not None:
        if (
            existing.owner_id != principal.owner_id
            or existing.tenant_id != principal.tenant_id
            or existing.operation_kind != operation_kind
            or existing.policy_version != policy_version
        ):
            raise PublicAdmissionRejected("public_operation_identity_conflict")
        return existing

    owner_counter, tenant_counter = await _locked_counters(
        session,
        policy_version=policy_version,
        owner_id=principal.owner_id,
        tenant_id=principal.tenant_id,
        operation_kind=operation_kind,
    )
    owner_limits = _quota_limits(
        policy, scope_type="owner", operation_kind=operation_kind
    )
    tenant_limits = _quota_limits(
        policy, scope_type="tenant", operation_kind=operation_kind
    )
    if owner_counter.queued_count >= owner_limits[1]:
        raise PublicAdmissionRejected(
            "public_owner_queue_limit_exceeded", retry_after_seconds=15
        )
    if tenant_counter.queued_count >= tenant_limits[1]:
        raise PublicAdmissionRejected(
            "public_tenant_queue_limit_exceeded", retry_after_seconds=15
        )

    now = get_current_utc_datetime()
    row = PublicOperationAdmission(
        operation_id=operation_id,
        policy_version=policy_version,
        owner_id=principal.owner_id,
        tenant_id=principal.tenant_id,
        trace_id_hash=sha256(principal.trace_id.encode("utf-8")).hexdigest(),
        operation_kind=operation_kind,
        slide_count=slide_count,
        planned_request_limit=request_limit,
        retry_request_limit=retry_limit,
        estimated_cost_limit_microunits=estimated_limit,
        actual_cost_limit_microunits=actual_limit,
        queued_at=now,
        updated_at=now,
    )
    owner_counter.queued_count += 1
    tenant_counter.queued_count += 1
    owner_counter.version += 1
    tenant_counter.version += 1
    owner_counter.updated_at = tenant_counter.updated_at = now
    session.add(row)
    await session.flush()
    return row


async def activate_public_operation(
    session: AsyncSession, admission_id: uuid.UUID
) -> PublicOperationAdmission:
    row = await session.scalar(
        select(PublicOperationAdmission)
        .where(PublicOperationAdmission.id == admission_id)
        .with_for_update()
    )
    if row is None:
        raise PublicAdmissionRejected("public_operation_not_found")
    if row.state == "active":
        return row
    if row.state != "queued":
        raise PublicAdmissionRejected("public_operation_not_queued")
    policy = await load_public_policy(session, row.policy_version)
    owner_counter, tenant_counter = await _locked_counters(
        session,
        policy_version=row.policy_version,
        owner_id=row.owner_id,
        tenant_id=row.tenant_id,
        operation_kind=row.operation_kind,
    )
    owner_active, _ = _quota_limits(
        policy, scope_type="owner", operation_kind=row.operation_kind
    )
    tenant_active, _ = _quota_limits(
        policy, scope_type="tenant", operation_kind=row.operation_kind
    )
    if owner_counter.active_count >= owner_active:
        raise PublicAdmissionRejected("public_owner_active_limit_exceeded")
    if tenant_counter.active_count >= tenant_active:
        raise PublicAdmissionRejected("public_tenant_active_limit_exceeded")
    if owner_counter.queued_count < 1 or tenant_counter.queued_count < 1:
        raise RuntimeError("public_quota_counter_underflow")
    now = get_current_utc_datetime()
    for counter in (owner_counter, tenant_counter):
        counter.queued_count -= 1
        counter.active_count += 1
        counter.last_activated_at = now
        counter.updated_at = now
        counter.version += 1
    row.state = "active"
    row.activated_at = now
    row.updated_at = now
    await session.flush()
    return row


async def finish_public_operation(
    session: AsyncSession, admission_id: uuid.UUID, *, state: str
) -> PublicOperationAdmission:
    if state not in _TERMINAL_STATES:
        raise ValueError("public_terminal_state_invalid")
    row = await session.scalar(
        select(PublicOperationAdmission)
        .where(PublicOperationAdmission.id == admission_id)
        .with_for_update()
    )
    if row is None:
        raise PublicAdmissionRejected("public_operation_not_found")
    # Acquire a real database write lock before sweeping reservations. This is
    # required on SQLite where SELECT .. FOR UPDATE is ignored; PostgreSQL also
    # serializes a concurrent reservation UPDATE behind this fence.
    terminal_fence = await session.execute(
        update(PublicOperationAdmission)
        .where(
            PublicOperationAdmission.id == row.id,
            PublicOperationAdmission.state == row.state,
        )
        .values(state=PublicOperationAdmission.state)
    )
    if terminal_fence.rowcount != 1:
        raise PublicAdmissionRejected("public_operation_terminal_conflict")
    await reconcile_orphaned_public_provider_calls(
        session,
        admission_id=row.id,
        force=True,
    )
    await session.refresh(row)
    reserved_rows = list(
        (
            await session.execute(
                select(PublicProviderLogicalCall.id).where(
                    PublicProviderLogicalCall.admission_id == row.id,
                    PublicProviderLogicalCall.state == "reserved",
                )
            )
        ).scalars()
    )
    if (
        reserved_rows
        or row.estimated_reserved_microunits != 0
        or row.actual_reserved_microunits != 0
    ):
        raise RuntimeError("public_provider_terminal_reserved_invariant")
    if row.state in _TERMINAL_STATES:
        if row.state != state:
            raise PublicAdmissionRejected("public_operation_terminal_conflict")
        return row
    owner_counter, tenant_counter = await _locked_counters(
        session,
        policy_version=row.policy_version,
        owner_id=row.owner_id,
        tenant_id=row.tenant_id,
        operation_kind=row.operation_kind,
    )
    source = "active_count" if row.state == "active" else "queued_count"
    for counter in (owner_counter, tenant_counter):
        current = int(getattr(counter, source))
        if current < 1:
            raise RuntimeError("public_quota_counter_underflow")
        setattr(counter, source, current - 1)
        counter.version += 1
        counter.updated_at = get_current_utc_datetime()
    row.state = state
    row.terminal_at = get_current_utc_datetime()
    row.updated_at = row.terminal_at
    await session.flush()
    return row


async def claim_next_public_operation(
    session: AsyncSession, *, operation_kind: str
) -> PublicOperationAdmission | None:
    """Choose one head per owner, then the least-recently-served owner."""

    # Claim polling is the durable control-plane sweep cadence. Settle any
    # reservation left by a terminal or lease-expired worker before allocating
    # more provider budget; no background process-local timer is authoritative.
    await reconcile_orphaned_public_provider_calls(session)

    candidates = list(
        (
            await session.execute(
                select(PublicOperationAdmission)
                .where(
                    PublicOperationAdmission.operation_kind == operation_kind,
                    PublicOperationAdmission.state == "queued",
                )
                .order_by(
                    PublicOperationAdmission.queued_at,
                    PublicOperationAdmission.id,
                )
                .limit(256)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    if not candidates:
        return None
    owner_heads: dict[tuple[str, uuid.UUID], PublicOperationAdmission] = {}
    for candidate in candidates:
        owner_heads.setdefault((candidate.policy_version, candidate.owner_id), candidate)
    counters = list(
        (
            await session.execute(
                select(PublicQuotaCounter).where(
                    PublicQuotaCounter.scope_type == "owner",
                    PublicQuotaCounter.operation_kind == operation_kind,
                )
            )
        ).scalars()
    )
    last_active = {
        (counter.policy_version, counter.scope_key): counter.last_activated_at
        for counter in counters
    }
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    ordered = sorted(
        owner_heads.values(),
        key=lambda item: (
            last_active.get((item.policy_version, str(item.owner_id))) or epoch,
            item.queued_at,
            str(item.id),
        ),
    )
    for candidate in ordered:
        try:
            return await activate_public_operation(session, candidate.id)
        except PublicAdmissionRejected as error:
            if error.code not in {
                "public_owner_active_limit_exceeded",
                "public_tenant_active_limit_exceeded",
            }:
                raise
    return None


async def reserve_public_provider_call(
    session: AsyncSession,
    *,
    admission_id: uuid.UUID,
    stage: str,
    slide_index: int | None,
    logical_call_ordinal: int | None,
    retry_class: str,
    estimated_cost_microunits: int,
    logical_call_key: str | None = None,
    provider_id: str = "unavailable",
    model_id: str = "unavailable",
    pricing_revision: str = "unpriced-v1",
    provider_idempotency_capable: bool = False,
    _return_reuse: bool = False,
) -> PublicProviderLogicalCall | tuple[PublicProviderLogicalCall, bool]:
    """Atomically commit one stable logical-call reservation before provider I/O."""

    if stage not in PUBLIC_PROVIDER_STAGES:
        raise ValueError("public_provider_stage_invalid")
    normalized_slide = -1 if slide_index is None else slide_index
    _nonnegative(normalized_slide + 1, "slide_index")
    if retry_class not in {"initial", "retry", "resume", "recovery"}:
        raise ValueError("public_provider_retry_class_invalid")
    for value, field in (
        (provider_id, "provider_id"),
        (model_id, "model_id"),
        (pricing_revision, "pricing_revision"),
    ):
        max_length = 128 if field == "model_id" else 64
        if len(value) > max_length or not _SAFE_ID_RE.fullmatch(value):
            raise ValueError(f"public_provider_{field}_invalid")
    if not isinstance(provider_idempotency_capable, bool):
        raise ValueError("public_provider_idempotency_capability_invalid")
    estimated = _nonnegative(
        estimated_cost_microunits, "estimated_cost_microunits"
    )
    admission = await session.scalar(
        select(PublicOperationAdmission)
        .where(PublicOperationAdmission.id == admission_id)
    )
    if admission is None or admission.state != "active":
        raise PublicAdmissionRejected("public_provider_operation_not_active")
    ordinal = _logical_call_ordinal(
        operation_id=admission.operation_id,
        stage=stage,
        slide_index=normalized_slide,
        logical_call_ordinal=logical_call_ordinal,
        logical_call_key=logical_call_key,
    )
    identity_material = (
        sha256(logical_call_key.encode("utf-8")).hexdigest()
        if logical_call_key is not None
        else str(ordinal)
    )
    preimage = (
        f"public-provider-call/v2:{admission.operation_id}:{stage}:"
        f"{normalized_slide}:{ordinal}:{identity_material}"
    )
    idempotency_key_hash = sha256(preimage.encode("utf-8")).hexdigest()
    existing = await session.scalar(
        select(PublicProviderLogicalCall).where(
            PublicProviderLogicalCall.admission_id == admission_id,
            PublicProviderLogicalCall.stage == stage,
            PublicProviderLogicalCall.slide_index == normalized_slide,
            PublicProviderLogicalCall.logical_call_ordinal == ordinal,
        )
    )
    if existing is not None:
        if (
            existing.estimated_cost_microunits != estimated
            or existing.idempotency_key_hash != idempotency_key_hash
            or existing.provider_id != provider_id
            or existing.model_id != model_id
            or existing.pricing_revision != pricing_revision
            or existing.provider_idempotency_capable
            != provider_idempotency_capable
            or (
                logical_call_key is None and existing.retry_class != retry_class
            )
        ):
            raise PublicAdmissionRejected("public_provider_logical_call_conflict")
        return (existing, True) if _return_reuse else existing
    is_retry = retry_class != "initial"
    row = PublicProviderLogicalCall(
        admission_id=admission_id,
        stage=stage,
        slide_index=normalized_slide,
        logical_call_ordinal=ordinal,
        retry_class=retry_class,
        idempotency_key_hash=idempotency_key_hash,
        estimated_cost_microunits=estimated,
        provider_id=provider_id,
        model_id=model_id,
        pricing_revision=pricing_revision,
        provider_idempotency_capable=provider_idempotency_capable,
    )
    request_count = (
        PublicOperationAdmission.retry_request_count
        if is_retry
        else PublicOperationAdmission.initial_request_count
    )
    request_limit = (
        PublicOperationAdmission.retry_request_limit
        if is_retry
        else PublicOperationAdmission.planned_request_limit
    )
    try:
        async with session.begin_nested():
            changed = await session.execute(
                update(PublicOperationAdmission)
                .where(
                    PublicOperationAdmission.id == admission_id,
                    PublicOperationAdmission.state == "active",
                    request_count < request_limit,
                    PublicOperationAdmission.estimated_consumed_microunits
                    + PublicOperationAdmission.estimated_reserved_microunits
                    + estimated
                    <= PublicOperationAdmission.estimated_cost_limit_microunits,
                    PublicOperationAdmission.actual_consumed_microunits
                    + PublicOperationAdmission.actual_reserved_microunits
                    + estimated
                    <= PublicOperationAdmission.actual_cost_limit_microunits,
                )
                .values(
                    {
                        request_count: request_count + 1,
                        PublicOperationAdmission.estimated_reserved_microunits:
                            PublicOperationAdmission.estimated_reserved_microunits
                            + estimated,
                        PublicOperationAdmission.actual_reserved_microunits:
                            PublicOperationAdmission.actual_reserved_microunits
                            + estimated,
                        PublicOperationAdmission.updated_at:
                            get_current_utc_datetime(),
                    }
                )
            )
            if changed.rowcount != 1:
                raise PublicAdmissionRejected("public_provider_budget_cas_rejected")
            session.add(row)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(
            select(PublicProviderLogicalCall).where(
                PublicProviderLogicalCall.admission_id == admission_id,
                PublicProviderLogicalCall.stage == stage,
                PublicProviderLogicalCall.slide_index == normalized_slide,
                PublicProviderLogicalCall.logical_call_ordinal == ordinal,
            )
        )
        if existing is not None and existing.estimated_cost_microunits == estimated:
            if (
                existing.idempotency_key_hash != idempotency_key_hash
                or existing.provider_id != provider_id
                or existing.model_id != model_id
                or existing.pricing_revision != pricing_revision
                or existing.provider_idempotency_capable
                != provider_idempotency_capable
            ):
                raise PublicAdmissionRejected(
                    "public_provider_logical_call_conflict"
                )
            return (existing, True) if _return_reuse else existing
        raise PublicAdmissionRejected("public_provider_logical_call_conflict")
    except PublicAdmissionRejected as error:
        if error.code != "public_provider_budget_cas_rejected":
            raise
        current = await session.scalar(
            select(PublicOperationAdmission)
            .where(PublicOperationAdmission.id == admission_id)
            .execution_options(populate_existing=True)
        )
        if current is None or current.state != "active":
            raise PublicAdmissionRejected("public_provider_operation_not_active")
        used = current.retry_request_count if is_retry else current.initial_request_count
        limit = current.retry_request_limit if is_retry else current.planned_request_limit
        if used >= limit:
            raise PublicAdmissionRejected(
                "public_provider_retry_limit_exceeded"
                if is_retry
                else "public_provider_request_limit_exceeded"
            )
        if (
            current.estimated_consumed_microunits
            + current.estimated_reserved_microunits
            + estimated
            > current.estimated_cost_limit_microunits
        ):
            raise PublicAdmissionRejected(
                "public_provider_estimated_cost_limit_exceeded"
            )
        raise PublicAdmissionRejected("public_provider_actual_cost_limit_exceeded")
    return (row, False) if _return_reuse else row


async def reconcile_public_provider_call(
    session: AsyncSession,
    *,
    logical_call_id: uuid.UUID,
    outcome: str,
    latency_ms: int,
    actual_cost_microunits: int | None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> PublicProviderLogicalCall:
    """Turn one reservation into a content-free receipt exactly once."""

    if outcome not in {"succeeded", "failed", "cancelled"}:
        raise ValueError("public_provider_outcome_invalid")
    latency = _nonnegative(latency_ms, "latency_ms")
    actual = (
        None
        if actual_cost_microunits is None
        else _nonnegative(actual_cost_microunits, "actual_cost_microunits")
    )
    input_count = None if input_tokens is None else _nonnegative(input_tokens, "input_tokens")
    output_count = None if output_tokens is None else _nonnegative(output_tokens, "output_tokens")
    call = await session.scalar(
        select(PublicProviderLogicalCall)
        .where(PublicProviderLogicalCall.id == logical_call_id)
        .with_for_update()
    )
    if call is None:
        raise PublicAdmissionRejected("public_provider_logical_call_not_found")
    target_state = (
        "unavailable"
        if actual is None
        else ("reported" if outcome == "succeeded" else outcome)
    )
    if call.state != "reserved":
        if (
            call.state == target_state
            and call.outcome == outcome
            and call.latency_ms == latency
            and call.actual_cost_microunits == actual
            and call.input_tokens == input_count
            and call.output_tokens == output_count
        ):
            return call
        raise PublicAdmissionRejected("public_provider_receipt_conflict")
    reserved = call.estimated_cost_microunits
    reconciled_at = get_current_utc_datetime()
    claimed = await session.execute(
        update(PublicProviderLogicalCall)
        .where(
            PublicProviderLogicalCall.id == logical_call_id,
            PublicProviderLogicalCall.state == "reserved",
        )
        .values(
            state=target_state,
            outcome=outcome,
            latency_ms=latency,
            actual_cost_microunits=actual,
            input_tokens=input_count,
            output_tokens=output_count,
            reconciled_at=reconciled_at,
        )
    )
    if claimed.rowcount != 1:
        current = await session.scalar(
            select(PublicProviderLogicalCall)
            .where(PublicProviderLogicalCall.id == logical_call_id)
            .execution_options(populate_existing=True)
        )
        if current is not None and (
            current.state == target_state
            and current.outcome == outcome
            and current.latency_ms == latency
            and current.actual_cost_microunits == actual
            and current.input_tokens == input_count
            and current.output_tokens == output_count
        ):
            return current
        raise PublicAdmissionRejected("public_provider_receipt_conflict")
    actual_charge = reserved if actual is None else actual
    accounting = await session.execute(
        update(PublicOperationAdmission)
        .where(
            PublicOperationAdmission.id == call.admission_id,
            PublicOperationAdmission.estimated_reserved_microunits >= reserved,
            PublicOperationAdmission.actual_reserved_microunits >= reserved,
        )
        .values(
            estimated_reserved_microunits=(
                PublicOperationAdmission.estimated_reserved_microunits - reserved
            ),
            estimated_consumed_microunits=(
                PublicOperationAdmission.estimated_consumed_microunits + reserved
            ),
            actual_reserved_microunits=(
                PublicOperationAdmission.actual_reserved_microunits - reserved
            ),
            actual_consumed_microunits=(
                PublicOperationAdmission.actual_consumed_microunits + actual_charge
            ),
            actual_cost_unavailable_count=(
                PublicOperationAdmission.actual_cost_unavailable_count
                + (1 if actual is None else 0)
            ),
            updated_at=reconciled_at,
        )
    )
    if accounting.rowcount != 1:
        raise RuntimeError("public_provider_reservation_underflow")
    current = await session.scalar(
        select(PublicProviderLogicalCall)
        .where(PublicProviderLogicalCall.id == logical_call_id)
        .execution_options(populate_existing=True)
    )
    if current is None:
        raise RuntimeError("public_provider_logical_call_missing_after_reconcile")
    return current


async def reconcile_orphaned_public_provider_calls(
    session: AsyncSession,
    *,
    admission_id: uuid.UUID | None = None,
    now: datetime | None = None,
    force: bool = False,
) -> int:
    """Settle reserved rows whose operation is terminal or whose lease expired.

    Unknown external effects are charged at the estimate and represented as an
    unavailable receipt. This is conservative: an orphan must never silently
    release public actual-cost budget as though no provider I/O occurred.
    """

    observed_at = now or get_current_utc_datetime()
    statement = (
        select(PublicProviderLogicalCall.id)
        .join(
            PublicOperationAdmission,
            PublicOperationAdmission.id == PublicProviderLogicalCall.admission_id,
        )
        .join(
            WorkspaceOperation,
            WorkspaceOperation.id == PublicOperationAdmission.operation_id,
        )
        .where(PublicProviderLogicalCall.state == "reserved")
    )
    if admission_id is not None:
        statement = statement.where(
            PublicProviderLogicalCall.admission_id == admission_id
        )
    if not force:
        statement = statement.where(
            or_(
                PublicOperationAdmission.state.in_(_TERMINAL_STATES),
                WorkspaceOperation.state.in_(
                    {"succeeded", "failed", "cancelled", "dead_letter"}
                ),
                and_(
                    WorkspaceOperation.state == "running",
                    WorkspaceOperation.lease_expires_at.is_not(None),
                    WorkspaceOperation.lease_expires_at <= observed_at,
                ),
            )
        )
    call_ids = list((await session.execute(statement)).scalars())
    reconciled = 0
    for call_id in call_ids:
        try:
            await reconcile_public_provider_call(
                session,
                logical_call_id=call_id,
                outcome="cancelled",
                latency_ms=0,
                actual_cost_microunits=None,
            )
        except PublicAdmissionRejected as error:
            if error.code != "public_provider_receipt_conflict":
                raise
            continue
        reconciled += 1
    return reconciled


async def enrich_public_provider_call(
    session: AsyncSession,
    *,
    logical_call_id: uuid.UUID,
    latency_ms: int,
    actual_cost_microunits: int | None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    provider_id: str | None = None,
    model_id: str | None = None,
    pricing_revision: str | None = None,
) -> PublicProviderLogicalCall:
    """Replace a succeeded unavailable receipt with provider-reported usage.

    The first reconciliation charges the estimate fail-closed. A later SDK
    usage observation may enrich that same logical call exactly once without
    double charging it; no other terminal transition is permitted here.
    """

    latency = _nonnegative(latency_ms, "latency_ms")
    actual = (
        None
        if actual_cost_microunits is None
        else _nonnegative(actual_cost_microunits, "actual_cost_microunits")
    )
    input_count = (
        None if input_tokens is None else _nonnegative(input_tokens, "input_tokens")
    )
    output_count = (
        None if output_tokens is None else _nonnegative(output_tokens, "output_tokens")
    )
    metadata = {
        "provider_id": provider_id,
        "model_id": model_id,
        "pricing_revision": pricing_revision,
    }
    for field, value in metadata.items():
        max_length = 128 if field == "model_id" else 64
        if value is not None and (
            len(value) > max_length or not _SAFE_ID_RE.fullmatch(value)
        ):
            raise ValueError(f"public_provider_{field}_invalid")
    call = await session.get(PublicProviderLogicalCall, logical_call_id)
    if call is None:
        raise PublicAdmissionRejected("public_provider_logical_call_not_found")
    if actual is None:
        if not (
            call.state == "unavailable"
            and call.outcome == "succeeded"
            and call.actual_cost_microunits is None
        ):
            raise PublicAdmissionRejected("public_provider_receipt_conflict")
        if (
            call.latency_ms == latency
            and call.input_tokens == input_count
            and call.output_tokens == output_count
            and (provider_id is None or call.provider_id == provider_id)
            and (model_id is None or call.model_id == model_id)
            and (
                pricing_revision is None
                or call.pricing_revision == pricing_revision
            )
        ):
            return call
        # Usage tokens may arrive even when the selected pricing profile has no
        # cost conversion. Keep the fail-closed estimated charge and unavailable
        # counter while enriching only the content-free receipt fields.
        if (
            (call.input_tokens is not None and call.input_tokens != input_count)
            or (call.output_tokens is not None and call.output_tokens != output_count)
        ):
            raise PublicAdmissionRejected("public_provider_receipt_conflict")
        enriched = await session.execute(
            update(PublicProviderLogicalCall)
            .where(
                PublicProviderLogicalCall.id == logical_call_id,
                PublicProviderLogicalCall.state == "unavailable",
                PublicProviderLogicalCall.outcome == "succeeded",
                PublicProviderLogicalCall.actual_cost_microunits.is_(None),
                or_(
                    PublicProviderLogicalCall.input_tokens.is_(None),
                    PublicProviderLogicalCall.input_tokens == input_count,
                ),
                or_(
                    PublicProviderLogicalCall.output_tokens.is_(None),
                    PublicProviderLogicalCall.output_tokens == output_count,
                ),
            )
            .values(
                latency_ms=latency,
                input_tokens=input_count,
                output_tokens=output_count,
                provider_id=(
                    provider_id
                    if provider_id is not None
                    else PublicProviderLogicalCall.provider_id
                ),
                model_id=(
                    model_id
                    if model_id is not None
                    else PublicProviderLogicalCall.model_id
                ),
                pricing_revision=(
                    pricing_revision
                    if pricing_revision is not None
                    else PublicProviderLogicalCall.pricing_revision
                ),
                reconciled_at=get_current_utc_datetime(),
            )
        )
        if enriched.rowcount != 1:
            current = await session.scalar(
                select(PublicProviderLogicalCall)
                .where(PublicProviderLogicalCall.id == logical_call_id)
                .execution_options(populate_existing=True)
            )
            if current is not None and (
                current.state == "unavailable"
                and current.outcome == "succeeded"
                and current.actual_cost_microunits is None
                and current.input_tokens == input_count
                and current.output_tokens == output_count
                and (provider_id is None or current.provider_id == provider_id)
                and (model_id is None or current.model_id == model_id)
                and (
                    pricing_revision is None
                    or current.pricing_revision == pricing_revision
                )
            ):
                return current
            raise PublicAdmissionRejected("public_provider_receipt_conflict")
        current = await session.scalar(
            select(PublicProviderLogicalCall)
            .where(PublicProviderLogicalCall.id == logical_call_id)
            .execution_options(populate_existing=True)
        )
        if current is None:
            raise RuntimeError("public_provider_logical_call_missing_after_enrich")
        return current
    if call.state == "reported":
        if (
            call.outcome == "succeeded"
            and call.actual_cost_microunits == actual
            and call.input_tokens == input_count
            and call.output_tokens == output_count
            and (provider_id is None or call.provider_id == provider_id)
            and (model_id is None or call.model_id == model_id)
            and (
                pricing_revision is None
                or call.pricing_revision == pricing_revision
            )
        ):
            return call
        raise PublicAdmissionRejected("public_provider_receipt_conflict")
    if not (
        call.state == "unavailable"
        and call.outcome == "succeeded"
        and call.actual_cost_microunits is None
    ):
        raise PublicAdmissionRejected("public_provider_receipt_conflict")
    estimated_charge = call.estimated_cost_microunits
    claimed_at = get_current_utc_datetime()
    claimed = await session.execute(
        update(PublicProviderLogicalCall)
        .where(
            PublicProviderLogicalCall.id == logical_call_id,
            PublicProviderLogicalCall.state == "unavailable",
            PublicProviderLogicalCall.outcome == "succeeded",
            PublicProviderLogicalCall.actual_cost_microunits.is_(None),
        )
        .values(
            state="reported",
            latency_ms=latency,
            actual_cost_microunits=actual,
            input_tokens=input_count,
            output_tokens=output_count,
            provider_id=(
                provider_id
                if provider_id is not None
                else PublicProviderLogicalCall.provider_id
            ),
            model_id=(
                model_id if model_id is not None else PublicProviderLogicalCall.model_id
            ),
            pricing_revision=(
                pricing_revision
                if pricing_revision is not None
                else PublicProviderLogicalCall.pricing_revision
            ),
            reconciled_at=claimed_at,
        )
    )
    if claimed.rowcount != 1:
        current = await session.scalar(
            select(PublicProviderLogicalCall)
            .where(PublicProviderLogicalCall.id == logical_call_id)
            .execution_options(populate_existing=True)
        )
        if current is not None and (
            current.state == "reported"
            and current.outcome == "succeeded"
            and current.actual_cost_microunits == actual
            and current.input_tokens == input_count
            and current.output_tokens == output_count
            and (provider_id is None or current.provider_id == provider_id)
            and (model_id is None or current.model_id == model_id)
            and (
                pricing_revision is None
                or current.pricing_revision == pricing_revision
            )
        ):
            return current
        raise PublicAdmissionRejected("public_provider_receipt_conflict")
    accounting = await session.execute(
        update(PublicOperationAdmission)
        .where(
            PublicOperationAdmission.id == call.admission_id,
            PublicOperationAdmission.actual_cost_unavailable_count >= 1,
            PublicOperationAdmission.actual_consumed_microunits >= estimated_charge,
        )
        .values(
            actual_cost_unavailable_count=(
                PublicOperationAdmission.actual_cost_unavailable_count - 1
            ),
            actual_consumed_microunits=(
                PublicOperationAdmission.actual_consumed_microunits
                - estimated_charge
                + actual
            ),
            updated_at=claimed_at,
        )
    )
    if accounting.rowcount != 1:
        raise RuntimeError("public_provider_unavailable_charge_underflow")
    current = await session.scalar(
        select(PublicProviderLogicalCall)
        .where(PublicProviderLogicalCall.id == logical_call_id)
        .execution_options(populate_existing=True)
    )
    if current is None:
        raise RuntimeError("public_provider_logical_call_missing_after_enrich")
    return current


async def reserve_public_provider_call_detached(
    *,
    operation_id: uuid.UUID,
    stage: str,
    slide_index: int | None,
    logical_call_ordinal: int | None,
    retry_class: str,
    estimated_cost_microunits: int,
    logical_call_key: str | None = None,
    expected_attempt_token: str | None = None,
    expected_fencing_token: int | None = None,
    provider_id: str = "unavailable",
    model_id: str = "unavailable",
    pricing_revision: str = "unpriced-v1",
    provider_idempotency_capable: bool = False,
) -> PublicProviderCallLease | None:
    """Reserve a public logical call in its own transaction before SDK I/O.

    Legacy/local operations without a public admission row remain supported;
    once a row exists, every failure is fail-closed and reaches the caller.
    """

    from services.database import async_session_maker

    async with async_session_maker() as lookup_session:
        result = (
            await lookup_session.execute(
                select(PublicOperationAdmission, WorkspaceOperation).join(
                    WorkspaceOperation,
                    WorkspaceOperation.id == PublicOperationAdmission.operation_id,
                ).where(PublicOperationAdmission.operation_id == operation_id)
            )
        ).first()
        if result is None:
            await lookup_session.rollback()
            return None
        admission, operation = result
        if expected_attempt_token is not None and (
            operation.state != "running"
            or operation.attempt_token != expected_attempt_token
        ):
            raise PublicAdmissionRejected("public_provider_stale_attempt")
        if expected_fencing_token is not None and (
            operation.fencing_token != expected_fencing_token
        ):
            raise PublicAdmissionRejected("public_provider_stale_fencing_token")
        admission_id = admission.id
        await lookup_session.rollback()
    async with async_session_maker() as session:
        if expected_attempt_token is not None or expected_fencing_token is not None:
            fencing_conditions = [
                WorkspaceOperation.id == operation_id,
                WorkspaceOperation.state == "running",
            ]
            if expected_attempt_token is not None:
                fencing_conditions.append(
                    WorkspaceOperation.attempt_token == expected_attempt_token
                )
            if expected_fencing_token is not None:
                fencing_conditions.append(
                    WorkspaceOperation.fencing_token == expected_fencing_token
                )
            fenced = await session.execute(
                update(WorkspaceOperation).where(
                    *fencing_conditions
                ).values(version=WorkspaceOperation.version)
            )
            if fenced.rowcount != 1:
                raise PublicAdmissionRejected("public_provider_stale_attempt")
        reserved_result = await reserve_public_provider_call(
            session,
            admission_id=admission_id,
            stage=stage,
            slide_index=slide_index,
            logical_call_ordinal=logical_call_ordinal,
            retry_class=retry_class,
            estimated_cost_microunits=estimated_cost_microunits,
            logical_call_key=logical_call_key,
            provider_id=provider_id,
            model_id=model_id,
            pricing_revision=pricing_revision,
            provider_idempotency_capable=provider_idempotency_capable,
            _return_reuse=True,
        )
        if not isinstance(reserved_result, tuple):
            raise RuntimeError("public_provider_reservation_reuse_contract_invalid")
        call, reused = reserved_result
        if reused and not provider_idempotency_capable:
            raise PublicAdmissionRejected(
                "public_provider_inflight_replay_unsupported"
                if call.state == "reserved"
                else "public_provider_terminal_replay_unsupported"
            )
        await session.commit()
        return PublicProviderCallLease(
            logical_call_id=call.id,
            admission_id=admission_id,
            provider_idempotency_key=call.idempotency_key_hash,
            state=call.state,
            reused=reused,
        )


async def reconcile_public_provider_call_detached(
    lease: PublicProviderCallLease,
    *,
    outcome: str,
    latency_ms: int,
    actual_cost_microunits: int | None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> None:
    from services.database import async_session_maker

    if lease.is_terminal_reuse:
        # The stable logical call was already durably settled by a prior
        # attempt. A provider-idempotent replay may observe it again, but must
        # not reconcile budget a second time or conflict on a new latency.
        return
    async with async_session_maker() as session:
        await reconcile_public_provider_call(
            session,
            logical_call_id=lease.logical_call_id,
            outcome=outcome,
            latency_ms=latency_ms,
            actual_cost_microunits=actual_cost_microunits,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        await session.commit()


async def enrich_public_provider_call_detached(
    lease: PublicProviderCallLease,
    *,
    latency_ms: int,
    actual_cost_microunits: int | None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    provider_id: str | None = None,
    model_id: str | None = None,
    pricing_revision: str | None = None,
) -> None:
    """Enrich one fail-closed unavailable receipt in its own transaction."""

    from services.database import async_session_maker

    if lease.is_terminal_reuse and lease.state != "unavailable":
        return
    async with async_session_maker() as session:
        await enrich_public_provider_call(
            session,
            logical_call_id=lease.logical_call_id,
            latency_ms=latency_ms,
            actual_cost_microunits=actual_cost_microunits,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider_id=provider_id,
            model_id=model_id,
            pricing_revision=pricing_revision,
        )
        await session.commit()


async def enrich_public_provider_usage_detached(
    lease: PublicProviderCallLease,
    *,
    pricing: ProviderModelPricing,
    latency_ms: int,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> ProviderUsageCost:
    """Price reported usage under one exact revision and atomically enrich it."""

    usage_cost = calculate_actual_usage_cost(
        pricing,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
    )
    await enrich_public_provider_call_detached(
        lease,
        latency_ms=latency_ms,
        actual_cost_microunits=usage_cost.total_cost_microunits,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        provider_id=pricing.provider_id,
        model_id=pricing.model_id,
        pricing_revision=pricing.revision,
    )
    return usage_cost


def _age_ms(now: datetime, then: datetime | None) -> int:
    if then is None:
        return 0
    observed = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    normalized = then if then.tzinfo is not None else then.replace(tzinfo=timezone.utc)
    return max(0, int((observed - normalized).total_seconds() * 1000))


async def public_quota_metrics(
    session: AsyncSession,
    *,
    principal: PrincipalContext,
    operation_kind: str,
) -> dict[str, int]:
    """Return release-gate-safe numeric queue, wait, quota, and cost counters."""

    principal = _principal(principal)
    now = get_current_utc_datetime()
    queued = list(
        (
            await session.execute(
                select(PublicOperationAdmission).where(
                    PublicOperationAdmission.operation_kind == operation_kind,
                    PublicOperationAdmission.state == "queued",
                )
            )
        ).scalars()
    )
    owner_rows = [row for row in queued if row.owner_id == principal.owner_id]
    tenant_rows = [row for row in queued if row.tenant_id == principal.tenant_id]
    owner_active = await session.scalar(
        select(PublicQuotaCounter).where(
            PublicQuotaCounter.scope_type == "owner",
            PublicQuotaCounter.scope_key == str(principal.owner_id),
            PublicQuotaCounter.operation_kind == operation_kind,
        )
    )
    tenant_active = await session.scalar(
        select(PublicQuotaCounter).where(
            PublicQuotaCounter.scope_type == "tenant",
            PublicQuotaCounter.scope_key == principal.tenant_id,
            PublicQuotaCounter.operation_kind == operation_kind,
        )
    )
    return {
        "queue_depth": len(queued),
        "oldest_queue_age_ms": max((_age_ms(now, row.queued_at) for row in queued), default=0),
        "owner_queue_depth": len(owner_rows),
        "tenant_queue_depth": len(tenant_rows),
        "owner_wait_ms": max((_age_ms(now, row.queued_at) for row in owner_rows), default=0),
        "tenant_wait_ms": max((_age_ms(now, row.queued_at) for row in tenant_rows), default=0),
        "owner_active": int(owner_active.active_count if owner_active else 0),
        "tenant_active": int(tenant_active.active_count if tenant_active else 0),
    }


async def public_operation_metrics(
    session: AsyncSession, admission_id: uuid.UUID
) -> dict[str, int]:
    row = await session.get(PublicOperationAdmission, admission_id)
    if row is None:
        raise PublicAdmissionRejected("public_operation_not_found")
    now = get_current_utc_datetime()
    return {
        "queue_wait_ms": _age_ms(row.activated_at or now, row.queued_at),
        "initial_request_count": row.initial_request_count,
        "retry_request_count": row.retry_request_count,
        "planned_request_limit": row.planned_request_limit,
        "retry_request_limit": row.retry_request_limit,
        "estimated_cost_limit_microunits": row.estimated_cost_limit_microunits,
        "estimated_reserved_microunits": row.estimated_reserved_microunits,
        "estimated_consumed_microunits": row.estimated_consumed_microunits,
        "actual_cost_limit_microunits": row.actual_cost_limit_microunits,
        "actual_reserved_microunits": row.actual_reserved_microunits,
        "actual_consumed_microunits": row.actual_consumed_microunits,
        "actual_cost_unavailable_count": row.actual_cost_unavailable_count,
    }


def _observed_metric(value: int | str) -> dict[str, int | str]:
    return {"state": "observed", "value": value}


def _unavailable_metric(reason: str) -> dict[str, str]:
    return {"state": "unavailable", "reason": reason}


async def public_operation_observability_metrics(
    session: AsyncSession, operation_id: uuid.UUID
) -> dict[str, dict[str, int | str]]:
    """Project one operation's durable queue/provider metrics without nulls.

    Missing provider usage is represented as an allowlisted unavailable state;
    it is never converted to a numeric zero.  Prompt/response/provider request
    identifiers are intentionally outside this projection.
    """

    admission = await session.scalar(
        select(PublicOperationAdmission).where(
            PublicOperationAdmission.operation_id == operation_id
        )
    )
    if admission is None:
        return {
            name: _unavailable_metric("public_admission_not_found")
            for name in (
                "queueWait",
                "queuePosition",
                "providerInitialCalls",
                "providerRetryCalls",
                "providerLogicalOrdinal",
                "estimatedTokens",
                "actualTokens",
                "estimatedCost",
                "actualCost",
            )
        }
    calls = list(
        (
            await session.execute(
                select(PublicProviderLogicalCall).where(
                    PublicProviderLogicalCall.admission_id == admission.id
                )
            )
        ).scalars()
    )
    actual_tokens_available = bool(calls) and all(
        call.input_tokens is not None and call.output_tokens is not None
        for call in calls
    )
    actual_cost_available = bool(calls) and all(
        call.actual_cost_microunits is not None for call in calls
    )
    return {
        "queueWait": _observed_metric(
            _age_ms(admission.activated_at or get_current_utc_datetime(), admission.queued_at)
        ),
        "queuePosition": _unavailable_metric("queue_position_not_persisted"),
        "providerInitialCalls": _observed_metric(admission.initial_request_count),
        "providerRetryCalls": _observed_metric(admission.retry_request_count),
        "providerLogicalOrdinal": _observed_metric(len(calls)),
        "estimatedTokens": _unavailable_metric("provider_token_estimate_not_recorded"),
        "actualTokens": (
            _observed_metric(
                sum((call.input_tokens or 0) + (call.output_tokens or 0) for call in calls)
            )
            if actual_tokens_available
            else _unavailable_metric("provider_token_usage_not_reported")
        ),
        "estimatedCost": _observed_metric(
            admission.estimated_consumed_microunits
            + admission.estimated_reserved_microunits
        ),
        "actualCost": (
            _observed_metric(sum(call.actual_cost_microunits or 0 for call in calls))
            if actual_cost_available
            else _unavailable_metric("provider_actual_cost_not_reported")
        ),
    }


async def public_prometheus_metrics_text(session: AsyncSession) -> str:
    """Render aggregate-only public queue and budget metrics without identities."""

    try:
        rows = list((await session.execute(select(PublicOperationAdmission))).scalars())
    except OperationalError:
        # Keep the pre-migration scrape endpoint available during a rolling
        # start; absence is explicit and never reported as healthy public data.
        await session.rollback()
        return "presenton_public_metrics_available 0\n"
    now = get_current_utc_datetime()
    lines = [
        "# HELP presenton_public_operations Durable public operation states.",
        "# TYPE presenton_public_operations gauge",
    ]
    for operation_kind in ("generation", "export"):
        kind_rows = [row for row in rows if row.operation_kind == operation_kind]
        for state in ("queued", "active", "succeeded", "failed", "cancelled", "budget_exhausted"):
            count = sum(1 for row in kind_rows if row.state == state)
            lines.append(
                f'presenton_public_operations{{kind="{operation_kind}",state="{state}"}} {count}'
            )
        queued = [row for row in kind_rows if row.state == "queued"]
        lines.append(
            f'presenton_public_oldest_queue_age_ms{{kind="{operation_kind}"}} '
            f'{max((_age_ms(now, row.queued_at) for row in queued), default=0)}'
        )
        lines.append(
            f'presenton_public_provider_requests_total{{kind="{operation_kind}",class="initial"}} '
            f'{sum(row.initial_request_count for row in kind_rows)}'
        )
        lines.append(
            f'presenton_public_provider_requests_total{{kind="{operation_kind}",class="retry"}} '
            f'{sum(row.retry_request_count for row in kind_rows)}'
        )
        lines.append(
            f'presenton_public_provider_cost_microunits{{kind="{operation_kind}",class="estimated_consumed"}} '
            f'{sum(row.estimated_consumed_microunits for row in kind_rows)}'
        )
        lines.append(
            f'presenton_public_provider_cost_microunits{{kind="{operation_kind}",class="actual_consumed"}} '
            f'{sum(row.actual_consumed_microunits for row in kind_rows)}'
        )
        lines.append(
            f'presenton_public_provider_actual_cost_unavailable_total{{kind="{operation_kind}"}} '
            f'{sum(row.actual_cost_unavailable_count for row in kind_rows)}'
        )
    lines.append("presenton_public_metrics_available 1")
    return "\n".join(lines) + "\n"


__all__ = [
    "PUBLIC_DEFAULT_POLICY_VERSION",
    "PUBLIC_PROVIDER_STAGES",
    "PrincipalContext",
    "ProviderRequestPlan",
    "PublicAdmissionRejected",
    "PublicProviderCallLease",
    "activate_public_operation",
    "admit_public_operation",
    "claim_next_public_operation",
    "current_principal_context",
    "enrich_public_provider_call",
    "enrich_public_provider_call_detached",
    "enrich_public_provider_usage_detached",
    "finish_public_operation",
    "load_public_policy",
    "plan_provider_requests",
    "public_operation_metrics",
    "public_operation_observability_metrics",
    "public_prometheus_metrics_text",
    "public_quota_metrics",
    "reconcile_orphaned_public_provider_calls",
    "reconcile_public_provider_call",
    "reconcile_public_provider_call_detached",
    "reserve_public_provider_call",
    "reserve_public_provider_call_detached",
]
