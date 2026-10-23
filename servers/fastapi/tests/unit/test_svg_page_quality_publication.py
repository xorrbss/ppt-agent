"""Page quality never fails an SVG deck at publication; integrity still does.

The design gate, advisory record, classification and source receipts below are
produced by the real producers (``_seal_design_gate``, ``advisory_record``,
``reclassify_pages``, ``assemble_source_svg``); only the reviewer's answer and
the storage boundary are fixtures.
"""
from __future__ import annotations

import asyncio
import struct
import uuid
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.authored_checkpoint_errors import AuthoredStageError
from services.generation_core.drivers.svg import publish
from services.generation_core.drivers.svg import quality as deck_quality
from services.generation_core.drivers.svg.quality import (
    REVIEW_FLAG_KEY, _candidate_local_valid, _seal_design_gate, apply_quality_recovery_publication,
)
from services.generation_core.quality.advisory_record import advisory_record, content_digest
from services.generation_core.quality.flag_classification import (
    FAILURE_PLACEHOLDER_OUTCOME, REPAIR_NOT_ADMITTED, REVIEW_REQUIRED, classify_flagged_slides,
)
from services.generation_core.quality.terminal_slide_flag_only import flagged_entry
from services.generation_core.quality.vision_advisory_prompt import DESIGN_AXES, DESIGN_REVIEW_PROMPT_VERSION
from services.svg_authoring_profile import ART_DIRECTION_PROFILE_V8
from services.svg_contract import source_digest
from test_svg_source_layout import assemble, manifest_for
from utils.hashing import canonical_json_digest, sha256_hex

PNG = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + struct.pack(">II", 1280, 720)
TENANT = "fixture-tenant"


def _measured(svg, png=PNG, **extra):
    digest = sha256_hex(png)
    return dict(passed=True, verified=True, failures=[], measurement_complete=True,
                source_digest=source_digest(svg), png_digest="sha256:" + digest, **extra)


def _axes(overrides=None):
    axes = {name: dict(verdict="passed", severity="none", repairScope="none",
                       visibleEvidence="Readable source grouping.") for name in DESIGN_AXES}
    for name, (verdict, severity, scope) in (overrides or {}).items():
        axes[name] = dict(verdict=verdict, severity=severity, repairScope=scope,
                          visibleEvidence="The reviewer's visible evidence.")
    return axes


