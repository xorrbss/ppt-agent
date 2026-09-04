"""Focused executable contract for the single-node public capacity profile."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import QueuePool

from services.public_operation_admission import (
    PublicAdmissionRejected,
    _effective_runtime_quota_limits,
    _effective_runtime_resource_limits,
)
from services.public_render_batching import pooled_render_authority_admission
from services.public_workstation_runtime_profile import (
    PROFILE_VERSION,
    PublicWorkstationProfileError,
    resolve_enabled_workstation_runtime_profile,
)


_DIGEST = "a" * 64
_RC = "sha256:" + "b" * 64


def _environment(*, tier: str = "verification", **extra: str) -> dict[str, str]:
    return {
        "PUBLIC_RUNTIME_PROFILE": PROFILE_VERSION,
        "PUBLIC_RUNTIME_CAPACITY_TIER": tier,
        "PPT_RUNTIME_PHYSICAL_MEMORY_BYTES": str(128 * 1024**3),
        "PUBLIC_EXECUTABLE_GENERATION_SLOTS": "30",
        "PUBLIC_EXECUTABLE_EXPORT_SLOTS": "30",
        "PUBLIC_PROVIDER_GENERATION_CONCURRENCY_CAP": "30",
        "PUBLIC_PROVIDER_EXPORT_CONCURRENCY_CAP": "30",
        "PUBLIC_RUNTIME_RELEASE_CANDIDATE_DIGEST": _RC,
        **extra,
    }


def _evidence(cell: str) -> dict[str, str]:
    return {
        f"PUBLIC_RUNTIME_{cell}_EVIDENCE_DIGEST": _DIGEST,
        f"PUBLIC_RUNTIME_{cell}_RC_DIGEST": _RC,
    }


def _policy() -> SimpleNamespace:
    values: dict[str, int] = {}
    for scope in ("global", "tenant", "owner"):
        values[f"{scope}_active_generation"] = 20
        values[f"{scope}_queued_generation"] = 60
        values[f"{scope}_active_export"] = 20
        values[f"{scope}_queued_export"] = 60
        values[f"{scope}_queued_resource_units"] = 20_000
        values[f"{scope}_active_resource_units"] = 20_000
    return SimpleNamespace(**values)


def test_workstation_128g_profile_gates_tiers_and_clamps_admission(monkeypatch):
    verification = resolve_enabled_workstation_runtime_profile(_environment())
    assert verification is not None
    assert verification.tier.name == "verification"
    assert verification.admission_capacity("generation").generation_active_slots == 4
    assert verification.admission_capacity("generation").generation_queue_units == 16
    assert verification.admission_capacity("export").render_export_active_slots == 4
    assert verification.owner_export_active == 1
    assert verification.powerpoint_com_concurrency == 1
    assert verification.database_pool_budget.service_maximum == 52
    assert verification.database_pool_budget.burst_maximum == 64

    with pytest.raises(PublicWorkstationProfileError, match="C5_EVIDENCE"):
        resolve_enabled_workstation_runtime_profile(_environment(tier="service-default"))
    service = resolve_enabled_workstation_runtime_profile(
        _environment(tier="service-default", **_evidence("C5"))
    )
    assert service is not None
    assert service.admission_capacity("generation").generation_active_slots == 8
    assert service.admission_capacity("generation").generation_queue_units == 32
    assert service.admission_capacity("export").render_export_active_slots == 8
    assert service.owner_export_active == 2

    with pytest.raises(PublicWorkstationProfileError, match="C10_EVIDENCE"):
        resolve_enabled_workstation_runtime_profile(
            _environment(tier="normal", **_evidence("C5"))
        )
    normal = resolve_enabled_workstation_runtime_profile(
        _environment(
            tier="normal",
            **_evidence("C5"),
            **_evidence("C10"),
        )
    )
    assert normal is not None and normal.admission_capacity("generation").generation_active_slots == 12

    with pytest.raises(PublicWorkstationProfileError, match="recovery_evidence"):
        resolve_enabled_workstation_runtime_profile(
            _environment(
                tier="burst",
                **_evidence("C5"),
                **_evidence("C10"),
                **_evidence("C16"),
                PUBLIC_RUNTIME_RECOVERY_EVIDENCE_DIGEST=_DIGEST,
                PUBLIC_RUNTIME_RECOVERY_SECONDS="86399",
            )
        )
    burst = resolve_enabled_workstation_runtime_profile(
        _environment(
            tier="burst",
            **_evidence("C5"),
            **_evidence("C10"),
            **_evidence("C16"),
            PUBLIC_RUNTIME_RECOVERY_EVIDENCE_DIGEST=_DIGEST,
            PUBLIC_RUNTIME_RECOVERY_SECONDS="86400",
        )
    )
    assert burst is not None
    assert burst.admission_capacity("generation").generation_active_slots == 16
    assert burst.admission_capacity("generation").generation_queue_units == 64
    assert burst.admission_capacity("export").render_export_active_slots == 12

    for key, value in _environment(
        tier="service-default",
        **_evidence("C5"),
        PPT_RUNTIME_AVAILABLE_MEMORY_BYTES=str(15 * 1024**3),
    ).items():
        monkeypatch.setenv(key, value)
    profile = resolve_enabled_workstation_runtime_profile()
    assert profile is not None and profile.admission_capacity("generation").generation_active_slots == 0

    for key, value in _environment(
        tier="service-default",
        **_evidence("C5"),
    ).items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("PPT_RUNTIME_AVAILABLE_MEMORY_BYTES")
    policy = _policy()
    assert _effective_runtime_quota_limits(
        policy, scope_type="global", operation_kind="generation"
    ) == (8, 32)
    assert _effective_runtime_quota_limits(
        policy, scope_type="owner", operation_kind="generation"
    ) == (1, 3)
    assert _effective_runtime_quota_limits(
        policy, scope_type="owner", operation_kind="export"
    ) == (2, 6)
    assert _effective_runtime_resource_limits(
        policy, scope_type="global", operation_kind="generation"
    ) == (32, 32)

    class Bind:
        dialect = SimpleNamespace(name="postgresql")

        def __init__(self) -> None:
            self.pool = QueuePool(lambda: None, pool_size=5, max_overflow=10)

    async def assert_render_pool_is_profile_clamped() -> None:
        assert pooled_render_authority_admission(Bind())._slots._value == 8

    asyncio.run(assert_render_pool_is_profile_clamped())

    monkeypatch.setenv("PPT_RUNTIME_AVAILABLE_MEMORY_BYTES", str(15 * 1024**3))
    assert _effective_runtime_quota_limits(
        policy, scope_type="global", operation_kind="generation"
    ) == (0, 0)
    with pytest.raises(PublicAdmissionRejected, match="public_runtime"):
        # Invalid selected profile must never fall back to a permissive policy.
        monkeypatch.setenv("PUBLIC_PROVIDER_GENERATION_CONCURRENCY_CAP", "0")
        _effective_runtime_quota_limits(
            policy, scope_type="global", operation_kind="generation"
        )
