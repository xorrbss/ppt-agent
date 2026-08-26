"""Network-free, fail-closed OOXML package admission."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile, ZipInfo

from lxml import etree

from services.source_content.errors import SECURITY_REJECTED, SourceContentError


REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
MAX_ARCHIVE_BYTES = 80 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 320 * 1024 * 1024
MAX_ENTRY_BYTES = 64 * 1024 * 1024
MAX_FILES = 12_000
MAX_COMPRESSION_RATIO = 150
MAX_SLIDES = 500
MAX_RELATIONSHIPS = 40_000

_ACTIVE_PART_MARKERS = (
    "vbaproject.bin",
    "activex/",
)
_ACTIVE_REL_MARKERS = (
    "/oleobject",
    "/control",
    "/activex",
    "/attachedtemplate",
)


def secure_xml(value: bytes, *, part_name: str) -> etree._Element:
    if b"<!DOCTYPE" in value.upper() or b"<!ENTITY" in value.upper():
        raise SourceContentError(
            SECURITY_REJECTED,
            detail={"reason": "xml_dtd_or_entity", "part": part_name},
        )
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        recover=False,
        huge_tree=False,
    )
    try:
        return etree.fromstring(value, parser=parser)
    except (etree.XMLSyntaxError, ValueError) as error:
        raise SourceContentError(
            SECURITY_REJECTED,
            detail={"reason": "invalid_xml", "part": part_name},
        ) from error


@dataclass(frozen=True, slots=True)
class PackageSecurityReport:
    entries: tuple[str, ...]
    relationship_parts: tuple[str, ...]
    relationship_count: int
    external_relationship_count: int
    slide_count: int
    uncompressed_bytes: int


def _safe_name(info: ZipInfo) -> str:
    name = info.filename.replace("\\", "/")
    path = PurePosixPath(name)
    if (
        not name
        or name.startswith("/")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise SourceContentError(
            SECURITY_REJECTED,
            detail={"reason": "unsafe_package_path"},
        )
    return name


def _admit_entry(info: ZipInfo, *, archive_size: int) -> str:
    name = _safe_name(info)
    if info.flag_bits & 0x1:
        raise SourceContentError(
            SECURITY_REJECTED,
            detail={"reason": "encrypted_package_entry"},
        )
    if info.file_size > MAX_ENTRY_BYTES:
        raise SourceContentError(
            SECURITY_REJECTED,
            detail={"reason": "package_entry_too_large", "part": name},
        )
    if info.file_size and info.compress_size == 0:
        raise SourceContentError(
            SECURITY_REJECTED,
            detail={"reason": "invalid_compression_ratio", "part": name},
        )
    ratio = info.file_size / max(1, info.compress_size)
    if ratio > MAX_COMPRESSION_RATIO:
        raise SourceContentError(
            SECURITY_REJECTED,
            detail={"reason": "compression_ratio_exceeded", "part": name},
        )
    lowered = name.lower()
    if any(marker in lowered for marker in _ACTIVE_PART_MARKERS):
        raise SourceContentError(
            SECURITY_REJECTED,
            detail={"reason": "active_content", "part": name},
        )
    if lowered.startswith("ppt/embeddings/") and not lowered.endswith(".xlsx"):
        raise SourceContentError(
            SECURITY_REJECTED,
            detail={"reason": "active_or_unsupported_embedding", "part": name},
        )
    if archive_size > MAX_ARCHIVE_BYTES:
        raise SourceContentError(
            SECURITY_REJECTED,
            status_code=413,
            detail={"reason": "archive_too_large"},
        )
    return name


def admit_pptx_package(path: Path) -> PackageSecurityReport:
    """Inspect central-directory and XML relationship metadata without extraction."""

    try:
        archive_size = path.stat().st_size
    except OSError as error:
        raise SourceContentError(
            SECURITY_REJECTED,
            status_code=404,
            detail={"reason": "source_unavailable"},
        ) from error
    try:
        with ZipFile(path, "r") as package:
            infos = package.infolist()
            if not infos or len(infos) > MAX_FILES:
                raise SourceContentError(
                    SECURITY_REJECTED,
                    detail={"reason": "package_file_count_exceeded"},
                )
            entries: list[str] = []
            total = 0
            for info in infos:
                if info.is_dir():
                    continue
                entries.append(_admit_entry(info, archive_size=archive_size))
                total += info.file_size
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise SourceContentError(
                        SECURITY_REJECTED,
                        status_code=413,
                        detail={"reason": "package_uncompressed_size_exceeded"},
                    )
            names = set(entries)
            if "[Content_Types].xml" not in names or "ppt/presentation.xml" not in names:
                raise SourceContentError(
                    SECURITY_REJECTED,
                    detail={"reason": "not_a_pptx_package"},
                )
            content_types = package.read("[Content_Types].xml").lower()
            if b"macroenabled" in content_types or b"vbaproject" in content_types:
                raise SourceContentError(
                    SECURITY_REJECTED,
                    detail={"reason": "macro_enabled_package"},
                )

            relationship_parts = sorted(name for name in names if name.endswith(".rels"))
            relationship_count = 0
            external_count = 0
            for rel_part in relationship_parts:
                root = secure_xml(package.read(rel_part), part_name=rel_part)
                relationships = root.findall(f"{{{REL_NS}}}Relationship")
                relationship_count += len(relationships)
                if relationship_count > MAX_RELATIONSHIPS:
                    raise SourceContentError(
                        SECURITY_REJECTED,
                        detail={"reason": "relationship_count_exceeded"},
                    )
                for relationship in relationships:
                    rel_type = relationship.get("Type", "").lower()
                    target_mode = relationship.get("TargetMode", "").lower()
                    target = relationship.get("Target", "")
                    if target_mode == "external":
                        external_count += 1
                    source_is_chart = rel_part.startswith("ppt/charts/_rels/chart")
                    inert_chart_workbook = (
                        rel_type.endswith("/package")
                        and source_is_chart
                        and target.lower().endswith(".xlsx")
                    )
                    active_relationship = any(
                        marker in rel_type for marker in _ACTIVE_REL_MARKERS
                    ) or (rel_type.endswith("/package") and not inert_chart_workbook)
                    if target_mode == "external" or active_relationship:
                        raise SourceContentError(
                            SECURITY_REJECTED,
                            detail={
                                "reason": (
                                    "external_relationship"
                                    if target_mode == "external"
                                    else "active_content_relationship"
                                ),
                                "part": rel_part,
                                "target": target,
                            },
                        )
            slide_count = sum(
                1
                for name in names
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            )
            if slide_count < 1 or slide_count > MAX_SLIDES:
                raise SourceContentError(
                    SECURITY_REJECTED,
                    detail={"reason": "slide_count_unsupported"},
                )
            return PackageSecurityReport(
                entries=tuple(sorted(entries)),
                relationship_parts=tuple(relationship_parts),
                relationship_count=relationship_count,
                external_relationship_count=external_count,
                slide_count=slide_count,
                uncompressed_bytes=total,
            )
    except BadZipFile as error:
        raise SourceContentError(
            SECURITY_REJECTED,
            detail={"reason": "invalid_zip_package"},
        ) from error
