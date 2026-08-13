"""Offline 10-slide published-master generation lifecycle contract.

This test intentionally depends only on the in-memory repository and deterministic
core bridge.  It is the executable acceptance contract for generation, one-slide
body regeneration, CAS conflict, rollback, resume, and stale-master fail-closed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from models.authored_vnext.deck import (
    GenerateDeckRequest,
    MasterDigestPin,
    RegenerateBodyCommand,
    ResumeGenerationRequest,
    RollbackDeckRequest,
)
from models.authored_vnext.master.contract import DeckMasterContract
from models.authored_vnext.master.operations import MasterRevisionResponse
from services.authored_vnext.generation.core_bridge import DeterministicCoreBridge
from services.authored_vnext.generation.errors import (
    GenerationConflictError,
    StaleMasterError,
)
from services.authored_vnext.generation.outline import (
    OutlineItem as PlanningOutlineItem,
)
from services.authored_vnext.generation.outline import plan_outline
from services.authored_vnext.generation.pipeline import (
    AuthoredVNextGenerationPipeline,
)
from services.authored_vnext.generation.repository import (
    InMemoryGenerationRepository,
)
from services.authored_vnext.master.digests import (
    canonical_digest,
    seal_master_contract,
)
from tests.support.authored_vnext_master_fixture import master_contract_payload


FIXTURE_PATH = (
    Path(__file__).resolve().parents[4]
    / "docs"
    / "experiments"
    / "authored-vnext"
    / "06-master-aware-e2e"
    / "fixture-10-slides.json"
)
FIXED_NOW = "2026-08-13T09:00:00Z"


def _published_master() -> MasterRevisionResponse:
    contract = seal_master_contract(
        DeckMasterContract.model_validate(master_contract_payload())
    )
    assert contract.digest is not None
    return MasterRevisionResponse(
        master_id=contract.master_id,
        revision=2,
        digest=contract.digest,
        status="published",
        contract=contract,
    )


def _outline_payload() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _master_pin(master: MasterRevisionResponse) -> MasterDigestPin:
    contract = master.contract
    assert contract.font_manifest.digest is not None
    return MasterDigestPin(
        master_id=master.master_id,
        master_revision=master.revision,
        master_digest=master.digest,
        layout_registry_digest=canonical_digest(
            contract.layouts.model_dump(mode="json", by_alias=True)
        ),
        font_manifest_digest=contract.font_manifest.digest,
    )


def _request(
    master: MasterRevisionResponse,
    *,
    deck_id: str = "deck:master-aware-e2e",
    operation_id: str = "operation:generate-master-aware-e2e",
) -> GenerateDeckRequest:
    return GenerateDeckRequest(
        operation_id=operation_id,
        deck_id=deck_id,
        master=_master_pin(master),
        outline=_outline_payload(),
        quality_policy_version="authored-vnext-quality/v1",
        publication_fence={
            "expectedDeckRevision": 0,
            "expectedActiveRevisionDigest": None,
        },
    )


def _pipeline(master: MasterRevisionResponse):
    repository = InMemoryGenerationRepository()
    repository.save_published_master(master)
    bridge = DeterministicCoreBridge()
    return AuthoredVNextGenerationPipeline(repository, bridge), repository, bridge


def _assert_pinned_deck(result, request: GenerateDeckRequest) -> None:
    assert len(result.deck.slides) == 10
    assert result.deck.master == request.master
    assert result.deck.source_digest == request.outline.source_digest
    assert result.deck.digest == result.quality.deck_digest
    assert result.deck.digest == result.montage.deck_digest
    for validated in result.deck.slides:
        slide = validated.slide
        checkpoint = validated.checkpoint
        assert slide.master_digest == request.master.master_digest
        assert slide.title_fit.font_manifest_digest == request.master.font_manifest_digest
        assert slide.title_fit.clipped is False
        assert checkpoint.master_digest == request.master.master_digest
        assert checkpoint.layout_registry_digest == request.master.layout_registry_digest
        assert checkpoint.font_manifest_digest == request.master.font_manifest_digest
        assert checkpoint.source_digest == request.outline.source_digest


def test_offline_10_slide_generate_regenerate_conflict_and_rollback(monkeypatch) -> None:
    monkeypatch.setattr(
        "services.authored_vnext.generation.pipeline._now",
        lambda: FIXED_NOW,
    )
    master = _published_master()
    pipeline, repository, bridge = _pipeline(master)
    request = _request(master)

    # The only model-visible surface is one writable body slot plus body text.
    fixture = _outline_payload()
    planning_items = [
        PlanningOutlineItem(
            slide_id=f"slide:{item['itemId'].removeprefix('outline:')}",
            title=item["title"],
            body=item["summary"],
            kind="section" if item.get("roleHint") == "section" else "content",
        )
        for item in fixture["items"]
    ]
    plan = plan_outline(master, planning_items)
    assert len(plan.slides) == 10
    for planned in plan.slides:
        model_payload = planned.model_input.to_model_payload()
        assert set(model_payload) == {"bodySlot", "body"}
        assert set(model_payload["bodySlot"]) == {"id", "role", "bounds", "style"}
        serialized = json.dumps(model_payload, ensure_ascii=False).casefold()
        assert all(
            forbidden not in serialized
            for forbidden in ("logo", "header", "footer", "divider", "pagenumber")
        )

    generated = pipeline.generate(request)

    repeated_pipeline, _, repeated_bridge = _pipeline(master)
    repeated = repeated_pipeline.generate(request)
    assert repeated.model_dump(mode="json", by_alias=True) == generated.model_dump(
        mode="json", by_alias=True
    )
    assert repeated_bridge.calls == bridge.calls

    assert generated.operation_id == request.operation_id
    assert generated.checkpoint.stage == "published"
    assert generated.checkpoint.total_slides == 10
    assert len(generated.checkpoint.completed_slides) == 10
    assert [assignment.role for assignment in generated.outline_assignments.assignments[:2]] == [
        "cover",
        "section",
    ]
    assert [assignment.layout_id for assignment in generated.outline_assignments.assignments[:2]] == [
        master.contract.layouts.cover.id,
        master.contract.layouts.section.id,
    ]
    promoted = generated.outline_assignments.assignments[5]
    assert promoted.role == "longTitle"
    assert promoted.title_promoted is True
    assert promoted.layout_id == master.contract.layouts.long_title.id
    _assert_pinned_deck(generated, request)

    assert generated.quality.passed is True
    assert generated.quality.issues == []
    assert len(generated.quality.slide_gates) == 10
    assert all(gate.passed for gate in generated.quality.slide_gates)
    assert sum(gate.furniture_body_duplicates for gate in generated.quality.slide_gates) == 0
    consistency_checks = (
        generated.quality.fonts,
        generated.quality.colors,
        generated.quality.frame,
        generated.quality.page_numbers,
        generated.quality.layouts,
        generated.quality.slots,
        generated.quality.sources,
    )
    assert all(check.passed for check in consistency_checks)

    assert generated.montage.deck_size == 10
    assert [tile.slide_index for tile in generated.montage.tiles] == list(range(10))
    assert [tile.slide_id for tile in generated.montage.tiles] == [
        validated.slide.slide_id for validated in generated.deck.slides
    ]
    assert len(bridge.calls) == 1
    assert bridge.calls[0]["checkpoint"] == {
        "masterDigest": request.master.master_digest,
        "layoutRegistryDigest": request.master.layout_registry_digest,
        "fontManifestDigest": request.master.font_manifest_digest,
        "sourceDigest": request.outline.source_digest,
    }

    target = generated.deck.slides[6]
    regenerate = RegenerateBodyCommand(
        operation_id="operation:regenerate-slide-07",
        deck_id=generated.deck.deck_id,
        slide_id=target.slide.slide_id,
        expected_slide_revision=target.slide.revision,
        expected_slide_spec_digest=target.slide.spec_digest,
        master=request.master,
        source_digest=request.outline.source_digest,
        body="재생성된 본문은 master furniture를 만들지 않고 본문 슬롯만 갱신합니다.",
        publication_fence={
            "expectedDeckRevision": generated.deck.revision,
            "expectedActiveRevisionDigest": generated.deck.digest,
        },
    )
    first_candidate = pipeline.prepare_regeneration(regenerate)
    repeated_candidate = pipeline.prepare_regeneration(
        regenerate.model_copy(
            update={"operation_id": "operation:regenerate-slide-07-retry"}
        )
    )
    assert repeated_candidate == first_candidate
    regenerated = pipeline.publish_regeneration(
        first_candidate.candidate_digest,
        publication_operation_id=regenerate.operation_id,
    )

    assert regenerated.candidate.lifecycle == "detached"
    assert regenerated.candidate.parent_deck_digest == generated.deck.digest
    assert regenerated.candidate.slide_gate.passed is True
    assert regenerated.candidate.slide_gate.dom_passed is True
    assert regenerated.candidate.slide_gate.ocr_passed is True
    assert regenerated.candidate.slide_gate.title_fit_passed is True
    assert regenerated.candidate.slide_gate.furniture_body_duplicates == 0
    assert regenerated.published_by_cas is True
    assert regenerated.deck.revision == 2
    assert regenerated.deck.parent_revision_digest == generated.deck.digest
    changed = regenerated.deck.slides[6]
    assert changed.slide.revision == target.slide.revision + 1
    assert changed.slide.body == regenerate.body
    assert changed.slide.title == target.slide.title
    assert changed.slide.layout_id == target.slide.layout_id
    assert changed.checkpoint.master_digest == request.master.master_digest
    assert all(
        before.slide.spec_digest == after.slide.spec_digest
        for index, (before, after) in enumerate(
            zip(generated.deck.slides, regenerated.deck.slides, strict=True)
        )
        if index != 6
    )
    assert len(bridge.calls) == 2

    # Replaying the stale fence must not export or append another revision.
    with pytest.raises(GenerationConflictError):
        pipeline.regenerate_body(regenerate)
    assert len(bridge.calls) == 2
    assert repository.get_deck(generated.deck.deck_id).digest == regenerated.deck.digest

    rolled_back = pipeline.rollback(
        RollbackDeckRequest(
            operation_id="operation:rollback-to-generated",
            deck_id=generated.deck.deck_id,
            target_revision=generated.deck.revision,
            target_revision_digest=generated.deck.digest,
            master_digest=request.master.master_digest,
            publication_fence={
                "expectedDeckRevision": regenerated.deck.revision,
                "expectedActiveRevisionDigest": regenerated.deck.digest,
            },
            reason="regenerated body was rejected by editorial review",
        )
    )
    assert rolled_back.restored_from_revision == 1
    assert rolled_back.deck.revision == 3
    assert rolled_back.deck.parent_revision_digest == regenerated.deck.digest
    assert rolled_back.deck.slides[6].slide.body == target.slide.body
    assert rolled_back.deck.master.master_digest == request.master.master_digest
    assert repository.get_deck(generated.deck.deck_id, revision=1).digest == generated.deck.digest
    assert repository.get_deck(generated.deck.deck_id, revision=2).digest == regenerated.deck.digest
    assert repository.get_deck(generated.deck.deck_id).digest == rolled_back.deck.digest


def test_resume_keeps_master_digest_and_stale_master_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        "services.authored_vnext.generation.pipeline._now",
        lambda: FIXED_NOW,
    )
    master = _published_master()
    pipeline, repository, bridge = _pipeline(master)
    request = _request(
        master,
        deck_id="deck:resume-master-aware-e2e",
        operation_id="operation:resume-master-aware-e2e",
    )
    generated = pipeline.generate(request)
    calls_after_generate = len(bridge.calls)
    resume = ResumeGenerationRequest(
        operation_id=request.operation_id,
        expected_checkpoint_digest=generated.checkpoint.checkpoint_digest,
        expected_master_digest=request.master.master_digest,
        checkpoint=generated.checkpoint,
    )

    resumed = pipeline.resume(resume)
    assert resumed == generated
    assert resumed.checkpoint.master == request.master
    assert all(
        slide.checkpoint.master_digest == request.master.master_digest
        for slide in resumed.deck.slides
    )
    assert len(bridge.calls) == calls_after_generate

    # A newly published head makes the old checkpoint stale even if its revision
    # remains readable. Resume must stop before core export or deck publication.
    repository.save_published_master(master.model_copy(update={"revision": 3}))
    head_before = repository.get_deck(request.deck_id)
    with pytest.raises(StaleMasterError):
        pipeline.resume(resume)
    assert len(bridge.calls) == calls_after_generate
    assert repository.get_deck(request.deck_id).digest == head_before.digest
