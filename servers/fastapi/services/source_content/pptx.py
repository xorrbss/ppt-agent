"""Deterministic, network-free PPTX object inventory and extraction."""

from __future__ import annotations

import base64
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import mimetypes
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from lxml import etree

from services.source_content.contracts import (
    SOURCE_LAYOUT_VERSION_V2,
    SourceContentBlock,
    SourceContentCounts,
    SourceContentManifest,
    SourceContentUnit,
    SourceInventoryObject,
    SourceObjectInventorySummary,
)
from services.source_content.digests import digest_bytes, digest_json
from services.source_content.errors import (
    MANIFEST_INVALID,
    UNSUPPORTED_OBJECT,
    SourceContentError,
)
from services.source_content.pptx_document_identity import (
    extract_document_title,
    title_from_file_name,
)
from services.source_content.pptx_text_body import (
    extract_text_body,
    placeholder_payload,
)
from services.source_content.pptx_typography import MANIFEST_ID_EXTRACTOR_REVISION, PptxTypographyResolver
from services.source_content.pptx_custom_geometry import extract_custom_geometry
from services.source_content.pptx_extraction_constants import (
    P_NS as P_NS,
    A_NS as A_NS,
    R_NS as R_NS,
    C_NS as C_NS,
    NS as NS,
    _SLIDE_RE as _SLIDE_RE,
    _NOTES_RE as _NOTES_RE,
    _SUPPORTED_REL_SUFFIXES as _SUPPORTED_REL_SUFFIXES,
    _UNSUPPORTED_CONTENT_REL_SUFFIXES as _UNSUPPORTED_CONTENT_REL_SUFFIXES,
    AffineTransform as AffineTransform,
    IDENTITY_TRANSFORM as IDENTITY_TRANSFORM,
)
from services.source_content.pptx_package_parts import (
    _relationship_source_part as _relationship_source_part,
    _resolve_target as _resolve_target,
    _relationship_map as _relationship_map,
    _ordered_slide_parts as _ordered_slide_parts,
    _part_classification as _part_classification,
    _rel_classification as _rel_classification,
)
from services.source_content.pptx_geometry import (
    _compose_affine as _compose_affine,
    _apply_affine as _apply_affine,
    _group_child_transform as _group_child_transform,
    _geometry as _geometry,
    _image_crop as _image_crop,
    _image_color_transforms as _image_color_transforms,
)
from services.source_content.pptx_semantics import (
    _extract_table as _extract_table,
    _chart_values as _chart_values,
    _numeric_chart_values as _numeric_chart_values,
    _extract_chart as _extract_chart,
)
from services.source_content.security import (
    REL_NS,
    PackageSecurityReport,
    admit_pptx_package,
    secure_xml,
)




@dataclass(frozen=True, slots=True)
class SealedManifestAsset:
    asset_id: str
    media_type: str
    content_digest: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class ExtractedSourceContent:
    manifest: SourceContentManifest
    assets: tuple[SealedManifestAsset, ...]














def _shape_identity(element: etree._Element, fallback: int) -> tuple[str, str]:
    c_nv_pr = element.find(".//p:cNvPr", namespaces=NS)
    if c_nv_pr is None:
        return str(fallback), ""
    return c_nv_pr.get("id", str(fallback)), c_nv_pr.get("name", "")
















def _block(
    *,
    block_id: str,
    kind: str,
    role: str,
    order: int,
    payload: dict[str, Any],
    native_candidate: bool = True,
) -> SourceContentBlock:
    projection = None
    if kind in {"text", "source_note"}:
        projection = {"unicodeForm": "NFC", "lineEnding": "LF"}
    return SourceContentBlock(
        source_block_id=block_id,
        kind=kind,
        semantic_role=role,
        order=order,
        payload=payload,
        comparison_projection=projection,
        content_digest=digest_json(payload),
        native_candidate=native_candidate,
    )










def _semantic_role(element: etree._Element, *, default: str) -> str:
    placeholder = element.find(".//p:ph", namespaces=NS)
    placeholder_type = placeholder.get("type") if placeholder is not None else None
    return {
        "title": "title",
        "ctrTitle": "title",
        "subTitle": "subtitle",
        "body": "body",
        "ftr": "source_note",
    }.get(placeholder_type or "", default)


