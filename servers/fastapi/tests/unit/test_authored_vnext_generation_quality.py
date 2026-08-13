from __future__ import annotations

from copy import deepcopy

import pytest

from services.authored_vnext.generation.montage import build_montage_manifest
from services.authored_vnext.generation.quality import (
    validate_deck_consistency,
    validate_slide_candidate,
)


MASTER = "sha256:" + "1" * 64
FONTS = "sha256:" + "2" * 64
COLORS = "sha256:" + "3" * 64
FRAME = "sha256:" + "4" * 64
LAYOUTS = "sha256:" + "5" * 64
SOURCE = "sha256:" + "6" * 64


def _candidate(number: int = 1, *, layout: str = "layout:standard") -> dict[str, object]:
    slide_id = f"slide:{number:02d}"
    title = f"제목 {number}"
    body = f"본문 {number}은 source snapshot에서 생성되었습니다."
    slot_ids = ["slot:standard-title", "slot:standard-body"]
    return {
        "slideId": slide_id,
        "revision": 1,
        "masterDigest": MASTER,
        "fontManifestDigest": FONTS,
        "colorDigest": COLORS,
        "frameDigest": FRAME,
        "layoutRegistryDigest": LAYOUTS,
        "layoutId": layout,
        "slotIds": slot_ids,
        "sourceDigest": SOURCE,
        "pageNumber": number,
        "specDigest": "sha256:" + f"{number:064x}",
        "title": {"text": title, "lines": [title]},
        "body": body,
        "titleFit": {
            "layoutId": layout,
            "fontManifestDigest": FONTS,
            "clipped": False,
            "violations": [],
        },
        "dom": {
            "text": f"HEADER {title} {body} FOOTER {number}",
            "slotIds": slot_ids,
            "sourceDigest": SOURCE,
            "overflowCount": 0,
            "overlapCount": 0,
            "duplicatedFurnitureIds": [],
        },
        "ocr": {
            "text": f"{title} {body}",
            "sourceDigest": SOURCE,
        },
        "previewArtifact": {
            "artifactDigest": "sha256:" + f"{number + 100:064x}",
            "storageReference": f"artifact://preview/{slide_id}.png",
        },
    }


def _deck_gate(slides: list[dict[str, object]]):
    return validate_deck_consistency(
        slides,
        expected_master_digest=MASTER,
        expected_font_manifest_digest=FONTS,
        expected_color_digest=COLORS,
        expected_frame_digest=FRAME,
        expected_layout_registry_digest=LAYOUTS,
        expected_source_digest=SOURCE,
        allowed_layout_ids={"layout:standard"},
        expected_slot_ids={
            "layout:standard": {"slot:standard-title", "slot:standard-body"},
        },
    )


def test_slide_candidate_passes_dom_ocr_title_fit_and_checkpoint_gates() -> None:
    report = validate_slide_candidate(
        _candidate(),
        expected_master_digest=MASTER,
        allowed_layout_ids={"layout:standard"},
        expected_slot_ids={
            "layout:standard": {"slot:standard-title", "slot:standard-body"},
        },
        expected_source_digest=SOURCE,
    )

    assert report.valid is True
    assert report.errors == ()
    assert report.ocr_text_coverage == 1.0
    assert report.to_dict()["checks"] == {
        "master": True,
        "layout": True,
        "slot": True,
        "source": True,
        "dom": True,
        "ocr": True,
        "titleFit": True,
    }


def test_slide_candidate_fails_closed_on_dom_ocr_and_title_clipping() -> None:
    candidate = _candidate()
    candidate["dom"] = {"text": "", "overflowCount": 1}
    candidate["ocr"] = {"text": "완전히 다른 내용", "sourceDigest": SOURCE}
    candidate["titleFit"] = {
        "layoutId": "layout:standard",
        "fontManifestDigest": FONTS,
        "clipped": True,
        "violations": ["textBoundsExceeded"],
    }

    report = validate_slide_candidate(candidate, expected_master_digest=MASTER)

    assert report.valid is False
    assert {error.code for error in report.errors} == {
        "quality_candidate_dom_gate_failed",
        "quality_candidate_ocr_gate_failed",
        "quality_candidate_title_fit_failed",
    }
    assert report.checks["dom"] is False
    assert report.checks["ocr"] is False
    assert report.checks["titleFit"] is False


