"""Page-level error classes: which page failures still end an SVG deck.

``page_failure_kind.page_error_class`` sorts every code a page raises into
integrity (deck fails), provider (today's retry/partial-recovery path) and page
quality (the rescue ladder). These tests drive the real rescue ladder, the real
render step for the validation codes, the real recovery repair (archive and
restore of the prior candidate), the real group-review call path with the real
gate seal and advisory record, and W1's real publication flagging; only the
browser, the provider and the storage boundaries are fixtures.
"""
from __future__ import annotations

import asyncio
import struct
import uuid
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_svg_source_layout import manifest_for
from test_svg_terminal_rescue import (
    _AUTHORED, _SOURCE_BLOCKS, _Stores, _ctx, _quality, _renderer, _rendered_payload, _request, _source_ctx,
    _source_rendered_payload,
)
from services.authored_checkpoint_errors import AuthoredStageError
from services.generation_core.drivers.svg import collect, quality_recovery, render, terminal
from services.generation_core.drivers.svg import quality as deck_quality
from services.generation_core.quality.advisory_record import content_digest
from services.generation_core.quality.page_failure_kind import (
    INTEGRITY, PAGE_QUALITY, PROVIDER, absorbable_page_error, error_class, page_error_class,
)
from services.generation_core.quality.vision_advisory_prompt import (
    DESIGN_AXES, DESIGN_LOCAL_AXES, DESIGN_PRINCIPLES_VERSION, DESIGN_REVIEW_PROMPT_VERSION,
)
from services.generation_core.runtime.profile import resolve_svg_runtime_profile
from services.provider_runtime_primitives import BudgetExceededError, CancellationRequested
from services.public_operation_admission_models import PublicAdmissionRejected
from services.svg_authoring_profile import ART_DIRECTION_PROFILE_V8
from services.svg_contract import source_digest
from utils.hashing import canonical_json_digest, sha256_hex
from utils.provider_overload import ProviderOutcomeError, ProviderQuotaPause

PNG = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + struct.pack(">II", 1280, 720)
TENANT = "fixture-tenant"


def _stage_error(index=1, code="svg_validation_failed", retryable=False):
    return AuthoredStageError(slide_index=index, stage="render", code=code, retryable=retryable)


# --------------------------------------------------------------------------
# the classification table
# --------------------------------------------------------------------------

_TABLE = [
    # G1: a source-reflow page's second attempt failing validation.
    ("svg_source_receipt_text_mismatch", PAGE_QUALITY),
    ("svg_source_receipt_binding_mismatch", PAGE_QUALITY),
    ("svg_source_native_table_group_invalid", PAGE_QUALITY),
    ("svg_source_image_unknown_slot", PAGE_QUALITY),
    ("svg_source_classification_missing", PAGE_QUALITY),
    # G2: measured render gates and the asset capture of one page.
    ("svg_source_paint_occluded", PAGE_QUALITY),
    ("svg_source_paint_unproven", PAGE_QUALITY),
    ("svg_timeline_caption_ink_failed", PAGE_QUALITY),
    ("svg_common_header_body_intrusion", PAGE_QUALITY),
    ("svg_common_header_title_overflow", PAGE_QUALITY),
    ("svg_common_header_digest_mismatch", PAGE_QUALITY),
    ("svg_asset_capture_failed", PAGE_QUALITY),
    ("svg_asset_screen_derivative_failed", INTEGRITY),
    ("svg_template_fallback_binding_invalid", INTEGRITY),
    ("svg_correction_previous_render_binding_invalid", INTEGRITY),
    ("svg_common_header_binding_invalid", INTEGRITY),
    ("svg_source_local_escalation_invalid", INTEGRITY),
    # author.py: the page's own source layout could not be built.
    ("svg_source_local_layout_failed", PAGE_QUALITY),
    ("svg_source_template_layout_unavailable", PAGE_QUALITY),
    # The attempt-budget codes the ladder always rescued.
    ("svg_validation_failed", PAGE_QUALITY),
    ("svg_repair_budget_exhausted", PAGE_QUALITY),
    ("svg_source_quality_failed", PAGE_QUALITY),
    ("svg_source_quality_unverified", PAGE_QUALITY),
    ("svg_source_template_layout_unavailable", PAGE_QUALITY),
    ("svg_source_header_body_ownership_conflict", PAGE_QUALITY),
    ("svg_partial_recovery_cached_quality_failed", PAGE_QUALITY),
    # Provider outcomes keep today's retry and partial-recovery path.
    ("public_provider_result_unknown", PROVIDER),
    ("public_provider_call_timeout", PROVIDER),
    ("svg_provider_attempt_uncertain", PROVIDER),
    ("svg_provider_terminal_output_mismatch", PROVIDER),
    ("svg_common_header_provider_result_unknown", PROVIDER),
    # Integrity: authority, lease, fence, cancellation, bound evidence, plan/manifest.
    ("svg_provider_checkpoint_authority_expired", INTEGRITY),
    ("svg_provider_completed_checkpoint_authority_expired", INTEGRITY),
    ("authored_attempt_lost", INTEGRITY),
    ("authored_workspace_operation_fence_lost", INTEGRITY),
    ("generation_cancelled", INTEGRITY),
    ("svg_cache_remeasurement_changed", INTEGRITY),
    ("svg_cache_remeasurement_invalid", INTEGRITY),
    ("svg_cached_author_render_provider_forbidden", INTEGRITY),
    ("svg_partial_recovery_cached_provider_forbidden", INTEGRITY),
    ("svg_quality_recovery_local_evidence_stale", INTEGRITY),
    ("svg_validated_recovery_reservation_stale", INTEGRITY),
    ("svg_source_receipt_input_mismatch", INTEGRITY),
    ("svg_source_manifest_digest_mismatch", INTEGRITY),
    ("svg_source_plan_input_mismatch", INTEGRITY),
    ("svg_source_template_snapshot_required", INTEGRITY),
    ("svg_source_header_contract_invalid", INTEGRITY),
    ("svg_source_header_composition_invalid", INTEGRITY),
    ("svg_source_header_receipt_changed", INTEGRITY),
    ("svg_source_chart_numeric_mismatch", INTEGRITY),
    ("svg_common_header_contract_invalid", INTEGRITY),
    ("svg_checkpoint_input_conflict", INTEGRITY),
    ("svg_stage_checkpoint_conflict", INTEGRITY),
    # A code with a detail suffix is read by its code.
    ("svg_source_image_only_page_not_supported:page=3", INTEGRITY),
    ("svg_source_receipt_text_mismatch:block=b1", PAGE_QUALITY),
    # Unknown codes are page quality; no code at all is not.
    ("svg_a_page_check_nobody_emits_yet", PAGE_QUALITY),
    (None, INTEGRITY),
    ("", INTEGRITY),
]


