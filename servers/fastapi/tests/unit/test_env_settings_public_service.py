import re
from pathlib import Path

import pytest

from utils.env_parse import EnvValueError
from utils.env_settings import public_service as ps
from utils.env_settings.public_service import (
    ENV_NAMES,
    get_cors_allowed_origins,
    get_log_level,
    get_public_artifact_store_backend,
    get_public_artifact_store_settings,
    get_public_process_identity_settings,
    get_public_render_max_concurrency,
    get_public_render_settings,
    get_public_worker_node_id,
    get_public_worker_settings,
    get_runtime_settings_refresh_token,
    get_worker_artifact_cache_max_bytes,
    get_worker_artifact_cache_ttl_seconds,
    get_workspace_operation_external_controller_text,
    get_workspace_operation_worker_mode_text,
    is_public_artifact_lifecycle_enabled,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_env_names_ledger_covers_every_literal_read():
    source = Path(ps.__file__).read_text(encoding="utf-8")
    literals = set(re.findall(r'"((?:PUBLIC|PPT|LOG_LEVEL|CORS|WORKSPACE|PRESENTON)[A-Z0-9_]*)"', source))
    assert literals == set(ENV_NAMES)
    assert len(ENV_NAMES) == len(set(ENV_NAMES))


def test_module_imports_no_services_or_api():
    source = Path(ps.__file__).read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import)\s+(services|api)\b", source, re.M)


def test_worker_defaults():
    s = get_public_worker_settings()
    assert (s.node_id, s.capability_key_file, s.object_store_root, s.artifact_cache_root) == (
        None,
        None,
        None,
        None,
    )
    assert get_public_worker_node_id() == ""
    assert get_public_worker_node_id("export-controller") == "export-controller"
    assert get_worker_artifact_cache_ttl_seconds() == 900
    assert get_worker_artifact_cache_max_bytes() == 1024 * 1024 * 1024


def test_worker_strings_are_stripped_and_blank_is_unset(monkeypatch):
    monkeypatch.setenv("PUBLIC_WORKER_NODE_ID", " node-a ")
    monkeypatch.setenv("PUBLIC_WORKER_CAPABILITY_KEY_FILE", "   ")
    monkeypatch.setenv("PUBLIC_WORKER_OBJECT_STORE_ROOT", " /store ")
    s = get_public_worker_settings()
    assert s.node_id == "node-a"
    assert get_public_worker_node_id("fallback") == "node-a"
    assert s.capability_key_file is None
    assert s.object_store_root == "/store"


@pytest.mark.parametrize("raw", ["abc", "0", "-5"])
def test_worker_cache_limits_raise_on_invalid(monkeypatch, raw):
    monkeypatch.setenv("PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS", raw)
    with pytest.raises(EnvValueError):
        get_worker_artifact_cache_ttl_seconds()
    monkeypatch.setenv("PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES", raw)
    with pytest.raises(ValueError):
        get_worker_artifact_cache_max_bytes()


def test_worker_cache_limits_blank_is_default(monkeypatch):
    monkeypatch.setenv("PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS", " ")
    assert get_worker_artifact_cache_ttl_seconds() == 900
    monkeypatch.setenv("PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS", " 15 ")
    assert get_worker_artifact_cache_ttl_seconds() == 15


def test_artifact_store_defaults():
    s = get_public_artifact_store_settings()
    assert s.backend == ""
    assert get_public_artifact_store_backend() == ""
    assert s.s3_bucket == ""
    assert s.s3_prefix == "presenton-artifacts"
    assert s.s3_endpoint_url == ""
    assert s.s3_region == "us-east-1"
    assert s.s3_addressing_style == "virtual"
    assert s.s3_tls_verify_text == "true"
    assert s.s3_server_side_encryption == "AES256"
    assert s.local_download_signing_key is None
    assert s.local_download_origin == "http://127.0.0.1:8010"


def test_artifact_store_normalisation(monkeypatch):
    monkeypatch.setenv("PUBLIC_ARTIFACT_STORE_BACKEND", " S3 ")
    monkeypatch.setenv("PUBLIC_ARTIFACT_S3_ADDRESSING_STYLE", " Path ")
    monkeypatch.setenv("PUBLIC_ARTIFACT_S3_TLS_VERIFY", " FALSE ")
    monkeypatch.setenv("PUBLIC_ARTIFACT_S3_TLS_VERIFY", " Bogus ")
    monkeypatch.setenv("PUBLIC_ARTIFACT_S3_PREFIX", "  ")
    monkeypatch.setenv("PUBLIC_ARTIFACT_S3_REGION", " eu-west-1 ")
    monkeypatch.setenv("PUBLIC_LOCAL_ARTIFACT_DOWNLOAD_SIGNING_KEY", "k")
    monkeypatch.setenv("PUBLIC_LOCAL_ARTIFACT_DOWNLOAD_ORIGIN", " http://x:1 ")
    s = get_public_artifact_store_settings()
    assert s.backend == "s3"
    assert s.s3_addressing_style == "path"
    # Validation of the vocabulary stays in the service; only text is normalised here.
    assert s.s3_tls_verify_text == "bogus"
    assert s.s3_prefix == "presenton-artifacts"
    assert s.s3_region == "eu-west-1"
    assert s.local_download_signing_key == "k"
    assert s.local_download_origin == "http://x:1"


