from __future__ import annotations

import posixpath
from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath

from .models import RelationshipEvidence, RelationshipGraphEvidence
from .package_reader import PptxPackageReader, UnsafePptxPackage


PACKAGE_RELATIONSHIPS_NS = (
    "http://schemas.openxmlformats.org/package/2006/relationships"
)
ROOT_PART = "ppt/presentation.xml"

# Evidence V0 follows only inert OOXML structure and asset parts. Executable or
# embedded-package relationships (OLE, packages, VBA, attached files) are absent
# by design and are never opened.
_ALLOWED_INTERNAL_RELATIONSHIPS = {
    "http://schemas.microsoft.com/office/2006/relationships/diagramColors": (
        "diagram_colors"
    ),
    "http://schemas.microsoft.com/office/2006/relationships/diagramData": (
        "diagram_data"
    ),
    "http://schemas.microsoft.com/office/2006/relationships/diagramLayout": (
        "diagram_layout"
    ),
    "http://schemas.microsoft.com/office/2006/relationships/diagramQuickStyle": (
        "diagram_quick_style"
    ),
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart": (
        "chart"
    ),
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments": (
        "comments"
    ),
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    "commentAuthors": "comment_authors",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image": (
        "image"
    ),
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    "notesMaster": "notes_master",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    "notesSlide": "notes_slide",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    "presProps": "presentation_properties",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide": (
        "slide"
    ),
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    "slideLayout": "slide_layout",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    "slideMaster": "slide_master",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    "tableStyles": "table_styles",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme": (
        "theme"
    ),
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    "viewProps": "view_properties",
}
_KNOWN_EXTERNAL_RELATIONSHIPS = {
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/audio": (
        "audio"
    ),
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    "externalLink": "external_link",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    "hyperlink": "hyperlink",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image": (
        "image"
    ),
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/video": (
        "video"
    ),
}
_BLOCKED_INTERNAL_RELATIONSHIPS = {
    "http://schemas.microsoft.com/office/2006/relationships/activeXControl": (
        "active_x"
    ),
    "http://schemas.microsoft.com/office/2006/relationships/vbaProject": "vba",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    "control": "control",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    "oleObject": "ole_object",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    "package": "embedded_package",
}


@dataclass(frozen=True)
class RelationshipGraphLimits:
    max_nodes: int = 512
    max_edges: int = 2_048
    max_depth: int = 32
    max_manifest_items: int = 64

    def __post_init__(self) -> None:
        if (
            self.max_nodes < 1
            or self.max_edges < 1
            or self.max_depth < 0
            or self.max_manifest_items < 0
        ):
            raise ValueError("relationship_graph_limits_must_be_non_negative")


def _relationship_part(source_part: str) -> str:
    directory = posixpath.dirname(source_part)
    basename = posixpath.basename(source_part)
    return posixpath.join(directory, "_rels", f"{basename}.rels")


def _safe_part_target(source_part: str, target: str) -> str:
    if (
        "\\" in target
        or target.startswith("/")
        or target.startswith("//")
        or ":" in target
        or "\x00" in target
    ):
        raise UnsafePptxPackage("unsafe_relationship_target")
    resolved = posixpath.normpath(
        posixpath.join(posixpath.dirname(source_part), target)
    )
    if resolved in {"", ".", ".."} or resolved.startswith("../"):
        raise UnsafePptxPackage("unsafe_relationship_target")
    return str(PurePosixPath(resolved))


def _external_kind(relationship_type: str) -> str:
    return _KNOWN_EXTERNAL_RELATIONSHIPS.get(relationship_type, "other")


