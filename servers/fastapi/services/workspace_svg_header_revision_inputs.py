"""Read-only source adapters and operation-fenced SVG asset inputs for header edits."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree as ET
import uuid

from services.svg_common_header_generation import header_revision_context
from utils.hashing import canonical_json_digest, sha256_hex


async def revision_inputs(session, claim, presentation, rows, snapshot, admitted):
    from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
    from models.sql.image_asset import ImageAsset
    from services.artifact_store import configured_public_artifact_service
    from services.authored_canvas import presentation_canvas_contract, presentation_canvas_metadata
    from services.svg_asset_manifest import SourceManifest
    from services.svg_asset_persistence import SvgPublicationContext, persist_manifest_if_current
    from services.svg_document_mutations import read_svg_slide_assets
    from services.svg_generation_assets import author_images
    from services.source_images.models import SourceImageCandidate
    from services.svg_export_source_preservation import source_preservation_receipts, sealed_whole_page_manifest
    from utils.owner_context import get_current_tenant_id

    canvas = presentation_canvas_contract(presentation.theme)
    tenant = get_current_tenant_id()
    if not tenant:
        raise ValueError("svg_common_header_principal_required")
    service = configured_public_artifact_service()
    asset_context = SvgPublicationContext(operation_id=claim.operation_id, owner_id=claim.owner_id,
        tenant_id=tenant, attempt_number=claim.attempt_number, attempt_token=claim.attempt_token,
        fencing_token=claim.fencing_token)
    assets = [await read_svg_slide_assets(session, presentation, row) for row in rows]
    if any(asset is None for asset in assets) and any(asset is not None for asset in assets):
        raise ValueError("svg_common_header_mixed_asset_contract")
    manifests, planned = [{} for _ in rows], [{} for _ in rows]
    pointer = source = None
    if any(assets):
        slots, offered = {}, []
        for index, (row, asset) in enumerate(zip(rows, assets, strict=True)):
            manifest = asset[0]
            source_index = row.content["svgAssetManifest"].get("sourcePageIndex", index)
            selected = next(page.slot_ids for page in manifest.offered_pages if page.page_index == source_index)
            offered.append({"pageIndex": index, "slotIds": list(selected)})
            for slot in manifest.slots:
                if slot.slot_id not in selected:
                    continue
                if slot.slot_id in slots and slots[slot.slot_id] != slot:
                    raise ValueError("svg_common_header_source_asset_conflict")
                slots[slot.slot_id] = slot
                asset_row = await session.get(ImageAsset, slot.source_asset_id)
                if asset_row is None or asset_row.owner_id != claim.owner_id:
                    raise ValueError("svg_common_header_source_asset_owner_invalid")
                path = Path(asset_row.path)
                if path.stat().st_size > 32 * 1024 * 1024 or sha256_hex(path.read_bytes()) != slot.source_sha256:
                    raise ValueError("svg_source_image_digest_mismatch")
                planned[index][slot.slot_id] = SourceImageCandidate(asset_id=str(slot.source_asset_id), path=path,
                    mime_type=(asset_row.extras or {}).get("mime_type", "image/png"), width=slot.source_width,
                    height=slot.source_height, digest=slot.source_sha256, occurrence_id=str(slot.occurrence_id),
                    semantic_description=slot.semantic_description, source_locator=slot.source_locator.model_dump(mode="json", by_alias=True))
        source = SourceManifest.seal({"presentationId": presentation.id, "sourceRevision": claim.expected_revision + 1,
            "slots": [slot.model_dump(mode="json", by_alias=True) for slot in slots.values()], "offeredPages": offered})
        pointer_model = await persist_manifest_if_current(session, service, context=asset_context, manifest=source,
            publication_name="svg-header-revision-source-manifest", revision=claim.expected_revision + 1)
        if pointer_model is None:
            raise ValueError("svg_asset_publication_fence_lost")
        pointer = pointer_model.model_dump(mode="json", by_alias=True)
        manifests = [await author_images(session, service, context=asset_context, manifest=source, page_index=index)
                     for index in range(len(rows))]
        by_slot = {slot.slot_id: slot for slot in source.slots}
        for page in manifests:
            for slot_id, value in page.items():
                value["policy"] = by_slot[slot_id].policy.model_dump(mode="json", by_alias=True)
    receipts = source_preservation_receipts(presentation, rows, canvas)
    sealed = await sealed_whole_page_manifest(session, claim=claim, presentation=presentation, slides=rows, receipts=receipts)
    document = units = profile = None
    if sealed is not None:
        from services.source_content.contracts import SourceContentManifest
        from services.svg_source_layout import source_pages
        from utils.svg_template_fonts import source_font_profile
        document = SourceContentManifest.model_validate(sealed["manifest"])
        units = source_pages(document, chart_contract=snapshot.get("source_chart_contract"))
        profile = source_font_profile(snapshot)
    elif receipts:
        from sqlalchemy import select
        from models.sql.source_content import SourceContentManifestRecord
        from services.source_content.manifest_store import _manifest_from_record
        from services.svg_source_layout import source_pages, verify_source_receipt
        from utils.svg_template_fonts import source_font_profile
        records = list(await session.scalars(select(SourceContentManifestRecord).where(
            SourceContentManifestRecord.owner_id == claim.owner_id,
            SourceContentManifestRecord.owner_scope == claim.owner_scope,
            SourceContentManifestRecord.manifest_digest == receipts[0]["manifest_digest"],
            SourceContentManifestRecord.source_digest == receipts[0]["source_digest"])))
        if len(records) != 1:
            raise ValueError("svg_common_header_source_manifest_binding_invalid")
        document = _manifest_from_record(records[0], owner_id=claim.owner_id)
        for index, (row, receipt) in enumerate(zip(rows, receipts, strict=True)):
            verify_source_receipt(row.svg_content, receipt, document, index)
        units = source_pages(document, chart_contract=snapshot.get("source_chart_contract"))
        profile = source_font_profile(snapshot)
    raster_analysis = raster_pin = None
    raster_receipts = [(row.content or {}).get("svgReceipt") or {} for row in rows]
    pins = [receipt.get("rasterApprovalPin") for receipt in raster_receipts]
    if any(pins):
        if any(pin != pins[0] for pin in pins):
            raise ValueError("svg_common_header_raster_pin_conflict")
        from services.source_content.raster_contracts import RasterApprovalPin
        from services.source_content.raster_reconstruction_store import admit_approved_raster_analysis
        raster_pin = RasterApprovalPin.model_validate(pins[0])
        raster_analysis = await admit_approved_raster_analysis(session, operation_id=raster_pin.operation_id,
            owner_id=claim.owner_id, tenant_id=tenant, pin=raster_pin, artifact_service=service)
        if raster_pin.full_page_preserve_pages:
            raise ValueError("svg_common_header_source_preservation_conflict")
    physical = presentation_canvas_metadata(presentation.theme)
    from services.document_canvas import parse_canvas_metadata, DocumentCanvasMetadata
    physical_metadata = parse_canvas_metadata(physical)
    context_snapshot = {**deepcopy(snapshot), "common_header_design_plan": admitted["plan"]}
    context_snapshot["digest"] = canonical_json_digest({key: value for key, value in context_snapshot.items() if key != "digest"}, allow_nan=True)
    ctx = header_revision_context(snapshot=context_snapshot,
        canvas=canvas, roles=["content"] * len(rows), manifests=manifests,
        physical_kwargs={"physical_canvas": physical_metadata} if isinstance(physical_metadata, DocumentCanvasMetadata) else {}, source_reflow=document is not None,
        source_document=document, source_units=units, source_font_profile=profile,
        canonical_assets=source is not None, source_manifest=source, source_pointer=pointer,
        asset_context=asset_context, asset_revision=claim.expected_revision + 1,
        service=service, planned=planned, raster_reconstruction=raster_pin is not None,
        raster_analysis=raster_analysis, raster_pin=raster_pin)
    from sqlalchemy import select
    from services.generation_core.drivers.svg.native_declarations import native_binding_digest
    checkpoints = list(await session.scalars(select(AuthoredSlideCheckpoint).where(
        AuthoredSlideCheckpoint.job_id == uuid.UUID(admitted["source_job_id"]))))
    reuse_payloads = {}
    for checkpoint in checkpoints:
        payload = checkpoint.svg_checkpoint or {}
        index = checkpoint.slide_index
        if (0 <= index < len(rows) and native_binding_digest(payload.get("svg_content")) is not None
                and native_binding_digest(payload["svg_content"]) == native_binding_digest(rows[index].svg_content)):
            reuse_payloads[index] = deepcopy(payload)
    await session.commit()
    return {"ctx": ctx, "assets": assets, "source_document": document, "reuse_payloads": reuse_payloads}


def raw_body(row):
    from services.svg_common_header import GROUP_ID
    from services.svg_export_page_evidence import _common_header_current
    receipt = (row.content or {}).get("svgReceipt") or {}
    composition = (receipt.get("source_preservation") or {}).get("common_header_composition")
    if composition:
        return composition["body_svg"], deepcopy(composition["body_receipt"])
    root = ET.fromstring(row.svg_content)
    groups = [node for node in root.iter() if node.get("id") == GROUP_ID]
    if groups:
        if len(groups) != 1 or groups[0] not in list(root) or not _common_header_current(row, receipt.get("quality") or {}):
            raise ValueError("svg_common_header_body_reuse_unverified")
        root.remove(groups[0])
    return ET.tostring(root, encoding="unicode"), deepcopy(receipt.get("source_preservation"))


def reusable_body(old, new, row):
    from services.svg_contract import source_digest
    receipt = (row.content or {}).get("svgReceipt") or {}
    quality = receipt.get("quality") or {}
    return (isinstance(old, dict) and old.get("body_box") == new.get("body_box")
            and old.get("shared_style", {}) == new.get("shared_style", {})
            and not receipt.get("terminal") and not receipt.get("flag")
            and quality.get("passed") is True and quality.get("geometry_passed") is True
            and quality.get("measurement_complete") is True
            and quality.get("source_digest") == source_digest(row.svg_content))


def merge_header_revision_values(raw_design, outline, prepared_values, *, parent_revision):
    """Inherit omitted fixed scopes by stable page id and retain their exact origin."""
    prefixes = {"document_values": "request:#/common_header_design/document_values/",
                "page_values": "request:#/common_header_design/page_values/"}
    result, lineage, document_values, page_values = deepcopy(prepared_values), [], {}, []
    old = {slide.page_id: slide.common_header for slide in outline.slides}
    for index, page in enumerate(result):
        page_fixed = {}
        previous = old.get(page["page_id"]) or {}
        for field in previous.get("fields", []):
            key = field.get("value_ref")
            if field.get("user_fixed") is not True or key in {"title", "page_number", "page_count"}:
                continue
            source_ref = field.get("source_ref", "")
            scope = next((name for name, prefix in prefixes.items() if source_ref.startswith(prefix)), None)
            if scope is None:
                raise ValueError("svg_common_header_value_scope_unavailable")
            if scope in raw_design:
                continue
            incoming = page["values"].get(key)
            if incoming is not None and incoming.get("user_fixed") is True and incoming.get("source_ref") != source_ref:
                raise ValueError("svg_common_header_value_scope_conflict")
            bound = {name: deepcopy(field[name]) for name in ("value", "source_ref", "user_fixed")}
            page["values"][key] = bound
            lineage.append({"page_id": page["page_id"], "value_ref": key, "scope": scope,
                            "parent_revision": parent_revision, **bound})
        for key, bound in page["values"].items():
            if bound.get("user_fixed") is not True:
                continue
            source_ref = bound["source_ref"]
            if source_ref.startswith(prefixes["document_values"]):
                if key in document_values and document_values[key] != bound["value"]:
                    raise ValueError("svg_common_header_value_scope_conflict")
                document_values[key] = bound["value"]
            elif source_ref.startswith(prefixes["page_values"]):
                page_fixed[key] = bound["value"]
            else:
                raise ValueError("svg_common_header_value_scope_unavailable")
        if page_fixed:
            page_values.append({"page_index": index, "values": page_fixed})
    return result, {"document_values": document_values, "page_values": page_values}, lineage
