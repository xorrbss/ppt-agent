"""Fail-closed outbound-provider policy for hosted and self-hosted runtimes."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
import ipaddress
import re
import socket
import os
from urllib.parse import parse_qsl, urlsplit


_SERVICE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_HOST_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_SENSITIVE_QUERY_NAMES = frozenset(
    {
        "access_token",
        "api-key",
        "api_key",
        "apikey",
        "authorization",
        "key",
        "password",
        "secret",
        "sig",
        "signature",
        "token",
    }
)
_DENIED_HOSTNAMES = frozenset(
    {
        "instance-data",
        "instance-data.ec2.internal",
        "metadata",
        "metadata.google.internal",
    }
)


class EgressPolicyError(RuntimeError):
    """Stable public policy error that never embeds a URL, address, or secret."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class EgressCategory(str, Enum):
    PROVIDER = "provider"
    IMAGE = "image"
    SEARCH = "search"
    FONT = "font"
    TELEMETRY = "telemetry"


def _normalize_service_id(value: str) -> str:
    if not isinstance(value, str):
        raise EgressPolicyError("egress_service_id_invalid")
    normalized = value.strip().lower()
    if not _SERVICE_ID_PATTERN.fullmatch(normalized):
        raise EgressPolicyError("egress_service_id_invalid")
    return normalized


def _normalize_hostname(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 253:
        raise EgressPolicyError("egress_hostname_invalid")
    if any(character.isspace() for character in value) or "%" in value:
        raise EgressPolicyError("egress_hostname_invalid")
    candidate = value.rstrip(".").lower()
    try:
        normalized = candidate.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise EgressPolicyError("egress_hostname_invalid") from error
    labels = normalized.split(".")
    if len(labels) < 2 or any(
        not _HOST_LABEL_PATTERN.fullmatch(label) for label in labels
    ):
        raise EgressPolicyError("egress_hostname_invalid")
    return normalized


def _normalize_host_pattern(value: str) -> str:
    if value.startswith("*."):
        return f"*.{_normalize_hostname(value[2:])}"
    return _normalize_hostname(value)


def _matches_host_pattern(hostname: str, pattern: str) -> bool:
    if not pattern.startswith("*."):
        return hostname == pattern
    suffix = pattern[1:]
    return hostname.endswith(suffix) and hostname != pattern[2:]


@dataclass(frozen=True, slots=True)
class EgressRule:
    category: EgressCategory
    service_id: str
    allowed_hostnames: tuple[str, ...]
    enabled: bool = False
    allowed_ports: frozenset[int] = frozenset({443})

    def __post_init__(self) -> None:
        try:
            category = EgressCategory(self.category)
        except ValueError as error:
            raise EgressPolicyError("egress_category_invalid") from error
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "service_id", _normalize_service_id(self.service_id))
        patterns = tuple(
            sorted({_normalize_host_pattern(host) for host in self.allowed_hostnames})
        )
        if self.enabled and not patterns:
            raise EgressPolicyError("egress_enabled_rule_requires_hostname")
        object.__setattr__(self, "allowed_hostnames", patterns)
        ports = frozenset(self.allowed_ports)
        if not ports or any(
            not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535
            for port in ports
        ):
            raise EgressPolicyError("egress_port_invalid")
        object.__setattr__(self, "allowed_ports", ports)