def build_relationship_graph_evidence(
    reader: PptxPackageReader,
    *,
    limits: RelationshipGraphLimits | None = None,
) -> RelationshipGraphEvidence:
    """Build bounded, local-only relationship evidence from the PPTX package."""

    graph_limits = limits or RelationshipGraphLimits()
    reader.preflight()
    members = reader.member_names
    nodes: set[str] = {ROOT_PART}
    visited: set[str] = set()
    active: set[str] = set()
    relationships: list[RelationshipEvidence] = []
    missing_parts: set[str] = set()
    cycle_count = 0
    skipped_relationship_count = 0
    blocked_relationship_kind_counts: Counter[str] = Counter()
    edge_count = 0

    def visit(source_part: str, depth: int) -> None:
        nonlocal cycle_count, edge_count, skipped_relationship_count
        if depth > graph_limits.max_depth:
            raise UnsafePptxPackage("relationship_graph_depth_limit_exceeded")
        if source_part in visited:
            return
        active.add(source_part)
        rels_part = _relationship_part(source_part)
        if rels_part in members:
            root = reader.read_xml(rels_part)
            raw_relationships = list(
                root.findall(f"{{{PACKAGE_RELATIONSHIPS_NS}}}Relationship")
            )
            raw_relationships.sort(
                key=lambda rel: (
                    rel.get("Id") or "",
                    rel.get("Type") or "",
                    rel.get("TargetMode") or "",
                    rel.get("Target") or "",
                )
            )
            relationship_ids: set[str] = set()
            for relationship in raw_relationships:
                relationship_id = relationship.get("Id")
                relationship_type = relationship.get("Type")
                target = relationship.get("Target")
                if not relationship_id or not relationship_type or not target:
                    raise UnsafePptxPackage("invalid_ooxml_relationship")
                if relationship_id in relationship_ids:
                    raise UnsafePptxPackage("duplicate_ooxml_relationship_id")
                relationship_ids.add(relationship_id)
                edge_count += 1
                if edge_count > graph_limits.max_edges:
                    raise UnsafePptxPackage(
                        "relationship_graph_edge_limit_exceeded"
                    )
                if relationship.get("TargetMode") == "External":
                    relationships.append(
                        RelationshipEvidence(
                            source_part=source_part,
                            relationship_id=relationship_id,
                            relationship_kind=_external_kind(
                                relationship_type
                            ),
                            external=True,
                        )
                    )
                    continue
                target_part = _safe_part_target(source_part, target)
                relationship_kind = _ALLOWED_INTERNAL_RELATIONSHIPS.get(
                    relationship_type
                )
                if relationship_kind is None:
                    skipped_relationship_count += 1
                    blocked_relationship_kind_counts[
                        _BLOCKED_INTERNAL_RELATIONSHIPS.get(
                            relationship_type,
                            "unrecognized",
                        )
                    ] += 1
                    continue
                if target_part not in members:
                    missing_parts.add(target_part)
                    relationships.append(
                        RelationshipEvidence(
                            source_part=source_part,
                            relationship_id=relationship_id,
                            relationship_kind=relationship_kind,
                            target_part=target_part,
                            missing=True,
                        )
                    )
                    continue
                is_cycle = target_part in active
                relationships.append(
                    RelationshipEvidence(
                        source_part=source_part,
                        relationship_id=relationship_id,
                        relationship_kind=relationship_kind,
                        target_part=target_part,
                        cycle=is_cycle,
                    )
                )
                if is_cycle:
                    cycle_count += 1
                    continue
                if target_part not in nodes:
                    if len(nodes) >= graph_limits.max_nodes:
                        raise UnsafePptxPackage(
                            "relationship_graph_node_limit_exceeded"
                        )
                    nodes.add(target_part)
                if target_part not in visited:
                    visit(target_part, depth + 1)
        active.remove(source_part)
        visited.add(source_part)

    visit(ROOT_PART, 0)
    return RelationshipGraphEvidence(
        nodes=sorted(nodes),
        relationships=relationships,
        missing_parts=sorted(missing_parts),
        cycle_count=cycle_count,
        skipped_relationship_count=skipped_relationship_count,
        blocked_relationship_kind_counts=dict(
            sorted(blocked_relationship_kind_counts.items())
        ),
    )


def relationship_graph_manifest_summary(
    evidence: RelationshipGraphEvidence,
    *,
    max_items: int = RelationshipGraphLimits().max_manifest_items,
) -> dict[str, object]:
    """Return a bounded manifest summary; external targets are never represented."""

    if max_items < 0:
        raise ValueError("max_items_must_be_non_negative")
    external = [
        {
            "source_part": item.source_part,
            "relationship_id": item.relationship_id,
            "relationship_kind": item.relationship_kind,
        }
        for item in evidence.relationships
        if item.external
    ]
    cycles = [
        {
            "source_part": item.source_part,
            "relationship_id": item.relationship_id,
            "relationship_kind": item.relationship_kind,
            "target_part": item.target_part,
        }
        for item in evidence.relationships
        if item.cycle
    ]
    kind_counts = Counter(
        item.relationship_kind for item in evidence.relationships
    )
    return {
        "evidence_version": evidence.evidence_version,
        "root_part": evidence.root_part,
        "node_count": len(evidence.nodes),
        "edge_count": len(evidence.relationships),
        "relationship_kind_counts": dict(sorted(kind_counts.items())),
        "external_relationship_count": len(external),
        "external_relationships": external[:max_items],
        "external_relationships_omitted": max(0, len(external) - max_items),
        "missing_part_count": len(evidence.missing_parts),
        "missing_parts": evidence.missing_parts[:max_items],
        "missing_parts_omitted": max(
            0, len(evidence.missing_parts) - max_items
        ),
        "cycle_count": evidence.cycle_count,
        "cycles": cycles[:max_items],
        "cycles_omitted": max(0, len(cycles) - max_items),
        "skipped_relationship_count": evidence.skipped_relationship_count,
        "blocked_relationship_kind_counts": (
            evidence.blocked_relationship_kind_counts
        ),
        "embedded_content_policy": {
            "dereference_enabled": False,
            "execution_enabled": False,
            "retained_target_identifiers": False,
        },
        "processing": {
            "local_render_enabled": evidence.local_render_enabled,
            "ocr_enabled": evidence.ocr_enabled,
            "external_model_access": evidence.external_model_access,
        },
    }
