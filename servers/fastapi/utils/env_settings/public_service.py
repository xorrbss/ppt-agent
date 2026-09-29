"""Typed accessors for the public-service (isolated worker / artifact store /
render controller / process identity) env knobs.

Every read goes through ``utils.get_env.get_setting_env`` so a name that is
later registered as an admin-managed setting is honoured without touching the
call sites.  Nothing is read at import time.

Normalisation policy (mirrors the retired inline reads exactly unless noted):

* Strings are stripped; a blank value counts as unset and yields the
  documented default (``None`` for optional knobs).
* ``PUBLIC_ARTIFACT_S3_SERVER_SIDE_ENCRYPTION`` is the one presence-sensitive
  knob: *unset* means ``"AES256"``, while an explicitly blank value means
  "no server-side encryption header" (``""``).  That distinction is an
  operator choice and is preserved.
* Numeric knobs whose call site owns a stable error code
  (``artifact.worker_cache_limits_invalid``,
  ``public_render_concurrency_invalid``) use ``on_invalid="raise"`` so that
  the caller can keep translating :class:`~utils.env_parse.EnvValueError`
  (a ``ValueError``) into that code.  The single-flight knobs, whose old
  reads would have crashed with a bare ``ValueError`` and were then clamped,
  use ``on_invalid="clamp"`` with the same bounds the call site applied.
* ``PUBLIC_RENDER_MAX_CONCURRENCY`` has different defaults at different
  sites (2 for the export controller, 4 as the slide-capture fallback), so
  :func:`get_public_render_max_concurrency` takes ``default`` from the caller
  instead of choosing one here.
* Validation that carries service-specific error codes (S3 addressing style,
  TLS-verify text, worker-mode vocabulary) stays in the service; this module
  only normalises text.

Secret-like values (the local download signing key, the capability key file
path, the runtime-settings refresh token) are returned as-is and must never
be logged.
"""

from __future__ import annotations

from dataclasses import dataclass

from utils.get_env import env_bool, env_float, env_int, get_setting_env
from utils.runtime_settings import RuntimeSettings

WORKSPACE_OPERATION_WORKER_MODE_ENV = "WORKSPACE_OPERATION_WORKER_MODE"
WORKSPACE_OPERATION_EXTERNAL_CONTROLLER_ENV = "WORKSPACE_OPERATION_EXTERNAL_CONTROLLER"
PUBLIC_ARTIFACT_LIFECYCLE_ENV = "PUBLIC_ARTIFACT_LIFECYCLE_ENABLED"
RUNTIME_SETTINGS_REFRESH_TOKEN_ENV = "PRESENTON_RUNTIME_SETTINGS_REFRESH_TOKEN"

ENV_NAMES: tuple[str, ...] = (
    # PublicWorkerSettings
    "PUBLIC_WORKER_NODE_ID",
    "PUBLIC_WORKER_CAPABILITY_KEY_FILE",
    "PUBLIC_WORKER_OBJECT_STORE_ROOT",
    "PUBLIC_WORKER_ARTIFACT_CACHE_ROOT",
    "PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS",
    "PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES",
    # PublicArtifactStoreSettings
    "PUBLIC_ARTIFACT_STORE_BACKEND",
    "PUBLIC_ARTIFACT_S3_BUCKET",
    "PUBLIC_ARTIFACT_S3_PREFIX",
    "PUBLIC_ARTIFACT_S3_ENDPOINT_URL",
    "PUBLIC_ARTIFACT_S3_REGION",
    "PUBLIC_ARTIFACT_S3_ADDRESSING_STYLE",
    "PUBLIC_ARTIFACT_S3_TLS_VERIFY",
    "PUBLIC_ARTIFACT_S3_SERVER_SIDE_ENCRYPTION",
    "PUBLIC_LOCAL_ARTIFACT_DOWNLOAD_SIGNING_KEY",
    "PUBLIC_LOCAL_ARTIFACT_DOWNLOAD_ORIGIN",
    PUBLIC_ARTIFACT_LIFECYCLE_ENV,
    # PublicRenderSettings
    "PUBLIC_RENDER_MAX_CONCURRENCY",
    "PUBLIC_RENDER_SINGLEFLIGHT_WAIT_SECONDS",
    "PUBLIC_RENDER_SINGLEFLIGHT_MAX",
    # PublicProcessIdentitySettings
    "PUBLIC_DATABASE_PROCESS_ROLE",
    "PUBLIC_V2_INTEGRATION_PROFILE",
    "PPT_PUBLIC_OIDC_ISSUER",
    "PPT_PUBLIC_OIDC_AUDIENCE",
    "PPT_PUBLIC_OIDC_JWKS_FILE",
    "PPT_SERVICE_TENANT_ID",
    # scalar accessors
    "LOG_LEVEL",
    "CORS_ALLOWED_ORIGINS",
    WORKSPACE_OPERATION_WORKER_MODE_ENV,
    WORKSPACE_OPERATION_EXTERNAL_CONTROLLER_ENV,
    RUNTIME_SETTINGS_REFRESH_TOKEN_ENV,
)

