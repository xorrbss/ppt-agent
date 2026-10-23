"""A flagged SVG deck exports, is re-reviewed and is edited; integrity still refuses.

Decks are published by the real ``_publish`` over gates sealed by the real
``_seal_design_gate`` (fixtures shared with ``test_svg_page_quality_publication``);
export admission, the saved review, flag replacement, classification and the
deck status fields are the production functions. Only the reviewer's answer,
the database rows and the storage boundary are fixtures.
"""
from __future__ import annotations

import asyncio
import uuid
from copy import deepcopy
from types import SimpleNamespace

import pytest

from models.sql.presentation import PresentationModel
from services.generation_core.drivers.svg import publish
from services.generation_core.drivers.svg import quality as deck_quality
from services.generation_core.drivers.svg.quality import (
    REVIEW_FLAG_KEY, _candidate_local_valid, apply_quality_recovery_publication,
)
from services.generation_core.quality.advisory_record import content_digest
from services.generation_core.quality.flag_classification import (
    FAILURE_PLACEHOLDER_OUTCOME, REVIEW_REQUIRED, SOURCE_VISIBILITY_REASON,
)
from services.generation_core.quality.terminal_slide_flag_only import flagged_entry
from services.generation_core.quality.vision_advisory_prompt import DESIGN_REVIEW_PROMPT_VERSION
from services import svg_export_operation as export_operation
from services import svg_quality_recovery_review as saved_review
from services.public_export_errors import PublicExportOperationError
from services.svg_contract import source_digest
from services.svg_export_source_preservation import source_preservation_receipts, sealed_whole_page_manifest
from services.svg_export_visual_review import svg_visual_review
from services.svg_page_publication import (
    classify_svg_page_quality, set_svg_page, svg_deck_quality_fields,
)
from test_svg_page_quality_publication import (
    PNG, _FAILED_STRUCTURE, _Session, _axes, _deck, _measured, _outline, _published, _request, _source_page,
    _storage,
)
from test_svg_source_layout import manifest_for
from utils.hashing import sha256_hex


def _publish_deck(monkeypatch, **deck):
    """Publish a policy deck through ``_publish`` and return its rows at revision 1."""
    presentation_id = uuid.uuid4()
    fixture = _deck(monkeypatch, presentation_id=presentation_id, **deck)
    _storage(monkeypatch)
    presentation = PresentationModel(id=presentation_id, owner_id=fixture.owner, content="deck", n_slides=3,
                                     mode="svg", revision=0)
    session = _Session(presentation, SimpleNamespace(owner_id=fixture.owner, presentation_id=presentation_id,
                                                     owner_scope=fixture.owner))
    advisory = deck.pop("advisory", fixture.advisory) if "advisory" in deck else fixture.advisory
    asyncio.run(publish._publish(_request(), presentation_id, _outline(3), "ko", session, fixture.job,
        fixture.snapshot, ["content"] * 3, fixture.pages, quality_summary=fixture.summary, advisory=advisory))
    _, slides = _published(session)
    presentation.revision = 1  # what publish_presentation_revision records
    for slide in slides:
        slide.speaker_note = ""
    return fixture, presentation, slides


def _unreviewed_deck(monkeypatch):
    presentation_id = uuid.uuid4()
    fixture = _deck(monkeypatch, presentation_id=presentation_id)
    _storage(monkeypatch)
    presentation = PresentationModel(id=presentation_id, owner_id=fixture.owner, content="deck", n_slides=3,
                                     mode="svg", revision=0)
    session = _Session(presentation)
    asyncio.run(publish._publish(_request(), presentation_id, _outline(3), "ko", session, fixture.job,
        fixture.snapshot, ["content"] * 3, fixture.pages, quality_summary=fixture.summary, advisory=None))
    _, slides = _published(session)
    presentation.revision = 1
    return fixture, presentation, slides


# --------------------------------------------------------------------------
# 1. export admission: interactive exports a flagged deck, release-v1 refuses
# --------------------------------------------------------------------------