@dataclass(frozen=True, slots=True)
class EgressManifest:
    """Auditable declarations for every outbound data category."""

    rules: tuple[EgressRule, ...]
    self_hosted: bool = True
    telemetry_opt_in: bool = False

    def __post_init__(self) -> None:
        entries: set[tuple[EgressCategory, str]] = set()
        categories: set[EgressCategory] = set()
        for rule in self.rules:
            key = (rule.category, rule.service_id)
            if key in entries:
                raise EgressPolicyError("egress_manifest_duplicate_rule")
            entries.add(key)
            categories.add(rule.category)
        missing = set(EgressCategory) - categories
        if missing:
            raise EgressPolicyError("egress_manifest_incomplete")
        if any(
            rule.enabled and rule.category is EgressCategory.TELEMETRY
            for rule in self.rules
        ) and not self.telemetry_opt_in:
            raise EgressPolicyError("telemetry_requires_explicit_opt_in")

    @classmethod
    def self_hosted_default(cls) -> EgressManifest:
        return cls(
            rules=tuple(
                EgressRule(
                    category=category,
                    service_id="default",
                    allowed_hostnames=(),
                    enabled=False,
                )
                for category in EgressCategory
            ),
            self_hosted=True,
            telemetry_opt_in=False,
        )

    def rule_for(
        self, category: EgressCategory | str, service_id: str
    ) -> EgressRule:
        try:
            normalized_category = EgressCategory(category)
        except ValueError as error:
            raise EgressPolicyError("egress_category_invalid") from error
        normalized_service = _normalize_service_id(service_id)
        for rule in self.rules:
            if (
                rule.category is normalized_category
                and rule.service_id == normalized_service
            ):
                return rule
        raise EgressPolicyError("egress_destination_not_declared")

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "self_hosted": self.self_hosted,
            "telemetry_opt_in": self.telemetry_opt_in,
            "rules": [
                {
                    "category": rule.category.value,
                    "service_id": rule.service_id,
                    "enabled": rule.enabled,
                    "allowed_hostnames": list(rule.allowed_hostnames),
                    "allowed_ports": sorted(rule.allowed_ports),
                }
                for rule in self.rules
            ],
        }


@dataclass(frozen=True, slots=True)
class ValidatedEndpoint:
    """A hostname-authorized endpoint with DNS answers pinned for connection."""

    url: str
    category: EgressCategory
    service_id: str
    hostname: str
    port: int
    resolved_ips: frozenset[str]


AddressResolver = Callable[[str, int], Iterable[str]]


def system_address_resolver(hostname: str, port: int) -> tuple[str, ...]:
    """Production resolver. Tests should inject a deterministic offline resolver."""

    return tuple(
        result[4][0]
        for result in socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    )


def _parse_public_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise EgressPolicyError("egress_dns_answer_invalid") from error
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if not address.is_global:
        raise EgressPolicyError("egress_private_address_denied")
    return address


def _is_denied_hostname(hostname: str) -> bool:
    return (
        hostname in _DENIED_HOSTNAMES
        or hostname == "localhost"
        or hostname.endswith(".localhost")
        or hostname.endswith(".local")
        or hostname.endswith(".internal")
    )