def _deck(monkeypatch, *, count=3, version=1, axes=None, unevaluated=(), targets=None, retained=(),
          selected=(), presentation_id=None):
    """A policy deck as collect hands it to publication: classified, reviewed, sealed."""
    owner = uuid.uuid4()
    monkeypatch.setattr("utils.owner_context.get_current_owner_id", lambda: owner)
    monkeypatch.setattr("utils.owner_context.get_current_tenant_id", lambda: TENANT)
    job = SimpleNamespace(id=uuid.uuid4(), workspace_operation_id=uuid.uuid4(),
                          presentation_id=presentation_id or uuid.uuid4(), attempt_token="attempt",
                          attempt_number=1)
    snapshot = dict(authoring_profile=dict(ART_DIRECTION_PROFILE_V8), canvas={"width": 1280, "height": 720},
                    quality_recovery_policy={"id": "svg-quality-first", "version": version})
    snapshot["digest"] = canonical_json_digest(snapshot, allow_nan=True)
    png_digest = sha256_hex(PNG)
    pages = []
    for index in range(count):
        svg = f"<svg>page {index}</svg>"
        payload = dict(svg_content=svg, png_sha256=png_digest, input_digest=f"input-{index}",
                       attempts=[], quality=_measured(svg))
        if index in retained or index in selected:
            payload["quality_recovery"] = {"status": "retained" if index in retained else "selected"}
        pages.append((payload, PNG))
    summary = deck_quality.reclassify_pages(pages)
    review_pages = []
    for index in range(count):
        page = dict(index=index, evaluated=index not in unevaluated, issues=[],
                    axes=_axes((axes or {}).get(index)))
        if version == 2:
            page["designTargets"] = deepcopy((targets or {}).get(index, []))
        review_pages.append(page)
    vision = dict(slides=[], promptVersion=DESIGN_REVIEW_PROMPT_VERSION, model="fixture-model",
                  reviewedPageIndices=list(range(count)), reviewedPngDigests=[png_digest] * count,
                  designReview=dict(pages=review_pages, deckFlow={"evaluated": True}))
    context = dict(policy=snapshot["quality_recovery_policy"], style={"canvas": snapshot["canvas"]},
        styleDigest=content_digest(snapshot["canvas"]), snapshotDigest=content_digest(snapshot),
        runtimeBinding="fixture-runtime", exampleDigests=[], roles=["content"] * count,
        pages=[dict(index=index, content=f"source {index}") for index in range(count)],
        binding=dict(jobId=str(job.id), operationId=str(job.workspace_operation_id),
                     presentationId=str(job.presentation_id), ownerId=str(owner), tenantId=TENANT),
        priorCandidates={}, deckComplete=True,
        mandatoryEvidence=[bool(_candidate_local_valid(payload)) for payload, _ in pages])
    vision["designGate"] = _seal_design_gate(context, pages, [PNG] * count, vision)
    advisory = advisory_record(vision, [sha256_hex(payload["svg_content"].encode()) for payload, _ in pages])
    return SimpleNamespace(owner=owner, job=job, snapshot=snapshot, pages=pages, advisory=advisory,
                           context=context, vision=vision, summary=summary)


def _flags(pages):
    return {index: (payload[REVIEW_FLAG_KEY]["reason"], payload[REVIEW_FLAG_KEY]["remakeAttempted"],
                    payload[REVIEW_FLAG_KEY]["remakeOutcome"])
            for index, (payload, _) in enumerate(pages) if REVIEW_FLAG_KEY in payload}


# --------------------------------------------------------------------------
# flag vocabulary
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("entry", "expected"), [
    (flagged_entry(index=0, reason="design:information_structure", attempted=False, outcome="not_attempted"),
     REVIEW_REQUIRED),
    (flagged_entry(index=0, reason="design:unreviewed", attempted=False, outcome="not_attempted"), REVIEW_REQUIRED),
    ({**flagged_entry(index=0, reason="svg:x", attempted=False, outcome="verbatim_flag_only"),
      "reasons": ["svg:x", "design:density_balance"]}, REVIEW_REQUIRED),
    (flagged_entry(index=0, reason="svg:x", attempted=False, outcome="verbatim_flag_only"), REPAIR_NOT_ADMITTED),
    (flagged_entry(index=0, reason="designer:not-a-prefix", attempted=False, outcome="not_attempted"),
     REPAIR_NOT_ADMITTED),
])
def test_design_reasons_are_measured_review_requirements(entry, expected):
    [classified] = classify_flagged_slides([entry])
    assert classified["classification"] == expected


def test_page_outcome_keeps_the_terminal_record_and_adds_the_review_reason():
    svg = "<svg>p</svg>"
    payload = dict(svg_content=svg, quality=_measured(svg),
                   terminal=flagged_entry(index=0, reason="svg:x", attempted=False, outcome="verbatim_flag_only"))
    pages = [(payload, PNG)]
    assert deck_quality.reclassify_pages(pages)["status"] == "passed"
    payload[REVIEW_FLAG_KEY] = flagged_entry(index=0, reason="design:hierarchy_emphasis", attempted=False,
                                             outcome="not_attempted")
    summary = deck_quality.reclassify_pages(pages)
    assert summary["status"] == REVIEW_REQUIRED and summary["reviewPages"] == [0]
    assert payload["flag"]["reason"] == "svg:x"
    assert payload["flag"]["reasons"] == ["svg:x", "design:hierarchy_emphasis"]
    assert payload["quality"]["verified"] is False


