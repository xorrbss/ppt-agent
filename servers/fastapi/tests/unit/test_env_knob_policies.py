"""Pins the malformed-value policy each call site chose when its private
env parser was replaced by ``utils.get_env.env_int``/``env_float``/``env_bool``
(docs/refactoring-audit-20260929.md section 4, "positive-int env parser").

Where two modules read the same env name with different contracts before,
the policy pinned here is the one that survived.
"""

from __future__ import annotations

import logging

import pytest

from services import decompose_job_service
from services import workspace_event_retention
from services import workspace_operation_outbox_dispatcher as outbox
from services import workspace_operation_runner as runner
from services import artifact_lifecycle_runtime
from services import authored_checkpoint_sweep_budget as sweep
from services import authored_generation_performance as perf
from services.source_locked import lifecycle as sl_lifecycle
from services.source_locked import maintenance as sl_maintenance
from services.source_locked.errors import SourceLockedError
from utils import db_utils
from utils.env_parse import EnvValueError


# --- warn + default ------------------------------------------------------


def test_decompose_job_knobs_warn_and_keep_default(monkeypatch, caplog):
    monkeypatch.setenv("DOCUMENT_DECOMPOSE_JOB_MAX_CONCURRENCY", "abc")
    with caplog.at_level(logging.WARNING, logger="utils.env_parse"):
        assert (
            decompose_job_service._positive_int_env(
                "DOCUMENT_DECOMPOSE_JOB_MAX_CONCURRENCY", 1, maximum=8
            )
            == 1
        )
    assert len(caplog.records) == 1
    caplog.clear()
    monkeypatch.setenv("DOCUMENT_DECOMPOSE_JOB_MAX_CONCURRENCY", "99")
    with caplog.at_level(logging.WARNING, logger="utils.env_parse"):
        assert (
            decompose_job_service._positive_int_env(
                "DOCUMENT_DECOMPOSE_JOB_MAX_CONCURRENCY", 1, maximum=8
            )
            == 1
        )
    assert "out-of-range" in caplog.records[0].getMessage()
    monkeypatch.setenv("DOCUMENT_DECOMPOSE_JOB_MAX_CONCURRENCY", " 4 ")
    assert (
        decompose_job_service._positive_int_env(
            "DOCUMENT_DECOMPOSE_JOB_MAX_CONCURRENCY", 1, maximum=8
        )
        == 4
    )


# --- raise with a stable code ------------------------------------------------


def test_workspace_event_retention_rejects_malformed_with_legacy_codes(monkeypatch):
    monkeypatch.setenv("WORKSPACE_EVENT_RETENTION_SECONDS", "soon")
    with pytest.raises(ValueError, match="WORKSPACE_EVENT_RETENTION_SECONDS_must_be_a_positive_integer"):
        workspace_event_retention.get_workspace_event_retention_config()
    monkeypatch.setenv("WORKSPACE_EVENT_RETENTION_SECONDS", "0")
    with pytest.raises(ValueError, match="WORKSPACE_EVENT_RETENTION_SECONDS_must_be_a_positive_integer"):
        workspace_event_retention.get_workspace_event_retention_config()
    monkeypatch.delenv("WORKSPACE_EVENT_RETENTION_SECONDS")
    monkeypatch.setenv("WORKSPACE_EVENT_RETENTION_BATCH_SIZE", "20000")
    with pytest.raises(ValueError, match="WORKSPACE_EVENT_RETENTION_BATCH_SIZE_between_1_and_10000"):
        workspace_event_retention.get_workspace_event_retention_config()
    monkeypatch.setenv("WORKSPACE_EVENT_RETENTION_BATCH_SIZE", "250")
    assert workspace_event_retention.get_workspace_event_retention_config().batch_size == 250


def test_workspace_event_retention_disabled_sentinel_survives(monkeypatch):
    monkeypatch.setenv("WORKSPACE_EVENT_RETENTION_SECONDS", " Disabled ")
    config = workspace_event_retention.get_workspace_event_retention_config()
    assert config.retention_seconds is None
    assert config.enabled is False


def test_source_locked_maintenance_raises_env_value_error(monkeypatch):
    monkeypatch.setenv("SOURCE_LOCKED_MAINTENANCE_MAX_WORKERS", "-1")
    with pytest.raises(EnvValueError) as info:
        sl_maintenance.SourceLockedMaintenancePolicy.from_environment()
    assert info.value.code == "SOURCE_LOCKED_MAINTENANCE_MAX_WORKERS_must_be_a_positive_integer"
    monkeypatch.setenv("SOURCE_LOCKED_MAINTENANCE_MAX_WORKERS", "3")
    assert sl_maintenance.SourceLockedMaintenancePolicy.from_environment().max_workers == 3


