"""Canonical, fail-closed presentation profiles and driver capabilities.

Profiles are a projection of persisted presentation identity.  They are never
accepted from a client and are not a second persistence discriminator.  The
legacy ``generation_mode`` column remains a compatibility input/output only.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, Literal, TypeAlias, cast

PresentationDriverId: TypeAlias = Literal[
    "standard", "smart", "template-v2", "authored"
]
ContentModel: TypeAlias = Literal[
    "legacy-elements", "smart-html", "template-v2", "authored"
]
GenerationStrategy: TypeAlias = Literal[
    "standard", "smart", "template", "authored"
]
StyleSource: TypeAlias = Literal["builtin", "community", "imported", "authored"]
LegacyGenerationMode: TypeAlias = Literal["standard", "smart"]

PROFILE_SCHEMA_VERSION: Final = 1
CAPABILITY_SCHEMA_VERSION: Final = 1
_KNOWN_VERSIONS: Final = frozenset({"v1-standard", "v2-standard"})
_KNOWN_MODES: Final = frozenset({None, "template", "adaptive", "authored"})
_KNOWN_THEME_MODES: Final = frozenset({None, "template", "adaptive", "authored"})
_DRIVER_IDS: Final[tuple[PresentationDriverId, ...]] = (
    "standard",
    "smart",
    "template-v2",
    "authored",
)


class PresentationProfileError(ValueError):
    """Stable, content-free failure at a persisted-identity boundary."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PresentationProfile:
    schema_version: Literal[1]
    driver_id: PresentationDriverId
    content_model: ContentModel
    generation_strategy: GenerationStrategy
    style_source: StyleSource
    generation_mode: LegacyGenerationMode


@dataclass(frozen=True, slots=True)
class EditorCapabilities:
    element_text_edit: bool
    element_geometry_edit: bool
    chart_edit: bool
    theme_apply: bool
    template_apply: bool
    slide_image_regenerate: bool
    smart_selection: bool


@dataclass(frozen=True, slots=True)
class PresentationCapabilities:
    schema_version: Literal[1]
    driver_id: PresentationDriverId
    renderer_adapter_id: str
    editor_adapter_id: str
    editor: EditorCapabilities
    agent_tool_capability: PresentationDriverId
    export_adapter_id: str
    export_editable_pptx: bool


PersistedIdentity: TypeAlias = object | Mapping[str, Any]
DriverValidator: TypeAlias = Callable[[PresentationProfile, PersistedIdentity], None]


@dataclass(frozen=True, slots=True)
class PresentationDriver:
    driver_id: PresentationDriverId
    content_model: ContentModel
    generation_strategy: GenerationStrategy
    validate: DriverValidator
    capabilities: PresentationCapabilities


@dataclass(frozen=True, slots=True)
class GenerationModeCompatibilityCodec:
    """One-way compatibility facade around the legacy two-value discriminator."""

    field_name: Literal["generation_mode"]
    source_of_truth: Literal["derived-presentation-profile"]
    accepted_values: tuple[LegacyGenerationMode, LegacyGenerationMode]
    removal_condition: str

    def decode_persisted(self, value: object) -> LegacyGenerationMode:
        if value not in self.accepted_values:
            raise PresentationProfileError("unsupported_generation_mode")
        return cast(LegacyGenerationMode, value)

    def decode_driver(self, value: object) -> PresentationDriverId:
        """Decode a legacy value only when no canonical profile exists."""

        mode = self.decode_persisted(value)
        return cast(
            PresentationDriverId,
            {"standard": "standard", "smart": "smart"}[mode],
        )

    def encode_profile(self, profile: PresentationProfile) -> LegacyGenerationMode:
        expected: LegacyGenerationMode = (
            "smart" if profile.driver_id == "smart" else "standard"
        )
        if profile.generation_mode != expected:
            raise PresentationProfileError("generation_mode_profile_conflict")
        return expected


GENERATION_MODE_COMPATIBILITY: Final = GenerationModeCompatibilityCodec(
    field_name="generation_mode",
    source_of_truth="derived-presentation-profile",
    accepted_values=("standard", "smart"),
    removal_condition=(
        "Remove only after every supported persisted row and public API consumer "
        "uses server-issued PresentationProfile.driver_id, no supported rolling "
        "deployment reads generation_mode, and a compatibility migration has "
        "been released and rollback-tested."
    ),
)