# --------------------------------------------------------------------------
# apply_quality_recovery_publication: quality verdicts become page flags
# --------------------------------------------------------------------------


_FAILED_STRUCTURE = {"information_structure": ("failed", "major", "design_rewrite")}
_NEEDED_TARGET = [dict(axis="density_balance", evidenceRegion="body", visibleEvidence="Crowded body",
                       sourceEvidence="Current source", improvementGoal="Rebalance density")]


@pytest.mark.parametrize(("case", "deck", "expected"), [
    ("clean", dict(), {}),
    ("blocking_retained", dict(axes={1: _FAILED_STRUCTURE}, retained={1}),
     {1: ("design:information_structure", True, "design_review_retained")}),
    ("blocking_selected", dict(axes={1: _FAILED_STRUCTURE}, selected={1}),
     {1: ("design:information_structure", True, "not_attempted")}),
    ("blocking_two_axes",
     dict(axes={0: {**_FAILED_STRUCTURE, "density_balance": ("advisory", "major", "local_repair")}}),
     {0: ("design:density_balance,information_structure", False, "not_attempted")}),
    ("unreviewed", dict(unevaluated={2}, retained={2}), {2: ("design:unreviewed", False, "not_attempted")}),
    ("improvement_needed", dict(version=2, targets={0: _NEEDED_TARGET}),
     {0: ("design:density_balance", False, "not_attempted")}),
])
def test_quality_verdicts_flag_pages_and_publish(monkeypatch, case, deck, expected):
    fixture = _deck(monkeypatch, **deck)
    result = apply_quality_recovery_publication(fixture.snapshot, fixture.pages, fixture.advisory, job=fixture.job)
    assert result["status"] == "reviewed"
    assert result["gateDigest"] == fixture.vision["designGate"]["digest"]
    assert result["flaggedPages"] == sorted(expected)
    assert _flags(fixture.pages) == expected
    summary = deck_quality.reclassify_pages(fixture.pages, summary=fixture.summary)
    assert summary["reviewPages"] == sorted(expected)
    assert summary["status"] == (REVIEW_REQUIRED if expected else "passed")
    for index, (payload, _) in enumerate(fixture.pages):
        assert payload["quality"]["classification"] == (REVIEW_REQUIRED if index in expected else "passed")
        assert payload["quality"]["verified"] is (index not in expected)


def test_a_page_with_a_residual_failure_is_flagged_not_fatal(monkeypatch):
    # The real b01261c1 shape: every other page passed; this one carries a
    # measured residual, so mandatory evidence fails and the gate blocks it.
    fixture = _deck(monkeypatch, count=2)
    payload = fixture.pages[1][0]
    payload["quality"] = {**payload["quality"], "passed": False, "failures": [{"kind": "text_overlap"}]}
    fixture = _deck_from_pages(fixture)
    apply_quality_recovery_publication(fixture.snapshot, fixture.pages, fixture.advisory, job=fixture.job)
    assert _flags(fixture.pages) == {1: ("design:mandatory_evidence", False, "not_attempted")}
    summary = deck_quality.reclassify_pages(fixture.pages)
    assert summary["reviewPages"] == [1]


def _deck_from_pages(fixture):
    """Reseal ``fixture``'s gate over its current pages, as the review would."""
    deck_quality.reclassify_pages(fixture.pages)
    fixture.context["mandatoryEvidence"] = [bool(_candidate_local_valid(payload)) for payload, _ in fixture.pages]
    vision = {key: value for key, value in fixture.vision.items() if key != "designGate"}
    vision["designGate"] = _seal_design_gate(fixture.context, fixture.pages, [PNG] * len(fixture.pages), vision)
    fixture.vision = vision
    fixture.advisory = advisory_record(vision, [sha256_hex(p["svg_content"].encode()) for p, _ in fixture.pages])
    return fixture


