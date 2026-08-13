"""Tenant-scoped provider credential references and vault boundaries.

Only :class:`CredentialRef.storage_value` is suitable for persistence.  Secret
material remains behind a desktop credential-vault or server KMS/secret-manager
adapter and is revealed only at the provider invocation boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import base64
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import re
import secrets
from typing import Protocol, runtime_checkable
from uuid import UUID


_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_KEY_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
_REFERENCE_PREFIX = "credential-ref:v1"
_SUPPORTED_BACKENDS = frozenset({"desktop_vault", "server_kms", "memory"})
_MAX_SECRET_BYTES = 64 * 1024


class CredentialVaultError(RuntimeError):
    """A stable, non-sensitive credential boundary error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _validate_component(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not _COMPONENT_PATTERN.fullmatch(value):
        raise CredentialVaultError(f"credential_{field}_invalid")
    return value


def _normalize_scope_id(value: str | UUID, *, field: str) -> str:
    normalized = str(value) if isinstance(value, UUID) else value
    return _validate_component(normalized, field=field)


@dataclass(frozen=True, slots=True)
class CredentialScope:
    tenant_id: str | UUID
    user_id: str | UUID

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "tenant_id",
            _normalize_scope_id(self.tenant_id, field="tenant_id"),
        )
        object.__setattr__(
            self,
            "user_id",
            _normalize_scope_id(self.user_id, field="user_id"),
        )


@dataclass(frozen=True, slots=True)
class CredentialRef:
    """An opaque, non-secret reference that is safe to store in the database."""

    backend: str
    tenant_id: str
    user_id: str
    provider_id: str
    key_id: str

    def __post_init__(self) -> None:
        if self.backend not in _SUPPORTED_BACKENDS:
            raise CredentialVaultError("credential_backend_invalid")
        _validate_component(self.tenant_id, field="tenant_id")
        _validate_component(self.user_id, field="user_id")
        _validate_component(self.provider_id, field="provider_id")
        if not isinstance(self.key_id, str) or not _KEY_ID_PATTERN.fullmatch(
            self.key_id
        ):
            raise CredentialVaultError("credential_key_id_invalid")

    @property
    def scope(self) -> CredentialScope:
        return CredentialScope(self.tenant_id, self.user_id)

    @property
    def storage_value(self) -> str:
        return ":".join(
            (
                _REFERENCE_PREFIX,
                self.backend,
                self.tenant_id,
                self.user_id,
                self.provider_id,
                self.key_id,
            )
        )

    def __str__(self) -> str:
        return self.storage_value

    @classmethod
    def parse(cls, value: str) -> CredentialRef:
        if not isinstance(value, str) or len(value) > 768:
            raise CredentialVaultError("credential_ref_invalid")
        parts = value.split(":")
        if len(parts) != 7 or parts[:2] != ["credential-ref", "v1"]:
            raise CredentialVaultError("credential_ref_invalid")
        try:
            return cls(
                backend=parts[2],
                tenant_id=parts[3],
                user_id=parts[4],
                provider_id=parts[5],
                key_id=parts[6],
            )
        except CredentialVaultError as error:
            raise CredentialVaultError("credential_ref_invalid") from error


class SecretValue:
    """A deliberately non-printable wrapper around provider secret material."""

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise CredentialVaultError("credential_secret_invalid")
        encoded = value.encode("utf-8")
        if not encoded or len(encoded) > _MAX_SECRET_BYTES or "\x00" in value:
            raise CredentialVaultError("credential_secret_invalid")
        self.__value = value

    def reveal(self) -> str:
        """Reveal only immediately before constructing an SDK/provider client."""

        return self.__value

    def __repr__(self) -> str:
        return "SecretValue(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


@runtime_checkable
class CredentialBackend(Protocol):
    """Low-level boundary implemented by OS vaults or server secret managers."""

    backend_id: str

    def put(self, reference: CredentialRef, secret: SecretValue) -> None: ...

    def get(self, reference: CredentialRef) -> SecretValue | None: ...

    def delete(self, reference: CredentialRef) -> None: ...


class DesktopVaultBackend(CredentialBackend, Protocol):
    """Electron/desktop adapter interface (for example, OS keychain storage)."""


class ServerKmsBackend(CredentialBackend, Protocol):
    """Server KMS/secret-manager adapter interface."""


