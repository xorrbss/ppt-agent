from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import os
from threading import Barrier
import uuid

import pytest

from services.authored_vnext.generation.scene_checkpoint import (
    SceneRepairBudgetExhausted,
    claim_scene_repair,
    persist_repaired_scene_envelope,
    persist_scene_envelope,
    read_scene_checkpoint_state,
)
from services.authored_vnext.generation.scene_envelope import (
    AuthoredSlideSceneEnvelope,
    replace_deck_scene_slide,
    seal_deck_scene_envelope,
    seal_slide_scene_envelope,
)


def _html(label: str) -> str:
    return (
        '<main data-qid="slide.root" data-semantic-role="decoration">'
        f'<h1 data-qid="title" data-semantic-role="title">{label}</h1>'
        '<p data-qid="body" data-semantic-role="body">Body</p></main>'
    )


def _claim_in_process(
    checkpoint_directory: str,
    job_id: str,
    envelope_payload: dict[str, object],
    ready,
    start,
    results,
) -> None:
    os.environ["AUTHORED_CHECKPOINT_DIRECTORY"] = checkpoint_directory
    envelope = AuthoredSlideSceneEnvelope.from_payload(envelope_payload)
    ready.put(True)
    start.wait()
    try:
        claim_scene_repair(
            uuid.UUID(job_id),
            envelope,
            error_code="authored_composition_safety_gate_failed",
            issues=({"message": "browser_body:299>270"},),
        )
        results.put("claimed")
    except SceneRepairBudgetExhausted:
        results.put("exhausted")


def test_scene_envelope_reuses_normal_slide_and_persists_one_repair_budget(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("AUTHORED_CHECKPOINT_DIRECTORY", str(tmp_path))
    first = seal_slide_scene_envelope(_html("One"), slide_index=0)
    second = seal_slide_scene_envelope(_html("Two"), slide_index=1)
    deck = seal_deck_scene_envelope((first, second))

    repaired = replace_deck_scene_slide(
        deck,
        slide_index=1,
        html_projection=_html("Two repaired"),
    )
    assert repaired.slides[0] is deck.slides[0]
    assert repaired.slides[0].scene_digest == deck.slides[0].scene_digest
    assert repaired.slides[1].revision == 2
    assert repaired.scene_digest != deck.scene_digest

    job_id = uuid.uuid4()
    persisted = persist_scene_envelope(job_id, second)
    issues = ({"message": "browser_body:299>270"},)
    claimed = claim_scene_repair(
        job_id,
        second,
        error_code="authored_composition_safety_gate_failed",
        issues=issues,
    )
    with pytest.raises(SceneRepairBudgetExhausted) as repeated:
        claim_scene_repair(
            job_id,
            second,
            error_code="authored_composition_safety_gate_failed",
            issues=issues,
        )
    assert repeated.value.repeated is True

    persist_repaired_scene_envelope(job_id, repaired.slides[1], claimed)
    recovered = read_scene_checkpoint_state(job_id, 1)
    assert persisted.repair_count == 0
    assert recovered is not None
    assert recovered.repair_count == 1
    assert recovered.envelope.scene_digest == repaired.slides[1].scene_digest

    vqa_revised = replace_deck_scene_slide(
        repaired,
        slide_index=1,
        html_projection=_html("Two VQA revised"),
    )
    persisted_vqa = persist_scene_envelope(job_id, vqa_revised.slides[1])
    assert persisted_vqa.repair_count == 1
    assert persisted_vqa.failure_fingerprint == claimed.failure_fingerprint
    with pytest.raises(SceneRepairBudgetExhausted) as changed_digest:
        claim_scene_repair(
            job_id,
            vqa_revised.slides[1],
            error_code="authored_composition_safety_gate_failed",
            issues=({"message": "browser_body:310>270"},),
        )
    assert changed_digest.value.repeated is False


def test_scene_repair_claim_has_one_thread_winner(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AUTHORED_CHECKPOINT_DIRECTORY", str(tmp_path))
    job_id = uuid.uuid4()
    envelope = seal_slide_scene_envelope(_html("Thread race"), slide_index=0)
    persist_scene_envelope(job_id, envelope)
    barrier = Barrier(8)

    def claim() -> str:
        barrier.wait()
        try:
            claim_scene_repair(
                job_id,
                envelope,
                error_code="authored_composition_safety_gate_failed",
                issues=({"message": "browser_body:299>270"},),
            )
            return "claimed"
        except SceneRepairBudgetExhausted:
            return "exhausted"

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(lambda _: claim(), range(8)))
    assert outcomes.count("claimed") == 1
    assert outcomes.count("exhausted") == 7


def test_scene_repair_claim_has_one_process_winner(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AUTHORED_CHECKPOINT_DIRECTORY", str(tmp_path))
    job_id = uuid.uuid4()
    envelope = seal_slide_scene_envelope(_html("Process race"), slide_index=0)
    persist_scene_envelope(job_id, envelope)
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_claim_in_process,
            args=(
                str(tmp_path),
                str(job_id),
                envelope.to_payload(),
                ready,
                start,
                results,
            ),
        )
        for _ in range(4)
    ]
    try:
        for process in processes:
            process.start()
        for _ in processes:
            assert ready.get(timeout=15) is True
        start.set()
        for process in processes:
            process.join(timeout=15)
            assert process.exitcode == 0
        outcomes = [results.get(timeout=5) for _ in processes]
        assert outcomes.count("claimed") == 1
        assert outcomes.count("exhausted") == 3
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        ready.close()
        results.close()
