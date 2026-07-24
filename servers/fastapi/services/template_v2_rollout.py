"""Operational guardrails shared by internal PoC and official Template V2.

This service intentionally has no persistence or HTTP dependencies. Endpoint
callers apply their policy before discovery/creation, while readers and
exporters can continue to serve already persisted V2 rows after the creation
kill switch is disabled. The selected format marker distinguishes the internal
PoC envelope from the official native ``v2-standard`` contract.

Observations use a closed schema.  Presentation payloads, prompts, slide text,
authored HTML, and raw template identifiers are never accepted by the event
builder.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import logging
from typing import Any, Callable, Iterable, Literal, Mapping, Protocol

from services.template_v2_poc import (
    TEMPLATE_V2_FORMAT,
    TemplateV2ContractError,
)
from templates.v2.constants import TEMPLATE_V2_VERSION


LOGGER = logging.getLogger(__name__)

TemplateV2Operation = Literal[
    "adapter",
    "create",
    "discover",
    "delete",
    "duplicate",
    "export",
    "read",
    "reopen",
    "restore",
    "save",
    "undo_redo",
]
TemplateV2Outcome = Literal["allowed", "blocked", "failure", "fallback", "success"]
TemplateV2ExportType = Literal["pdf", "pptx"]

_OPERATIONS = {
    "adapter",
    "create",
    "discover",
    "delete",
    "duplicate",
    "export",
    "read",
    "reopen",
    "restore",
    "save",
    "undo_redo",
}
_OUTCOMES = {"allowed", "blocked", "failure", "fallback", "success"}
_EXPORT_TYPES = {"pdf", "pptx"}
_OBSERVATION_CODES = {
    "enabled_allowlisted",
    "existing_marked_row",
    "unsupported_element",
    "template_v2_authored_output_forbidden",
    "template_v2_creation_disabled",
    "template_v2_duplicate_node_id",
    "template_v2_duplicate_slide_id",
    "template_v2_duplicate_text_slot",
    "template_v2_export_not_json_safe",
    "template_v2_invalid_adapter_output",
    "template_v2_invalid_adaptive_content",
    "template_v2_invalid_adaptive_slide",
    "template_v2_invalid_adaptive_slides",
    "template_v2_invalid_export_content",
    "template_v2_invalid_node",
    "template_v2_invalid_node_fields",
    "template_v2_invalid_nodes",
    "template_v2_invalid_presentation",
    "template_v2_invalid_presentation_fields",
    "template_v2_invalid_slide",
    "template_v2_invalid_slide_fields",
    "template_v2_invalid_slide_order",
    "template_v2_invalid_text",
    "template_v2_invalid_title",
    "template_v2_marker_required",
    "template_v2_missing_node_id",
    "template_v2_missing_presentation_id",
    "template_v2_missing_slide_id",
    "template_v2_missing_slides",
    "template_v2_missing_template_id",
    "template_v2_missing_text_slot",
    "template_v2_nothing_to_redo",
    "template_v2_nothing_to_undo",
    "template_v2_presentation_exists",
    "template_v2_presentation_not_found",
    "template_v2_source_mutated",
    "template_v2_template_not_allowed",
    "template_v2_template_id_required",
    "template_v2_unknown_internal_template",
    "template_v2_unknown_layout",
    "template_v2_unknown_node_type",
    "template_v2_unknown_text_slot",
    "template_v2_unsupported_export_format",
    "template_v2_unsupported_version",
}

ObservationSink = Callable[[dict[str, object]], None]


class TemplateV2RolloutPolicy(Protocol):
    creation_enabled: bool
    allowed_template_ids: frozenset[str]

    def can_discover(self, template_id: str) -> bool: ...

    def can_read(self, presentation: Mapping[str, Any]) -> bool: ...


@dataclass(frozen=True, slots=True)
class TemplateV2Observation:
    """Content-free, machine-readable event emitted by rollout decisions."""

    schema_version: int
    event: str
    operation: TemplateV2Operation
    outcome: TemplateV2Outcome
    format_marker: str
    template_id_hash: str
    creation_enabled: bool
    code: str | None = None
    export_type: TemplateV2ExportType | None = None

    def as_event(self) -> dict[str, object]:
        return {key: value for key, value in asdict(self).items() if value is not None}


def _default_observation_sink(event: dict[str, object]) -> None:
    LOGGER.info(
        "template_v2_rollout %s",
        json.dumps(event, sort_keys=True, separators=(",", ":")),
    )


def _template_id_hash(template_id: str) -> str:
    return sha256(template_id.encode("utf-8")).hexdigest()[:16]


def _validate_code(code: str | None) -> str | None:
    if code is None:
        return None
    if code not in _OBSERVATION_CODES:
        raise ValueError("Template V2 observation code is not in the safe allowlist")
    return code


class TemplateV2RolloutService:
    """Apply rollout access rules and emit content-free observations."""

    def __init__(
        self,
        policy: TemplateV2RolloutPolicy,
        observation_sink: ObservationSink | None = None,
        *,
        format_marker: str = TEMPLATE_V2_FORMAT,
    ) -> None:
        if format_marker not in {TEMPLATE_V2_FORMAT, TEMPLATE_V2_VERSION}:
            raise ValueError("Unsupported Template V2 format marker")
        self._policy = policy
        self._observation_sink = observation_sink or _default_observation_sink
        self._format_marker = format_marker

    def can_discover(self, template_id: str) -> bool:
        allowed = self._policy.can_discover(template_id)
        self._observe_access("discover", template_id, allowed)
        return allowed

    def filter_discoverable(self, template_ids: Iterable[str]) -> tuple[str, ...]:
        """Return only V2 templates visible under the current rollout policy."""

        return tuple(
            template_id
            for template_id in template_ids
            if self.can_discover(template_id)
        )

    def require_creation(self, template_id: str) -> None:
        if not self._policy.creation_enabled:
            self._observe(
                operation="create",
                outcome="blocked",
                template_id=template_id,
                code="template_v2_creation_disabled",
            )
            raise TemplateV2ContractError("template_v2_creation_disabled")
        if template_id not in self._policy.allowed_template_ids:
            self._observe(
                operation="create",
                outcome="blocked",
                template_id=template_id,
                code="template_v2_template_not_allowed",
            )
            raise TemplateV2ContractError("template_v2_template_not_allowed")

        self._observe(
            operation="create",
            outcome="allowed",
            template_id=template_id,
            code="enabled_allowlisted",
        )

    def require_existing_read(
        self,
        presentation: Mapping[str, Any],
        template_id: str,
    ) -> None:
        self._require_existing_access("read", presentation, template_id)

    def require_existing_export(
        self,
        presentation: Mapping[str, Any],
        template_id: str,
        export_type: TemplateV2ExportType,
    ) -> None:
        self._require_existing_access(
            "export",
            presentation,
            template_id,
            export_type=export_type,
        )

    def record_outcome(
        self,
        *,
        operation: TemplateV2Operation,
        outcome: Literal["failure", "fallback", "success"],
        template_id: str,
        code: str | None = None,
        export_type: TemplateV2ExportType | None = None,
    ) -> None:
        """Record an operation result without accepting presentation content."""

        self._observe(
            operation=operation,
            outcome=outcome,
            template_id=template_id,
            code=code,
            export_type=export_type,
        )

    def _require_existing_access(
        self,
        operation: Literal["export", "read"],
        presentation: Mapping[str, Any],
        template_id: str,
        *,
        export_type: TemplateV2ExportType | None = None,
    ) -> None:
        if not self._policy.can_read(presentation):
            self._observe(
                operation=operation,
                outcome="blocked",
                template_id=template_id,
                code="template_v2_marker_required",
                export_type=export_type,
            )
            raise TemplateV2ContractError("template_v2_marker_required")

        # Intentionally independent of creation_enabled: the flag is a creation
        # and discovery kill switch, not a data migration or read/export switch.
        self._observe(
            operation=operation,
            outcome="allowed",
            template_id=template_id,
            code="existing_marked_row",
            export_type=export_type,
        )

    def _observe_access(
        self,
        operation: Literal["discover"],
        template_id: str,
        allowed: bool,
    ) -> None:
        if allowed:
            code = "enabled_allowlisted"
        elif not self._policy.creation_enabled:
            code = "template_v2_creation_disabled"
        else:
            code = "template_v2_template_not_allowed"
        self._observe(
            operation=operation,
            outcome="allowed" if allowed else "blocked",
            template_id=template_id,
            code=code,
        )

    def _observe(
        self,
        *,
        operation: TemplateV2Operation,
        outcome: TemplateV2Outcome,
        template_id: str,
        code: str | None = None,
        export_type: TemplateV2ExportType | None = None,
    ) -> None:
        if operation not in _OPERATIONS:
            raise ValueError(f"Unsupported Template V2 operation: {operation}")
        if outcome not in _OUTCOMES:
            raise ValueError(f"Unsupported Template V2 outcome: {outcome}")
        if export_type is not None and export_type not in _EXPORT_TYPES:
            raise ValueError(f"Unsupported Template V2 export type: {export_type}")
        if not isinstance(template_id, str) or not template_id:
            raise ValueError("Template V2 observation requires a template identifier")

        event = TemplateV2Observation(
            schema_version=1,
            event="template_v2_rollout",
            operation=operation,
            outcome=outcome,
            format_marker=self._format_marker,
            template_id_hash=_template_id_hash(template_id),
            creation_enabled=self._policy.creation_enabled,
            code=_validate_code(code),
            export_type=export_type,
        )
        self._observation_sink(event.as_event())
