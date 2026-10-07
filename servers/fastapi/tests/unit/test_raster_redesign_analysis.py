"""Contract, digest back-compat, ledger and blocker coverage for raster redesign."""

from __future__ import annotations

import copy
import hashlib
import uuid

import pytest
from pydantic import ValidationError

from models.sql.public_artifact import ArtifactRef, artifact_storage_key
from services.source_content.raster_contracts import (
    RasterAnalysis,
    RasterApprovalPin,
    approval_digest_matches,
    raster_digest,
    require_analysis_digest,
    seal_analysis,
    seal_approval,
)
from services.source_content.raster_reconstruction_store import raster_review_blockers
from services.source_content.raster_structure import redesign_content_ledger, xy_cut_order

W, H = 1_672_000, 941_000
SHA = hashlib.sha256(b"source").hexdigest()


def _token(text: str, x: float = 0) -> dict:
    return {
        "text": text,
        "bbox": {"x": x, "y": 0, "width": 10, "height": 10},
        "confidence": 0.95,
        "engine": "test",
        "source_sha256": SHA,
        "review_required": False,
    }


def _region(region_id: str, role: str, order: int, tokens: tuple[int, ...], **extra) -> dict:
    return {
        "region_id": region_id,
        "page_number": 1,
        "role": role,
        "bbox": {"x": 1000 * order, "y": 0, "width": 1000, "height": 1000},
        "z_order": order,
        "reading_order": order,
        "confidence": 0.9,
        "review_required": False,
        "ocr_token_indices": tokens,
        **extra,
    }


def _reference() -> dict:
    return {
        "region_id": "p1-source-reference",
        "page_number": 1,
        "role": "source_reference",
        "bbox": {"x": 0, "y": 0, "width": W, "height": H},
        "z_order": 0,
        "review_required": False,
    }


def _redesign_payload() -> dict:
    inventory = [_token("매출"), _token("현황"), _token("항목"), _token("40,000원"), _token("UI"), _token("—")]
    regions = [
        _region("p1-r1", "title", 0, (0, 1), text="매출 현황"),
        _region("p1-r2", "table", 1, (2, 3), table_cells=(("항목", "40,000원"),)),
        _region("p1-r3", "screenshot", 2, (4,)),
    ]
    return {
        "analysis_id": str(uuid.uuid4()),
        "source": {"source_sha256": SHA, "page_count": 1, "canvas_width_emu": W, "canvas_height_emu": H},
        "target_canvas": {"orientation": "landscape", "width_units": 1188, "height_units": 840},
        "pages": [
            {
                "page_number": 1,
                "regions": regions,
                "ocr_inventory": inventory,
                "render_mode": "redesign",
                "approval_method": "automatic_redesign",
                "reference_region": _reference(),
                "unpromoted_ocr_indices": (5,),
            }
        ],
    }