def _extract_unit(
    package: ZipFile,
    *,
    part_name: str,
    source_unit_id: str,
    source_index: int,
    object_type: str,
    media_assets: dict[str, SealedManifestAsset],
    image_occurrences: Counter[str],
    typography: PptxTypographyResolver,
) -> tuple[SourceContentUnit, list[SourceInventoryObject], dict[str, int]]:
    root = secure_xml(package.read(part_name), part_name=part_name)
    rels = _relationship_map(package, part_name)
    shape_tree = root.find(".//p:spTree", namespaces=NS)
    if shape_tree is None:
        raise SourceContentError(
            MANIFEST_INVALID,
            detail={"reason": "shape_tree_missing", "sourceUnitId": source_unit_id},
        )
    blocks: list[SourceContentBlock] = []
    objects: list[SourceInventoryObject] = []
    stats = {
        "table_cells": 0,
        "chart_values": 0,
        "tables": 0,
        "charts": 0,
        "picture_bullets": 0,
    }
    order = 0
    source_shape_index = 0

    def bullet_image_resource(rel_id: str) -> dict[str, str]:
        target = rels.get(rel_id)
        if (
            target is None
            or not target[1].endswith("/image")
            or target[0] not in package.namelist()
        ):
            raise SourceContentError(
                MANIFEST_INVALID,
                detail={
                    "reason": "picture_bullet_relationship_invalid",
                    "part": part_name,
                    "relationshipId": rel_id,
                },
            )
        media_part = target[0]
        payload = package.read(media_part)
        media_type = mimetypes.guess_type(media_part)[0] or "application/octet-stream"
        return {
            "bulletImageDataUri": (
                f"data:{media_type};base64,{base64.b64encode(payload).decode('ascii')}"
            ),
            "bulletImageDigest": digest_bytes(payload),
        }

    def visit_shapes(
        container: etree._Element,
        parent_transform: AffineTransform,
    ) -> None:
        nonlocal order, source_shape_index
        for ordinal, element in enumerate(container):
            local = etree.QName(element).localname
            if local in {"nvGrpSpPr", "grpSpPr", "extLst"}:
                continue
            shape_id, shape_name = _shape_identity(element, ordinal)
            current_shape_index = source_shape_index
            source_shape_index += 1
            object_id = f"{source_unit_id}:object:{shape_id}"
            content_bearing = True
            supported = True
            classification = "supported_slide_object"
            new_block: SourceContentBlock | None = None
            group_transform: AffineTransform | None = None

            def source_payload(shape_id=shape_id, current_shape_index=current_shape_index) -> dict[str, Any]:
                return {
                    "shapeId": shape_id,
                    "sourceSlideIndex": source_index,
                    "sourceShapeIndex": current_shape_index,
                }

            def geometry_for(block_id: str, element=element) -> dict[str, Any]:
                return _geometry(
                    element,
                    parent_transform=parent_transform,
                    inherited_shapes=typography.placeholder_shape_chain(
                        element, part_name=part_name
                    ),
                    local_source="slide" if object_type == "slide_object" else "notes",
                    source_block_id=block_id,
                )

            if local == "sp":
                body = extract_text_body(
                    element,
                    context=typography.shape_context(element, part_name=part_name),
                    bullet_image_resolver=bullet_image_resource,
                )
                stats["picture_bullets"] += body.picture_bullet_count
                if body.text:
                    kind = "source_note" if object_type == "notes_object" else "text"
                    role = _semantic_role(element, default="body")
                    block_id = f"{source_unit_id}:{kind}:{shape_id}"
                    new_block = _block(
                        block_id=block_id,
                        kind=kind,
                        role=role,
                        order=order,
                        payload={
                            "text": body.text,
                            "paragraphs": body.paragraphs,
                            "runs": body.runs,
                            **source_payload(),
                            "shapeName": shape_name,
                            "geometry": geometry_for(block_id),
                            "runStyles": body.run_styles,
                            "dominantSizePt": body.dominant_size_pt,
                            "textBody": body.text_body,
                            **placeholder_payload(element),
                        },
                    )
                elif element.find(".//p:ph", namespaces=NS) is not None:
                    content_bearing = False
                    classification = "empty_placeholder"
                else:
                    preset = element.find(".//a:prstGeom", namespaces=NS)
                    block_id = f"{source_unit_id}:shape:{shape_id}"
                    new_block = _block(
                        block_id=block_id,
                        kind="shape",
                        role="shape",
                        order=order,
                        payload={
                            **source_payload(),
                            "shapeName": shape_name,
                            "preset": preset.get("prst") if preset is not None else None,
                            **({"customGeometry": extract_custom_geometry(element)} if preset is None else {}),
                            "geometry": geometry_for(block_id),
                        },
                    )
            elif local == "cxnSp":
                block_id = f"{source_unit_id}:shape:{shape_id}"
                connector_preset = element.find(".//a:prstGeom", namespaces=NS)
                new_block = _block(
                    block_id=block_id,
                    kind="shape",
                    role="connector",
                    order=order,
                    payload={
                        **source_payload(),
                        "shapeName": shape_name,
                        "preset": connector_preset.get("prst") if connector_preset is not None else None,
                        "geometry": geometry_for(block_id),
                    },
                )
            elif local == "pic":
                blip = element.find(".//a:blip", namespaces=NS)
                rel_id = blip.get(f"{{{R_NS}}}embed") if blip is not None else None
                target = rels.get(rel_id or "")
                if target is None or target[0] not in package.namelist():
                    supported = False
                    classification = "unclassified"
                else:
                    media_part = target[0]
                    payload = package.read(media_part)
                    media_digest = digest_bytes(payload)
                    asset_id = f"scma_{media_digest.removeprefix('sha256:')}"
                    media_assets.setdefault(
                        asset_id,
                        SealedManifestAsset(
                            asset_id=asset_id,
                            media_type=mimetypes.guess_type(media_part)[0] or "application/octet-stream",
                            content_digest=media_digest,
                            payload=payload,
                        ),
                    )
                    image_occurrences[asset_id] += 1
                    block_id = f"{source_unit_id}:image:{shape_id}"
                    new_block = _block(
                        block_id=block_id,
                        kind="image",
                        role="image",
                        order=order,
                        payload={
                            **source_payload(),
                            "assetId": asset_id,
                            "contentDigest": media_digest,
                            "sizeBytes": len(payload),
                            "occurrence": image_occurrences[asset_id],
                            "geometry": geometry_for(block_id),
                            "crop": _image_crop(element),
                            "colorTransforms": _image_color_transforms(element),
                            "altText": shape_name,
                            **placeholder_payload(element),
                        },
                    )
            elif local == "graphicFrame":
                graphic_data = element.find(".//a:graphicData", namespaces=NS)
                uri = graphic_data.get("uri", "") if graphic_data is not None else ""
                if uri.endswith("/table") and element.find(".//a:tbl", namespaces=NS) is not None:
                    block_id = f"{source_unit_id}:table:{shape_id}"
                    payload, cell_count, picture_bullet_count = _extract_table(
                        element,
                        source_block_id=block_id,
                        bullet_image_resolver=bullet_image_resource,
                    )
                    payload.update(source_payload())
                    payload["geometry"] = geometry_for(block_id)
                    stats["table_cells"] += cell_count
                    stats["picture_bullets"] += picture_bullet_count
                    stats["tables"] += 1
                    new_block = _block(
                        block_id=block_id,
                        kind="table",
                        role="table",
                        order=order,
                        payload=payload,
                    )
                elif uri.endswith("/chart"):
                    chart = element.find(".//c:chart", namespaces=NS)
                    rel_id = chart.get(f"{{{R_NS}}}id") if chart is not None else None
                    target = rels.get(rel_id or "")
                    if target is None:
                        supported = False
                        classification = "unclassified"
                    else:
                        payload, value_count = _extract_chart(package, target[0])
                        block_id = f"{source_unit_id}:chart:{shape_id}"
                        payload.update(source_payload())
                        payload["geometry"] = geometry_for(block_id)
                        stats["chart_values"] += value_count
                        stats["charts"] += 1
                        new_block = _block(
                            block_id=block_id,
                            kind="chart",
                            role="chart",
                            order=order,
                            payload=payload,
                        )
                else:
                    supported = False
                    classification = "unsupported_content_object"
            elif local == "grpSp":
                content_bearing = False
                classification = "supported_group"
                group_transform = _compose_affine(
                    parent_transform, _group_child_transform(element)
                )
            else:
                supported = False
                classification = "unclassified"

            objects.append(
                SourceInventoryObject(
                    object_id=object_id,
                    object_type=object_type,
                    classification=classification,
                    source_unit_id=source_unit_id,
                    content_bearing=content_bearing,
                    supported=supported,
                )
            )
            if new_block is not None:
                blocks.append(new_block)
                order += 1
            if group_transform is not None:
                visit_shapes(element, group_transform)

    visit_shapes(shape_tree, IDENTITY_TRANSFORM)
    return (
        SourceContentUnit(
            source_unit_id=source_unit_id,
            source_index=source_index,
            blocks=tuple(blocks),
        ),
        objects,
        stats,
    )


