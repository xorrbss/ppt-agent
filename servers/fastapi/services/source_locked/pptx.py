from __future__ import annotations

import posixpath
import zipfile
from collections import defaultdict
from io import BytesIO
from xml.etree import ElementTree as ET

from services.source_locked.contracts import (
    AnalysisStage,
    ImageAssetV1,
    ImageOccurrenceScope,
    ImageOccurrenceV1,
    PptxSlideManifestV1,
    PptxSourceManifestV1,
)
from services.source_locked.digests import sha256_digest
from services.source_locked.errors import SourceLockedError

_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PML_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_DML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _rels_name(part: str) -> str:
    directory, filename = posixpath.split(part)
    return posixpath.join(directory, "_rels", f"{filename}.rels")


def _resolve_target(source_part: str, target: str) -> str:
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))


def _relationships(zf: zipfile.ZipFile, source_part: str) -> dict[str, tuple[str, str, bool]]:
    rels_part = _rels_name(source_part)
    try:
        root = ET.fromstring(zf.read(rels_part))
    except KeyError:
        return {}
    except ET.ParseError as exc:
        raise SourceLockedError(
            "PPTX_RELATIONSHIPS_INVALID",
            AnalysisStage.SOURCE_ANALYSIS,
            f"Invalid relationships XML for {source_part}",
        ) from exc
    result: dict[str, tuple[str, str, bool]] = {}
    for rel in root.findall(f"{{{_REL_NS}}}Relationship"):
        rel_id = rel.attrib.get("Id")
        target = rel.attrib.get("Target")
        rel_type = rel.attrib.get("Type", "")
        if rel_id and target:
            external = rel.attrib.get("TargetMode") == "External"
            resolved = target if external else _resolve_target(source_part, target)
            result[rel_id] = (rel_type, resolved, external)
    return result


def _ordered_slide_parts(zf: zipfile.ZipFile) -> list[str]:
    try:
        presentation = ET.fromstring(zf.read("ppt/presentation.xml"))
    except (KeyError, ET.ParseError) as exc:
        raise SourceLockedError(
            "PPTX_PRESENTATION_INVALID",
            AnalysisStage.SOURCE_ANALYSIS,
            "The PPTX package has no valid presentation part",
        ) from exc
    relationships = _relationships(zf, "ppt/presentation.xml")
    slide_parts: list[str] = []
    for slide_id in presentation.findall(f".//{{{_PML_NS}}}sldId"):
        rel_id = slide_id.attrib.get(f"{{{_DOC_REL_NS}}}id")
        relation = relationships.get(rel_id or "")
        if not relation or not relation[0].endswith("/slide") or relation[2]:
            raise SourceLockedError(
                "PPTX_SLIDE_RELATIONSHIP_MISSING",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "The PPTX package references a missing slide",
            )
        slide_parts.append(relation[1])
    if not slide_parts:
        raise SourceLockedError(
            "PPTX_EMPTY",
            AnalysisStage.SOURCE_ANALYSIS,
            "The PPTX source contains no slides",
        )
    return slide_parts


def _related_part(zf: zipfile.ZipFile, source_part: str, relationship_suffix: str) -> str | None:
    for rel_type, target, external in _relationships(zf, source_part).values():
        if rel_type.endswith(relationship_suffix):
            if external or target not in zf.namelist():
                raise SourceLockedError(
                    "PPTX_INHERITED_PART_MISSING",
                    AnalysisStage.MISSING_CONTENT_VALIDATION,
                    f"The PPTX package is missing inherited content for {source_part}",
                )
            return target
    return None


def _part_occurrences(
    zf: zipfile.ZipFile,
    part: str,
    scope: ImageOccurrenceScope,
    asset_digest_by_part: dict[str, str],
    *,
    slide_number: int | None = None,
) -> list[ImageOccurrenceV1]:
    try:
        root = ET.fromstring(zf.read(part))
    except (KeyError, ET.ParseError) as exc:
        raise SourceLockedError(
            "PPTX_CONTENT_PART_INVALID",
            AnalysisStage.SOURCE_ANALYSIS,
            f"The PPTX package has an invalid content part: {part}",
        ) from exc
    relationships = _relationships(zf, part)
    occurrences: list[ImageOccurrenceV1] = []
    for ordinal, blip in enumerate(root.iter(f"{{{_DML_NS}}}blip"), start=1):
        rel_id = blip.attrib.get(f"{{{_DOC_REL_NS}}}embed") or blip.attrib.get(
            f"{{{_DOC_REL_NS}}}link"
        )
        relation = relationships.get(rel_id or "")
        if not rel_id or not relation or not relation[0].endswith("/image"):
            raise SourceLockedError(
                "PPTX_IMAGE_RELATIONSHIP_MISSING",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                f"An image occurrence in {part} cannot be resolved",
            )
        _, asset_part, external = relation
        if external or asset_part not in asset_digest_by_part:
            raise SourceLockedError(
                "PPTX_EXTERNAL_OR_MISSING_IMAGE",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "All source-locked image occurrences must be embedded in the package",
            )
        occurrences.append(
            ImageOccurrenceV1(
                occurrence_id=f"{scope.value}:{part}:{ordinal}",
                scope=scope,
                source_part=part,
                relationship_id=rel_id,
                asset_part=asset_part,
                asset_digest=asset_digest_by_part[asset_part],
                slide_number=slide_number,
            )
        )
    return occurrences


