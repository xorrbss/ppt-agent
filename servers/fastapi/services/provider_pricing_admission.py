"""Compare the selected model with the pricing catalog that must name it.

Two independent settings decide whether a deck can be generated at all:

* the selection -- ``LLM`` plus that provider's ``*_MODEL``, which is what
  ``utils.llm_provider.get_model`` returns and what every SDK call is issued
  against;
* the price -- ``PUBLIC_PROVIDER_PRICING_CATALOG`` and its pinned
  ``PUBLIC_PROVIDER_PRICING_REVISION``, a hand-written list of exact
  provider/model rates.

``services.provider_pricing_registry.resolve_public_provider_pricing`` fails
closed when the second does not name the first, and it is deliberate that it
does: an unpriced model must never be billed at a guessed rate. But nothing
compared the two where they are set, so the only place they ever met was inside
a provider call. A selection the catalog does not price therefore surfaced as a
500 minutes into a generation, on a slide, after the deck had already been paid
for in wall-clock -- and it read as a generation bug rather than a settings one.

This module is that missing comparison. It is wired at the two moments a
selection comes into existence -- when settings are written
(``services.provider_settings.save_provider_settings_record``) and when the
process starts (``api.lifespan``) -- so the mismatch is refused where it is
created instead of discovered where it is used.

It never invents a rate and never relaxes the gate; it only reports, ahead of
time, exactly what the gate would later refuse.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from fastapi import HTTPException

from services.provider_settings_values import ProviderSettingsValidationError
from utils.runtime_settings import RuntimeSettings, resolve_runtime_settings


class ProviderPricingSelectionError(ProviderSettingsValidationError):
    """A settings write that would select a model the catalog cannot price.

    Subclasses the settings validation error so the admin route keeps its one
    mapping to 422; the message is the gap itself rather than a bare key name.
    """

    def __init__(self, message: str) -> None:
        ValueError.__init__(self, message)


@dataclass(frozen=True, slots=True)
class PricingGap:
    """One selected model the active pricing catalog does not name."""

    #: Which selection produced it: the deck model, or the light-model tier.
    selection: str
    provider_id: str
    model_id: str
    revision: str
    #: What the catalog does price, for the same provider, as a repair hint.
    priced_models: tuple[str, ...]

    def message(self) -> str:
        priced = ", ".join(self.priced_models) if self.priced_models else "nothing"
        return (
            f"provider pricing does not cover the selected {self.selection}: "
            f"{self.provider_id}/{self.model_id} is absent from "
            f"PUBLIC_PROVIDER_PRICING_CATALOG revision '{self.revision}', "
            f"which prices {priced} for this provider. "
            "Add the model to the catalog (and bump "
            "PUBLIC_PROVIDER_PRICING_REVISION with it) or select a priced "
            "model; every provider call fails closed until they agree."
        )


def _catalog_is_configured(snapshot: RuntimeSettings) -> bool:
    """Whether public admission prices calls at all under this snapshot.

    With either half unset the public provider path never resolves pricing, so
    there is no invariant to hold and nothing to refuse.
    """

    revision = str(snapshot.get_env("PUBLIC_PROVIDER_PRICING_REVISION") or "").strip()
    catalog = snapshot.get_env("PUBLIC_PROVIDER_PRICING_CATALOG")
    return bool(revision) and bool(str(catalog or "").strip())


def _priced_models_for(provider_id: str, snapshot: RuntimeSettings) -> tuple[str, ...]:
    """List the models the active catalog prices for one provider.

    Best effort: this only decorates an error message, so a catalog too broken
    to read yields an empty hint rather than replacing the real failure.
    """

    import json

    try:
        payload = json.loads(str(snapshot.get_env("PUBLIC_PROVIDER_PRICING_CATALOG")))
        entries = payload["entries"]
    except (TypeError, ValueError, KeyError):
        return ()
    if not isinstance(entries, list):
        return ()
    models = [
        str(entry.get("modelId"))
        for entry in entries
        if isinstance(entry, Mapping) and entry.get("providerId") == provider_id
    ]
    return tuple(dict.fromkeys(model for model in models if model))


def selected_models(
    settings: RuntimeSettings | None = None,
) -> tuple[tuple[str, str, str], ...]:
    """Return ``(selection, provider_id, model_id)`` for every model in play.

    The deck model is what ``get_model`` resolves. ``LLM_LIGHT_MODEL`` joins it
    when set, because ``utils.llm_provider.get_model_for`` issues calls against
    that id under the same pricing gate -- an unpriced light model would fail
    exactly the same way, at the first call site that adopts the tier.
    """

    from utils.get_env_programme import get_llm_light_model_env
    from utils.generation_model import CODEX_GENERATION_PRESETS
    from utils.llm_provider import get_llm_provider, get_model

    snapshot = resolve_runtime_settings(settings)
    provider_id = str(get_llm_provider(snapshot).value)
    selections = [("deck model", provider_id, str(get_model(snapshot) or ""))]
    light_model = str(get_llm_light_model_env(snapshot) or "").strip()
    if light_model:
        selections.append(("light model (LLM_LIGHT_MODEL)", provider_id, light_model))
    if provider_id == "codex":
        for preset, (model_id, _) in CODEX_GENERATION_PRESETS.items():
            selections.append((f"generation preset {preset}", provider_id, model_id))
    unique: dict[tuple[str, str], tuple[str, str, str]] = {}
    for selection, provider, model in selections:
        if model:
            unique.setdefault((provider, model), (selection, provider, model))
    return tuple(unique.values())


def find_pricing_gaps(
    settings: RuntimeSettings | None = None,
) -> tuple[PricingGap, ...]:
    """Report every selected model the active catalog would refuse to price.

    Empty when no catalog is configured (the gate is inactive), when the
    selection resolves, or when the catalog is malformed -- a malformed catalog
    is its own failure and belongs to
    ``services.provider_pricing_registry``, not to this comparison.
    """

    from services.provider_pricing_registry import (
        PublicProviderPricingUnavailable,
        resolve_public_provider_pricing,
    )

    snapshot = resolve_runtime_settings(settings)
    if not _catalog_is_configured(snapshot):
        return ()
    revision = str(snapshot.get_env("PUBLIC_PROVIDER_PRICING_REVISION") or "").strip()
    try:
        selections = selected_models(snapshot)
    except HTTPException:
        # An unset or unknown ``LLM`` is a provider-resolution failure with its
        # own report; there is no selection here to price.
        return ()
    gaps: list[PricingGap] = []
    for selection, provider_id, model_id in selections:
        try:
            resolve_public_provider_pricing(
                provider_id=provider_id,
                model_id=model_id,
                settings=snapshot,
            )
        except PublicProviderPricingUnavailable as error:
            if str(error) != "public_provider_pricing_exact_match_required":
                continue
            gaps.append(
                PricingGap(
                    selection=selection,
                    provider_id=provider_id,
                    model_id=model_id,
                    revision=revision,
                    priced_models=_priced_models_for(provider_id, snapshot),
                )
            )
    return tuple(gaps)


def pricing_gap_message(settings: RuntimeSettings | None = None) -> str | None:
    """One operator-facing line for the gaps, or ``None`` when there are none."""

    gaps = find_pricing_gaps(settings)
    if not gaps:
        return None
    return " ".join(gap.message() for gap in gaps)


def _gap_identities(config: Mapping[str, Any]) -> frozenset[tuple[str, str]]:
    snapshot = RuntimeSettings.from_sources(user_config=dict(config))
    return frozenset(
        (gap.provider_id, gap.model_id) for gap in find_pricing_gaps(snapshot)
    )


def reject_write_that_introduces_a_pricing_gap(
    *,
    existing: Mapping[str, Any],
    merged: Mapping[str, Any],
) -> None:
    """Refuse a settings write that selects a model the catalog cannot price.

    Only a *newly* introduced gap is refused. A configuration that is already
    unpriced stays writable, because refusing every write into a broken state
    would lock the operator out of the same screen that repairs it -- including
    the act of selecting a priced model again.
    """

    introduced = _gap_identities(merged) - _gap_identities(existing)
    if not introduced:
        return
    snapshot = RuntimeSettings.from_sources(user_config=dict(merged))
    message = " ".join(
        gap.message()
        for gap in find_pricing_gaps(snapshot)
        if (gap.provider_id, gap.model_id) in introduced
    )
    raise ProviderPricingSelectionError(message)


__all__ = [
    "PricingGap",
    "ProviderPricingSelectionError",
    "find_pricing_gaps",
    "pricing_gap_message",
    "reject_write_that_introduces_a_pricing_gap",
    "selected_models",
]