@pytest.mark.parametrize("code,expected", _TABLE)
def test_page_error_class_table(code, expected):
    assert page_error_class(code) == expected


@pytest.mark.parametrize("error,expected,absorbable", [
    (asyncio.CancelledError(), INTEGRITY, False),
    (CancellationRequested("user"), INTEGRITY, False),
    (ProviderOutcomeError("public_provider_call_timeout", recovery_allowed=False), PROVIDER, True),
    (ProviderOutcomeError("public_provider_result_unknown", recovery_allowed=False), PROVIDER, True),
    (ProviderQuotaPause(reset_at=10, observed_at=0), PROVIDER, False),  # deferred, never absorbed
    (PublicAdmissionRejected("public_provider_request_limit_exceeded"), PROVIDER, True),
    (BudgetExceededError(kind="estimated", limit=1, observed=2), PROVIDER, True),
    (TimeoutError(), PROVIDER, True),
    (_stage_error(code="svg_asset_capture_failed"), PAGE_QUALITY, True),
    (_stage_error(code="authored_attempt_lost"), INTEGRITY, False),
    (ValueError("svg_quality_recovery_authority_mismatch"), INTEGRITY, False),
    (KeyError("svg_content"), INTEGRITY, False),
])
def test_error_class_of_exceptions(error, expected, absorbable):
    assert error_class(error) == expected
    assert absorbable_page_error(error) is absorbable


@pytest.mark.parametrize("error,exhausted", [
    (_stage_error(1, "svg_source_receipt_text_mismatch"), True),
    (_stage_error(1, "svg_timeline_caption_ink_failed"), True),
    (_stage_error(1, "svg_source_paint_occluded"), True),
    (_stage_error(1, "svg_validation_failed"), True),
    (_stage_error(0, "svg_validation_failed"), False),  # another page's error
    (_stage_error(None, "svg_validation_failed"), False),  # a deck-level error
    (_stage_error(1, "svg_validation_failed", retryable=True), False),
    (_stage_error(1, "svg_provider_attempt_uncertain"), False),
    (_stage_error(1, "public_provider_result_unknown"), False),
    (_stage_error(1, "svg_template_fallback_binding_invalid"), False),
    (_stage_error(1, "authored_attempt_lost"), False),
    (ValueError("svg_validation_failed"), False),
])
def test_is_exhausted_page_follows_the_classification(error, exhausted):
    assert terminal.is_exhausted_page(error, 1) is exhausted


# --------------------------------------------------------------------------
# G1/G2: a page-quality code reaches the rescue ladder
# --------------------------------------------------------------------------


def _rescue(ctx, stores, payload, error, *, index):
    return asyncio.run(terminal.rescue_terminal_page(
        ctx=ctx, request=_request(), index=index, content="제목\n- 항목", payload=payload, error=error,
        job=None, checkpoint=stores.checkpoint, write_png=stores.write_png, read_png=stores.read_png,
        presentation_id=None, async_session_maker=None))


@pytest.mark.parametrize("code", ["svg_source_receipt_text_mismatch", "svg_source_receipt_binding_mismatch",
                                  "svg_source_native_table_group_invalid", "svg_source_image_unknown_slot"])
def test_a_source_validation_code_on_the_last_attempt_gets_the_verbatim_fallback(monkeypatch, code):
    """The real render step raises the page's own code; the ladder replaces the page."""
    _renderer(monkeypatch)
    manifest = manifest_for(_SOURCE_BLOCKS)
    ctx, stores = _source_ctx(manifest), _Stores()
    payload = {"input_digest": "in", "attempts": [
        {"kind": "provider", "receipt": {}},
        {"kind": "provider", "receipt": {"validation_error": f"{code}:witness"}}]}

    with pytest.raises(AuthoredStageError) as raised:
        asyncio.run(render.attempt_render(ctx=ctx, request=_request(), presentation_id=None, index=0,
            content="", payload=payload, svg="<svg/>", ordinal=1, job=None, checkpoint=stores.checkpoint,
            async_session_maker=None, write_png=stores.write_png))
    error = raised.value
    assert error.code == code and error.slide_index == 0 and error.retryable is False
    assert terminal.is_exhausted_page(error, 0)

    rescued, png = _rescue(ctx, stores, payload, error, index=0)

    assert png is None
    assert rescued["terminal"] == {"index": 0, "reason": "author:source_verbatim_fallback",
                                   "remakeAttempted": False, "remakeOutcome": "svg_source_fallback_page"}
    assert rescued["terminal_error"] == code


@pytest.mark.parametrize("code,kind", [
    ("svg_timeline_caption_ink_failed", "timeline_caption_bar_ink_overlap"),
    ("svg_common_header_body_intrusion", "svg_common_header_body_intrusion"),
    ("svg_asset_capture_failed", "text_overlap"),
])
def test_a_layout_render_gate_keeps_the_last_render_flagged(monkeypatch, code, kind):
    renderer = _renderer(monkeypatch)
    stores = _Stores()
    payload = _rendered_payload(stores, _AUTHORED, _quality(_AUTHORED, passed=False, failures=[{"kind": kind}]))

    rescued, png = _rescue(_ctx(), stores, payload, _stage_error(1, code), index=1)

    assert rescued["terminal"] == {"index": 1, "reason": f"svg:{code}", "remakeAttempted": False,
                                   "remakeOutcome": "verbatim_flag_only"}
    assert png == b"png:" + _AUTHORED.encode()
    renderer.assert_not_awaited()


def test_a_source_occlusion_gate_replaces_the_page_whose_text_is_hidden(monkeypatch):
    _renderer(monkeypatch)
    manifest = manifest_for(_SOURCE_BLOCKS)
    stores = _Stores()
    payload = _source_rendered_payload(stores, manifest, [{"kind": "source_text_paint_occluded"}])

    rescued, _png = _rescue(_source_ctx(manifest), stores, payload, _stage_error(0, "svg_source_paint_occluded"),
                            index=0)

    assert rescued["terminal"]["remakeOutcome"] == "svg_source_fallback_page"


