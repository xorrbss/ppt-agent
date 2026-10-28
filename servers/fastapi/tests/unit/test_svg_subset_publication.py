"""Real subset producer -> publication -> export evidence, with storage isolated."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from models.sql.presentation import PresentationModel
from services.generation_core.drivers.svg import publish, quality
from services.generation_core.quality.vision_advisory_prompt import (
    AXIS_REVIEW_PROMPT_VERSION, DESIGN_REVIEW_PROMPT_VERSION,
)
from services.svg_export_visual_review import release_readiness, svg_visual_review, svg_quality_status
from services.svg_export_quality import svg_public_quality_receipt
from tests.unit.test_svg_axis_review import _fresh
from tests.unit.test_svg_page_quality_publication import (
    PNG, _Session, _outline, _published, _request, _storage,
)
from tests.unit.test_svg_review_read_retries import _harness, _provider
from utils.hashing import sha256_hex

# Indices and digests only, derived from the LIVE-4 R30 deck whose publication raised IndexError.
LIVE4_FIXTURE = json.loads((Path(__file__).parents[1] / "fixtures" / "svg_live4_r30_review_coverage.json")
                           .read_text(encoding="utf-8"))


def _export_binding(presentation, session, svg):
    """Mirror svg_source_review_context plus the export's identity binding."""
    publication = svg["qualityRecoveryPublication"]
    parent = session.get.return_value
    source_context = dict(snapshot=svg["styleSnapshot"], publication=publication,
        producer=dict(operationId=str(parent.id), presentationId=str(parent.presentation_id),
            ownerId=str(parent.owner_id), ownerScope=str(parent.owner_scope),
            sourceRevision=publication["sourceGenerationRevision"], state=parent.state, kind=parent.kind))
    identity = dict(presentationId=str(presentation.id), ownerId=str(presentation.owner_id),
        ownerScope=str(parent.owner_scope), sourceRevision=publication["sourceRevision"])
    return {**identity, "_generationSourceReview": source_context}


def subset_review_deck(monkeypatch, count, invalid, axis_review=True):
    """The real producer's review of ``count`` pages whose ``invalid`` pages lack mandatory evidence."""
    harness = _fresh(monkeypatch, count) if axis_review else _harness(monkeypatch, count)
    original_context = quality._design_context
    pngs = [PNG + str(index).encode() for index in range(count)]
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
        result["mandatoryEvidence"] = [index not in invalid for index in range(count)]
        return result

    monkeypatch.setattr(quality, "_design_context", context)
    by_digest = {sha256_hex(png): png for png in pngs}
    advisory = asyncio.run(quality.deck_advisory(harness.ctx, harness.pages, job=harness.job,
        read_png=lambda payload: by_digest[payload["png_sha256"]], run_advisory=_provider(harness)))
    return harness, advisory, pngs


@pytest.mark.parametrize(("count", "invalid", "axis_review"),
                         [(5, [1, 4], True), (5, [1], True), (5, [], True), (5, [], False),
                          (LIVE4_FIXTURE["pageCount"], LIVE4_FIXTURE["unreviewedPages"], True)])
def test_subset_publication_and_export_keep_original_page_identity(monkeypatch, count, invalid, axis_review):
    harness, advisory, pngs = subset_review_deck(monkeypatch, count, invalid, axis_review)
    expected = [index for index in range(count) if index not in invalid]
    gate = advisory["vision"]["designGate"]
    assert gate["unreviewedPages"] == invalid
    assert gate["status"] == ("unreviewed" if invalid else "passed")
    assert quality.quality_recovery_decisions(harness.ctx, harness.pages, advisory) == {}

    _storage(monkeypatch)
    harness.job.attempt_token = "attempt"
    presentation = PresentationModel(id=harness.job.presentation_id, owner_id=harness.owner,
        content="deck", n_slides=count, mode="svg", revision=0)
    parent = SimpleNamespace(id=harness.job.workspace_operation_id, owner_id=harness.owner,
        presentation_id=harness.job.presentation_id, owner_scope=harness.owner,
        state="succeeded", kind="presentation.generate", result_revision=1)
    session = _Session(presentation, parent)
    asyncio.run(publish._publish(_request(), harness.job.presentation_id, _outline(count), "ko", session,
        harness.job, harness.snapshot, ["content"] * count, harness.pages,
        quality_summary=quality.reclassify_pages(harness.pages), advisory=advisory))
    session.commit.assert_awaited_once()
    svg, slides = _published(session)
    assert len(slides) == count
    assert advisory["vision"]["promptVersion"] == (
        AXIS_REVIEW_PROMPT_VERSION if axis_review else DESIGN_REVIEW_PROMPT_VERSION)
    assert svg["reviewPages"] == invalid
    assert svg["designReview"].get("unreviewedPages", []) == invalid
    if invalid:
        assert svg["advisory"]["vision"]["designReview"]["deckFlow"]["evaluated"] is False
    assert svg["advisory"]["operationBinding"]["reviewedPageIndices"] == expected
    records = {record["index"]: record for record in svg["pageAdvisories"]}
    assert set(records) == set(range(count))
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
    exported = svg_visual_review(slides=slides, advisory=svg["advisory"],
        expected_binding=_export_binding(presentation, session, svg))
    if invalid:
        assert exported == dict(required=True, status="failed",
            reasonCode="svg_visual_advisory_review_coverage_incomplete")
        receipt = svg_public_quality_receipt(slides=slides, advisory=svg["advisory"],
            source_preservation=None, worker_metrics={}, review_binding=_export_binding(presentation, session, svg),
            exporter_version="test-exporter/v1", quality_receipt_id="quality-replay",
            operation_id="operation-replay", presentation_id=str(presentation.id),
            artifact_digest="sha256:" + "a" * 64, source_revision=1,
            scene_digest="sha256:" + "b" * 64, evaluated_at=datetime.now(timezone.utc))
        assert receipt["visualReview"]["status"] == exported["status"]
        assert receipt["visualReview"]["reasonCode"] == exported["reasonCode"]
        assert receipt["releaseReadiness"] == "blocked"
    else:
        assert exported["status"] == "passed" and "reasonCode" not in exported
        assert release_readiness([exported]) == "ready"
