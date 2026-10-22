"""Actual telemetry call sites are default-off and fail closed."""

from __future__ import annotations

import sys
from types import SimpleNamespace

from services import provider_telemetry_runtime
from services.provider_egress_policy import EgressCategory, ValidatedEndpoint


def test_sentry_call_site_requires_separate_telemetry_opt_in(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("SENTRY_DSN", "https://public@telemetry.attacker.test/1")
    monkeypatch.setenv(
        "PROVIDER_TELEMETRY_EGRESS_ALLOWLIST", "telemetry.attacker.test"
    )
    monkeypatch.delenv("PROVIDER_TELEMETRY_EGRESS_OPT_IN", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "sentry_sdk",
        SimpleNamespace(init=lambda **kwargs: calls.append(kwargs)),
    )

    from api import main as api_main

    api_main._maybe_init_sentry()

    assert calls == []


def test_sentry_call_site_rejects_opted_in_loopback_before_sdk(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("SENTRY_DSN", "https://public@127.0.0.1:4443/1")
    monkeypatch.setenv("PROVIDER_TELEMETRY_EGRESS_ALLOWLIST", "127.0.0.1")
    monkeypatch.setenv("PROVIDER_TELEMETRY_EGRESS_OPT_IN", "true")
    monkeypatch.setitem(
        sys.modules,
        "sentry_sdk",
        SimpleNamespace(init=lambda **kwargs: calls.append(kwargs)),
    )

    from api import main as api_main

    api_main._maybe_init_sentry()

    assert calls == []


def test_sentry_call_site_fails_closed_in_secure_mode_before_sdk(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("SENTRY_DSN", "https://public@telemetry.example.test/1")
    monkeypatch.setenv("PROVIDER_TELEMETRY_EGRESS_OPT_IN", "true")
    monkeypatch.setenv("PROVIDER_SECURE_MODE", "true")
    monkeypatch.setattr(
        provider_telemetry_runtime,
        "validate_configured_provider_endpoint",
        lambda *_args, **_kwargs: ValidatedEndpoint(
            url="https://telemetry.example.test/",
            category=EgressCategory.TELEMETRY,
            service_id="sentry",
            hostname="telemetry.example.test",
            port=443,
            resolved_ips=frozenset({"93.184.216.34"}),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "sentry_sdk",
        SimpleNamespace(init=lambda **kwargs: calls.append(kwargs)),
    )

    from api import main as api_main

    api_main._maybe_init_sentry()

    assert calls == []
