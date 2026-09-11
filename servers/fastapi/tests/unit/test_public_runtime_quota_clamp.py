"""Global active quota is bounded by the knobs that actually run the work.

Without a workstation runtime profile (``PUBLIC_RUNTIME_PROFILE`` unset, the
default), admission used to take the policy row's ``global_active_*`` columns
verbatim.  The row is a deployment artefact; the process knobs are what bound
execution: ``AUTHORED_CLUSTER_JOB_CONCURRENCY`` for generation and
``PUBLIC_EXPORT_SCHEDULER_GLOBAL_CAP`` for export.  Admitting more than they
can run only grows the queue behind them, so the global active limit is
``min(policy, knob)`` when no profile is selected.  A selected profile keeps
its own (evidence-backed) clamp and ignores the knobs.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from models.sql.public_admission import PublicPolicyProfile
from services import public_operation_admission
from services.public_operation_admission import _effective_runtime_quota_limits


_KNOBS = (
    "PUBLIC_RUNTIME_PROFILE",
    "AUTHORED_CLUSTER_JOB_CONCURRENCY",
    "AUTHORED_JOB_CONCURRENCY",
    "PUBLIC_EXPORT_SCHEDULER_GLOBAL_CAP",
)


@pytest.fixture(autouse=True)
def _no_profile_and_no_knobs(monkeypatch):
    for knob in _KNOBS:
        monkeypatch.delenv(knob, raising=False)


def _policy(*, global_active_generation: int, global_active_export: int) -> PublicPolicyProfile:
    return PublicPolicyProfile(
        version="clamp-test/v1",
        max_slides=100,
        global_active_generation=global_active_generation,
        global_queued_generation=8,
        owner_active_generation=1,
        owner_queued_generation=4,
        tenant_active_generation=2,
        tenant_queued_generation=8,
        global_active_export=global_active_export,
        global_queued_export=8,
        owner_active_export=1,
        owner_queued_export=4,
        tenant_active_export=2,
        tenant_queued_export=8,
        global_active_resource_units=100,
        global_queued_resource_units=100,
        tenant_active_resource_units=50,
        tenant_queued_resource_units=50,
        owner_active_resource_units=25,
        owner_queued_resource_units=25,
        retry_request_limit=1,
        estimated_cost_limit_microunits=13,
        actual_cost_limit_microunits=12,
        generation_deadline_seconds=900,
        export_deadline_seconds=1800,
    )


def test_global_active_is_clamped_by_the_knobs_when_no_profile_is_selected(
    monkeypatch,
) -> None:
    policy = _policy(global_active_generation=4, global_active_export=4)
    monkeypatch.setenv("AUTHORED_CLUSTER_JOB_CONCURRENCY", "2")
    monkeypatch.setenv("PUBLIC_EXPORT_SCHEDULER_GLOBAL_CAP", "3")

    assert _effective_runtime_quota_limits(
        policy, scope_type="global", operation_kind="generation"
    ) == (2, 8)
    assert _effective_runtime_quota_limits(
        policy, scope_type="global", operation_kind="export"
    ) == (3, 8)
    # Owner and tenant scopes are not process-bound; they keep the row.
    assert _effective_runtime_quota_limits(
        policy, scope_type="owner", operation_kind="generation"
    ) == (1, 4)
    assert _effective_runtime_quota_limits(
        policy, scope_type="tenant", operation_kind="export"
    ) == (2, 8)


def test_knob_defaults_and_a_wider_knob_never_raise_the_row(monkeypatch) -> None:
    # Unset knobs fall back to their code defaults (1 job, 4 export slots).
    policy = _policy(global_active_generation=4, global_active_export=8)
    assert _effective_runtime_quota_limits(
        policy, scope_type="global", operation_kind="generation"
    ) == (1, 8)
    assert _effective_runtime_quota_limits(
        policy, scope_type="global", operation_kind="export"
    ) == (4, 8)
    # min(policy, knob): a knob wider than the row cannot over-admit it.
    monkeypatch.setenv("AUTHORED_CLUSTER_JOB_CONCURRENCY", "16")
    monkeypatch.setenv("PUBLIC_EXPORT_SCHEDULER_GLOBAL_CAP", "64")
    assert _effective_runtime_quota_limits(
        policy, scope_type="global", operation_kind="generation"
    ) == (4, 8)
    assert _effective_runtime_quota_limits(
        policy, scope_type="global", operation_kind="export"
    ) == (8, 8)


def test_a_selected_profile_keeps_its_own_clamp_and_ignores_the_knobs(
    monkeypatch,
) -> None:
    capacity = SimpleNamespace(
        generation_active_slots=3,
        render_export_active_slots=3,
        generation_queue_units=5,
    )
    profile = SimpleNamespace(
        admission_capacity=lambda _kind: capacity,
        owner_generation_active=1,
        owner_generation_queued=2,
        owner_export_active=1,
        owner_export_queued=2,
    )
    monkeypatch.setattr(
        public_operation_admission,
        "resolve_enabled_workstation_runtime_profile",
        lambda: profile,
    )
    monkeypatch.setenv("AUTHORED_CLUSTER_JOB_CONCURRENCY", "1")
    monkeypatch.setenv("PUBLIC_EXPORT_SCHEDULER_GLOBAL_CAP", "1")
    policy = _policy(global_active_generation=4, global_active_export=4)
    assert _effective_runtime_quota_limits(
        policy, scope_type="global", operation_kind="generation"
    ) == (3, 5)
    assert _effective_runtime_quota_limits(
        policy, scope_type="global", operation_kind="export"
    ) == (3, 5)
