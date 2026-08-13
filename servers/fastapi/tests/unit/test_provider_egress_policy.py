from __future__ import annotations

import pytest

from services.provider_egress_policy import (
    EgressCategory,
    EgressManifest,
    EgressPolicyError,
    EgressRule,
    ProviderEgressPolicy,
    validate_builtin_provider_endpoint,
)
from services.provider_transport_security import require_pinned_transport


PUBLIC_IP = "93.184.216.34"


def _manifest(
    *,
    category: EgressCategory = EgressCategory.PROVIDER,
    service_id: str = "custom-openai",
    hostname: str = "api.example.com",
    telemetry_opt_in: bool = False,
) -> EgressManifest:
    rules = []
    for candidate in EgressCategory:
        enabled = candidate is category
        rules.append(
            EgressRule(
                category=candidate,
                service_id=service_id if enabled else "default",
                allowed_hostnames=(hostname,) if enabled else (),
                enabled=enabled,
            )
        )
    return EgressManifest(
        rules=tuple(rules),
        self_hosted=True,
        telemetry_opt_in=telemetry_opt_in,
    )


def _offline_resolver(_hostname: str, _port: int) -> tuple[str, ...]:
    return (PUBLIC_IP,)


def test_self_hosted_manifest_declares_all_categories_and_defaults_off() -> None:
    manifest = EgressManifest.self_hosted_default()
    safe = manifest.to_safe_dict()

    assert {rule["category"] for rule in safe["rules"]} == {
        "provider",
        "image",
        "search",
        "font",
        "telemetry",
    }
    assert all(not rule["enabled"] for rule in safe["rules"])
    assert safe["self_hosted"] is True
    assert safe["telemetry_opt_in"] is False


def test_default_off_rejects_before_dns_resolution() -> None:
    called = False

    def forbidden_resolver(_hostname: str, _port: int) -> tuple[str, ...]:
        nonlocal called
        called = True
        raise AssertionError("DNS must not be reached")

    policy = ProviderEgressPolicy(resolver=forbidden_resolver)

    with pytest.raises(EgressPolicyError, match="egress_destination_not_declared"):
        policy.validate_custom_endpoint(
            "https://api.example.com/v1",
            category=EgressCategory.PROVIDER,
            service_id="custom-openai",
        )
    assert called is False


def test_explicit_provider_manifest_allows_https_and_pins_public_dns() -> None:
    calls: list[tuple[str, int]] = []

    def resolver(hostname: str, port: int) -> tuple[str, ...]:
        calls.append((hostname, port))
        return (PUBLIC_IP, "2606:2800:220:1:248:1893:25c8:1946")

    policy = ProviderEgressPolicy(_manifest(), resolver=resolver)
    validated = policy.validate_custom_endpoint(
        "https://api.example.com/v1/responses",
        category="provider",
        service_id="custom-openai",
    )

    assert calls == [("api.example.com", 443)]
    assert validated.hostname == "api.example.com"
    assert validated.resolved_ips == frozenset(
        {PUBLIC_IP, "2606:2800:220:1:248:1893:25c8:1946"}
    )
    policy.validate_connected_ip(validated, PUBLIC_IP)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://api.example.com/v1",
        "https://user:password@api.example.com/v1",
        "https://api.example.com/v1#fragment",
        "https://api.example.com/v1?api_key=private-canary",
        "https://api.example.com\\@127.0.0.1/v1",
        "https://93.184.216.34/v1",
    ],
)
def test_endpoint_parser_rejects_unsafe_forms_without_dns(endpoint: str) -> None:
    called = False

    def resolver(_hostname: str, _port: int) -> tuple[str, ...]:
        nonlocal called
        called = True
        return (PUBLIC_IP,)

    policy = ProviderEgressPolicy(_manifest(), resolver=resolver)

    with pytest.raises(EgressPolicyError):
        policy.validate_custom_endpoint(
            endpoint,
            category="provider",
            service_id="custom-openai",
        )
    assert called is False


@pytest.mark.parametrize(
    "hostname",
    [
        "localhost",
        "service.localhost",
        "metadata.google.internal",
        "instance-data.ec2.internal",
    ],
)
def test_loopback_and_metadata_hostnames_fail_closed(hostname: str) -> None:
    with pytest.raises(EgressPolicyError):
        manifest = _manifest(hostname=hostname)
        policy = ProviderEgressPolicy(manifest, resolver=_offline_resolver)
        policy.validate_custom_endpoint(
            f"https://{hostname}/v1",
            category="provider",
            service_id="custom-openai",
        )


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.100.100.200",
        "0.0.0.0",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",
    ],
)
def test_any_private_reserved_metadata_or_loopback_dns_answer_denies(address: str) -> None:
    policy = ProviderEgressPolicy(
        _manifest(), resolver=lambda _host, _port: (PUBLIC_IP, address)
    )

    with pytest.raises(EgressPolicyError, match="egress_private_address_denied"):
        policy.validate_custom_endpoint(
            "https://api.example.com/v1",
            category="provider",
            service_id="custom-openai",
        )