def test_a_flagged_deck_passes_interactive_export_admission_and_release_refuses(monkeypatch):
    _, presentation, slides = _publish_deck(monkeypatch, axes={1: _FAILED_STRUCTURE}, retained={1})
    assert slides[1].content["svgReceipt"]["quality"]["verified"] is False
    publication = export_operation._quality_recovery_export_gate(presentation, slides)
    assert publication == presentation.theme["svg"]["qualityRecoveryPublication"]
    assert isinstance(export_operation.svg_export_admission_snapshot(presentation, slides), str)
    with pytest.raises(ValueError, match="svg_export_published_source_required"):
        export_operation.svg_export_admission_snapshot(presentation, slides, require_passed=True)
    with pytest.raises(ValueError, match="svg_quality_recovery_export_review_required"):
        export_operation._quality_recovery_export_gate(presentation, slides, require_passed=True)
    # The receipt never reports a flagged deck as passed.
    review = svg_visual_review(slides=slides, advisory=presentation.theme["svg"]["advisory"])
    assert review["status"] == "failed"


def test_a_clean_deck_still_passes_the_release_gate(monkeypatch):
    _, presentation, slides = _publish_deck(monkeypatch)
    assert export_operation._quality_recovery_export_gate(presentation, slides, require_passed=True)
    assert export_operation.svg_export_admission_snapshot(presentation, slides, require_passed=True)


def test_a_deck_without_a_design_review_exports_interactively(monkeypatch):
    _, presentation, slides = _unreviewed_deck(monkeypatch)
    assert presentation.theme["svg"]["designReview"] == {"status": "unavailable"}
    assert export_operation._quality_recovery_export_gate(presentation, slides) is None
    assert export_operation.svg_export_admission_snapshot(presentation, slides)
    asyncio.run(export_operation.require_quality_recovery_export_parent(None, presentation, slides))
    with pytest.raises(ValueError, match="svg_quality_recovery_export_review_required"):
        export_operation._quality_recovery_export_gate(presentation, slides, require_passed=True)
    assert svg_visual_review(slides=slides, advisory=presentation.theme["svg"].get("advisory"))["status"] == "failed"


def test_a_publication_an_edit_superseded_is_history_not_a_refusal(monkeypatch):
    _, presentation, slides = _publish_deck(monkeypatch, axes={1: _FAILED_STRUCTURE})
    presentation.revision = 2
    assert export_operation._quality_recovery_export_gate(presentation, slides) is None
    with pytest.raises(ValueError, match="svg_quality_recovery_export_review_required"):
        export_operation._quality_recovery_export_gate(presentation, slides, require_passed=True)


@pytest.mark.parametrize(("case", "code"), [
    ("publication_digest", "svg_quality_recovery_export_review_required"),
    ("future_revision", "svg_quality_recovery_export_review_required"),
    ("parent_identity", "svg_quality_recovery_export_parent_invalid"),
    ("page_bytes", "svg_quality_recovery_export_review_required"),
    ("owner", "svg_quality_recovery_export_review_required"),
    ("snapshot", "svg_authoring_profile_snapshot_digest_mismatch"),
])
def test_integrity_still_refuses_an_interactive_export(monkeypatch, case, code):
    _, presentation, slides = _publish_deck(monkeypatch, axes={1: _FAILED_STRUCTURE})
    svg = presentation.theme["svg"]
    if case == "publication_digest":
        svg["qualityRecoveryPublication"]["sourceContentDigests"] = []
    elif case == "future_revision":
        presentation.revision = 0
    elif case == "parent_identity":
        svg["qualityRecoveryPublication"]["parentJobId"] = "not-a-uuid"
    elif case == "page_bytes":
        receipt = slides[0].content["svgReceipt"]
        slides[0].svg_content = "<svg>tampered</svg>"
        receipt["quality"]["source_digest"] = source_digest(slides[0].svg_content)
    elif case == "owner":
        monkeypatch.setattr("utils.owner_context.get_current_owner_id", lambda: uuid.uuid4())
    else:
        svg["styleSnapshot"] = {**svg["styleSnapshot"], "canvas": {"width": 1280, "height": 721}}
    with pytest.raises(ValueError, match=code):
        export_operation._quality_recovery_export_gate(presentation, slides)


