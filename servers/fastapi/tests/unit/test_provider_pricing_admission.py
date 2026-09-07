"""The selected model and its price are compared before anything is generated.

Regression cover for a configuration that could be saved and booted but could
never generate: ``LLM``/``*_MODEL`` named one model while
``PUBLIC_PROVIDER_PRICING_CATALOG`` priced another, and the two were first
compared inside a provider call -- so the mismatch arrived as a 500 on a slide,
minutes into a run, instead of at the moment the model was chosen.
"""

import pytest

from services.provider_pricing_admission import (
    ProviderPricingSelectionError,
    find_pricing_gaps,
    pricing_gap_message,
    reject_write_that_introduces_a_pricing_gap,
)
from utils.runtime_settings import RuntimeSettings


REVISION = "pricing-fixture-r1"


def _catalog(*model_ids: str, revision: str = REVISION) -> str:
    entries = ",".join(
        '{"providerId":"openai","modelId":"%s",'
        '"inputMicrounitsPerMillionTokens":100,'
        '"outputMicrounitsPerMillionTokens":200}' % model_id
        for model_id in model_ids
    )
    return (
        '{"schemaVersion":"public-provider-pricing-catalog/v1",'
        f'"revision":"{revision}","entries":[{entries}]}}'
    )


def _settings(**overrides: object) -> RuntimeSettings:
    values: dict[str, object] = {
        "LLM": "openai",
        "OPENAI_MODEL": "gpt-priced",
        "PUBLIC_PROVIDER_PRICING_REVISION": REVISION,
        "PUBLIC_PROVIDER_PRICING_CATALOG": _catalog("gpt-priced"),
    }
    values.update(overrides)
    return RuntimeSettings({k: v for k, v in values.items() if v is not None})


def test_no_gap_when_the_catalog_prices_the_selected_model() -> None:
    assert find_pricing_gaps(_settings()) == ()
    assert pricing_gap_message(_settings()) is None


def test_an_unconfigured_catalog_is_not_a_gap() -> None:
    """With no catalog the public gate never resolves pricing at all."""

    unpriced = _settings(
        PUBLIC_PROVIDER_PRICING_REVISION=None,
        PUBLIC_PROVIDER_PRICING_CATALOG=None,
    )
    assert find_pricing_gaps(unpriced) == ()


def test_a_selected_model_absent_from_the_catalog_is_reported() -> None:
    gaps = find_pricing_gaps(_settings(OPENAI_MODEL="gpt-unpriced"))

    assert len(gaps) == 1
    gap = gaps[0]
    assert (gap.provider_id, gap.model_id) == ("openai", "gpt-unpriced")
    assert gap.revision == REVISION
    assert gap.priced_models == ("gpt-priced",)
    message = gap.message()
    assert "gpt-unpriced" in message
    assert "gpt-priced" in message
    assert "PUBLIC_PROVIDER_PRICING_CATALOG" in message


def test_the_light_model_tier_is_priced_under_the_same_gate() -> None:
    """``get_model_for`` issues calls against ``LLM_LIGHT_MODEL`` unchanged."""

    gaps = find_pricing_gaps(_settings(LLM_LIGHT_MODEL="gpt-light-unpriced"))

    assert [gap.model_id for gap in gaps] == ["gpt-light-unpriced"]
    assert gaps[0].selection == "light model (LLM_LIGHT_MODEL)"


def test_an_unresolvable_provider_reports_nothing_here() -> None:
    """Provider resolution has its own failure; this is only about pricing."""

    assert find_pricing_gaps(_settings(LLM="not-a-provider")) == ()


def test_a_write_that_selects_an_unpriced_model_is_refused() -> None:
    priced = {
        "LLM": "openai",
        "OPENAI_MODEL": "gpt-priced",
        "PUBLIC_PROVIDER_PRICING_REVISION": REVISION,
        "PUBLIC_PROVIDER_PRICING_CATALOG": _catalog("gpt-priced"),
    }

    with pytest.raises(ProviderPricingSelectionError) as raised:
        reject_write_that_introduces_a_pricing_gap(
            existing=priced,
            merged={**priced, "OPENAI_MODEL": "gpt-unpriced"},
        )
    assert "gpt-unpriced" in str(raised.value)


def test_an_already_unpriced_configuration_stays_writable() -> None:
    """Otherwise the operator is locked out of the screen that repairs it."""

    broken = {
        "LLM": "openai",
        "OPENAI_MODEL": "gpt-unpriced",
        "PUBLIC_PROVIDER_PRICING_REVISION": REVISION,
        "PUBLIC_PROVIDER_PRICING_CATALOG": _catalog("gpt-priced"),
    }

    reject_write_that_introduces_a_pricing_gap(
        existing=broken,
        merged={**broken, "OPENAI_API_KEY": "sk-unrelated-edit"},
    )
    reject_write_that_introduces_a_pricing_gap(
        existing=broken,
        merged={**broken, "OPENAI_MODEL": "gpt-priced"},
    )


def test_the_2026_09_07_codex_incident_is_caught_before_generation() -> None:
    """The exact shape that failed: the catalog kept the previous Codex model.

    ``CODEX_MODEL`` moved to ``gpt-6-astra`` (also the registry default) while
    the catalog still priced only ``gpt-5.6-sol``. Every authored call then
    failed ``public_provider_pricing_exact_match_required`` on a slide.
    """

    incident = RuntimeSettings(
        {
            "LLM": "codex",
            "CODEX_MODEL": "gpt-6-astra",
            "PUBLIC_PROVIDER_PRICING_REVISION": "subscription-local-20260901",
            "PUBLIC_PROVIDER_PRICING_CATALOG": (
                '{"schemaVersion":"public-provider-pricing-catalog/v1",'
                '"revision":"subscription-local-20260901","entries":[{'
                '"providerId":"codex","modelId":"gpt-5.6-sol",'
                '"inputMicrounitsPerMillionTokens":0,'
                '"outputMicrounitsPerMillionTokens":0,"currency":"USD"}]}'
            ),
        }
    )

    gaps = find_pricing_gaps(incident)
    assert [(gap.provider_id, gap.model_id) for gap in gaps] == [
        ("codex", "gpt-6-astra")
    ]
    assert "gpt-5.6-sol" in gaps[0].message()


def test_extending_the_catalog_admits_the_selected_model() -> None:
    """The other half of the repair: price the model instead of changing it."""

    broken = {
        "LLM": "openai",
        "OPENAI_MODEL": "gpt-unpriced",
        "PUBLIC_PROVIDER_PRICING_REVISION": REVISION,
        "PUBLIC_PROVIDER_PRICING_CATALOG": _catalog("gpt-priced"),
    }
    repaired = {
        **broken,
        "PUBLIC_PROVIDER_PRICING_CATALOG": _catalog("gpt-priced", "gpt-unpriced"),
    }

    reject_write_that_introduces_a_pricing_gap(existing=broken, merged=repaired)
    assert find_pricing_gaps(RuntimeSettings(repaired)) == ()