DEFAULT_WORKER_ARTIFACT_CACHE_TTL_SECONDS = 900
DEFAULT_WORKER_ARTIFACT_CACHE_MAX_BYTES = 1024 * 1024 * 1024
DEFAULT_S3_PREFIX = "presenton-artifacts"
DEFAULT_S3_REGION = "us-east-1"
DEFAULT_S3_ADDRESSING_STYLE = "virtual"
DEFAULT_S3_TLS_VERIFY_TEXT = "true"
DEFAULT_S3_SERVER_SIDE_ENCRYPTION = "AES256"
DEFAULT_LOCAL_ARTIFACT_DOWNLOAD_ORIGIN = "http://127.0.0.1:8010"
DEFAULT_RENDER_SINGLEFLIGHT_WAIT_SECONDS = 125.0
DEFAULT_RENDER_SINGLEFLIGHT_MAX = 64
DEFAULT_SERVICE_TENANT_ID = "default"
DEFAULT_WORKSPACE_OPERATION_WORKER_MODE = "embedded"


def _text(name: str, settings: RuntimeSettings | None) -> str | None:
    value = (get_setting_env(name, settings) or "").strip()
    return value or None


@dataclass(frozen=True, slots=True)
class PublicWorkerSettings:
    node_id: str | None
    capability_key_file: str | None
    object_store_root: str | None
    artifact_cache_root: str | None


def get_public_worker_settings(
    settings: RuntimeSettings | None = None,
) -> PublicWorkerSettings:
    return PublicWorkerSettings(
        node_id=_text("PUBLIC_WORKER_NODE_ID", settings),
        capability_key_file=_text("PUBLIC_WORKER_CAPABILITY_KEY_FILE", settings),
        object_store_root=_text("PUBLIC_WORKER_OBJECT_STORE_ROOT", settings),
        artifact_cache_root=_text("PUBLIC_WORKER_ARTIFACT_CACHE_ROOT", settings),
    )


def get_public_worker_node_id(
    default: str = "",
    *,
    settings: RuntimeSettings | None = None,
) -> str:
    """``PUBLIC_WORKER_NODE_ID`` stripped, or ``default`` when unset/blank."""
    return _text("PUBLIC_WORKER_NODE_ID", settings) or default


def get_worker_artifact_cache_ttl_seconds(
    settings: RuntimeSettings | None = None,
) -> int:
    """Raises :class:`EnvValueError` on malformed or non-positive text."""
    return env_int(
        "PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS",
        DEFAULT_WORKER_ARTIFACT_CACHE_TTL_SECONDS,
        minimum=1,
        on_invalid="raise",
        settings=settings,
    )


