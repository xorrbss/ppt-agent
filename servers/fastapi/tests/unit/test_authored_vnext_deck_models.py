"""Focused contract tests for authored vNext deck generation models."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from models.authored_vnext.deck import (
    ConsistencyCheck,
    DeckGenerationSnapshot,
    DeckQualityReport,
    GenerationCheckpoint,
    MasterDigestPin,
    MontageManifest,
    OutlineAssignmentSet,
    OutlineInput,
    RegenerateBodyCommand,
    ResumeGenerationRequest,
    SlideGateReport,
    SlideValidationCheckpoint,
    SlideVisualPlan,
    ValidatedSlideSpecV2,
)
from models.authored_vnext.master.contract import Ownership, PowerPointMargins
from models.authored_vnext.master.slide import ExplicitTitle, SlideSpecV2, TitleFitResult


D0 = "sha256:" + "0" * 64
D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64
D3 = "sha256:" + "3" * 64
NOW = "2026-08-13T00:00:00Z"


def _master() -> MasterDigestPin:
    return MasterDigestPin(
        master_id="master:test",
        master_revision=3,
        master_digest=D0,
        layout_registry_digest=D1,
        font_manifest_digest=D2,
    )


def _slide() -> ValidatedSlideSpecV2:
    title = ExplicitTitle(text="검증된 제목", lines=["검증된 제목"])
    fit = TitleFitResult(
        layout_id="layout:standard",
        explicit_lines=["검증된 제목"],
        font_family="Malgun Gothic",
        font_manifest_digest=D2,
        chosen_font_pt=38.0,
        margins=PowerPointMargins(),
        clipped=False,
    )
    slide = SlideSpecV2(
        slide_id="slide:one",
        revision=1,
        layout_id="layout:standard",
        title=title,
        body="body slot only",
        ownership=Ownership(locked=False, model_writable=True),
        master_digest=D0,
        title_fit=fit,
        content_digest=D1,
        spec_digest=D3,
    )
    checkpoint = SlideValidationCheckpoint(
        master_digest=D0,
        layout_registry_digest=D1,
        font_manifest_digest=D2,
        source_digest=D3,
        visual_plan_digest=D1,
        quality_policy_version="local-fixture/v1",
        layout_id="layout:standard",
        body_slot_id="slot:standard-body",
        validated_at=NOW,
    )
    return ValidatedSlideSpecV2(slide=slide, checkpoint=checkpoint)


def _checkpoint() -> GenerationCheckpoint:
    return GenerationCheckpoint(
        operation_id="operation:test",
        deck_id="deck:test",
        stage="slidesCompiling",
        master=_master(),
        source_digest=D3,
        quality_policy_version="local-fixture/v1",
        total_slides=10,
        checkpoint_digest=D1,
        created_at=NOW,
    )


def test_outline_and_assignment_contracts_are_ordered_and_cover_first() -> None:
    outline = OutlineInput.model_validate(
        {
            "sourceDigest": D3,
            "deckTitle": "10장 fixture",
            "items": [
                {
                    "itemId": f"outline:{index}",
                    "index": index,
                    "title": f"제목 {index}",
                    "summary": f"본문 {index}",
                }
                for index in range(10)
            ],
        }
    )
    assignments = OutlineAssignmentSet.model_validate(
        {
            "sourceDigest": D3,
            "masterDigest": D0,
            "layoutRegistryDigest": D1,
            "assignments": [
                {
                    "itemId": f"outline:{index}",
                    "index": index,
                    "role": "cover" if index == 0 else "standard",
                    "layoutId": "layout:standard",
                    "reason": "first-slide-cover" if index == 0 else "standard-content",
                }
                for index in range(10)
            ],
        }
    )
    assert len(outline.items) == len(assignments.assignments) == 10

    bad = assignments.model_dump(mode="json", by_alias=True)
    bad["assignments"][1]["role"] = "cover"
    with pytest.raises(ValidationError, match="exactly the first"):
        OutlineAssignmentSet.model_validate_json(json.dumps(bad))


def test_visual_plan_is_structurally_body_only_and_forbids_furniture() -> None:
    payload = {
        "slideId": "slide:one",
        "outlineItemId": "outline:0",
        "role": "standard",
        "layoutId": "layout:standard",
        "bodySlotId": "slot:standard-body",
        "sourceDigest": D3,
        "elements": [
            {
                "kind": "text",
                "elementId": "body:message",
                "bounds": {"x": 100.0, "y": 250.0, "width": 900.0, "height": 200.0},
                "semanticRole": "body",
                "text": "본문만 생성",
            }
        ],
        "planDigest": D1,
    }
    plan = SlideVisualPlan.model_validate(payload)
    assert plan.scope == "bodyOnly"
    assert plan.elements[0].element_id.startswith("body:")

    payload["furniture"] = {"pageNumber": "10"}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SlideVisualPlan.model_validate_json(json.dumps(payload))


def test_validated_slide_seals_master_layout_font_and_source_checkpoint() -> None:
    validated = _slide()
    assert validated.slide.master_digest == validated.checkpoint.master_digest
    assert validated.slide.title_fit.font_manifest_digest == D2

    stale = validated.model_dump(mode="json", by_alias=True)
    stale["checkpoint"]["fontManifestDigest"] = D3
    with pytest.raises(ValidationError, match="font manifest digests differ"):
        ValidatedSlideSpecV2.model_validate_json(json.dumps(stale))

    clipped = validated.model_dump(mode="json", by_alias=True)
    clipped["slide"]["titleFit"]["clipped"] = True
    with pytest.raises(ValidationError, match="cannot be clipped"):
        ValidatedSlideSpecV2.model_validate_json(json.dumps(clipped))


def test_resume_fails_closed_for_stale_master_or_checkpoint() -> None:
    checkpoint = _checkpoint()
    request = ResumeGenerationRequest(
        operation_id=checkpoint.operation_id,
        expected_checkpoint_digest=checkpoint.checkpoint_digest,
        expected_master_digest=checkpoint.master.master_digest,
        checkpoint=checkpoint,
    )
    assert request.checkpoint.master.master_digest == D0

    payload = request.model_dump(mode="json", by_alias=True)
    payload["expectedMasterDigest"] = D3
    with pytest.raises(ValidationError, match="stale published master"):
        ResumeGenerationRequest.model_validate_json(json.dumps(payload))
    payload = request.model_dump(mode="json", by_alias=True)
    payload["expectedCheckpointDigest"] = D3
    with pytest.raises(ValidationError, match="stale generation checkpoint"):
        ResumeGenerationRequest.model_validate_json(json.dumps(payload))


def test_regeneration_command_requires_body_and_cas_fence() -> None:
    command = RegenerateBodyCommand(
        operation_id="operation:regen",
        deck_id="deck:test",
        slide_id="slide:one",
        expected_slide_revision=1,
        expected_slide_spec_digest=D3,
        master=_master(),
        source_digest=D3,
        body="새 body slot content",
        publication_fence={
            "expectedDeckRevision": 1,
            "expectedActiveRevisionDigest": D1,
        },
    )
    assert command.scope == "bodyOnly"
    missing_body = command.model_dump(mode="json", by_alias=True)
    del missing_body["body"]
    with pytest.raises(ValidationError, match="Field required"):
        RegenerateBodyCommand.model_validate_json(json.dumps(missing_body))


def test_deck_gate_and_montage_verdicts_are_derived() -> None:
    check = ConsistencyCheck(passed=True)
    slide_gate = SlideGateReport(
        slide_id="slide:one",
        candidate_digest=D1,
        dom_passed=True,
        ocr_passed=True,
        title_fit_passed=True,
        furniture_body_duplicates=0,
        passed=True,
    )
    report = DeckQualityReport(
        deck_id="deck:test",
        deck_digest=D3,
        master_digest=D0,
        fonts=check,
        colors=check,
        frame=check,
        page_numbers=check,
        layouts=check,
        slots=check,
        sources=check,
        slide_gates=[slide_gate],
        passed=True,
    )
    assert report.passed

    with pytest.raises(ValidationError, match="deck gate passed"):
        DeckQualityReport.model_validate_json(
            json.dumps(
                {**report.model_dump(mode="json", by_alias=True), "passed": False}
            )
        )

    montage = MontageManifest(
        deck_id="deck:test",
        deck_digest=D3,
        deck_size=10,
        columns=5,
        tiles=[
            {"slideId": f"slide:{index}", "slideIndex": index, "pngDigest": D2}
            for index in range(10)
        ],
        montage_digest=D1,
    )
    assert len(montage.tiles) == 10
    invalid = montage.model_dump(mode="json", by_alias=True)
    invalid["tiles"].pop()
    with pytest.raises(ValidationError, match="contain every slide"):
        MontageManifest.model_validate_json(json.dumps(invalid))


def test_montage_accepts_counts_up_to_the_template_default() -> None:
    payload = {
        "deckId": "deck:shorter-than-default",
        "deckDigest": D3,
        "deckSize": 8,
        "columns": 5,
        "tiles": [
            {"slideId": f"slide:{index}", "slideIndex": index, "pngDigest": D2}
            for index in range(8)
        ],
        "montageDigest": D1,
    }

    assert MontageManifest.model_validate(payload).deck_size == 8

    unsupported = {
        **payload,
        "deckSize": 11,
        "tiles": [
            {"slideId": f"slide:{index}", "slideIndex": index, "pngDigest": D2}
            for index in range(11)
        ],
    }
    with pytest.raises(ValidationError, match="between 1 and 10, or exactly 90"):
        MontageManifest.model_validate(unsupported)


def test_deck_snapshot_rejects_mixed_source_or_master_digests() -> None:
    snapshot = DeckGenerationSnapshot(
        deck_id="deck:test",
        revision=1,
        digest=D3,
        master=_master(),
        source_digest=D3,
        slides=[_slide()],
        created_at=NOW,
    )
    assert snapshot.slides[0].checkpoint.source_digest == snapshot.source_digest
    stale = snapshot.model_dump(mode="json", by_alias=True)
    stale["sourceDigest"] = D2
    with pytest.raises(ValidationError, match="different source digest"):
        DeckGenerationSnapshot.model_validate_json(json.dumps(stale))


def test_generated_contracts_are_current_and_compact() -> None:
    repository_root = Path(__file__).resolve().parents[4]
    generator_path = repository_root / "contracts/authored_vnext/deck/generate.py"
    spec = importlib.util.spec_from_file_location("authored_vnext_deck_generate", generator_path)
    assert spec is not None and spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    assert generator.generate(check=True, repository_root=repository_root) == 0
    outputs = generator.render_outputs(repository_root)
    assert len(outputs) == 13
    assert all(content.count(b"\n") == 1 for content in outputs.values())
