from __future__ import annotations

from services.public_export_operation import _resource_summary
from services.trusted_executor.isolation_policy import (
    EnforcementStatus,
    IsolationControl,
    PlatformIsolationReport,
    probe_platform_isolation,
)


def _sample(attestation: dict[str, object]) -> dict[str, object]:
    return {
        "role": "render",
        "enforcementLayer": "test-launcher",
        "resourceLimitsEnforced": True,
        "limits": {},
        "recycledWithinBaseline": True,
        "finalProcessCount": 0,
        "isolationAttestation": attestation,
    }


def test_windows_job_object_report_never_claims_network_none_provenance() -> None:
    attestation = probe_platform_isolation(platform="nt").to_attestation()

    summary = _resource_summary([_sample(attestation)])

    assert summary["network_none_worker"] is False
    assert summary["worker_isolation_attestations"] == [attestation]
    assert attestation["productionEligible"] is False
    assert attestation["controls"]["network-none"]["status"] == "unsupported"


def test_network_none_provenance_requires_complete_launcher_attestation() -> None:
    report = PlatformIsolationReport(
        platform="test",
        backend="verified-test-launcher",
        resource_limits_supported=True,
        resource_limits_reason="test launcher evidence",
        controls=tuple(
            (control, EnforcementStatus.ENFORCED, "test launcher evidence")
            for control in IsolationControl
        ),
    )

    summary = _resource_summary([_sample(report.to_attestation())])

    assert summary["network_none_worker"] is True
