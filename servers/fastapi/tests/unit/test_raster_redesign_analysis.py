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