def test_server_side_encryption_is_presence_sensitive(monkeypatch):
    assert get_public_artifact_store_settings().s3_server_side_encryption == "AES256"
    monkeypatch.setenv("PUBLIC_ARTIFACT_S3_SERVER_SIDE_ENCRYPTION", "")
    assert get_public_artifact_store_settings().s3_server_side_encryption == ""
    monkeypatch.setenv("PUBLIC_ARTIFACT_S3_SERVER_SIDE_ENCRYPTION", "aws:kms")
    assert get_public_artifact_store_settings().s3_server_side_encryption == "aws:kms"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, False), ("", False), ("1", True), ("yes", True), ("off", False), ("bogus", False)],
)
def test_lifecycle_flag(monkeypatch, raw, expected):
    if raw is not None:
        monkeypatch.setenv("PUBLIC_ARTIFACT_LIFECYCLE_ENABLED", raw)
    assert is_public_artifact_lifecycle_enabled() is expected


def test_render_max_concurrency_uses_caller_default(monkeypatch):
    assert get_public_render_max_concurrency(2) == 2
    assert get_public_render_max_concurrency(4) == 4
    monkeypatch.setenv("PUBLIC_RENDER_MAX_CONCURRENCY", " 3 ")
    assert get_public_render_max_concurrency(2) == 3
    monkeypatch.setenv("PUBLIC_RENDER_MAX_CONCURRENCY", "many")
    with pytest.raises(EnvValueError):
        get_public_render_max_concurrency(2)


def test_render_singleflight_defaults_and_clamp(monkeypatch):
    s = get_public_render_settings()
    assert s.singleflight_wait_seconds == 125.0
    assert s.singleflight_max == 64
    monkeypatch.setenv("PUBLIC_RENDER_SINGLEFLIGHT_WAIT_SECONDS", "0.1")
    monkeypatch.setenv("PUBLIC_RENDER_SINGLEFLIGHT_MAX", "9999")
    s = get_public_render_settings()
    assert s.singleflight_wait_seconds == 1.0
    assert s.singleflight_max == 256
    monkeypatch.setenv("PUBLIC_RENDER_SINGLEFLIGHT_WAIT_SECONDS", "nope")
    monkeypatch.setenv("PUBLIC_RENDER_SINGLEFLIGHT_MAX", "0")
    s = get_public_render_settings()
    assert s.singleflight_wait_seconds == 125.0
    assert s.singleflight_max == 1


def test_process_identity_defaults_and_normalisation(monkeypatch):
    s = get_public_process_identity_settings()
    assert s.database_process_role == ""
    assert s.v2_integration_profile == ""
    assert (s.oidc_issuer, s.oidc_audience, s.oidc_jwks_file) == ("", "", "")
    assert s.service_tenant_id == "default"
    monkeypatch.setenv("PUBLIC_DATABASE_PROCESS_ROLE", " Controller ")
    monkeypatch.setenv("PUBLIC_V2_INTEGRATION_PROFILE", " postgresql-strict ")
    monkeypatch.setenv("PPT_PUBLIC_OIDC_ISSUER", " https://issuer ")
    monkeypatch.setenv("PPT_SERVICE_TENANT_ID", " acme ")
    s = get_public_process_identity_settings()
    assert s.database_process_role == "controller"
    assert s.v2_integration_profile == "postgresql-strict"
    assert s.oidc_issuer == "https://issuer"
    assert s.service_tenant_id == "acme"
    # Whitespace-only tenant id strips to "" (the principal layer rejects it),
    # exactly as the retired inline read did.
    monkeypatch.setenv("PPT_SERVICE_TENANT_ID", "  ")
    assert get_public_process_identity_settings().service_tenant_id == ""


def test_log_level(monkeypatch):
    assert get_log_level() == "INFO"
    assert get_log_level("WARNING") == "WARNING"
    monkeypatch.setenv("LOG_LEVEL", " debug ")
    assert get_log_level() == "DEBUG"
    monkeypatch.setenv("LOG_LEVEL", "  ")
    assert get_log_level() == "INFO"


def test_cors_allowed_origins(monkeypatch):
    assert get_cors_allowed_origins() == ()
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", " http://a , ,http://b ")
    assert get_cors_allowed_origins() == ("http://a", "http://b")


def test_workspace_worker_mode_text(monkeypatch):
    assert get_workspace_operation_worker_mode_text() == "embedded"
    assert get_workspace_operation_external_controller_text() == ""
    monkeypatch.setenv("WORKSPACE_OPERATION_WORKER_MODE", " External ")
    monkeypatch.setenv("WORKSPACE_OPERATION_EXTERNAL_CONTROLLER", " Export ")
    assert get_workspace_operation_worker_mode_text() == "external"
    assert get_workspace_operation_external_controller_text() == "export"
    monkeypatch.setenv("WORKSPACE_OPERATION_WORKER_MODE", "sidecar")
    assert get_workspace_operation_worker_mode_text() == "sidecar"


def test_runtime_settings_refresh_token_is_returned_verbatim(monkeypatch):
    assert get_runtime_settings_refresh_token() == ""
    monkeypatch.setenv("PRESENTON_RUNTIME_SETTINGS_REFRESH_TOKEN", " tok ")
    assert get_runtime_settings_refresh_token() == " tok "