def get_worker_artifact_cache_max_bytes(
    settings: RuntimeSettings | None = None,
) -> int:
    """Raises :class:`EnvValueError` on malformed or non-positive text."""
    return env_int(
        "PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES",
        DEFAULT_WORKER_ARTIFACT_CACHE_MAX_BYTES,
        minimum=1,
        on_invalid="raise",
        settings=settings,
    )


@dataclass(frozen=True, slots=True)
class PublicArtifactStoreSettings:
    backend: str
    s3_bucket: str
    s3_prefix: str
    s3_endpoint_url: str
    s3_region: str
    s3_addressing_style: str
    s3_tls_verify_text: str
    s3_server_side_encryption: str
    local_download_signing_key: str | None
    local_download_origin: str


def get_public_artifact_store_backend(
    settings: RuntimeSettings | None = None,
) -> str:
    """``PUBLIC_ARTIFACT_STORE_BACKEND`` stripped and lower-cased (``""`` when unset)."""
    return (get_setting_env("PUBLIC_ARTIFACT_STORE_BACKEND", settings) or "").strip().lower()


def get_public_artifact_store_settings(
    settings: RuntimeSettings | None = None,
) -> PublicArtifactStoreSettings:
    sse_raw = get_setting_env("PUBLIC_ARTIFACT_S3_SERVER_SIDE_ENCRYPTION", settings)
    return PublicArtifactStoreSettings(
        backend=get_public_artifact_store_backend(settings),
        s3_bucket=_text("PUBLIC_ARTIFACT_S3_BUCKET", settings) or "",
        s3_prefix=_text("PUBLIC_ARTIFACT_S3_PREFIX", settings) or DEFAULT_S3_PREFIX,
        s3_endpoint_url=_text("PUBLIC_ARTIFACT_S3_ENDPOINT_URL", settings) or "",
        s3_region=_text("PUBLIC_ARTIFACT_S3_REGION", settings) or DEFAULT_S3_REGION,
        s3_addressing_style=(
            _text("PUBLIC_ARTIFACT_S3_ADDRESSING_STYLE", settings)
            or DEFAULT_S3_ADDRESSING_STYLE
        ).lower(),
        s3_tls_verify_text=(
            _text("PUBLIC_ARTIFACT_S3_TLS_VERIFY", settings)
            or DEFAULT_S3_TLS_VERIFY_TEXT
        ).lower(),
        # Presence-sensitive: unset -> AES256, explicitly blank -> no SSE.
        s3_server_side_encryption=(
            DEFAULT_S3_SERVER_SIDE_ENCRYPTION if sse_raw is None else sse_raw
        ),
        local_download_signing_key=(
            get_setting_env("PUBLIC_LOCAL_ARTIFACT_DOWNLOAD_SIGNING_KEY", settings)
            or None
        ),
        local_download_origin=(
            _text("PUBLIC_LOCAL_ARTIFACT_DOWNLOAD_ORIGIN", settings)
            or DEFAULT_LOCAL_ARTIFACT_DOWNLOAD_ORIGIN
        ),
    )


def is_public_artifact_lifecycle_enabled(
    settings: RuntimeSettings | None = None,
) -> bool:
    """``PUBLIC_ARTIFACT_LIFECYCLE_ENABLED`` (default off)."""
    return env_bool(PUBLIC_ARTIFACT_LIFECYCLE_ENV, False, settings=settings)


@dataclass(frozen=True, slots=True)
class PublicRenderSettings:
    singleflight_wait_seconds: float
    singleflight_max: int


def get_public_render_max_concurrency(
    default: int,
    *,
    settings: RuntimeSettings | None = None,
) -> int:
    """``PUBLIC_RENDER_MAX_CONCURRENCY`` with the caller's default.

    Malformed text raises :class:`EnvValueError`; the caller translates it
    into its own error code.
    """
    return env_int(
        "PUBLIC_RENDER_MAX_CONCURRENCY",
        default,
        on_invalid="raise",
        settings=settings,
    )