def _inventory_package(
    package: ZipFile,
    report: PackageSecurityReport,
) -> list[SourceInventoryObject]:
    objects: list[SourceInventoryObject] = []
    names = set(report.entries)
    for name in report.entries:
        classification, content_bearing = _part_classification(name)
        objects.append(
            SourceInventoryObject(
                object_id=f"part:{name}",
                object_type="package_part",
                classification=classification,
                content_bearing=content_bearing,
                supported=classification != "unclassified",
                target_part=name,
            )
        )
    for rel_part in report.relationship_parts:
        root = secure_xml(package.read(rel_part), part_name=rel_part)
        source_part = _relationship_source_part(rel_part)
        for rel in sorted(
            root.findall(f"{{{REL_NS}}}Relationship"),
            key=lambda item: item.get("Id", ""),
        ):
            rel_id = rel.get("Id", "missing")
            rel_type = rel.get("Type", "")
            target = rel.get("Target", "")
            classification, content_bearing, supported = _rel_classification(rel_type)
            target_part = _resolve_target(source_part, target)
            if target_part not in names and not rel.get("TargetMode", "").lower() == "external":
                classification = "unclassified"
                supported = False
            objects.append(
                SourceInventoryObject(
                    object_id=f"rel:{rel_part}:{rel_id}",
                    object_type="relationship",
                    classification=classification,
                    content_bearing=content_bearing,
                    supported=supported,
                    relationship_type=rel_type,
                    target_part=target_part,
                )
            )
    return objects