@pytest.mark.parametrize("code", ["svg_template_fallback_binding_invalid", "svg_asset_screen_derivative_failed",
                                  "svg_provider_attempt_uncertain", "authored_attempt_lost"])
def test_integrity_and_provider_codes_are_not_rescued(monkeypatch, code):
    renderer = _renderer(monkeypatch)
    stores = _Stores()
    payload = _rendered_payload(stores, _AUTHORED, _quality(_AUTHORED, passed=False,
                                                            failures=[{"kind": "text_overlap"}]))
    assert _rescue(_ctx(), stores, payload, _stage_error(1, code), index=1) is None
    renderer.assert_not_awaited()


# --------------------------------------------------------------------------
# G3: a failed optional redesign keeps the archived prior
# --------------------------------------------------------------------------


class _RecoveryHarness:
    def __init__(self, monkeypatch, tmp_path, attempt_loop):
        owner = uuid.uuid4()
        monkeypatch.setattr("utils.owner_context.get_current_owner_id", lambda: owner)
        monkeypatch.setattr("utils.owner_context.get_current_tenant_id", lambda: TENANT)
        monkeypatch.setattr("services.authored_checkpoint_artifacts.checkpoint_root", lambda: tmp_path)
        plan = dict(slide_count=3, review_round_limit=2, page_attempt_limit=3, phase_group_limit=1,
                    review_group_size=5, global_format_retry_limit=0)
        monkeypatch.setattr(quality_recovery, "read_quality_recovery_execution_plan", lambda *_a, **_k: plan)
        monkeypatch.setattr(quality_recovery, "ensure_recovery_authority", AsyncMock())
        monkeypatch.setattr(quality_recovery, "recovery_binding", lambda *_args: "binding")
        monkeypatch.setattr(quality_recovery, "_prepare_final_selected_source", AsyncMock(return_value=None))
        self.stores = _Stores()
        self.pages = []
        for index in range(3):
            svg = f"<svg>prior {index}</svg>"
            payload = dict(svg_content=svg, input_digest=f"in-{index}", quality={"passed": True},
                           attempts=[{"kind": "provider", "receipt": {}}],
                           **self.stores.write_png(b"png" + svg.encode()))
            self.pages.append((payload, self.stores.read_png(payload)))
        self.advisory = {"vision": "first-review"}
        self.quality = SimpleNamespace(
            deck_advisory=AsyncMock(return_value=self.advisory),
            quality_recovery_decisions=lambda *_args: {1: {"repair_scope": "design_rewrite", "issues": [],
                                                           "target_axes": ["information_structure"]}},
            reusable_final_deck_advisory=lambda _ctx, _pages, advisory, **_kwargs: advisory,
            classify_pages=AsyncMock())
        self.ctx = SimpleNamespace(snapshot={"quality_recovery_policy": {"version": 2}},
            quality_recovery_input_binding="binding", semaphore=asyncio.Semaphore(3),
            quality_recovery_previous_renders={})
        self.job = SimpleNamespace(id=uuid.uuid4(), workspace_operation_id=uuid.uuid4(), request_payload={})
        self.hooks = SimpleNamespace(checkpoint=self.stores.checkpoint, read_png=self.stores.read_png,
                                     write_png=self.stores.write_png)
        self.state = SimpleNamespace(fatal_error=None)
        self.attempt_loop = attempt_loop

    def run(self):
        outline = SimpleNamespace(slides=[SimpleNamespace(content=f"page {i}") for i in range(3)])
        return asyncio.run(quality_recovery.recover_deck(self.ctx, _request(), None, outline, self.job,
            self.pages, self.state, self.hooks, AsyncMock(), self.attempt_loop, self.quality))


def _raising(error, *, write_candidate=True):
    async def attempt_loop(_ctx, _request, _presentation_id, _index, _content, payload, *_args):
        payload["attempts"].append({"kind": "provider", "receipt": {}})
        if write_candidate:
            payload["svg_content"] = "<svg>candidate that failed</svg>"
        raise error
    return AsyncMock(side_effect=attempt_loop)


@pytest.mark.parametrize("error,reason", [
    (_stage_error(1, "svg_source_quality_failed"), "svg_source_quality_failed"),
    (_stage_error(1, "svg_timeline_caption_ink_failed"), "svg_timeline_caption_ink_failed"),
    (_stage_error(1, "svg_asset_capture_failed"), "svg_asset_capture_failed"),
    (_stage_error(1, "svg_source_receipt_text_mismatch"), "svg_source_receipt_text_mismatch"),
    (_stage_error(1, "svg_provider_attempt_uncertain"), "svg_provider_attempt_uncertain"),
    (ProviderOutcomeError("public_provider_call_timeout", recovery_allowed=False), "public_provider_call_timeout"),
    (ProviderOutcomeError("public_provider_result_unknown", recovery_allowed=False),
     "public_provider_result_unknown"),
    (TimeoutError(), "TimeoutError"),
    (BudgetExceededError(kind="estimated", limit=1, observed=2), "BudgetExceededError"),
])
def test_a_failed_redesign_restores_the_archived_prior(monkeypatch, tmp_path, error, reason):
    harness = _RecoveryHarness(monkeypatch, tmp_path, _raising(error))
    prior = deepcopy(harness.pages[1][0])

    pages, advisory = harness.run()

    restored, png = pages[1]
    assert restored["svg_content"] == prior["svg_content"] and restored["png_sha256"] == prior["png_sha256"]
    assert png == harness.pages[1][1]
    assert restored["quality_recovery"]["status"] == "retained"
    assert restored["quality_recovery"]["reason"] == reason
    assert len(restored["attempts"]) == 2  # the spent attempt stays in the history
    assert harness.stores.saved[1] is restored
    assert harness.state.fatal_error is None
    assert advisory is harness.advisory  # the deck goes on to publication


