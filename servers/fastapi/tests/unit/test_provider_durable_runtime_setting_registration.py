"""EXT-05 / ARCH-07: PROVIDER_DURABLE_RUNTIME_REQUIRED is a registered setting.

A sibling to ``test_runtime_settings_surfaces.py`` (kept separate so this
addition does not collide with concurrent edits to that file). Verifies the
registry entries exist with sane types/defaults and that the accessors in
``utils/get_env.py`` parse truthy values the same way
``services.provider_durable_admission`` expects.
"""

from __future__ import annotations

from constants.settings_registry import get_settings_by_key
from services.provider_durable_admission import durable_provider_runtime_required
from utils.get_env import (
    get_provider_durable_runtime_required_env,
    get_template_v2_deployment_tier_env,
)


def test_provider_durable_runtime_required_is_registered():
    entry = get_settings_by_key().get("PROVIDER_DURABLE_RUNTIME_REQUIRED")
    assert entry is not None
    assert entry["type"] == "boolean"
    assert entry["default"] is False
    assert entry["secret"] is False
    assert "fastapi.runtime" in entry["targets"]


def test_template_v2_deployment_tier_is_registered():
    entry = get_settings_by_key().get("TEMPLATE_V2_DEPLOYMENT_TIER")
    assert entry is not None
    assert entry["type"] == "string"
    assert entry["default"] == "local"
    assert entry["secret"] is False
    assert "fastapi.runtime" in entry["targets"]


def test_accessors_round_trip_through_environment(monkeypatch):
    monkeypatch.delenv("PROVIDER_DURABLE_RUNTIME_REQUIRED", raising=False)
    monkeypatch.delenv("TEMPLATE_V2_DEPLOYMENT_TIER", raising=False)
    # Unset falls back to each entry's registered default (false / "local").
    assert get_provider_durable_runtime_required_env() == "false"
    assert get_template_v2_deployment_tier_env() == "local"

    monkeypatch.setenv("PROVIDER_DURABLE_RUNTIME_REQUIRED", "true")
    assert (get_provider_durable_runtime_required_env() or "").strip().lower() == "true"

    monkeypatch.setenv("TEMPLATE_V2_DEPLOYMENT_TIER", "production")
    assert (get_template_v2_deployment_tier_env() or "").strip().lower() == "production"


def test_durable_provider_runtime_required_parses_truthy_values(monkeypatch):
    monkeypatch.delenv("TEMPLATE_V2_DEPLOYMENT_TIER", raising=False)

    monkeypatch.delenv("PROVIDER_DURABLE_RUNTIME_REQUIRED", raising=False)
    assert durable_provider_runtime_required() is False

    monkeypatch.setenv("PROVIDER_DURABLE_RUNTIME_REQUIRED", "true")
    assert durable_provider_runtime_required() is True

    monkeypatch.setenv("PROVIDER_DURABLE_RUNTIME_REQUIRED", "false")
    assert durable_provider_runtime_required() is False


def test_managed_deployment_tier_forces_durable_runtime(monkeypatch):
    monkeypatch.delenv("PROVIDER_DURABLE_RUNTIME_REQUIRED", raising=False)
    monkeypatch.setenv("TEMPLATE_V2_DEPLOYMENT_TIER", "staging")
    assert durable_provider_runtime_required() is True

    monkeypatch.setenv("TEMPLATE_V2_DEPLOYMENT_TIER", "local")
    assert durable_provider_runtime_required() is False
