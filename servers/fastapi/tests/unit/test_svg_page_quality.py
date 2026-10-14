"""Unit tests for the SVG page-quality modules (c1-quality).

Covers classification (``page_outcome.py``), the terminal-page policy
(``terminal_policy.py`` / ``terminal_slide_policy.terminal_fallback_active``),
the vision-advisory record (``advisory_record.py``), the export receipt's
``visual_review`` domain (``svg_export_visual_review.py``), and the end-to-end
public quality receipt (``svg_export_quality.py`` / ``svg_export_release_gate.py``).
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from models.public_v2_contracts import QualityReceiptV3
from services.generation_core.quality.advisory_record import (
    advisory_flagged_pages, advisory_ran, advisory_record, record_matches,
)
from services.generation_core.quality.page_outcome import (
    PageOutcome, classify_page_outcomes, deck_quality_summary,
)
from services.generation_core.quality.terminal_policy import TerminalPolicy, resolve_terminal_policy
from services.generation_core.quality.terminal_slide_flag_only import flagged_entry
from services.generation_core.quality.terminal_slide_policy import (
    publish_flagged_allowed, terminal_fallback_active,
)
from services.generation_core.runtime.profile import resolve_svg_runtime_profile
from services.svg_export_quality import source_svg_public_quality_receipt, svg_public_quality_receipt
from services.svg_export_release_gate import require_release_v1_receipt
from services.svg_export_visual_review import slide_page_digests, split_page_advisories, svg_visual_review
from services.svg_contract import source_digest
from services.workspace_operation_handler_registry import NonRetryableWorkspaceOperationError
from test_svg_export_quality import evidence_fixture
from services.generation_core.authoring.author_slide import EXACT_RECONSTRUCTION, STYLE_REFERENCE

# --------------------------------------------------------------------------
# page_outcome.classify_page_outcomes / deck_quality_summary
# --------------------------------------------------------------------------


def _outcome(index=0, *, quality=None, failures=(), flag=None):
    return PageOutcome(index=index, source_format="svg", source="<svg/>", png_path="p",
                       quality=quality or {"passed": True}, attempts=1, failures=failures, flag=flag)


def test_clean_page_is_passed_and_verified():
    [classified] = classify_page_outcomes([_outcome()])
    assert classified.classification == "passed"
    assert classified.verified is True
    assert classified.flag is None


def test_residual_measured_failure_is_review_required():
    outcome = _outcome(quality={"passed": False, "failures": [{"kind": "overflow"}]},
                       failures=({"kind": "overflow"},))
    [classified] = classify_page_outcomes([outcome])
    assert classified.classification == "review_required"
    assert classified.verified is False
    assert classified.flag["reason"].startswith("quality:")


def test_substitute_flag_overrides_a_clean_measurement():
    flag = flagged_entry(index=0, reason="author:attempts_exhausted", attempted=False,
                         outcome="svg_fallback_page")
    outcome = _outcome(quality={"passed": True}, flag=flag)
    [classified] = classify_page_outcomes([outcome])
    assert classified.classification == "review_required"
    assert classified.verified is False


def test_flag_only_entry_on_a_clean_page_is_repair_not_admitted():
    flag = flagged_entry(index=0, reason="svg:x", attempted=False, outcome="verbatim_flag_only")
    outcome = _outcome(quality={"passed": True}, flag=flag)
    [classified] = classify_page_outcomes([outcome])
    assert classified.classification == "repair_not_admitted"
    assert classified.verified is True


def test_deck_quality_summary_counts_status_and_review_pages():
    clean = _outcome(index=0)
    flag = flagged_entry(index=1, reason="author:attempts_exhausted", attempted=False,
                         outcome="svg_fallback_page")
    flagged = _outcome(index=1, flag=flag)
    outcomes = classify_page_outcomes([clean, flagged])
    summary = deck_quality_summary(outcomes)
    assert summary["status"] == "review_required"
    assert summary["pageCount"] == 2
    assert summary["passedPages"] == 1
    assert summary["flaggedPages"] == 1
    assert summary["reviewPages"] == [1]
    assert summary["flaggedSlides"][0]["index"] == 1


def test_deck_quality_summary_all_clean_passes():
    outcomes = classify_page_outcomes([_outcome(index=0), _outcome(index=1)])
    summary = deck_quality_summary(outcomes)
    assert summary["status"] == "passed"
    assert summary["flaggedPages"] == 0
    assert summary["reviewPages"] == []
    assert summary["flaggedSlides"] == []


# --------------------------------------------------------------------------
# resolve_terminal_policy / resolve_svg_runtime_profile
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _default_publish_flagged_env(monkeypatch):
    """Every test in this module starts from the documented defaults."""
    monkeypatch.delenv("AUTHORED_PUBLISH_FLAGGED_SLIDES", raising=False)
    monkeypatch.delenv("AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW", raising=False)


def test_resolve_terminal_policy_defaults_fallback_for_ordinary_decks():
    assert resolve_terminal_policy(source_reflow=False, exact_reconstruction=False) is TerminalPolicy.FALLBACK


def test_resolve_terminal_policy_defaults_fail_for_source_reflow():
    assert resolve_terminal_policy(source_reflow=True, exact_reconstruction=False) is TerminalPolicy.FAIL


def test_resolve_terminal_policy_never_publishes_for_exact_reconstruction():
    assert resolve_terminal_policy(source_reflow=False, exact_reconstruction=True) is TerminalPolicy.FAIL
    assert resolve_terminal_policy(source_reflow=True, exact_reconstruction=True) is TerminalPolicy.FAIL


def test_resolve_terminal_policy_disabled_knob_fails_ordinary_decks(monkeypatch):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES", "false")
    assert resolve_terminal_policy(source_reflow=False, exact_reconstruction=False) is TerminalPolicy.FAIL


def test_resolve_terminal_policy_source_reflow_flag_only_when_enabled(monkeypatch):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW", "true")
    assert resolve_terminal_policy(source_reflow=True, exact_reconstruction=False) is TerminalPolicy.FLAG_ONLY


def test_runtime_profile_pins_terminal_policy_and_vision_advisory_by_class():
    ordinary = resolve_svg_runtime_profile(source_reflow=False)
    assert ordinary.terminal_policy is TerminalPolicy.FALLBACK
    assert ordinary.vision_advisory is True

    reflow = resolve_svg_runtime_profile(source_reflow=True)
    assert reflow.terminal_policy is TerminalPolicy.FAIL
    assert reflow.vision_advisory is True

    raster = resolve_svg_runtime_profile(source_reflow=False, raster_reconstruction=True)
    assert raster.terminal_policy is TerminalPolicy.FAIL
    assert raster.vision_advisory is False


# --------------------------------------------------------------------------
# terminal_fallback_active truth table (unchanged behaviour, D7)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("source_reflow", [False, True])
@pytest.mark.parametrize("reference_mode", [STYLE_REFERENCE, EXACT_RECONSTRUCTION])
@pytest.mark.parametrize("strict_dom_gate", [False, True])
@pytest.mark.parametrize("allow_summarization", [False, True])
@pytest.mark.parametrize("env_enabled", [False, True])
def test_terminal_fallback_active_matches_the_original_expression(
    monkeypatch, env_enabled, allow_summarization, strict_dom_gate, reference_mode, source_reflow,
):
    env_key = "AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW" if source_reflow else "AUTHORED_PUBLISH_FLAGGED_SLIDES"
    monkeypatch.setenv(env_key, "true" if env_enabled else "false")
    profile = SimpleNamespace(strict_dom_gate=strict_dom_gate, allow_summarization=allow_summarization)

    switches = strict_dom_gate if source_reflow else (strict_dom_gate and allow_summarization)
    expected = env_enabled and reference_mode != EXACT_RECONSTRUCTION and switches

    assert terminal_fallback_active(
        profile=profile, reference_mode=reference_mode, source_reflow=source_reflow,
    ) == expected
    # publish_flagged_allowed is the format-neutral half of the same switch.
    assert publish_flagged_allowed(
        source_reflow=source_reflow, exact_reconstruction=reference_mode == EXACT_RECONSTRUCTION,
    ) == (env_enabled and reference_mode != EXACT_RECONSTRUCTION)


# --------------------------------------------------------------------------
# advisory_record / record_matches / advisory_ran / advisory_flagged_pages
# --------------------------------------------------------------------------


def test_advisory_record_round_trips_and_flags_pages_with_issues():
    vision = {"slides": [{"index": 0, "issues": []}, {"index": 1, "issues": ["off-brand color"]}],
              "promptVersion": "v1", "model": "test-model"}
    digests = ["aaa", "bbb"]
    record = advisory_record(vision, digests)
    assert record["schemaVersion"] == "deck-vision-advisory/v1"
    assert advisory_ran(record) is True
    assert record_matches(record, digests) is True
    assert advisory_flagged_pages(record) == [1]


def test_advisory_ran_is_false_for_a_skipped_advisory():
    record = advisory_record({"skipped": "render_missing"}, ["aaa"])
    assert advisory_ran(record) is False


def test_record_matches_rejects_a_tampered_record():
    record = advisory_record({"slides": [], "model": "m"}, ["aaa"])
    tampered = {**record, "vision": {"slides": [{"index": 0, "issues": ["x"]}]}}
    assert record_matches(tampered, ["aaa"]) is False


def test_record_matches_rejects_a_changed_page_digest():
    record = advisory_record({"slides": [], "model": "m"}, ["aaa"])
    assert record_matches(record, ["bbb"]) is False


# --------------------------------------------------------------------------
# svg_export_visual_review.svg_visual_review
# --------------------------------------------------------------------------


def _slide(svg, *, verified=True, passed=True):
    return SimpleNamespace(svg_content=svg,
                           content={"svgReceipt": {"png_sha256": "a" * 64,
                               "quality": {"verified": verified, "passed": passed,
                                   "measurement_complete": True, "source_digest": source_digest(svg),
                                   "png_digest": "sha256:" + "a" * 64}}})


def _bound_advisory(vision, slides):
    record = advisory_record(vision, slide_page_digests(slides))
    record["pageAdvisories"] = split_page_advisories(vision, slides, range(len(slides)))
    return record


def test_visual_review_unavailable_without_an_advisory():
    review = svg_visual_review(slides=[_slide("<svg>a</svg>")], advisory=None)
    assert review == {"required": True, "status": "failed", "reasonCode": "svg_visual_advisory_unavailable"}


@pytest.mark.parametrize("policy_version", [None, 1, 2])
def test_visual_review_passes_with_a_matching_clean_advisory(policy_version, monkeypatch):
    slides = [_slide("<svg>a</svg>"), _slide("<svg>b</svg>")]
    digests = slide_page_digests(slides)
    vision = {"slides": [{"index": 0, "issues": []}, {"index": 1, "issues": []}],
              "promptVersion": "v1", "model": "m"}
    advisory = _bound_advisory(vision, slides)

    review = svg_visual_review(slides=slides, advisory=advisory)

    assert review["status"] == "passed"
    assert review["evidence"]["measurements"]["advisoryDigest"] == advisory["digest"]
    if policy_version is not None:
        from copy import deepcopy
        import struct
        import uuid
        from services.generation_core.drivers.svg.quality import _seal_design_gate
        from services.generation_core.quality.advisory_record import content_digest
        from services.generation_core.quality.vision_advisory_prompt import DESIGN_AXES, DESIGN_REVIEW_PROMPT_VERSION
        from services.svg_authoring_profile import ART_DIRECTION_PROFILE_V8
        from utils.hashing import canonical_json_digest, sha256_hex
        owner, operation, job, presentation = (str(uuid.uuid4()) for _ in range(4))
        monkeypatch.setattr("utils.owner_context.get_current_owner_id", lambda: owner)
        monkeypatch.setattr("utils.owner_context.get_current_tenant_id", lambda: "fixture-tenant")
        snapshot = dict(authoring_profile=dict(ART_DIRECTION_PROFILE_V8),
            quality_recovery_policy={"id": "svg-quality-first", "version": policy_version},
            canvas={"width": 1280, "height": 720})
        snapshot["digest"] = canonical_json_digest(snapshot, allow_nan=True)
        png = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + struct.pack(">II", 1280, 720)
        png_digest = sha256_hex(png)
        for index, slide in enumerate(slides):
            receipt = slide.content["svgReceipt"]
            receipt.update(png_sha256=png_digest, input_digest=f"fixture-input-{index}")
            receipt["quality"]["png_digest"] = "sha256:" + png_digest
            slide.content["source"] = f"Fixture source {index}"
        axes = {name: dict(verdict="passed", severity="none", repairScope="none",
                           visibleEvidence="Readable source grouping.") for name in DESIGN_AXES}
        vision = dict(slides=[{"index": 0, "issues": []}, {"index": 1, "issues": []}],
            promptVersion=DESIGN_REVIEW_PROMPT_VERSION, model="fixture-model",
            reviewedPageIndices=[0, 1], reviewedPngDigests=[png_digest, png_digest],
            designReview=dict(pages=[dict(index=index, evaluated=True, axes=deepcopy(axes), issues=[], designTargets=[])
                                    for index in range(2)], deckFlow={"evaluated": True}))
        context = dict(policy=snapshot["quality_recovery_policy"], style={"canvas": snapshot["canvas"]},
            styleDigest=content_digest(snapshot["canvas"]), snapshotDigest=content_digest(snapshot),
            runtimeBinding="fixture-runtime", exampleDigests=[], roles=["content", "content"],
            pages=[dict(index=index, content=slide.content["source"]) for index, slide in enumerate(slides)],
            binding=dict(jobId=job, operationId=operation, presentationId=presentation, ownerId=owner, tenantId="fixture-tenant"),
            priorCandidates={}, mandatoryEvidence=[True, True], deckComplete=True)
        vision["designGate"] = _seal_design_gate(context,
            [({**slide.content["svgReceipt"], "svg_content": slide.svg_content}, png) for slide in slides], [png, png], vision)
        if policy_version == 2:
            from services.svg_quality_recovery_review import seal_saved_review
            saved_context = {**context, "reviewScope": "edited"}
            prepared = dict(context=saved_context, changed=[0], reusable={}, snapshot=snapshot,
                runtime={"fixture": "current-runtime"}, ownerScope=owner, sourceGenerationRevision=1,
                publication=dict(parentOperationId=operation, parentJobId=job),
                pages=[({**slide.content["svgReceipt"], "svg_content": slide.svg_content}, png) for slide in slides],
                pngs=[png, png])
            record, _ = seal_saved_review(prepared, slides, vision,
                SimpleNamespace(expected_revision=1, operation_id=operation))
            assert record["vision"]["designReview"] == vision["designReview"]
            assert record["vision"]["designGate"]["status"] == "passed"
            incomplete = deepcopy(vision)
            incomplete["designReview"]["pages"].pop()
            incomplete_record, _ = seal_saved_review(prepared, slides, incomplete,
                SimpleNamespace(expected_revision=1, operation_id=operation))
            assert incomplete_record["vision"]["designGate"]["status"] == "unreviewed"
            from services import svg_quality_recovery_review as saved_review
            parent = dict(parentOperationId=operation, parentJobId=job, sourceGenerationRevision=1)
            parent["digest"] = content_digest(parent)
            saved_presentation = SimpleNamespace(id=presentation, owner_id=owner, revision=1,
                theme={"svg": {"styleSnapshot": snapshot, "qualityRecoveryPublication": parent}})
            expanded = [deepcopy(slides[index % 2]) for index in range(6)]
            monkeypatch.setattr(saved_review, "saved_review_runtime", lambda: {"fixture": "current-runtime"})
            monkeypatch.setattr("services.svg_authoring_profile.read_quality_recovery_plan",
                lambda _: dict(slide_count=6, review_group_size=5))
            monkeypatch.setattr("services.generation_core.drivers.svg.quality._candidate_local_valid", lambda *a, **k: True)
            monkeypatch.setattr("services.generation_core.drivers.svg.quality._design_context", lambda *a, **k: saved_context)
            expanded_prepared = saved_review.prepare_saved_review(saved_presentation, expanded, [0], [png] * 6,
                operation_id=operation, owner_scope=owner)
            assert expanded_prepared["changed"] == list(range(6))
            assert expanded_prepared["context"]["reviewScope"] == "full"
            saved_context["policy"] = {"id": "svg-quality-first", "version": 1}
            with pytest.raises(ValueError, match="single_call_capacity_exceeded"):
                saved_review.prepare_saved_review(saved_presentation, expanded, [0], [png] * 6,
                    operation_id=operation, owner_scope=owner)
        publication = dict(schema="svg-quality-recovery-publication/v1", gateDigest=vision["designGate"]["digest"],
            parentOperationId=operation, parentJobId=job, sourceRevision=1, sourceGenerationRevision=1,
            snapshotDigest=content_digest(snapshot), sourceContentDigests=[content_digest(slide.content["source"]) for slide in slides],
            inputDigests=[slide.content["svgReceipt"]["input_digest"] for slide in slides])
        publication["digest"] = content_digest(publication)
        producer = dict(operationId=operation, presentationId=presentation, ownerId=owner,
                        ownerScope=owner, sourceRevision=1, state="succeeded", kind="presentation.generate")
        expected = dict(presentationId=presentation, ownerId=owner, ownerScope=owner, sourceRevision=1,
            _generationSourceReview=dict(snapshot=snapshot, publication=publication, producer=producer))
        bound = _bound_advisory(vision, slides)
        binding = dict(schemaVersion="svg-visual-review-operation-binding/v1", presentationId=presentation,
            ownerId=owner, ownerScope=owner, sourceRevision=1, operationId=operation, reviewedPageIndices=[0, 1],
            sourceDigests=digests, pngDigests=[png_digest, png_digest], pageRecordDigests=[record["digest"] for record in bound["pageAdvisories"]])
        bound["operationBinding"] = {**binding, "digest": content_digest(binding)}
        assert svg_visual_review(slides=slides, advisory=bound, expected_binding=expected)["status"] == "passed"
        if policy_version == 2:
            import asyncio
            from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
            from services.svg_common_header import prepare_common_header
            from services.svg_export_operation import require_quality_recovery_source_parent

            header_outline = PresentationOutlineModel(slides=[
                SlideOutlineModel(content=slide.content["source"], title=f"Source {index + 1}")
                for index, slide in enumerate(slides)])
            header, header_bindings = prepare_common_header(snapshot, header_outline, snapshot["canvas"])
            for page, page_binding in zip(header_outline.slides, header_bindings, strict=True):
                page.common_header = page_binding
            frozen_snapshot = {**deepcopy(snapshot), "common_header": header}
            frozen_snapshot["digest"] = canonical_json_digest(
                {key: value for key, value in frozen_snapshot.items() if key != "digest"}, allow_nan=True)
            generation = SimpleNamespace(id=uuid.UUID(operation), state="succeeded", kind="presentation.generate",
                owner_id=uuid.UUID(owner), presentation_id=uuid.UUID(presentation), result_revision=1)
            published_job = SimpleNamespace(state="published", workspace_operation_id=generation.id,
                presentation_id=generation.presentation_id, svg_style_snapshot=frozen_snapshot,
                authored_outline=header_outline.model_dump(mode="json"),
                request_payload={"_internal_svg_style_snapshot": deepcopy(snapshot)})
            current_presentation = SimpleNamespace(id=generation.presentation_id, owner_id=generation.owner_id,
                revision=1, theme={"svg": {"styleSnapshot": deepcopy(frozen_snapshot)}},
                outlines=deepcopy(published_job.authored_outline))
            current_admission = SimpleNamespace(owner_id=generation.owner_id, tenant_id="fixture-tenant")

            class ParentSession:
                async def get(self, model, _identifier):
                    return generation if model.__name__ == "WorkspaceOperation" else published_job

                async def scalar(self, _statement):
                    return current_admission

            before_header_inputs = deepcopy((snapshot, frozen_snapshot, published_job.request_payload))
            asyncio.run(require_quality_recovery_source_parent(ParentSession(), current_presentation, publication))
            assert (snapshot, frozen_snapshot, published_job.request_payload) == before_header_inputs
            wrong_base = deepcopy(snapshot)
            wrong_base["tokens"] = {"primary": "#FF0000"}
            wrong_base["digest"] = canonical_json_digest(
                {key: value for key, value in wrong_base.items() if key != "digest"}, allow_nan=True)
            published_job.request_payload["_internal_svg_style_snapshot"] = wrong_base
            with pytest.raises(ValueError, match="svg_quality_recovery_export_parent_invalid"):
                asyncio.run(require_quality_recovery_source_parent(ParentSession(), current_presentation, publication))
            published_job.request_payload["_internal_svg_style_snapshot"] = deepcopy(snapshot)
            generation.state = "failed"
            with pytest.raises(ValueError, match="svg_quality_recovery_export_parent_invalid"):
                asyncio.run(require_quality_recovery_source_parent(ParentSession(), current_presentation, publication))
            generation.state = "succeeded"
            for violation in ("none", "header_digest", "binding"):
                published_job.svg_style_snapshot = deepcopy(frozen_snapshot)
                published_job.authored_outline = header_outline.model_dump(mode="json")
                if violation == "none":
                    published_job.svg_style_snapshot["common_header"] = None
                    published_job.svg_style_snapshot["digest"] = canonical_json_digest(
                        {key: value for key, value in published_job.svg_style_snapshot.items() if key != "digest"},
                        allow_nan=True)
                    for page in published_job.authored_outline["slides"]:
                        page.pop("common_header")
                elif violation == "header_digest":
                    published_job.svg_style_snapshot["common_header"]["digest"] = "sha256:" + "0" * 64
                else:
                    published_job.authored_outline["slides"][0]["common_header"]["digest"] = "sha256:" + "0" * 64
                current_presentation.theme["svg"]["styleSnapshot"] = deepcopy(published_job.svg_style_snapshot)
                current_presentation.outlines = deepcopy(published_job.authored_outline)
                with pytest.raises(ValueError, match="svg_quality_recovery_export_parent_invalid"):
                    asyncio.run(require_quality_recovery_source_parent(ParentSession(), current_presentation, publication))

            original = advisory_record(vision, digests)
            before = deepcopy(original)
            consumed = svg_visual_review(slides=slides, advisory=original, expected_binding=expected)
            assert consumed["status"] == "passed" and original == before
            assert consumed["evidence"]["measurements"]["reviewScope"] == "source-svg-png-design/v1"
            assert "pageAdvisories" not in original and "operationBinding" not in original
            for violation in ("goal", "owner_scope", "revision", "snapshot", "png", "unknown", "source"):
                candidate, evidence = deepcopy(original), deepcopy(expected)
                candidate_slides = deepcopy(slides)
                proof = evidence["_generationSourceReview"]
                if violation == "goal":
                    gate = candidate["vision"]["designGate"]
                    gate["designImprovementStatus"] = "needed"
                    gate["digest"] = content_digest({key: value for key, value in gate.items() if key != "digest"})
                    proof["publication"]["gateDigest"] = gate["digest"]
                    proof["publication"]["digest"] = content_digest({key: value for key, value in proof["publication"].items() if key != "digest"})
                    candidate = advisory_record(candidate["vision"], digests)
                elif violation == "owner_scope":
                    proof["producer"]["ownerScope"] = "another-scope"
                elif violation == "revision":
                    evidence["sourceRevision"] = 2
                elif violation == "snapshot":
                    proof["snapshot"]["canvas"]["width"] = 999
                elif violation == "png":
                    candidate_slides[0].content["svgReceipt"]["png_sha256"] = "0" * 64
                elif violation == "unknown":
                    proof["producer"]["state"] = "unknown"
                else:
                    candidate_slides[0].svg_content = "<svg>changed</svg>"
                assert svg_visual_review(slides=candidate_slides, advisory=candidate, expected_binding=evidence)["status"] == "failed"
            assert svg_visual_review(slides=slides, advisory=original, expected_binding={key: value for key, value in expected.items()
                if key != "_generationSourceReview"})["status"] == "failed"


def test_visual_review_records_but_does_not_flag_on_an_advisory_issue():
    # The advisory is advisory by contract: generation reports this deck as
    # 0 flagged pages, and the export review must agree.
    slides = [_slide("<svg>a</svg>")]
    digests = slide_page_digests(slides)
    vision = {"slides": [{"index": 0, "issues": ["off-brand color"]}], "promptVersion": "v1", "model": "m"}
    advisory = _bound_advisory(vision, slides)

    review = svg_visual_review(slides=slides, advisory=advisory)

    assert review["status"] == "passed"
    assert "reasonCode" not in review
    assert review["evidence"]["measurements"]["advisoryFlaggedPages"] == 1
    assert review["evidence"]["measurements"]["reviewPages"] == 0


def test_visual_review_stale_when_a_page_svg_changed_after_the_advisory():
    slides = [_slide("<svg>a</svg>")]
    digests = slide_page_digests(slides)
    advisory = _bound_advisory({"slides": [], "promptVersion": "v1", "model": "m"}, slides)
    slides[0].svg_content = "<svg>changed</svg>"

    review = svg_visual_review(slides=slides, advisory=advisory)

    assert review["status"] == "failed"
    assert review["reasonCode"] == "svg_visual_advisory_stale"


def test_visual_review_flags_a_page_whose_quality_is_not_verified():
    slides = [_slide("<svg>a</svg>", verified=False)]
    digests = slide_page_digests(slides)
    vision = {"slides": [{"index": 0, "issues": []}], "promptVersion": "v1", "model": "m"}
    advisory = _bound_advisory(vision, slides)

    review = svg_visual_review(slides=slides, advisory=advisory)

    assert review["status"] == "failed"
    assert review["reasonCode"] == "svg_visual_review_flagged"


# --------------------------------------------------------------------------
# End-to-end receipt: svg_public_quality_receipt / require_release_v1_receipt
# --------------------------------------------------------------------------


def _clean_advisory(slides):
    digests = slide_page_digests(slides)
    vision = {"slides": [{"index": i, "issues": []} for i in range(len(slides))],
              "promptVersion": "v1", "model": "test-model"}
    return _bound_advisory(vision, slides)


def _ordinary_slides():
    return [_slide("<svg>1</svg>"), _slide("<svg>2</svg>")]


def _ordinary_receipt(advisory):
    return svg_public_quality_receipt(
        slides=_ordinary_slides(), advisory=advisory, source_preservation=None, worker_metrics={},
        exporter_version="x", quality_receipt_id="quality-t", operation_id="operation-t",
        presentation_id="presentation-t", artifact_digest="sha256:" + "a" * 64, source_revision=1,
        scene_digest="sha256:" + "b" * 64, evaluated_at=datetime.now(timezone.utc),
    )


def test_ordinary_deck_receipt_is_camel_case_and_visual_review_passes_but_blocked():
    receipt = _ordinary_receipt(_clean_advisory(_ordinary_slides()))
    validated = QualityReceiptV3.model_validate(receipt)

    assert "visualReview" in receipt and "releaseReadiness" in receipt
    assert validated.visual_review.status == "passed"
    # These fixture pages carry no measured page quality, so authoring and
    # geometry/font stay fail-closed and the deck is not release ready.
    assert receipt["authoring"]["reasonCode"] == "svg_authoring_evidence_missing"
    assert receipt["geometryFont"]["reasonCode"] == "svg_geometry_font_evidence_missing"
    assert receipt["releaseReadiness"] == "blocked"
    with pytest.raises(NonRetryableWorkspaceOperationError, match="svg_release_quality_not_release_ready"):
        require_release_v1_receipt(receipt)


def test_ordinary_deck_receipt_flagged_visual_review_is_rejected_by_the_gate():
    slides = [_slide("<svg>1</svg>", verified=False), _slide("<svg>2</svg>")]
    receipt = svg_public_quality_receipt(
        slides=slides, advisory=_clean_advisory(slides), source_preservation=None, worker_metrics={},
        exporter_version="x", quality_receipt_id="quality-t", operation_id="operation-t",
        presentation_id="presentation-t", artifact_digest="sha256:" + "a" * 64, source_revision=1,
        scene_digest="sha256:" + "b" * 64, evaluated_at=datetime.now(timezone.utc),
    )

    assert receipt["visualReview"]["status"] == "failed"
    assert receipt["visualReview"]["reasonCode"] == "svg_visual_review_flagged"
    with pytest.raises(NonRetryableWorkspaceOperationError,
                        match="svg_release_quality_visual_review_not_passed"):
        require_release_v1_receipt(receipt)


def test_source_preservation_receipt_is_release_ready_with_a_clean_advisory():
    canvas, presentation, slide, receipt, readback = evidence_fixture()
    # ``render.attempt_render`` always sets this for a verified source-reflow
    # page; the shared fixture omits it because its own tests never reach
    # ``svg_visual_review``'s per-page review check.
    slide.content["svgReceipt"]["quality"]["verified"] = True
    slide.content["svgReceipt"]["quality"]["png_digest"] = "sha256:" + "a" * 64
    slide.content["svgReceipt"]["png_sha256"] = "a" * 64
    advisory = _clean_advisory([slide])

    result = source_svg_public_quality_receipt(
        slides=[slide], source_preservation=[receipt],
        worker_metrics={"public_export_worker_evidence": {"structuralReadback": {"sourcePreservation": readback}}},
        quality_receipt_id="quality-t", operation_id="operation-t", presentation_id="presentation-t",
        artifact_digest="sha256:" + "a" * 64, source_revision=1, scene_digest="sha256:" + "b" * 64,
        evaluated_at=datetime.now(timezone.utc), exporter_version="test-converter/v1", advisory=advisory,
    )
    validated = QualityReceiptV3.model_validate(result)

    assert validated.visual_review.status == "passed"
    assert result["releaseReadiness"] == "ready"
    require_release_v1_receipt(result)  # must not raise
