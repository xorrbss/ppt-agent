"""Cluster-wide public operation admission and provider budget enforcement.

Only a verified :class:`PrincipalContext` may supply ownership.  This module's
inputs deliberately have no cookie, session, owner override, payload, prompt, or
provider-response fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from inspect import isawaitable
import re
import uuid

from sqlalchemy import and_, or_, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.sql import public_admission as public_admission_models
from models.sql.public_admission import (
    PublicOperationAdmission,
    PublicPolicyProfile,
    PublicProviderLogicalCall,
    PublicQuotaCounter,
)
from migration_revision_contract import (
    PUBLIC_CONTROL_PLANE_AUTHORITY_PROBE_SQL,
    REVISION_PUBLIC_CONTROL_PLANE,
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
    {"succeeded", "failed", "cancelled", "budget_exhausted", "deadline_exceeded"}
)
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_PHASE2_COUNTER_FIELDS = frozenset({"queued_resource_units", "active_resource_units"})
_PHASE2_ADMISSION_FIELDS = frozenset(
    {"resource_units", "deadline_at", "policy_digest", "fence"}
)
_PHASE2_COMMIT_RESERVE_SECONDS = 10


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


@dataclass(frozen=True, slots=True)
class WeightedResourcePlan:
    """Content-free scheduler estimate persisted as resource units.

    The fixed weights deliberately make a high-DPR, SVG-heavy Hybrid deck cost
    more than a small text deck.  The value is an admission reservation, not a
    runtime measurement, so it is stable across retries and cluster nodes.
    """

    pixel_count: int
    device_pixel_ratio_milli: int
    dom_node_count: int
    font_count: int
    svg_complexity: int
    estimated_memory_mib: int
    fidelity_mode: str

    @property
    def resource_units(self) -> int:
        mode_weight = {"standard": 0, "fidelity": 2, "hybrid": 4}.get(
            self.fidelity_mode
        )
        if mode_weight is None:
            raise ValueError("public_fidelity_mode_invalid")
        # Integer arithmetic keeps the reservation deterministic and avoids a
        # float/rounding difference becoming a distributed quota bypass.
        dpr_squared_milli = self.device_pixel_ratio_milli**2
        pixel_units = (self.pixel_count * dpr_squared_milli + 999_999_999_999) // 1_000_000_000_000
        return max(
            1,
            pixel_units
            + ((self.dom_node_count + 999) // 1_000)
            + ((self.font_count + 7) // 8)
            + ((self.svg_complexity + 249) // 250)
            + ((self.estimated_memory_mib + 127) // 128)
            + mode_weight,
        )


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


def plan_weighted_resources(
    *,
    pixel_count: int,
    device_pixel_ratio_milli: int,
    dom_node_count: int,
    font_count: int,
    svg_complexity: int,
    estimated_memory_mib: int,
    fidelity_mode: str,
) -> WeightedResourcePlan:
    """Normalize a scheduler estimate before it becomes a durable reservation."""

    plan = WeightedResourcePlan(
        pixel_count=_nonnegative(pixel_count, "pixel_count"),
        device_pixel_ratio_milli=_nonnegative(
            device_pixel_ratio_milli, "device_pixel_ratio_milli"
        ),
        dom_node_count=_nonnegative(dom_node_count, "dom_node_count"),
        font_count=_nonnegative(font_count, "font_count"),
        svg_complexity=_nonnegative(svg_complexity, "svg_complexity"),
        estimated_memory_mib=_nonnegative(
            estimated_memory_mib, "estimated_memory_mib"
        ),
        fidelity_mode=fidelity_mode,
    )
    if plan.device_pixel_ratio_milli < 1:
        raise ValueError("device_pixel_ratio_milli_must_be_positive")
    # Resolve the property here so an invalid fidelity mode never reaches DB.
    _ = plan.resource_units
    return plan


def _model_columns(model: type[object]) -> set[str]:
    table = getattr(model, "__table__", None)
    return set(table.columns.keys()) if table is not None else set()


def _phase2_resource_lease_model() -> type[object] | None:
    """Read the DB-lane model lazily so a rolling Phase-1 deploy still imports."""

    candidate = getattr(public_admission_models, "PublicResourceLease", None)
    if candidate is None:
        return None
    required = {
        "admission_id",
        "operation_id",
        "policy_version",
        "scope_type",
        "scope_key",
        "operation_kind",
        "resource_units",
        "state",
        "attempt",
        "fence",
        "reserved_at",
        "deadline_at",
    }
    return candidate if required <= _model_columns(candidate) else None


def _phase2_available() -> bool:
    return (
        _PHASE2_COUNTER_FIELDS <= _model_columns(PublicQuotaCounter)
        and _PHASE2_ADMISSION_FIELDS <= _model_columns(PublicOperationAdmission)
        and _phase2_resource_lease_model() is not None
    )


async def _require_phase2_authority(session: AsyncSession) -> type[object]:
    """Reject instead of silently falling back when durable authority is absent."""

    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        raise PublicAdmissionRejected("public_postgresql_authority_required")
    lease_model = _phase2_resource_lease_model()
    if not _phase2_available() or lease_model is None:
        raise PublicAdmissionRejected("public_phase2_schema_unavailable")
    try:
        async with session.begin_nested():
            authority_revision = await session.scalar(
                text(PUBLIC_CONTROL_PLANE_AUTHORITY_PROBE_SQL)
            )
    except SQLAlchemyError as error:
        raise PublicAdmissionRejected(
            "public_phase2_schema_authority_unavailable"
        ) from error
    if authority_revision != REVISION_PUBLIC_CONTROL_PLANE:
        raise PublicAdmissionRejected("public_phase2_schema_authority_unavailable")
    return lease_model


async def _resolved_phase2_authority(session: AsyncSession) -> type[object]:
    """Resolve the async authority guard while retaining isolated test seams."""

    authority = _require_phase2_authority(session)
    if isawaitable(authority):
        authority = await authority
    return authority


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field}_required")
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _absolute_deadline(
    *,
    now: datetime,
    policy: PublicPolicyProfile,
    operation_kind: str,
    auth_expires_at: datetime | None,
    server_hard_deadline_at: datetime | None,
) -> datetime:
    seconds = _nonnegative(
        int(getattr(policy, f"{operation_kind}_deadline_seconds")),
        "policy_deadline_seconds",
    )
    if seconds < _PHASE2_COMMIT_RESERVE_SECONDS:
        raise PublicAdmissionRejected("public_policy_deadline_invalid")
    candidates = [_utc(now, "admission_now") + timedelta(seconds=seconds)]
    if auth_expires_at is not None:
        candidates.append(_utc(auth_expires_at, "auth_expires_at"))
    if server_hard_deadline_at is not None:
        candidates.append(_utc(server_hard_deadline_at, "server_hard_deadline_at"))
    deadline = min(candidates)
    if deadline <= _utc(now, "admission_now") + timedelta(
        seconds=_PHASE2_COMMIT_RESERVE_SECONDS
    ):
        raise PublicAdmissionRejected("public_deadline_insufficient")
    return deadline


def _policy_digest(policy: PublicPolicyProfile) -> str:
    """Pin quota/deadline policy content rather than trusting a mutable version label."""

    fields = sorted(_model_columns(PublicPolicyProfile) - {"created_at"})
    values = "|".join(f"{field}={getattr(policy, field)!r}" for field in fields)
    return sha256(f"public-policy/v2|{values}".encode("utf-8")).hexdigest()


def _phase2_workspace_model() -> type[object] | None:
    candidate = getattr(public_admission_models, "WorkspaceOperation", None)
    if candidate is None:
        # Keep the cross-module dependency lazy during the schema rollout.
        from models.sql import workspace as workspace_models

        candidate = getattr(workspace_models, "WorkspaceOperation", None)
    required = {
        "id",
        "owner_id",
        "deadline_at",
        "contract_version",
        "execution_profile_version",
        "phase",
    }
    return candidate if candidate is not None and required <= _model_columns(candidate) else None


def _require_phase2_workspace_model() -> type[object]:
    model = _phase2_workspace_model()
    if model is None:
        raise PublicAdmissionRejected("public_phase2_workspace_schema_unavailable")
    return model


def _resource_lease_scope_key(scope_type: str, principal: PrincipalContext) -> str:
    return {
        "global": "global",
        "tenant": principal.tenant_id,
        "owner": str(principal.owner_id),
    }[scope_type]


def _assert_deadline_remaining(deadline_at: datetime, *, now: datetime) -> None:
    if _utc(deadline_at, "deadline_at") <= _utc(now, "admission_now") + timedelta(
        seconds=_PHASE2_COMMIT_RESERVE_SECONDS
    ):
        raise PublicAdmissionRejected("public_deadline_insufficient")


async def _require_phase2_provider_contract(
    session: AsyncSession,
    admission: PublicOperationAdmission,
    *,
    allow_terminal_reconcile: bool = False,
) -> None:
    """Bind detached provider I/O to the PostgreSQL operation authority.

    A public admission row alone is insufficient: Phase-1 rows and SQLite
    fixtures do not carry the immutable deadline/fence contract required to
    authorize external provider work. Reconciliation remains allowed after a
    budget terminal transition so already-started calls cannot strand counters.
    """

    await _resolved_phase2_authority(session)
    if (
        not getattr(admission, "policy_digest", None)
        or getattr(admission, "deadline_at", None) is None
        or int(getattr(admission, "fence", 0)) < 1
    ):
        raise PublicAdmissionRejected("public_phase2_admission_invalid")
    if admission.state == "budget_exhausted" and not allow_terminal_reconcile:
        raise PublicAdmissionRejected(
            "public_provider_actual_cost_limit_exceeded"
        )
    if admission.state != "active" and not (
        allow_terminal_reconcile and admission.state == "budget_exhausted"
    ):
        raise PublicAdmissionRejected("public_provider_operation_not_active")
    workspace_model = _require_phase2_workspace_model()
    operation = await session.scalar(
        select(workspace_model)
        .where(workspace_model.id == admission.operation_id)
        .with_for_update()
    )
    allowed_phases = (
        {"active", "budget_exhausted"}
        if allow_terminal_reconcile
        else {"active"}
    )
    if (
        operation is None
        or operation.contract_version != "public-operation/v2"
        or operation.execution_profile_version != "public-generation-export/v2"
        or operation.phase not in allowed_phases
        or operation.fencing_token != admission.fence
        or operation.deadline_at is None
        or _utc(operation.deadline_at, "operation_deadline_at")
        != _utc(admission.deadline_at, "admission_deadline_at")
    ):
        raise PublicAdmissionRejected("public_operation_workspace_contract_conflict")
    if not allow_terminal_reconcile:
        _assert_deadline_remaining(
            admission.deadline_at,
            now=get_current_utc_datetime(),
        )


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
    if scope_type not in {"global", "owner", "tenant"} or operation_kind not in {
        "generation",
        "export",
    }:
        raise ValueError("public_quota_scope_invalid")
    prefix = f"{scope_type}_"
    return (
        int(getattr(policy, f"{prefix}active_{operation_kind}")),
        int(getattr(policy, f"{prefix}queued_{operation_kind}")),
    )


def _resource_limits(
    policy: PublicPolicyProfile, *, scope_type: str
) -> tuple[int, int]:
    if scope_type not in {"global", "tenant", "owner"}:
        raise ValueError("public_resource_scope_invalid")
    prefix = f"{scope_type}_"
    return (
        int(getattr(policy, f"{prefix}queued_resource_units")),
        int(getattr(policy, f"{prefix}active_resource_units")),
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
    # Every transition follows the global/tenant/owner hierarchy.  Phase 1
    # does not have a global counter, so its compatible subset is tenant/owner.
    identities = (("tenant", tenant_id), ("owner", str(owner_id)))
    for scope_type, scope_key in identities:
        await _ensure_counter(
            session,
            policy_version=policy_version,
            scope_type=scope_type,
            scope_key=scope_key,
            operation_kind=operation_kind,
        )
    by_type: dict[str, PublicQuotaCounter] = {}
    for scope_type, scope_key in identities:
        row = await session.scalar(
            select(PublicQuotaCounter)
            .where(
                PublicQuotaCounter.policy_version == policy_version,
                PublicQuotaCounter.scope_type == scope_type,
                PublicQuotaCounter.scope_key == scope_key,
                PublicQuotaCounter.operation_kind == operation_kind,
            )
            .with_for_update()
        )
        if row is not None:
            by_type[scope_type] = row
    if set(by_type) != {"owner", "tenant"}:
        raise RuntimeError("public_quota_counter_missing")
    return by_type["owner"], by_type["tenant"]


async def _locked_phase2_counters(
    session: AsyncSession,
    *,
    policy_version: str,
    owner_id: uuid.UUID,
    tenant_id: str,
    operation_kind: str,
) -> dict[str, PublicQuotaCounter]:
    """Create and lock all scheduler axes in the only permitted order."""

    identities = (
        ("global", "global"),
        ("tenant", tenant_id),
        ("owner", str(owner_id)),
    )
    for scope_type, scope_key in identities:
        await _ensure_counter(
            session,
            policy_version=policy_version,
            scope_type=scope_type,
            scope_key=scope_key,
            operation_kind=operation_kind,
        )
    locked: dict[str, PublicQuotaCounter] = {}
    for scope_type, scope_key in identities:
        row = await session.scalar(
            select(PublicQuotaCounter)
            .where(
                PublicQuotaCounter.policy_version == policy_version,
                PublicQuotaCounter.scope_type == scope_type,
                PublicQuotaCounter.scope_key == scope_key,
                PublicQuotaCounter.operation_kind == operation_kind,
            )
            .with_for_update()
        )
        if row is None:
            raise PublicAdmissionRejected("public_quota_authority_unavailable")
        locked[scope_type] = row
    return locked


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
    legacy_deadline_seconds = max(
        1, int(getattr(policy, f"{operation_kind}_deadline_seconds"))
    )
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
        # Phase-1 callers have no auth/server deadline input.  Preserve their
        # API while supplying durable non-null Phase-2 columns; the strict v2
        # entry point computes the full min(policy, auth, server) contract.
        resource_units=1,
        deadline_at=now + timedelta(seconds=legacy_deadline_seconds),
        policy_digest=_policy_digest(policy),
        fence=0,
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


async def admit_public_operation_v2(
    session: AsyncSession,
    *,
    principal: PrincipalContext,
    operation_id: uuid.UUID,
    operation_kind: str,
    request_plan: ProviderRequestPlan | None,
    resource_plan: WeightedResourcePlan,
    auth_expires_at: datetime | None = None,
    server_hard_deadline_at: datetime | None = None,
    policy_version: str = PUBLIC_DEFAULT_POLICY_VERSION,
    estimated_cost_limit_microunits: int | None = None,
    actual_cost_limit_microunits: int | None = None,
) -> PublicOperationAdmission:
    """Phase-2 control-plane admission in one PostgreSQL transaction.

    This is intentionally separate from the Phase-1 entry point: callers that
    opt in cannot accidentally receive a local/SQLite or incomplete-schema
    admission.  The caller owns commit and must return a receipt only after it.
    """

    principal = _principal(principal)
    if operation_kind not in {"generation", "export"}:
        raise ValueError("public_operation_kind_invalid")
    lease_model = await _resolved_phase2_authority(session)
    workspace_model = _require_phase2_workspace_model()
    operation = await session.scalar(
        select(workspace_model)
        .where(workspace_model.id == operation_id)
        .with_for_update()
    )
    if operation is None or operation.owner_id != principal.owner_id:
        raise PublicAdmissionRejected("public_operation_authority_unavailable")
    existing = await session.scalar(
        select(PublicOperationAdmission)
        .where(PublicOperationAdmission.operation_id == operation_id)
        .with_for_update()
    )
    resource_units = resource_plan.resource_units
    if existing is not None:
        if (
            existing.owner_id != principal.owner_id
            or existing.tenant_id != principal.tenant_id
            or existing.operation_kind != operation_kind
            or existing.policy_version != policy_version
            or existing.resource_units != resource_units
            or not existing.policy_digest
            or operation.deadline_at is None
            or operation.fencing_token != existing.fence
            or _utc(operation.deadline_at, "operation_deadline_at")
            != _utc(existing.deadline_at, "admission_deadline_at")
        ):
            raise PublicAdmissionRejected("public_operation_identity_conflict")
        _assert_deadline_remaining(existing.deadline_at, now=get_current_utc_datetime())
        return existing
    policy = await load_public_policy(session, policy_version)
    now = get_current_utc_datetime()
    deadline = _absolute_deadline(
        now=now,
        policy=policy,
        operation_kind=operation_kind,
        auth_expires_at=auth_expires_at,
        server_hard_deadline_at=server_hard_deadline_at,
    )
    existing_deadline = getattr(operation, "deadline_at", None)
    if existing_deadline is not None and _utc(existing_deadline, "operation_deadline_at") != deadline:
        raise PublicAdmissionRejected("public_operation_deadline_conflict")
    contract = "public-operation/v2"
    profile = "public-generation-export/v2"
    for field, value in (
        ("contract_version", contract),
        ("execution_profile_version", profile),
    ):
        prior = getattr(operation, field, None)
        if prior is not None and prior != value:
            raise PublicAdmissionRejected("public_operation_contract_conflict")
    prior_phase = getattr(operation, "phase", None)
    if prior_phase not in {None, "queued", "admitted"}:
        raise PublicAdmissionRejected("public_operation_phase_conflict")

    if operation_kind == "generation":
        if request_plan is None:
            raise PublicAdmissionRejected("provider_request_plan_required")
        if request_plan.slide_count > policy.max_slides:
            raise PublicAdmissionRejected("public_slide_limit_exceeded")
        request_limit = request_plan.initial_request_limit
        retry_limit = policy.retry_request_limit
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

    counters = await _locked_phase2_counters(
        session,
        policy_version=policy_version,
        owner_id=principal.owner_id,
        tenant_id=principal.tenant_id,
        operation_kind=operation_kind,
    )
    for scope_type in ("global", "tenant", "owner"):
        counter = counters[scope_type]
        _, queued_count_limit = _quota_limits(
            policy, scope_type=scope_type, operation_kind=operation_kind
        )
        queued_resource_limit, _ = _resource_limits(policy, scope_type=scope_type)
        if counter.queued_count >= queued_count_limit:
            raise PublicAdmissionRejected(
                f"public_{scope_type}_queue_limit_exceeded", retry_after_seconds=15
            )
        if counter.queued_resource_units + resource_units > queued_resource_limit:
            raise PublicAdmissionRejected(
                f"public_{scope_type}_resource_limit_exceeded", retry_after_seconds=15
            )

    fence = 1
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
        resource_units=resource_units,
        deadline_at=deadline,
        policy_digest=_policy_digest(policy),
        fence=fence,
        queued_at=now,
        updated_at=now,
    )
    operation.deadline_at = deadline
    operation.contract_version = contract
    operation.execution_profile_version = profile
    operation.phase = "admitted"
    operation.fencing_token = fence
    session.add(row)
    await session.flush()
    for scope_type in ("global", "tenant", "owner"):
        counter = counters[scope_type]
        counter.queued_count += 1
        counter.queued_resource_units += resource_units
        counter.version += 1
        counter.updated_at = now
        session.add(
            lease_model(
                admission_id=row.id,
                operation_id=operation_id,
                policy_version=policy_version,
                scope_type=scope_type,
                scope_key=_resource_lease_scope_key(scope_type, principal),
                operation_kind=operation_kind,
                resource_units=resource_units,
                state="queued",
                attempt=0,
                fence=fence,
                reserved_at=now,
                deadline_at=deadline,
                lease_expires_at=deadline,
                version=0,
            )
        )
    await session.flush()
    return row


async def _locked_phase2_leases(
    session: AsyncSession, *, lease_model: type[object], admission: PublicOperationAdmission
) -> list[object]:
    leases = list(
        (
            await session.execute(
                select(lease_model)
                .where(lease_model.admission_id == admission.id)
                .with_for_update()
            )
        ).scalars()
    )
    expected = {"global", "tenant", "owner"}
    if len(leases) != 3 or {lease.scope_type for lease in leases} != expected:
        raise PublicAdmissionRejected("public_resource_lease_authority_unavailable")
    if any(
        lease.operation_id != admission.operation_id
        or lease.resource_units != admission.resource_units
        for lease in leases
    ):
        raise PublicAdmissionRejected("public_resource_lease_conflict")
    return leases


async def activate_public_operation_v2(
    session: AsyncSession,
    *,
    admission_id: uuid.UUID,
    lease_owner_hash: str,
    lease_seconds: int,
    attempt: int | None = None,
    fence: int | None = None,
) -> PublicOperationAdmission:
    """Atomically activate an admission and issue scope-aligned resource leases."""

    lease_model = await _resolved_phase2_authority(session)
    if not _HASH_RE.fullmatch(lease_owner_hash):
        raise ValueError("public_lease_owner_hash_invalid")
    duration = _nonnegative(lease_seconds, "lease_seconds")
    if duration < 1:
        raise ValueError("public_lease_seconds_must_be_positive")
    admission = await session.scalar(
        select(PublicOperationAdmission)
        .where(PublicOperationAdmission.id == admission_id)
        .with_for_update()
    )
    if admission is None:
        raise PublicAdmissionRejected("public_operation_not_found")
    if fence is not None and admission.fence != _nonnegative(fence, "fence"):
        raise PublicAdmissionRejected("public_operation_fence_conflict")
    workspace_model = _require_phase2_workspace_model()
    operation = await session.scalar(
        select(workspace_model)
        .where(workspace_model.id == admission.operation_id)
        .with_for_update()
    )
    if operation is None or _utc(operation.deadline_at, "operation_deadline_at") != _utc(
        admission.deadline_at, "admission_deadline_at"
    ) or operation.fencing_token != admission.fence:
        raise PublicAdmissionRejected("public_operation_fence_conflict")
    if admission.state == "active":
        leases = await _locked_phase2_leases(session, lease_model=lease_model, admission=admission)
        if attempt is not None and any(lease.attempt != attempt for lease in leases):
            raise PublicAdmissionRejected("public_resource_lease_attempt_conflict")
        return admission
    if admission.state != "queued":
        raise PublicAdmissionRejected("public_operation_not_queued")
    now = get_current_utc_datetime()
    _assert_deadline_remaining(admission.deadline_at, now=now)
    policy = await load_public_policy(session, admission.policy_version)
    counters = await _locked_phase2_counters(
        session,
        policy_version=admission.policy_version,
        owner_id=admission.owner_id,
        tenant_id=admission.tenant_id,
        operation_kind=admission.operation_kind,
    )
    leases = await _locked_phase2_leases(session, lease_model=lease_model, admission=admission)
    for scope_type in ("global", "tenant", "owner"):
        counter = counters[scope_type]
        active_count_limit, _ = _quota_limits(
            policy, scope_type=scope_type, operation_kind=admission.operation_kind
        )
        _, active_resource_limit = _resource_limits(policy, scope_type=scope_type)
        if counter.active_count >= active_count_limit:
            raise PublicAdmissionRejected(f"public_{scope_type}_active_limit_exceeded")
        if counter.active_resource_units + admission.resource_units > active_resource_limit:
            raise PublicAdmissionRejected(f"public_{scope_type}_active_resource_limit_exceeded")
        if (
            counter.queued_count < 1
            or counter.queued_resource_units < admission.resource_units
        ):
            raise PublicAdmissionRejected("public_quota_counter_underflow")
        counter.queued_count -= 1
        counter.queued_resource_units -= admission.resource_units
        counter.active_count += 1
        counter.active_resource_units += admission.resource_units
        counter.last_activated_at = now
        counter.updated_at = now
        counter.version += 1
    expires_at = min(
        _utc(admission.deadline_at, "admission_deadline_at"),
        _utc(now, "activation_now") + timedelta(seconds=duration),
    )
    for lease in leases:
        if lease.state != "queued" or lease.fence != admission.fence:
            raise PublicAdmissionRejected("public_resource_lease_conflict")
        lease.state = "leased"
        lease.attempt = (
            _nonnegative(attempt, "attempt") if attempt is not None else lease.attempt + 1
        )
        if lease.attempt < 1:
            raise PublicAdmissionRejected("public_resource_lease_attempt_invalid")
        lease.lease_owner_hash = lease_owner_hash
        lease.leased_at = now
        lease.heartbeat_at = now
        lease.lease_expires_at = expires_at
        lease.version += 1
    admission.state = "active"
    admission.activated_at = now
    admission.updated_at = now
    operation.phase = "active"
    operation.fencing_token = admission.fence
    await session.flush()
    return admission


async def requeue_public_operation_v2(
    session: AsyncSession,
    *,
    admission_id: uuid.UUID,
    expected_fence: int,
    expected_attempt: int | None = None,
) -> PublicOperationAdmission:
    """Return an active admission to queue without losing its resource reservation."""

    lease_model = await _resolved_phase2_authority(session)
    admission = await session.scalar(
        select(PublicOperationAdmission)
        .where(PublicOperationAdmission.id == admission_id)
        .with_for_update()
    )
    if admission is None or admission.state != "active":
        raise PublicAdmissionRejected("public_operation_not_active")
    if admission.fence != _nonnegative(expected_fence, "expected_fence"):
        raise PublicAdmissionRejected("public_operation_fence_conflict")
    now = get_current_utc_datetime()
    _assert_deadline_remaining(admission.deadline_at, now=now)
    workspace_model = _require_phase2_workspace_model()
    operation = await session.scalar(
        select(workspace_model)
        .where(workspace_model.id == admission.operation_id)
        .with_for_update()
    )
    if operation is None or operation.fencing_token != expected_fence or _utc(
        operation.deadline_at, "operation_deadline_at"
    ) != _utc(admission.deadline_at, "admission_deadline_at"):
        raise PublicAdmissionRejected("public_operation_fence_conflict")
    counters = await _locked_phase2_counters(
        session,
        policy_version=admission.policy_version,
        owner_id=admission.owner_id,
        tenant_id=admission.tenant_id,
        operation_kind=admission.operation_kind,
    )
    leases = await _locked_phase2_leases(session, lease_model=lease_model, admission=admission)
    next_fence = admission.fence + 1
    for scope_type in ("global", "tenant", "owner"):
        counter = counters[scope_type]
        if (
            counter.active_count < 1
            or counter.active_resource_units < admission.resource_units
        ):
            raise PublicAdmissionRejected("public_quota_counter_underflow")
        counter.active_count -= 1
        counter.active_resource_units -= admission.resource_units
        counter.queued_count += 1
        counter.queued_resource_units += admission.resource_units
        counter.updated_at = now
        counter.version += 1
    for lease in leases:
        if (
            lease.state != "leased"
            or lease.fence != expected_fence
            or (expected_attempt is not None and lease.attempt != expected_attempt)
        ):
            raise PublicAdmissionRejected("public_resource_lease_fence_conflict")
        lease.state = "queued"
        lease.fence = next_fence
        lease.lease_owner_hash = None
        lease.lease_expires_at = admission.deadline_at
        lease.version += 1
    admission.state = "queued"
    admission.fence = next_fence
    admission.updated_at = now
    operation.fencing_token = next_fence
    operation.phase = "queued"
    await session.flush()
    return admission


async def finish_public_operation_v2(
    session: AsyncSession,
    *,
    admission_id: uuid.UUID,
    state: str,
    expected_fence: int | None = None,
    expected_attempt: int | None = None,
) -> PublicOperationAdmission:
    """Release every count/resource lease with the same fence that owned it."""

    if state not in _TERMINAL_STATES:
        raise ValueError("public_terminal_state_invalid")
    lease_model = await _resolved_phase2_authority(session)
    admission = await session.scalar(
        select(PublicOperationAdmission)
        .where(PublicOperationAdmission.id == admission_id)
        .with_for_update()
    )
    if admission is None:
        raise PublicAdmissionRejected("public_operation_not_found")
    if admission.state in _TERMINAL_STATES:
        if admission.state != state:
            raise PublicAdmissionRejected("public_operation_terminal_conflict")
        return admission
    workspace_model = _require_phase2_workspace_model()
    operation = await session.scalar(
        select(workspace_model)
        .where(workspace_model.id == admission.operation_id)
        .with_for_update()
    )
    if operation is None or operation.fencing_token != admission.fence or _utc(
        operation.deadline_at, "operation_deadline_at"
    ) != _utc(admission.deadline_at, "admission_deadline_at"):
        raise PublicAdmissionRejected("public_operation_fence_conflict")
    if admission.state == "active":
        if expected_fence is None or admission.fence != _nonnegative(
            expected_fence, "expected_fence"
        ):
            raise PublicAdmissionRejected("public_operation_fence_conflict")
        source_count, source_resource = "active_count", "active_resource_units"
        expected_lease_state = "leased"
    else:
        # Queued cancellation is permitted before a worker owns a fence.
        if expected_fence is not None and admission.fence != _nonnegative(
            expected_fence, "expected_fence"
        ):
            raise PublicAdmissionRejected("public_operation_fence_conflict")
        source_count, source_resource = "queued_count", "queued_resource_units"
        expected_lease_state = "queued"
    now = get_current_utc_datetime()
    counters = await _locked_phase2_counters(
        session,
        policy_version=admission.policy_version,
        owner_id=admission.owner_id,
        tenant_id=admission.tenant_id,
        operation_kind=admission.operation_kind,
    )
    leases = await _locked_phase2_leases(session, lease_model=lease_model, admission=admission)
    for scope_type in ("global", "tenant", "owner"):
        counter = counters[scope_type]
        if (
            getattr(counter, source_count) < 1
            or getattr(counter, source_resource) < admission.resource_units
        ):
            raise PublicAdmissionRejected("public_quota_counter_underflow")
        setattr(counter, source_count, getattr(counter, source_count) - 1)
        setattr(
            counter,
            source_resource,
            getattr(counter, source_resource) - admission.resource_units,
        )
        counter.updated_at = now
        counter.version += 1
    for lease in leases:
        if lease.state != expected_lease_state or (
            expected_fence is not None and lease.fence != expected_fence
        ) or (expected_attempt is not None and lease.attempt != expected_attempt):
            raise PublicAdmissionRejected("public_resource_lease_fence_conflict")
        lease.state = (
            "cancelled"
            if state == "cancelled"
            else "expired"
            if state == "deadline_exceeded"
            else "released"
        )
        lease.released_at = now
        lease.lease_owner_hash = None
        lease.version += 1
    admission.state = state
    admission.terminal_at = now
    admission.updated_at = now
    operation.phase = state
    operation.fencing_token = admission.fence
    await session.flush()
    return admission


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
    if admission is not None and admission.state == "budget_exhausted":
        raise PublicAdmissionRejected("public_provider_actual_cost_limit_exceeded")
    if admission is None or admission.state != "active":
        raise PublicAdmissionRejected("public_provider_operation_not_active")
    deadline_at = getattr(admission, "deadline_at", None)
    if deadline_at is not None:
        _assert_deadline_remaining(deadline_at, now=get_current_utc_datetime())
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
    admission = await session.scalar(
        select(PublicOperationAdmission)
        .where(PublicOperationAdmission.id == call.admission_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if admission is None:
        raise RuntimeError("public_provider_admission_missing_after_reconcile")
    if (
        admission.actual_consumed_microunits
        + admission.actual_reserved_microunits
        > admission.actual_cost_limit_microunits
        and admission.state == "active"
    ):
        if (
            getattr(admission, "policy_digest", None)
            and getattr(admission, "deadline_at", None) is not None
            and int(getattr(admission, "fence", 0)) >= 1
        ):
            await finish_public_operation_v2(
                session,
                admission_id=admission.id,
                state="budget_exhausted",
                expected_fence=admission.fence,
            )
        else:
            await finish_public_operation(
                session,
                admission.id,
                state="budget_exhausted",
            )
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
        admission = await session.scalar(
            select(PublicOperationAdmission)
            .where(PublicOperationAdmission.id == admission_id)
            .with_for_update()
        )
        if admission is None:
            raise PublicAdmissionRejected("public_provider_admission_missing")
        await _require_phase2_provider_contract(session, admission)
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
        await _resolved_phase2_authority(session)
        await reconcile_public_provider_call(
            session,
            logical_call_id=lease.logical_call_id,
            outcome=outcome,
            latency_ms=latency_ms,
            actual_cost_microunits=actual_cost_microunits,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        admission = await session.get(PublicOperationAdmission, lease.admission_id)
        if admission is None:
            raise PublicAdmissionRejected("public_provider_admission_missing")
        await _require_phase2_provider_contract(
            session,
            admission,
            allow_terminal_reconcile=True,
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
    "WeightedResourcePlan",
    "PublicAdmissionRejected",
    "PublicProviderCallLease",
    "activate_public_operation",
    "activate_public_operation_v2",
    "admit_public_operation",
    "admit_public_operation_v2",
    "claim_next_public_operation",
    "current_principal_context",
    "enrich_public_provider_call",
    "enrich_public_provider_call_detached",
    "enrich_public_provider_usage_detached",
    "finish_public_operation",
    "load_public_policy",
    "plan_provider_requests",
    "plan_weighted_resources",
    "public_operation_metrics",
    "public_operation_observability_metrics",
    "public_prometheus_metrics_text",
    "public_quota_metrics",
    "reconcile_orphaned_public_provider_calls",
    "reconcile_public_provider_call",
    "reconcile_public_provider_call_detached",
    "requeue_public_operation_v2",
    "reserve_public_provider_call",
    "reserve_public_provider_call_detached",
    "finish_public_operation_v2",
]
