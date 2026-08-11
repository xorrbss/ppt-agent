"""Canonical provider policy and server-only credential resolution.

The JSON source is the only provider catalog.  Clients receive the generated
public projection; this module intentionally retains credential *references*
only and never returns stored credential values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from constants.provider_registry_generated import PROVIDERS_BY_ID, public_provider_metadata


class ProviderResolutionError(ValueError):
    """Fail-closed provider selection error with a stable machine code."""

    def __init__(self, code: str, provider_id: str) -> None:
        self.code = code
        self.provider_id = provider_id
        super().__init__(f"{code}: {provider_id}")


@dataclass(frozen=True)
class ResolvedProvider:
    provider_id: str
    kind: str
    display_name: str
    capabilities: Mapping[str, str]
    presentation_drivers: tuple[str, ...]
    credential_setting_keys: tuple[str, ...]


def _provider_or_fail(provider_id: str) -> Mapping[str, Any]:
    provider = PROVIDERS_BY_ID.get(provider_id)
    if provider is None:
        raise ProviderResolutionError("provider_unknown", provider_id)
    if not provider["enabled"]:
        raise ProviderResolutionError("provider_disabled", provider_id)
    return provider


def resolve_provider(
    provider_id: str,
    *,
    required_capabilities: Iterable[str] = (),
    presentation_driver: str | None = None,
) -> ResolvedProvider:
    """Resolve only enabled providers and reject unknown/mismatched policy.

    ``conditional`` is not an entitlement: a caller must make a model-specific
    capability decision before requesting the operation.  This protects future
    provider/model combinations from silently acquiring powers.
    """

    provider = _provider_or_fail(provider_id)
    if presentation_driver is not None and presentation_driver not in provider["presentationDrivers"]:
        raise ProviderResolutionError("provider_driver_mismatch", provider_id)
    for capability in required_capabilities:
        if provider["capabilities"].get(capability) != "supported":
            raise ProviderResolutionError("provider_capability_mismatch", provider_id)
    return ResolvedProvider(
        provider_id=provider["id"],
        kind=provider["kind"],
        display_name=provider["displayName"],
        capabilities=provider["capabilities"],
        presentation_drivers=tuple(provider["presentationDrivers"]),
        credential_setting_keys=tuple(
            credential["settingKey"] for credential in provider["credentialSchema"]
        ),
    )


def get_public_provider_metadata() -> tuple[Mapping[str, Any], ...]:
    """Client-safe catalog with credential schemas and values removed."""

    return public_provider_metadata()


def credential_setting_keys_for(provider_id: str) -> tuple[str, ...]:
    """Server-only credential-reference resolver; never reads secret values."""

    return resolve_provider(provider_id).credential_setting_keys
