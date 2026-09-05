"""SSRF guard for operator-supplied outbound URLs such as webhook targets.

The provider egress policy (``services.provider_egress_policy``) is an
allowlist for source-reviewed provider hosts. Webhook subscribers supply
arbitrary public URLs instead, so this module reuses the policy's hostname
normalisation, denied-host list, error type, redirect rejection and the
DNS-pinned aiohttp connector while replacing the allowlist with a strict
"public address only" rule that is enforced both on the URL literal and on
every DNS answer.

Self-hosted operators legitimately point a webhook at an internal host, so
``WEBHOOK_PRIVATE_TARGET_ALLOWLIST`` (env, comma-separated hostnames/
``*.``-wildcards/IP literals/CIDR ranges) opts specific named targets out of
the private-address rejection. It is empty by default: an unconfigured
deployment keeps the original fail-closed behaviour exactly.
"""

from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

import aiohttp

from services.image_generation_stock_adapter import pinned_connector
from services.provider_egress_policy import (
    AddressResolver,
    EgressPolicyError,
    ProviderEgressPolicy,
    _DENIED_HOSTNAMES,
    _is_denied_hostname,
    _matches_host_pattern,
    _normalize_hostname,
    _normalize_host_pattern,
    system_address_resolver,
)
from utils.get_env import get_webhook_private_target_allowlist_env

ALLOWED_SCHEMES = frozenset({"http", "https"})
_DEFAULT_PORTS = {"http": 80, "https": 443}
_MAX_URL_LENGTH = 2048

# Resolver used when a caller does not inject one. Tests replace this with an
# offline resolver; production code never reassigns it.
DEFAULT_RESOLVER: AddressResolver = system_address_resolver

# Seam for tests: production code never reassigns this. Indirection lets tests
# inject a value without touching the runtime-settings/env plumbing.
_get_private_allowlist_env = get_webhook_private_target_allowlist_env


@dataclass(frozen=True, slots=True)
class OutboundTarget:
    """A validated outbound URL with its public DNS answers pinned.

    Field names match ``ValidatedEndpoint`` so the pinned aiohttp connector and
    ``ProviderEgressPolicy.validate_connected_ip`` accept either object.
    """

    url: str
    scheme: str
    hostname: str
    port: int
    resolved_ips: frozenset[str]


IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def _embedded_ipv4(address: ipaddress.IPv6Address) -> tuple[IPAddress, ...]:
    """IPv4 addresses carried inside transition-mechanism IPv6 addresses."""

    embedded: list[IPAddress] = []
    if address.ipv4_mapped is not None:
        embedded.append(address.ipv4_mapped)
    if address.sixtofour is not None:
        embedded.append(address.sixtofour)
    if address.teredo is not None:
        embedded.extend(address.teredo)
    return tuple(embedded)


def _is_public_address(address: IPAddress) -> bool:
    # ``is_global`` alone is not enough: multicast (224/4, ff00::/8) and some
    # reserved ranges are not classified as private by ``ipaddress``.
    return not (
        address.is_multicast
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or not address.is_global
    )


def parse_public_address(value: str, *, allow_private: bool = False) -> IPAddress:
    """Parse an address literal or DNS answer, rejecting anything non-public.

    ``allow_private`` is set only for a target the operator explicitly named
    in ``WEBHOOK_PRIVATE_TARGET_ALLOWLIST`` (see ``_target_is_allowlisted``);
    every other caller keeps the strict public-address-only rule.
    """

    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise EgressPolicyError("egress_dns_answer_invalid") from error
    if allow_private:
        return address
    if isinstance(address, ipaddress.IPv6Address) and any(
        not _is_public_address(embedded) for embedded in _embedded_ipv4(address)
    ):
        raise EgressPolicyError("egress_private_address_denied")
    if not _is_public_address(address):
        raise EgressPolicyError("egress_private_address_denied")
    return address


def _parse_private_allowlist(raw: str) -> tuple[tuple[str, ...], tuple[
    ipaddress.IPv4Network | ipaddress.IPv6Network, ...
]]:
    """Split ``WEBHOOK_PRIVATE_TARGET_ALLOWLIST`` into host patterns and networks.

    Each comma-separated entry is either an IP address/CIDR range or an exact
    or ``*.``-wildcard hostname. A malformed entry is dropped rather than
    raised: one operator typo must not crash delivery for every subscription.
    """

    host_patterns: list[str] = []
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in raw.split(","):
        candidate = item.strip()
        if not candidate:
            continue
        try:
            networks.append(ipaddress.ip_network(candidate, strict=False))
            continue
        except ValueError:
            pass
        try:
            host_patterns.append(_normalize_host_pattern(candidate))
        except EgressPolicyError:
            continue
    return tuple(host_patterns), tuple(networks)


def _target_is_allowlisted(
    *, hostname: str, is_literal: bool, ip_literal: IPAddress | None
) -> bool:
    """Whether this webhook target is an operator-declared private exception.

    A hostname match trusts every DNS answer for that name (the whole point
    of naming an internal host is that it resolves privately); an IP literal
    or CIDR entry matches only that literal address, never a hostname that
    happens to resolve into the range.
    """

    raw = (_get_private_allowlist_env() or "").strip()
    if not raw:
        return False
    host_patterns, networks = _parse_private_allowlist(raw)
    if is_literal:
        return ip_literal is not None and any(
            ip_literal in network for network in networks
        )
    return any(_matches_host_pattern(hostname, pattern) for pattern in host_patterns)


