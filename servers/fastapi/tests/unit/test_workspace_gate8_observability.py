import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import yaml

from api.v1.ppt.endpoints import workspace as workspace_endpoint
from services.workspace_metrics import WorkspaceMetrics
from services.workspace_shadow_parity import (
    observe_workspace_timeline_shadow_parity,
)


PRESENTATION_ID = uuid.UUID("00000000-0000-0000-0000-000000000801")
CONVERSATION_ID = uuid.UUID("00000000-0000-0000-0000-000000000802")


def _shadow_timeline() -> dict[str, object]:
    return {
        "conversation_id": str(CONVERSATION_ID),
        "items": [
            {
                "anchor_type": "message",
                "position": 1,
                "role": "user",
                "content": "Make the title concise",
            },
            {
                "anchor_type": "operation",
                "position": 2,
                "operation_type": "slide.edit",
                "status": "succeeded",
                "result": {"summary": "Updated the title"},
                "events": [{"type": "operation.result"}],
            },
        ],
    }


def test_named_dead_letter_lease_and_conflict_slos_are_independent(monkeypatch):
    metrics = WorkspaceMetrics()
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_SLO_DEAD_LETTER_RATIO", "0")
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_SLO_LEASE_LOST_RATIO", "0")
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_SLO_REVISION_CONFLICT_RATIO", "0")

    for command_type in (
        "presentation.generate",
        "presentation.edit",
        "slide.edit",
    ):
        metrics.record_operation_admission(command_type)
    metrics.observe_operation(
        {"name": "dead_letter", "result_status": "dead_letter"}
    )
    metrics.observe_operation(
        {"name": "lease_lost", "fence_outcome": "lease_lost"}
    )
    metrics.observe_operation(
        {"name": "failed", "error_code": "stale_presentation_revision"}
    )

    status = metrics.evaluate_slo()
    assert status.dead_letter_total == 1
    assert status.dead_letter_ratio == 1 / 3
    assert status.lease_lost_total == 1
    assert status.lease_lost_ratio == 1 / 3
    assert status.revision_conflict_total == 1
    assert status.revision_conflict_ratio == 1 / 3
    assert {
        "workspace_dead_letter",
        "workspace_lease_lost",
        "workspace_revision_conflict",
    }.issubset(status.violations)


def test_prometheus_exposes_named_gate8_counters():
    metrics = WorkspaceMetrics()
    metrics.record_operation_admission("presentation.edit")
    metrics.observe_operation(
        {"name": "dead_letter", "result_status": "dead_letter"}
    )
    metrics.observe_operation(
        {"name": "lease_lost", "fence_outcome": "lease_lost"}
    )
    metrics.observe_operation(
        {"name": "failed", "error_code": "stale_presentation_revision"}
    )
    metrics.record_cleanup_outcome("claimed")
    metrics.record_cleanup_outcome("dead_letter")
    metrics.record_signal_failure("shadow_parity")
    metrics.record_signal_failure("provider-specific-secret")

    exposition = metrics.prometheus_text()
    assert "# TYPE workspace_operation_admission_total counter" in exposition
    assert (
        'workspace_operation_admission_total{command="presentation.edit"} 1'
        in exposition
    )
    assert "# TYPE workspace_dead_letter_total counter" in exposition
    assert "workspace_dead_letter_total 1" in exposition
    assert "# TYPE workspace_lease_lost_total counter" in exposition
    assert "workspace_lease_lost_total 1" in exposition
    assert "# TYPE workspace_revision_conflict_total counter" in exposition
    assert "workspace_revision_conflict_total 1" in exposition
    assert 'workspace_cleanup_total{outcome="claimed"} 1' in exposition
    assert "workspace_cleanup_dead_letter_total 1" in exposition
    assert (
        'workspace_observability_signal_failure_total{signal="shadow_parity"} 1'
        in exposition
    )
    assert (
        'workspace_observability_signal_failure_total{signal="other"} 1'
        in exposition
    )


def test_pre_operation_revision_conflict_uses_admission_denominator(monkeypatch):
    metrics = WorkspaceMetrics()
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_SLO_REVISION_CONFLICT_RATIO", "0.5")
    metrics.record_operation_admission("presentation.edit")
    metrics.record_revision_conflict()

    status = metrics.evaluate_slo()
    assert status.revision_conflict_total == 1
    assert status.revision_conflict_ratio == 1.0
    assert "workspace_revision_conflict" in status.violations


