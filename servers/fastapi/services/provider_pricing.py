"""Explicit, revision-pinned provider/model usage pricing contracts.

Rates are injected from an immutable policy revision; this module deliberately
contains no mutable "latest price" lookup and never guesses a model alias.
Costs use integer micro-currency units and round each token class upward so a
small call cannot disappear as zero due to floating-point or integer truncation.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping


PRICING_SCHEMA_VERSION = "provider-model-pricing/v1"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_TOKENS_PER_RATE_UNIT = 1_000_000
#: Anthropic bills prompt-cache writes (5-minute TTL) at 1.25x the base input
#: rate. A catalog entry that does not state a cache-write rate gets this
#: surcharge over its input rate; providers that never report cache writes are
#: unaffected because their write token count is always zero.
CACHE_WRITE_DEFAULT_SURCHARGE_NUMERATOR = 5
CACHE_WRITE_DEFAULT_SURCHARGE_DENOMINATOR = 4


def _identifier(value: str, field: str) -> str:
    max_length = 128 if field == "model_id" else 64
    if (
        not isinstance(value, str)
        or len(value) > max_length
        or not _SAFE_ID.fullmatch(value)
    ):
        raise ValueError(f"provider_pricing_{field}_invalid")
    return value


def _nonnegative(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"provider_pricing_{field}_invalid")
    return value


@dataclass(frozen=True, slots=True)
class ProviderModelPricing:
    """One immutable provider/model rate card pinned by revision."""

    revision: str
    provider_id: str
    model_id: str
    input_microunits_per_million_tokens: int
    output_microunits_per_million_tokens: int
    cached_input_microunits_per_million_tokens: int | None = None
    currency: str = "USD"
    schema_version: str = PRICING_SCHEMA_VERSION
    #: Optional explicit rate for prompt-cache write tokens. ``None`` means
    #: ``CACHE_WRITE_DEFAULT_SURCHARGE`` (1.25x) over the input rate.
    cache_write_input_microunits_per_million_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.schema_version != PRICING_SCHEMA_VERSION:
            raise ValueError("provider_pricing_schema_version_invalid")
        _identifier(self.revision, "revision")
        _identifier(self.provider_id, "provider_id")
        _identifier(self.model_id, "model_id")
        if not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise ValueError("provider_pricing_currency_invalid")
        _nonnegative(
            self.input_microunits_per_million_tokens, "input_rate"
        )
        _nonnegative(
            self.output_microunits_per_million_tokens, "output_rate"
        )
        if self.cached_input_microunits_per_million_tokens is not None:
            _nonnegative(
                self.cached_input_microunits_per_million_tokens,
                "cached_input_rate",
            )
        if self.cache_write_input_microunits_per_million_tokens is not None:
            _nonnegative(
                self.cache_write_input_microunits_per_million_tokens,
                "cache_write_input_rate",
            )

    @property
    def effective_cache_write_microunits_per_million_tokens(self) -> int:
        explicit = self.cache_write_input_microunits_per_million_tokens
        if explicit is not None:
            return explicit
        return (
            self.input_microunits_per_million_tokens
            * CACHE_WRITE_DEFAULT_SURCHARGE_NUMERATOR
            + CACHE_WRITE_DEFAULT_SURCHARGE_DENOMINATOR
            - 1
        ) // CACHE_WRITE_DEFAULT_SURCHARGE_DENOMINATOR

    @property
    def key(self) -> tuple[str, str, str]:
        return self.revision, self.provider_id, self.model_id


@dataclass(frozen=True, slots=True)
class ProviderUsageCost:
    schema_version: str
    pricing_revision: str
    provider_id: str
    model_id: str
    currency: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    input_cost_microunits: int
    output_cost_microunits: int
    cached_input_cost_microunits: int
    total_cost_microunits: int
    #: Prompt-cache write tokens (billed at the surcharge rate, not in
    #: ``cached_input_tokens``). Both are subsets of ``input_tokens``.
    cache_write_input_tokens: int = 0
    cache_write_input_cost_microunits: int = 0


def _rounded_cost(tokens: int, rate: int) -> int:
    if tokens == 0 or rate == 0:
        return 0
    return (tokens * rate + _TOKENS_PER_RATE_UNIT - 1) // _TOKENS_PER_RATE_UNIT


def billable_cache_tokens(
    pricing: ProviderModelPricing | None,
    input_tokens: int | None,
    cached_tokens: int | None,
    cache_write_tokens: int | None,
) -> tuple[int, int]:
    """Cache (read, write) token counts that can be priced under ``pricing``.

    Fails toward the plain input rate, never toward a cheaper one: cache reads
    stay at the input rate when the catalog entry has no cached-input rate,
    and a report whose cache counters exceed the input total is not trusted.
    """

    cached = cached_tokens or 0
    written = cache_write_tokens or 0
    if pricing is None or cached + written > (input_tokens or 0):
        return 0, 0
    if pricing.cached_input_microunits_per_million_tokens is None:
        cached = 0
    return cached, written


def calculate_actual_usage_cost(
    pricing: ProviderModelPricing,
    *,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    cache_write_input_tokens: int = 0,
) -> ProviderUsageCost:
    """Calculate actual token cost under the exact pinned rate-card revision.

    ``input_tokens`` is the total prompt size including cache reads and cache
    writes (llmai sums Anthropic's three input counters). Cache reads are
    priced at the catalog's cached-input rate, cache writes at its cache-write
    rate (default 1.25x input), and the remainder at the plain input rate.
    """

    input_count = _nonnegative(input_tokens, "input_tokens")
    output_count = _nonnegative(output_tokens, "output_tokens")
    cached_count = _nonnegative(cached_input_tokens, "cached_input_tokens")
    write_count = _nonnegative(cache_write_input_tokens, "cache_write_input_tokens")
    if cached_count + write_count > input_count:
        raise ValueError("provider_pricing_cached_input_exceeds_input")
    uncached_input = input_count - cached_count - write_count
    cached_rate = pricing.cached_input_microunits_per_million_tokens
    if cached_count and cached_rate is None:
        raise ValueError("provider_pricing_cached_input_rate_unavailable")
    input_cost = _rounded_cost(
        uncached_input, pricing.input_microunits_per_million_tokens
    )
    output_cost = _rounded_cost(
        output_count, pricing.output_microunits_per_million_tokens
    )
    cached_cost = _rounded_cost(cached_count, cached_rate or 0)
    write_cost = _rounded_cost(
        write_count, pricing.effective_cache_write_microunits_per_million_tokens
    )
    return ProviderUsageCost(
        schema_version="provider-usage-cost/v1",
        pricing_revision=pricing.revision,
        provider_id=pricing.provider_id,
        model_id=pricing.model_id,
        currency=pricing.currency,
        input_tokens=input_count,
        output_tokens=output_count,
        cached_input_tokens=cached_count,
        input_cost_microunits=input_cost,
        output_cost_microunits=output_cost,
        cached_input_cost_microunits=cached_cost,
        total_cost_microunits=input_cost + output_cost + cached_cost + write_cost,
        cache_write_input_tokens=write_count,
        cache_write_input_cost_microunits=write_cost,
    )


class ProviderPricingCatalog:
    """Immutable exact-match catalog; aliases and fallback-to-latest are forbidden."""

    def __init__(self, entries: tuple[ProviderModelPricing, ...]) -> None:
        indexed: dict[tuple[str, str, str], ProviderModelPricing] = {}
        for entry in entries:
            if entry.key in indexed:
                raise ValueError("provider_pricing_duplicate_entry")
            indexed[entry.key] = entry
        self._entries: Mapping[
            tuple[str, str, str], ProviderModelPricing
        ] = indexed

    def resolve(
        self, *, revision: str, provider_id: str, model_id: str
    ) -> ProviderModelPricing:
        key = (
            _identifier(revision, "revision"),
            _identifier(provider_id, "provider_id"),
            _identifier(model_id, "model_id"),
        )
        try:
            return self._entries[key]
        except KeyError as error:
            raise LookupError("provider_model_pricing_unavailable") from error


__all__ = [
    "PRICING_SCHEMA_VERSION",
    "ProviderModelPricing",
    "ProviderPricingCatalog",
    "ProviderUsageCost",
    "billable_cache_tokens",
    "calculate_actual_usage_cost",
]
