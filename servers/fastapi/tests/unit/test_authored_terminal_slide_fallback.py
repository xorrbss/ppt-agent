"""D7 terminal-slide fallback: remake once, then publish flagged.

The deck-level cases drive the real generation function on SQLite the way
``tests/unit/test_authored_pipeline.py`` does (mocked provider, mocked
Chromium), because the decision under test is a deck outcome: published with a
flag versus failed with ``authored_composition_safety_gate_failed``.
"""

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.slide import SlideModel
from services import authored_terminal_slide_policy as policy_mod
from services.generation_core.quality.flag_classification import (
    REPAIR_NOT_ADMITTED,
    REVIEW_REQUIRED,
)
from services.authored_vnext.generation.scene_checkpoint import (
    SceneRepairBudgetExhausted,
)
from services.authored_composition_policy import CompositionProfile
from services.authored_generation_preflight import over_budget_violations
from services.authored_generation_timing_receipt_e4 import (
    flagged_slide_entries,
    optional_top_level_fields,
)
from services.source_content.browser_readback import (
    BrowserSourceBlockEvidence,
    BrowserSourceReadbackResult,
    log_browser_source_readback_failure,
)

TERMINAL_MARKER = "TERMINAL-SLIDE"

_TERMINAL_HTML = (
    "<!DOCTYPE html><html lang='en'><head><meta charset=\"utf-8\"></head>"
    f"<body><h1>{TERMINAL_MARKER}</h1></body></html>"
)
_REMADE_HTML = (
    "<!DOCTYPE html><html lang='en'><head><meta charset=\"utf-8\"></head>"
    "<body><h1>REMADE</h1></body></html>"
)


def _run(coro):
    return asyncio.run(coro)


def _pop_slide_ids(entries):
    """Strip the two fields these tests do not fix, after checking both.

    Publication backfills every flagged-slide entry with the id of the slide it
    was generated for (see ``authored_presentation_publication``). Its value is
    a fresh ``uuid4`` the test cannot predict, so assert only that it is present
    and well-formed, then compare the rest of the entry against the fixed
    expectations the tests already had.

    ``classification`` is likewise orthogonal to what these cases verify -- they
    pin which slides are flagged and why, not whether a flag asks for a reviewer.
    Assert it is one of the two values and strip it;
    ``tests/unit/test_authored_flag_classification.py`` fixes the rule itself, and
    ``test_the_published_receipt_classifies_every_flag`` below pins that the
    published receipt carries it at all.
    """

    result = []
    for entry in entries:
        entry = dict(entry)
        slide_id = entry.pop("slideId", None)
        assert isinstance(slide_id, str) and slide_id, entry
        uuid.UUID(slide_id)
        classification = entry.pop("classification", None)
        assert classification in {REVIEW_REQUIRED, REPAIR_NOT_ADMITTED}, entry
        result.append(entry)
    return result


class _FakeIssue:
    category = "overlap"
    detail = "two owned boxes overlap"
    severity = "high"
    qids = ()

    def to_dict(self):
        return {
            "category": self.category,
            "detail": self.detail,
            "severity": self.severity,
        }


class _FakeReport:
    telemetry_status = "available"
    # M1's fit magnitude rides on the same report; a stub report carries none.
    fit_summary = None

    def __init__(self, html):
        self.issues = [_FakeIssue()] if TERMINAL_MARKER in html else []

    @property
    def needs_attention(self):
        return bool(self.issues)


def _fake_inspect(html, _metrics, **_kwargs):
    return _FakeReport(html)


def _fake_blocking(report):
    return tuple(report.issues)


def _outline(count):
    return PresentationOutlineModel(
        slides=[SlideOutlineModel(content=f"slide {i} content") for i in range(count)]
    )


