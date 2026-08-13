"""Default-off admission boundary for optional Sentry telemetry."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from services.provider_egress_policy import (
    EgressCategory,
    EgressPolicyError,
    validate_configured_provider_endpoint,
)
from services.provider_transport_security import require_pinned_transport
from utils.get_env import (
    get_sentry_dsn_env,
    get_sentry_send_default_pii_env,
    get_sentry_traces_sample_rate_env,
)


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_TELEMETRY_ALLOWLIST_ENV = "PROVIDER_TELEMETRY_EGRESS_ALLOWLIST"
_TELEMETRY_OPT_IN_ENV = "PROVIDER_TELEMETRY_EGRESS_OPT_IN"


def _is_true(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUE_VALUES


def _endpoint_without_dsn_credentials(dsn: str) -> str:
    """Project a Sentry DSN to the credential-free origin admitted by policy."""

    try:
        parsed = urlsplit(dsn)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise EgressPolicyError("egress_endpoint_invalid") from error
    if not hostname:
        raise EgressPolicyError("egress_endpoint_invalid")
    authority = hostname if port is None else f"{hostname}:{port}"
    return f"{parsed.scheme}://{authority}/"


def maybe_init_sentry() -> bool:
    """Initialize Sentry only after explicit telemetry admission.

    The Sentry SDK does not expose a connected-peer IP assertion. Managed and
    explicit secure runtimes therefore reject it even after DNS admission.
    """

    sentry_dsn = get_sentry_dsn_env()
    if not sentry_dsn:
        return False
    try:
        endpoint = validate_configured_provider_endpoint(
            _endpoint_without_dsn_credentials(sentry_dsn),
            category=EgressCategory.TELEMETRY,
            service_id="sentry",
            allowlist_env=_TELEMETRY_ALLOWLIST_ENV,
            telemetry_opt_in=_is_true(os.getenv(_TELEMETRY_OPT_IN_ENV)),
        )
        require_pinned_transport(
            endpoint,
            transport_pins_connected_ip=False,
        )
    except EgressPolicyError:
        return False

    try:
        import sentry_sdk
    except Exception:
        return False

    traces_sample_rate = get_sentry_traces_sample_rate_env()
    send_default_pii = get_sentry_send_default_pii_env()
    try:
        parsed_sample_rate = (
            float(traces_sample_rate) if traces_sample_rate is not None else 1.0
        )
    except ValueError:
        parsed_sample_rate = 1.0

    parsed_send_default_pii = (
        send_default_pii.lower() == "true" if send_default_pii is not None else True
    )
    sentry_sdk.init(
        dsn=sentry_dsn,
        send_default_pii=parsed_send_default_pii,
        traces_sample_rate=parsed_sample_rate,
    )
    return True


__all__ = ["maybe_init_sentry"]