@pytest.mark.parametrize("style_id", ["default", "soft-editorial"])
def test_free_raster_composition_uses_selected_identity_and_preserves_reviewed_regions(style_id):
    import xml.etree.ElementTree as ET
    from services.generation_core.drivers.svg.svg_style_adapter import adapt_svg_style
    from services.source_content.raster_free_composition import raster_composition_context, assemble_free_raster_svg
    from services.svg_source_layout import SvgLayoutPlan
    from services.raster_source_assets import raster_slot_id
    from utils.authored_styles import resolve_authored_style

    payload = _redesign_payload()
    owner, operation = uuid.uuid4(), uuid.uuid4()
    crop = ArtifactRef(artifact_id=uuid.uuid4(), owner_id=owner, tenant_id="test", kind="image-derivative",
        sha256=SHA, size_bytes=20, media_type="image/png", created_by_operation_id=operation,
        retention_class="checkpoint", storage_key=artifact_storage_key(owner_id=owner, tenant_id="test", digest=SHA))
    payload["pages"][0]["regions"][2].update(crop_artifact_ref=crop.model_dump(mode="json"),
        source_occurrence_id="source-picture", source_pixel_bbox=(2, 0, 1, 1),
        crop_source_sha256=SHA, pixel_sha256=SHA)
    analysis = seal_analysis(payload)
    pin = RasterApprovalPin(operation_id=operation, analysis_id=analysis.analysis_id,
        analysis_revision=analysis.revision, analysis_digest=analysis.digest, source_sha256=SHA,
        approved_region_ids=tuple(region.region_id for region in analysis.pages[0].regions))
    snapshot = adapt_svg_style(resolve_authored_style(style_id), width=1188, height=840)
    context, items, ledger = raster_composition_context(analysis, pin, 0, snapshot)
    template = context["source_template"]
    placements = []
    for item, y in zip(items, (40, 250, 500), strict=True):
        role = "heading" if item.region.role == "title" else "table" if item.kind == "table" else "body"
        font = template["fonts"][role]
        placements.append(dict(block_id=item.region.region_id, x=40, y=y,
            width=200 if item.kind == "image" else 1000, height=200,
            font_family=font["family"], font_weight=str(font["weight"]), font_size=30 if role == "heading" else 20,
            fill=template["tokens"].get("heading", template["tokens"]["text"]) if role == "heading" else template["tokens"]["text"]))
    plan = SvgLayoutPlan(source_unit_id=context["source_unit_id"], page_digest=context["page_digest"],
        width=1188, height=840, background=template["tokens"]["background"], template_digest=template["digest"],
        layout_id="free-svg", placements=placements)
    svg, bindings = assemble_free_raster_svg(analysis, 0, plan, context, items)
    root = ET.fromstring(svg)
    text = "".join(root.itertext())
    assert "매출 현황" in text and "40,000원" in text and "항목" in text
    assert {binding["region_id"] for binding in bindings} == set(pin.approved_region_ids)
    assert ledger.lossless and analysis.digest == pin.analysis_digest
    assert [node.get("href") for node in root.iter() if node.tag.endswith("}image")] == [raster_slot_id(1, "p1-r3")]
    assert root[0].get("fill") == snapshot["tokens"]["background"]
    assert any(node.get("fill") == snapshot["tokens"]["primary"] for node in root.iter())
    invalid = plan.model_copy(update={"placements": plan.placements[:-1]})
    with pytest.raises(ValueError, match="region_coverage_mismatch"):
        assemble_free_raster_svg(analysis, 0, invalid, context, items)


def test_redesign_page_contract_accepts_valid_payload():
    analysis = seal_analysis(_redesign_payload())
    require_analysis_digest(analysis)
    page = analysis.pages[0]
    assert page.render_mode == "redesign" and page.reference_region.role == "source_reference"


@pytest.mark.parametrize(
    "mutate, code",
    [
        (lambda p: p["pages"][0].update(render_mode="reconstruct"), "raster_automatic_redesign_mode_invalid"),
        (lambda p: p["pages"][0].update(reference_region=None), "raster_redesign_reference_required"),
        (
            lambda p: p["pages"][0]["regions"].append(_reference() | {"region_id": "x"}),
            "raster_source_reference_role_requires_page_field",
        ),
        (lambda p: p["pages"][0]["regions"][0].update(table_cells=(("a",),)), "raster_table_cells_invalid"),
        (
            lambda p: p["pages"][0]["regions"][2].update(ocr_token_indices=(0,)),
            "raster_region_token_assignment_invalid",
        ),
        (lambda p: p["pages"][0]["regions"][2].update(ocr_token_indices=(5,)), "raster_unpromoted_token_carried"),
        (lambda p: p["pages"][0]["regions"][2].update(reading_order=0), "raster_reading_order_duplicate"),
        (lambda p: p["pages"][0]["reference_region"]["bbox"].update(width=10), "raster_reference_geometry_invalid"),
    ],
)
def test_redesign_page_contract_rejects_invalid_payloads(mutate, code):
    payload = _redesign_payload()
    mutate(payload)
    with pytest.raises(ValidationError, match=code):
        RasterAnalysis.model_validate(payload)


def _legacy_digest(analysis: RasterAnalysis) -> str:
    """Digest exactly as sealed before the redesign fields existed."""
    payload = analysis.model_dump(mode="json", exclude={"digest", "stage_timings"})
    for page in payload["pages"]:
        for key in ("reference_region", "unpromoted_ocr_indices"):
            page.pop(key)
        for region in page["regions"]:
            for key in (
                "confidence",
                "reading_order",
                "table_cells",
                "review_reason",
                "ocr_token_indices",
                "editable_fallback",
                "group_id",
                "heading_line_count",
            ):
                region.pop(key)
    return raster_digest(payload)


