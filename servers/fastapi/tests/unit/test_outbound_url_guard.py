"""Outbound URL guard: user-supplied URLs may only reach public http(s) hosts."""
import pytest

from services import outbound_url_guard as guard
from services.outbound_url_guard import (
    EgressPolicyError,
    parse_public_address,
    reject_redirect,
    validate_outbound_url,
)

_PUBLIC = "93.184.216.34"


def _resolver_returning(*answers: str):
    def resolve(_hostname: str, _port: int) -> tuple[str, ...]:
        return tuple(answers)

    return resolve


def _code(url: str, *, resolver=None) -> str:
    with pytest.raises(EgressPolicyError) as raised:
        validate_outbound_url(url, resolver=resolver or _resolver_returning(_PUBLIC))
    return raised.value.code


def test_public_https_url_is_pinned_to_its_dns_answers():
    target = validate_outbound_url(
        "https://hooks.example.com/v1/deliver?x=1",
        resolver=_resolver_returning(_PUBLIC, "2606:2800:220:1:248:1893:25c8:1946"),
    )
    assert target.scheme == "https"
    assert target.hostname == "hooks.example.com"
    assert target.port == 443
    assert target.resolved_ips == frozenset(
        {_PUBLIC, "2606:2800:220:1:248:1893:25c8:1946"}
    )


def test_plain_http_with_explicit_port_is_allowed():
    target = validate_outbound_url(
        "http://hooks.example.com:8080/deliver", resolver=_resolver_returning(_PUBLIC)
    )
    assert target.scheme == "http"
    assert target.port == 8080


@pytest.mark.parametrize(
    "url",
    [
        "ftp://hooks.example.com/deliver",
        "file:///etc/passwd",
        "gopher://hooks.example.com/",
        "javascript:alert(1)",
    ],
)
def test_non_http_schemes_are_denied(url):
    assert _code(url) == "egress_scheme_denied"


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pass@hooks.example.com/deliver",
        "https://token@hooks.example.com/deliver",
    ],
)
def test_embedded_credentials_are_denied(url):
    assert _code(url) == "egress_url_credentials_denied"


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("", "egress_endpoint_invalid"),
        ("https://", "egress_endpoint_invalid"),
        ("https://hooks.example.com/a b", "egress_endpoint_invalid"),
        ("https://hooks.example.com/deliver#frag", "egress_endpoint_invalid"),
        ("https://hooks.example.com\\deliver", "egress_endpoint_invalid"),
        ("https://intranet/deliver", "egress_hostname_invalid"),
        # inet_aton shorthands are refused before DNS can canonicalise them
        ("http://127.1/hook", "egress_endpoint_invalid"),
        ("http://0x7f.1/hook", "egress_endpoint_invalid"),
        ("http://0177.0.0.1/hook", "egress_endpoint_invalid"),
    ],
)
def test_malformed_urls_are_rejected(url, code):
    assert _code(url) == code


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/hook",
        "http://[::1]/hook",
        "http://0.0.0.0/hook",
        "http://10.0.0.5/hook",
        "http://172.16.4.4/hook",
        "http://192.168.1.10/hook",
        "http://169.254.169.254/latest/meta-data/",
        "http://[fd00::1]/hook",
        "http://[fe80::1]/hook",
        "http://224.0.0.1/hook",
        "http://[ff02::1]/hook",
        "http://[::ffff:127.0.0.1]/hook",
        "http://[::ffff:169.254.169.254]/hook",
        "http://[2002:7f00:1::1]/hook",
        "http://100.64.0.1/hook",
        "http://240.0.0.1/hook",
    ],
)
def test_private_loopback_link_local_multicast_literals_are_denied(url):
    assert _code(url) == "egress_private_address_denied"


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/hook",
        "http://api.localhost/hook",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://metadata/hook",
        "http://instance-data.ec2.internal/latest/",
        "http://printer.local/hook",
        "http://db.internal/hook",
    ],
)
def test_denied_hostnames_never_reach_dns(url):
    def explode(_hostname: str, _port: int):
        raise AssertionError("DNS must not be consulted for a denied hostname")

    assert _code(url, resolver=explode) == "egress_metadata_or_loopback_denied"


@pytest.mark.parametrize(
    "answers",
    [
        ("127.0.0.1",),
        ("10.10.10.10",),
        ("169.254.169.254",),
        ("fd00::10",),
        ("::1",),
        # One public answer does not launder a private one (DNS rebinding /
        # round-robin): every answer must be public.
        (_PUBLIC, "192.168.0.1"),
    ],
)
def test_hostnames_resolving_to_non_public_addresses_are_denied(answers):
    assert (
        _code("https://hooks.example.com/deliver", resolver=_resolver_returning(*answers))
        == "egress_private_address_denied"
    )


def test_dns_failure_and_empty_answers_are_reported_as_unavailable():
    def failing(_hostname: str, _port: int):
        raise OSError("no resolver")

    assert (
        _code("https://hooks.example.com/deliver", resolver=failing)
        == "egress_dns_unavailable"
    )
    assert (
        _code("https://hooks.example.com/deliver", resolver=_resolver_returning())
        == "egress_dns_unavailable"
    )


def test_default_resolver_seam_is_used_when_none_is_injected(monkeypatch):
    monkeypatch.setattr(guard, "DEFAULT_RESOLVER", _resolver_returning(_PUBLIC))
    target = validate_outbound_url("https://hooks.example.com/deliver")
    assert target.resolved_ips == frozenset({_PUBLIC})


def test_parse_public_address_rejects_garbage_answers():
    with pytest.raises(EgressPolicyError) as raised:
        parse_public_address("not-an-ip")
    assert raised.value.code == "egress_dns_answer_invalid"


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirects_are_rejected(status):
    with pytest.raises(EgressPolicyError) as raised:
        reject_redirect(status, "http://169.254.169.254/")
    assert raised.value.code == "egress_redirect_denied"
    assert "169.254" not in str(raised.value)


@pytest.mark.parametrize("status", [200, 204, 404, 500])
def test_non_redirect_statuses_pass_through(status):
    reject_redirect(status)


def test_errors_never_embed_the_url():
    secret_url = "https://user:hunter2@hooks.example.com/deliver"
    with pytest.raises(EgressPolicyError) as raised:
        validate_outbound_url(secret_url, resolver=_resolver_returning(_PUBLIC))
    assert "hunter2" not in str(raised.value)
    assert "hooks.example.com" not in str(raised.value)