def test_source_locked_lifecycle_wraps_into_source_locked_error(monkeypatch):
    monkeypatch.setenv("SOURCE_LOCKED_ARTIFACT_RETENTION_SECONDS", "week")
    with pytest.raises(SourceLockedError) as info:
        sl_lifecycle.ArtifactLifecyclePolicy.from_environment()
    assert info.value.detail.code == "SOURCE_LOCKED_LIFECYCLE_CONFIG_INVALID"
    assert isinstance(info.value.__cause__, EnvValueError)
    monkeypatch.setenv("SOURCE_LOCKED_ARTIFACT_RETENTION_SECONDS", "0")
    with pytest.raises(SourceLockedError):
        sl_lifecycle.ArtifactLifecyclePolicy.from_environment()


# --- silent clamp ----------------------------------------------------------


@pytest.mark.parametrize("module", [outbox, runner])
def test_workspace_runner_and_outbox_clamp_silently(module, monkeypatch):
    monkeypatch.setenv("PRESENTON_TEST_CLAMP_INT", "0")
    assert module._positive_int_env("PRESENTON_TEST_CLAMP_INT", 30) == 1
    monkeypatch.setenv("PRESENTON_TEST_CLAMP_INT", "x")
    assert module._positive_int_env("PRESENTON_TEST_CLAMP_INT", 30) == 30
    monkeypatch.setenv("PRESENTON_TEST_CLAMP_FLOAT", "0.001")
    assert module._positive_float_env("PRESENTON_TEST_CLAMP_FLOAT", 1.0) == 0.05
    monkeypatch.setenv("PRESENTON_TEST_CLAMP_FLOAT", "nan")
    assert module._positive_float_env("PRESENTON_TEST_CLAMP_FLOAT", 1.0) == 1.0


def test_public_artifact_lifecycle_clamps_to_its_minimum(monkeypatch):
    monkeypatch.setenv("PUBLIC_ARTIFACT_DELETE_GRACE_SECONDS", "5")
    assert (
        artifact_lifecycle_runtime._positive_int(
            "PUBLIC_ARTIFACT_DELETE_GRACE_SECONDS", 3600, minimum=60
        )
        == 60
    )
    monkeypatch.setenv("PUBLIC_ARTIFACT_DELETE_GRACE_SECONDS", "later")
    assert (
        artifact_lifecycle_runtime._positive_int(
            "PUBLIC_ARTIFACT_DELETE_GRACE_SECONDS", 3600, minimum=60
        )
        == 3600
    )


# --- default, with call-site upper cap ----------------------------------------


def test_checkpoint_sweep_budget_defaults_below_and_caps_above(monkeypatch):
    monkeypatch.setenv("AUTHORED_CHECKPOINT_SWEEP_BATCH_SIZE", "0")
    assert sweep._artifact_sweep_batch_size() == 50
    monkeypatch.setenv("AUTHORED_CHECKPOINT_SWEEP_BATCH_SIZE", "many")
    assert sweep._artifact_sweep_batch_size() == 50
    monkeypatch.setenv("AUTHORED_CHECKPOINT_SWEEP_BATCH_SIZE", "9999")
    assert sweep._artifact_sweep_batch_size() == 500


# --- optional override -------------------------------------------------------


def test_authored_provider_concurrency_override_is_optional(monkeypatch):
    monkeypatch.setenv("AUTHORED_PROVIDER_CONCURRENCY", "0")
    assert perf._positive_int("AUTHORED_PROVIDER_CONCURRENCY") is None
    monkeypatch.setenv("AUTHORED_PROVIDER_CONCURRENCY", " 3 ")
    assert perf._positive_int("AUTHORED_PROVIDER_CONCURRENCY") == 3
    monkeypatch.delenv("AUTHORED_PROVIDER_CONCURRENCY")
    assert perf._positive_int("AUTHORED_PROVIDER_CONCURRENCY") is None


# --- deliberately lenient pool knobs -------------------------------------------


def test_db_pool_knobs_keep_sqlalchemy_sentinel_values(monkeypatch):
    monkeypatch.setenv("DB_POOL_SIZE", "0")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "-1")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "thirty")
    monkeypatch.setenv("DB_POOL_PRE_PING", "off")
    kwargs = db_utils.get_pool_kwargs()
    assert kwargs["pool_size"] == 0
    assert kwargs["max_overflow"] == -1
    assert kwargs["pool_timeout"] == 30
    assert kwargs["pool_pre_ping"] is False
    monkeypatch.setenv("DB_POOL_PRE_PING", "garbage")
    assert db_utils.get_pool_kwargs()["pool_pre_ping"] is True
    monkeypatch.delenv("DB_POOL_PRE_PING")
    assert db_utils.get_pool_kwargs()["pool_pre_ping"] is True
