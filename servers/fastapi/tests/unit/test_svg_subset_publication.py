"""Real subset producer -> publication -> export evidence, with storage isolated."""
import asyncio
from types import SimpleNamespace

import pytest

from models.sql.presentation import PresentationModel
from services.generation_core.drivers.svg import publish, quality
from services.svg_export_visual_review import svg_visual_review, svg_quality_status
from tests.unit.test_svg_axis_review import _fresh
from tests.unit.test_svg_page_quality_publication import (
    PNG, _Session, _outline, _published, _request, _storage,
)
from tests.unit.test_svg_review_read_retries import _provider
from utils.hashing import sha256_hex


@pytest.mark.parametrize("invalid", [[1, 4], [1], []])
def test_subset_publication_and_export_keep_original_page_identity(monkeypatch, invalid):
    harness = _fresh(monkeypatch, 5)
    original_context = quality._design_context
    pngs = [PNG + str(index).encode() for index in range(5)]
    for index, (payload, _) in enumerate(harness.pages):
        payload["png_sha256"] = sha256_hex(pngs[index])
        payload["quality"]["png_digest"] = "sha256:" + sha256_hex(pngs[index])
        if index in invalid:
            payload["quality"]["passed"] = False
            payload["terminal"] = dict(index=index, reason="svg:svg_source_quality_failed",
                                       remakeOutcome="verbatim_flag_only")
        harness.pages[index] = (payload, pngs[index])

    def context(*args, **kwargs):
        result = original_context(*args, **kwargs)
        result["mandatoryEvidence"] = [index not in invalid for index in range(5)]
        return result

    monkeypatch.setattr(quality, "_design_context", context)
    by_digest = {sha256_hex(png): png for png in pngs}
    advisory = asyncio.run(quality.deck_advisory(harness.ctx, harness.pages, job=harness.job,
        read_png=lambda payload: by_digest[payload["png_sha256"]], run_advisory=_provider(harness)))
    expected = [index for index in range(5) if index not in invalid]
    gate = advisory["vision"]["designGate"]
    assert gate["unreviewedPages"] == invalid
    assert gate["status"] == ("unreviewed" if invalid else "passed")
    assert quality.quality_recovery_decisions(harness.ctx, harness.pages, advisory) == {}

    _storage(monkeypatch)
    harness.job.attempt_token = "attempt"
    presentation = PresentationModel(id=harness.job.presentation_id, owner_id=harness.owner,
        content="deck", n_slides=5, mode="svg", revision=0)
    session = _Session(presentation, SimpleNamespace(owner_id=harness.owner,
        presentation_id=harness.job.presentation_id, owner_scope=harness.owner))
    asyncio.run(publish._publish(_request(), harness.job.presentation_id, _outline(5), "ko", session,
        harness.job, harness.snapshot, ["content"] * 5, harness.pages,
        quality_summary=quality.reclassify_pages(harness.pages), advisory=advisory))
    session.commit.assert_awaited_once()
    svg, slides = _published(session)
    assert len(slides) == 5
    assert svg["reviewPages"] == invalid
    assert svg["designReview"]["unreviewedPages"] == invalid
    assert svg["advisory"]["operationBinding"]["reviewedPageIndices"] == expected
    records = {record["index"]: record for record in svg["pageAdvisories"]}
    assert set(records) == set(range(5))
    for index, record in records.items():
        page = record["vision"]
        assert record["pngDigest"] == sha256_hex(pngs[index])
        assert page["reviewedPageIndices"] == ([] if index in invalid else [index])
        assert page["reviewedPngDigests"] == ([] if index in invalid else [sha256_hex(pngs[index])])
        if index in invalid:
            assert page["skipped"] == "page_not_reviewed"
            assert page["unreviewedPages"] == [index] and page["slides"] == []
    status = svg_quality_status(slides, svg["advisory"])
    assert status["reviewedPages"] == expected
    assert status["semanticCurrent"]["status"] == ("partial" if invalid else "current")
    exported = svg_visual_review(slides=slides, advisory=svg["advisory"])
    if invalid:
        assert exported == dict(required=True, status="failed",
            reasonCode="svg_visual_advisory_review_coverage_incomplete")
    else:
        assert exported["status"] == "passed" and "reasonCode" not in exported