async def _sqlite_engine(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'terminal-fallback.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    return engine


async def _slides_for(tmp_path, presentation_id):
    """Read back the persisted slide rows the same sqlite file now holds."""

    engine = await _sqlite_engine(tmp_path)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            rows = await session.scalars(
                select(SlideModel)
                .where(SlideModel.presentation == presentation_id)
                .order_by(SlideModel.index)
            )
            return list(rows)
    finally:
        await engine.dispose()


def _request():
    return GeneratePresentationRequest(
        content="topic", template="authored", authored_style="editorial"
    )


def test_source_reflow_flag_policy_is_separate_strict_and_not_exact(monkeypatch):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW", "true")
    verbatim = SimpleNamespace(strict_dom_gate=True, allow_summarization=False)

    assert policy_mod.terminal_fallback_active(
        profile=verbatim,
        reference_mode="style_reference",
        source_reflow=True,
    )
    assert not policy_mod.terminal_fallback_active(
        profile=SimpleNamespace(strict_dom_gate=False, allow_summarization=False),
        reference_mode="style_reference",
        source_reflow=True,
    )
    assert not policy_mod.terminal_fallback_active(
        profile=verbatim,
        reference_mode="exact_reconstruction",
        source_reflow=True,
    )


def test_flagged_slide_receipt_entries_are_validated_and_serialized():
    entries = [
        {
            "index": 4,
            "reason": "composition_safety:overlap",
            "remakeAttempted": True,
            "remakeOutcome": "remake_gate_failed",
        }
    ]

    assert flagged_slide_entries(entries) == tuple(entries)
    assert flagged_slide_entries(None) == ()
    fields = optional_top_level_fields(
        provider_rate_profile=None,
        normalizations=(),
        quality_loop_variant=None,
        flagged_slides=entries,
    )
    assert fields == {"flaggedSlides": entries}
    with pytest.raises(ValueError):
        flagged_slide_entries([{"index": 0}])


def test_partition_residual_failures_keeps_unflagged_slides_blocking():
    residual = [{"slide_index": 0, "issues": []}, {"slide_index": 2, "issues": []}]
    flagged = [
        {
            "index": 2,
            "reason": "composition_safety:overlap",
            "remakeAttempted": True,
            "remakeOutcome": "remake_gate_failed",
        }
    ]

    blocking, accepted = policy_mod.partition_residual_failures(residual, flagged)

    assert blocking == [{"slide_index": 0, "issues": []}]
    assert accepted == [{"slide_index": 2, "issues": []}]


# ---------------------------------------------------------------------------
# The bounded gate-repair round's own residual (bench job 8ae5d152, slides=[1]).
# Before D7 closed this, the round's ``except Exception`` raised
# ``authored_composition_safety_gate_failed`` before the terminal fallback ran.
# ---------------------------------------------------------------------------


def _source_reflow_request(count):
    request = _request()
    request._source_content_authored_reflow = True
    request._authored_composition_profile = CompositionProfile(
        "source-verbatim", "verbatim", False, True, 2
    )
    request._source_content_manifest = {
        "units": [
            {"sourceUnitId": f"unit-{index}", "blocks": []}
            for index in range(count)
        ]
    }
    request._source_document_outline = {
        "slides": [
            {"sourceUnitId": f"unit-{index}"}
            for index in range(count)
        ]
    }
    return request


def _bind_source_block(request, *, slide_index, block_id):
    request._source_content_manifest["units"][slide_index]["blocks"] = [
        {
            "kind": "text", "sourceBlockId": block_id,
            "contentDigest": "sha256:" + "1" * 64,
        }
    ]
    request._source_document_outline["slides"][slide_index]["readingOrder"] = [block_id]


def _browser_failure_receipt(count, *, block_id, slide_index, missing=False):
    evidence = () if missing else (
        BrowserSourceBlockEvidence(
            source_block_id=block_id, qid="q-source", rendered_text="source text",
            bbox={"x": 1.0, "y": 1.0, "width": 10.0, "height": 10.0},
            visible=True, clipped=False, occluded_fraction=0.1,
            continuous_occluded_fraction=0.1,
        ),
    )
    return BrowserSourceReadbackResult(
        checked_slide_count=count, checked_block_count=0 if missing else 1,
        evidence=evidence,
        missing_block_ids=(block_id,) if missing else (),
        visibility_failures=() if missing else (block_id,),
        failed_slide_indices=(slide_index,),
    )


def test_readback_failure_summary_splits_and_bounds_identifiers(caplog):
    result = BrowserSourceReadbackResult(
        2,
        15,
        missing_block_ids=tuple(f"missing-{index}" for index in range(10)),
        order_violations=3,
        integrity_failures=("integrity-1",),
        telemetry_failures=("telemetry-1",),
        visibility_failures=("visibility-1",),
        failed_slide_indices=(0, 1),
        diagnostic_error="must not be emitted",
    )

    with caplog.at_level("WARNING", logger="services.source_content.browser_readback"):
        summary = log_browser_source_readback_failure(result)

    assert "slides=[0,1]" in summary
    assert "missing=[missing-0,missing-1,missing-2,missing-3,missing-4,missing-5,missing-6,missing-7]+2" in summary
    assert "order=3" in summary
    assert "integrity=[integrity-1]" in summary
    assert "telemetry=[telemetry-1]" in summary
    assert "visibility=[visibility-1]" in summary
    assert "diagnostic=True" in summary
    assert "must not be emitted" not in caplog.text


# ---------------------------------------------------------------------------
# The durable Scene repair claim (bench job da98fa75, resumed attempt 2).
# Slide 12's single repair was consumed by attempt 1, so on resume the claim
# raised SceneRepairBudgetExhausted and the whole deck died with no provider
# call. Under D7 that slide is terminal: remake once, else flag.
# ---------------------------------------------------------------------------


def _exhausted_claim(*exhausted_indices, repeated=True):
    """A ``claim_scene_repair`` whose budget is spent for these slides.

    This is the resumed-attempt shape: attempt 1 consumed slide N's single
    durable repair, attempt 2 reuses every checkpoint and the claim refuses.
    """

    seen: list[int] = []

    def claim(index):
        seen.append(index)
        if index in exhausted_indices:
            raise SceneRepairBudgetExhausted(repeated=repeated)
        return SimpleNamespace(slide_index=index)

    return claim, seen


_ORDINARY = SimpleNamespace(
    profile_id="ordinary", strict_dom_gate=True, allow_summarization=True
)


def test_exhausted_claim_on_resume_leaves_the_round_and_is_declared_terminal():
    claim, seen = _exhausted_claim(1, repeated=True)

    outcome = policy_mod.claim_bounded_repairs(
        [0, 1, 2],
        claim=claim,
        budget_exhausted=SceneRepairBudgetExhausted,
        profile=_ORDINARY,
        reference_mode="style_reference",
        source_reflow=False,
    )

    assert seen == [0, 1, 2]
    assert outcome.blocked is None
    # Slide 1 never enters the deterministic/provider round, so nothing may
    # rewrite it and its original html/png/metrics triple stays untouched.
    assert outcome.flagged == [0, 2]
    assert sorted(outcome.claims) == [0, 2]
    assert outcome.predeclared == [
        {
            "index": 1,
            "reason": policy_mod.REPAIR_BUDGET_EXHAUSTED_REASON,
            "repeated": True,
        }
    ]


def test_exhausted_claim_blocks_the_deck_when_it_may_not_publish_flagged(
    monkeypatch,
):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES", "false")
    claim, _seen = _exhausted_claim(1, repeated=True)

    outcome = policy_mod.claim_bounded_repairs(
        [0, 1, 2],
        claim=claim,
        budget_exhausted=SceneRepairBudgetExhausted,
        profile=_ORDINARY,
        reference_mode="style_reference",
        source_reflow=False,
    )

    # The caller raises today's authored_composition_safety_gate_failed on this.
    assert outcome.blocked is not None
    blocked_index, blocked_error = outcome.blocked
    assert blocked_index == 1
    assert blocked_error.repeated is True
    assert outcome.predeclared == []


def test_a_deck_with_no_exhausted_claim_is_unchanged():
    claim, seen = _exhausted_claim()

    outcome = policy_mod.claim_bounded_repairs(
        [3],
        claim=claim,
        budget_exhausted=SceneRepairBudgetExhausted,
        profile=_ORDINARY,
        reference_mode="style_reference",
        source_reflow=False,
    )

    assert seen == [3]
    assert outcome.flagged == [3]
    assert outcome.predeclared == []
    assert outcome.blocked is None


def test_declaration_refuses_a_deck_that_may_not_publish_flagged(monkeypatch):
    error = SimpleNamespace(repeated=True)
    ordinary = SimpleNamespace(
        profile_id="ordinary", strict_dom_gate=True, allow_summarization=True
    )
    verbatim = SimpleNamespace(
        profile_id="source-verbatim", strict_dom_gate=True, allow_summarization=False
    )

    assert policy_mod.declare_exhausted_repair_budget(
        3, error, profile=ordinary, reference_mode="style_reference", source_reflow=False
    ) == {
        "index": 3,
        "reason": policy_mod.REPAIR_BUDGET_EXHAUSTED_REASON,
        "repeated": True,
    }
    # Source reflow may publish flagged by default; switched off, it may not.
    assert policy_mod.declare_exhausted_repair_budget(
        3, error, profile=verbatim, reference_mode="style_reference", source_reflow=True
    ) is not None
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW", "false")
    assert (
        policy_mod.declare_exhausted_repair_budget(
            3,
            error,
            profile=verbatim,
            reference_mode="style_reference",
            source_reflow=True,
        )
        is None
    )
    assert (
        policy_mod.declare_exhausted_repair_budget(
            3,
            error,
            profile=ordinary,
            reference_mode="exact_reconstruction",
            source_reflow=False,
        )
        is None
    )


def test_receipt_accepts_a_flag_with_and_without_the_repeated_bit():
    with_bit = {
        "index": 2,
        "reason": policy_mod.REPAIR_BUDGET_EXHAUSTED_REASON,
        "remakeAttempted": True,
        "remakeOutcome": "remake_gate_failed",
        "repeated": True,
    }
    without_bit = {
        "index": 3,
        "reason": "composition_safety:overlap",
        "remakeAttempted": True,
        "remakeOutcome": "remake_gate_failed",
    }

    assert flagged_slide_entries([with_bit, without_bit]) == (with_bit, without_bit)
    with pytest.raises(ValueError):
        flagged_slide_entries([{**with_bit, "repeated": "yes"}])


# ---------------------------------------------------------------------------
# The bounded gate repair whose candidate was not admitted (bench job 18306a40).
# Slide 10 authored, its repair produced a candidate, and both the candidate and
# the original carried only ELEMENT-NAMED capacity violations -- the body
# wrapper had collapsed to width 0. The deck died with
# ``authored_slide_capacity_exceeded`` without ever reaching the fallback.
# A violation that names an element is a layout defect; only a measured
# ``<check>:<measured>><allowed>`` is a budget the model cannot write around.
# ---------------------------------------------------------------------------


_ELEMENT_NAMED_VIOLATIONS = (
    "browser_geometry_unverified:closing.kicker",
    "browser_text_not_visible:closing.statement",
)
_MEASURED_VIOLATION = ("visible_text:400>280",)


def test_over_budget_violations_separate_a_budget_from_a_layout_defect():
    assert over_budget_violations(_ELEMENT_NAMED_VIOLATIONS) == ()
    assert over_budget_violations(_MEASURED_VIOLATION) == _MEASURED_VIOLATION
    # An unmeasured slide names no element, so it stays fail-closed.
    assert over_budget_violations(("browser_metrics_unavailable",)) == (
        "browser_metrics_unavailable",
    )


def test_gate_repair_rejection_needs_the_fallback_and_a_layout_defect_only():
    assert policy_mod.gate_repair_rejection_is_terminal_slide(
        enabled=True,
        candidate_violations=_ELEMENT_NAMED_VIOLATIONS,
        original_violations=_ELEMENT_NAMED_VIOLATIONS,
    )
    assert not policy_mod.gate_repair_rejection_is_terminal_slide(
        enabled=False,
        candidate_violations=_ELEMENT_NAMED_VIOLATIONS,
        original_violations=(),
    )
    # A measurement on either side of the admission ends the deck.
    assert not policy_mod.gate_repair_rejection_is_terminal_slide(
        enabled=True,
        candidate_violations=_ELEMENT_NAMED_VIOLATIONS,
        original_violations=_MEASURED_VIOLATION,
    )


def test_only_a_flagged_slide_absorbs_its_element_named_capacity_violations():
    assert policy_mod.flagged_slide_absorbs_capacity(
        1, _ELEMENT_NAMED_VIOLATIONS, flagged=[1]
    )
    assert not policy_mod.flagged_slide_absorbs_capacity(
        0, _ELEMENT_NAMED_VIOLATIONS, flagged=[1]
    )
    assert not policy_mod.flagged_slide_absorbs_capacity(
        1, _MEASURED_VIOLATION, flagged=[1]
    )


# ---------------------------------------------------------------------------
# Remake-first ordering (bench run 10: the patch round fixed 0 of 6 flagged
# slides, a fresh composition briefed with the same defects passed 4 of 8).
# On a fallback-active deck the remake now runs BEFORE the patch round, and a
# slide both rounds miss gets a second remake before it is flagged.
# ---------------------------------------------------------------------------


_FRAGMENTED_HTML = (
    "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'></head>"
    "<body><h1>FRAGMENTED</h1></body></html>"
)


def test_the_receipt_accepts_only_a_count_within_the_slide_budget():
    entry = {
        "index": 1,
        "reason": "composition_safety:overlap",
        "remakeAttempted": True,
        "remakeOutcome": "remake_budget_exhausted",
        "remakeCount": 2,
    }

    assert flagged_slide_entries([entry]) == (entry,)
    for invalid in (0, 3, True, "2"):
        with pytest.raises(ValueError):
            flagged_slide_entries([{**entry, "remakeCount": invalid}])


# ---------------------------------------------------------------------------
# The QualityReceipt v2 capacity evidence. A D7-flagged slide's capacity checks
# were still aggregated with ``all()``, so the published deck failed with
# ``authored_quality_receipt_gate_failed`` (capacityStatus=blocked) even though
# the safety evidence already records that slide as non-blocking.
# ---------------------------------------------------------------------------


_CAPACITY_MARKER = "CAPACITY-FAIL"
_CAPACITY_HTML = (
    "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'></head>"
    f"<body><h1>{_CAPACITY_MARKER}</h1></body></html>"
)
_CAPACITY_ROLES = ("text", "title", "body", "font", "qid", "image")


# ---------------------------------------------------------------------------
# The repair-round checkpoint persists (bench job 4e855068). A workspace-linked
# job's checkpoint service refuses a write whose ``fencing_token`` is not an
# int (``authored_workspace_operation_fencing_token_required``, retryable), so
# a deck that authored, rendered and persisted 10/10 slides died three
# identical attempts later in the dead-letter queue. Every checkpoint write
# after external success must carry the lease's fencing token.
# ---------------------------------------------------------------------------


_FENCING_TOKEN = 7


def _workspace_job(token=_FENCING_TOKEN):
    job = PresentationGenerationJob(
        task_id=f"task-{uuid.uuid4()}",
        presentation_id=uuid.uuid4(),
        # The workspace link is what makes a missing token fatal in production;
        # the artifact publication it also switches on is out of scope here, so
        # only the token the call sites must forward is set.
        request_sha256="0" * 64,
        request_payload={},
        attempt_token="attempt-token",
        attempt_number=1,
    )
    # The worker carries the lease's fencing token beside the row, not in it.
    object.__setattr__(job, "_workspace_fencing_token", token)
    return job


# ---------------------------------------------------------------------------
# Defect 3 (editor flagged-slide badge review): a slide can be flagged twice --
# once by the author-stage rescue, once by the terminal gate fallback that
# runs after it. Before ``_merge_flagged_slide_entries`` existed, the two
# lists were concatenated as-is, so the client's badge map (keyed by slide,
# not by list position) let the second entry silently replace the first,
# dropping the author-stage reason.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# M0 (design 2026-09-08): every provider call of the repair ladder is receipted
# under its own stage. Bench run 48 wrote 25 ``author`` receipts and none for
# the 7 remake calls, because both remake rounds ran outside a usage scope.
# ---------------------------------------------------------------------------


def _capture_provider_receipts(monkeypatch):
    receipts: list[object] = []

    async def receipt(value):
        receipts.append(value)

    monkeypatch.setattr(
        "services.provider_usage_receipt_service.record_provider_usage_receipt_detached",
        receipt,
    )
    monkeypatch.setattr(
        "services.provider_usage_instrumentation.durable_provider_runtime_required",
        lambda: False,
    )
    monkeypatch.setenv("AUTHORED_LOCAL_STAGE_ASYNC_ENABLED", "false")
    return receipts


async def _paid_call(*, slot, model, retry_class, input_tokens, output_tokens):
    """One admitted provider call, filed the way the author/structured boundaries file it."""

    from services.provider_usage_instrumentation import (
        provider_call_admission,
        record_provider_call,
    )

    async with provider_call_admission(
        is_retry=retry_class != "initial", semantic_slot=slot, provider="codex", model=model
    ):
        pass
    await record_provider_call(
        provider="codex", model=model, latency_ms=1000, retry_class=retry_class,
        response=SimpleNamespace(
            usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
        ),
    )
