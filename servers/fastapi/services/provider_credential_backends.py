"""Credential backend selection plus the development-only plain-file backend.

`PROVIDER_CREDENTIAL_BACKEND` picks the backend:

* ``desktop_vault`` — Windows DPAPI, the desktop/Electron default.
* ``server_kms`` — AWS Secrets Manager, the operations backend.
* ``local_file`` — plain files under ``PROVIDER_LOCAL_FILE_VAULT_DIRECTORY``.
  Nothing encrypts them, so this backend exists only so Linux development and CI
  have somewhere to keep a secret; it refuses to start unless ``ENVIRONMENT``
  names a non-production environment.
"""

from __future__ import annotations

from pathlib import Path

from services.provider_credential_vault import (
    AwsSecretsManagerCredentialBackend,
    CredentialBackend,
    CredentialRef,
    CredentialVaultError,
    SecretValue,
    WindowsDpapiCredentialBackend,
    configured_credential_backend_name,
    credential_entry_path,
    write_credential_file,
)
from utils.env_settings.provider_credentials import get_provider_credential_settings
from utils.get_env import get_app_data_directory_env, get_environment_name


LOCAL_FILE_ENVIRONMENTS = frozenset({"local", "development", "dev", "test"})
LOCAL_FILE_VAULT_DIRECTORY_ENV = "PROVIDER_LOCAL_FILE_VAULT_DIRECTORY"


def _environment_name() -> str:
    """Default to production so an unset ENVIRONMENT never unlocks a plaintext vault."""
    return get_environment_name("production")


class LocalFileCredentialBackend:
    """Unencrypted development vault; refused outside a non-production environment."""

    backend_id = "local_file"

    def __init__(self, root: Path) -> None:
        if _environment_name() not in LOCAL_FILE_ENVIRONMENTS:
            raise CredentialVaultError("credential_local_file_vault_forbidden")
        self.root = root.resolve()
        # Owner-only from creation; individual entries are written 0o600 by
        # `write_credential_file`, the same helper the DPAPI backend uses.
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)

    def _path(self, reference: CredentialRef) -> Path:
        return credential_entry_path(self.root, reference, ".secret")

    def put(self, reference: CredentialRef, secret: SecretValue) -> None:
        if reference.backend != self.backend_id:
            raise CredentialVaultError("credential_backend_mismatch")
        write_credential_file(self._path(reference), secret.reveal().encode("utf-8"))

    def get(self, reference: CredentialRef) -> SecretValue | None:
        if reference.backend != self.backend_id:
            return None
        path = self._path(reference)
        if not path.is_file():
            return None
        try:
            plain = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise CredentialVaultError("credential_local_file_vault_read_failed") from error
        return SecretValue(plain)

    def delete(self, reference: CredentialRef) -> None:
        if reference.backend != self.backend_id:
            raise CredentialVaultError("credential_backend_mismatch")
        self._path(reference).unlink(missing_ok=True)


def local_file_vault_root() -> Path:
    return Path(
        get_provider_credential_settings().local_file_vault_directory
        or Path(get_app_data_directory_env() or "app_data") / "local-file-credential-vault"
    )


def desktop_vault_root() -> Path:
    return Path(
        get_provider_credential_settings().desktop_vault_directory
        or Path(get_app_data_directory_env() or "app_data") / "provider-credential-vault"
    )


def backend_from_environment() -> CredentialBackend:
    """Build the configured backend; there is no plaintext fallback for an unset value."""
    backend = configured_credential_backend_name()
    if backend == "desktop_vault":
        return WindowsDpapiCredentialBackend(desktop_vault_root())
    if backend == "server_kms":
        credential_settings = get_provider_credential_settings()
        return AwsSecretsManagerCredentialBackend(
            region=credential_settings.aws_region,
            prefix=credential_settings.server_kms_prefix,
        )
    if backend == "local_file":
        return LocalFileCredentialBackend(local_file_vault_root())
    raise CredentialVaultError("credential_backend_not_configured")


__all__ = [
    "LOCAL_FILE_ENVIRONMENTS",
    "LOCAL_FILE_VAULT_DIRECTORY_ENV",
    "LocalFileCredentialBackend",
    "backend_from_environment",
    "desktop_vault_root",
    "local_file_vault_root",
]
