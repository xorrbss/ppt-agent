"""Fail-closed canary rollout policy for native Template V2 templates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from typing import Any, Mapping

from .constants import TEMPLATE_V2_VERSION


TEMPLATE_V2_FLAG = "ENABLE_TEMPLATE_V2"
TEMPLATE_V2_ALLOWLIST = "TEMPLATE_V2_TEMPLATE_ALLOWLIST"
TEMPLATE_V2_ANALYZER = "TEMPLATE_V2_PPTX_ANALYZER"
_MAX_TEMPLATE_ID_LENGTH = 128
_DEFAULT_PPTX_ANALYZER = "deterministic"
_PPTX_ANALYZERS = frozenset({_DEFAULT_PPTX_ANALYZER, "runtime"})


@dataclass(frozen=True, slots=True)
class TemplateV2CanaryReadiness:
    """Content-free operator summary; template identifiers are never exposed."""

    ready: bool
    code: str
    feature_enabled: bool
    configuration_valid: bool
    allowlisted_template_count: int
    pptx_analyzer: str

    def as_dict(self) -> dict[str, bool | int | str]:
        return asdict(self)


class StructuredTemplatePolicyError(ValueError):
    """Stable, payload-free policy failure suitable for API translation."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class StructuredTemplatePolicy:
    creation_enabled: bool
    allowed_template_ids: frozenset[str]
    pptx_analyzer: str = _DEFAULT_PPTX_ANALYZER
    configuration_error: str | None = None

    def can_discover(self, template_id: str) -> bool:
        return (
            self.configuration_error is None
            and self.creation_enabled
            and bool(self.allowed_template_ids)
            and template_id in self.allowed_template_ids
        )

    def can_read_existing(self, version: str) -> bool:
        """The kill switch never makes an already persisted V2 deck unreadable."""

        return version == TEMPLATE_V2_VERSION

    def can_read(self, presentation: Mapping[str, Any]) -> bool:
        """Compatibility boundary for the shared content-free rollout observer."""

        return self.can_read_existing(str(presentation.get("version", "")))

    def canary_readiness(self) -> TemplateV2CanaryReadiness:
        if self.configuration_error is not None:
            code = self.configuration_error
        elif not self.creation_enabled:
            code = "template_v2_feature_disabled"
        elif not self.allowed_template_ids:
            code = "template_v2_allowlist_required"
        else:
            code = "template_v2_canary_ready"
        return TemplateV2CanaryReadiness(
            ready=code == "template_v2_canary_ready",
            code=code,
            feature_enabled=self.creation_enabled,
            configuration_valid=self.configuration_error is None,
            allowlisted_template_count=len(self.allowed_template_ids),
            pptx_analyzer=self.pptx_analyzer,
        )

    def require_write_enabled(self, template_id: str | None = None) -> None:
        if self.configuration_error is not None:
            raise StructuredTemplatePolicyError(
                "template_v2_rollout_config_invalid"
            )
        if not self.creation_enabled:
            raise StructuredTemplatePolicyError("template_v2_creation_disabled")
        if not self.allowed_template_ids:
            raise StructuredTemplatePolicyError("template_v2_allowlist_required")
        if template_id is None:
            raise StructuredTemplatePolicyError("template_v2_template_id_required")
        if template_id not in self.allowed_template_ids:
            raise StructuredTemplatePolicyError("template_v2_template_not_allowed")


def _parse_feature_flag(raw_value: str | None) -> tuple[bool, str | None]:
    if raw_value is None:
        return False, None
    normalized = raw_value.strip().lower()
    if normalized == "true":
        return True, None
    if normalized in {"", "false"}:
        return False, None
    return False, "template_v2_flag_invalid"


def _parse_allowlist(raw_value: str | None) -> tuple[frozenset[str], str | None]:
    if raw_value is None or not raw_value.strip():
        return frozenset(), None

    raw_entries = raw_value.split(",")
    entries = [item.strip() for item in raw_entries]
    if (
        any(not item for item in entries)
        or len(set(entries)) != len(entries)
        or any(item == "*" for item in entries)
        or any(len(item) > _MAX_TEMPLATE_ID_LENGTH for item in entries)
        or any(
            any(not character.isprintable() for character in item)
            for item in raw_entries
        )
    ):
        # Never partially honor a malformed canary allowlist.
        return frozenset(), "template_v2_allowlist_invalid"
    return frozenset(entries), None


def _parse_pptx_analyzer(raw_value: str | None) -> tuple[str, str | None]:
    if raw_value is None:
        return _DEFAULT_PPTX_ANALYZER, None
    normalized = raw_value.strip().lower()
    if not normalized:
        return _DEFAULT_PPTX_ANALYZER, None
    if normalized in _PPTX_ANALYZERS:
        return normalized, None
    # An unknown backend never silently falls back to the default one.
    return _DEFAULT_PPTX_ANALYZER, "template_v2_pptx_analyzer_invalid"


def get_structured_template_policy(
    environ: Mapping[str, str] | None = None,
) -> StructuredTemplatePolicy:
    values = os.environ if environ is None else environ
    enabled, flag_error = _parse_feature_flag(values.get(TEMPLATE_V2_FLAG))
    allowlist, allowlist_error = _parse_allowlist(values.get(TEMPLATE_V2_ALLOWLIST))
    analyzer, analyzer_error = _parse_pptx_analyzer(values.get(TEMPLATE_V2_ANALYZER))
    configuration_error = flag_error or allowlist_error or analyzer_error
    return StructuredTemplatePolicy(
        creation_enabled=enabled and configuration_error is None,
        allowed_template_ids=(
            allowlist if configuration_error is None else frozenset()
        ),
        pptx_analyzer=(
            analyzer if configuration_error is None else _DEFAULT_PPTX_ANALYZER
        ),
        configuration_error=configuration_error,
    )
