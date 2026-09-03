"""Re-authoring a slide re-seals its scene envelope instead of ending the deck.

A source-binding rejection is a slide-scoped failure, so the next attempt
authors that slide again and its HTML legitimately differs from the document
the previous attempt sealed. Operation b5d5f33a authored, rendered and
persisted 35 of 35 slides and then died on
``authored_scene_checkpoint_projection_mismatch`` for slide 28, the one slide
it had re-authored.

Re-sealing is safe because ``persist_scene_envelope`` carries the slide's
repair count and failure fingerprint onto the new envelope, so a re-authored
slide cannot buy itself another repair.
"""

from __future__ import annotations

import uuid

import pytest

from services.authored_vnext.generation.scene_checkpoint import (
    SceneRepairBudgetExhausted,
    claim_scene_repair,
    persist_scene_envelope,
    read_scene_checkpoint_state,
)
from services.authored_vnext.generation.scene_envelope import (
    seal_slide_scene_envelope,
)


SLIDE = (
    "<html><head></head><body>"
    '<h1 data-qid="title" data-semantic-role="title">제목</h1>'
    '<p data-qid="body" data-semantic-role="body">{body}</p>'
    "</body></html>"
)


@pytest.fixture(autouse=True)
def _checkpoint_root(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTHORED_CHECKPOINT_DIRECTORY", str(tmp_path / "scenes"))


def test_resealing_replaces_the_envelope_and_keeps_the_repair_budget() -> None:
    job_id = uuid.uuid4()
    first = seal_slide_scene_envelope(SLIDE.format(body="첫 번째"), slide_index=27)
    persist_scene_envelope(job_id, first)
    claim_scene_repair(
        job_id, first, error_code="authored_composition_safety_gate_failed", issues=()
    )

    reauthored = seal_slide_scene_envelope(SLIDE.format(body="다시 쓴 본문"), slide_index=27)
    assert reauthored.scene_digest != first.scene_digest
    persist_scene_envelope(job_id, reauthored)

    state = read_scene_checkpoint_state(job_id, 27)
    assert state is not None
    assert state.envelope.scene_digest == reauthored.scene_digest
    assert state.envelope.html_projection == reauthored.html_projection
    # The slide already spent its one repair, and re-authoring does not
    # hand it another one.
    assert state.repair_count == 1
    with pytest.raises(SceneRepairBudgetExhausted):
        claim_scene_repair(
            job_id,
            reauthored,
            error_code="authored_composition_safety_gate_failed",
            issues=(),
        )


def test_an_unchanged_slide_keeps_its_sealed_envelope() -> None:
    job_id = uuid.uuid4()
    envelope = seal_slide_scene_envelope(SLIDE.format(body="그대로"), slide_index=3)
    persist_scene_envelope(job_id, envelope)
    again = seal_slide_scene_envelope(SLIDE.format(body="그대로"), slide_index=3)
    assert again.scene_digest == envelope.scene_digest
    persist_scene_envelope(job_id, again)
    state = read_scene_checkpoint_state(job_id, 3)
    assert state is not None and state.repair_count == 0
