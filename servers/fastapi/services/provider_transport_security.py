"""Fail-closed rules for SDKs that cannot prove transport-level DNS pinning."""

from __future__ import annotations

from collections.abc import Mapping
import os

from services.provider_egress_policy import EgressPolicyError, ValidatedEndpoint


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_MANAGED_TIERS = frozenset({"staging", "production"})


def provider_secure_mode(environ: Mapping[str, str] | None = None) -> bool:
    """Return whether transport-level provider guarantees are mandatory."""

    values = os.environ if environ is None else environ
    tier = values.get("TEMPLATE_V2_DEPLOYMENT_TIER", "local").strip().lower()
    secure_mode = values.get("PROVIDER_SECURE_MODE", "").strip().lower()
    return tier in _MANAGED_TIERS or secure_mode in _TRUE_VALUES


def require_pinned_transport(
    endpoint: ValidatedEndpoint,
    *,
    transport_pins_connected_ip: bool,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Reject a custom SDK boundary when secure mode cannot enforce the DNS pin.

    Endpoint admission only records public DNS answers.  A transport must either
    connect exclusively to those addresses and retain hostname TLS verification,
    or the custom provider is unavailable in secure/managed deployments.
    """

    del endpoint
    if provider_secure_mode(environ) and not transport_pins_connected_ip:
        raise EgressPolicyError("egress_connected_ip_pin_unavailable")


__all__ = ["provider_secure_mode", "require_pinned_transport"]