def test_without_a_review_the_deck_publishes_unflagged(monkeypatch):
    fixture = _deck(monkeypatch)
    assert apply_quality_recovery_publication(fixture.snapshot, fixture.pages, None, job=fixture.job) == {
        "status": "unavailable"}
    skipped = advisory_record({"skipped": "mandatory_evidence_invalid"},
                              [sha256_hex(p["svg_content"].encode()) for p, _ in fixture.pages])
    assert apply_quality_recovery_publication(fixture.snapshot, fixture.pages, skipped, job=fixture.job) == {
        "status": "unavailable", "reason": "mandatory_evidence_invalid"}
    assert _flags(fixture.pages) == {}


def test_no_quality_policy_is_a_no_op(monkeypatch):
    fixture = _deck(monkeypatch)
    assert apply_quality_recovery_publication({}, fixture.pages, None) is None


# --------------------------------------------------------------------------
# apply_quality_recovery_publication: integrity stays deck-fatal
# --------------------------------------------------------------------------


def _mutate(case, fixture, monkeypatch):
    snapshot, job, advisory = fixture.snapshot, fixture.job, fixture.advisory
    if case == "snapshot_digest":
        snapshot = {**snapshot, "canvas": {"width": 1280, "height": 721}}
        snapshot["digest"] = canonical_json_digest({k: v for k, v in snapshot.items() if k != "digest"},
                                                   allow_nan=True)
    elif case == "policy":
        # Sealed for this snapshot, but under another policy.
        fixture.context["policy"] = {"id": "svg-quality-first", "version": 2}
        advisory = _deck_from_pages(fixture).advisory
    elif case == "job_binding":
        job = SimpleNamespace(**{**vars(job), "id": uuid.uuid4()})
    elif case == "operation_binding":
        job = SimpleNamespace(**{**vars(job), "workspace_operation_id": uuid.uuid4()})
    elif case == "presentation_binding":
        job = SimpleNamespace(**{**vars(job), "presentation_id": uuid.uuid4()})
    elif case == "owner":
        monkeypatch.setattr("utils.owner_context.get_current_owner_id", lambda: uuid.uuid4())
    elif case == "tenant":
        monkeypatch.setattr("utils.owner_context.get_current_tenant_id", lambda: "other-tenant")
    elif case == "page_digest":
        payload = fixture.pages[0][0]
        payload["svg_content"] = "<svg>changed</svg>"
        payload["quality"]["source_digest"] = source_digest(payload["svg_content"])
    elif case == "png_digest":
        payload = fixture.pages[0][0]
        payload["png_sha256"] = "0" * 64
        payload["quality"]["png_digest"] = "sha256:" + "0" * 64
    elif case == "tampered_gate":
        advisory = deepcopy(advisory)
        advisory["vision"]["designGate"]["blockingPages"] = []
        advisory = advisory_record(advisory["vision"], advisory["pageDigests"])
    elif case == "gate_missing":
        vision = {key: value for key, value in advisory["vision"].items() if key != "designGate"}
        advisory = advisory_record(vision, advisory["pageDigests"])
    elif case == "source_preservation":
        payload = fixture.pages[1][0]
        payload["source_preservation"] = {"contract_version": "svg-source-preservation/v1",
                                          "svg_digest": source_digest("<svg>other</svg>")}
    return snapshot, job, advisory