def _read(identity: PersistedIdentity, key: str, default: Any = None) -> Any:
    if isinstance(identity, Mapping):
        return identity.get(key, default)
    return getattr(identity, key, default)


def _canonical_driver_id(
    identity: PersistedIdentity,
) -> PresentationDriverId | None:
    value = _read(identity, "profile_driver_id")
    if value is None:
        return None
    if value not in _DRIVER_IDS:
        raise PresentationProfileError("unsupported_presentation_driver")
    if _read(identity, "profile_schema_version") != PROFILE_SCHEMA_VERSION:
        raise PresentationProfileError("presentation_profile_schema_unsupported")
    return cast(PresentationDriverId, value)


def uses_generation_mode_fallback(identity: PersistedIdentity) -> bool:
    """Whether this row still needs the legacy discriminator to resolve."""

    return _canonical_driver_id(identity) is None


def _theme(identity: PersistedIdentity) -> Mapping[str, Any]:
    value = _read(identity, "theme")
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise PresentationProfileError("invalid_presentation_theme")
    return value


def _community_ids(identity: PersistedIdentity) -> tuple[int, ...]:
    value = _read(identity, "community_design_ids")
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or any(
        isinstance(item, bool) or not isinstance(item, int) or item < 1
        for item in value
    ):
        raise PresentationProfileError("invalid_community_design_identity")
    return tuple(value)


def _style_source(
    driver_id: PresentationDriverId,
    identity: PersistedIdentity,
    theme: Mapping[str, Any],
) -> StyleSource:
    if driver_id == "authored":
        return "authored"
    if _community_ids(identity):
        if driver_id in {"template-v2", "authored"}:
            raise PresentationProfileError("presentation_style_source_conflict")
        return "community"
    if driver_id == "template-v2":
        return "imported"

    theme_user = theme.get("user")
    if theme_user is None or theme_user == "builtin":
        return "builtin"
    if not isinstance(theme_user, str) or not theme_user.strip():
        raise PresentationProfileError("invalid_presentation_style_source")
    return "imported"


def _validate_common_identity(identity: PersistedIdentity) -> tuple[
    LegacyGenerationMode, str | None, str, Mapping[str, Any]
]:
    canonical_driver = _canonical_driver_id(identity)
    generation_mode: LegacyGenerationMode = (
        "smart"
        if canonical_driver == "smart"
        else (
            "standard"
            if canonical_driver is not None
            else GENERATION_MODE_COMPATIBILITY.decode_persisted(
                _read(identity, "generation_mode", "standard")
            )
        )
    )
    mode = _read(identity, "mode")
    if mode not in _KNOWN_MODES:
        raise PresentationProfileError("unsupported_presentation_mode")
    version = _read(identity, "version", "v1-standard")
    if version not in _KNOWN_VERSIONS:
        raise PresentationProfileError("unsupported_presentation_version")
    theme = _theme(identity)
    theme_mode = theme.get("mode")
    if theme_mode not in _KNOWN_THEME_MODES:
        raise PresentationProfileError("unsupported_presentation_theme_mode")
    if mode is not None and theme_mode is not None and mode != theme_mode:
        raise PresentationProfileError("presentation_mode_theme_conflict")
    if version == "v2-standard":
        if generation_mode != "standard":
            raise PresentationProfileError("template_v2_generation_mode_conflict")
        if mode == "authored" or theme_mode == "authored":
            raise PresentationProfileError("authored_version_conflict")
        # Rows created before the additive ``mode`` facade existed legitimately
        # have NULL here.  The persisted v2 version remains authoritative.
        if mode not in {None, "template"}:
            raise PresentationProfileError("template_v2_identity_conflict")

    deck_plan = _read(identity, "deck_plan")
    if deck_plan is not None and not isinstance(deck_plan, Mapping):
        raise PresentationProfileError("invalid_presentation_deck_plan")
    if deck_plan is not None and mode not in {None, "adaptive"}:
        raise PresentationProfileError("presentation_deck_plan_mode_conflict")
    return generation_mode, cast(str | None, mode), cast(str, version), theme


