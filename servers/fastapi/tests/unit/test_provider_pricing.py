import pytest

from services.provider_pricing import (
    ProviderModelPricing,
    ProviderPricingCatalog,
    calculate_actual_usage_cost,
)
from services.provider_pricing_registry import (
    PublicProviderPricingUnavailable,
    resolve_public_provider_pricing,
)
from utils.runtime_settings import RuntimeSettings


def _anthropic_catalog(extra: str = "") -> RuntimeSettings:
    return RuntimeSettings(
        {
            "PUBLIC_PROVIDER_PRICING_REVISION": "anthropic-55-20261102",
            "PUBLIC_PROVIDER_PRICING_CATALOG": (
                '{"schemaVersion":"public-provider-pricing-catalog/v1",'
                '"revision":"anthropic-55-20261102","entries":[{'
                '"providerId":"anthropic","modelId":"claude-sonnet-5-5",'
                '"inputMicrounitsPerMillionTokens":2000000,'
                '"outputMicrounitsPerMillionTokens":10000000,'
                '"cachedInputMicrounitsPerMillionTokens":200000'
                + extra
                + "}]}"
            ),
        }
    )


def test_cache_write_rate_defaults_to_1_25x_input_and_v1_catalogs_still_load() -> None:
    legacy = resolve_public_provider_pricing(
        provider_id="anthropic", model_id="claude-sonnet-5-5", settings=_anthropic_catalog()
    )
    assert legacy.cache_write_input_microunits_per_million_tokens is None
    assert legacy.effective_cache_write_microunits_per_million_tokens == 2_500_000

    explicit = resolve_public_provider_pricing(
        provider_id="anthropic",
        model_id="claude-sonnet-5-5",
        settings=_anthropic_catalog(',"cacheWriteInputMicrounitsPerMillionTokens":3750000'),
    )
    assert explicit.effective_cache_write_microunits_per_million_tokens == 3_750_000

    cost = calculate_actual_usage_cost(
        legacy, input_tokens=1_000_000, output_tokens=0,
        cached_input_tokens=400_000, cache_write_input_tokens=100_000,
    )
    assert cost.cache_write_input_cost_microunits == 250_000
    assert cost.cached_input_cost_microunits == 80_000
    assert cost.input_cost_microunits == 1_000_000
    assert cost.total_cost_microunits == 1_330_000
    with pytest.raises(ValueError, match="cached_input_exceeds_input"):
        calculate_actual_usage_cost(
            legacy, input_tokens=10, output_tokens=0,
            cached_input_tokens=6, cache_write_input_tokens=5,
        )


def test_actual_usage_cost_is_revision_pinned_and_rounds_each_class_up() -> None:
    pricing = ProviderModelPricing(
        revision="pricing-2026-08-30",
        provider_id="fixture-provider",
        model_id="fixture-model",
        input_microunits_per_million_tokens=2_000_000,
        output_microunits_per_million_tokens=8_000_000,
        cached_input_microunits_per_million_tokens=500_000,
    )

    cost = calculate_actual_usage_cost(
        pricing,
        input_tokens=1_001,
        output_tokens=501,
        cached_input_tokens=1,
    )

    assert cost.pricing_revision == "pricing-2026-08-30"
    assert cost.input_cost_microunits == 2_000
    assert cost.output_cost_microunits == 4_008
    assert cost.cached_input_cost_microunits == 1
    assert cost.total_cost_microunits == 6_009


def test_catalog_requires_exact_provider_model_revision() -> None:
    pricing = ProviderModelPricing(
        revision="pricing-r1",
        provider_id="provider-a",
        model_id="model-a",
        input_microunits_per_million_tokens=1,
        output_microunits_per_million_tokens=2,
    )
    catalog = ProviderPricingCatalog((pricing,))

    assert (
        catalog.resolve(
            revision="pricing-r1", provider_id="provider-a", model_id="model-a"
        )
        is pricing
    )
    with pytest.raises(LookupError, match="provider_model_pricing_unavailable"):
        catalog.resolve(
            revision="pricing-r2", provider_id="provider-a", model_id="model-a"
        )


def test_public_runtime_pricing_resolves_exact_revision_and_fails_closed() -> None:
    settings = RuntimeSettings(
        {
            "PUBLIC_PROVIDER_PRICING_REVISION": "pricing-2026-08-30",
            "PUBLIC_PROVIDER_PRICING_CATALOG": (
                '{"schemaVersion":"public-provider-pricing-catalog/v1",'
                '"revision":"pricing-2026-08-30","entries":[{'
                '"providerId":"openai","modelId":"gpt-test",'
                '"inputMicrounitsPerMillionTokens":100,'
                '"outputMicrounitsPerMillionTokens":200}]}'
            ),
        }
    )

    pricing = resolve_public_provider_pricing(
        provider_id="openai",
        model_id="gpt-test",
        settings=settings,
    )
    assert pricing.revision == "pricing-2026-08-30"

    with pytest.raises(
        PublicProviderPricingUnavailable,
        match="public_provider_pricing_exact_match_required",
    ) as raised:
        resolve_public_provider_pricing(
            provider_id="openai",
            model_id="gpt-missing",
            settings=settings,
        )
    assert raised.value.code == "public_provider_pricing_missing_openai_gpt_missing"
