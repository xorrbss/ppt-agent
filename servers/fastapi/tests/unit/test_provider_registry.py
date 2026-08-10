from __future__ import annotations

import pytest

from constants import provider_registry
from constants.provider_registry_generated import PROVIDERS_BY_ID


def test_public_provider_metadata_never_contains_credential_schema_or_values() -> None:
    metadata = provider_registry.get_public_provider_metadata()

    assert metadata
    assert all("credentialSchema" not in entry for entry in metadata)
    assert all("secret" not in entry for entry in metadata)
    assert {entry["id"] for entry in metadata} >= {"openai", "gpt-image-1.5", "tavily"}


def test_server_credential_resolver_returns_only_setting_references() -> None:
    resolved = provider_registry.resolve_provider(
        "openai", required_capabilities=("vision", "structuredOutput"), presentation_driver="authored"
    )

    assert resolved.provider_id == "openai"
    assert resolved.credential_setting_keys == ("OPENAI_API_KEY",)
    assert "registry-must-never-render-this-secret" not in repr(resolved)


@pytest.mark.parametrize(
    ("provider_id", "capabilities", "driver", "code"),
    [
        ("does-not-exist", (), None, "provider_unknown"),
        ("cerebras", ("vision",), None, "provider_capability_mismatch"),
        ("cerebras", (), "authored", "provider_driver_mismatch"),
        ("azure", ("vision",), None, "provider_capability_mismatch"),
    ],
)
def test_unknown_and_capability_mismatch_fail_closed(
    provider_id: str, capabilities: tuple[str, ...], driver: str | None, code: str
) -> None:
    with pytest.raises(provider_registry.ProviderResolutionError) as error:
        provider_registry.resolve_provider(
            provider_id, required_capabilities=capabilities, presentation_driver=driver
        )
    assert error.value.code == code


def test_disabled_provider_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    disabled = dict(PROVIDERS_BY_ID["openai"])
    disabled["enabled"] = False
    monkeypatch.setattr(provider_registry, "PROVIDERS_BY_ID", {"openai": disabled})

    with pytest.raises(provider_registry.ProviderResolutionError, match="provider_disabled"):
        provider_registry.resolve_provider("openai")
