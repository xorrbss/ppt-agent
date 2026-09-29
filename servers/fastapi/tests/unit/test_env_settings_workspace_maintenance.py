"""Pins defaults and malformed-value policies of the workspace maintenance
accessors (utils/env_settings/workspace_maintenance.py)."""

from __future__ import annotations

import pytest

from utils.env_parse import EnvValueError
from utils.env_settings import workspace_maintenance as wm


def test_env_names_cover_every_knob():
    assert len(set(wm.ENV_NAMES)) == len(wm.ENV_NAMES)
    assert "WORKSPACE_OPERATION_METRICS_PORT" in wm.ENV_NAMES


def test_asset_settings_defaults_and_clamp(monkeypatch):
    for name in (
        "WORKSPACE_ASSET_ORPHAN_TTL_SECONDS",
        "WORKSPACE_ASSET_MALWARE_SCAN_MODE",
        "WORKSPACE_ASSET_MALWARE_SCANNER",
    ):
        monkeypatch.delenv(name, raising=False)
    s = wm.get_workspace_asset_settings()
    assert (s.orphan_ttl_seconds, s.malware_scan_mode, s.malware_scanner) == (
        86400,
        "disabled",
        "",
    )
    monkeypatch.setenv("WORKSPACE_ASSET_ORPHAN_TTL_SECONDS", "10")
    assert wm.get_workspace_asset_settings().orphan_ttl_seconds == 300
    monkeypatch.setenv("WORKSPACE_ASSET_ORPHAN_TTL_SECONDS", "999999999")
    assert wm.get_workspace_asset_settings().orphan_ttl_seconds == 30 * 86400
    monkeypatch.setenv("WORKSPACE_ASSET_ORPHAN_TTL_SECONDS", "garbage")
    assert wm.get_workspace_asset_settings().orphan_ttl_seconds == 86400
    monkeypatch.setenv("WORKSPACE_ASSET_MALWARE_SCAN_MODE", " Required ")
    monkeypatch.setenv("WORKSPACE_ASSET_MALWARE_SCANNER", " clamdscan ")
    s = wm.get_workspace_asset_settings()
    assert (s.malware_scan_mode, s.malware_scanner) == ("required", "clamdscan")
    monkeypatch.setenv("WORKSPACE_ASSET_MALWARE_SCAN_MODE", "")
    assert wm.get_workspace_asset_settings().malware_scan_mode == ""


def test_positive_int_knob_policy(monkeypatch):
    name = "WORKSPACE_ASSET_PROCESSING_TIMEOUT_SECONDS"
    monkeypatch.delenv(name, raising=False)
    assert wm.positive_int_knob(name, 600, minimum=30) == 600
    monkeypatch.setenv(name, "abc")
    assert wm.positive_int_knob(name, 600, minimum=30) == 600
    monkeypatch.setenv(name, "0")
    assert wm.positive_int_knob(name, 600, minimum=30) == 600
    monkeypatch.setenv(name, "-5")
    assert wm.positive_int_knob(name, 600, minimum=30) == 600
    monkeypatch.setenv(name, "5")
    assert wm.positive_int_knob(name, 600, minimum=30) == 30
    monkeypatch.setenv(name, " 90 ")
    assert wm.positive_int_knob(name, 600, minimum=30) == 90


def test_destructive_approval_ttl_clamps(monkeypatch):
    name = "WORKSPACE_DESTRUCTIVE_APPROVAL_TTL_SECONDS"
    monkeypatch.delenv(name, raising=False)
    assert wm.get_destructive_approval_ttl_seconds(86400) == 86400
    monkeypatch.setenv(name, "1")
    assert wm.get_destructive_approval_ttl_seconds(86400) == 60
    monkeypatch.setenv(name, str(10**9))
    assert wm.get_destructive_approval_ttl_seconds(86400) == 7 * 86400
    monkeypatch.setenv(name, "x")
    assert wm.get_destructive_approval_ttl_seconds(86400) == 86400


def test_slo_threshold_defaults_on_malformed(monkeypatch):
    name = "CHAT_FIRST_WORKSPACE_SLO_ERROR_RATIO"
    monkeypatch.delenv(name, raising=False)
    assert wm.get_slo_threshold(name, 0.01) == 0.01
    monkeypatch.setenv(name, "0.5")
    assert wm.get_slo_threshold(name, 0.01) == 0.5
    monkeypatch.setenv(name, "nope")
    assert wm.get_slo_threshold(name, 0.01) == 0.01


def test_metrics_exporter_settings(monkeypatch):
    monkeypatch.delenv("WORKSPACE_OPERATION_METRICS_PORT", raising=False)
    monkeypatch.delenv("WORKSPACE_OPERATION_METRICS_HOST", raising=False)
    s = wm.get_metrics_exporter_settings()
    assert (s.port, s.host) == (None, "127.0.0.1")
    monkeypatch.setenv("WORKSPACE_OPERATION_METRICS_PORT", " 9100 ")
    monkeypatch.setenv("WORKSPACE_OPERATION_METRICS_HOST", " 0.0.0.0 ")
    s = wm.get_metrics_exporter_settings()
    assert (s.port, s.host) == (9100, "0.0.0.0")
    for bad in ("abc", "0", "70000"):
        monkeypatch.setenv("WORKSPACE_OPERATION_METRICS_PORT", bad)
        with pytest.raises(EnvValueError):
            wm.get_metrics_exporter_settings()


def test_account_deletion_interval_and_flags(monkeypatch):
    name = "ACCOUNT_DELETION_CONTROLLER_INTERVAL_SECONDS"
    monkeypatch.delenv(name, raising=False)
    assert wm.get_account_deletion_interval_seconds(5) == 5
    monkeypatch.setenv(name, "0")
    assert wm.get_account_deletion_interval_seconds(5) == 1
    monkeypatch.setenv(name, "zzz")
    assert wm.get_account_deletion_interval_seconds(5) == 5
    monkeypatch.setenv(name, "12")
    assert wm.get_account_deletion_interval_seconds(5) == 12

    monkeypatch.delenv("AUTHORED_VNEXT_OOXML_PACKAGE_ENABLED", raising=False)
    monkeypatch.delenv("AUTHORED_VNEXT_MASTER_ENABLED", raising=False)
    assert wm.is_authored_vnext_ooxml_storage_required() is False
    monkeypatch.setenv("AUTHORED_VNEXT_MASTER_ENABLED", "TRUE")
    assert wm.is_authored_vnext_ooxml_storage_required() is True
