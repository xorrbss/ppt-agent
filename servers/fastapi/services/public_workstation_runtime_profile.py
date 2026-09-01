"""Executable, evidence-gated capacity profile for one 128 GiB workstation.

The database policy remains the durable authority.  This module only narrows
that policy to the slots this workstation can execute right now; it never
raises a policy limit.  A caller enables the profile explicitly with
``PUBLIC_RUNTIME_PROFILE=workstation-128g/v1``.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Mapping


PROFILE_VERSION = "workstation-128g/v1"
GIB = 1024**3
_EVIDENCE_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_RC_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")


class PublicWorkstationProfileError(RuntimeError):
    """A configured runtime profile cannot safely admit new work."""


@dataclass(frozen=True, slots=True)
class CapacityTier:
    name: str
    generation_slots: int
    generation_queue_units: int
    render_export_slots: int


VERIFICATION = CapacityTier("verification", 4, 16, 4)
SERVICE_DEFAULT = CapacityTier("service-default", 8, 32, 8)
NORMAL = CapacityTier("normal", 12, 48, 8)
BURST = CapacityTier("burst", 16, 64, 12)
_TIERS = {tier.name: tier for tier in (VERIFICATION, SERVICE_DEFAULT, NORMAL, BURST)}


@dataclass(frozen=True, slots=True)
class DatabasePoolBudget:
    """Maximum PostgreSQL connections across the role-separated process set."""

    api_maximum: int = 16
    generation_controller_maximum: int = 12
    export_controller_maximum: int = 10
    observation_maximum: int = 4
    reserved_operator_connections: int = 10

    @property
    def service_maximum(self) -> int:
        return (
            self.api_maximum
            + self.generation_controller_maximum
            + self.export_controller_maximum
            + self.observation_maximum
            + self.reserved_operator_connections
        )

    @property
    def burst_maximum(self) -> int:
        # Eight generation controllers use pool=2 plus overflow=1 at burst.
        return self.service_maximum + 12


@dataclass(frozen=True, slots=True)
class RuntimePressure:
    available_memory_bytes: int | None = None
    cpu_percent: int | None = None
    system_volume_free_percent: int | None = None
    system_volume_free_bytes: int | None = None

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> "RuntimePressure":
        return cls(
            available_memory_bytes=_optional_int(environ, "PPT_RUNTIME_AVAILABLE_MEMORY_BYTES"),
            cpu_percent=_optional_percent(environ, "PPT_RUNTIME_CPU_PERCENT"),
            system_volume_free_percent=_optional_percent(
                environ, "PPT_RUNTIME_SYSTEM_VOLUME_FREE_PERCENT"
            ),
            system_volume_free_bytes=_optional_int(
                environ, "PPT_RUNTIME_SYSTEM_VOLUME_FREE_BYTES"),
        )

    @property
    def is_hard(self) -> bool:
        return bool(
            (self.available_memory_bytes is not None and self.available_memory_bytes < 16 * GIB)
            or (self.cpu_percent is not None and self.cpu_percent > 90)
            or (self.system_volume_free_percent is not None and self.system_volume_free_percent < 10)
            or (self.system_volume_free_bytes is not None and self.system_volume_free_bytes < 100 * GIB)
        )

    @property
    def is_soft(self) -> bool:
        return bool(
            self.is_hard
            or (self.available_memory_bytes is not None and self.available_memory_bytes < 24 * GIB)
            or (self.cpu_percent is not None and self.cpu_percent > 80)
            or (self.system_volume_free_percent is not None and self.system_volume_free_percent < 20)
        )

    @property
    def limiting_reason(self) -> str | None:
        if self.is_hard:
            return "hard_resource_pressure"
        if self.is_soft:
            return "soft_resource_pressure"
        return None


@dataclass(frozen=True, slots=True)
class RuntimeAdmissionCapacity:
    generation_active_slots: int
    generation_queue_units: int
    render_export_active_slots: int
    limiting_reason: str | None


@dataclass(frozen=True, slots=True)
class WorkstationRuntimeProfile:
    version: str
    tier: CapacityTier
    generation_executable_slots: int
    export_executable_slots: int
    generation_provider_cap: int
    export_provider_cap: int
    pressure: RuntimePressure
    database_pool_budget: DatabasePoolBudget = DatabasePoolBudget()
    minimum_physical_memory_gib: int = 120
    owner_generation_active: int = 1
    owner_generation_queued: int = 3
    owner_export_queued: int = 6
    powerpoint_com_concurrency: int = 1

    @property
    def owner_export_active(self) -> int:
        # Verification keeps the previous single-owner export allowance. C5
        # is the first evidence-gated point at which two active exports apply.
        return 1 if self.tier is VERIFICATION else 2

    def admission_capacity(self, operation_kind: str) -> RuntimeAdmissionCapacity:
        if operation_kind not in {"generation", "export"}:
            raise ValueError("public_operation_kind_invalid")
        configured = (
            self.tier.generation_slots
            if operation_kind == "generation"
            else self.tier.render_export_slots
        )
        provider = (
            self.generation_provider_cap
            if operation_kind == "generation"
            else self.export_provider_cap
        )
        executable = (
            self.generation_executable_slots
            if operation_kind == "generation"
            else self.export_executable_slots
        )
        effective = min(configured, executable, provider)
        reason = (
            "executable_slot_cap"
            if executable == effective and executable < configured
            else "provider_cap"
            if provider == effective and provider < configured
            else None
        )
        if self.pressure.is_hard:
            return RuntimeAdmissionCapacity(0, 0, 0, self.pressure.limiting_reason)
        if self.pressure.is_soft:
            effective = min(effective, VERIFICATION.generation_slots if operation_kind == "generation" else VERIFICATION.render_export_slots)
            reason = self.pressure.limiting_reason
        queue_units = self.tier.generation_queue_units if operation_kind == "generation" else self.tier.render_export_slots * 4
        return RuntimeAdmissionCapacity(
            generation_active_slots=effective if operation_kind == "generation" else 0,
            generation_queue_units=queue_units,
            render_export_active_slots=effective if operation_kind == "export" else 0,
            limiting_reason=reason,
        )


def resolve_enabled_workstation_runtime_profile(
    environ: Mapping[str, str] | None = None,
) -> WorkstationRuntimeProfile | None:
    environment = os.environ if environ is None else environ
    selected = environment.get("PUBLIC_RUNTIME_PROFILE", "").strip()
    if not selected:
        return None
    if selected != PROFILE_VERSION:
        raise PublicWorkstationProfileError("public_runtime_profile_invalid")
    physical_memory = _positive_int(environment, "PPT_RUNTIME_PHYSICAL_MEMORY_BYTES")
    if physical_memory < 120 * GIB:
        raise PublicWorkstationProfileError("public_runtime_physical_memory_insufficient")
    tier = _resolve_tier(environment)
    return WorkstationRuntimeProfile(
        version=PROFILE_VERSION,
        tier=tier,
        generation_executable_slots=_positive_int(
            environment, "PUBLIC_EXECUTABLE_GENERATION_SLOTS"
        ),
        export_executable_slots=_positive_int(
            environment, "PUBLIC_EXECUTABLE_EXPORT_SLOTS"
        ),
        generation_provider_cap=_positive_int(
            environment, "PUBLIC_PROVIDER_GENERATION_CONCURRENCY_CAP"
        ),
        export_provider_cap=_positive_int(
            environment, "PUBLIC_PROVIDER_EXPORT_CONCURRENCY_CAP"
        ),
        pressure=RuntimePressure.from_environment(environment),
    )


def _resolve_tier(environ: Mapping[str, str]) -> CapacityTier:
    name = environ.get("PUBLIC_RUNTIME_CAPACITY_TIER", VERIFICATION.name).strip().lower()
    try:
        tier = _TIERS[name]
    except KeyError as error:
        raise PublicWorkstationProfileError("public_runtime_capacity_tier_invalid") from error
    if tier is SERVICE_DEFAULT:
        _require_same_rc_evidence(environ, "C5")
    elif tier is NORMAL:
        _require_same_rc_evidence(environ, "C5")
        _require_same_rc_evidence(environ, "C10")
    elif tier is BURST:
        _require_same_rc_evidence(environ, "C5")
        _require_same_rc_evidence(environ, "C10")
        _require_same_rc_evidence(environ, "C16")
        _require_evidence(environ, "PUBLIC_RUNTIME_RECOVERY_EVIDENCE_DIGEST")
        if _positive_int(environ, "PUBLIC_RUNTIME_RECOVERY_SECONDS") < 86_400:
            raise PublicWorkstationProfileError("public_runtime_c16_recovery_evidence_required")
    return tier


def _require_evidence(environ: Mapping[str, str], key: str) -> None:
    if not _EVIDENCE_DIGEST.fullmatch(environ.get(key, "").strip().lower()):
        raise PublicWorkstationProfileError(f"public_runtime_evidence_required:{key}")


def _require_same_rc_evidence(environ: Mapping[str, str], cell: str) -> None:
    _require_evidence(environ, f"PUBLIC_RUNTIME_{cell}_EVIDENCE_DIGEST")
    release_candidate = environ.get(
        "PUBLIC_RUNTIME_RELEASE_CANDIDATE_DIGEST", ""
    ).strip().lower()
    observed_candidate = environ.get(
        f"PUBLIC_RUNTIME_{cell}_RC_DIGEST", ""
    ).strip().lower()
    if (
        _RC_DIGEST.fullmatch(release_candidate) is None
        or observed_candidate != release_candidate
    ):
        raise PublicWorkstationProfileError(
            f"public_runtime_same_rc_evidence_required:{cell}"
        )


def _positive_int(environ: Mapping[str, str], key: str) -> int:
    value = _optional_int(environ, key)
    if value is None or value < 1:
        raise PublicWorkstationProfileError(f"public_runtime_positive_integer_required:{key}")
    return value


def _optional_int(environ: Mapping[str, str], key: str) -> int | None:
    raw = environ.get(key, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as error:
        raise PublicWorkstationProfileError(f"public_runtime_integer_invalid:{key}") from error
    if value < 0:
        raise PublicWorkstationProfileError(f"public_runtime_integer_invalid:{key}")
    return value


def _optional_percent(environ: Mapping[str, str], key: str) -> int | None:
    value = _optional_int(environ, key)
    if value is not None and value > 100:
        raise PublicWorkstationProfileError(f"public_runtime_percent_invalid:{key}")
    return value


__all__ = [
    "BURST",
    "NORMAL",
    "PROFILE_VERSION",
    "SERVICE_DEFAULT",
    "VERIFICATION",
    "CapacityTier",
    "DatabasePoolBudget",
    "PublicWorkstationProfileError",
    "RuntimeAdmissionCapacity",
    "RuntimePressure",
    "WorkstationRuntimeProfile",
    "resolve_enabled_workstation_runtime_profile",
]