def _validate_standard(profile: PresentationProfile, identity: PersistedIdentity) -> None:
    generation_mode, mode, version, _ = _validate_common_identity(identity)
    if generation_mode != "standard":
        raise PresentationProfileError("standard_generation_mode_conflict")
    if mode == "authored":
        raise PresentationProfileError("standard_authored_mode_conflict")
    if version == "v2-standard" and mode == "template":
        raise PresentationProfileError("standard_template_v2_identity_conflict")
    if profile.content_model != "legacy-elements":
        raise PresentationProfileError("standard_content_model_conflict")


def _validate_smart(profile: PresentationProfile, identity: PersistedIdentity) -> None:
    generation_mode, mode, _, theme = _validate_common_identity(identity)
    if generation_mode != "smart":
        raise PresentationProfileError("smart_generation_mode_conflict")
    if mode == "authored" or theme.get("mode") == "authored":
        raise PresentationProfileError("smart_authored_identity_conflict")
    if profile.content_model != "smart-html":
        raise PresentationProfileError("smart_content_model_conflict")


def _validate_template_v2(
    profile: PresentationProfile, identity: PersistedIdentity
) -> None:
    generation_mode, mode, version, _ = _validate_common_identity(identity)
    if generation_mode != "standard":
        raise PresentationProfileError("template_v2_generation_mode_conflict")
    if version != "v2-standard" or mode not in {None, "template"}:
        raise PresentationProfileError("template_v2_identity_conflict")
    if profile.content_model != "template-v2":
        raise PresentationProfileError("template_v2_content_model_conflict")


def _validate_authored(profile: PresentationProfile, identity: PersistedIdentity) -> None:
    generation_mode, mode, version, theme = _validate_common_identity(identity)
    if generation_mode != "standard":
        raise PresentationProfileError("authored_generation_mode_conflict")
    if mode != "authored" and theme.get("mode") != "authored":
        raise PresentationProfileError("authored_identity_conflict")
    if version != "v1-standard":
        raise PresentationProfileError("authored_version_conflict")
    if profile.content_model != "authored":
        raise PresentationProfileError("authored_content_model_conflict")


def _capabilities(
    driver_id: PresentationDriverId,
    *,
    renderer: str,
    editor_adapter: str,
    editor: EditorCapabilities,
    export_adapter: str,
    export_editable_pptx: bool,
) -> PresentationCapabilities:
    return PresentationCapabilities(
        schema_version=CAPABILITY_SCHEMA_VERSION,
        driver_id=driver_id,
        renderer_adapter_id=renderer,
        editor_adapter_id=editor_adapter,
        editor=editor,
        agent_tool_capability=driver_id,
        export_adapter_id=export_adapter,
        export_editable_pptx=export_editable_pptx,
    )


_STANDARD_EDITOR = EditorCapabilities(True, True, True, True, True, False, False)
_SMART_EDITOR = EditorCapabilities(True, True, False, False, False, False, True)
_TEMPLATE_V2_EDITOR = EditorCapabilities(True, True, True, True, True, False, False)
_AUTHORED_EDITOR = EditorCapabilities(False, False, False, False, True, True, False)


_DRIVERS: dict[PresentationDriverId, PresentationDriver] = {
    "standard": PresentationDriver(
        driver_id="standard",
        content_model="legacy-elements",
        generation_strategy="standard",
        validate=_validate_standard,
        capabilities=_capabilities(
            "standard",
            renderer="legacy-elements",
            editor_adapter="legacy-elements",
            editor=_STANDARD_EDITOR,
            export_adapter="legacy-general",
            export_editable_pptx=True,
        ),
    ),
    "smart": PresentationDriver(
        driver_id="smart",
        content_model="smart-html",
        generation_strategy="smart",
        validate=_validate_smart,
        capabilities=_capabilities(
            "smart",
            renderer="smart-html",
            editor_adapter="smart-html",
            editor=_SMART_EDITOR,
            export_adapter="legacy-general",
            export_editable_pptx=True,
        ),
    ),
    "template-v2": PresentationDriver(
        driver_id="template-v2",
        content_model="template-v2",
        generation_strategy="template",
        validate=_validate_template_v2,
        capabilities=_capabilities(
            "template-v2",
            renderer="template-v2",
            editor_adapter="template-v2",
            editor=_TEMPLATE_V2_EDITOR,
            export_adapter="template-v2-general",
            export_editable_pptx=True,
        ),
    ),
    "authored": PresentationDriver(
        driver_id="authored",
        content_model="authored",
        generation_strategy="authored",
        validate=_validate_authored,
        capabilities=_capabilities(
            "authored",
            renderer="authored",
            editor_adapter="authored-readonly",
            editor=_AUTHORED_EDITOR,
            export_adapter="authored-hybrid",
            export_editable_pptx=True,
        ),
    ),
}
if tuple(_DRIVERS) != _DRIVER_IDS:
    raise RuntimeError("presentation_driver_registry_incomplete")

