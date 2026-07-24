"""Exhaustive strategy resolution at the persisted presentation boundary.

The resolver only classifies the two Phase 1 identities. Existing V1
template/adaptive execution remains on its current code path and is never
implicitly upgraded to Template V2.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence

from .constants import LEGACY_PRESENTATION_VERSION, TEMPLATE_V2_VERSION


class GenerationStrategy(str, Enum):
    TEMPLATE_V2 = "template-v2"
    AUTHORED_HTML = "authored-html"


class EditorCapability(str, Enum):
    TEMPLATE_V2 = "template-v2"
    AUTHORED_HTML = "authored-html"


class ExportStrategy(str, Enum):
    TEMPLATE_V2_GENERAL = "template-v2-general"
    AUTHORED_HYBRID = "authored-hybrid"


class GenerationAdapter(Protocol):
    async def generate(self, *args: Any, **kwargs: Any) -> Any: ...


class EditorAdapter(Protocol):
    def capability(self) -> EditorCapability: ...


class ExportAdapter(Protocol):
    async def export(self, *args: Any, **kwargs: Any) -> Any: ...


class StrategyResolutionError(ValueError):
    """Stable content-free invariant failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class PresentationStrategies:
    generation: GenerationStrategy
    editor: EditorCapability
    export: ExportStrategy


@dataclass(frozen=True)
class ResolvedPresentationAdapters:
    """Existing executors selected for one invariant-checked presentation."""

    generation: GenerationAdapter
    editor: EditorAdapter
    export: ExportAdapter


@dataclass(frozen=True)
class PresentationAdapterRegistry:
    """Exhaustive adapter bindings without moving executor behavior here."""

    generation: Mapping[GenerationStrategy, GenerationAdapter]
    editor: Mapping[EditorCapability, EditorAdapter]
    export: Mapping[ExportStrategy, ExportAdapter]

    def __post_init__(self) -> None:
        _require_exact_adapter_keys(
            "generation",
            self.generation,
            set(GenerationStrategy),
        )
        _require_exact_adapter_keys(
            "editor",
            self.editor,
            set(EditorCapability),
        )
        _require_exact_adapter_keys(
            "export",
            self.export,
            set(ExportStrategy),
        )

    def bind(
        self,
        strategies: PresentationStrategies,
    ) -> ResolvedPresentationAdapters:
        return ResolvedPresentationAdapters(
            generation=self.generation[strategies.generation],
            editor=self.editor[strategies.editor],
            export=self.export[strategies.export],
        )


TEMPLATE_V2_STRATEGIES = PresentationStrategies(
    generation=GenerationStrategy.TEMPLATE_V2,
    editor=EditorCapability.TEMPLATE_V2,
    export=ExportStrategy.TEMPLATE_V2_GENERAL,
)
AUTHORED_STRATEGIES = PresentationStrategies(
    generation=GenerationStrategy.AUTHORED_HTML,
    editor=EditorCapability.AUTHORED_HTML,
    export=ExportStrategy.AUTHORED_HYBRID,
)


def resolve_presentation_strategies(
    presentation: object | Mapping[str, Any],
    slides: Sequence[object | Mapping[str, Any]],
) -> PresentationStrategies:
    """Resolve only when persisted identity and every slide payload agree."""

    version = _read(presentation, "version", LEGACY_PRESENTATION_VERSION)
    mode = _read(presentation, "mode", None)
    if version not in {LEGACY_PRESENTATION_VERSION, TEMPLATE_V2_VERSION}:
        raise StrategyResolutionError("unsupported_presentation_identity")
    if mode not in {None, "template", "adaptive", "authored"}:
        raise StrategyResolutionError("unsupported_presentation_identity")
    if not slides:
        raise StrategyResolutionError("presentation_slides_required")

    authored = _is_authored(presentation, version)
    theme = _read(presentation, "theme", None)
    theme_mode = theme.get("mode") if isinstance(theme, Mapping) else None
    if theme_mode not in {None, "template", "adaptive", "authored"}:
        raise StrategyResolutionError("unsupported_presentation_identity")
    if (
        mode is not None
        and theme_mode is not None
        and theme_mode != mode
    ):
        raise StrategyResolutionError("presentation_identity_conflict")
    if any(not _is_slide_payload(slide) for slide in slides):
        raise StrategyResolutionError("invalid_slide_payload")
    payloads = [
        (
            _read(slide, "ui", None),
            _read(slide, "html_content", None),
        )
        for slide in slides
    ]

    if any(ui is not None and bool(html) for ui, html in payloads):
        raise StrategyResolutionError("mixed_slide_payload_forbidden")

    if authored:
        if (
            version != LEGACY_PRESENTATION_VERSION
            or any(ui is not None for ui, _ in payloads)
        ):
            raise StrategyResolutionError("authored_identity_payload_mismatch")
        if any(not html for _, html in payloads):
            raise StrategyResolutionError("authored_html_payload_required")
        return AUTHORED_STRATEGIES

    if version == TEMPLATE_V2_VERSION:
        if mode != "template" or theme_mode not in {None, "template"}:
            raise StrategyResolutionError("template_v2_identity_mismatch")
        if any(bool(html) for _, html in payloads):
            raise StrategyResolutionError("template_v2_authored_payload_forbidden")
        if any(ui is None for ui, _ in payloads):
            raise StrategyResolutionError("template_v2_ui_payload_required")
        return TEMPLATE_V2_STRATEGIES

    if any(ui is not None or bool(html) for ui, html in payloads):
        raise StrategyResolutionError("legacy_payload_identity_mismatch")

    raise StrategyResolutionError("legacy_strategy_managed_by_existing_pipeline")


def resolve_presentation_adapters(
    presentation: object | Mapping[str, Any],
    slides: Sequence[object | Mapping[str, Any]],
    registry: PresentationAdapterRegistry,
) -> ResolvedPresentationAdapters:
    """Resolve persisted invariants, then delegate to registered executors."""

    return registry.bind(resolve_presentation_strategies(presentation, slides))


def _require_exact_adapter_keys(
    family: str,
    adapters: Mapping[Enum, object],
    expected: set[Enum],
) -> None:
    if set(adapters) != expected:
        raise StrategyResolutionError(f"{family}_adapter_registry_incomplete")


def _is_authored(
    presentation: object | Mapping[str, Any],
    version: object,
) -> bool:
    mode = _read(presentation, "mode", None)
    if mode is not None:
        return mode == "authored"
    theme = _read(presentation, "theme", None)
    if isinstance(theme, Mapping) and theme.get("mode") is not None:
        return theme.get("mode") == "authored"
    # A native V2 deck intentionally has no legacy React layout. Only an
    # explicit ``mode="template"`` is accepted for it by the resolver.
    if version == TEMPLATE_V2_VERSION:
        return False
    is_authored = getattr(presentation, "is_authored", None)
    if callable(is_authored):
        return bool(is_authored())
    return _read(presentation, "layout", None) is None


def _read(value: object | Mapping[str, Any], key: str, default: Any) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _is_slide_payload(value: object | Mapping[str, Any]) -> bool:
    """Accept decoded mappings and ORM/model objects, never JSON primitives."""

    return isinstance(value, Mapping) or (
        not isinstance(
            value,
            (str, bytes, bytearray, bool, int, float, list, tuple, set, frozenset),
        )
        and (
            hasattr(value, "ui")
            or hasattr(value, "html_content")
        )
    )
