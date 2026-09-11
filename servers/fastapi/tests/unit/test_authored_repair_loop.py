"""One bounded repair loop: order, the N bound, rejection, knob, evidence.

Design ``docs/authored-verbatim-placeholder-authoring-design-20260911.md`` 2.5.
Everything here is offline: no provider call, no browser, no database.
"""

from __future__ import annotations

import asyncio
from functools import wraps
from types import SimpleNamespace

import pytest

from services import authored_admission as gate
from services.authored_admission_context import context_from_source
from services.authored_repair_loop import (
    RepairLoopDeps,
    RepairLoopSlide,
    loop_slides,
    run_repair_loop,
    single_repair_loop_enabled,
)
from services.authored_repair_loop_evidence import (
    TOKEN_TEXT_RULE,
    VERBATIM_TEXT_RULE,
    merge_evidence,
    repair_correction_hint,
    visual_review_evidence_only,
)
from services.authored_repair_loop_model import build_repair_author
from services.authored_structural_repair import (
    RepairAssessment,
    _digest,
    _json_digest,
)
from utils.get_env_programme import (
    get_authored_verbatim_repair_loop_model_calls,
    is_authored_verbatim_single_repair_loop_enabled,
)
from utils.runtime_settings import RuntimeSettings

ORIGINAL = "<!doctype html><html><body><h1>원문 제목</h1></body></html>"
DETERMINISTIC = "<!doctype html><html><body><h1 style=\"margin:0\">원문 제목</h1></body></html>"
MODEL = "<!doctype html><html><body><h2>원문 제목</h2></body></html>"