@pytest.mark.parametrize(("case", "code", "slide_index"), [
    ("snapshot_digest", "svg_quality_recovery_review_required", None),
    ("policy", "svg_quality_recovery_review_required", None),
    ("job_binding", "svg_quality_recovery_review_required", None),
    ("operation_binding", "svg_quality_recovery_review_required", None),
    ("presentation_binding", "svg_quality_recovery_review_required", None),
    ("png_digest", "svg_quality_recovery_review_required", None),
    ("owner", "svg_quality_recovery_review_unavailable", None),
    ("tenant", "svg_quality_recovery_review_unavailable", None),
    ("page_digest", "svg_quality_recovery_review_unavailable", None),
    ("tampered_gate", "svg_quality_recovery_review_unavailable", None),
    ("gate_missing", "svg_quality_recovery_review_unavailable", None),
    ("source_preservation", "svg_quality_recovery_review_required", 1),
])
def test_integrity_violations_still_refuse_the_deck(monkeypatch, case, code, slide_index):
    fixture = _deck(monkeypatch, axes={1: _FAILED_STRUCTURE})
    snapshot, job, advisory = _mutate(case, fixture, monkeypatch)
    with pytest.raises(AuthoredStageError) as refused:
        apply_quality_recovery_publication(snapshot, fixture.pages, advisory, job=job)
    assert (refused.value.code, refused.value.slide_index) == (code, slide_index)


def test_a_failure_placeholder_claims_no_source_receipt(monkeypatch):
    fixture = _deck(monkeypatch)
    payload = fixture.pages[1][0]
    payload["source_preservation"] = {"contract_version": "svg-source-preservation/v1"}
    with pytest.raises(AuthoredStageError):
        apply_quality_recovery_publication(fixture.snapshot, fixture.pages, None, job=fixture.job)
    payload["terminal"] = flagged_entry(index=1, reason="author:placeholder", attempted=False,
                                        outcome=FAILURE_PLACEHOLDER_OUTCOME)
    assert apply_quality_recovery_publication(fixture.snapshot, fixture.pages, None, job=fixture.job) == {
        "status": "unavailable"}


# --------------------------------------------------------------------------
# _publish end to end, at the storage boundary
# --------------------------------------------------------------------------


class _Session:
    def __init__(self, presentation, parent=None):
        self.rows, self.presentation = [], presentation
        self.get = AsyncMock(return_value=parent)
        self.rollback, self.flush, self.execute, self.commit = (AsyncMock() for _ in range(4))

    async def scalar(self, _statement):
        return self.presentation

    def add(self, row):
        self.rows.append(row)


def _storage(monkeypatch):
    revision = AsyncMock()
    monkeypatch.setattr("services.account_deletion_service.require_active_account_generation", AsyncMock())
    monkeypatch.setattr("services.presentation_publication_service.publish_presentation_revision", revision)
    monkeypatch.setattr("services.svg_source_input_snapshot.publish_source_input_snapshot", AsyncMock())
    monkeypatch.setattr("services.svg_quality_recovery_review.saved_review_runtime", lambda: {"fixture": "runtime"})
    monkeypatch.setattr(publish, "begin_publish", AsyncMock())
    monkeypatch.setattr(publish, "complete_publish", AsyncMock())
    def images(_presentation_id, items, _token):
        return [f"image-{index}.png" for index in range(len(items))]
    monkeypatch.setattr(publish, "_save_slide_pngs", images)
    monkeypatch.setattr(publish, "_save_source_page_pngs", images)
    return revision


def _request(**extra):
    return SimpleNamespace(content="deck", authored_style="default", instructions=None, reference_snapshot_ids=[],
                           codex_generation_preset=None, aspect_ratio="16:9", **extra)


def _outline(count):
    return SimpleNamespace(slides=[SimpleNamespace(content=f"source {index}") for index in range(count)],
                           model_dump=lambda **_kwargs: {})


def _published(session):
    presentation = session.presentation
    slides = sorted((row for row in session.rows if isinstance(row, SlideModel)), key=lambda row: row.index)
    return presentation.theme["svg"], slides


