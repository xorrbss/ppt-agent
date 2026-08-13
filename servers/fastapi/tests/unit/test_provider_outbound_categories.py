"""Actual font and telemetry call sites are default-off and fail closed."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

from api.v1.ppt.endpoints import pptx_slides
from services import provider_font_runtime, provider_telemetry_runtime
from services.provider_egress_policy import EgressCategory, ValidatedEndpoint
from templates import font_utils, pptx_font_utils


class _ForbiddenClientSession:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs
        raise AssertionError("font transport must not open")


def test_google_font_call_site_is_default_off_before_transport(monkeypatch) -> None:
    monkeypatch.delenv("PROVIDER_FONT_EGRESS_ALLOWLIST", raising=False)
    monkeypatch.setattr(
        provider_font_runtime.aiohttp,
        "ClientSession",
        _ForbiddenClientSession,
    )

    available = asyncio.run(pptx_slides.check_google_font_availability("Open Sans"))

    assert available is False
    assert asyncio.run(font_utils.check_google_font_availability("Open Sans")) is False
    assert (
        asyncio.run(pptx_font_utils.check_google_font_availability("Open Sans"))
        is False
    )
    assert asyncio.run(
        pptx_font_utils.get_google_font_file_urls("Open Sans", "not-sent")
    ) == []


def test_google_font_call_site_rejects_allowlisted_loopback_before_transport(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PROVIDER_FONT_EGRESS_ALLOWLIST", "127.0.0.1")
    monkeypatch.setattr(
        provider_font_runtime,
        "_google_fonts_stylesheet_url",
        lambda _font: "https://127.0.0.1:4443/hostile-font.css",
    )
    monkeypatch.setattr(
        provider_font_runtime.aiohttp,
        "ClientSession",
        _ForbiddenClientSession,
    )
    monkeypatch.setattr(
        pptx_font_utils,
        "build_google_fonts_stylesheet_url",
        lambda *_args, **_kwargs: "https://127.0.0.1:4443/hostile-font.css",
    )

    available = asyncio.run(pptx_slides.check_google_font_availability("Hostile"))

    assert available is False
    assert (
        asyncio.run(pptx_font_utils.check_google_font_availability("Hostile"))
        is False
    )


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