def test_slide_candidate_rejects_stale_master_layout_slot_and_source() -> None:
    candidate = _candidate()
    candidate["masterDigest"] = "sha256:" + "9" * 64
    candidate["layoutId"] = "layout:unpublished"
    candidate["slotIds"] = ["slot:model-owned-furniture"]
    candidate["sourceDigest"] = "sha256:" + "8" * 64

    report = validate_slide_candidate(
        candidate,
        expected_master_digest=MASTER,
        allowed_layout_ids={"layout:standard"},
        expected_slot_ids={
            "layout:standard": {"slot:standard-title", "slot:standard-body"},
        },
        expected_source_digest=SOURCE,
    )

    codes = {error.code for error in report.errors}
    assert "quality_candidate_master_digest_mismatch" in codes
    assert "quality_candidate_layout_invalid" in codes
    assert "quality_candidate_slot_mismatch" in codes
    assert "quality_candidate_source_digest_mismatch" in codes


def test_ten_slide_deck_passes_all_required_consistency_checks() -> None:
    slides = [_candidate(index) for index in range(1, 11)]

    report = _deck_gate(slides)

    assert report.valid is True
    assert report.slide_count == 10
    assert report.errors == ()
    assert report.to_dict()["checks"] == {
        "master": True,
        "fonts": True,
        "colors": True,
        "frame": True,
        "pageNo": True,
        "layout": True,
        "slot": True,
        "source": True,
    }


def test_deck_gate_reports_each_drift_dimension_without_short_circuiting() -> None:
    slides = [_candidate(1), _candidate(2)]
    drifted = slides[1]
    drifted["masterDigest"] = "sha256:" + "a" * 64
    drifted["fontManifestDigest"] = "sha256:" + "b" * 64
    drifted["colorDigest"] = "sha256:" + "c" * 64
    drifted["frameDigest"] = "sha256:" + "d" * 64
    drifted["pageNumber"] = 4
    drifted["layoutRegistryDigest"] = "sha256:" + "e" * 64
    drifted["slotIds"] = ["slot:unknown"]
    drifted["sourceDigest"] = "sha256:" + "f" * 64

    report = _deck_gate(slides)

    assert report.valid is False
    assert report.to_dict()["checks"] == {
        name: False
        for name in ("master", "fonts", "colors", "frame", "pageNo", "layout", "slot", "source")
    }
    assert len(report.errors) == 8


@pytest.mark.parametrize(("count", "expected_sheets"), [(10, 1), (90, 9)])
def test_montage_manifest_covers_every_slide_deterministically(
    count: int,
    expected_sheets: int,
) -> None:
    slides = [_candidate(index) for index in range(1, count + 1)]

    first = build_montage_manifest(slides, deck_id="deck:fixture", deck_digest=MASTER)
    second = build_montage_manifest(deepcopy(slides), deck_id="deck:fixture", deck_digest=MASTER)

    assert first == second
    assert first.slide_count == count
    assert first.sheet_count == expected_sheets
    assert len(first.tiles) == count
    assert [tile.slide_number for tile in first.tiles] == list(range(1, count + 1))
    assert first.tiles[0].sheet == 1
    assert first.tiles[9].row == 2
    assert first.tiles[9].column == 5
    assert first.tiles[-1].sheet == expected_sheets
    assert first.to_dict()["selectionPolicy"] == "all-slides-in-deck-order"
    assert first.digest.startswith("sha256:")


def test_montage_manifest_fails_closed_without_preview_provenance() -> None:
    slide = _candidate()
    del slide["previewArtifact"]

    with pytest.raises(ValueError, match="previewArtifact.artifactDigest"):
        build_montage_manifest([slide], deck_id="deck:fixture", deck_digest=MASTER)