def test_a_blocking_page_publishes_the_deck_flagged(monkeypatch):
    presentation_id = uuid.uuid4()
    fixture = _deck(monkeypatch, axes={1: _FAILED_STRUCTURE}, retained={1}, presentation_id=presentation_id)
    revision = _storage(monkeypatch)
    presentation = PresentationModel(id=presentation_id, owner_id=fixture.owner, content="deck", n_slides=3,
                                     mode="svg", revision=0)
    session = _Session(presentation, SimpleNamespace(owner_id=fixture.owner, presentation_id=presentation_id,
                                                     owner_scope=fixture.owner))
    asyncio.run(publish._publish(_request(), presentation_id, _outline(3), "ko", session, fixture.job,
        fixture.snapshot, ["content"] * 3, fixture.pages, quality_summary=fixture.summary, advisory=fixture.advisory))

    svg, slides = _published(session)
    assert svg["qualityStatus"] == REVIEW_REQUIRED and svg["reviewPages"] == [1]
    assert svg["qualitySummary"]["status"] == REVIEW_REQUIRED and svg["qualitySummary"]["reviewPages"] == [1]
    [entry] = svg["flaggedSlides"]
    assert entry["index"] == 1 and entry["slideId"] == str(slides[1].id)
    assert (entry["reason"], entry["classification"], entry["remakeOutcome"]) == (
        "design:information_structure", REVIEW_REQUIRED, "design_review_retained")
    assert svg["designReview"]["status"] == "reviewed" and svg["designReview"]["flaggedPages"] == [1]
    receipt = slides[1].content["svgReceipt"]
    assert receipt[REVIEW_FLAG_KEY]["reason"] == "design:information_structure"
    assert receipt["quality"]["classification"] == REVIEW_REQUIRED and receipt["quality"]["verified"] is False
    assert slides[0].content["svgReceipt"]["quality"]["verified"] is True
    # The producer evidence still binds the published deck to its intact gate.
    gate_digest = fixture.vision["designGate"]["digest"]
    assert svg["qualityRecoveryPublication"]["gateDigest"] == gate_digest
    assert svg["advisory"]["savedPageReviewEvidence"]["gateDigest"] == gate_digest
    assert len(svg["pageAdvisories"]) == 3 and svg["advisory"]["operationBinding"]["sourceRevision"] == 1
    result = revision.await_args.kwargs["result"]
    assert result["quality_status"] == REVIEW_REQUIRED and result["quality_pages"] == {"passed": 2, "flagged": 1}


def test_a_deck_without_a_review_publishes_with_design_review_unavailable(monkeypatch):
    presentation_id = uuid.uuid4()
    fixture = _deck(monkeypatch, presentation_id=presentation_id)
    revision = _storage(monkeypatch)
    presentation = PresentationModel(id=presentation_id, owner_id=fixture.owner, content="deck", n_slides=3,
                                     mode="svg", revision=0)
    session = _Session(presentation)
    asyncio.run(publish._publish(_request(), presentation_id, _outline(3), "ko", session, fixture.job,
        fixture.snapshot, ["content"] * 3, fixture.pages, quality_summary=fixture.summary, advisory=None))

    svg, slides = _published(session)
    assert svg["designReview"] == {"status": "unavailable"}
    assert svg["qualityStatus"] == "passed" and svg["reviewPages"] == []
    assert not any(key in svg for key in ("qualityRecoveryPublication", "pageAdvisories", "advisory"))
    session.get.assert_not_awaited()
    assert all(REVIEW_FLAG_KEY not in slide.content["svgReceipt"] for slide in slides)
    assert revision.await_args.kwargs["result"]["quality_status"] == "passed"


def test_a_stage_review_flag_carried_in_publishes_flagged(monkeypatch):
    # collect._flag_stage_review re-derives this flag from the persisted stage
    # record on every run; publication keeps it (W4 decision, 2026-10-23).
    presentation_id = uuid.uuid4()
    fixture = _deck(monkeypatch, presentation_id=presentation_id)
    staged = fixture.pages[0][0]
    staged[REVIEW_FLAG_KEY] = flagged_entry(index=0, reason="design:hierarchy_emphasis", attempted=False,
                                            outcome="not_attempted")
    summary = deck_quality.reclassify_pages(fixture.pages)
    assert summary["reviewPages"] == [0]
    _storage(monkeypatch)
    session = _Session(PresentationModel(id=presentation_id, owner_id=fixture.owner, content="deck", n_slides=3,
                                         mode="svg", revision=0))
    asyncio.run(publish._publish(_request(), presentation_id, _outline(3), "ko", session, fixture.job,
        fixture.snapshot, ["content"] * 3, fixture.pages, quality_summary=summary, advisory=None))
    svg, slides = _published(session)
    assert svg["reviewPages"] == [0] and svg["qualityStatus"] == "review_required"
    assert slides[0].content["svgReceipt"][REVIEW_FLAG_KEY]["reason"] == "design:hierarchy_emphasis"


