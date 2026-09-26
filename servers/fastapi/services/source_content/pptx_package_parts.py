"""Relationship and part classification for source PPTX packages."""

from __future__ import annotations

import posixpath
from pathlib import PurePosixPath
from zipfile import ZipFile

from services.source_content.errors import MANIFEST_INVALID, SourceContentError
from services.source_content.security import PackageSecurityReport, REL_NS, secure_xml
from services.source_content.pptx_extraction_constants import (
    NS, R_NS, _NOTES_RE, _SLIDE_RE, _SUPPORTED_REL_SUFFIXES,
    _UNSUPPORTED_CONTENT_REL_SUFFIXES,
)


def _relationship_source_part(rel_part: str) -> str:
    if rel_part == "_rels/.rels":
        return ""
    prefix, filename = rel_part.rsplit("/_rels/", 1)
    if not filename.endswith(".rels"):
        return ""
    return f"{prefix}/{filename[:-5]}"


def _resolve_target(source_part: str, target: str) -> str:
    target = target.replace("\\", "/")
    if target.startswith("/"):
        resolved = target.lstrip("/")
    else:
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))
    if resolved.startswith("../") or resolved in {"", ".", ".."}:
        raise SourceContentError(
            MANIFEST_INVALID,
            detail={"reason": "relationship_target_invalid"},
        )
    return str(PurePosixPath(resolved))


def _relationship_map(package: ZipFile, source_part: str) -> dict[str, tuple[str, str]]:
    if "/" in source_part:
        directory, filename = source_part.rsplit("/", 1)
        rel_part = f"{directory}/_rels/{filename}.rels"
    else:
        rel_part = f"_rels/{source_part}.rels"
    if rel_part not in package.namelist():
        return {}
    root = secure_xml(package.read(rel_part), part_name=rel_part)
    result: dict[str, tuple[str, str]] = {}
    for rel in root.findall(f"{{{REL_NS}}}Relationship"):
        rel_id = rel.get("Id")
        target = rel.get("Target")
        if not rel_id or not target:
            raise SourceContentError(
                MANIFEST_INVALID,
                detail={"reason": "relationship_missing_identity", "part": rel_part},
            )
        result[rel_id] = (_resolve_target(source_part, target), rel.get("Type", ""))
    return result


def _ordered_slide_parts(
    package: ZipFile, report: PackageSecurityReport
) -> list[str]:
    presentation_part = "ppt/presentation.xml"
    if presentation_part not in report.entries:
        raise SourceContentError(
            MANIFEST_INVALID,
            detail={"reason": "presentation_part_missing"},
        )
    root = secure_xml(package.read(presentation_part), part_name=presentation_part)
    relationships = _relationship_map(package, presentation_part)
    slide_entries = {
        name for name in report.entries if _SLIDE_RE.match(name)
    }
    ordered: list[str] = []
    for slide_id in root.findall(".//p:sldId", namespaces=NS):
        rel_id = slide_id.get(f"{{{R_NS}}}id")
        relation = relationships.get(rel_id or "")
        if (
            relation is None
            or not relation[1].endswith("/slide")
            or relation[0] not in slide_entries
        ):
            raise SourceContentError(
                MANIFEST_INVALID,
                detail={"reason": "slide_relationship_invalid"},
            )
        ordered.append(relation[0])
    if len(ordered) != len(slide_entries) or set(ordered) != slide_entries:
        raise SourceContentError(
            MANIFEST_INVALID,
            detail={"reason": "slide_order_invalid"},
        )
    return ordered


def _part_classification(name: str) -> tuple[str, bool]:
    lowered = name.lower()
    if _SLIDE_RE.match(name) or _NOTES_RE.match(name):
        return "source_unit", True
    if lowered.startswith("ppt/media/"):
        return "content_asset", True
    if lowered.startswith("ppt/charts/") or lowered.startswith("ppt/embeddings/"):
        return "content_data", True
    if lowered.startswith(("ppt/slidemasters/", "ppt/slidelayouts/", "ppt/theme/")):
        return "source_decorative", False
    if lowered.endswith(".rels"):
        return "relationship_container", False
    if lowered in {"[content_types].xml", "ppt/presentation.xml"}:
        return "package_structure", False
    if lowered.startswith(
        (
            "docprops/",
            "ppt/notesmasters/",
            "ppt/handoutmasters/",
            "ppt/presprops.xml",
            "ppt/revisioninfo.xml",
            "ppt/changesinfos/",
            "ppt/viewprops.xml",
            "ppt/tablestyles.xml",
            "ppt/commentauthors.xml",
            "ppt/comments/",
            "ppt/printersettings/",
            "ppt/tags/",
            "customxml/",
        )
    ):
        return "package_metadata", False
    return "unclassified", False


def _rel_classification(rel_type: str) -> tuple[str, bool, bool]:
    if any(rel_type.endswith(suffix) for suffix in _SUPPORTED_REL_SUFFIXES):
        content = any(
            rel_type.endswith(suffix)
            for suffix in ("/image", "/chart", "/audio", "/video", "/media", "/notesSlide")
        )
        return "supported_relationship", content, True
    if any(rel_type.endswith(suffix) for suffix in _UNSUPPORTED_CONTENT_REL_SUFFIXES):
        return "unsupported_content_relationship", True, False
    return "unclassified", False, False
