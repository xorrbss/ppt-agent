"""Tests for the ``scripts.sync_existing_provider_settings`` CLI entry point.

This script is invoked as a one-shot launcher step (see
``scripts/start-public-v2-rc.ps1``) whose exit code gates whether the caller
proceeds. It must fail loudly -- non-zero exit, clear message -- rather than
reporting success (exit 0) when it silently imported nothing from a
non-empty provider config file.
"""

from __future__ import annotations

from dataclasses import dataclass

from scripts import sync_existing_provider_settings
from services.provider_settings_sync import ProviderSettingsImportError


@dataclass(frozen=True)
class _FakeRecord:
    revision: int
    config: dict


def test_main_returns_zero_on_a_real_import(monkeypatch):
    monkeypatch.setattr(
        sync_existing_provider_settings,
        "sync_legacy_file_to_provider_settings",
        lambda **_kwargs: _FakeRecord(0, {"LLM": "openai"}),
    )

    assert sync_existing_provider_settings.main() == 0


def test_main_returns_one_when_usercfg_path_is_unset(monkeypatch):
    monkeypatch.setattr(
        sync_existing_provider_settings,
        "sync_legacy_file_to_provider_settings",
        lambda **_kwargs: None,
    )

    assert sync_existing_provider_settings.main() == 1


def test_main_returns_one_and_prints_a_clear_message_on_import_divergence(
    monkeypatch, capsys
):
    """Regression test for the reported defect: importing nothing from a
    non-empty file must not exit 0."""

    def raise_divergence(**_kwargs):
        raise ProviderSettingsImportError(
            "the file has content on disk but read_user_config_file "
            "returned an empty config"
        )

    monkeypatch.setattr(
        sync_existing_provider_settings,
        "sync_legacy_file_to_provider_settings",
        raise_divergence,
    )

    assert sync_existing_provider_settings.main() == 1
    assert "returned an empty config" in capsys.readouterr().err