def sync_async(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


def metrics_for(html: str) -> dict:
    return {"width": 1280, "height": 720, "source_digest": _digest(html), "elements": []}


def assessment_for(html: str, metrics: dict, decision: str) -> RepairAssessment:
    """A real assessment, so ``bind_repair_candidate`` binds real digests."""

    return RepairAssessment(
        decision=decision,
        reasons=() if decision == "admitted" else ("overflow:body",),
        source_integrity={"verified": decision == "admitted"},
        composition_report={"passed": decision == "admitted"},
        style_report={"passed": decision == "admitted"},
        evidence={
            "gate_version": "test",
            "absolute_slide_index": 0,
            "baseline_html_digest": _digest(ORIGINAL),
            "html_digest": _digest(html),
            "metrics_digest": _json_digest(metrics),
            "source_context_digest": "sha256:source",
        },
    )


class Recorder:
    """A scripted evaluator, capture and proposer with counted calls."""

    def __init__(self, *, admits: frozenset[str] = frozenset(), candidate: str = MODEL):
        self.admits = admits
        self.candidate = candidate
        self.evaluated: list[str] = []
        self.captured: list[str] = []
        self.proposed: list[tuple[int, tuple[str, ...]]] = []

    async def evaluate(self, index: int, html: str, metrics: dict) -> RepairAssessment:
        self.evaluated.append(html)
        decision = "admitted" if html in self.admits else "reject"
        return assessment_for(html, metrics, decision)

    async def capture(self, html: str, index: int):
        self.captured.append(html)
        return html, b"png-" + str(len(self.captured)).encode(), metrics_for(html)

    async def propose(self, index: int, evidence: tuple[str, ...]):
        self.proposed.append((index, evidence))
        # A distinct document per call: an identical repeat is refused by the
        # loop's own duplicate-digest rule, which is not what N bounds.
        return self.candidate.replace("<h2>", f'<h2 data-try="{len(self.proposed)}">')


def slide(**changes) -> RepairLoopSlide:
    values = dict(
        slide_index=0,
        local_index=0,
        html=ORIGINAL,
        png=b"png-original",
        metrics=metrics_for(ORIGINAL),
        deterministic_candidate=DETERMINISTIC,
        provider_allowed=True,
        evidence=("overflow: body overflows the frame",),
    )
    values.update(changes)
    return RepairLoopSlide(**values)


def deps_for(recorder: Recorder, **changes) -> RepairLoopDeps:
    values = dict(
        evaluators={0: recorder.evaluate},
        capture=recorder.capture,
        propose=recorder.propose,
        model_calls=1,
    )
    values.update(changes)
    return RepairLoopDeps(**values)


@sync_async
async def test_deterministic_repairs_run_before_any_model_call():
    recorder = Recorder(admits=frozenset({DETERMINISTIC}))
    [result] = await run_repair_loop([slide()], deps=deps_for(recorder))
    assert recorder.captured == [DETERMINISTIC]
    assert recorder.proposed == []
    assert result.html == DETERMINISTIC
    assert result.final_status == "admitted"
    assert result.cycle_history[0]["path"] == "deterministic"


@sync_async
async def test_the_model_step_runs_only_after_a_deterministic_rejection():
    first = MODEL.replace("<h2>", '<h2 data-try="1">')
    recorder = Recorder(admits=frozenset({first}))
    [result] = await run_repair_loop([slide()], deps=deps_for(recorder))
    assert recorder.captured == [DETERMINISTIC, first]
    assert [entry["path"] for entry in result.cycle_history] == [
        "deterministic",
        "single_repair_loop",
    ]
    assert result.html == first
    assert result.final_status == "admitted"


@pytest.mark.parametrize("budget", [0, 1, 2, 3])
def test_model_calls_are_bounded_by_n(budget):
    recorder = Recorder()

    async def run():
        return await run_repair_loop([slide()], deps=deps_for(recorder, model_calls=budget))

    [result] = asyncio.run(run())
    assert len(recorder.proposed) == budget
    assert result.final_status == "unresolved"


@sync_async
async def test_a_rejected_candidate_leaves_the_frozen_admission_in_place():
    recorder = Recorder()
    [result] = await run_repair_loop([slide()], deps=deps_for(recorder, model_calls=2))
    assert result.html == ORIGINAL
    assert result.png == b"png-original"
    assert result.changed is False
    assert result.final_status == "unresolved"
    assert {entry.get("stop_reason") for entry in result.cycle_history} == {
        "deterministic_rejected",
        "model_candidate_rejected",
    }


@sync_async
async def test_a_rejected_model_candidate_keeps_the_deterministic_selection():
    recorder = Recorder(admits=frozenset())
    recorder.candidate = MODEL

    async def evaluate(index, html, metrics):
        recorder.evaluated.append(html)
        decision = "provisional" if html == DETERMINISTIC else "reject"
        return assessment_for(html, metrics, decision)

    [result] = await run_repair_loop(
        [slide()], deps=deps_for(recorder, evaluators={0: evaluate})
    )
    assert result.html == DETERMINISTIC
    assert result.changed is True
    assert result.final_status == "unresolved"


@sync_async
async def test_a_slide_without_a_provider_claim_spends_no_model_call():
    recorder = Recorder()
    [result] = await run_repair_loop(
        [slide(provider_allowed=False)], deps=deps_for(recorder)
    )
    assert recorder.proposed == []
    assert result.cycle_history[-1]["stop_reason"] == "provider_recovery_blocked"


@sync_async
async def test_a_failing_candidate_does_not_end_the_deck():
    recorder = Recorder()

    async def capture(html, index):
        raise RuntimeError("browser evidence unavailable")

    [result] = await run_repair_loop(
        [slide()], deps=deps_for(recorder, capture=capture)
    )
    assert result.html == ORIGINAL
    assert result.final_status == "unresolved"


def test_loop_slides_carry_the_callers_order_and_frozen_inputs():
    slides = loop_slides(
        flagged=[3, 1],
        htmls=["a", "b", "c", "d"],
        pngs=[b"a", b"b", b"c", b"d"],
        metrics_by_index={1: {"n": 1}, 3: {"n": 3}},
        deterministic_by_index={1: "b1", 3: "d3"},
        provider_allowed=[True, False],
        evidence_by_index={3: ("clip",)},
    )
    assert [(item.slide_index, item.local_index) for item in slides] == [(3, 0), (1, 1)]
    assert slides[0].evidence == ("clip",) and slides[1].evidence == ()
    assert slides[1].provider_allowed is False


def test_the_knob_is_off_by_default_and_owns_the_verbatim_path_only(monkeypatch):
    assert is_authored_verbatim_single_repair_loop_enabled(RuntimeSettings({})) is False
    assert (
        is_authored_verbatim_single_repair_loop_enabled(
            RuntimeSettings({"AUTHORED_VERBATIM_SINGLE_REPAIR_LOOP": True})
        )
        is True
    )
    assert single_repair_loop_enabled(source_reflow=True) is False
    import services.authored_repair_loop as loop

    monkeypatch.setattr(loop, "is_authored_verbatim_single_repair_loop_enabled", lambda: True)
    assert single_repair_loop_enabled(source_reflow=True) is True
    assert single_repair_loop_enabled(source_reflow=False) is False


@pytest.mark.parametrize(
    "value, expected", [(None, 1), ("2", 2), ("0", 0), ("9", 3), ("-4", 0)]
)
def test_the_model_call_budget_defaults_to_one_and_clamps(value, expected):
    settings = RuntimeSettings({"AUTHORED_VERBATIM_REPAIR_LOOP_MODEL_CALLS": value})
    assert get_authored_verbatim_repair_loop_model_calls(settings) == expected


@sync_async
async def test_the_model_step_asks_for_layout_change_and_forbids_source_text():
    captured: dict[str, object] = {}

    async def author(content, design_system, brand, role, index, n, **kwargs):
        captured.update(kwargs, content=content, index=index, n=n, role=role)
        return MODEL

    propose = build_repair_author(
        contents=["c0", "c1"],
        roles=["TITLE", "BODY"],
        brand=SimpleNamespace(),
        design_system="design",
        capacity_briefs=["b0", "b1"],
        page_number_mode="template",
        instructions=None,
        source_images_by_slide=None,
        author=author,
    )
    assert await propose(1, ("overflow: body overflows",)) == (
        await propose(1, ("overflow: body overflows",))
    )
    hint = str(captured["correction_hint"])
    assert "overflow: body overflows" in hint
    assert "MAY move, resize, restyle" in hint
    # No placeholder contract is in scope here, so the brief carries the
    # verbatim boundary rather than instructing the model to place tokens that
    # were never defined for it.
    assert VERBATIM_TEXT_RULE in hint and TOKEN_TEXT_RULE not in hint
    assert captured["receipt_retry_class"] == "recovery"
    assert captured["capacity_brief"] == "b1"


def test_the_model_step_uses_the_one_authoring_prompt():
    """The T5 contract reaches the repair call because it is that prompt."""

    from utils.llm_calls.author_slide import author_slide_html
    from services.authored_repair_loop_model import build_repair_author as builder

    assert builder.__defaults__ is None
    assert (
        builder.__kwdefaults__["author"] is author_slide_html
    ), "the loop must call the one authoring prompt, not a second repair prompt"


def test_the_repair_prompt_carries_the_placeholder_contract(monkeypatch):
    from services.source_content.placeholder import prompt
    from services.source_content.placeholder.block_table import (
        build_block_table,
        placeholder_prompt_contract,
    )
    from utils.llm_calls.author_slide_prompt import author_prompt
    import services.authored_admission_session as session_module

    context = SimpleNamespace(
        source_reflow=True,
        reference_mode="source_content_restyle",
        bindings=(("b1", "원문 제목"),),
    )
    monkeypatch.setattr(
        session_module, "current_session", lambda: SimpleNamespace(contexts=(context,))
    )
    monkeypatch.setattr(
        prompt, "is_authored_verbatim_placeholder_authoring_enabled", lambda: True
    )
    assert "MAY NOT write" in repair_correction_hint(("overflow",), role="BODY", index=0)
    built = author_prompt(
        "content",
        "design",
        SimpleNamespace(wordmark="", topic="topic"),
        "BODY",
        0,
        2,
    )
    assert placeholder_prompt_contract(build_block_table(context.bindings)) in built


@pytest.mark.parametrize("placeholder_on", [False, True])
@pytest.mark.parametrize("loop_on", [False, True])
def test_the_repair_brief_agrees_with_the_authoring_contract(monkeypatch, placeholder_on, loop_on):
    """B3: the two knobs can never produce a self-contradictory repair prompt.

    The repair brief's text boundary is derived from the same source the
    authoring prompt uses, so "place every source occurrence through its
    placeholder token" is emitted exactly when a placeholder block table is
    actually in that prompt -- for all four knob combinations, the loop knob
    included.
    """

    from services.source_content.placeholder import prompt
    from services.source_content.placeholder.prompt import placeholder_prompt_for_slide
    from utils.llm_calls.author_slide_prompt import author_prompt
    import services.authored_admission_session as session_module
    import services.authored_repair_loop as loop_module

    context = SimpleNamespace(
        source_reflow=True,
        reference_mode="source_content_restyle",
        bindings=(("b1", "원문 제목"),),
    )
    monkeypatch.setattr(
        session_module, "current_session", lambda: SimpleNamespace(contexts=(context,))
    )
    monkeypatch.setattr(
        prompt, "is_authored_verbatim_placeholder_authoring_enabled", lambda: placeholder_on
    )
    monkeypatch.setattr(
        loop_module, "is_authored_verbatim_single_repair_loop_enabled", lambda: loop_on
    )

    assert single_repair_loop_enabled(source_reflow=True) is loop_on
    contract = placeholder_prompt_for_slide(0)
    assert bool(contract) is placeholder_on

    hint = repair_correction_hint(("overflow",), role="BODY", index=0)
    authored = author_prompt(
        "content", "design", SimpleNamespace(wordmark="", topic="topic"), "BODY", 0, 2
    )
    assert (TOKEN_TEXT_RULE in hint) is placeholder_on
    assert (VERBATIM_TEXT_RULE in hint) is not placeholder_on
    # The brief never names a token contract the authoring prompt does not carry.
    assert ("{{q:" in authored) is placeholder_on
    assert ("placeholder token" in hint) is ("{{q:" in authored)


MANIFEST = {
    "units": [
        {
            "sourceUnitId": "u1",
            "sourceIndex": 0,
            "blocks": [
                {
                    "sourceBlockId": "b1",
                    "kind": "text",
                    "order": 0,
                    "payload": {"text": "원문 제목"},
                }
            ],
        }
    ]
}
OUTLINE = {"slides": [{"sourceUnitId": "u1", "sourceIndex": 0, "readingOrder": ["b1"]}]}


def token_context():
    return context_from_source(
        manifest=MANIFEST,
        projection=OUTLINE,
        presentation_id="p1",
        operation_id="op1",
        slide_index=0,
        source_unit_id="u1",
        policy_revision="test/v1",
        fenced_revision=1,
        attempt_token="attempt-1",
        reference_mode="source_content_restyle",
        source_reflow=True,
        terminal_fallback_eligible=False,
        bindings=(("b1", "원문 제목"),),
        state="token",
    )


def test_a_candidate_that_retypes_source_text_is_rejected_by_the_gate(monkeypatch):
    """The loop cannot emit source text: the gate's static checks refuse it."""

    from services.source_content.placeholder import text_step

    monkeypatch.setattr(gate, "get_authored_html_normalization", lambda: "legacy")
    monkeypatch.setattr(
        gate, "is_authored_verbatim_placeholder_authoring_enabled", lambda: True
    )
    monkeypatch.setattr(
        text_step, "is_authored_verbatim_placeholder_authoring_enabled", lambda: True
    )
    retyped = gate.admit_slide_html(token_context(), ORIGINAL)
    assert retyped.verdict == "rejected"
    assert retyped.code == "source_placeholder_missing"
    tokenized = gate.admit_slide_html(
        token_context(), "<!doctype html><html><body><h1>{{q:b1}}</h1></body></html>"
    )
    assert tokenized.verdict == "admitted"
    assert "원문 제목" in tokenized.html


@sync_async
async def test_the_vision_review_is_an_evidence_source_and_writes_no_html(monkeypatch):
    import utils.llm_calls.author_vision_qa as vqa

    calls: dict[str, object] = {}

    async def critique_authored(pngs, contexts=None, **kwargs):
        calls.update(kwargs, pngs=len(pngs), contexts=contexts)
        return [SimpleNamespace(needs_fix=True, issues=()), None]

    monkeypatch.setattr(vqa, "critique_authored", critique_authored)
    evidence = await vqa.review_slides_for_evidence(
        [b"a", b"b"], slide_indices=[2, 5], contexts=["c2", "c5"]
    )
    assert set(evidence) == {2, 5}
    assert evidence[5] is None
    assert calls["slide_indices"] == [2, 5]
    assert calls["critique_round"] == "evidence"
    with pytest.raises(ValueError):
        await vqa.review_slides_for_evidence([b"a"], slide_indices=[1, 2])


def test_evidence_only_review_records_unresolved_slides_as_residual():
    payload = visual_review_evidence_only(
        reviewed_indices=[0, 1, 2],
        unresolved_indices=[1],
        model_reviewed=3,
    )
    assert payload["status"] == "failed"
    assert payload["residualSlideIndices"] == [1]
    assert payload["modelReviewedSlideCount"] == 3
    assert payload["fixedSlideCount"] == 0
    assert payload["reviewedSlideCount"] == 3
    assert visual_review_evidence_only(
        reviewed_indices=[], unresolved_indices=[], model_reviewed=0
    )["fullDeck"] is False


def test_evidence_is_bounded_and_deduplicated():
    merged = merge_evidence(
        ("a", "b", "a"), tuple(f"x{index}" for index in range(12)), ("",)
    )
    assert merged[:2] == ("a", "b")
    assert len(merged) == 8
    assert "" not in merged