def extract_pptx_source_content(
    path: Path,
    *,
    owner_id: str,
    reference_snapshot_id: str,
    original_file_name: str | None = None,
) -> ExtractedSourceContent:
    report = admit_pptx_package(path)
    source_bytes = path.read_bytes()
    source_digest = digest_bytes(source_bytes)
    # Revision is part of the seed so an extractor upgrade seals a new manifest_id.
    seed = f"{MANIFEST_ID_EXTRACTOR_REVISION}\n{owner_id}\n{reference_snapshot_id}\n{source_digest}"
    manifest_id = f"scm_{sha256(seed.encode('utf-8')).hexdigest()}"

    with ZipFile(path, "r") as package:
        typography = PptxTypographyResolver(package, relationship_map=_relationship_map)
        slide_size = typography.slide_size()
        document_title = extract_document_title(package, report)
        source_file_title = title_from_file_name(original_file_name)
        inventory_objects = _inventory_package(package, report)
        media_assets: dict[str, SealedManifestAsset] = {}
        image_occurrences: Counter[str] = Counter()
        units: list[SourceContentUnit] = []
        totals = {
            "table_cells": 0,
            "chart_values": 0,
            "tables": 0,
            "charts": 0,
            "picture_bullets": 0,
        }
        slide_parts = _ordered_slide_parts(package, report)
        for index, part_name in enumerate(slide_parts):
            source_unit_id = f"source-slide:{index + 1}"
            unit, objects, stats = _extract_unit(
                package,
                part_name=part_name,
                source_unit_id=source_unit_id,
                source_index=index,
                object_type="slide_object",
                media_assets=media_assets,
                image_occurrences=image_occurrences,
                typography=typography,
            )
            units.append(unit)
            inventory_objects.extend(objects)
            for key in totals:
                totals[key] += stats[key]

            notes_part = None
            for target, rel_type in _relationship_map(package, part_name).values():
                if rel_type.endswith("/notesSlide"):
                    notes_part = target
                    break
            if notes_part is not None and notes_part in package.namelist():
                notes_unit, notes_objects, _ = _extract_unit(
                    package,
                    part_name=notes_part,
                    source_unit_id=source_unit_id,
                    source_index=index,
                    object_type="notes_object",
                    media_assets=media_assets,
                    image_occurrences=image_occurrences,
                    typography=typography,
                )
                if notes_unit.blocks:
                    units[-1] = SourceContentUnit(
                        source_unit_id=source_unit_id,
                        source_index=index,
                        blocks=tuple([*unit.blocks, *notes_unit.blocks]),
                    )
                inventory_objects.extend(notes_objects)

    unsupported = tuple(
        item.object_id
        for item in inventory_objects
        if item.content_bearing and not item.supported and item.classification.startswith("unsupported")
    )
    unclassified = tuple(
        item.object_id for item in inventory_objects if item.classification == "unclassified"
    )
    classified_count = sum(item.classification != "unclassified" for item in inventory_objects)
    inventory = SourceObjectInventorySummary(
        discovered_object_count=len(inventory_objects),
        classified_object_count=classified_count,
        content_bearing_object_count=sum(item.content_bearing for item in inventory_objects),
        unsupported_content_object_ids=unsupported,
        unclassified_object_ids=unclassified,
        external_relationship_count=report.external_relationship_count,
    )
    if unclassified:
        raise SourceContentError(
            MANIFEST_INVALID,
            detail={"unclassifiedObjectIds": list(unclassified[:50])},
        )
    if unsupported:
        raise SourceContentError(
            UNSUPPORTED_OBJECT,
            detail={"unsupportedContentObjectIds": list(unsupported[:50])},
        )

    blocks = [block for unit in units for block in unit.blocks]
    counts = SourceContentCounts(
        source_units=len(units),
        # Speaker notes are metadata, not visible source substance to carry
        # into a template-restyled slide.
        text_blocks=sum(block.kind == "text" for block in blocks),
        table_cells=totals["table_cells"],
        chart_values=totals["chart_values"],
        unique_images=len(media_assets),
        image_occurrences=sum(image_occurrences.values()),
        picture_bullet_count=totals["picture_bullets"],
        raster_fallback_blocks=sum(not block.native_candidate for block in blocks),
    )
    manifest_without_digest = {
        "contractVersion": "source-content-manifest/v1",
        "sourceLayoutVersion": SOURCE_LAYOUT_VERSION_V2,
        "manifestId": manifest_id,
        "referenceSnapshotId": reference_snapshot_id,
        "sourceType": "pptx",
        "sourceDigest": source_digest,
        "objectInventory": inventory.model_dump(mode="json", by_alias=True),
        "inventoryObjects": [
            item.model_dump(mode="json", by_alias=True) for item in inventory_objects
        ],
        "units": [unit.model_dump(mode="json", by_alias=True) for unit in units],
        "counts": counts.model_dump(mode="json", by_alias=True),
    }
    if slide_size is not None:
        manifest_without_digest["slideSize"] = slide_size
    if document_title is not None:
        manifest_without_digest["documentTitle"] = document_title
    if source_file_title is not None:
        manifest_without_digest["sourceFileTitle"] = source_file_title
    manifest = SourceContentManifest(
        **manifest_without_digest,
        owner_id=owner_id,
        manifest_digest=digest_json(manifest_without_digest),
    )
    return ExtractedSourceContent(
        manifest=manifest,
        assets=tuple(media_assets[key] for key in sorted(media_assets)),
    )