def analyze_pptx(data: bytes) -> PptxSourceManifestV1:
    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SourceLockedError(
            "PPTX_PACKAGE_INVALID",
            AnalysisStage.SOURCE_ANALYSIS,
            "The uploaded .pptx is not a valid OOXML package",
        ) from exc

    with zf:
        names = set(zf.namelist())
        media_parts = sorted(name for name in names if name.startswith("ppt/media/") and not name.endswith("/"))
        parts_by_digest: dict[str, list[str]] = defaultdict(list)
        asset_digest_by_part: dict[str, str] = {}
        asset_size_by_digest: dict[str, int] = {}
        for media_part in media_parts:
            payload = zf.read(media_part)
            digest = sha256_digest(payload)
            parts_by_digest[digest].append(media_part)
            asset_digest_by_part[media_part] = digest
            asset_size_by_digest.setdefault(digest, len(payload))
        assets = tuple(
            ImageAssetV1(
                asset_digest=digest,
                byte_length=asset_size_by_digest[digest],
                package_parts=tuple(parts),
            )
            for digest, parts in sorted(parts_by_digest.items())
        )

        slide_parts = _ordered_slide_parts(zf)
        all_occurrences: list[ImageOccurrenceV1] = []
        slides: list[PptxSlideManifestV1] = []
        cached_inherited: dict[tuple[ImageOccurrenceScope, str], list[ImageOccurrenceV1]] = {}
        for slide_number, slide_part in enumerate(slide_parts, start=1):
            if slide_part not in names:
                raise SourceLockedError(
                    "PPTX_SLIDE_MISSING",
                    AnalysisStage.MISSING_CONTENT_VALIDATION,
                    f"Slide {slide_number} is missing from the PPTX package",
                )
            direct = _part_occurrences(
                zf,
                slide_part,
                ImageOccurrenceScope.DIRECT,
                asset_digest_by_part,
                slide_number=slide_number,
            )
            all_occurrences.extend(direct)
            layout_part = _related_part(zf, slide_part, "/slideLayout")
            master_part = _related_part(zf, layout_part, "/slideMaster") if layout_part else None

            layout_occurrences: list[ImageOccurrenceV1] = []
            if layout_part:
                key = (ImageOccurrenceScope.LAYOUT, layout_part)
                if key not in cached_inherited:
                    cached_inherited[key] = _part_occurrences(
                        zf, layout_part, ImageOccurrenceScope.LAYOUT, asset_digest_by_part
                    )
                layout_occurrences = [
                    occurrence.model_copy(
                        update={
                            "occurrence_id": f"{occurrence.occurrence_id}:slide:{slide_number}",
                            "slide_number": slide_number,
                        }
                    )
                    for occurrence in cached_inherited[key]
                ]
                all_occurrences.extend(layout_occurrences)

            master_occurrences: list[ImageOccurrenceV1] = []
            if master_part:
                key = (ImageOccurrenceScope.MASTER, master_part)
                if key not in cached_inherited:
                    cached_inherited[key] = _part_occurrences(
                        zf, master_part, ImageOccurrenceScope.MASTER, asset_digest_by_part
                    )
                master_occurrences = [
                    occurrence.model_copy(
                        update={
                            "occurrence_id": f"{occurrence.occurrence_id}:slide:{slide_number}",
                            "slide_number": slide_number,
                        }
                    )
                    for occurrence in cached_inherited[key]
                ]
                all_occurrences.extend(master_occurrences)

            inherited_for_slide = len(layout_occurrences) + len(master_occurrences)
            slides.append(
                PptxSlideManifestV1(
                    slide_number=slide_number,
                    slide_part=slide_part,
                    layout_part=layout_part,
                    master_part=master_part,
                    direct_occurrence_count=len(direct),
                    inherited_layout_occurrence_count=len(layout_occurrences),
                    inherited_master_occurrence_count=len(master_occurrences),
                    effective_occurrence_count=len(direct) + inherited_for_slide,
                )
            )

    direct_count = sum(item.scope == ImageOccurrenceScope.DIRECT for item in all_occurrences)
    layout_count = sum(item.scope == ImageOccurrenceScope.LAYOUT for item in all_occurrences)
    master_count = sum(item.scope == ImageOccurrenceScope.MASTER for item in all_occurrences)
    inherited_count = layout_count + master_count
    return PptxSourceManifestV1(
        slide_count=len(slide_parts),
        page_count=len(slide_parts),
        media_assets=assets,
        image_occurrences=tuple(all_occurrences),
        slides=tuple(slides),
        media_asset_count=len(assets),
        image_occurrence_count=len(all_occurrences),
        direct_occurrence_count=direct_count,
        layout_occurrence_count=layout_count,
        master_occurrence_count=master_count,
        inherited_occurrence_count=inherited_count,
        effective_occurrence_count=direct_count + inherited_count,
        package_sha256=sha256_digest(data),
    )
