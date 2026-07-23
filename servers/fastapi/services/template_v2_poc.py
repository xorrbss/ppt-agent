"""Isolated Template V2 rollout policy and adapter boundary.

This module deliberately does not add routes, persistence, or a concrete V2
node schema. Phase-one callers must provide a template-specific adapter whose
output already satisfies the existing adaptive slide contract.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import os
from typing import Any, Callable, Mapping


TEMPLATE_V2_FORMAT = "v2-standard"
TEMPLATE_V2_FLAG = "ENABLE_TEMPLATE_V2_POC"
TEMPLATE_V2_ALLOWLIST = "TEMPLATE_V2_POC_TEMPLATE_ALLOWLIST"


class TemplateV2ContractError(ValueError):
    """A stable, content-free Template V2 validation failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class TemplateV2Policy:
    creation_enabled: bool
    allowed_template_ids: frozenset[str]

    def can_create(self, template_id: str) -> bool:
        return self.creation_enabled and template_id in self.allowed_template_ids

    def can_discover(self, template_id: str) -> bool:
        return self.can_create(template_id)

    def can_read(self, presentation: Mapping[str, Any]) -> bool:
        """Existing marked rows remain readable after the kill switch is used."""

        return presentation.get("version") == TEMPLATE_V2_FORMAT


AdaptiveAdapter = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def get_template_v2_policy(
    environ: Mapping[str, str] | None = None,
) -> TemplateV2Policy:
    values = os.environ if environ is None else environ
    enabled = values.get(TEMPLATE_V2_FLAG, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    allowlist = frozenset(
        item.strip()
        for item in values.get(TEMPLATE_V2_ALLOWLIST, "").split(",")
        if item.strip()
    )
    return TemplateV2Policy(
        creation_enabled=enabled,
        allowed_template_ids=allowlist,
    )


def require_template_v2_creation(
    template_id: str,
    policy: TemplateV2Policy,
) -> None:
    if not policy.creation_enabled:
        raise TemplateV2ContractError("template_v2_creation_disabled")
    if template_id not in policy.allowed_template_ids:
        raise TemplateV2ContractError("template_v2_template_not_allowed")


def adapt_template_v2_to_adaptive(
    source: Mapping[str, Any],
    adapter: AdaptiveAdapter,
) -> dict[str, Any]:
    """Run a concrete adapter behind a defensive adaptive-only boundary.

    The boundary validates the explicit format marker and stable slide IDs,
    supplies a deep copy to the adapter, rejects authored output, and checks
    the minimal shape consumed by the existing adaptive slide persistence
    layer. It never writes the adapted form back over the V2 source.
    """

    _validate_source_envelope(source)
    source_snapshot = deepcopy(source)
    adapted = adapter(deepcopy(source))

    if source != source_snapshot:
        raise TemplateV2ContractError("template_v2_source_mutated")
    if not isinstance(adapted, Mapping):
        raise TemplateV2ContractError("template_v2_invalid_adapter_output")
    if adapted.get("mode") != "adaptive":
        raise TemplateV2ContractError("template_v2_authored_output_forbidden")

    slides = adapted.get("slides")
    if not isinstance(slides, list) or len(slides) != len(source["slides"]):
        raise TemplateV2ContractError("template_v2_invalid_adaptive_slides")

    expected_indices = list(range(len(slides)))
    actual_indices: list[int] = []
    for slide in slides:
        if not isinstance(slide, Mapping):
            raise TemplateV2ContractError("template_v2_invalid_adaptive_slide")
        required = ("layout_group", "layout", "index", "content")
        if any(key not in slide for key in required):
            raise TemplateV2ContractError("template_v2_invalid_adaptive_slide")
        if not isinstance(slide["content"], Mapping):
            raise TemplateV2ContractError("template_v2_invalid_adaptive_content")
        if "html_content" in slide:
            raise TemplateV2ContractError("template_v2_authored_output_forbidden")
        actual_indices.append(slide["index"])

    if actual_indices != expected_indices:
        raise TemplateV2ContractError("template_v2_invalid_slide_order")

    return deepcopy(dict(adapted))


def _validate_source_envelope(source: Mapping[str, Any]) -> None:
    if source.get("version") != TEMPLATE_V2_FORMAT:
        raise TemplateV2ContractError("template_v2_unsupported_version")

    template_id = source.get("template_id")
    if not isinstance(template_id, str) or not template_id.strip():
        raise TemplateV2ContractError("template_v2_missing_template_id")

    slides = source.get("slides")
    if not isinstance(slides, list) or not slides:
        raise TemplateV2ContractError("template_v2_missing_slides")

    ids: list[str] = []
    for slide in slides:
        if not isinstance(slide, Mapping):
            raise TemplateV2ContractError("template_v2_invalid_slide")
        slide_id = slide.get("id")
        if not isinstance(slide_id, str) or not slide_id.strip():
            raise TemplateV2ContractError("template_v2_missing_slide_id")
        ids.append(slide_id)

    if len(ids) != len(set(ids)):
        raise TemplateV2ContractError("template_v2_duplicate_slide_id")
