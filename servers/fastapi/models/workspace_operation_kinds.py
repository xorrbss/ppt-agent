"""Closed, lifecycle-independent catalog of durable workspace operation kinds."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class WorkspaceOperationKindSpec:
    kind: str
    command_admitted: bool
    bootstrap_admitted: bool
    destructive: bool
    title: str
    target_summary: str
    activity_label: str


_SPECS = (
    WorkspaceOperationKindSpec(
        "presentation.generate", False, True, False,
        "Generate presentation", "Entire presentation", "Presentation generation",
    ),
    WorkspaceOperationKindSpec(
        "presentation.edit", True, True, False,
        "Edit presentation", "Entire presentation", "Presentation editing",
    ),
    WorkspaceOperationKindSpec(
        "presentation.delete", True, True, True,
        "Delete presentation", "Entire presentation", "Destructive presentation deletion",
    ),
    WorkspaceOperationKindSpec(
        "slide.edit", True, True, False,
        "Edit slide", "Selected slide", "Slide editing",
    ),
    WorkspaceOperationKindSpec(
        "slide.regenerate", True, True, False,
        "Regenerate slide", "Selected slide", "Slide regeneration",
    ),
    WorkspaceOperationKindSpec(
        "slide.regenerate.batch", True, True, False,
        "Regenerate selected slides", "Selected slides", "Batch slide regeneration",
    ),
    WorkspaceOperationKindSpec(
        "slide.delete", True, True, True,
        "Delete slide", "Selected slide", "Destructive slide deletion",
    ),
    WorkspaceOperationKindSpec(
        "version.restore", True, True, True,
        "Restore version", "Saved presentation version", "Presentation version restoration",
    ),
    WorkspaceOperationKindSpec(
        "template.apply", True, True, False,
        "Apply template", "AI-authored template change", "AI-authored template generation",
    ),
    WorkspaceOperationKindSpec(
        "design.apply", True, True, False,
        "Apply design", "Presentation design change", "Presentation design generation",
    ),
    WorkspaceOperationKindSpec(
        "quality.review", True, True, False,
        "Review quality", "Presentation quality review", "Presentation quality review",
    ),
    WorkspaceOperationKindSpec(
        "export.pptx", True, True, False,
        "Export PowerPoint", "PowerPoint export", "PowerPoint export",
    ),
    WorkspaceOperationKindSpec(
        "export.pdf", True, True, False,
        "Export PDF", "PDF export", "PDF export",
    ),
)

WORKSPACE_OPERATION_KIND_REGISTRY = MappingProxyType(
    {spec.kind: spec for spec in _SPECS}
)
WORKSPACE_OPERATION_KINDS = tuple(WORKSPACE_OPERATION_KIND_REGISTRY)
WORKSPACE_COMMAND_KINDS = tuple(
    spec.kind for spec in _SPECS if spec.command_admitted
)
WORKSPACE_BOOTSTRAP_KINDS = tuple(
    spec.kind for spec in _SPECS if spec.bootstrap_admitted
)
WORKSPACE_DESTRUCTIVE_OPERATION_KINDS = frozenset(
    spec.kind for spec in _SPECS if spec.destructive
)


def get_workspace_operation_kind(
    kind: object,
) -> WorkspaceOperationKindSpec | None:
    if not isinstance(kind, str):
        return None
    return WORKSPACE_OPERATION_KIND_REGISTRY.get(kind)


def is_workspace_command_kind(kind: object) -> bool:
    spec = get_workspace_operation_kind(kind)
    return bool(spec is not None and spec.command_admitted)


def is_workspace_bootstrap_kind(kind: object) -> bool:
    spec = get_workspace_operation_kind(kind)
    return bool(spec is not None and spec.bootstrap_admitted)