class ProviderEgressPolicy:
    """Authorize an endpoint before I/O and pin its public DNS answers."""

    def __init__(
        self,
        manifest: EgressManifest | None = None,
        *,
        resolver: AddressResolver = system_address_resolver,
    ) -> None:
        self.manifest = manifest or EgressManifest.self_hosted_default()
        self._resolver = resolver

    def validate_custom_endpoint(
        self,
        endpoint: str,
        *,
        category: EgressCategory | str,
        service_id: str,
    ) -> ValidatedEndpoint:
        if (
            not isinstance(endpoint, str)
            or not endpoint
            or len(endpoint) > 2048
            or any(character.isspace() for character in endpoint)
            or "\\" in endpoint
        ):
            raise EgressPolicyError("egress_endpoint_invalid")
        try:
            parsed = urlsplit(endpoint)
            hostname_value = parsed.hostname
            port = parsed.port
        except ValueError as error:
            raise EgressPolicyError("egress_endpoint_invalid") from error
        if (
            parsed.scheme.lower() != "https"
            or not parsed.netloc
            or hostname_value is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise EgressPolicyError("egress_endpoint_invalid")
        if any(
            name.lower() in _SENSITIVE_QUERY_NAMES
            for name, _value in parse_qsl(parsed.query, keep_blank_values=True)
        ):
            raise EgressPolicyError("egress_url_credentials_denied")

        try:
            ipaddress.ip_address(hostname_value)
        except ValueError:
            hostname = _normalize_hostname(hostname_value)
        else:
            raise EgressPolicyError("egress_ip_literal_denied")
        if _is_denied_hostname(hostname):
            raise EgressPolicyError("egress_metadata_or_loopback_denied")

        normalized_category = EgressCategory(category)
        normalized_service = _normalize_service_id(service_id)
        rule = self.manifest.rule_for(normalized_category, normalized_service)
        if not rule.enabled:
            raise EgressPolicyError("egress_destination_disabled")
        if not any(
            _matches_host_pattern(hostname, pattern)
            for pattern in rule.allowed_hostnames
        ):
            raise EgressPolicyError("egress_hostname_not_allowlisted")
        effective_port = port or 443
        if effective_port not in rule.allowed_ports:
            raise EgressPolicyError("egress_port_denied")

        try:
            answers = tuple(self._resolver(hostname, effective_port))
        except Exception as error:
            raise EgressPolicyError("egress_dns_unavailable") from error
        if not answers:
            raise EgressPolicyError("egress_dns_unavailable")
        public_ips = frozenset(str(_parse_public_ip(answer)) for answer in answers)
        return ValidatedEndpoint(
            url=endpoint,
            category=normalized_category,
            service_id=normalized_service,
            hostname=hostname,
            port=effective_port,
            resolved_ips=public_ips,
        )

    @staticmethod
    def validate_connected_ip(endpoint: ValidatedEndpoint, connected_ip: str) -> None:
        """Call after connect to close DNS-rebinding/alternate-route gaps."""

        normalized = str(_parse_public_ip(connected_ip))
        if normalized not in endpoint.resolved_ips:
            raise EgressPolicyError("egress_dns_pin_mismatch")

    @staticmethod
    def reject_redirect(status_code: int, location: str | None = None) -> None:
        """Custom endpoints are invoked with redirects disabled, always."""

        del location  # Never include an attacker-controlled redirect URL in errors.
        if 300 <= status_code < 400:
            raise EgressPolicyError("egress_redirect_denied")


def validate_configured_provider_endpoint(
    endpoint: str,
    *,
    category: EgressCategory | str,
    service_id: str,
    allowlist_env: str,
    telemetry_opt_in: bool = False,
    resolver: AddressResolver = system_address_resolver,
) -> ValidatedEndpoint:
    """Validate one configured endpoint against an operator-owned allowlist.

    Custom endpoints are disabled until the named environment variable contains
    one or more comma-separated exact/wildcard hostnames. This intentionally has
    no permissive fallback for a URL stored in user settings.
    """

    raw = os.getenv(allowlist_env, "")
    allowed = tuple(item.strip() for item in raw.split(",") if item.strip())
    requested_category = EgressCategory(category)
    requested_service = _normalize_service_id(service_id)
    rules = []
    for candidate in EgressCategory:
        matches = candidate is requested_category
        rules.append(
            EgressRule(
                category=candidate,
                service_id=requested_service if matches else "default",
                allowed_hostnames=allowed if matches else (),
                enabled=bool(allowed) if matches else False,
            )
        )
    return ProviderEgressPolicy(
        EgressManifest(
            rules=tuple(rules),
            telemetry_opt_in=telemetry_opt_in,
        ),
        resolver=resolver,
    ).validate_custom_endpoint(
        endpoint,
        category=requested_category,
        service_id=requested_service,
    )


def validate_builtin_provider_endpoint(
    endpoint: str,
    *,
    category: EgressCategory | str,
    service_id: str,
    allowed_hostnames: tuple[str, ...],
    resolver: AddressResolver = system_address_resolver,
) -> ValidatedEndpoint:
    """Validate a source-reviewed built-in provider endpoint with DNS pinning."""

    requested_category = EgressCategory(category)
    requested_service = _normalize_service_id(service_id)
    rules = tuple(
        EgressRule(
            category=candidate,
            service_id=(requested_service if candidate is requested_category else "default"),
            allowed_hostnames=(
                allowed_hostnames if candidate is requested_category else ()
            ),
            enabled=candidate is requested_category,
        )
        for candidate in EgressCategory
    )
    return ProviderEgressPolicy(
        EgressManifest(rules=rules), resolver=resolver
    ).validate_custom_endpoint(
        endpoint,
        category=requested_category,
        service_id=requested_service,
    )


__all__ = [
    "AddressResolver",
    "EgressCategory",
    "EgressManifest",
    "EgressPolicyError",
    "EgressRule",
    "ProviderEgressPolicy",
    "ValidatedEndpoint",
    "system_address_resolver",
    "validate_builtin_provider_endpoint",
    "validate_configured_provider_endpoint",
]