def test_a_measurement_that_does_not_describe_the_bytes_refuses_every_policy(monkeypatch):
    _, presentation, slides = _unreviewed_deck(monkeypatch)
    slides[2].content["svgReceipt"]["quality"]["source_digest"] = source_digest("<svg>other</svg>")
    for require_passed in (False, True):
        with pytest.raises(ValueError, match="svg_export_published_source_required"):
            export_operation.svg_export_admission_snapshot(presentation, slides, require_passed=require_passed)


def test_a_superseded_v2_review_carries_no_producer_into_the_export_receipt(monkeypatch):
    _, presentation, slides = _publish_deck(monkeypatch)
    publication = dict(presentation.theme["svg"]["qualityRecoveryPublication"],
                       schema="svg-quality-recovery-publication/v2", reviewOperationId=str(uuid.uuid4()))
    publication.pop("digest")
    presentation.theme["svg"]["qualityRecoveryPublication"] = {**publication, "digest": content_digest(publication)}
    presentation.revision = 2

    class NoHeaderAuthority:
        async def scalars(self, _statement):
            return SimpleNamespace(all=lambda: [])

    context = asyncio.run(export_operation.svg_source_review_context(NoHeaderAuthority(), presentation, slides))
    assert context["producer"] is None


# --------------------------------------------------------------------------
# 2. source pages: a flagged quality outcome exports, a mismatch stays fatal
# --------------------------------------------------------------------------


def _source_deck(monkeypatch, *, case):
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
    _, slides = _published(session)
    return manifest, session.presentation, slides


def _canvas(slides):
    receipt = slides[0].content["svgReceipt"].get("source_preservation") or {"canvas": {"width": 1280, "height": 720}}
    return SimpleNamespace(width_px=receipt["canvas"]["width"], height_px=receipt["canvas"]["height"])


@pytest.mark.parametrize("case", ["clean", "rendering_unverified", "residual", "placeholder"])
def test_flagged_source_pages_pass_export_and_mutation_admission(monkeypatch, case):
    from services.svg_document_mutations import _preserve_source_pages
    manifest, presentation, slides = _source_deck(monkeypatch, case=case)
    receipts = source_preservation_receipts(presentation, slides, _canvas(slides))
    assert (receipts[0] is None) is (case == "placeholder")
    assert _preserve_source_pages(presentation, slides, _canvas(slides), source_manifest=manifest) is True
    claim = SimpleNamespace(owner_id=presentation.owner_id, owner_scope="scope")
    if case == "placeholder":
        assert asyncio.run(sealed_whole_page_manifest(None, claim=claim, presentation=presentation,
                                                      slides=slides, receipts=receipts)) is None


def test_an_unverified_source_page_without_its_flag_stays_refused(monkeypatch):
    from services.svg_document_mutations import SourcePublicationBindingRejected, _preserve_source_pages
    manifest, presentation, slides = _source_deck(monkeypatch, case="rendering_unverified")
    receipt = slides[0].content["svgReceipt"]
    flag = receipt[REVIEW_FLAG_KEY]
    assert SOURCE_VISIBILITY_REASON in (flag.get("reasons") or [flag["reason"]])
    for key in (REVIEW_FLAG_KEY, "flag"):
        receipt.pop(key)
    with pytest.raises(PublicExportOperationError, match="svg_source_export_rendering_unverified"):
        source_preservation_receipts(presentation, slides, _canvas(slides))
    with pytest.raises(SourcePublicationBindingRejected):
        _preserve_source_pages(presentation, slides, _canvas(slides), source_manifest=manifest)


