from __future__ import annotations

from unittest.mock import patch

from fastapi import HTTPException
import pytest

from enums.llm_provider import LLMProvider
from services.provider_egress_policy import (
    EgressCategory,
    ValidatedEndpoint,
)
from utils.llm_config import enforce_llm_egress_policy


def _validated_custom_llm() -> ValidatedEndpoint:
    return ValidatedEndpoint(
        url="https://api.example.com/v1",
        category=EgressCategory.PROVIDER,
        service_id="custom-llm",
        hostname="api.example.com",
        port=443,
        resolved_ips=frozenset({"93.184.216.34"}),
    )


def test_custom_llmai_sdk_fails_closed_when_connected_ip_cannot_be_pinned(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PROVIDER_SECURE_MODE", "true")

    with patch(
        "utils.llm_config.get_llm_provider", return_value=LLMProvider.CUSTOM
    ), patch(
        "utils.llm_config.get_custom_llm_url_env",
        return_value="https://api.example.com/v1",
    ), patch(
        "utils.llm_config.validate_configured_provider_endpoint",
        return_value=_validated_custom_llm(),
    ):
        with pytest.raises(HTTPException) as raised:
            enforce_llm_egress_policy()

    assert raised.value.status_code == 400
    assert raised.value.detail == "egress_connected_ip_pin_unavailable"


def test_custom_llmai_sdk_compatibility_mode_retains_local_endpoint_support(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PROVIDER_SECURE_MODE", "false")

    with patch(
        "utils.llm_config.get_llm_provider", return_value=LLMProvider.CUSTOM
    ), patch(
        "utils.llm_config.get_custom_llm_url_env",
        return_value="https://api.example.com/v1",
    ), patch(
        "utils.llm_config.validate_configured_provider_endpoint",
        return_value=_validated_custom_llm(),
    ):
        enforce_llm_egress_policy()


@pytest.mark.parametrize(
    "environ",
    [
        {"TEMPLATE_V2_DEPLOYMENT_TIER": "production"},
        {"TEMPLATE_V2_DEPLOYMENT_TIER": "staging"},
        {"PROVIDER_SECURE_MODE": "true"},
    ],
)
def test_secure_failure_is_stable_and_never_discloses_endpoint(
    environ: dict[str, str],
) -> None:
    from services.provider_egress_policy import EgressPolicyError
    from services.provider_transport_security import require_pinned_transport

    endpoint = _validated_custom_llm()
    with pytest.raises(EgressPolicyError) as raised:
        require_pinned_transport(
            endpoint,
            transport_pins_connected_ip=False,
            environ=environ,
        )

    message = str(raised.value)
    assert message == "egress_connected_ip_pin_unavailable"
    assert endpoint.url not in message
    assert endpoint.hostname not in message
    assert endpoint.resolved_ips.isdisjoint(message.split())
    assert "token" not in message.lower()