def get_public_render_settings(
    settings: RuntimeSettings | None = None,
) -> PublicRenderSettings:
    return PublicRenderSettings(
        singleflight_wait_seconds=env_float(
            "PUBLIC_RENDER_SINGLEFLIGHT_WAIT_SECONDS",
            DEFAULT_RENDER_SINGLEFLIGHT_WAIT_SECONDS,
            minimum=1.0,
            maximum=600.0,
            on_invalid="clamp",
            settings=settings,
        ),
        singleflight_max=env_int(
            "PUBLIC_RENDER_SINGLEFLIGHT_MAX",
            DEFAULT_RENDER_SINGLEFLIGHT_MAX,
            minimum=1,
            maximum=256,
            on_invalid="clamp",
            settings=settings,
        ),
    )


@dataclass(frozen=True, slots=True)
class PublicProcessIdentitySettings:
    database_process_role: str
    v2_integration_profile: str
    oidc_issuer: str
    oidc_audience: str
    oidc_jwks_file: str
    service_tenant_id: str


def get_public_process_identity_settings(
    settings: RuntimeSettings | None = None,
) -> PublicProcessIdentitySettings:
    return PublicProcessIdentitySettings(
        database_process_role=(
            _text("PUBLIC_DATABASE_PROCESS_ROLE", settings) or ""
        ).lower(),
        v2_integration_profile=_text("PUBLIC_V2_INTEGRATION_PROFILE", settings) or "",
        oidc_issuer=_text("PPT_PUBLIC_OIDC_ISSUER", settings) or "",
        oidc_audience=_text("PPT_PUBLIC_OIDC_AUDIENCE", settings) or "",
        oidc_jwks_file=_text("PPT_PUBLIC_OIDC_JWKS_FILE", settings) or "",
        service_tenant_id=(
            get_setting_env("PPT_SERVICE_TENANT_ID", settings)
            or DEFAULT_SERVICE_TENANT_ID
        ).strip(),
    )


def get_log_level(
    default: str = "INFO",
    *,
    settings: RuntimeSettings | None = None,
) -> str:
    """``LOG_LEVEL`` stripped and upper-cased; unset/blank yields ``default``."""
    value = (get_setting_env("LOG_LEVEL", settings) or "").strip().upper()
    return value or default


def get_cors_allowed_origins(
    settings: RuntimeSettings | None = None,
) -> tuple[str, ...]:
    """Comma-separated ``CORS_ALLOWED_ORIGINS``; empty tuple when unset."""
    raw = (get_setting_env("CORS_ALLOWED_ORIGINS", settings) or "").strip()
    if not raw:
        return ()
    return tuple(origin.strip() for origin in raw.split(",") if origin.strip())


def get_workspace_operation_worker_mode_text(
    settings: RuntimeSettings | None = None,
) -> str:
    """Raw ``WORKSPACE_OPERATION_WORKER_MODE`` (stripped, lower-cased).

    Vocabulary validation stays in ``services.workspace_operation_worker``.
    """
    value = (
        get_setting_env(WORKSPACE_OPERATION_WORKER_MODE_ENV, settings) or ""
    ).strip().lower()
    return value or DEFAULT_WORKSPACE_OPERATION_WORKER_MODE


def get_workspace_operation_external_controller_text(
    settings: RuntimeSettings | None = None,
) -> str:
    """Raw ``WORKSPACE_OPERATION_EXTERNAL_CONTROLLER`` (stripped, lower-cased, ``""`` when unset)."""
    return (
        get_setting_env(WORKSPACE_OPERATION_EXTERNAL_CONTROLLER_ENV, settings) or ""
    ).strip().lower()


def get_runtime_settings_refresh_token(
    settings: RuntimeSettings | None = None,
) -> str:
    """Loopback refresh-endpoint bearer token, unmodified (``""`` when unset)."""
    return get_setting_env(RUNTIME_SETTINGS_REFRESH_TOKEN_ENV, settings) or ""