def test_a_source_receipt_that_does_not_bind_the_bytes_stays_refused(monkeypatch):
    from services.svg_document_mutations import SourcePublicationBindingRejected, _preserve_source_pages
    manifest, presentation, slides = _source_deck(monkeypatch, case="residual")
    slides[0].svg_content = slides[0].svg_content.replace("원문", "요약")
    with pytest.raises(PublicExportOperationError, match="svg_source_export_receipt_input_mismatch"):
        source_preservation_receipts(presentation, slides, _canvas(slides))
    with pytest.raises(SourcePublicationBindingRejected):
        _preserve_source_pages(presentation, slides, _canvas(slides), source_manifest=manifest)


def test_a_missing_receipt_on_a_page_that_is_not_a_placeholder_stays_refused(monkeypatch):
    _, presentation, slides = _source_deck(monkeypatch, case="placeholder")
    slides[0].content["svgReceipt"]["terminal"]["remakeOutcome"] = "verbatim_flag_only"
    with pytest.raises(PublicExportOperationError, match="svg_source_export_receipt_required"):
        source_preservation_receipts(presentation, slides, _canvas(slides))


# --------------------------------------------------------------------------
# 3. re-review: works on a flagged deck and replaces the design verdict
# --------------------------------------------------------------------------


def _prepare(monkeypatch, presentation, slides, **kwargs):
    monkeypatch.setattr("services.svg_authoring_profile.read_quality_recovery_plan",
                        lambda _snapshot: dict(slide_count=3, review_group_size=5))
    # The production runtime names its worker; the shared publish fixture's runtime does not.
    monkeypatch.setattr(saved_review, "saved_review_runtime",
                        lambda: {"fixture": "runtime", "worker": {"checkpointReuseEligible": False}})
    return saved_review.prepare_saved_review(presentation, slides, [], [PNG] * len(slides),
                                             operation_id=uuid.uuid4(), owner_scope="scope", **kwargs)


def _answer(prepared, axes=None):
    """The reviewer's parsed answer: a local and a global judgement of every page."""
    count = len(prepared["pages"])
    pages = [dict(index=index, evaluated=True, issues=[], axes=_axes((axes or {}).get(index)))
             for index in range(count)]
    return dict(slides=[], promptVersion=DESIGN_REVIEW_PROMPT_VERSION, model="fixture-model",
                reviewedPageIndices=list(range(count)), reviewedPngDigests=[sha256_hex(PNG)] * count,
                designReview=dict(localPages=deepcopy(pages), pages=pages, deckFlow={"evaluated": True}))


def test_a_saved_review_judges_a_flagged_page_on_its_measurement(monkeypatch):
    _, presentation, slides = _publish_deck(monkeypatch, axes={1: _FAILED_STRUCTURE})
    payload = {**slides[1].content["svgReceipt"], "svg_content": slides[1].svg_content}
    assert _candidate_local_valid(payload) is False  # the cycle: verified carries the old verdict
    prepared = _prepare(monkeypatch, presentation, slides)
    assert prepared["context"]["mandatoryEvidence"] == [True, True, True]