PresentationDriverRegistry: TypeAlias = Mapping[PresentationDriverId, PresentationDriver]
PRESENTATION_DRIVER_REGISTRY: Final[PresentationDriverRegistry] = MappingProxyType(
    _DRIVERS
)


def get_presentation_driver(driver_id: object) -> PresentationDriver:
    if driver_id not in _DRIVERS:
        raise PresentationProfileError("unsupported_presentation_driver")
    return _DRIVERS[cast(PresentationDriverId, driver_id)]


def resolve_presentation_profile(identity: PersistedIdentity) -> PresentationProfile:
    """Derive a profile from server-loaded persisted fields only.

    Any client-supplied ``profile``, ``capabilities`` or ``driver_id`` keys are
    intentionally unread.  Callers must pass the authoritative presentation row.
    """

    generation_mode, mode, version, theme = _validate_common_identity(identity)
    canonical_driver = _canonical_driver_id(identity)
    if canonical_driver is not None:
        driver_id = canonical_driver
    else:
        legacy_mode_driver = GENERATION_MODE_COMPATIBILITY.decode_driver(
            generation_mode
        )
        if legacy_mode_driver == "smart":
            driver_id = legacy_mode_driver
        elif mode == "authored" or theme.get("mode") == "authored":
            driver_id = "authored"
        elif version == "v2-standard" and mode in {None, "template"}:
            driver_id = "template-v2"
        else:
            driver_id = legacy_mode_driver

    driver = get_presentation_driver(driver_id)
    profile = PresentationProfile(
        schema_version=PROFILE_SCHEMA_VERSION,
        driver_id=driver_id,
        content_model=driver.content_model,
        generation_strategy=driver.generation_strategy,
        style_source=_style_source(driver_id, identity, theme),
        generation_mode=generation_mode,
    )
    driver.validate(profile, identity)
    GENERATION_MODE_COMPATIBILITY.encode_profile(profile)
    return profile


def resolve_presentation_capabilities(
    profile: PresentationProfile,
) -> PresentationCapabilities:
    driver = get_presentation_driver(profile.driver_id)
    if (
        profile.schema_version != PROFILE_SCHEMA_VERSION
        or profile.content_model != driver.content_model
        or profile.generation_strategy != driver.generation_strategy
    ):
        raise PresentationProfileError("presentation_profile_driver_conflict")
    GENERATION_MODE_COMPATIBILITY.encode_profile(profile)
    return driver.capabilities


def resolve_presentation_driver(
    identity: PersistedIdentity,
) -> tuple[PresentationProfile, PresentationCapabilities, PresentationDriver]:
    profile = resolve_presentation_profile(identity)
    driver = get_presentation_driver(profile.driver_id)
    return profile, resolve_presentation_capabilities(profile), driver


__all__ = [
    "CAPABILITY_SCHEMA_VERSION",
    "GENERATION_MODE_COMPATIBILITY",
    "PRESENTATION_DRIVER_REGISTRY",
    "PROFILE_SCHEMA_VERSION",
    "EditorCapabilities",
    "GenerationModeCompatibilityCodec",
    "PresentationCapabilities",
    "PresentationDriver",
    "PresentationDriverRegistry",
    "PresentationProfile",
    "PresentationProfileError",
    "get_presentation_driver",
    "resolve_presentation_capabilities",
    "resolve_presentation_driver",
    "resolve_presentation_profile",
    "uses_generation_mode_fallback",
]
