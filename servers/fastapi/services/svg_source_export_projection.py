"""Recover native tables from a sealed layout checkpoint without editing a slide.

Only identity groups are added. Original browser/vision evidence remains bound
to the persisted parent page; export identity also binds this verified derivative.
"""
from __future__ import annotations

from copy import deepcopy
import re
from types import SimpleNamespace
from xml.etree import ElementTree as ET

from sqlalchemy import select

from services.svg_contract import source_digest


def _tree(node):
    return (node.tag, tuple(sorted(node.attrib.items())), node.text, node.tail,
            tuple(_tree(child) for child in node))


def replay_source_table_projection(slide, receipt, checkpoint, manifest, snapshot, *, pt_per_px):
    from services.generation_core.authoring.author_source_svg_layout import repair_source_plan
    from services.source_content.digests import digest_json
    from services.svg_source_layout import SvgLayoutPlan, assemble_source_svg, verify_source_receipt
    from services.svg_source_template_layout import repair_template_coordinates, selected_template, validate_template_plan
    from services.svg_source_native_tables import source_table_native_objects, unwrap_source_table_groups
    from services.generation_core.native_objects import SLIDE_CONTENT_KEY as NATIVE_OBJECTS_KEY

    index = slide.index
    verify_source_receipt(slide.svg_content, receipt, manifest, index)
    if checkpoint.get("svg_content") != slide.svg_content:
        raise ValueError("svg_source_export_checkpoint_stale")
    template = selected_template(snapshot)
    floor = receipt["font_policy"]["minimum_px"]
    plan = None
    for attempt in reversed(checkpoint.get("attempts", [])):
        author = attempt.get("receipt") or {}
        if (author.get("source_preservation") or {}).get("plan_digest") != receipt["plan_digest"]:
            continue
        raw = attempt.get("raw_response") or ""
        if author.get("raw_digest") != source_digest(raw):
            raise ValueError("svg_source_export_checkpoint_raw_mismatch")
        fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", raw.strip())
        plan = SvgLayoutPlan.model_validate_json(fenced[1] if fenced else raw.strip())
        validate_template_plan(plan, manifest, index, template)
        plan, _ = repair_source_plan(plan, manifest, index, floor, template)
        plan, _ = repair_template_coordinates(plan, manifest, index, template)
        break
    if plan is None or digest_json(plan.model_dump(mode="json")) != receipt["plan_digest"]:
        raise ValueError("svg_source_export_checkpoint_plan_mismatch")
    images = {b["slot_id"]: {"required": True} for b in receipt["bindings"] if b["kind"] == "image"}
    slots = {b["source_id"]: b["slot_id"] for b in receipt["bindings"] if b["kind"] == "image"}
    assembled = assemble_source_svg(manifest, index, plan, source_images=images, image_slots=slots,
        min_font_size_px=floor, style_digest=snapshot["digest"], template_snapshot=template)
    unwrapped = unwrap_source_table_groups(assembled.svg_content, assembled.receipt)
    if _tree(ET.fromstring(unwrapped)) != _tree(ET.fromstring(slide.svg_content)):
        raise ValueError("svg_source_export_projection_paint_mismatch")
    for key, value in assembled.receipt.items():
        if key not in {"svg_digest", "native_tables", "rendering_verified"} and receipt.get(key) != value:
            raise ValueError("svg_source_export_projection_receipt_mismatch")
    projected = deepcopy(receipt)
    projected.update(svg_digest=source_digest(assembled.svg_content), native_tables=assembled.receipt["native_tables"],
        export_projection={"version": "svg-source-native-table-projection/v1",
            "parent_svg_digest": receipt["svg_digest"], "unchanged_paint": True,
            "plan_digest": receipt["plan_digest"]})
    verify_source_receipt(assembled.svg_content, projected, manifest, index)
    native, summary = source_table_native_objects(assembled.svg_content, projected, manifest, index, pt_per_px=pt_per_px)
    if not native or not summary or summary.get("dropped"):
        raise ValueError("svg_source_export_projection_native_failed")
    content = deepcopy(slide.content)
    content[NATIVE_OBJECTS_KEY] = native
    content["svgReceipt"]["source_preservation"] = projected
    content["svgReceipt"]["nativeObjects"] = summary
    if content["svgReceipt"].get("system_furniture"):
        from services.svg_master_furniture import master_furniture_receipt
        content["svgReceipt"]["system_furniture"] = master_furniture_receipt(
            assembled.svg_content, snapshot, width=plan.width, height=plan.height,
            index=index, n=receipt["page_count"], source_manifest=images)
    # This is an export view, never attached to a persistence session.
    return SimpleNamespace(**{**slide.model_dump(), "svg_content": assembled.svg_content, "content": content})


async def project_cached_source_tables(session, *, presentation, slides, receipts, canvas, owner_id):
    snapshot = ((presentation.theme or {}).get("svg") or {}).get("styleSnapshot") or {}
    if (snapshot.get("source_template") or {}).get("composition_mode") != "free-svg":
        return slides
    # A failure placeholder (``None`` receipt) has no source table to project.
    present = [r for r in receipts or () if r is not None]
    if not present or not any(not r.get("native_tables") and any(b["kind"] == "table_cell" for b in r["bindings"])
                              for r in present):
        return slides
    if presentation.owner_id != owner_id:
        raise ValueError("svg_source_export_owner_mismatch")
    from models.sql.source_content import SourceContentManifestRecord
    from models.sql.presentation_generation_job import PresentationGenerationJob
    from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
    from services.source_content.manifest_store import _manifest_from_record
    from services.generation_core.drivers.svg.native_publish import canvas_point_per_px

    records = (await session.scalars(select(SourceContentManifestRecord).where(
        SourceContentManifestRecord.owner_id == owner_id,
        SourceContentManifestRecord.owner_scope == str(owner_id),
        SourceContentManifestRecord.manifest_digest == present[0]["manifest_digest"],
        SourceContentManifestRecord.source_digest == present[0]["source_digest"]))).all()
    if len(records) != 1:
        raise ValueError("svg_source_export_manifest_binding_invalid")
    manifest = _manifest_from_record(records[0], owner_id=owner_id)
    job = await session.scalar(select(PresentationGenerationJob).where(
        PresentationGenerationJob.presentation_id == presentation.id))
    if job is None or job.svg_style_snapshot != snapshot:
        raise ValueError("svg_source_export_checkpoint_style_mismatch")
    checkpoints = {row.slide_index: row.svg_checkpoint for row in (await session.scalars(
        select(AuthoredSlideCheckpoint).where(AuthoredSlideCheckpoint.job_id == job.id))).all()}
    projected = []
    for slide, receipt in zip(slides, receipts, strict=True):
        if (receipt is None or receipt.get("native_tables")
                or not any(b["kind"] == "table_cell" for b in receipt["bindings"])):
            projected.append(slide)
            continue
        projected.append(replay_source_table_projection(slide, receipt, checkpoints.get(slide.index) or {},
            manifest, snapshot, pt_per_px=canvas_point_per_px(canvas)))
    return projected