def test_cleanup_dead_letter_slo_uses_claimed_cleanup_attempts(monkeypatch):
    metrics = WorkspaceMetrics()
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_SLO_CLEANUP_DEAD_LETTER_RATIO", "0")
    metrics.record_cleanup_outcome("claimed")
    metrics.record_cleanup_outcome("dead_letter")

    status = metrics.evaluate_slo()
    assert status.cleanup_dead_letter_total == 1
    assert status.cleanup_dead_letter_ratio == 1.0
    assert "workspace_cleanup_dead_letter" in status.violations


def test_outbox_slos_use_dispatched_attempts_and_closed_outcomes(monkeypatch):
    metrics = WorkspaceMetrics()
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_SLO_OUTBOX_DEAD_LETTER_RATIO", "0")
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_SLO_OUTBOX_LEASE_LOST_RATIO", "0")
    metrics.record_outbox_outcome("dispatched")
    metrics.record_outbox_outcome("dispatched")
    metrics.record_outbox_outcome("dead_letter")
    metrics.record_outbox_outcome("lease_lost")
    metrics.record_outbox_outcome("provider-specific-secret")

    status = metrics.evaluate_slo()
    assert status.outbox_dead_letter_total == 1
    assert status.outbox_dead_letter_ratio == 0.5
    assert status.outbox_lease_lost_total == 1
    assert status.outbox_lease_lost_ratio == 0.5
    assert {
        "workspace_outbox_dead_letter",
        "workspace_outbox_lease_lost",
    }.issubset(status.violations)
    assert metrics.snapshot()["outbox"]["other"] == 1
    assert (
        'workspace_outbox_total{outcome="dead_letter"} 1'
        in metrics.prometheus_text()
    )


def test_slo_http_response_exposes_cleanup_and_outbox_fields(monkeypatch):
    metrics = WorkspaceMetrics()
    metrics.record_cleanup_outcome("claimed")
    metrics.record_cleanup_outcome("claimed")
    metrics.record_cleanup_outcome("dead_letter")
    for _ in range(4):
        metrics.record_outbox_outcome("dispatched")
    metrics.record_outbox_outcome("dead_letter")
    metrics.record_outbox_outcome("lease_lost")
    metrics.record_outbox_outcome("lease_lost")
    monkeypatch.setattr(workspace_endpoint, "WORKSPACE_METRICS", metrics)
    monkeypatch.setattr(
        workspace_endpoint,
        "_require_supported_runtime",
        lambda: None,
    )
    app = FastAPI()
    app.include_router(workspace_endpoint.WORKSPACE_ROUTER)

    response = TestClient(app).get("/workspace/observability/slo")

    assert response.status_code == 200
    payload = response.json()
    assert payload["cleanup_dead_letter_total"] == 1
    assert payload["cleanup_dead_letter_ratio"] == 0.5
    assert payload["outbox_dead_letter_total"] == 1
    assert payload["outbox_dead_letter_ratio"] == 0.25
    assert payload["outbox_lease_lost_total"] == 2
    assert payload["outbox_lease_lost_ratio"] == 0.5


def test_shadow_mode_off_skips_the_legacy_read(monkeypatch):
    legacy_read = AsyncMock()
    record = Mock()
    monkeypatch.setattr(
        "services.workspace_shadow_parity.resolve_active_workspace_write_rollout",
        lambda _presentation_id: SimpleNamespace(shadow_mode=False),
    )
    monkeypatch.setattr("services.workspace_shadow_parity.load_messages", legacy_read)
    monkeypatch.setattr(
        "services.workspace_shadow_parity.WORKSPACE_METRICS.record_shadow_parity",
        record,
    )

    asyncio.run(
        observe_workspace_timeline_shadow_parity(
            Mock(),
            presentation_id=PRESENTATION_ID,
            timeline=_shadow_timeline(),
        )
    )

    legacy_read.assert_not_awaited()
    record.assert_not_called()


def test_shadow_mode_executes_live_dual_read_and_detects_silent_loss(monkeypatch):
    legacy_read = AsyncMock(return_value=[])
    record = Mock()
    monkeypatch.setattr(
        "services.workspace_shadow_parity.resolve_active_workspace_write_rollout",
        lambda _presentation_id: SimpleNamespace(shadow_mode=True),
    )
    monkeypatch.setattr("services.workspace_shadow_parity.load_messages", legacy_read)
    monkeypatch.setattr(
        "services.workspace_shadow_parity.WORKSPACE_METRICS.record_shadow_parity",
        record,
    )
    session = Mock()

    asyncio.run(
        observe_workspace_timeline_shadow_parity(
            session,
            presentation_id=PRESENTATION_ID,
            timeline=_shadow_timeline(),
        )
    )

    legacy_read.assert_awaited_once_with(
        session,
        presentation_id=PRESENTATION_ID,
        conversation_id=CONVERSATION_ID,
    )
    record.assert_called_once_with(matched=False, silent_loss=True)