def test_a_measurement_that_no_longer_describes_the_page_refuses_the_review(monkeypatch):
    _, presentation, slides = _publish_deck(monkeypatch, axes={1: _FAILED_STRUCTURE})
    slides[0].content["svgReceipt"]["quality"]["png_digest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="svg_quality_review_measurement_stale"):
        _prepare(monkeypatch, presentation, slides)


def test_a_deck_without_a_publication_is_reviewed_under_its_generation(monkeypatch):
    fixture, presentation, slides = _unreviewed_deck(monkeypatch)
    with pytest.raises(ValueError, match="svg_quality_recovery_review_parent_invalid"):
        _prepare(monkeypatch, presentation, slides)
    job = SimpleNamespace(id=fixture.job.id)
    operation = SimpleNamespace(id=fixture.job.workspace_operation_id, result_revision=1)

    class Rows:
        def __init__(self, rows):
            self.rows = rows

        async def execute(self, _statement):
            return SimpleNamespace(all=lambda: self.rows)

    parent = asyncio.run(saved_review.generation_source_parent(Rows([(job, operation)]), presentation))
    assert parent["parentJobId"] == str(job.id) and parent["sourceGenerationRevision"] == 1
    with pytest.raises(ValueError, match="parent_invalid"):
        asyncio.run(saved_review.generation_source_parent(Rows([]), presentation))
    prepared = _prepare(monkeypatch, presentation, slides, source_parent=parent)
    assert prepared["publication"] == parent


@pytest.mark.parametrize(("axes", "expected_status", "expected_reason"), [
    (None, "passed", None),
    ({1: _FAILED_STRUCTURE}, REVIEW_REQUIRED, "design:information_structure"),
])
def test_a_completed_review_replaces_the_design_flag(monkeypatch, axes, expected_status, expected_reason):
    _, presentation, slides = _publish_deck(monkeypatch, axes={1: {"density_balance": ("failed", "major",
                                                                                        "local_repair")}})
    assert slides[1].content["svgReceipt"][REVIEW_FLAG_KEY]["reason"] == "design:density_balance"
    prepared = _prepare(monkeypatch, presentation, slides)
    review_id = uuid.uuid4()
    record, _ = saved_review.seal_saved_review(prepared, slides, _answer(prepared, axes),
                                               SimpleNamespace(expected_revision=1, operation_id=review_id))
    gate = record["vision"]["designGate"]
    changed, design_review = saved_review.apply_saved_review_flags(slides, gate)
    assert changed == [1]
    assert design_review["status"] == "reviewed" and design_review["gateDigest"] == gate["digest"]
    assert design_review["flaggedPages"] == ([] if expected_reason is None else [1])
    receipt = slides[1].content["svgReceipt"]
    if expected_reason is None:
        assert REVIEW_FLAG_KEY not in receipt and "flag" not in receipt
        assert receipt["quality"]["verified"] is True
    else:
        assert receipt[REVIEW_FLAG_KEY]["reason"] == expected_reason
        assert receipt["quality"]["classification"] == REVIEW_REQUIRED
    fields = svg_deck_quality_fields(slides, presentation.theme["svg"])
    assert fields["qualityStatus"] == expected_status
    assert [entry["slideId"] for entry in fields["flaggedSlides"]] == (
        [] if expected_reason is None else [str(slides[1].id)])
    # The page evidence the review sealed still describes the flagged pages.
    assert record["savedPageReviewEvidence"] == saved_review.seal_saved_page_evidence(
        slides, prepared["snapshot"], gate, runtime=prepared["runtime"])


def test_a_review_that_judged_no_page_keeps_every_page_verdict(monkeypatch):
    # A gate with every page unreviewed attributes nothing: no page gets
    # design:unreviewed, the deck reports the review unavailable once.
    _, presentation, slides = _publish_deck(monkeypatch, axes={1: _FAILED_STRUCTURE})
    before = [deepcopy(slide.content["svgReceipt"]) for slide in slides]
    prepared = _prepare(monkeypatch, presentation, slides)
    answer = _answer(prepared)
    answer["designReview"].pop("localPages")
    record, _ = saved_review.seal_saved_review(prepared, slides, answer,
                                               SimpleNamespace(expected_revision=1, operation_id=uuid.uuid4()))
    gate = record["vision"]["designGate"]
    assert gate["unreviewedPages"] == [0, 1, 2]
    changed, design_review = saved_review.apply_saved_review_flags(slides, gate)
    assert changed == [] and [slide.content["svgReceipt"] for slide in slides] == before
    assert design_review == {"status": "unavailable", "reason": "review_incomplete", "gateDigest": gate["digest"]}
    assert svg_deck_quality_fields(slides, presentation.theme["svg"])["reviewPages"] == [1]


def test_a_review_keeps_a_flag_reason_that_is_not_its_verdict(monkeypatch):
    _, presentation, slides = _publish_deck(monkeypatch, axes={1: _FAILED_STRUCTURE})
    receipt = slides[1].content["svgReceipt"]
    receipt[REVIEW_FLAG_KEY] = {**receipt[REVIEW_FLAG_KEY],
                                "reasons": ["design:information_structure", SOURCE_VISIBILITY_REASON]}
    prepared = _prepare(monkeypatch, presentation, slides)
    record, _ = saved_review.seal_saved_review(prepared, slides, _answer(prepared),
                                               SimpleNamespace(expected_revision=1, operation_id=uuid.uuid4()))
    saved_review.apply_saved_review_flags(slides, record["vision"]["designGate"])
    flag = slides[1].content["svgReceipt"][REVIEW_FLAG_KEY]
    assert flag["reasons"] == [SOURCE_VISIBILITY_REASON] and flag["reason"] == SOURCE_VISIBILITY_REASON
    assert svg_deck_quality_fields(slides, presentation.theme["svg"])["reviewPages"] == [1]


def test_a_later_review_verdict_does_not_revoke_a_header_revision(monkeypatch):
    _, _, slides = _publish_deck(monkeypatch, axes={1: _FAILED_STRUCTURE})
    assert export_operation._verified_apart_from_design_review(slides[1]) is True
    slides[1].content["svgReceipt"]["terminal"] = flagged_entry(
        index=1, reason="author:attempts_exhausted", attempted=False, outcome="svg_fallback_page")
    assert export_operation._verified_apart_from_design_review(slides[1]) is False


# --------------------------------------------------------------------------
# 4. edit lifecycle: changed bytes drop the design verdict
# --------------------------------------------------------------------------


def _design_receipt(svg):
    flag = flagged_entry(index=0, reason="design:information_structure", attempted=False, outcome="not_attempted")
    return {"source_digest": sha256_hex(svg.encode()), REVIEW_FLAG_KEY: flag,
            "flag": {**flag, "classification": REVIEW_REQUIRED}, "png_sha256": sha256_hex(PNG),
            "quality": {**_measured(svg), "verified": False, "classification": REVIEW_REQUIRED}}


def test_new_bytes_are_classified_without_the_old_design_verdict():
    old, new = "<svg>old</svg>", "<svg>new</svg>"
    receipt = _design_receipt(old)
    same = classify_svg_page_quality(svg_content=old, quality=_measured(old), index=0, png_path="p.png",
                                     receipt=receipt)
    assert same["classification"] == REVIEW_REQUIRED
    edited = classify_svg_page_quality(svg_content=new, quality=_measured(new), index=0, png_path="p.png",
                                       receipt=receipt)
    assert edited["classification"] == "passed" and edited["verified"] is True
    receipt[REVIEW_FLAG_KEY]["reasons"] = ["design:information_structure", SOURCE_VISIBILITY_REASON]
    kept = classify_svg_page_quality(svg_content=new, quality=_measured(new), index=0, png_path="p.png",
                                     receipt=receipt)
    assert kept["classification"] == REVIEW_REQUIRED


def test_publishing_new_bytes_drops_the_design_flag_from_the_receipt():
    old, new = "<svg>old</svg>", "<svg>new</svg>"
    quality = classify_svg_page_quality(svg_content=new, quality=_measured(new), index=0, png_path="p.png",
                                        receipt=_design_receipt(old))
    fields = {}
    set_svg_page(fields, svg_content=new, content={"svgReceipt": _design_receipt(old)}, quality=quality, png=PNG)
    receipt = fields["content"]["svgReceipt"]
    assert REVIEW_FLAG_KEY not in receipt and "flag" not in receipt
    unchanged = {}
    set_svg_page(unchanged, svg_content=old, content={"svgReceipt": _design_receipt(old)})
    assert unchanged["content"]["svgReceipt"][REVIEW_FLAG_KEY]["reason"] == "design:information_structure"


@pytest.mark.parametrize("policy", [False, True])
def test_a_remake_clears_the_design_flag_and_rebuilds_the_deck_status(monkeypatch, policy):
    import test_svg_page_remake as remake_tests
    from services import svg_page_remake as remake
    session, presentation, target, task, capture = remake_tests._harness(monkeypatch)
    old = target.svg_content
    flag = flagged_entry(index=0, reason="design:information_structure", attempted=False, outcome="not_attempted")
    target.content["svgReceipt"].update({REVIEW_FLAG_KEY: flag, "flag": {**flag, "classification": REVIEW_REQUIRED},
                                         "source_digest": sha256_hex(old.encode())})
    task.data["source_digest"] = remake_tests.authored_page_source_digest(target)
    if policy:
        presentation.theme["svg"]["styleSnapshot"] = _deck(monkeypatch).snapshot
    result = asyncio.run(remake.publish_svg_candidate(session, presentation, [target], 0, task, snapshot=False))
    params = session.execute.await_args.args[0].compile(dialect=remake_tests.sqlite.dialect()).params
    receipt = params["content"]["svgReceipt"]
    svg = presentation.theme["svg"]
    if policy:
        # The deck review cannot certify the new page: unreviewed, still flagged.
        assert receipt[REVIEW_FLAG_KEY]["reason"] == "design:unreviewed"
        assert result["quality_status"] == svg["qualityStatus"] == REVIEW_REQUIRED
        assert [entry["reason"] for entry in svg["flaggedSlides"]] == ["design:unreviewed"]
    else:
        assert REVIEW_FLAG_KEY not in receipt and "flag" not in receipt
        assert result["quality_status"] == svg["qualityStatus"] == "passed"
        assert svg["reviewPages"] == [] and svg["flaggedSlides"] == []


# --------------------------------------------------------------------------
# 5. readability: a flagged page publishes, an unbound measurement does not
# --------------------------------------------------------------------------


def _readability_page(*, classification, policy_digest=None):
    from services.authored_canvas import presentation_canvas_metadata
    from services.document_canvas_generation import svg_readability_policy
    policy = svg_readability_policy(presentation_canvas_metadata({"svg": {}}))
    svg = "<svg>small text</svg>"
    quality = {**_measured(svg), "passed": False, "failures": [{"kind": "small_body_text"}],
               "verified": classification == "passed", "classification": classification,
               "physical_readability": {"policy": policy, "policy_digest": policy_digest or policy["digest"],
                                        "measurement_complete": True, "passed": False, "minimum_font_pt": 4}}
    return svg, {"svgReceipt": {"readability_policy": policy, "quality": quality,
                                "source_digest": sha256_hex(svg.encode())}}


def test_a_flagged_readability_failure_publishes():
    svg, content = _readability_page(classification=REVIEW_REQUIRED)
    fields = {}
    set_svg_page(fields, svg_content=svg, content=content)
    assert fields["svg_content"] == svg


@pytest.mark.parametrize(("classification", "digest"), [
    ("passed", None),  # a failure classification did not flag is not a page flag
    (REVIEW_REQUIRED, "sha256:" + "0" * 64),  # measured under another policy
])
def test_an_unflagged_or_unbound_readability_measurement_is_refused(classification, digest):
    svg, content = _readability_page(classification=classification, policy_digest=digest)
    with pytest.raises(ValueError, match="svg_readability_publication_failed"):
        set_svg_page({}, svg_content=svg, content=content)


# --------------------------------------------------------------------------
# 6. a staged deck's stage-review flag survives publication's verdict
# --------------------------------------------------------------------------


def test_publication_merges_its_verdict_onto_a_stage_review_flag(monkeypatch):
    fixture = _deck(monkeypatch, axes={1: _FAILED_STRUCTURE})
    stage = flagged_entry(index=1, reason="design:overlap", attempted=False, outcome="not_attempted")
    fixture.pages[1][0][REVIEW_FLAG_KEY] = deepcopy(stage)
    fixture.pages[2][0][REVIEW_FLAG_KEY] = {**deepcopy(stage), "index": 2}
    apply_quality_recovery_publication(fixture.snapshot, fixture.pages, fixture.advisory, job=fixture.job)
    merged = fixture.pages[1][0][REVIEW_FLAG_KEY]
    assert merged["reason"] == "design:information_structure"
    assert merged["reasons"] == ["design:information_structure", "design:overlap"]
    assert fixture.pages[2][0][REVIEW_FLAG_KEY]["reason"] == "design:overlap"