class InMemoryCredentialBackend:
    """Deterministic fake for tests; never select this backend in production."""

    def __init__(self, *, backend_id: str = "memory") -> None:
        if backend_id not in _SUPPORTED_BACKENDS:
            raise CredentialVaultError("credential_backend_invalid")
        self.backend_id = backend_id
        self._secrets: dict[str, SecretValue] = {}

    def put(self, reference: CredentialRef, secret: SecretValue) -> None:
        if reference.backend != self.backend_id:
            raise CredentialVaultError("credential_backend_mismatch")
        if reference.storage_value in self._secrets:
            raise CredentialVaultError("credential_ref_conflict")
        self._secrets[reference.storage_value] = secret

    def get(self, reference: CredentialRef) -> SecretValue | None:
        if reference.backend != self.backend_id:
            return None
        return self._secrets.get(reference.storage_value)

    def delete(self, reference: CredentialRef) -> None:
        if reference.backend == self.backend_id:
            self._secrets.pop(reference.storage_value, None)

    @property
    def entry_count(self) -> int:
        return len(self._secrets)


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(value: bytes) -> tuple[_DataBlob, object]:
    buffer = ctypes.create_string_buffer(value)
    return (
        _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))),
        buffer,
    )


class WindowsDpapiCredentialBackend:
    """Per-user Windows DPAPI vault with opaque files keyed by CredentialRef."""

    backend_id = "desktop_vault"

    def __init__(self, root: Path) -> None:
        if os.name != "nt":
            raise CredentialVaultError("credential_desktop_vault_unavailable")
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, reference: CredentialRef) -> Path:
        return self.root / reference.tenant_id / reference.user_id / f"{reference.key_id}.bin"

    @staticmethod
    def _entropy(reference: CredentialRef) -> bytes:
        return (
            f"presenton-provider-v1:{reference.tenant_id}:"
            f"{reference.user_id}:{reference.provider_id}"
        ).encode("utf-8")

    def _protect(self, value: bytes, entropy: bytes) -> bytes:
        source, _source_buffer = _blob(value)
        entropy_blob, _entropy_buffer = _blob(entropy)
        output = _DataBlob()
        crypt32 = ctypes.windll.crypt32
        if not crypt32.CryptProtectData(
            ctypes.byref(source),
            None,
            ctypes.byref(entropy_blob),
            None,
            None,
            0x01,
            ctypes.byref(output),
        ):
            raise CredentialVaultError("credential_desktop_vault_write_failed")
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(output.pbData)

    def _unprotect(self, value: bytes, entropy: bytes) -> bytes:
        source, _source_buffer = _blob(value)
        entropy_blob, _entropy_buffer = _blob(entropy)
        output = _DataBlob()
        crypt32 = ctypes.windll.crypt32
        if not crypt32.CryptUnprotectData(
            ctypes.byref(source),
            None,
            ctypes.byref(entropy_blob),
            None,
            None,
            0x01,
            ctypes.byref(output),
        ):
            raise CredentialVaultError("credential_desktop_vault_read_failed")
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(output.pbData)

    def put(self, reference: CredentialRef, secret: SecretValue) -> None:
        if reference.backend != self.backend_id:
            raise CredentialVaultError("credential_backend_mismatch")
        destination = self._path(reference)
        destination.parent.mkdir(parents=True, exist_ok=True)
        encrypted = self._protect(
            secret.reveal().encode("utf-8"), self._entropy(reference)
        )
        temporary = destination.with_suffix(f".{secrets.token_hex(8)}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(base64.b64encode(encrypted))
                handle.flush()
                os.fsync(handle.fileno())
            if destination.exists():
                raise CredentialVaultError("credential_ref_conflict")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, reference: CredentialRef) -> SecretValue | None:
        if reference.backend != self.backend_id:
            return None
        path = self._path(reference)
        if not path.is_file():
            return None
        try:
            encrypted = base64.b64decode(path.read_bytes(), validate=True)
            plain = self._unprotect(encrypted, self._entropy(reference)).decode("utf-8")
        except (OSError, UnicodeDecodeError, ValueError) as error:
            raise CredentialVaultError("credential_desktop_vault_read_failed") from error
        return SecretValue(plain)

    def delete(self, reference: CredentialRef) -> None:
        if reference.backend != self.backend_id:
            raise CredentialVaultError("credential_backend_mismatch")
        self._path(reference).unlink(missing_ok=True)