def test_shadow_read_failure_emits_closed_signal_failure_without_content(monkeypatch):
    legacy_read = AsyncMock(side_effect=RuntimeError("private SQL value"))
    metrics = Mock()
    monkeypatch.setattr(
        "services.workspace_shadow_parity.resolve_active_workspace_write_rollout",
        lambda _presentation_id: SimpleNamespace(shadow_mode=True),
    )
    monkeypatch.setattr("services.workspace_shadow_parity.load_messages", legacy_read)
    monkeypatch.setattr("services.workspace_shadow_parity.WORKSPACE_METRICS", metrics)

    asyncio.run(
        observe_workspace_timeline_shadow_parity(
            Mock(),
            presentation_id=PRESENTATION_ID,
            timeline=_shadow_timeline(),
        )
    )

    metrics.record_signal_failure.assert_called_once_with("shadow_parity")
    metrics.record_shadow_parity.assert_not_called()


def test_gate8_alert_file_names_each_slo_and_metric():
    alert_path = (
        Path(__file__).resolve().parents[2]
        / "ops"
        / "prometheus"
        / "chat-first-workspace-alerts.yml"
    )
    document = yaml.safe_load(alert_path.read_text(encoding="utf-8"))
    rules = document["groups"][0]["rules"]
    by_name = {rule["alert"]: rule for rule in rules}

    expected = {
        "WorkspaceDeadLetterSLOBreach": "workspace_dead_letter_total",
        "WorkspaceLeaseLostSLOBreach": "workspace_lease_lost_total",
        "WorkspaceRevisionConflictSLOBreach": "workspace_revision_conflict_total",
        "WorkspaceCleanupDeadLetterSLOBreach": "workspace_cleanup_dead_letter_total",
        "WorkspaceOutboxDeadLetterSLOBreach": (
            'workspace_outbox_total{outcome="dead_letter"}'
        ),
        "WorkspaceOutboxLeaseLostSLOBreach": (
            'workspace_outbox_total{outcome="lease_lost"}'
        ),
        "WorkspaceAPIExporterAbsent": 'up{job="chat-first-workspace-api"}',
        "WorkspaceAPIExporterDown": 'up{job="chat-first-workspace-api"}',
        "WorkspaceWorkerExporterAbsent": 'up{job="chat-first-workspace-workers"}',
        "WorkspaceWorkerExporterDown": 'up{job="chat-first-workspace-workers"}',
        "WorkspaceSignalSilentLossDetected": "workspace_silent_loss_total",
        "WorkspaceObservabilitySignalFailure": (
            "workspace_observability_signal_failure_total"
        ),
        "WorkspaceShadowParityDivergence": "workspace_shadow_parity_total",
        "WorkspaceEventProcessingLagSLOBreach": "workspace_event_lag_ms_bucket",
        "WorkspaceEventLagSignalStalled": (
            'workspace_outbox_total{outcome="event_delivered"}'
        ),
    }
    assert set(by_name) == set(expected)
    for alert_name, metric_name in expected.items():
        assert metric_name in by_name[alert_name]["expr"]
        assert "clamp_min" not in by_name[alert_name]["expr"]
    for alert_name in {
        "WorkspaceDeadLetterSLOBreach",
        "WorkspaceLeaseLostSLOBreach",
        "WorkspaceRevisionConflictSLOBreach",
    }:
        assert "workspace_operation_admission_total" in by_name[alert_name]["expr"]
    assert (
        'workspace_cleanup_total{outcome="claimed"}'
        in by_name["WorkspaceCleanupDeadLetterSLOBreach"]["expr"]
    )
    for alert_name in {
        "WorkspaceOutboxDeadLetterSLOBreach",
        "WorkspaceOutboxLeaseLostSLOBreach",
    }:
        assert (
            'workspace_outbox_total{outcome="dispatched"}'
            in by_name[alert_name]["expr"]
        )
    assert (
        "workspace_event_lag_ms_count"
        in by_name["WorkspaceEventLagSignalStalled"]["expr"]
    )
