"""Content-free canary metrics for Template V2 generation and export.

The repository's operational metrics are structured JSON log events rather
than an in-process metrics registry.  This module follows that convention and
keeps the label set closed: presentation content, request/job identifiers,
template identifiers, file paths, prompts, and raw exception text cannot enter
the event payload.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import logging
from math import isfinite
from typing import Literal


LOGGER = logging.getLogger(__name__)

TemplateV2GenerationOperation = Literal["generate", "export"]
TemplateV2GenerationOutcome = Literal["success", "failure"]
TemplateV2GenerationExportType = Literal["pdf", "pptx"]

_OPERATIONS = {"generate", "export"}
_OUTCOMES = {"success", "failure"}
_EXPORT_TYPES = {"pdf", "pptx"}
_FAILURE_CODES = {
    "template_v2_allowlist_required",
    "template_v2_creation_disabled",
    "template_v2_export_failed",
    "template_v2_fillable_layout_required",
    "template_v2_generation_invalid",
    "template_v2_layouts_invalid",
    "template_v2_revision_conflict",
    "template_v2_snapshot_invalid",
    "template_v2_rollout_config_invalid",
    "template_v2_snapshot_missing",
    "template_v2_snapshot_not_found",
    "template_v2_source_invalid",
    "template_v2_template_id_required",
    "template_v2_template_not_allowed",
    "template_v2_template_not_found",
    "template_v2_unexpected_failure",
}
_MAX_DURATION_MS = 86_400_000.0
_MAX_REVISION = 2_147_483_647


@dataclass(frozen=True, slots=True)
class TemplateV2GenerationObservation:
    """A bounded event suitable for canary ratios and latency histograms."""

    schema_version: int
    operation: TemplateV2GenerationOperation
    outcome: TemplateV2GenerationOutcome
    request_strategy: str
    generation_strategy: str
    generation_profile: str
    template_id_hash: str
    template_revision: int
    duration_ms: float
    export_type: TemplateV2GenerationExportType | None = None
    code: str | None = None

    def as_event(self) -> dict[str, object]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value is not None
        }


def _validate_duration_ms(duration_ms: float) -> float:
    if (
        isinstance(duration_ms, bool)
        or not isinstance(duration_ms, (int, float))
        or not isfinite(duration_ms)
        or duration_ms < 0
        or duration_ms > _MAX_DURATION_MS
    ):
        raise ValueError("Template V2 duration_ms must be a bounded millisecond value")
    return float(duration_ms)


def build_template_v2_generation_observation(
    *,
    operation: TemplateV2GenerationOperation,
    outcome: TemplateV2GenerationOutcome,
    template_id: str,
    template_revision: int,
    duration_ms: float,
    export_type: TemplateV2GenerationExportType | None = None,
    code: str | None = None,
) -> dict[str, object]:
    """Build one low-cardinality metric without retaining a template ID."""

    if operation not in _OPERATIONS:
        raise ValueError("Template V2 operation is not in the safe allowlist")
    if outcome not in _OUTCOMES:
        raise ValueError("Template V2 outcome is not in the safe allowlist")
    if not isinstance(template_id, str) or not template_id:
        raise ValueError("Template V2 observation requires a template identifier")
    if (
        isinstance(template_revision, bool)
        or not isinstance(template_revision, int)
        or template_revision < 1
        or template_revision > _MAX_REVISION
    ):
        raise ValueError("Template V2 revision must be a bounded positive integer")
    if export_type is not None and export_type not in _EXPORT_TYPES:
        raise ValueError("Template V2 export type is not in the safe allowlist")
    if operation == "export" and export_type is None:
        raise ValueError("Template V2 export observation requires export_type")
    if operation == "generate" and export_type is not None:
        raise ValueError("Template V2 generate observation forbids export_type")
    if outcome == "success" and code is not None:
        raise ValueError("Template V2 success observation forbids a failure code")
    if outcome == "failure" and code not in _FAILURE_CODES:
        raise ValueError("Template V2 failure code is not in the safe allowlist")

    return TemplateV2GenerationObservation(
        schema_version=1,
        operation=operation,
        outcome=outcome,
        request_strategy="template_v2",
        generation_strategy="template-v2",
        generation_profile="staged-a-hybrid-v1",
        template_id_hash=sha256(template_id.encode("utf-8")).hexdigest()[:16],
        template_revision=template_revision,
        duration_ms=_validate_duration_ms(duration_ms),
        export_type=export_type,
        code=code,
    ).as_event()


def log_template_v2_generation_observation(
    *,
    operation: TemplateV2GenerationOperation,
    outcome: TemplateV2GenerationOutcome,
    template_id: str,
    template_revision: int,
    duration_ms: float,
    export_type: TemplateV2GenerationExportType | None = None,
    code: str | None = None,
    logger: logging.Logger = LOGGER,
) -> dict[str, object]:
    """Log and return a validated Template V2 canary metric."""

    event = build_template_v2_generation_observation(
        operation=operation,
        outcome=outcome,
        template_id=template_id,
        template_revision=template_revision,
        duration_ms=duration_ms,
        export_type=export_type,
        code=code,
    )
    logger.info(
        "template_v2_generation %s",
        json.dumps(event, sort_keys=True, separators=(",", ":")),
    )
    return event


__all__ = [
    "build_template_v2_generation_observation",
    "log_template_v2_generation_observation",
]