def _legacy_analysis() -> RasterAnalysis:
    return RasterAnalysis.model_validate(
        {
            "source": {"source_sha256": SHA, "page_count": 1, "canvas_width_emu": W, "canvas_height_emu": H},
            "target_canvas": {"orientation": "portrait"},
            "pages": [
                {
                    "page_number": 1,
                    "regions": [
                        {
                            "region_id": "p1-r1",
                            "page_number": 1,
                            "role": "body",
                            "bbox": {"x": 0, "y": 0, "width": 10, "height": 10},
                            "z_order": 0,
                            "text": "hello",
                        }
                    ],
                    "ocr_inventory": [_token("hello")],
                }
            ],
        }
    )


def test_stored_pre_redesign_analysis_digest_still_verifies():
    legacy = _legacy_analysis()
    stored = legacy.model_copy(update={"digest": _legacy_digest(legacy)})
    require_analysis_digest(stored)
    tampered = stored.model_copy(update={"unresolved": ("changed",)})
    with pytest.raises(ValueError, match="raster_analysis_digest_mismatch"):
        require_analysis_digest(tampered)


def test_redesign_data_cannot_use_the_legacy_digest_branch():
    analysis = seal_analysis(_redesign_payload())
    forged = analysis.model_copy(update={"digest": hashlib.sha256(b"x").hexdigest()})
    with pytest.raises(ValueError, match="raster_analysis_digest_mismatch"):
        require_analysis_digest(forged)


def _pin(**extra) -> RasterApprovalPin:
    return RasterApprovalPin(
        operation_id=uuid.uuid4(),
        analysis_id=uuid.uuid4(),
        analysis_revision=1,
        analysis_digest=SHA,
        source_sha256=SHA,
        approved_region_ids=("p1-r1",),
        **extra,
    )


def test_pin_digest_back_compat_and_redesign_pages():
    pin = _pin(automatic_preserve_pages=(2,))
    legacy = pin.model_copy(
        update={
            "approval_digest": raster_digest(
                pin.model_dump(mode="json", exclude={"approval_digest", "automatic_redesign_pages"})
            )
        }
    )
    assert approval_digest_matches(legacy)
    redesign = seal_approval(_pin(automatic_redesign_pages=(1,)))
    assert approval_digest_matches(redesign)
    forged = redesign.model_copy(
        update={
            "approval_digest": raster_digest(
                redesign.model_dump(mode="json", exclude={"approval_digest", "automatic_redesign_pages"})
            )
        }
    )
    assert not approval_digest_matches(forged)


def test_ledger_accounts_for_every_promoted_token():
    analysis = seal_analysis(_redesign_payload())
    ledger = redesign_content_ledger(analysis, 1)
    assert ledger.lossless, ledger.violations
    assert [entry.carrier for entry in ledger.entries] == ["text", "table_cells", "pixels"]
    assert ledger.entries[1].tokens == ("항목", "40,000원")
    assert ledger.unpromoted_indices == (5,)


def test_ledger_reports_missing_tokens_and_rewritten_numbers():
    payload = _redesign_payload()
    payload["pages"][0]["regions"][2]["ocr_token_indices"] = ()
    payload["pages"][0]["regions"][1]["table_cells"] = (("항목", "40,001원"),)
    ledger = redesign_content_ledger(seal_analysis(payload), 1)
    assert ledger.missing_indices == (4,)
    assert "p1-r2:numbers_not_verbatim" in ledger.violations
    assert not ledger.lossless


def _with_crops(payload: dict) -> dict:
    owner = uuid.uuid4()
    ref = ArtifactRef(
        artifact_id=uuid.uuid4(),
        owner_id=owner,
        tenant_id="tenant",
        kind="image-derivative",
        sha256=SHA,
        size_bytes=1,
        media_type="image/png",
        storage_key=artifact_storage_key(owner_id=owner, tenant_id="tenant", digest=SHA),
        created_by_operation_id=uuid.uuid4(),
        retention_class="published",
    ).model_dump(mode="json")
    page = payload["pages"][0]
    for region in (*page["regions"], page["reference_region"]):
        region.update(
            crop_artifact_ref=ref,
            source_occurrence_id="slide-1-image-1",
            source_media_part="ppt/media/image1.png",
            source_pixel_bbox=(0, 0, 10, 10),
            crop_source_sha256=SHA,
            pixel_sha256=SHA,
        )
    return payload