class AwsSecretsManagerCredentialBackend:
    """Server secret-manager adapter; no database or filesystem fallback."""

    backend_id = "server_kms"

    def __init__(self, *, region: str | None = None, prefix: str = "presenton/provider") -> None:
        try:
            import boto3
        except ImportError as error:
            raise CredentialVaultError("credential_server_kms_unavailable") from error
        self._client = boto3.client("secretsmanager", region_name=region or None)
        self._prefix = prefix.strip("/")

    def _name(self, reference: CredentialRef) -> str:
        return "/".join(
            (
                self._prefix,
                reference.tenant_id,
                reference.user_id,
                reference.provider_id,
                reference.key_id,
            )
        )

    def put(self, reference: CredentialRef, secret: SecretValue) -> None:
        if reference.backend != self.backend_id:
            raise CredentialVaultError("credential_backend_mismatch")
        try:
            self._client.create_secret(
                Name=self._name(reference), SecretString=secret.reveal()
            )
        except Exception as error:
            raise CredentialVaultError("credential_server_kms_write_failed") from error

    def get(self, reference: CredentialRef) -> SecretValue | None:
        if reference.backend != self.backend_id:
            return None
        try:
            response = self._client.get_secret_value(SecretId=self._name(reference))
        except self._client.exceptions.ResourceNotFoundException:
            return None
        except Exception as error:
            raise CredentialVaultError("credential_server_kms_read_failed") from error
        value = response.get("SecretString")
        if not isinstance(value, str):
            raise CredentialVaultError("credential_server_kms_read_failed")
        return SecretValue(value)

    def delete(self, reference: CredentialRef) -> None:
        if reference.backend != self.backend_id:
            raise CredentialVaultError("credential_backend_mismatch")
        try:
            self._client.delete_secret(
                SecretId=self._name(reference), ForceDeleteWithoutRecovery=True
            )
        except self._client.exceptions.ResourceNotFoundException:
            return
        except Exception as error:
            raise CredentialVaultError("credential_server_kms_delete_failed") from error


class ProviderCredentialVault:
    """Enforce ownership checks around an injected desktop-vault/KMS backend."""

    def __init__(self, backend: CredentialBackend) -> None:
        if backend.backend_id not in _SUPPORTED_BACKENDS:
            raise CredentialVaultError("credential_backend_invalid")
        self._backend = backend

    def store(
        self,
        *,
        scope: CredentialScope,
        provider_id: str,
        secret: str,
    ) -> CredentialRef:
        provider_id = _validate_component(provider_id, field="provider_id")
        secret_value = SecretValue(secret)
        reference = CredentialRef(
            backend=self._backend.backend_id,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            provider_id=provider_id,
            key_id=secrets.token_hex(16),
        )
        self._backend.put(reference, secret_value)
        return reference

    def resolve(
        self,
        reference: CredentialRef | str,
        *,
        scope: CredentialScope,
        provider_id: str | None = None,
    ) -> SecretValue:
        parsed = (
            CredentialRef.parse(reference)
            if isinstance(reference, str)
            else reference
        )
        if parsed.scope != scope:
            raise CredentialVaultError("credential_scope_mismatch")
        if provider_id is not None and parsed.provider_id != _validate_component(
            provider_id, field="provider_id"
        ):
            raise CredentialVaultError("credential_provider_mismatch")
        if parsed.backend != self._backend.backend_id:
            raise CredentialVaultError("credential_backend_mismatch")
        secret = self._backend.get(parsed)
        if secret is None:
            raise CredentialVaultError("credential_not_found")
        return secret

    def delete(
        self,
        reference: CredentialRef | str,
        *,
        scope: CredentialScope,
    ) -> None:
        parsed = (
            CredentialRef.parse(reference)
            if isinstance(reference, str)
            else reference
        )
        if parsed.scope != scope:
            raise CredentialVaultError("credential_scope_mismatch")
        if parsed.backend != self._backend.backend_id:
            raise CredentialVaultError("credential_backend_mismatch")
        self._backend.delete(parsed)


def validate_persisted_credential_ref(value: object) -> CredentialRef:
    """Reject raw API keys or structured blobs at every DB write boundary."""

    if not isinstance(value, str):
        raise CredentialVaultError("credential_storage_requires_ref")
    try:
        return CredentialRef.parse(value)
    except CredentialVaultError as error:
        raise CredentialVaultError("credential_storage_requires_ref") from error


def provider_credential_vault_from_environment() -> ProviderCredentialVault:
    """Build the configured production backend without a plaintext fallback."""

    backend = (os.getenv("PROVIDER_CREDENTIAL_BACKEND") or "").strip().lower()
    if backend == "desktop_vault":
        root = Path(
            os.getenv("PROVIDER_DESKTOP_VAULT_DIRECTORY")
            or Path(os.getenv("APP_DATA_DIRECTORY") or "app_data")
            / "provider-credential-vault"
        )
        return ProviderCredentialVault(WindowsDpapiCredentialBackend(root))
    if backend == "server_kms":
        return ProviderCredentialVault(
            AwsSecretsManagerCredentialBackend(
                region=(os.getenv("AWS_REGION") or "").strip() or None,
                prefix=(
                    os.getenv("PROVIDER_SERVER_KMS_PREFIX")
                    or "presenton/provider"
                ),
            )
        )
    raise CredentialVaultError("credential_backend_not_configured")


__all__ = [
    "CredentialBackend",
    "CredentialRef",
    "CredentialScope",
    "CredentialVaultError",
    "DesktopVaultBackend",
    "AwsSecretsManagerCredentialBackend",
    "InMemoryCredentialBackend",
    "ProviderCredentialVault",
    "SecretValue",
    "ServerKmsBackend",
    "WindowsDpapiCredentialBackend",
    "provider_credential_vault_from_environment",
    "validate_persisted_credential_ref",
]
