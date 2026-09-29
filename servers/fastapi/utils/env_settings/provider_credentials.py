"""Environment knobs for provider credential storage, admission and egress.

One typed snapshot (``ProviderCredentialSettings``) plus a few small accessors
replace the literal ``os.getenv`` reads that were spread over the
``services/provider_*`` modules.  Nothing here reads the environment at import
time; every value is resolved through ``utils.get_env.get_setting_env`` when an
accessor is called.

Layering: this module must never import ``services`` or ``api``, and
``utils/get_env.py`` must never import this module (``get_setting_env`` lazily
imports ``services.provider_secure_runtime``, which imports this module).

``PROVIDER_CREDENTIAL_SCOPE_USER_ID`` is identity-sensitive: never log it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from utils.env_parse import EnvValueError, is_truthy
from utils.get_env import env_int, get_setting_env

if TYPE_CHECKING:
    from utils.runtime_settings import RuntimeSettings

CREDENTIAL_BACKEND_ENV = "PROVIDER_CREDENTIAL_BACKEND"
DESKTOP_VAULT_DIRECTORY_ENV = "PROVIDER_DESKTOP_VAULT_DIRECTORY"
LOCAL_FILE_VAULT_DIRECTORY_ENV = "PROVIDER_LOCAL_FILE_VAULT_DIRECTORY"
SERVER_KMS_PREFIX_ENV = "PROVIDER_SERVER_KMS_PREFIX"
AWS_REGION_ENV = "AWS_REGION"
CREDENTIAL_SCOPE_USER_ID_ENV = "PROVIDER_CREDENTIAL_SCOPE_USER_ID"
CONFIG_IMPORT_PATH_ENV = "PRESENTON_PROVIDER_CONFIG_IMPORT_PATH"
SKIP_AVAILABILITY_CHECK_ENV = "PRESENTON_SKIP_PROVIDER_AVAILABILITY_CHECK"
TELEMETRY_EGRESS_OPT_IN_ENV = "PROVIDER_TELEMETRY_EGRESS_OPT_IN"

# The admission / job-registration limit names are literals at their call
# sites; egress allowlist variable names are supplied by the caller.
ENV_NAMES: tuple[str, ...] = (
    CREDENTIAL_BACKEND_ENV,
    DESKTOP_VAULT_DIRECTORY_ENV,
    LOCAL_FILE_VAULT_DIRECTORY_ENV,
    SERVER_KMS_PREFIX_ENV,
    AWS_REGION_ENV,
    CREDENTIAL_SCOPE_USER_ID_ENV,
    CONFIG_IMPORT_PATH_ENV,
    SKIP_AVAILABILITY_CHECK_ENV,
    TELEMETRY_EGRESS_OPT_IN_ENV,
    "PROVIDER_RPM",
    "PROVIDER_CIRCUIT_FAILURE_THRESHOLD",
    "PROVIDER_CIRCUIT_RESET_SECONDS",
    "PROVIDER_RESERVATION_LEASE_SECONDS",
    "PROVIDER_JOB_ESTIMATED_BUDGET_MICROUNITS",
    "PROVIDER_JOB_ACTUAL_BUDGET_MICROUNITS",
    "PROVIDER_JOB_DEADLINE_SECONDS",
    "PROVIDER_CALL_ESTIMATED_COST_MICROUNITS",
)


@dataclass(frozen=True, slots=True)
class ProviderCredentialSettings:
    """Normalised provider credential settings (empty string / None = unset)."""

    backend: str  # stripped, lower-cased; "" when unconfigured
    desktop_vault_directory: str | None
    local_file_vault_directory: str | None
    server_kms_prefix: str  # defaults to "presenton/provider"
    aws_region: str | None
    scope_user_id: str  # stripped; identity-sensitive, never log
    config_import_path: str | None


def get_provider_credential_settings(
    settings: RuntimeSettings | None = None,
) -> ProviderCredentialSettings:
    def read(name: str) -> str | None:
        return get_setting_env(name, settings)

    return ProviderCredentialSettings(
        backend=(read(CREDENTIAL_BACKEND_ENV) or "").strip().lower(),
        desktop_vault_directory=read(DESKTOP_VAULT_DIRECTORY_ENV) or None,
        local_file_vault_directory=read(LOCAL_FILE_VAULT_DIRECTORY_ENV) or None,
        server_kms_prefix=read(SERVER_KMS_PREFIX_ENV) or "presenton/provider",
        aws_region=(read(AWS_REGION_ENV) or "").strip() or None,
        scope_user_id=(read(CREDENTIAL_SCOPE_USER_ID_ENV) or "").strip(),
        config_import_path=read(CONFIG_IMPORT_PATH_ENV) or None,
    )


def is_provider_availability_check_skipped(
    settings: RuntimeSettings | None = None,
) -> bool:
    """True only for the exact text ``true`` (the historical strict contract)."""
    return get_setting_env(SKIP_AVAILABILITY_CHECK_ENV, settings) == "true"


def is_provider_telemetry_egress_opted_in(
    settings: RuntimeSettings | None = None,
) -> bool:
    """Strict opt-in: 1/true/yes/on (case-insensitive) enables, else off."""
    return is_truthy(get_setting_env(TELEMETRY_EGRESS_OPT_IN_ENV, settings))


def get_egress_allowlist_text(
    allowlist_env: str,
    settings: RuntimeSettings | None = None,
) -> str:
    """Raw comma-separated allowlist text for the named variable (``""`` unset)."""
    return get_setting_env(allowlist_env, settings) or ""


def provider_required_int(
    name: str,
    default: int,
    *,
    minimum: int = 1,
) -> int:
    """Integer limit; malformed or below ``minimum`` raises ``RuntimeError``."""
    try:
        return env_int(name, default, minimum=minimum, on_invalid="raise")
    except EnvValueError as error:
        raise RuntimeError(error.code) from error


def provider_optional_limit(name: str) -> int | None:
    """Optional non-negative integer; unset is ``None``, bad raises ``RuntimeError``."""
    try:
        return env_int(name, None, minimum=0, on_invalid="raise")
    except EnvValueError as error:
        raise RuntimeError(error.code) from error