def test_redesign_blockers_only_flag_low_confidence_regions():
    payload = _with_crops(_redesign_payload())
    payload["unresolved"] = ("advisory:page_1:p1-r1:low_ocr_confidence", "page_1:human_review_required")
    analysis = seal_analysis(payload)
    assert raster_review_blockers(analysis) == ()
    flagged = copy.deepcopy(payload)
    flagged["pages"][0]["regions"][0].update(review_required=True, review_reason="low_ocr_confidence")
    assert raster_review_blockers(seal_analysis(flagged)) == ("page_1:low_confidence_region:p1-r1",)
    missing = copy.deepcopy(payload)
    missing["pages"][0]["reference_region"].pop("crop_artifact_ref")
    assert "page_1:reference_crop_missing" in raster_review_blockers(seal_analysis(missing))


def test_reconstruct_pages_keep_existing_blockers():
    analysis = seal_analysis(_legacy_analysis())
    assert "page_1:region_review_required" in raster_review_blockers(analysis)


def test_xy_cut_reads_columns_after_full_width_bands():
    boxes = [(0, 0, 100, 10), (0, 20, 40, 10), (60, 20, 40, 10), (0, 35, 40, 10), (60, 35, 40, 10)]
    assert xy_cut_order(boxes) == [0, 1, 3, 2, 4]


def test_advisory_entries_never_block_other_pages():
    analysis = seal_analysis(_legacy_analysis().model_dump(mode="json") | {"unresolved": ("advisory:page_9:x:y",)})
    assert "advisory:page_9:x:y" not in raster_review_blockers(analysis)


def test_editable_fallback_region_carries_tokens_as_pixels():
    payload = _redesign_payload()
    payload["pages"][0]["regions"][0].update(
        role="screenshot",
        editable_fallback=True,
        review_reason="low_ocr_confidence",
        text="매출 현황",
    )
    analysis = seal_analysis(payload)
    ledger = redesign_content_ledger(analysis, 1)
    assert ledger.lossless and ledger.entries[0].carrier == "pixels"
    assert analysis.pages[0].regions[0].text == "매출 현황"


@pytest.mark.parametrize("selectors", [
    {"aspect_ratio": "4:3"}, {"aspect_ratio": "9:16"}, {"aspect_ratio": "1:1"},
    {"aspect_ratio": "21:9"}, {"svg_canvas": {"width_mm": 157.13, "height_mm": 493.27}},
])
def test_raster_output_canvas_bound_to_analysis_digest(selectors):
    from services.source_content.raster_target import raster_target_from_selectors, raster_target_document_canvas
    from services.source_content.raster_contracts_base import RasterTargetCanvas

    payload = _redesign_payload()
    baseline = seal_analysis(payload)
    target = raster_target_from_selectors(**selectors)
    payload["target_canvas"] = target.model_dump(mode="json")
    analysis = seal_analysis(payload)
    assert analysis.digest != baseline.digest
    require_analysis_digest(analysis)
    restored = RasterAnalysis.model_validate_json(analysis.model_dump_json())
    assert restored.target_canvas == target
    assert restored.source == baseline.source
    assert raster_target_document_canvas(target) == target.document_canvas
    corrupt = target.model_dump(mode="json")
    corrupt["width_units"] += 1
    with pytest.raises(ValueError, match="raster_target_canvas_mismatch"):
        RasterTargetCanvas.model_validate(corrupt)


def test_raster_legacy_a4_target_wire_unchanged():
    from services.source_content.raster_target import raster_target_from_selectors

    assert raster_target_from_selectors(orientation="portrait").model_dump(mode="json") == {
        "format": "A4", "orientation": "portrait", "width_units": 840,
        "height_units": 1188, "emu_per_unit": 9000,
    }


def test_raster_analysis_request_selectors_are_exclusive():
    from api.v1.ppt.endpoints.raster_reconstruction import AnalyzeRasterRequest

    ids = {"reference_snapshot_id": uuid.uuid4(), "presentation_id": uuid.uuid4()}
    assert AnalyzeRasterRequest(**ids, aspect_ratio="4:3").aspect_ratio == "4:3"
    with pytest.raises(ValueError, match="document_canvas_selectors_conflict"):
        AnalyzeRasterRequest(**ids, aspect_ratio="4:3", orientation="landscape")



def test_custom_physical_readability_rejects_tiny_text():
    from services.source_content.raster_target import raster_target_from_selectors, require_raster_physical_readability

    target = raster_target_from_selectors(svg_canvas={"width_mm": 50, "height_mm": 50})
    with pytest.raises(ValueError, match="raster_text_requires_below_9_5pt"):
        require_raster_physical_readability('<svg><text font-size="14">내용</text></svg>', target)
    require_raster_physical_readability('<svg><text font-size="90">내용</text></svg>', target)