def test_dns_failure_empty_answer_and_pin_mismatch_fail_closed() -> None:
    unavailable = ProviderEgressPolicy(
        _manifest(), resolver=lambda _host, _port: ()
    )
    with pytest.raises(EgressPolicyError, match="egress_dns_unavailable"):
        unavailable.validate_custom_endpoint(
            "https://api.example.com/v1",
            category="provider",
            service_id="custom-openai",
        )

    policy = ProviderEgressPolicy(_manifest(), resolver=_offline_resolver)
    validated = policy.validate_custom_endpoint(
        "https://api.example.com/v1",
        category="provider",
        service_id="custom-openai",
    )
    with pytest.raises(EgressPolicyError, match="egress_dns_pin_mismatch"):
        policy.validate_connected_ip(validated, "8.8.8.8")


def test_exact_and_wildcard_hostname_allowlist_cannot_be_bypassed() -> None:
    exact = ProviderEgressPolicy(_manifest(), resolver=_offline_resolver)
    with pytest.raises(EgressPolicyError, match="egress_hostname_not_allowlisted"):
        exact.validate_custom_endpoint(
            "https://api.example.com.attacker.test/v1",
            category="provider",
            service_id="custom-openai",
        )

    wildcard = ProviderEgressPolicy(
        _manifest(hostname="*.example.com"), resolver=_offline_resolver
    )
    wildcard.validate_custom_endpoint(
        "https://images.example.com/v1",
        category="provider",
        service_id="custom-openai",
    )
    with pytest.raises(EgressPolicyError, match="egress_hostname_not_allowlisted"):
        wildcard.validate_custom_endpoint(
            "https://example.com/v1",
            category="provider",
            service_id="custom-openai",
        )


def test_redirects_are_never_followed() -> None:
    for status in (300, 301, 302, 303, 307, 308):
        with pytest.raises(EgressPolicyError, match="egress_redirect_denied"):
            ProviderEgressPolicy.reject_redirect(
                status, "https://169.254.169.254/latest/meta-data"
            )
    ProviderEgressPolicy.reject_redirect(200)


def test_telemetry_requires_separate_explicit_privacy_opt_in() -> None:
    with pytest.raises(
        EgressPolicyError, match="telemetry_requires_explicit_opt_in"
    ):
        _manifest(
            category=EgressCategory.TELEMETRY,
            service_id="otel",
            hostname="telemetry.example.com",
            telemetry_opt_in=False,
        )

    manifest = _manifest(
        category=EgressCategory.TELEMETRY,
        service_id="otel",
        hostname="telemetry.example.com",
        telemetry_opt_in=True,
    )
    endpoint = ProviderEgressPolicy(
        manifest, resolver=_offline_resolver
    ).validate_custom_endpoint(
        "https://telemetry.example.com/v1/traces",
        category="telemetry",
        service_id="otel",
    )
    assert endpoint.category is EgressCategory.TELEMETRY


def test_builtin_search_endpoint_is_exact_host_and_public_ip_pinned() -> None:
    endpoint = validate_builtin_provider_endpoint(
        "https://api.pexels.com/v1/search",
        category=EgressCategory.SEARCH,
        service_id="pexels",
        allowed_hostnames=("api.pexels.com",),
        resolver=_offline_resolver,
    )
    assert endpoint.hostname == "api.pexels.com"
    ProviderEgressPolicy.validate_connected_ip(endpoint, PUBLIC_IP)

    with pytest.raises(EgressPolicyError, match="egress_hostname_not_allowlisted"):
        validate_builtin_provider_endpoint(
            "https://api.pexels.com.attacker.test/v1/search",
            category=EgressCategory.SEARCH,
            service_id="pexels",
            allowed_hostnames=("api.pexels.com",),
            resolver=_offline_resolver,
        )


def test_dns_admission_does_not_claim_sdk_connection_pinning() -> None:
    """Regression: validated DNS answers were never enforced by the SDK socket."""

    endpoint = ProviderEgressPolicy(
        _manifest(), resolver=_offline_resolver
    ).validate_custom_endpoint(
        "https://api.example.com/v1",
        category="provider",
        service_id="custom-openai",
    )

    with pytest.raises(
        EgressPolicyError, match="egress_connected_ip_pin_unavailable"
    ):
        require_pinned_transport(
            endpoint,
            transport_pins_connected_ip=False,
            environ={"PROVIDER_SECURE_MODE": "true"},
        )

    # A transport such as the repository's pinned aiohttp connector may attest
    # the guarantee, while compatibility mode preserves local custom providers.
    require_pinned_transport(
        endpoint,
        transport_pins_connected_ip=True,
        environ={"PROVIDER_SECURE_MODE": "true"},
    )
    require_pinned_transport(
        endpoint,
        transport_pins_connected_ip=False,
        environ={"PROVIDER_SECURE_MODE": "false"},
    )
