from __future__ import annotations

import pytest

from utils.env_settings import provider_credentials as pc

_ALL = pc.ENV_NAMES


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in _ALL:
        monkeypatch.delenv(name, raising=False)


def test_defaults_when_unset():
    s = pc.get_provider_credential_settings()
    assert s.backend == ""
    assert s.desktop_vault_directory is None
    assert s.local_file_vault_directory is None
    assert s.server_kms_prefix == "presenton/provider"
    assert s.aws_region is None
    assert s.scope_user_id == ""
    assert s.config_import_path is None


def test_normalisation(monkeypatch):
    monkeypatch.setenv("PROVIDER_CREDENTIAL_BACKEND", "  Server_KMS ")
    monkeypatch.setenv("AWS_REGION", "  ap-northeast-2 ")
    monkeypatch.setenv("PROVIDER_CREDENTIAL_SCOPE_USER_ID", "  u1 ")
    monkeypatch.setenv("PROVIDER_SERVER_KMS_PREFIX", "x/y")
    s = pc.get_provider_credential_settings()
    assert (s.backend, s.aws_region, s.scope_user_id, s.server_kms_prefix) == (
        "server_kms",
        "ap-northeast-2",
        "u1",
        "x/y",
    )
    monkeypatch.setenv("AWS_REGION", "   ")
    monkeypatch.setenv("PROVIDER_SERVER_KMS_PREFIX", "")
    s = pc.get_provider_credential_settings()
    assert s.aws_region is None
    assert s.server_kms_prefix == "presenton/provider"


def test_skip_availability_is_exact_true(monkeypatch):
    assert pc.is_provider_availability_check_skipped() is False
    for value, expected in (("true", True), ("TRUE", False), ("1", False)):
        monkeypatch.setenv("PRESENTON_SKIP_PROVIDER_AVAILABILITY_CHECK", value)
        assert pc.is_provider_availability_check_skipped() is expected


def test_telemetry_opt_in_truthy_set(monkeypatch):
    assert pc.is_provider_telemetry_egress_opted_in() is False
    for value, expected in (("yes", True), (" On ", True), ("0", False), ("x", False)):
        monkeypatch.setenv("PROVIDER_TELEMETRY_EGRESS_OPT_IN", value)
        assert pc.is_provider_telemetry_egress_opted_in() is expected


def test_allowlist_text(monkeypatch):
    assert pc.get_egress_allowlist_text("PROVIDER_TEST_ALLOWLIST") == ""
    monkeypatch.setenv("PROVIDER_TEST_ALLOWLIST", "a.example, b.example")
    assert pc.get_egress_allowlist_text("PROVIDER_TEST_ALLOWLIST") == "a.example, b.example"


def test_required_int(monkeypatch):
    assert pc.provider_required_int("PROVIDER_RPM", 60) == 60
    monkeypatch.setenv("PROVIDER_RPM", " 7 ")
    assert pc.provider_required_int("PROVIDER_RPM", 60) == 7
    for bad in ("abc", "0"):
        monkeypatch.setenv("PROVIDER_RPM", bad)
        with pytest.raises(RuntimeError):
            pc.provider_required_int("PROVIDER_RPM", 60)


def test_optional_limit(monkeypatch):
    assert pc.provider_optional_limit("PROVIDER_JOB_DEADLINE_SECONDS") is None
    monkeypatch.setenv("PROVIDER_JOB_DEADLINE_SECONDS", "0")
    assert pc.provider_optional_limit("PROVIDER_JOB_DEADLINE_SECONDS") == 0
    for bad in ("-1", "x"):
        monkeypatch.setenv("PROVIDER_JOB_DEADLINE_SECONDS", bad)
        with pytest.raises(RuntimeError):
            pc.provider_optional_limit("PROVIDER_JOB_DEADLINE_SECONDS")