# --------------------------------------------------------------------------
# source reflow: residuals and unverified readback publish flagged
# --------------------------------------------------------------------------


def _source_page(manifest, *, rendering_verified=True, passed=True):
    result = assemble(manifest)
    receipt = deepcopy(result.receipt)
    if rendering_verified:
        receipt["rendering_verified"] = True  # what render.py records after the readback
    quality = _measured(result.svg_content)
    if not passed:
        quality.update(passed=False, failures=[{"kind": "text_overlap"}])
    return dict(svg_content=result.svg_content, quality=quality, source_preservation=receipt,
                png_sha256=sha256_hex(PNG), attempts=[])


@pytest.mark.parametrize(("case", "expected_reason"), [
    ("clean", None),
    ("rendering_unverified", "source_visibility"),
    ("residual", "quality:text_overlap"),
    ("placeholder", "author:placeholder"),
])
def test_source_reflow_pages_publish_flagged(monkeypatch, case, expected_reason):
    manifest = manifest_for([("text", {"text": "원문 그대로 125%"})])
    if case == "placeholder":
        payload = dict(svg_content="<svg>placeholder</svg>", quality=_measured("<svg>placeholder</svg>"),
                       png_sha256=sha256_hex(PNG), attempts=[],
                       terminal=flagged_entry(index=0, reason="author:placeholder", attempted=False,
                                              outcome=FAILURE_PLACEHOLDER_OUTCOME))
    else:
        payload = _source_page(manifest, rendering_verified=case != "rendering_unverified",
                               passed=case != "residual")
    pages = [(payload, None)]
    summary = deck_quality.reclassify_pages(pages)
    owner, presentation_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr("utils.owner_context.get_current_owner_id", lambda: owner)
    _storage(monkeypatch)
    session = _Session(PresentationModel(id=presentation_id, owner_id=owner, content="deck", n_slides=1,
                                         mode="svg", revision=0))
    request = _request(_source_content_svg_reflow=True, _source_content_manifest=manifest)
    asyncio.run(publish._publish(request, presentation_id, _outline(1), "ko", session, None, {}, ["content"],
                                 pages, quality_summary=summary))
    svg, slides = _published(session)
    if expected_reason is None:
        assert svg["reviewPages"] == [] and svg["flaggedSlides"] == []
        return
    assert svg["qualityStatus"] == REVIEW_REQUIRED and svg["reviewPages"] == [0]
    [entry] = svg["flaggedSlides"]
    assert entry["reason"] == expected_reason and entry["slideId"] == str(slides[0].id)
    assert entry["classification"] == REVIEW_REQUIRED


def test_source_reflow_still_refuses_a_page_that_is_not_the_source(monkeypatch):
    manifest = manifest_for([("text", {"text": "원문"})])
    payload = _source_page(manifest)
    payload["svg_content"] = payload["svg_content"].replace("원문", "요약")
    payload["quality"] = _measured(payload["svg_content"])
    payload["terminal"] = flagged_entry(index=0, reason="svg:text_overlap", attempted=False,
                                        outcome="verbatim_flag_only")
    request = _request(_source_content_svg_reflow=True, _source_content_manifest=manifest)
    with pytest.raises(ValueError, match="svg_source_"):
        asyncio.run(publish._publish(request, uuid.uuid4(), _outline(1), "ko", None, None, {}, ["content"],
                                     [(payload, None)]))