@pytest.mark.parametrize("error", [
    _stage_error(1, "authored_attempt_lost"),
    _stage_error(1, "svg_cache_remeasurement_changed"),
    ValueError("svg_quality_recovery_authority_mismatch"),
    CancellationRequested("user"),
    ProviderQuotaPause(reset_at=10, observed_at=0),
])
def test_integrity_cancellation_and_deferral_during_a_redesign_stay_fatal(monkeypatch, tmp_path, error):
    harness = _RecoveryHarness(monkeypatch, tmp_path, _raising(error))

    with pytest.raises(type(error)) as raised:
        harness.run()
    assert raised.value is error
    assert harness.state.fatal_error is error
    assert harness.stores.saved[1]["quality_recovery"]["status"] == "repair_pending"


def test_another_pages_fatal_error_reraised_in_a_redesign_stays_fatal(monkeypatch, tmp_path):
    other = _stage_error(0, "svg_validation_failed")
    harness = _RecoveryHarness(monkeypatch, tmp_path, None)

    async def attempt_loop(*_args):
        harness.state.fatal_error = other
        raise other
    harness.attempt_loop = AsyncMock(side_effect=attempt_loop)

    with pytest.raises(AuthoredStageError) as raised:
        harness.run()
    assert raised.value is other


def test_a_retained_prior_needs_its_archive(monkeypatch, tmp_path):
    """Restoring is integrity-checked: a missing archive still fails the deck."""
    harness = _RecoveryHarness(monkeypatch, tmp_path, None)

    async def attempt_loop(_ctx, _request, _presentation_id, index, _content, payload, *_args):
        for path in tmp_path.rglob("*.svg"):
            path.write_bytes(b"<svg>tampered</svg>")
        raise _stage_error(index, "svg_source_quality_failed")
    harness.attempt_loop = AsyncMock(side_effect=attempt_loop)

    with pytest.raises(ValueError, match="svg_quality_recovery_candidate_invalid"):
        harness.run()


# --------------------------------------------------------------------------
# G4: a failed review call leaves its pages unreviewed
# --------------------------------------------------------------------------


