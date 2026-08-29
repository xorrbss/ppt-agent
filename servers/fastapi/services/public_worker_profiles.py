"""Fail-closed execution profiles for independently scalable PPT processes."""

from __future__ import annotations

from dataclasses import dataclass
import os
from types import MappingProxyType
from typing import Literal, Mapping


WorkerRole = Literal["ppt-core", "generation", "render", "export"]


@dataclass(frozen=True, slots=True)
class PublicWorkerProfile:
    role: WorkerRole
    operation_kinds: tuple[str, ...]
    canonical_database_access: bool
    provider_network_access: bool
    artifact_access: Literal["metadata-authority", "scoped-read-write"]
    network_mode: Literal["service", "provider-egress", "none"]
    browser_allowed: bool
    converter_allowed: bool
    max_jobs_per_process: int
    independently_scalable: bool = True


PUBLIC_WORKER_PROFILES = MappingProxyType(
    {
        "ppt-core": PublicWorkerProfile(
            role="ppt-core",
            operation_kinds=(),
            canonical_database_access=True,
            provider_network_access=False,
            artifact_access="metadata-authority",
            network_mode="service",
            browser_allowed=False,
            converter_allowed=False,
            max_jobs_per_process=0,
        ),
        "generation": PublicWorkerProfile(
            role="generation",
            operation_kinds=("presentation.generate",),
            canonical_database_access=True,
            provider_network_access=True,
            artifact_access="metadata-authority",
            network_mode="provider-egress",
            browser_allowed=False,
            converter_allowed=False,
            max_jobs_per_process=1,
        ),
        "render": PublicWorkerProfile(
            role="render",
            operation_kinds=("presentation.render",),
            canonical_database_access=False,
            provider_network_access=False,
            artifact_access="scoped-read-write",
            network_mode="none",
            browser_allowed=True,
            converter_allowed=False,
            max_jobs_per_process=1,
        ),
        "export": PublicWorkerProfile(
            role="export",
            operation_kinds=("export.pptx", "export.pdf"),
            canonical_database_access=False,
            provider_network_access=False,
            artifact_access="scoped-read-write",
            network_mode="none",
            browser_allowed=False,
            converter_allowed=True,
            max_jobs_per_process=1,
        ),
    }
)

_USER_SESSION_ENV_KEYS = frozenset(
    {
        "COOKIE",
        "HTTP_COOKIE",
        "SESSION",
        "SESSION_COOKIE",
        "PRESENTON_SESSION",
        "PRESENTON_SESSION_COOKIE",
    }
)
_CANONICAL_DB_ENV_KEYS = frozenset(
    {
        "DATABASE_URL",
        "SQLALCHEMY_DATABASE_URI",
        "POSTGRES_DSN",
        "MYSQL_DSN",
    }
)
_PROVIDER_SECRET_MARKERS = (
    "ANTHROPIC_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "OLLAMA_API_KEY",
    "OPENAI_API_KEY",
    "PERPLEXITY_API_KEY",
)
_ISOLATION_REQUIREMENTS = {
    "PPT_WORKER_NETWORK_MODE": "none",
    "PPT_WORKER_ROOT_FILESYSTEM": "read-only",
    "PPT_WORKER_SCRATCH": "tmpfs",
    "PPT_WORKER_RUN_AS_NON_ROOT": "true",
}
_POSITIVE_LIMITS = (
    "PPT_WORKER_MEMORY_LIMIT_BYTES",
    "PPT_WORKER_CPU_MILLIS",
    "PPT_WORKER_PID_LIMIT",
    "PPT_WORKER_OPEN_FILE_LIMIT",
    "PPT_WORKER_OUTPUT_LIMIT_BYTES",
    "PPT_WORKER_WALL_CLOCK_SECONDS",
)


def get_public_worker_profile(role: str) -> PublicWorkerProfile:
    normalized = role.strip().lower()
    try:
        return PUBLIC_WORKER_PROFILES[normalized]
    except KeyError as exc:
        raise RuntimeError("public_worker.profile_invalid") from exc


def validate_public_worker_environment(
    profile: PublicWorkerProfile,
    environ: Mapping[str, str],
) -> None:
    """Validate launch-time isolation; orchestration must enforce these values."""

    present = {str(key).upper() for key, value in environ.items() if str(value).strip()}
    if present & _USER_SESSION_ENV_KEYS:
        raise RuntimeError("public_worker.user_session_environment_forbidden")

    if profile.role in {"render", "export"}:
        if present & _CANONICAL_DB_ENV_KEYS:
            raise RuntimeError("public_worker.canonical_database_credential_forbidden")
        if present.intersection(_PROVIDER_SECRET_MARKERS):
            raise RuntimeError("public_worker.provider_credential_forbidden")
        for key, expected in _ISOLATION_REQUIREMENTS.items():
            if environ.get(key, "").strip().lower() != expected:
                raise RuntimeError(f"public_worker.isolation_requirement_missing:{key}")
        for key in _POSITIVE_LIMITS:
            try:
                value = int(environ.get(key, ""))
            except ValueError as exc:
                raise RuntimeError(f"public_worker.resource_limit_invalid:{key}") from exc
            if value <= 0:
                raise RuntimeError(f"public_worker.resource_limit_invalid:{key}")

    try:
        replicas = int(environ.get("PPT_WORKER_REPLICAS", "1"))
    except ValueError as exc:
        raise RuntimeError("public_worker.replica_count_invalid") from exc
    if replicas < 1:
        raise RuntimeError("public_worker.replica_count_invalid")


def require_public_worker_profile_from_environment(
    environ: Mapping[str, str] | None = None,
) -> PublicWorkerProfile:
    environment = os.environ if environ is None else environ
    profile = get_public_worker_profile(environment.get("PPT_PROCESS_PROFILE", "ppt-core"))
    validate_public_worker_environment(profile, environment)
    return profile