def _resolve(
    hostname: str, port: int, resolver: AddressResolver | None
) -> tuple[str, ...]:
    try:
        answers = tuple((resolver or DEFAULT_RESOLVER)(hostname, port))
    except Exception as error:
        raise EgressPolicyError("egress_dns_unavailable") from error
    if not answers:
        raise EgressPolicyError("egress_dns_unavailable")
    return answers


def validate_outbound_url(
    url: str,
    *,
    resolver: AddressResolver | None = None,
) -> OutboundTarget:
    """Validate a user-supplied URL and pin its public DNS answers.

    Raises ``EgressPolicyError`` with a stable code (never echoing the URL)
    when the URL is malformed, uses a scheme other than http(s), embeds
    credentials, names a loopback/metadata host, or resolves to any
    non-public address.
    """

    if (
        not isinstance(url, str)
        or not url
        or len(url) > _MAX_URL_LENGTH
        or any(character.isspace() for character in url)
        or "\\" in url
    ):
        raise EgressPolicyError("egress_endpoint_invalid")
    try:
        parsed = urlsplit(url)
        hostname_value = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise EgressPolicyError("egress_endpoint_invalid") from error

    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise EgressPolicyError("egress_scheme_denied")
    if not parsed.netloc or hostname_value is None or parsed.fragment:
        raise EgressPolicyError("egress_endpoint_invalid")
    if parsed.username is not None or parsed.password is not None:
        raise EgressPolicyError("egress_url_credentials_denied")

    effective_port = port or _DEFAULT_PORTS[scheme]
    literal = hostname_value.strip("[]")
    try:
        ipaddress.ip_address(literal)
    except ValueError:
        is_literal = False
    else:
        is_literal = True

    if is_literal:
        # An IP literal needs no DNS, but must still be a public address
        # unless the operator explicitly allowlisted this exact literal.
        hostname = literal.lower()
        answers: tuple[str, ...] = (literal,)
        allow_private = _target_is_allowlisted(
            hostname=hostname,
            is_literal=True,
            ip_literal=ipaddress.ip_address(literal),
        )
    else:
        # Deny-list before normalisation so single-label names such as
        # ``localhost`` report the denial rather than a generic syntax error.
        # The four cloud-metadata aliases (``_DENIED_HOSTNAMES``) are never
        # allowlist-overridable; the broader localhost/.local/.internal
        # suffix rule is, since that is exactly how a self-hosted operator
        # names their own internal webhook receiver.
        pre_normalized = literal.rstrip(".").lower()
        if pre_normalized in _DENIED_HOSTNAMES or (
            _is_denied_hostname(pre_normalized)
            and not _target_is_allowlisted(
                hostname=pre_normalized, is_literal=False, ip_literal=None
            )
        ):
            raise EgressPolicyError("egress_metadata_or_loopback_denied")
        hostname = _normalize_hostname(hostname_value)
        allow_private = _target_is_allowlisted(
            hostname=hostname, is_literal=False, ip_literal=None
        )
        if hostname in _DENIED_HOSTNAMES or (
            _is_denied_hostname(hostname) and not allow_private
        ):
            raise EgressPolicyError("egress_metadata_or_loopback_denied")
        # ``127.1``, ``0x7f.1`` and similar inet_aton shorthands are not DNS
        # names (a TLD is never numeric); refuse them instead of letting the
        # system resolver canonicalise them into an address.
        if hostname.rsplit(".", 1)[-1].isdigit():
            raise EgressPolicyError("egress_endpoint_invalid")
        answers = _resolve(hostname, effective_port, resolver)

    resolved = frozenset(
        str(parse_public_address(answer, allow_private=allow_private))
        for answer in answers
    )
    return OutboundTarget(
        url=url,
        scheme=scheme,
        hostname=hostname,
        port=effective_port,
        resolved_ips=resolved,
    )


async def validate_outbound_url_async(
    url: str,
    *,
    resolver: AddressResolver | None = None,
) -> OutboundTarget:
    """Run the blocking DNS step off the event loop."""

    return await asyncio.to_thread(validate_outbound_url, url, resolver=resolver)


def pinned_client_session(target: OutboundTarget) -> aiohttp.ClientSession:
    """A session that can only connect to the addresses pinned by validation."""

    return aiohttp.ClientSession(
        trust_env=False,
        connector=pinned_connector(
            target,  # type: ignore[arg-type]  # duck-typed ValidatedEndpoint
            connected_ip_validator=ProviderEgressPolicy.validate_connected_ip,
        ),
    )


reject_redirect = ProviderEgressPolicy.reject_redirect


__all__ = [
    "ALLOWED_SCHEMES",
    "DEFAULT_RESOLVER",
    "EgressPolicyError",
    "OutboundTarget",
    "parse_public_address",
    "pinned_client_session",
    "reject_redirect",
    "validate_outbound_url",
    "validate_outbound_url_async",
]