class _ReviewHarness:
    """The real ``_quality_first_deck_advisory`` over a fixture design context."""

    def __init__(self, monkeypatch, count, *, version=1):
        self.owner = uuid.uuid4()
        monkeypatch.setattr("utils.owner_context.get_current_owner_id", lambda: self.owner)
        monkeypatch.setattr("utils.owner_context.get_current_tenant_id", lambda: TENANT)
        self.job = SimpleNamespace(id=uuid.uuid4(), workspace_operation_id=uuid.uuid4(),
                                   presentation_id=uuid.uuid4(), attempt_number=1, request_payload={})
        self.snapshot = dict(canvas={"width": 1280, "height": 720}, authoring_profile=dict(ART_DIRECTION_PROFILE_V8),
                             quality_recovery_policy={"id": "svg-quality-first", "version": version})
        self.snapshot["digest"] = canonical_json_digest(self.snapshot, allow_nan=True)
        self.pages = []
        for index in range(count):
            svg = f"<svg>page {index}</svg>"
            self.pages.append((dict(svg_content=svg, png_sha256=sha256_hex(PNG), input_digest=f"in-{index}",
                                    attempts=[], quality=dict(passed=True, verified=True, failures=[],
                                    measurement_complete=True, source_digest=source_digest(svg),
                                    png_digest="sha256:" + sha256_hex(PNG))), PNG))
        context = dict(policy=self.snapshot["quality_recovery_policy"], style={"canvas": self.snapshot["canvas"]},
            styleDigest=content_digest(self.snapshot["canvas"]), snapshotDigest=content_digest(self.snapshot),
            runtimeBinding="fixture-runtime", exampleDigests=[], examples=[], roles=["content"] * count,
            pages=[dict(index=index, content=f"source {index}", referenceRoles=["content"])
                   for index in range(count)],
            binding=dict(jobId=str(self.job.id), operationId=str(self.job.workspace_operation_id),
                         presentationId=str(self.job.presentation_id), ownerId=str(self.owner), tenantId=TENANT),
            priorCandidates={}, deckComplete=True, composition={"pages": [], "groups": []},
            mandatoryEvidence=[True] * count)
        if version == 2:
            context["reviewPrinciplesVersion"] = DESIGN_PRINCIPLES_VERSION
        monkeypatch.setattr(deck_quality, "_design_context", lambda *_args, **_kwargs: deepcopy(context))
        groups = -(-count // 5)
        self.plan = dict(slide_count=count, review_round_limit=2, page_attempt_limit=3, review_group_size=5,
                         phase_group_limit=groups + (count > 5), global_format_retry_limit=0)
        monkeypatch.setattr("services.svg_authoring_profile.read_quality_recovery_execution_plan",
                            lambda *_args, **_kwargs: self.plan)
        self.groups = {}
        self.calls = []
        self.ctx = SimpleNamespace(snapshot=self.snapshot, quality_recovery_review_group_checkpoint=self.checkpoint)

    async def checkpoint(self, ordinal, marker=None):
        if marker is not None:
            self.groups[str(ordinal)] = deepcopy(marker)
        return deepcopy(self.groups.get(str(ordinal)))

    def review(self, run_advisory):
        return asyncio.run(deck_quality._quality_first_deck_advisory(self.ctx, self.pages, job=self.job,
            read_png=lambda _payload: PNG, run_advisory=run_advisory, phase=0))

    def provider(self, *, fail_scopes=(), error=None):
        async def run_advisory(renders, *, slide_count, required, design_context):
            scope = design_context["reviewScope"]
            self.calls.append((scope, design_context.get("globalPageIndices")))
            if scope in fail_scopes:
                raise error
            axes = DESIGN_AXES if scope in {"full", "global"} else DESIGN_LOCAL_AXES
            if scope == "global":
                axes = DESIGN_AXES[-1:]
            pages = [dict(index=index, evaluated=True, issues=[], axes={name: dict(verdict="passed",
                     severity="none", repairScope="none", visibleEvidence="Readable grouping.") for name in axes})
                     for index in range(slide_count)]
            return dict(promptVersion=DESIGN_REVIEW_PROMPT_VERSION, model="fixture", slideCount=slide_count,
                        reviewedPageIndices=list(range(slide_count)),
                        reviewedPngDigests=[sha256_hex(png) for png in renders], slides=[],
                        designReview=dict(pages=pages, candidateComparisons=[], deckFlow={"evaluated": True}))
        return run_advisory


def _publish_flags(harness, advisory):
    review = deck_quality.apply_quality_recovery_publication(harness.snapshot, harness.pages, advisory,
                                                            job=harness.job)
    summary = deck_quality.reclassify_pages(harness.pages)
    return review, summary, {index: payload.get(deck_quality.REVIEW_FLAG_KEY, {}).get("reason")
                             for index, (payload, _) in enumerate(harness.pages)}


def test_a_complete_review_passes_every_page(monkeypatch):
    harness = _ReviewHarness(monkeypatch, 2)
    advisory = harness.review(harness.provider())
    gate = advisory["vision"]["designGate"]
    assert gate["status"] == "passed" and gate["unreviewedPages"] == []
    review, summary, flags = _publish_flags(harness, advisory)
    assert review["status"] == "reviewed" and summary["status"] == "passed"
    assert flags == {0: None, 1: None}


@pytest.mark.parametrize("error", [
    ProviderOutcomeError("public_provider_call_timeout", recovery_allowed=False),
    ProviderOutcomeError("public_provider_result_unknown", recovery_allowed=False),
    BudgetExceededError(kind="actual", limit=1, observed=2),
    TimeoutError(),
])
def test_a_failed_full_review_call_publishes_its_pages_unreviewed(monkeypatch, error):
    harness = _ReviewHarness(monkeypatch, 2)

    advisory = harness.review(harness.provider(fail_scopes={"full"}, error=error))

    gate = advisory["vision"]["designGate"]
    assert gate["status"] == "unreviewed" and gate["unreviewedPages"] == [0, 1]
    assert advisory["vision"]["skipped"] == "review_call_unavailable"
    marker = harness.groups["0"]
    assert marker["status"] == "review_failed" and marker["input_digest"]
    assert marker["failureCode"] == getattr(error, "code", type(error).__name__)
    # A gate that judged no page is reported once for the deck, never as one
    # design:unreviewed flag per page.
    review, summary, flags = _publish_flags(harness, advisory)
    assert review == {"status": "unavailable", "reason": "review_call_unavailable", "gateDigest": gate["digest"]}
    assert summary["status"] == "passed" and summary["reviewPages"] == []
    assert flags == {0: None, 1: None}


@pytest.mark.parametrize("fail_scope", ["local", "global"])
def test_a_failed_local_or_global_call_seals_the_deck_unreviewed(monkeypatch, fail_scope):
    """The deck-flow axis is part of every page's verdict: one missing call
    leaves every page unreviewed, and the deck still publishes, with the
    review reported unavailable for the deck and no page flagged."""
    harness = _ReviewHarness(monkeypatch, 7)
    error = ProviderOutcomeError("public_provider_call_timeout", recovery_allowed=False)

    async def run_advisory(renders, **kwargs):
        context = kwargs["design_context"]
        if context["reviewScope"] == fail_scope and (fail_scope == "global" or context["globalPageIndices"][0] == 5):
            harness.calls.append(("failed", context["reviewScope"]))
            raise error
        return await harness.provider()(renders, **kwargs)

    advisory = harness.review(run_advisory)

    gate = advisory["vision"]["designGate"]
    assert gate["status"] == "unreviewed" and gate["unreviewedPages"] == list(range(7))
    failed_ordinal = "1" if fail_scope == "local" else "2"
    assert harness.groups[failed_ordinal]["status"] == "review_failed"
    assert [group["groupId"] for group in advisory["vision"]["unreviewedGroups"]] == [
        f"phase-0/group-{failed_ordinal}"]
    review, summary, flags = _publish_flags(harness, advisory)
    assert review["status"] == "unavailable" and review["reason"] == "review_call_unavailable"
    assert summary["status"] == "passed"
    assert set(flags.values()) == {None}


@pytest.mark.parametrize("saved_status", ["review_failed", "review_unknown", "review_inflight", "legacy_unknown"])
def test_a_resumed_review_never_buys_an_unfinished_group_again(monkeypatch, saved_status):
    harness = _ReviewHarness(monkeypatch, 2)
    harness.review(harness.provider())
    if saved_status == "legacy_unknown":
        # Markers written before groups kept their input digest.
        harness.groups["0"] = {"status": "review_unknown", "failureDiagnostics": {}}
    else:
        harness.groups["0"]["status"] = saved_status
    harness.calls.clear()

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("an unfinished group must not be bought again")

    advisory = harness.review(forbidden)

    assert harness.calls == []
    assert advisory["vision"]["designGate"]["unreviewedPages"] == [0, 1]


def test_a_resumed_completed_group_is_reused_without_a_call(monkeypatch):
    harness = _ReviewHarness(monkeypatch, 2)
    first = harness.review(harness.provider())

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("a sealed group is reused")

    second = harness.review(forbidden)
    assert second["vision"]["designGate"]["digest"] == first["vision"]["designGate"]["digest"]


def test_an_unfinished_group_of_other_inputs_is_a_conflict(monkeypatch):
    harness = _ReviewHarness(monkeypatch, 2)
    harness.groups["0"] = {"status": "review_unknown", "input_digest": "sha256:other-inputs"}
    with pytest.raises(ValueError, match="svg_quality_recovery_review_group_conflict"):
        harness.review(harness.provider())


def test_tampered_sealed_group_evidence_stays_fatal(monkeypatch):
    harness = _ReviewHarness(monkeypatch, 2)
    harness.review(harness.provider())
    harness.groups["0"]["advisory"]["vision"]["slides"] = [{"index": 0, "issues": ["illegible"]}]
    with pytest.raises(ValueError, match="svg_quality_recovery_review_group_evidence_invalid"):
        harness.review(harness.provider())


@pytest.mark.parametrize("error", [asyncio.CancelledError(), ProviderQuotaPause(reset_at=10, observed_at=0),
                                   ValueError("svg_quality_recovery_authority_mismatch")])
def test_cancellation_deferral_and_integrity_in_a_review_call_still_raise(monkeypatch, error):
    harness = _ReviewHarness(monkeypatch, 2)
    with pytest.raises(type(error)):
        harness.review(harness.provider(fail_scopes={"full"}, error=error))
    marker = harness.groups["0"]
    # Marked unknown, with its input digest, so a resume never buys it again.
    assert marker["status"] == "review_unknown" and marker["input_digest"]


def _recover_with_saved_round(monkeypatch, tmp_path, round_status):
    harness = _RecoveryHarness(monkeypatch, tmp_path, AsyncMock())
    digests = [collect._digest({"svg": p.get("svg_content"), "png": p.get("png_sha256")})
               for p, _ in harness.pages]
    saved_groups = {"0": {"status": "review_unknown", "input_digest": "sha256:any"}}
    harness.pages[0][0]["quality_recovery_execution"] = {
        "schema": "svg-quality-recovery-execution/v1", "binding": "binding",
        "reviewPrinciplesVersion": DESIGN_PRINCIPLES_VERSION,
        "rounds": [{"status": round_status, "page_digests": digests, "groups": saved_groups}]}
    seen = {}

    async def deck_advisory(ctx, pages, **_kwargs):
        seen["group"] = await ctx.quality_recovery_review_group_checkpoint(0)
        return harness.advisory
    harness.quality.deck_advisory = AsyncMock(side_effect=deck_advisory)
    harness.quality.quality_recovery_decisions = lambda *_args: {}
    return harness, seen


@pytest.mark.parametrize("round_status", ["review_unknown", "review_inflight"])
def test_a_resumed_open_round_is_finished_over_its_saved_groups(monkeypatch, tmp_path, round_status):
    harness, seen = _recover_with_saved_round(monkeypatch, tmp_path, round_status)

    _pages, advisory = harness.run()

    assert advisory is harness.advisory
    assert seen["group"]["status"] == "review_unknown"  # handed to request_group, not dropped
    execution = harness.stores.saved[0]["quality_recovery_execution"]
    assert len(execution["rounds"]) == 1 and execution["rounds"][0]["status"] == "completed"


def test_a_saved_round_of_other_pages_is_still_a_conflict(monkeypatch, tmp_path):
    harness, _seen = _recover_with_saved_round(monkeypatch, tmp_path, "review_unknown")
    harness.pages[0][0]["quality_recovery_execution"]["rounds"][0]["page_digests"][0] = "changed"
    with pytest.raises(ValueError, match="svg_quality_recovery_review_conflict"):
        harness.run()


def test_a_saved_round_in_an_unknown_state_is_a_conflict(monkeypatch, tmp_path):
    harness, _seen = _recover_with_saved_round(monkeypatch, tmp_path, "forged_state")
    with pytest.raises(ValueError, match="svg_quality_recovery_review_conflict"):
        harness.run()


# --------------------------------------------------------------------------
# G5: resuming a saved page that cannot be reused as saved
# --------------------------------------------------------------------------


def _binding_ctx(ctx):
    ctx.quality_recovery_input_binding = "binding"
    ctx.quality_recovery_checkpoint_reuse_eligible = True
    return ctx


def _bound(ctx, payload):
    payload["quality_recovery_local_evidence"] = quality_recovery.local_evidence_binding(
        ctx.quality_recovery_input_binding, payload)
    return payload


def test_a_saved_page_without_evidence_is_measured_again(monkeypatch):
    stores = _Stores()
    ctx = _binding_ctx(_ctx())
    payload = _rendered_payload(stores, _AUTHORED, _quality(_AUTHORED, passed=False,
                                                            failures=[{"kind": "text_overlap"}]))
    assert collect._saved_page_reusable(ctx, payload, 1) is False


def test_a_saved_page_with_its_own_evidence_resumes(monkeypatch):
    stores = _Stores()
    ctx = _binding_ctx(_ctx())
    payload = _bound(ctx, _rendered_payload(stores, _AUTHORED, _quality(_AUTHORED)))
    assert collect._saved_page_reusable(ctx, payload, 1) is True


@pytest.mark.parametrize("change", ["other_binding", "changed_bytes", "reuse_refused"])
def test_saved_evidence_bound_to_something_else_is_integrity(monkeypatch, change):
    stores = _Stores()
    ctx = _binding_ctx(_ctx())
    payload = _bound(ctx, _rendered_payload(stores, _AUTHORED, _quality(_AUTHORED)))
    if change == "other_binding":
        ctx.quality_recovery_input_binding = "another-operation"
    elif change == "changed_bytes":
        payload["png_sha256"] = "0" * 64
    else:
        ctx.quality_recovery_checkpoint_reuse_eligible = False
    with pytest.raises(ValueError, match="svg_quality_recovery_local_evidence_stale"):
        collect._saved_page_reusable(ctx, payload, 1)


def test_a_rescued_page_resumes_as_flagged_even_without_evidence():
    stores = _Stores()
    ctx = _binding_ctx(_ctx())
    payload = _rendered_payload(stores, _AUTHORED, _quality(_AUTHORED, passed=False))
    payload["terminal"] = {"index": 1, "reason": "svg:x", "remakeAttempted": False,
                           "remakeOutcome": "verbatim_flag_only"}
    payload = _bound(ctx, payload)
    assert collect._saved_page_reusable(ctx, payload, 1) is True


def test_a_source_page_that_did_not_pass_is_measured_again():
    manifest = manifest_for(_SOURCE_BLOCKS)
    payload = _source_rendered_payload(_Stores(), manifest, [{"kind": "text_overlap"}])
    assert collect._saved_page_reusable(_source_ctx(manifest), payload, 0) is False


def test_a_source_page_with_a_review_flag_resumes_as_flagged():
    manifest = manifest_for(_SOURCE_BLOCKS)
    payload = _source_rendered_payload(_Stores(), manifest, [{"kind": "text_overlap"}])
    payload[deck_quality.REVIEW_FLAG_KEY] = {"index": 0, "reason": "design:unreviewed",
                                             "remakeAttempted": False, "remakeOutcome": "not_attempted"}
    assert collect._saved_page_reusable(_source_ctx(manifest), payload, 0) is True


def test_a_passed_unverified_source_page_resumes_for_publication_to_flag():
    manifest = manifest_for(_SOURCE_BLOCKS)
    payload = _source_rendered_payload(_Stores(), manifest, [])
    payload["quality"]["passed"] = True
    assert payload["source_preservation"].get("rendering_verified") is not True
    assert collect._saved_page_reusable(_source_ctx(manifest), payload, 0) is True


def test_a_saved_source_page_that_is_not_verbatim_is_integrity():
    manifest = manifest_for(_SOURCE_BLOCKS)
    payload = _source_rendered_payload(_Stores(), manifest, [])
    payload["quality"]["passed"] = True
    payload["source_preservation"] = {**payload["source_preservation"], "page_digest": "sha256:other"}
    with pytest.raises(ValueError, match="svg_source_receipt_input_mismatch"):
        collect._saved_page_reusable(_source_ctx(manifest), payload, 0)


def _resume_page(monkeypatch, ctx, stores, saved, *, index):
    """``collect._page`` for one saved page, with the deck preparation stubbed."""
    monkeypatch.setattr("services.svg_generation_inputs.svg_page_input_digest", lambda **_kwargs: "in")
    monkeypatch.setattr(collect, "_digest", lambda value: "in")
    stores.saved[index] = saved
    hooks = SimpleNamespace(checkpoint=stores.checkpoint, read_png=stores.read_png, write_png=stores.write_png,
                            async_session_maker=None)
    state = SimpleNamespace(fatal_error=None, cached_author_render_manifest_hash=None, cache_only_approval=None)
    outline = SimpleNamespace(slides=[SimpleNamespace(content="제목\n- 항목")] * ctx.count)
    request = SimpleNamespace(**vars(_request()), codex_generation_preset=None)
    return asyncio.run(collect._page(ctx, request, None, outline, "ko", None, index, state, hooks))


def test_resuming_a_failed_source_page_measures_it_again_and_rescues_it(monkeypatch):
    """The saved last attempt failed: the loop re-measures the saved SVG (no
    provider call) and the ladder keeps it flagged."""
    renderer = _renderer(monkeypatch, failures=[{"kind": "writing_policy_visible_symbols"}])
    monkeypatch.setattr(collect.author, "attempt_author", AsyncMock(side_effect=AssertionError("no purchase")))
    manifest = manifest_for(_SOURCE_BLOCKS)
    ctx, stores = _source_ctx(manifest), _Stores()
    saved = _source_rendered_payload(stores, manifest, [{"kind": "writing_policy_visible_symbols"}])
    saved["input_digest"] = "in"
    saved["attempts"] = [{"kind": "provider", "receipt": {}}, {"kind": "provider", "receipt": {}}]

    rescued, png = _resume_page(monkeypatch, ctx, stores, saved, index=0)

    assert png is None
    assert rescued["terminal"]["reason"] == "svg:svg_source_quality_failed"
    assert rescued["terminal"]["remakeOutcome"] == "verbatim_flag_only"
    renderer.assert_awaited()  # measured again, not trusted as saved


def test_resuming_a_rescued_source_page_reuses_it(monkeypatch):
    renderer = _renderer(monkeypatch)
    manifest = manifest_for(_SOURCE_BLOCKS)
    ctx, stores = _source_ctx(manifest), _Stores()
    saved = _source_rendered_payload(stores, manifest, [{"kind": "text_overlap"}])
    saved["input_digest"] = "in"
    saved["terminal"] = {"index": 0, "reason": "svg:svg_source_quality_failed", "remakeAttempted": False,
                         "remakeOutcome": "verbatim_flag_only"}

    page, png = _resume_page(monkeypatch, ctx, stores, saved, index=0)

    assert page is saved and png is None
    renderer.assert_not_awaited()


# --------------------------------------------------------------------------
# G5: an append-only cached page that remeasures below its gate
# --------------------------------------------------------------------------


def _append_only(stores, *, status="pending"):
    saved = _rendered_payload(stores, _AUTHORED, _quality(_AUTHORED))
    saved["input_digest"] = "in"
    saved["append_only_cache"] = {"status": status, "lineage": {"child_job_id": "child"},
        "witness": {"source_digest": sha256_hex(_AUTHORED.encode()), "author_digest": collect._digest(
            saved["attempts"]), "input_digest": "in", "native_digest": collect._digest(None)}}
    return saved


@pytest.mark.parametrize("outcome", ["retry", "measured_failure", "gate_error"])
def test_a_failed_append_only_remeasure_is_rescued(monkeypatch, outcome):
    stores = _Stores()
    saved = _append_only(stores)
    monkeypatch.setattr(collect, "_provider_attempt_limit", lambda *_args, **_kwargs: 2)
    _renderer(monkeypatch)

    async def attempt_render(**kwargs):
        payload = kwargs["payload"]
        if outcome == "retry":
            payload["svg_content"] = None
            return render.RETRY
        payload["quality"] = _quality(_AUTHORED, passed=False, failures=[{"kind": "text_overlap"}])
        if outcome == "gate_error":
            raise _stage_error(1, "svg_timeline_caption_ink_failed")
        return payload, b"png"
    monkeypatch.setattr(collect.render, "attempt_render", attempt_render)
    hooks = SimpleNamespace(checkpoint=stores.checkpoint, read_png=stores.read_png, write_png=stores.write_png,
                            async_session_maker=None)

    with pytest.raises(ValueError, match="svg_source_quality_failed") as raised:
        asyncio.run(collect._remeasure_append_only_cache(_ctx(), _request(), None, 1, "제목", saved, "in",
                                                         None, hooks))
    failed = raised.value
    assert isinstance(failed, collect._CachedRemeasureFailed)
    assert terminal.is_exhausted_page(failed.error, 1)
    if outcome == "retry":
        assert "quality" not in failed.payload  # never kept on the earlier measurement
    if outcome == "gate_error":
        assert failed.error.code == "svg_timeline_caption_ink_failed"

    rescued, _png = _rescue(_ctx(), stores, failed.payload, failed.error, index=1)
    expected = "svg_fallback_page" if outcome == "retry" else "verbatim_flag_only"
    assert rescued["terminal"]["remakeOutcome"] == expected


def test_a_rescued_append_only_page_resumes_as_flagged(monkeypatch):
    stores = _Stores()
    saved = _append_only(stores, status="failed")
    saved["terminal"] = {"index": 1, "reason": "svg:svg_source_quality_failed", "remakeAttempted": False,
                         "remakeOutcome": "verbatim_flag_only"}
    monkeypatch.setattr(collect, "_provider_attempt_limit", lambda *_args, **_kwargs: 2)
    monkeypatch.setattr(collect.render, "attempt_render", AsyncMock(side_effect=AssertionError("not remeasured")))
    hooks = SimpleNamespace(checkpoint=stores.checkpoint, read_png=stores.read_png, write_png=stores.write_png,
                            async_session_maker=None)

    page, png = asyncio.run(collect._remeasure_append_only_cache(_ctx(), _request(), None, 1, "제목", saved,
                                                                 "in", None, hooks))
    assert page is saved and png == stores.read_png(saved)


# --------------------------------------------------------------------------
# a partial recovery's known page that remeasures below its gate
# --------------------------------------------------------------------------


def _known_page(stores):
    from services.svg_cached_author_render_recovery import author_witness, digest
    job = SimpleNamespace(workspace_operation_id=uuid.uuid4())
    saved = _rendered_payload(stores, _AUTHORED, _quality(_AUTHORED))
    page = {"row_digest": "row-1", "category": "known", "svg_sha256": sha256_hex(_AUTHORED.encode()),
            "input_digest": "in", "author_receipt_digest": author_witness(saved)}
    manifest = {"pages": [{"category": "new"}, page], "known_indices": [1]}
    saved["partial_recovery"] = {"schema": "svg-partial-recovery-page/v1", "manifest_hash": digest(manifest),
        "slide_index": 1, "source_row_digest": "row-1", "category": "known",
        "operation_id": str(job.workspace_operation_id)}
    return job, saved, manifest


def _remeasure_known(monkeypatch, stores, *, outcome, profile=None):
    job, saved, manifest = _known_page(stores)
    ctx = _ctx(profile=profile)
    ctx.partial_recovery_manifest = manifest

    async def attempt_render(**kwargs):
        payload = kwargs["payload"]
        payload["quality"] = _quality(_AUTHORED, passed=False, failures=[{"kind": "required_text_missing"}])
        await kwargs["checkpoint"](job, 1, payload=payload)
        if outcome != "measured_failure":
            raise _stage_error(1, outcome)
        return payload, stores.read_png(payload)
    monkeypatch.setattr(collect.render, "attempt_render", attempt_render)
    hooks = SimpleNamespace(checkpoint=stores.checkpoint, read_png=stores.read_png, write_png=stores.write_png,
                            async_session_maker=None)
    return saved, asyncio.run(collect._remeasure_partial_cached_page(
        ctx, _request(), None, 1, "제목", saved, "in", job, hooks))


@pytest.mark.parametrize(("outcome", "code"), [
    ("measured_failure", "svg_partial_recovery_cached_quality_failed"),
    ("svg_timeline_caption_ink_failed", "svg_timeline_caption_ink_failed"),
])
def test_a_known_page_that_remeasures_below_its_gate_is_kept_flagged(monkeypatch, outcome, code):
    """The recovery manifest binds a known page's bytes, so no substitute may
    replace it: it is kept, flagged, whatever its failure kind (here a content
    kind the ladder would otherwise replace)."""
    stores = _Stores()
    saved, (page, png) = _remeasure_known(monkeypatch, stores, outcome=outcome)
    assert page["svg_content"] == saved["svg_content"] == _AUTHORED
    assert page["terminal"] == {"index": 1, "reason": f"svg:{code}", "remakeAttempted": False,
                                "remakeOutcome": terminal.FLAG_ONLY_OUTCOME}
    assert stores.saved[1]["terminal"] == page["terminal"]
    assert png == stores.read_png(page) and png is not None


@pytest.mark.parametrize("outcome", ["measured_failure", "svg_asset_screen_derivative_failed"])
def test_a_known_page_stays_fatal_when_the_deck_or_the_error_forbids_a_flag(monkeypatch, outcome):
    stores = _Stores()
    profile = (resolve_svg_runtime_profile(source_reflow=False, raster_reconstruction=True)
               if outcome == "measured_failure" else None)
    with pytest.raises(AuthoredStageError) as raised:
        _remeasure_known(monkeypatch, stores, outcome=outcome, profile=profile)
    expected = "svg_partial_recovery_cached_quality_failed" if outcome == "measured_failure" else outcome
    assert raised.value.code == expected
    assert "terminal" not in stores.saved[1]


# --------------------------------------------------------------------------
# resume and automatic retry read the same rules
# --------------------------------------------------------------------------


@pytest.mark.parametrize("error", [
    ProviderOutcomeError("public_provider_result_unknown", recovery_allowed=False),
    TimeoutError(),
])
def test_a_failed_review_group_marker_is_not_an_unknown_author_call(monkeypatch, error):
    """A review group's markers stay in the deck's review execution record; a
    resume never buys them again, so the author-call guard ignores them."""
    harness = _ReviewHarness(monkeypatch, 2)
    harness.review(harness.provider(fail_scopes={"full"}, error=error))
    marker = harness.groups["0"]
    assert marker["status"] == "review_failed"
    saved = {"svg_content": "<svg/>", "quality_recovery_execution": {
        "rounds": [{"status": "review_inflight", "groups": deepcopy(harness.groups)}],
        "final_selected_review": {"status": "review_unknown", "groups": {"0": {**marker, "status": "review_unknown"}}}}}
    assert collect._unknown_provider_checkpoint(saved) is False
    # An author call whose outcome is unknown still is one.
    assert collect._unknown_provider_checkpoint({**saved, "inflight": 1}) is True
    assert collect._unknown_provider_checkpoint(
        {**saved, "provider_failures": [{"code": "public_provider_result_unknown"}]}) is True


@pytest.mark.parametrize(("code", "retryable"), [
    ("svg_validation_failed", False),
    ("svg_source_paint_occluded", False),  # page quality outside the old three-code list
    ("svg_partial_recovery_cached_quality_failed", False),
    ("svg_asset_screen_derivative_failed", True),  # integrity: not an exhausted page
    ("public_provider_result_unknown", True),  # provider: today's retry path
])
def test_an_automatic_retry_is_refused_after_any_exhausted_page(code, retryable):
    from services.presentation_generation_operation_handler import _generation_failure_decision
    status_error = AuthoredStageError(slide_index=None, stage="author", code="svg_provider_transport_unavailable",
                                      retryable=True).as_safe_dict(request_id=None, attempt_number=1)
    row = _stage_error(1, code).as_safe_dict(request_id=None, attempt_number=1)
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [(1, row)])),
                              rollback=AsyncMock())
    assert asyncio.run(_generation_failure_decision(session, uuid.uuid4(), status_error)) == (
        "svg_provider_transport_unavailable", retryable)
