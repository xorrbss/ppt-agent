"""H6: the advisory vision pass over the deck contact sheet.

See ``docs/authored-quality-speed-programme-design-20260905.md`` section 6, H6.
The contract these tests hold is that the advisory is closed-vocabulary,
defensive about whatever the model returns, and incapable of raising: it is
written into the receipt as ``advisory.vision`` and read by nothing.
"""

from __future__ import annotations

import asyncio
import io
import json
from types import SimpleNamespace

import pytest

from services import authored_vision_advisory as advisory_mod
from services import authored_vision_advisory_prompt as prompt_mod
from services import authored_vision_contact_sheet as sheet_mod


def _run(coro):
    return asyncio.run(coro)


def _png(width: int = 1280, height: int = 720, colour: str = "white") -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Contact sheet
# ---------------------------------------------------------------------------


def test_the_sheet_is_four_columns_within_the_pixel_envelope():
    from PIL import Image

    sheet = sheet_mod.compose_contact_sheet_sync([_png() for _ in range(9)])

    with Image.open(io.BytesIO(sheet)) as image:
        assert image.format == "JPEG"
        assert image.width <= sheet_mod.SHEET_MAX_WIDTH_PX
        assert image.height <= sheet_mod.SHEET_MAX_HEIGHT_PX
        # 9 slides at 4 per row is 3 rows, each a 16:9 thumbnail plus its label.
        assert image.width == (sheet_mod.SHEET_MAX_WIDTH_PX // 4) * 4


def test_a_very_long_deck_shrinks_rather_than_exceeding_the_height_cap():
    from PIL import Image

    sheet = sheet_mod.compose_contact_sheet_sync([_png() for _ in range(120)])

    with Image.open(io.BytesIO(sheet)) as image:
        assert image.height <= sheet_mod.SHEET_MAX_HEIGHT_PX


def test_no_renders_compose_to_nothing():
    assert sheet_mod.compose_contact_sheet_sync([]) is None
    assert sheet_mod.compose_contact_sheet_sync([b"", None]) is None


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def test_the_vocabulary_is_closed_and_indices_become_zero_based():
    parsed = advisory_mod.parse_advisory_payload(
        {
            "slides": [
                {"index": 1, "issues": ["empty_band", "made_up_word"]},
                {"index": 3, "issues": ["off-palette", "Broken Figure"]},
            ]
        },
        slide_count=4,
    )

    assert parsed == [
        {"index": 0, "issues": ["empty_band"]},
        {"index": 2, "issues": ["off_palette", "broken_figure"]},
    ]


def test_a_slide_whose_every_word_was_invented_is_dropped():
    parsed = advisory_mod.parse_advisory_payload(
        {"slides": [{"index": 2, "issues": ["vibes", ""]}]}, slide_count=3
    )

    assert parsed == []


def test_an_out_of_range_index_is_clamped_not_dropped():
    parsed = advisory_mod.parse_advisory_payload(
        {"slides": [{"index": 99, "issues": ["cramped"]}, {"index": 0, "issues": ["cramped"]}]},
        slide_count=3,
    )

    assert [item["index"] for item in parsed] == [0, 2]


@pytest.mark.parametrize(
    "payload",
    [None, "text", {}, {"slides": "no"}, {"slides": [1, None, {"index": "x"}]}],
)
def test_a_malformed_payload_parses_to_nothing(payload):
    assert advisory_mod.parse_advisory_payload(payload, slide_count=3) == []


# ---------------------------------------------------------------------------
# run_vision_advisory: every failure is a skip, never a raise
# ---------------------------------------------------------------------------


def test_a_deck_with_no_renders_is_skipped():
    assert _run(advisory_mod.run_vision_advisory([], slide_count=0)) == {
        "skipped": "no_renders"
    }


def test_a_provider_failure_is_a_skip(monkeypatch):
    async def boom(sheet, *, slide_count):
        raise RuntimeError("provider down")

    monkeypatch.setattr(advisory_mod, "_request_advisory", boom)

    assert _run(advisory_mod.run_vision_advisory([_png()], slide_count=1)) == {
        "skipped": "provider_failed"
    }


def test_an_unparseable_response_is_a_skip(monkeypatch):
    async def unparseable(sheet, *, slide_count):
        return "some-model", None

    monkeypatch.setattr(advisory_mod, "_request_advisory", unparseable)

    assert _run(advisory_mod.run_vision_advisory([_png()], slide_count=1)) == {
        "skipped": "unparseable_response"
    }


def test_a_successful_advisory_reports_the_model_and_the_findings(monkeypatch):
    seen: dict[str, object] = {}

    async def respond(sheet, *, slide_count):
        seen["sheet_bytes"] = len(sheet)
        seen["slide_count"] = slide_count
        return "gpt-6-astra", {"slides": [{"index": 2, "issues": ["empty_band"]}]}

    monkeypatch.setattr(advisory_mod, "_request_advisory", respond)

    result = _run(advisory_mod.run_vision_advisory([_png(), _png()], slide_count=2))

    assert result["model"] == "gpt-6-astra"
    assert result["slideCount"] == 2
    assert result["slides"] == [{"index": 1, "issues": ["empty_band"]}]
    assert isinstance(result["latencyMs"], int)
    assert seen["slide_count"] == 2
    assert seen["sheet_bytes"] > 0


# ---------------------------------------------------------------------------
# The request: the same shape as the vision-QA critique path, which works
# ---------------------------------------------------------------------------


def _part_types(message):
    return [type(part).__name__ for part in message.content]


@pytest.mark.parametrize("quality_policy_version", [None, 2])
def test_the_advisory_request_has_the_same_message_shape_as_the_critique_call(quality_policy_version, monkeypatch):
    """Bench run 30 came back empty. The shape was not the difference, and it
    must never become one, so it is pinned to the call that does work."""

    from llmai.shared import SystemMessage, UserMessage

    from utils.llm_calls import critique_slide

    advisory = prompt_mod.build_advisory_messages(b"sheet", slide_count=12)
    critique = critique_slide._build_critique_messages(b"png")

    assert isinstance(advisory[0], SystemMessage)
    assert isinstance(advisory[1], UserMessage)
    assert len(advisory) == len(critique) == 2
    # Same content-part types, in the same order: text first, then the image.
    assert _part_types(advisory[1]) == _part_types(critique[1])
    assert _part_types(advisory[1]) == ["TextContentPart", "ImageContentPart"]
    assert advisory[1].content[1].data == b"sheet"
    assert advisory[1].content[1].mime_type == "image/jpeg"
    assert "12" in advisory[1].content[0].text
    if quality_policy_version == 2:
        from services.generation_core.quality import vision_advisory as shared_advisory
        names = prompt_mod.DESIGN_LOCAL_AXES
        context = dict(policy={"id": "svg-quality-first", "version": 2}, reviewScope="local",
            deckComplete=False, pages=[dict(index=0, content="Revenue leads the supporting evidence.")],
            blindVariantLabel="B", priorCandidates={0: dict(png=b"alternative-png", targetAxes=[names[0]])})
        messages = prompt_mod.build_advisory_messages(b"", slide_count=1, required=True,
            pngs=[b"evaluation-png"], design_context=context)
        assert isinstance(messages[0], SystemMessage) and isinstance(messages[1], UserMessage)
        assert len(messages) == 2
        labels = [part.text for part in messages[1].content if type(part).__name__ == "TextContentPart"]
        assert "Variant A, evaluation page 1." in labels and "Variant B, evaluation page 1." in labels
        visible = messages[0].content + " ".join(labels)
        assert all(hidden not in visible for hidden in ("PRIOR", "NEW", "priorCandidates", "priorAxes", "svg-quality-first"))
        assert "frozen document constraints" not in messages[0].content
        images = [part.data for part in messages[1].content if type(part).__name__ == "ImageContentPart"]
        assert images == [b"alternative-png", b"evaluation-png"]
        # Carry the actual frozen header and page title into the same review
        # request, keeping all existing source facts and ordinary design goals.
        from copy import deepcopy
        from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
        from services.authored_canvas import canvas_metadata_for_aspect_ratio
        from services.generation_core.drivers.svg import quality as deck_quality
        from services.svg_authoring_profile import ART_DIRECTION_PROFILE_V8
        from services.svg_common_header import prepare_common_header
        from utils.hashing import canonical_json_digest

        source = context["pages"][0]["content"]
        outline = PresentationOutlineModel(slides=[SlideOutlineModel(content=source, title="Revenue update")])
        snapshot = dict(tokens={}, canvas=dict(width=1280, height=720),
            authoring_profile=dict(ART_DIRECTION_PROFILE_V8), quality_recovery_policy=dict(context["policy"]))
        snapshot["digest"] = canonical_json_digest(snapshot, allow_nan=True)
        fixture_ctx = SimpleNamespace(snapshot=snapshot, roles=["content"], count=1,
            quality_review_contents=[source], header_pages=None)
        fixture_pages = [(dict(svg_content="<svg/>"), b"evaluation-png")]
        build_design_context = deck_quality._design_context
        seal_design_gate = deck_quality._seal_design_gate
        legacy_context = deck_quality._design_context(fixture_ctx, fixture_pages, job=None, read_png=lambda _: None)
        assert "reviewPrinciplesVersion" not in legacy_context
        assert "common_header" not in legacy_context["style"]
        assert "common_header" not in legacy_context["pages"][0]
        contract, bindings = prepare_common_header(snapshot, outline, canvas_metadata_for_aspect_ratio("16:9"))
        snapshot["common_header"] = contract
        snapshot["digest"] = canonical_json_digest(
            {key: value for key, value in snapshot.items() if key != "digest"}, allow_nan=True)
        with pytest.raises(ValueError, match="svg_common_header_review_binding_missing"):
            deck_quality._design_context(fixture_ctx, fixture_pages, job=None, read_png=lambda _: None)
        fixture_ctx.header_pages = bindings
        before_snapshot, before_bindings = deepcopy(snapshot), deepcopy(bindings)
        header_context = deck_quality._design_context(fixture_ctx, fixture_pages, job=None, read_png=lambda _: None)
        assert snapshot == before_snapshot and bindings == before_bindings
        assert header_context["style"]["common_header"] == contract
        assert header_context["pages"][0]["common_header"] == bindings[0]
        assert header_context["pages"][0]["content"] == source
        # Original user constraints are deck-wide source evidence, not authored
        # native declarations, and must invalidate every relevant review cache.
        from services.generation_core.drivers.svg.native_requirements import requested_native_data
        authority_ctx = deepcopy(fixture_ctx)
        authority_ctx.quality_review_contents = [source + "\n| User | Action |\n| --- | --- |\n| Member | Policy |"]
        authority_pages = deepcopy(fixture_pages)
        authority_pages[0][0].update(nativeRequirements={"tables": [{"rows": [["forged"]]}]},
            nativeDeclarations={"tables": [{"mergedCells": [{"columnSpan": 99}]}]})
        directives = "  Preserve original merged policy cells across covered actions.\nKeep all source facts.  "
        authority_job = SimpleNamespace(id="source-job", workspace_operation_id="source-operation",
            presentation_id="source-presentation", request_payload={"instructions": directives})
        before_authority = deepcopy((authority_ctx.quality_review_contents, authority_pages, authority_job.request_payload))
        authority_context = deck_quality._design_context(authority_ctx, authority_pages,
            job=authority_job, read_png=lambda _: None)
        assert before_authority == (authority_ctx.quality_review_contents, authority_pages, authority_job.request_payload)
        assert authority_context["sourceDirectives"] == directives
        expected_native = requested_native_data(authority_ctx.quality_review_contents[0])
        assert authority_context["pages"][0]["sourceNativeRequirements"] == expected_native
        assert "sourceDirectives" not in authority_context["pages"][0]
        local_context = deck_quality._local_group_context(authority_context, [0])
        global_context = {**authority_context, "reviewScope": "global", "representativeIndices": []}
        for review_context in (local_context, global_context):
            sent = prompt_mod.build_advisory_messages(b"sheet", slide_count=1, required=True,
                pngs=[b"evaluation-png"], design_context=review_context)
            sent_source = json.loads(sent[1].content[0].text)
            assert sent_source["sourceDirectives"] == directives
            # The existing anonymous public projection strips opaque IDs only.
            assert sent_source["pages"][0]["sourceNativeRequirements"]["tables"] == [
                {key: value for key, value in table.items() if key != "id"}
                for table in expected_native["tables"]]
            assert prompt_mod.SOURCE_AUTHORITY_REVIEW_RULES in sent[0].content
            changed_context = {**review_context, "sourceDirectives": directives + "\nAdditional source constraint."}
            assert deck_quality._group_review_input_digest(review_context, [b"evaluation-png"], 0, 0, [0]) != (
                deck_quality._group_review_input_digest(changed_context, [b"evaluation-png"], 0, 0, [0]))
        changed_authority = {**authority_context, "sourceDirectives": directives + "\nAdditional source constraint."}
        assert deck_quality._local_review_digest(authority_context, authority_pages[0][0], b"evaluation-png", 0) != (
            deck_quality._local_review_digest(changed_authority, authority_pages[0][0], b"evaluation-png", 0))
        context = {**context, "style": header_context["style"], "pages": header_context["pages"]}
        header_messages = prompt_mod.build_advisory_messages(b"", slide_count=1, required=True,
            pngs=[b"evaluation-png"], design_context=context)
        header_text = header_messages[0].content
        assert "frozen document constraints" in header_text and "document navigation" in header_text
        assert "common_header.body_box" in header_text and "grounded designTargets" in header_text
        assert "header substitution or forgery, overlap, clipping" in header_text
        assert "native binding" in header_text and "never certifies passed quality" in header_text
        assert "14 words" in header_text and "160 characters" in header_text
        sent_context = json.loads(header_messages[1].content[0].text)
        # The existing anonymous projection still omits version metadata;
        # visible geometry, typography, titles and binding evidence stay exact.
        projected_contract, projected_binding = deepcopy(contract), deepcopy(bindings[0])
        projected_contract.pop("version")
        projected_binding.pop("version")
        assert sent_context["style"]["common_header"] == projected_contract
        assert sent_context["style"]["common_header"]["font"] == contract["font"]
        assert sent_context["style"]["common_header"]["body_box"] == contract["body_box"]
        assert sent_context["pages"][0]["common_header"] == projected_binding
        assert sent_context["pages"][0]["content"] == source
        assert [part.data for part in header_messages[1].content if type(part).__name__ == "ImageContentPart"] == images
        axes = {name: dict(verdict="passed", severity="none", repairScope="none",
                           visibleEvidence="Clear source grouping remains readable.") for name in names}
        goal = dict(axis=names[0], evidenceRegion="Title and evidence block",
            visibleEvidence="Key claim has the same weight as notes.",
            sourceEvidence="Source revenue claim is the primary message.",
            improvementGoal="Distinguish the revenue claim from supporting notes.")
        payload = dict(slides=[dict(index=1, evaluated=True, issues=[], axes=axes, designTargets=[goal])],
            pairComparisons=[dict(index=1, evaluated=True, alternativeAxes=axes,
                visibleEvidence="B emphasizes the source claim without hiding evidence.",
                axisChanges={name: dict(preferred="B" if name == names[0] else "equivalent",
                    visibleEvidence="Claim is easier to find." if name == names[0] else "Source reading remains equivalent.")
                    for name in names})])
        parsed = shared_advisory._parse_baseline_review(payload, slide_count=1, context=context)
        assert parsed["pages"][0]["axes"][names[0]]["verdict"] == "passed"
        assert parsed["pages"][0]["designTargets"] == [goal]
        assert parsed["candidateComparisons"][0]["axisChanges"][names[0]]["change"] == "improved"
        payload["pairComparisons"][0]["axisChanges"][names[0]]["preferred"] = "unjudged"
        assert shared_advisory._parse_baseline_review(payload, slide_count=1, context=context)[
            "candidateComparisons"][0]["axisChanges"][names[0]]["change"] == "unjudged"
        # Exercise the real review dispatch boundary without provider/DB I/O.
        from contextlib import contextmanager
        from uuid import UUID
        from services import provider_usage_instrumentation, svg_authoring_profile
        from services.generation_core.drivers.svg import quality as deck_quality
        from utils.hashing import sha256_hex
        captured = []
        original_scope = provider_usage_instrumentation.provider_usage_scope
        @contextmanager
        def capture_scope(**kwargs):
            with original_scope(**kwargs):
                captured.append((kwargs, provider_usage_instrumentation._PUBLIC_OPERATION_FENCE.get(),
                                 provider_usage_instrumentation._ACTIVE_SCOPE.get()))
                yield
        monkeypatch.setattr(provider_usage_instrumentation, "provider_usage_scope", capture_scope)
        plan = dict(slide_count=1, review_round_limit=1, phase_group_limit=1,
                    review_group_size=5, global_format_retry_limit=0)
        monkeypatch.setattr(svg_authoring_profile, "read_quality_recovery_execution_plan", lambda *args, **kwargs: plan)
        png = _png()
        page = dict(svg_content="<svg/>", png_sha256=sha256_hex(png))
        async def checkpoint(*args):
            return None
        async def review(*args, **kwargs):
            return {}
        monkeypatch.setattr(deck_quality, "_seal_design_gate", lambda *args: {})
        operation = UUID("c32d38df-40d7-48f1-8706-9b46bde5f085")
        for version in (1, 2):
            for operation_id, partial in ((identity, partial)
                    for identity in (operation, None) for partial in (False, True)):
                review_context = dict(policy={"id": "svg-quality-first", "version": version},
                    style=dict(canvas=dict(width=1280, height=720)), priorCandidates={},
                    mandatoryEvidence=[True], runtimeBinding="current-runtime",
                    binding=dict(jobId="current-job", presentationId="current-presentation", ownerId="owner", tenantId="tenant"),
                    roles=["content"], pages=[dict(index=0, referenceRoles=["content"])],
                    composition={}, examples=[], exampleDigests=[])
                monkeypatch.setattr(deck_quality, "_design_context", lambda *args, **kwargs: review_context)
                ctx = SimpleNamespace(snapshot={}, quality_recovery_review_group_checkpoint=checkpoint)
                job = SimpleNamespace(id="current-job", workspace_operation_id=operation_id,
                                      attempt_number=1, request_payload={"_svg_partial_recovery": partial})
                with provider_usage_instrumentation.public_operation_scope(operation_id,
                        attempt_token="current-attempt", fencing_token=1):
                    _run(deck_quality._quality_first_deck_advisory(ctx, [(page, png)], job=job,
                        read_png=lambda _: png, run_advisory=review, phase=0))
                scope, fence, active = captured[-1]
                assert scope["job_id"] == job.id
                if partial:
                    assert scope["workspace_operation_id"] is operation_id
                else:
                    assert "workspace_operation_id" not in scope
                    assert active.workspace_operation_id is None
                if operation_id is not None:
                    assert fence.operation_id is operation
                    assert fence.attempt_token == "current-attempt" and fence.fencing_token == 1
                else:
                    assert fence is None
        from copy import deepcopy
        from jsonschema import ValidationError, validate
        full_context = {**context, "reviewScope": "full", "deckComplete": True, "priorCandidates": {},
            "reviewPrinciplesVersion": prompt_mod.DESIGN_PRINCIPLES_VERSION,
            "composition": {"pages": [{}], "groups": [{"pages": [0]}]}}
        full_axes = {name: dict(verdict="passed", severity="none", repairScope="none",
            visibleEvidence="Distinct grouping preserves readable source meaning.") for name in prompt_mod.DESIGN_AXES}
        harmless_group = dict(pages=[1], intentional=False, sourceEvidence="",
                              visibleEvidence="Distinct composition is harmless and readable.")
        full_payload = dict(slides=[dict(index=1, evaluated=True, issues=[], axes=full_axes, designTargets=[])],
            deckFlow=dict(evaluated=True, visibleEvidence="The observed composition is well designed.",
                          repetitionGroups=[harmless_group]))
        full_schema = prompt_mod.baseline_review_json_schema(slide_count=1, context=full_context)
        validate(full_payload, full_schema)
        full_messages = prompt_mod.build_advisory_messages(b"", slide_count=1, required=True,
            pngs=[png], design_context=full_context)
        assert "every supplied composition.groups.pages cluster" in full_messages[0].content
        assert "including harmless clusters and well-designed diverse pages" in full_messages[0].content
        async def completed_review(*args, **kwargs):
            return "fixture-model", deepcopy(full_payload)
        monkeypatch.setattr(shared_advisory, "_request_advisory", completed_review)
        full_result = _run(shared_advisory.run_vision_advisory([png], slide_count=1,
            required=True, design_context=full_context))
        assert "skipped" not in full_result
        assert all(axis["verdict"] == "passed" for axis in full_result["designReview"]["pages"][0]["axes"].values())
        assert full_result["designReview"]["pages"][0]["designTargets"] == []
        full_payload["slides"][0]["axes"][names[0]]["severity"] = "minor"
        with pytest.raises(ValidationError):
            validate(full_payload, full_schema)
        invalid_axes = _run(shared_advisory.run_vision_advisory([png], slide_count=1,
            required=True, design_context=full_context))
        assert invalid_axes["schemaRejectionReason"] == "axis_semantics"
        assert invalid_axes["schemaRejectionFields"] == {"fieldNames": ["evaluated", "verdict", "severity", "repairScope"]}
        full_payload["slides"][0]["axes"][names[0]]["severity"] = "none"
        full_payload["deckFlow"]["repetitionGroups"] = []
        missing_group = _run(shared_advisory.run_vision_advisory([png], slide_count=1,
            required=True, design_context=full_context))
        assert missing_group["schemaRejectionReason"] == "full_composition_coverage"
        assert missing_group["schemaRejectionFields"] == {"fieldNames": ["deckFlow", "repetitionGroups", "pages"]}
        assert set(missing_group) == {"skipped", "schemaRejectionReason", "schemaRejectionFields",
                                      "parsedPageCount", "parsedPageIndexCoverage"}
        assert missing_group["parsedPageCount"] == 1
        assert missing_group["parsedPageIndexCoverage"] is True
        full_payload["deckFlow"]["repetitionGroups"] = [harmless_group]
        del full_payload["slides"][0]["designTargets"]
        missing_targets = _run(shared_advisory.run_vision_advisory([png], slide_count=1,
            required=True, design_context=full_context))
        assert missing_targets["schemaRejectionReason"] == "design_target_fields"
        assert set(missing_targets) == {"skipped", "schemaRejectionReason", "schemaRejectionFields",
                                        "parsedPageCount", "parsedPageIndexCoverage"}
        assert missing_targets["parsedPageCount"] == 1
        assert missing_targets["parsedPageIndexCoverage"] is True
        from utils.hashing import sha256_prefixed
        principles = prompt_mod.BASELINE_DESIGN_PRINCIPLES
        assert principles in full_messages[0].content
        legacy_principles = prompt_mod.LEGACY_BASELINE_DESIGN_PRINCIPLES
        legacy_principles_digest = "sha256:6e16d5429931c880019fa8346543acd5fd3a126f4172a57ec7635f0ba8c5d9ce"
        assert sha256_prefixed(legacy_principles.encode("utf-8")) == legacy_principles_digest
        assert prompt_mod.baseline_design_principles_digest() == legacy_principles_digest
        assert prompt_mod.baseline_design_principles_digest(full_context) == sha256_prefixed(principles.encode("utf-8"))
        for cue in ("actual painted distribution", "large empty bands", "Intentional whitespace",
                    "low occupancy alone is not a defect", "grounded designTarget", "Do not invent an issue"):
            assert cue in full_messages[0].content
            assert cue not in messages[0].content
        identity_context = dict(policy={"id": "svg-quality-first", "version": 2},
                                exampleDigests=[], priorCandidates={}, reviewScope="full",
                                reviewPrinciplesVersion=prompt_mod.DESIGN_PRINCIPLES_VERSION)
        old_v2_context = {key: value for key, value in identity_context.items() if key != "reviewPrinciplesVersion"}
        legacy_context = {**old_v2_context, "policy": {"id": "svg-quality-first", "version": 1}}
        no_policy_context = {**old_v2_context, "policy": None}
        digest_args = ([png], 0, 0, [0])
        current_digest = deck_quality._group_review_input_digest(identity_context, *digest_args)
        old_v2_digest = deck_quality._group_review_input_digest(old_v2_context, *digest_args)
        old_digest_context = {**{key: value for key, value in old_v2_context.items()
                                if key not in {'examples', 'priorCandidates'}},
                              "reviewPrinciplesDigest": legacy_principles_digest}
        assert old_v2_digest == deck_quality.content_digest(dict(context=old_digest_context,
            examples=[], pngDigests=[sha256_hex(png)], priorCandidates={}, groupId="phase-0/group-0", indices=[0]))
        assert current_digest != old_v2_digest
        legacy_digest = deck_quality._group_review_input_digest(legacy_context, *digest_args)
        no_policy_digest = deck_quality._group_review_input_digest(no_policy_context, *digest_args)
        review_context["reviewPrinciplesVersion"] = prompt_mod.DESIGN_PRINCIPLES_VERSION
        saved_input = deck_quality._group_review_input_digest(
            deck_quality._local_group_context(review_context, [0], True), *digest_args)
        provider_calls = []
        async def stale_checkpoint(*args):
            return {"input_digest": saved_input, "status": "completed"}
        async def forbidden_provider(*args, **kwargs):
            provider_calls.append(True)
            return {}
        with monkeypatch.context() as changed_principles:
            changed_principles.setattr(prompt_mod, "BASELINE_DESIGN_PRINCIPLES",
                                      principles + "Fixture semantic revision. ")
            assert deck_quality._group_review_input_digest(identity_context, *digest_args) != current_digest
            assert deck_quality._group_review_input_digest(old_v2_context, *digest_args) == old_v2_digest
            assert prompt_mod.baseline_design_principles_digest() == legacy_principles_digest
            assert deck_quality._group_review_input_digest(legacy_context, *digest_args) == legacy_digest
            assert deck_quality._group_review_input_digest(no_policy_context, *digest_args) == no_policy_digest
            changed_messages = prompt_mod.build_advisory_messages(b"", slide_count=1, required=True,
                pngs=[png], design_context=full_context)
            assert prompt_mod.BASELINE_DESIGN_PRINCIPLES in changed_messages[0].content
            stale_ctx = SimpleNamespace(snapshot={}, quality_recovery_review_group_checkpoint=stale_checkpoint)
            with pytest.raises(ValueError, match="svg_quality_recovery_review_group_conflict"):
                _run(deck_quality._quality_first_deck_advisory(stale_ctx, [(page, png)], job=job,
                    read_png=lambda _: png, run_advisory=forbidden_provider, phase=0))
            assert provider_calls == []

        # Existing failed gates keep their exact principles and verdict when read
        # or inherited; only explicitly fresh contexts use density review v2.
        from services.generation_core.quality.advisory_record import advisory_record, content_digest
        from services.svg_final_selected_continuation import (
            _consume_local_producer, INITIAL_NAMESPACE, NAMESPACE, FinalSelectedEvidenceError,
        )
        valid_pages = [(page, png)]
        failed_payload = deepcopy(full_payload)
        failed_payload['slides'][0]['designTargets'] = []
        failed_payload['slides'][0]['axes']['density_balance'].update(
            verdict='failed', severity='major', repairScope='design_rewrite',
            visibleEvidence='An unexplained lower band disconnects the source evidence.')
        failed_payload['slides'][0]['issues'] = ['empty_band']
        with monkeypatch.context() as actual_context:
            actual_context.setattr(deck_quality, '_design_context', build_design_context)
            actual_context.setattr(deck_quality, '_seal_design_gate', seal_design_gate)
            actual_context.setattr(deck_quality, '_candidate_local_valid', lambda *args, **kwargs: True)
            for version in (None, prompt_mod.DESIGN_PRINCIPLES_VERSION):
                frozen_context = build_design_context(fixture_ctx, valid_pages, job=None,
                    read_png=lambda _: png, review_principles_version=version)
                frozen_review = shared_advisory._parse_baseline_review(
                    failed_payload, slide_count=1, context=frozen_context)
                vision = dict(promptVersion=prompt_mod.DESIGN_REVIEW_PROMPT_VERSION,
                    reviewedPageIndices=[0], reviewedPngDigests=[sha256_hex(png)], designReview=frozen_review)
                frozen_gate = seal_design_gate(frozen_context, valid_pages, [png], vision)
                assert frozen_gate['status'] == 'failed'
                assert frozen_gate.get('reviewPrinciplesVersion') == version
                record = advisory_record({**vision, 'designGate': frozen_gate}, frozen_gate['pageDigests'])
                before_record = deepcopy(record)
                decision = deck_quality.quality_recovery_decisions(fixture_ctx, valid_pages, record)[0]
                assert decision['repair_scope'] == 'design_rewrite'
                assert decision['target_axes'] == ['density_balance']
                assert deck_quality.reusable_final_deck_advisory(fixture_ctx, valid_pages, record,
                    job=None, read_png=lambda _: png) == before_record
                assert record == before_record
                if version is None:
                    legacy_frozen_context = deepcopy(frozen_context)
                    legacy_gate = deepcopy(frozen_gate)
                    legacy_review = deepcopy(frozen_review)
                    legacy_vision = deepcopy(vision)
                    fresh_context = build_design_context(fixture_ctx, valid_pages, job=None,
                        read_png=lambda _: png, review_principles_version=prompt_mod.DESIGN_PRINCIPLES_VERSION)
                    producer_group = deck_quality._local_group_context(frozen_context, [0], False)
                    producer_input = deck_quality._group_review_input_digest(producer_group, *digest_args)
                    producer_body = dict(inputDigest=producer_input, globalPageIndices=[0], reviewScope='local',
                        groupId='phase-0/group-0', vision={'slides': [{'index': 1, 'issues': ['empty_band']}]})
                    producer = {**producer_body, 'digest': content_digest(producer_body)}
                    evidence = dict(proof=dict(owner_id=frozen_context['binding']['ownerId'],
                        tenant_id=frozen_context['binding']['tenantId'], source_operation_id=None, source_job_id=None),
                        consumer_runtime_equivalent=True, execution=dict(rounds=[dict(advisory=record,
                            groups={'0': dict(status='completed', group_id='phase-0/group-0', review_scope='local', indices=[0],
                                input_digest=producer_input, advisory=producer)})]))
                    reused, consumers = {}, []
                    with pytest.raises(FinalSelectedEvidenceError, match='svg_final_selected_context_unproven'):
                        _consume_local_producer(fresh_context, valid_pages, [png], evidence,
                            reused, consumers, INITIAL_NAMESPACE)
                    assert reused == {} and consumers == []
                    _consume_local_producer(frozen_context, valid_pages, [png], evidence,
                        reused, consumers, INITIAL_NAMESPACE)
                    assert reused == {0: producer}
                    assert consumers[0]['original_verdict_preserved'] is True
                    assert record == before_record
                forged_gate = {**frozen_gate, 'reviewPrinciplesVersion': 'unrecognized-principles'}
                forged_gate['digest'] = content_digest({key: value for key, value in forged_gate.items() if key != 'digest'})
                forged = advisory_record({**vision, 'designGate': forged_gate}, frozen_gate['pageDigests'])
                assert deck_quality._gate_intact(forged) is None
            with pytest.raises(ValueError, match='svg_quality_review_principles_version_invalid'):
                build_design_context(fixture_ctx, valid_pages, job=None, read_png=lambda _: png,
                    review_principles_version='unrecognized-principles')

        # A failed global call keeps its UNKNOWN record while all 100 sealed
        # local verdicts can feed the existing consumer path in a new operation.
        from services import svg_source_local_review_continuation as source_local
        from services.svg_presentation_checkpoint import _digest as source_local_digest
        source_local_snapshot = {"quality_recovery_policy": {"id": "svg-quality-first", "version": 2}}
        source_local_job = dict(id=str(UUID(int=101)), workspace_operation_id=str(UUID(int=102)),
            presentation_id=str(UUID(int=103)), svg_style_snapshot=source_local_snapshot,
            request_payload={"strategy": "svg", "instructions": "Preserve every source fact."})
        source_local_binding = dict(jobId=source_local_job["id"], operationId=source_local_job["workspace_operation_id"],
            presentationId=source_local_job["presentation_id"], ownerId=str(UUID(int=104)), tenantId="tenant")
        source_local_pages = []
        for index in range(100):
            source_svg = f'<svg xmlns="http://www.w3.org/2000/svg"><text>Source fact {index}</text></svg>'
            source_sha = sha256_hex(source_svg.encode())
            source_local_pages.append((dict(svg_content=source_svg, source_digest=source_sha,
                input_digest=f"source-input-{index}", png_path=f"source-{index}.png", png_sha256=sha256_hex(png),
                quality=dict(passed=True, geometry_passed=True, measurement_complete=True, verified=True,
                    source_digest=source_sha, png_digest=sha256_hex(png)),
                source_preservation={"manifest_digest": "source-manifest", "source_digest": "source-bytes"},
                attempts=[dict(kind="provider", raw_response=source_svg, receipt={"source": index})]), png))
        source_local_context = dict(policy=source_local_snapshot["quality_recovery_policy"],
            style={"canvas": {"width": 1280, "height": 720}}, priorCandidates={},
            styleDigest=content_digest({"canvas": {"width": 1280, "height": 720}}),
            snapshotDigest=content_digest(source_local_snapshot), runtimeBinding="1" * 64,
            binding=source_local_binding, mandatoryEvidence=[True] * 100, roles=["content"] * 100,
            examples=[], exampleDigests=[], deckComplete=True,
            reviewPrinciplesVersion=prompt_mod.DESIGN_PRINCIPLES_VERSION,
            sourceDirectives="Preserve every source fact.",
            pages=[dict(index=i, content=f"Full source fact {i}", referenceRoles=["content"],
                        preservation=page["source_preservation"], inputDigest=page["input_digest"])
                   for i, (page, _) in enumerate(source_local_pages)],
            composition={"pages": [{} for _ in range(100)],
                         "groups": [{"pages": list(range(100)), "representative": 0}]})
        source_local_groups = {}
        for ordinal in range(20):
            indices = list(range(ordinal * 5, ordinal * 5 + 5))
            group_context = deck_quality._local_group_context(source_local_context, indices, False)
            group_input = deck_quality._group_review_input_digest(group_context, [png] * 5, 0, ordinal, indices)
            local_review = []
            for local_index, index in enumerate(indices):
                local_axes = {name: dict(verdict="passed", severity="none", repairScope="none",
                    visibleEvidence="The source relationships remain readable.") for name in names}
                if index == 0:
                    local_axes[names[0]].update(verdict="failed", severity="major", repairScope="design_rewrite")
                local_review.append(dict(index=local_index, evaluated=True, axes=local_axes, issues=[],
                    designTargets=[deepcopy(goal)] if index in (0, 1) else []))
            group_body = dict(groupId=f"phase-0/group-{ordinal}", globalPageIndices=indices,
                inputDigest=group_input, reviewScope="local",
                vision=dict(promptVersion=prompt_mod.DESIGN_REVIEW_PROMPT_VERSION, slideCount=5,
                    reviewedPageIndices=list(range(5)), reviewedPngDigests=[sha256_hex(png)] * 5,
                    slides=[], designReview=dict(pages=local_review, candidateComparisons=[], deckFlow={"evaluated": False})))
            group_record = {**group_body, "digest": content_digest(group_body)}
            source_local_groups[str(ordinal)] = dict(status="completed", group_id=group_body["groupId"],
                review_scope="local", indices=indices, input_digest=group_input, advisory=group_record)
        source_local_groups["20"] = dict(status="review_unknown",
            failureDiagnostics={"providerErrorCode": "context_length_exceeded"})
        source_local_execution = dict(schema="svg-quality-recovery-execution/v1", binding="1" * 64,
            reviewPrinciplesVersion=prompt_mod.DESIGN_PRINCIPLES_VERSION,
            rounds=[dict(status="review_unknown", page_digests=[source_local_digest({"svg": p["svg_content"],
                "png": p["png_sha256"]}) for p, _ in source_local_pages], groups=source_local_groups)])
        source_local_pages[0][0]["quality_recovery_execution"] = source_local_execution
        source_local_before = deepcopy(source_local_execution)
        source_local_proof = source_local.build_source_local_proof(
            operation_id=source_local_job["workspace_operation_id"], job_id=source_local_job["id"],
            owner_id=source_local_binding["ownerId"], tenant_id="tenant", archive_digest="3" * 64,
            parent_manifest_hash="4" * 64, payloads=[p for p, _ in source_local_pages],
            execution=source_local_execution, source_job=source_local_job)
        from services import svg_partial_recovery as source_partial
        from services.generation_core.drivers.svg import quality_recovery as source_recovery
        from services.svg_cached_author_render_recovery import digest as archive_digest
        source_archive = {"job": deepcopy(source_local_job)}
        source_working = {"checkpoints": [dict(slide_index=i, svg_checkpoint=deepcopy(page))
                          for i, (page, _) in enumerate(source_local_pages)]}
        archive_proof = source_local.build_source_local_proof(
            operation_id=source_local_job["workspace_operation_id"], job_id=source_local_job["id"],
            owner_id=source_local_binding["ownerId"], tenant_id="tenant", archive_digest=archive_digest(source_archive),
            parent_manifest_hash="4" * 64, payloads=[p for p, _ in source_local_pages],
            execution=source_local_execution, source_job=source_local_job)
        restored_job, restored_pages = source_local.archived_source_local_payloads(
            source_archive, source_working, archive_proof)
        assert restored_job == source_local_job and restored_pages == [p for p, _ in source_local_pages]
        assert restored_pages[0] is not source_local_pages[0][0]
        for mutation in ("archive", "parent", "unknown_local", "coverage", "seal", "proof"):
            changed_archive, changed_working, changed_proof = deepcopy(source_archive), deepcopy(source_working), deepcopy(archive_proof)
            if mutation == "archive":
                changed_archive["job"]["request_payload"]["instructions"] = "Changed parent"
            elif mutation == "parent":
                changed_working["checkpoints"][0]["svg_checkpoint"]["svg_content"] += "changed"
            elif mutation in {"unknown_local", "coverage", "seal"}:
                changed_groups = changed_working["checkpoints"][0]["svg_checkpoint"]["quality_recovery_execution"]["rounds"][0]["groups"]
                if mutation == "unknown_local":
                    changed_groups["0"] = dict(status="review_unknown", failureDiagnostics={})
                elif mutation == "coverage":
                    changed_groups.pop("0")
                else:
                    changed_groups["0"]["advisory"]["digest"] = "changed"
            else:
                changed_proof["digest"] = "changed"
            with pytest.raises(FinalSelectedEvidenceError):
                source_local.archived_source_local_payloads(changed_archive, changed_working, changed_proof)

        loader_manifest = dict(owner_id=source_local_binding["ownerId"], tenant_id="tenant",
            operation_id=source_local_job["workspace_operation_id"], job_id=source_local_job["id"],
            presentation_id=source_local_job["presentation_id"], source_local_continuation=archive_proof,
            append_only_recovery=True, source_request_routing={"presentation_id_present": False, "presentation_id": None})
        loader_job = SimpleNamespace(id=UUID(int=111), workspace_operation_id=UUID(int=112),
            presentation_id=UUID(int=113), svg_style_snapshot=source_local_snapshot,
            request_payload={**source_local_job["request_payload"], "presentation_id": str(UUID(int=113)),
                source_partial.LINEAGE_KEY: dict(schema="svg-partial-recovery-lineage/v2",
                    source_job_id=loader_manifest["job_id"], source_presentation_id=loader_manifest["presentation_id"],
                    presentation_id=str(UUID(int=113)), manifest_hash=archive_digest(loader_manifest))})
        loader_reads = []
        class LocalReviewSession:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return None
            async def get(self, model, operation_id):
                assert operation_id == loader_job.workspace_operation_id
                return SimpleNamespace(request_payload={source_partial.ARCHIVE_KEY: source_archive})
            async def rollback(self):
                loader_reads.append("rollback")
        async def loader_authority(job, hooks):
            assert job is loader_job
            loader_reads.append("authority")
        async def loader_verify(session, operation, job):
            assert job is loader_job
            loader_reads.append("prepared-episode")
            return loader_ctx.partial_recovery_manifest
        async def loader_hydrate(session, manifest, archive):
            assert archive is source_archive
            loader_reads.append("fresh-archive")
            return source_working
        def loader_runtime(ctx, request, producer):
            assert producer.workspace_operation_id == source_local_job["workspace_operation_id"]
            assert request.instructions == source_local_job["request_payload"]["instructions"]
            return "1" * 64
        loader_ctx = SimpleNamespace(partial_recovery_manifest=deepcopy(loader_manifest),
            quality_recovery_checkpoint_reuse_eligible=True)
        loader_hooks = SimpleNamespace(async_session_maker=LocalReviewSession)
        with monkeypatch.context() as loader_scope:
            loader_scope.setattr(source_recovery, "ensure_recovery_authority", loader_authority)
            loader_scope.setattr(source_recovery, "recovery_binding", loader_runtime)
            loader_scope.setattr(source_partial, "verify_prepared_episode", loader_verify)
            loader_scope.setattr(source_partial, "_hydrate_checkpoint_archive", loader_hydrate)
            loaded_source = _run(source_local.prepare_source_local_source(loader_ctx, loader_job, loader_hooks))
            assert loaded_source["execution"] == source_local_before
            assert loader_reads == ["authority", "prepared-episode", "fresh-archive", "rollback"]
            for field in ("owner_id", "tenant_id", "source_operation_id", "source_job_id", "source_presentation_id"):
                loader_ctx.partial_recovery_manifest = deepcopy(loader_manifest)
                loader_ctx.partial_recovery_manifest["source_local_continuation"][field] = "foreign"
                before_reads = len(loader_reads)
                with pytest.raises(FinalSelectedEvidenceError):
                    _run(source_local.prepare_source_local_source(loader_ctx, loader_job, loader_hooks))
                assert loader_reads[before_reads:] == ["authority", "prepared-episode"]
        source_local_current = deepcopy(source_local_context)
        source_local_current.update(binding={**source_local_binding, "jobId": str(UUID(int=111)),
            "operationId": str(UUID(int=112)), "presentationId": str(UUID(int=113))}, runtimeBinding="5" * 64)
        source_local_evidence = dict(proof=source_local_proof, consumer_namespace=INITIAL_NAMESPACE,
            execution=source_local_execution, producer_job=source_local_job,
            producer_payloads=[p for p, _ in source_local_pages], consumer_runtime_equivalent=True,
            consumer_binding=source_local_current["binding"])
        reused_local, local_consumers = source_local.reusable_source_local_groups(
            source_local_current, source_local_pages, [png] * 100, source_local_evidence)
        assert len(reused_local) == len(local_consumers) == 20
        assert reused_local[0] == source_local_groups["0"]["advisory"]
        assert reused_local[0]["vision"]["designReview"]["pages"][0]["axes"][names[0]]["verdict"] == "failed"
        assert reused_local[0]["vision"]["designReview"]["pages"][1]["designTargets"] == [goal]
        changed_final_pages = deepcopy(source_local_pages)
        changed_final_pages[0][0]["svg_content"] += " "
        changed_final_sha = sha256_hex(changed_final_pages[0][0]["svg_content"].encode())
        changed_final_pages[0][0]["source_digest"] = changed_final_sha
        changed_final_pages[0][0]["quality"]["source_digest"] = changed_final_sha
        final_local_reused, final_local_consumers = {}, []
        source_local._consume_source_local_producer(source_local_current, changed_final_pages, [png] * 100,
            source_local_evidence, final_local_reused, final_local_consumers, source_local.NAMESPACE)
        assert set(final_local_reused) == set(range(1, 20))
        assert len(final_local_consumers) == 19 and all(item["namespace"] == source_local.NAMESPACE
                                                       for item in final_local_consumers)
        with pytest.raises(FinalSelectedEvidenceError):
            source_local.reusable_source_local_groups(source_local_current, changed_final_pages, [png] * 100, source_local_evidence)
        for mutation in ("owner", "tenant", "source", "instructions", "style", "principles", "runtime", "png", "svg", "seal"):
            changed_context, changed_evidence = deepcopy(source_local_current), deepcopy(source_local_evidence)
            changed_pages, changed_renders = deepcopy(source_local_pages), [png] * 100
            if mutation in {"owner", "tenant"}:
                changed_context["binding"]["ownerId" if mutation == "owner" else "tenantId"] = "foreign"
            elif mutation == "source":
                changed_context["pages"][0]["content"] = "Changed source fact"
            elif mutation == "instructions":
                changed_context["sourceDirectives"] = "Changed user constraint"
            elif mutation == "style":
                changed_context["snapshotDigest"] = "changed"
            elif mutation == "principles":
                changed_context["reviewPrinciplesVersion"] = None
            elif mutation == "runtime":
                changed_evidence["consumer_runtime_equivalent"] = False
            elif mutation == "png":
                changed_renders[0] = b"changed-png"
            elif mutation == "svg":
                changed_pages[0][0]["svg_content"] += "changed"
            else:
                changed_evidence["execution"]["rounds"][0]["groups"]["0"]["advisory"]["digest"] = "changed"
            with pytest.raises(FinalSelectedEvidenceError):
                source_local.reusable_source_local_groups(changed_context, changed_pages, changed_renders, changed_evidence)
        source_local_calls, source_local_saved = [], {}

        async def source_local_checkpoint(ordinal, marker=None):
            if marker is not None:
                source_local_saved[ordinal] = deepcopy(marker)
            return deepcopy(source_local_saved.get(ordinal))

        async def source_local_global_only(images, **kwargs):
            assert kwargs["design_context"]["reviewScope"] == "global"
            assert kwargs["design_context"]["globalScopeRevision"] == "rendered-source-deck-flow/v2"
            assert kwargs["design_context"]["globalWireProjectionRevision"] == "source-relations/v3"
            assert kwargs["design_context"]["localReviewSummary"]["evaluatedPageCount"] == 100
            assert len(kwargs["design_context"]["localReviewSummary"]["sealedGroupDigests"]) == 20
            source_local_calls.append("global")
            return dict(promptVersion=prompt_mod.DESIGN_REVIEW_PROMPT_VERSION, model="fixture-model",
                reviewedPageIndices=list(range(100)), reviewedPngDigests=[sha256_hex(png)] * 100, slides=[],
                designReview=dict(pages=[dict(index=i, evaluated=True, issues=[], designTargets=[],
                    axes={prompt_mod.DESIGN_AXES[-1]: dict(verdict="passed", severity="none", repairScope="none",
                        visibleEvidence="Deck relationships remain distinct.")}) for i in range(100)],
                    candidateComparisons=[], deckFlow={"evaluated": True}))

        from services.source_content.contracts import SourceContentManifest
        source_local_document = SourceContentManifest.model_validate(dict(
            manifest_id="source-manifest", owner_id=source_local_binding["ownerId"], reference_snapshot_id="source-reference",
            source_digest="source-bytes", manifest_digest="source-manifest",
            object_inventory=dict(discovered_object_count=100, classified_object_count=100, content_bearing_object_count=100),
            inventory_objects=[dict(object_id=f"source-{i}", object_type="slide_object", classification="text",
                source_unit_id=f"source-page-{i}", content_bearing=True) for i in range(100)],
            units=[dict(source_unit_id=f"source-page-{i}", source_index=i, blocks=[dict(source_block_id=f"source-{i}",
                kind="text", semantic_role="content", order=0, payload={"text": f"Full source fact {i}"},
                content_digest=content_digest({"text": f"Full source fact {i}"}))]) for i in range(100)],
            counts=dict(source_units=100, text_blocks=100, table_cells=0, chart_values=0, unique_images=0, image_occurrences=0)))
        with monkeypatch.context() as source_local_hooks:
            source_local_hooks.setattr(svg_authoring_profile, "read_quality_recovery_execution_plan",
                lambda *args, **kwargs: dict(slide_count=100, review_round_limit=2, phase_group_limit=22,
                    review_group_size=5, global_format_retry_limit=1))
            source_local_hooks.setattr(deck_quality, "_design_context", lambda *args, **kwargs: deepcopy(source_local_current))
            source_local_hooks.setattr(deck_quality, "_seal_design_gate", seal_design_gate)
            child_ctx = SimpleNamespace(snapshot=source_local_snapshot, source_reflow=True,
                source_document=source_local_document, source_units=source_local_document.units,
                quality_recovery_runtime_binding="5" * 64, quality_recovery_input_binding="5" * 64,
                quality_recovery_review_group_checkpoint=source_local_checkpoint)
            child_job = SimpleNamespace(id=UUID(int=111), workspace_operation_id=UUID(int=112), attempt_number=1,
                request_payload={"_svg_partial_recovery": True})
            child_record = _run(deck_quality._quality_first_deck_advisory(child_ctx, source_local_pages,
                job=child_job, read_png=lambda _: png, run_advisory=source_local_global_only,
                phase=0, source_evidence=source_local_evidence))
        assert source_local_calls == ["global"] and set(source_local_saved) == {20}
        assert child_record["vision"]["designGate"]["status"] == "failed"
        assert child_record["vision"]["designGate"]["designImprovementStatus"] == "needed"
        assert len(child_record["vision"]["localEvidenceConsumers"]) == 20
        assert source_local_execution == source_local_before
        # Existing phase22 control already reserves GLOBAL ordinals20 and21.
        # A structural rejection may use slot21 once, with no LOCAL recall.
        original_local_calls, original_local_saved = list(source_local_calls), deepcopy(source_local_saved)
        for retry_reason in ("index_coverage", "global_review_fields"):
            for fail_both in (False, True):
                source_local_calls.clear()
                source_local_saved.clear()
                async def bounded_global(images, **kwargs):
                    assert kwargs["design_context"]["reviewScope"] == "global"
                    if not source_local_calls or fail_both:
                        if source_local_calls:
                            assert kwargs["design_context"]["formatRetry"]["attempt"] == 1
                            assert kwargs["design_context"]["formatRetry"]["reason"] == retry_reason
                        source_local_calls.append("global")
                        return dict(skipped="design_review_schema_or_coverage_invalid",
                            schemaRejectionReason=retry_reason,
                            schemaRejectionFields=dict(partitionCoverage=dict(missingPageCount=1)))
                    assert kwargs["design_context"]["formatRetry"] == dict(attempt=1,
                        reason=retry_reason, fields=dict(partitionCoverage=dict(missingPageCount=1)))
                    return await source_local_global_only(images, **kwargs)
                with monkeypatch.context() as retry_hooks:
                    retry_hooks.setattr(svg_authoring_profile, "read_quality_recovery_execution_plan",
                        lambda *args, **kwargs: dict(slide_count=100, review_round_limit=2,
                            phase_group_limit=22, review_group_size=5, global_format_retry_limit=1))
                    retry_hooks.setattr(deck_quality, "_design_context", lambda *args, **kwargs: deepcopy(source_local_current))
                    retry_hooks.setattr(deck_quality, "_seal_design_gate", seal_design_gate)
                    retry_record = _run(deck_quality._quality_first_deck_advisory(child_ctx, source_local_pages,
                        job=child_job, read_png=lambda _: png, run_advisory=bounded_global,
                        phase=0, source_evidence=source_local_evidence))
                assert source_local_calls == ["global", "global"]
                assert set(source_local_saved) == {20, 21}
                assert [source_local_saved[index]["physical_ordinal"] for index in (20, 21)] == [20, 21]
                assert all(source_local_saved[index]["status"] == "completed" for index in (20, 21))
                assert source_local_saved[20]["advisory"]["vision"]["schemaRejectionReason"] == retry_reason
                assert len(retry_record["vision"]["localEvidenceConsumers"]) == 20
                if fail_both:
                    assert retry_record["vision"]["designReview"]["pages"] == []
                    assert retry_record["vision"]["designReview"]["deckFlow"]["evaluated"] is False
                    assert retry_record["vision"]["designGate"]["status"] != "passed"
                else:
                    assert len(retry_record["vision"]["designReview"]["pages"]) == 100
                assert source_local_execution == source_local_before
        source_local_calls[:] = original_local_calls
        source_local_saved.clear()
        source_local_saved.update(original_local_saved)

        # A completed continuation retains its UNKNOWN leaf and reuses all
        # twenty local records without pretending it authored them again.
        from utils import owner_context
        from services import svg_asset_persistence
        from services.svg_asset_manifest import SourceManifest, ArtifactPointer
        bundle_owner, bundle_tenant = UUID(source_local_binding["ownerId"]), "tenant"
        bundle_transaction = object()
        bundle_session = SimpleNamespace(sync_session=SimpleNamespace(get_transaction=lambda: bundle_transaction))
        bundle_asset = SourceManifest.seal(dict(presentationId=str(UUID(int=201)), sourceRevision=1,
            slots=[], offeredPages=[]))
        changed_bundle_asset = SourceManifest.seal(dict(presentationId=str(UUID(int=201)), sourceRevision=2,
            slots=[], offeredPages=[]))
        bundle_pointers = [ArtifactPointer(ref=UUID(int=i), sha256="a" * 64) for i in (201, 202)]
        bundle_references = {pointer.ref: SimpleNamespace(kind="source-manifest", media_type="application/json",
            created_by_operation_id=UUID(int=203)) for pointer in bundle_pointers}
        bundle_reads, bundle_changed = [], False
        async def bundle_resolve(session, *, artifact_id, owner_id, tenant_id, expected_digest):
            assert session is bundle_session and (owner_id, tenant_id)==(bundle_owner,bundle_tenant)
            return bundle_references[artifact_id]
        async def bundle_load(session, service, *, pointer, owner_id, tenant_id):
            assert session is bundle_session and (owner_id, tenant_id)==(bundle_owner,bundle_tenant)
            bundle_reads.append(pointer.ref)
            return changed_bundle_asset if bundle_changed else bundle_asset
        async def bundle_fresh_row(query):
            assert query.get_execution_options().get("populate_existing") is True
            return object()
        bundle_session.scalar = bundle_fresh_row
        async def bundle_reuse_cases():
            nonlocal bundle_changed
            service = SimpleNamespace(resolve_ref=bundle_resolve)
            context = ("same-request", "same-snapshot")
            reuse = source_partial._SourceBundleRequestReuse(bundle_session,service,
                owner=bundle_owner,tenant=bundle_tenant,context=context)
            for _ in range(100):
                assert await reuse.load(bundle_session,pointer=bundle_pointers[0],reference=bundle_references[bundle_pointers[0].ref],
                    owner=bundle_owner,tenant=bundle_tenant,context=context) is bundle_asset
            assert len(bundle_reads)==1
            await reuse.finish()
            assert len(bundle_reads)==2
            with pytest.raises(source_partial.PartialRecoveryRejected):
                await reuse.load(bundle_session,pointer=bundle_pointers[0],reference=bundle_references[bundle_pointers[0].ref],
                    owner=bundle_owner,tenant=bundle_tenant,context=context)
            bundle_reads.clear()
            distinct = source_partial._SourceBundleRequestReuse(bundle_session,service,
                owner=bundle_owner,tenant=bundle_tenant,context=context)
            for pointer in bundle_pointers:
                await distinct.load(bundle_session,pointer=pointer,reference=bundle_references[pointer.ref],
                    owner=bundle_owner,tenant=bundle_tenant,context=context)
            await distinct.finish()
            assert len(bundle_reads)==4
            changed = source_partial._SourceBundleRequestReuse(bundle_session,service,
                owner=bundle_owner,tenant=bundle_tenant,context=context)
            await changed.load(bundle_session,pointer=bundle_pointers[0],reference=bundle_references[bundle_pointers[0].ref],
                owner=bundle_owner,tenant=bundle_tenant,context=context)
            bundle_changed=True
            with pytest.raises(source_partial.PartialRecoveryRejected):
                await changed.finish()
        with monkeypatch.context() as bundle_scope:
            bundle_scope.setattr(owner_context,"get_current_owner_id",lambda:bundle_owner)
            bundle_scope.setattr(owner_context,"get_current_tenant_id",lambda:bundle_tenant)
            bundle_scope.setattr(svg_asset_persistence,"load_source_manifest_pointer",bundle_load)
            _run(bundle_reuse_cases())
        flat_manifest = {**loader_manifest, "archive_digest": archive_digest(source_archive)}
        flat_request = {**source_local_job["request_payload"], "presentation_id": str(UUID(int=113)),
            source_partial.LINEAGE_KEY: dict(schema="svg-partial-recovery-lineage/v2",
                source_job_id=flat_manifest["job_id"], source_presentation_id=flat_manifest["presentation_id"],
                presentation_id=str(UUID(int=113)), manifest_hash=archive_digest(flat_manifest))}
        flat_job = dict(id=str(UUID(int=111)), workspace_operation_id=str(UUID(int=112)),
            presentation_id=str(UUID(int=113)), svg_style_snapshot=source_local_snapshot, request_payload=flat_request)
        flat_operation = SimpleNamespace(id=UUID(int=112), presentation_id=UUID(int=113),
            retry_of_operation_id=UUID(source_local_job["workspace_operation_id"]), request_payload={
                "svg_partial_recovery_manifest": flat_manifest,
                "svg_partial_recovery_manifest_hash": archive_digest(flat_manifest),
                source_partial.ARCHIVE_KEY: source_archive})
        flat_vision = deepcopy(child_record["vision"])
        for consumer in flat_vision["localEvidenceConsumers"]:
            consumer["producer_source_local_digest"] = archive_proof["digest"]
        flat_advisory = advisory_record(flat_vision, child_record["pageDigests"])
        flat_execution = dict(schema=source_local_execution["schema"], binding="5" * 64,
            reviewPrinciplesVersion=source_local_execution["reviewPrinciplesVersion"],
            rounds=[dict(status="completed", page_digests=deepcopy(source_local_execution["rounds"][0]["page_digests"]),
                groups={"20": deepcopy(source_local_saved[20])}, advisory=flat_advisory)],
            initial_continuation_review=dict(status="completed", namespace=INITIAL_NAMESPACE,
                initial_continuation=True, producer_proof=archive_proof, source_proof_digest=archive_proof["digest"],
                advisory=flat_advisory))
        flat_pages = deepcopy(source_local_pages)
        flat_pages[0][0]["quality_recovery_execution"] = flat_execution
        flat_args = dict(operation_id=flat_job["workspace_operation_id"], job_id=flat_job["id"],
            owner_id=source_local_binding["ownerId"], tenant_id="tenant", archive_digest="8" * 64,
            parent_manifest_hash=archive_digest(flat_manifest), payloads=[p for p, _ in flat_pages],
            execution=flat_execution, source_job=flat_job)
        flat_reads = []
        async def flat_hydrate(session, manifest, archive, **kwargs):
            assert manifest == flat_manifest and archive == source_archive
            flat_reads.append("one-leaf")
            return source_working
        with monkeypatch.context() as flat_scope:
            flat_scope.setattr(owner_context, "get_current_owner_id", lambda: UUID(source_local_binding["ownerId"]))
            flat_scope.setattr(owner_context, "get_current_tenant_id", lambda: "tenant")
            flat_scope.setattr(source_partial, "_hydrate_checkpoint_archive", flat_hydrate)
            flat_before = deepcopy(flat_execution)
            flat_proof = _run(source_local.build_source_local_manifest_proof(None,
                source_operation=flat_operation, **flat_args))
            assert flat_reads == ["one-leaf"]
            assert flat_execution == flat_before and source_local_execution == source_local_before
            assert flat_proof["flattened_local"]["proof"] == archive_proof
            assert flat_proof["flattened_local"]["execution"]["rounds"][0]["status"] == "review_unknown"
            assert flat_proof["completed_gate_digest"] == flat_advisory["vision"]["designGate"]["digest"]
            assert flat_advisory["vision"]["designGate"]["status"] == "failed"
            next_context = deepcopy(source_local_current)
            next_context["binding"].update(jobId=str(UUID(int=121)), operationId=str(UUID(int=122)),
                                           presentationId=str(UUID(int=123)))
            next_context["runtimeBinding"] = "9" * 64
            next_evidence = dict(proof=flat_proof, execution=flat_execution, producer_job=flat_job,
                producer_payloads=flat_args["payloads"], consumer_runtime_equivalent=True,
                consumer_binding=next_context["binding"])
            again, again_consumers = source_local.reusable_source_local_groups(
                next_context, flat_pages, [png] * 100, next_evidence)
            assert len(again) == len(again_consumers) == 20
            assert again[0] == source_local_groups["0"]["advisory"]
            assert all(c["producer_operation_id"] == source_local_job["workspace_operation_id"]
                       and c["consumer_source_local_digest"] == flat_proof["digest"] for c in again_consumers)
            changed_args = deepcopy(flat_args)
            for changed_index in (7, 12):
                changed_page = changed_args["payloads"][changed_index]
                changed_page["svg_content"] += " "
                changed_digest = sha256_hex(changed_page["svg_content"].encode())
                changed_page["source_digest"] = changed_digest
                changed_page["quality"]["source_digest"] = changed_digest
                changed_page["attempts"].append(dict(kind="provider", raw_response=changed_page["svg_content"], receipt={}))
            changed_proof = source_local.build_source_local_proof(**changed_args,
                flattened_leaf=deepcopy(flat_proof["flattened_local"]))
            assert changed_proof["fresh_local_indices"] == list(range(5, 15))
            assert len(changed_proof["source_local_group_digests"]) == 18
            changed_evidence = {**next_evidence, "proof": changed_proof,
                "producer_payloads": changed_args["payloads"], "execution": changed_args["execution"]}
            changed_pages = [(payload, png) for payload in changed_args["payloads"]]
            changed_reuse, changed_consumers = source_local.reusable_source_local_groups(
                next_context, changed_pages, [png] * 100, changed_evidence)
            assert set(changed_reuse) == set(range(20)) - {1, 2}
            assert len(changed_consumers) == 18
            assert changed_reuse[0] == source_local_groups["0"]["advisory"]
            assert changed_args["execution"] == flat_execution and source_local_execution == source_local_before
            unknown_args = deepcopy(flat_args)
            unknown_raw = unknown_args["payloads"]
            unknown_raw[3]["svg_content"] = None
            unknown_raw[3]["inflight"] = 2
            unknown_before = deepcopy(unknown_raw)
            unknown_rows = [dict(slide_index=i, svg_checkpoint=payload) for i, payload in enumerate(unknown_raw)]
            unknown_archive = dict(job=flat_job)
            unknown_args["archive_digest"] = source_partial.digest(unknown_archive)
            unknown_classification = {"3": dict(category="unknown", row_digest=source_partial.digest(unknown_rows[3]),
                source_payload_digest=source_partial.digest(unknown_raw[3]))}
            unknown_proof = source_local.build_source_local_proof(**unknown_args,
                flattened_leaf=deepcopy(flat_proof["flattened_local"]), raw_payloads=unknown_raw,
                fresh_page_witnesses=unknown_classification)
            assert unknown_proof["source_pages"][3]["phase"] == "fresh-author-required"
            assert unknown_proof["source_pages"][3]["source_category"] == "unknown"
            assert "png_digest" not in unknown_proof["source_pages"][3]
            assert unknown_proof["fresh_local_indices"] == list(range(5))
            assert len(unknown_proof["source_local_group_digests"]) == 19
            unknown_manifest_pages = [dict(slide_index=i, category="unknown" if i==3 else "known",
                row_digest=source_partial.digest(row)) for i,row in enumerate(unknown_rows)]
            _, replay_unknown = source_local.archived_source_local_payloads(
                unknown_archive, dict(checkpoints=unknown_rows), unknown_proof, manifest_pages=unknown_manifest_pages)
            assert replay_unknown == unknown_raw == unknown_before
            unknown_evidence = {**next_evidence, "proof": unknown_proof, "producer_payloads": unknown_raw,
                "producer_raw_payloads": unknown_raw}
            unknown_reuse, unknown_consumers = source_local.reusable_source_local_groups(
                next_context, flat_pages, [png] * 100, unknown_evidence)
            assert set(unknown_reuse) == set(range(1, 20)) and len(unknown_consumers) == 19
            assert unknown_raw == unknown_before and unknown_raw[3]["inflight"] == 2
            forged_classification = deepcopy(unknown_classification)
            forged_classification["3"]["category"] = "known"
            with pytest.raises(FinalSelectedEvidenceError):
                source_local.build_source_local_proof(**unknown_args,
                    flattened_leaf=deepcopy(flat_proof["flattened_local"]), raw_payloads=unknown_raw,
                    fresh_page_witnesses=forged_classification)
            changed_unknown_row = deepcopy(unknown_rows)
            changed_unknown_row[3]["svg_checkpoint"]["inflight"] = 3
            with pytest.raises(FinalSelectedEvidenceError):
                source_local.archived_source_local_payloads(unknown_archive,
                    dict(checkpoints=changed_unknown_row), unknown_proof, manifest_pages=unknown_manifest_pages)
            wrong_manifest_pages = deepcopy(unknown_manifest_pages)
            wrong_manifest_pages[3]["category"] = "known"
            with pytest.raises(FinalSelectedEvidenceError):
                source_local.archived_source_local_payloads(unknown_archive,
                    dict(checkpoints=unknown_rows), unknown_proof, manifest_pages=wrong_manifest_pages)
            # A sealed current author-only result is fresh evidence, not an
            # old PNG baseline. Archive replay keeps its original row intact.
            from services.svg_cached_author_render_recovery import author_witness
            author_args = deepcopy(flat_args)
            author_raw = deepcopy(author_args["payloads"])
            author_raw[3]["quality_recovery"] = dict(status="repair_pending", prior_candidate={"fixture": "prior"})
            author_page = deepcopy(author_raw[3])
            for key in ("quality", "initial_quality", "png_path", "png_sha256", "browser_metrics", "local_readability_repair"):
                author_page.pop(key, None)
            author_page["source_digest"] = None
            author_page["last_valid_svg"] = author_page["svg_content"]
            author_raw[3]["svg_content"] = None
            author_args["payloads"] = deepcopy(author_raw)
            author_args["payloads"][3] = author_page
            author_witness_value = dict(schema="svg-completed-repair-author-projection/v1",
                source_operation_id=flat_args["operation_id"], source_payload_digest=source_partial.digest(author_raw[3]),
                prior_candidate_digest=source_partial.digest(author_raw[3]["quality_recovery"]["prior_candidate"]),
                source_manifest_hash=flat_args["parent_manifest_hash"],
                approved_gate_digest=flat_proof["completed_gate_digest"],
                completed_receipt_digest="a" * 64, completed_output_digest="b" * 64,
                current_author_svg_digest=sha256_hex(author_page["svg_content"].encode()),
                current_author_receipt_digest=author_witness(author_page), input_digest=author_raw[3]["input_digest"])
            author_projections = {"3": dict(witness=author_witness_value,
                projected_payload_digest=source_partial.digest(author_page))}
            author_proof = source_local.build_source_local_proof(**author_args,
                flattened_leaf=deepcopy(flat_proof["flattened_local"]), raw_payloads=author_raw,
                baseline_projections=author_projections)
            assert author_proof["source_pages"][3]["phase"] == "fresh-author-only"
            assert "png_digest" not in author_proof["source_pages"][3]
            assert author_proof["fresh_local_indices"] == list(range(5))
            assert len(author_proof["source_local_group_digests"]) == 19
            replay_args = {**author_args, "payloads": author_raw}
            replay_proof = source_local.build_source_local_proof(**replay_args,
                flattened_leaf=deepcopy(flat_proof["flattened_local"]), raw_payloads=author_raw,
                baseline_projections=author_projections)
            assert replay_proof == author_proof
            author_evidence = {**next_evidence, "proof": author_proof, "producer_payloads": author_args["payloads"],
                "producer_raw_payloads": author_raw}
            author_reuse, author_consumers = source_local.reusable_source_local_groups(
                next_context, flat_pages, [png] * 100, author_evidence)
            assert set(author_reuse) == set(range(1, 20)) and len(author_consumers) == 19
            assert author_proof["completed_gate_digest"] == flat_proof["completed_gate_digest"]
            bad_author = deepcopy(author_projections)
            bad_author["3"]["witness"]["source_payload_digest"] = "c" * 64
            with pytest.raises(FinalSelectedEvidenceError):
                source_local.build_source_local_proof(**author_args,
                    flattened_leaf=deepcopy(flat_proof["flattened_local"]), raw_payloads=author_raw,
                    baseline_projections=bad_author)
            unknown_author = deepcopy(author_projections)
            unknown_author["3"]["witness"]["schema"] = "unknown-author-projection/v1"
            with pytest.raises(FinalSelectedEvidenceError):
                source_local.build_source_local_proof(**replay_args,
                    flattened_leaf=deepcopy(flat_proof["flattened_local"]), raw_payloads=author_raw,
                    baseline_projections=unknown_author)
            # Seventeen original leaf groups plus three real source-episode
            # groups retain their own producer identities after global failure.
            mixed_prior_args = deepcopy(changed_args)
            mixed_changed_page = mixed_prior_args["payloads"][3]
            mixed_changed_page["svg_content"] += " "
            mixed_changed_page["source_digest"] = sha256_hex(mixed_changed_page["svg_content"].encode())
            mixed_changed_page["quality"]["source_digest"] = mixed_changed_page["source_digest"]
            mixed_prior = source_local.build_source_local_proof(**mixed_prior_args,
                flattened_leaf=deepcopy(flat_proof["flattened_local"]))
            assert mixed_prior["fresh_local_indices"] == list(range(15))
            mixed_payloads = deepcopy(mixed_prior_args["payloads"])
            mixed_pages = [(payload,png) for payload in mixed_payloads]
            mixed_vector = source_local._source_local_vector(mixed_payloads)
            mixed_groups, mixed_records = {},[]
            for ordinal in range(20):
                marker = deepcopy(source_local_execution["rounds"][0]["groups"][str(ordinal)])
                if ordinal<3:
                    indices=marker["indices"]
                    group_context=deck_quality._local_group_context(next_context,indices,False)
                    input_digest=deck_quality._group_review_input_digest(group_context,[png]*5,0,ordinal,indices)
                    marker.update(input_digest=input_digest,namespace=INITIAL_NAMESPACE,
                        physical_purpose="initial-continuation",physical_ordinal=ordinal)
                    marker["advisory"]["inputDigest"]=input_digest
                    marker["advisory"]["digest"]=content_digest({k:v for k,v in marker["advisory"].items() if k!="digest"})
                    mixed_groups[str(ordinal)]=marker
                mixed_records.append(marker["advisory"])
            mixed_global=deepcopy(flat_execution["rounds"][0]["groups"]["20"])
            mixed_global["advisory"]["vision"]["designReview"]["deckFlow"]={"evaluated":False}
            mixed_global["advisory"]["digest"]=content_digest({k:v for k,v in mixed_global["advisory"].items() if k!="digest"})
            mixed_groups["20"]=mixed_global
            mixed_records.append(mixed_global["advisory"])
            mixed_vision=deepcopy(flat_advisory["vision"])
            mixed_vision["reviewGroups"]=mixed_records
            mixed_vision["designReview"]["deckFlow"]={"evaluated":False}
            for reviewed in mixed_vision["designReview"]["pages"]:
                reviewed["evaluated"]=False
                reviewed["axes"]["repetition_deck_flow"].update(verdict="unreviewed",severity="none",repairScope="none")
            mixed_consumers=[]
            for consumer in flat_vision["localEvidenceConsumers"]:
                if consumer["indices"][0]//5<3:
                    continue
                consumer=deepcopy(consumer)
                consumer.update(consumer_binding=deepcopy(next_context["binding"]),consumer_source_local_digest=mixed_prior["digest"])
                mixed_consumers.append(consumer)
            mixed_vision["localEvidenceConsumers"]=mixed_consumers
            mixed_vision["designGate"]=seal_design_gate(next_context,mixed_pages,[png]*100,mixed_vision)
            mixed_advisory=advisory_record(mixed_vision,[item["svg_digest"] for item in mixed_vector])
            mixed_execution=dict(schema=flat_execution["schema"],binding=next_context["runtimeBinding"],
                reviewPrinciplesVersion=flat_execution["reviewPrinciplesVersion"],
                rounds=[dict(status="completed",page_digests=[source_partial.digest({"svg":p["svg_content"],"png":p["png_sha256"]})
                    for p in mixed_payloads],groups=mixed_groups,advisory=mixed_advisory)],
                initial_continuation_review=dict(status="completed",namespace=INITIAL_NAMESPACE,initial_continuation=True,
                    producer_proof=mixed_prior,source_proof_digest=mixed_prior["digest"],advisory=mixed_advisory))
            mixed_payloads[0]["quality_recovery_execution"]=mixed_execution
            mixed_job={**deepcopy(flat_job),"id":next_context["binding"]["jobId"],
                "workspace_operation_id":next_context["binding"]["operationId"],"presentation_id":next_context["binding"]["presentationId"]}
            mixed_proof=source_local.build_source_local_proof(operation_id=mixed_job["workspace_operation_id"],job_id=mixed_job["id"],
                owner_id=flat_args["owner_id"],tenant_id=flat_args["tenant_id"],archive_digest="d"*64,
                parent_manifest_hash="e"*64,payloads=mixed_payloads,execution=mixed_execution,source_job=mixed_job,
                flattened_leaf=deepcopy(flat_proof["flattened_local"]))
            assert mixed_proof["source_local_group_origins"]==["source"]*3+["leaf"]*17
            assert mixed_proof["fresh_local_indices"]==[] and len(mixed_proof["source_local_group_digests"])==20
            assert mixed_vision["designGate"]["pageCoverageComplete"] is False
            third_context=deepcopy(next_context)
            third_context["binding"].update(jobId=str(UUID(int=131)),operationId=str(UUID(int=132)),presentationId=str(UUID(int=133)))
            third_context["runtimeBinding"]="b"*64
            mixed_before=deepcopy(mixed_execution)
            mixed_reuse,mixed_consumer_receipts=source_local.reusable_source_local_groups(third_context,mixed_pages,[png]*100,
                dict(proof=mixed_proof,execution=mixed_execution,producer_job=mixed_job,producer_payloads=mixed_payloads,
                    consumer_runtime_equivalent=True,consumer_binding=third_context["binding"]))
            assert len(mixed_reuse)==len(mixed_consumer_receipts)==20
            assert sum(c["producer_operation_id"]==mixed_job["workspace_operation_id"] for c in mixed_consumer_receipts)==3
            assert sum(c["producer_operation_id"]==source_local_job["workspace_operation_id"] for c in mixed_consumer_receipts)==17
            assert mixed_execution==mixed_before and source_local_execution==source_local_before
            changed_mixed_context=deepcopy(third_context)
            changed_mixed_context["pages"][0]["content"]="Changed original source fact"
            with pytest.raises(FinalSelectedEvidenceError):
                source_local.reusable_source_local_groups(changed_mixed_context,mixed_pages,[png]*100,
                    dict(proof=mixed_proof,execution=mixed_execution,producer_job=mixed_job,producer_payloads=mixed_payloads,
                        consumer_runtime_equivalent=True,consumer_binding=changed_mixed_context["binding"]))
            # Completed candidate-only sources have valid render authority but
            # no phase-0 local handoff: verify the inherited archive, then
            # require fresh local+global QA without relabeling its five indices.
            from services.svg_final_selected_continuation import build_selected_proof, reusable_local_groups
            completed_args = deepcopy(flat_args)
            completed_execution = completed_args["execution"]
            completed_execution["rounds"].append(deepcopy(completed_execution["rounds"][0]))
            completed_args["payloads"][43]["quality_recovery"] = dict(status="selected",selection={"selected":"candidate"})
            completed_args["payloads"][0]["quality_recovery_execution"] = completed_execution
            completed_before = deepcopy(completed_args)
            assert _run(source_local.prepare_source_local_leaf(None,source_operation=flat_operation,**completed_args)) is None
            assert source_local.build_source_local_proof(**completed_args) is None
            completed_proof = build_selected_proof(operation_id=completed_args["operation_id"],job_id=completed_args["job_id"],
                owner_id=completed_args["owner_id"],tenant_id=completed_args["tenant_id"],archive_digest=completed_args["archive_digest"],
                parent_manifest_hash=completed_args["parent_manifest_hash"],payloads=completed_args["payloads"],
                execution=completed_execution,source_snapshot=completed_args["source_job"]["svg_style_snapshot"])
            assert completed_proof["local_review_mode"] == "fresh-all" and completed_proof["fresh_local_indices"] == []
            completed_evidence=dict(proof=completed_proof,execution=completed_execution,producer_job=completed_args["source_job"],
                producer_payloads=completed_args["payloads"],consumer_runtime_equivalent=False,
                consumer_binding=next_context["binding"],producer_presentation_id=completed_args["source_job"]["presentation_id"])
            completed_pages=[(p,png) for p in completed_args["payloads"]]
            assert reusable_local_groups(next_context,completed_pages,[png]*100,completed_evidence) == ({},[])
            assert source_local.reusable_continuation_groups(next_context,completed_pages,[png]*100,completed_evidence) == ({},[])
            assert completed_args == completed_before
            for completed_mutation in ("proof", "owner", "svg", "runtime"):
                bad_evidence=deepcopy(completed_evidence)
                bad_context=deepcopy(next_context)
                bad_pages=deepcopy(completed_pages)
                if completed_mutation == "proof":
                    bad_evidence["proof"]["digest"]="changed"
                elif completed_mutation == "owner":
                    bad_context["binding"]["ownerId"]="foreign"
                elif completed_mutation == "svg":
                    bad_pages[43][0]["svg_content"]+="changed"
                else:
                    bad_context["runtimeBinding"]=None
                with pytest.raises(FinalSelectedEvidenceError):
                    source_local.reusable_continuation_groups(bad_context,bad_pages,[png]*100,bad_evidence)
            bad_completed_args=deepcopy(completed_args)
            bad_completed_args["execution"]["initial_continuation_review"]["source_proof_digest"]="changed"
            with pytest.raises(FinalSelectedEvidenceError):
                _run(source_local.prepare_source_local_leaf(None,source_operation=flat_operation,**bad_completed_args))
            undeclared_pages = deepcopy(changed_pages)
            undeclared_pages[20][0]["svg_content"] += " "
            undeclared_digest = sha256_hex(undeclared_pages[20][0]["svg_content"].encode())
            undeclared_pages[20][0]["source_digest"] = undeclared_digest
            undeclared_pages[20][0]["quality"]["source_digest"] = undeclared_digest
            with pytest.raises(FinalSelectedEvidenceError):
                source_local.reusable_source_local_groups(next_context, undeclared_pages, [png] * 100, changed_evidence)
            for mutation in ("owner", "consumer", "coverage", "seal", "global", "leaf", "svg", "nested", "missing"):
                bad = deepcopy(flat_args)
                material = deepcopy(flat_proof["flattened_local"])
                if mutation == "owner":
                    bad["owner_id"] = "foreign"
                elif mutation in {"consumer", "coverage", "seal", "global"}:
                    if mutation == "consumer":
                        bad["execution"]["rounds"][0]["advisory"]["vision"]["localEvidenceConsumers"][0]["consumer_binding"]["jobId"] = "foreign"
                    elif mutation == "coverage":
                        bad["execution"]["rounds"][0]["advisory"]["vision"]["localEvidenceConsumers"].pop()
                    elif mutation == "seal":
                        bad["execution"]["rounds"][0]["advisory"]["digest"] = "changed"
                    else:
                        bad["execution"]["rounds"][0]["groups"]["20"]["status"] = "review_unknown"
                elif mutation == "leaf":
                    material["execution"]["rounds"][0]["groups"]["0"]["advisory"]["digest"] = "changed"
                elif mutation == "svg":
                    bad["payloads"][62]["svg_content"] += "changed"
                elif mutation == "nested":
                    material["proof"]["flattened_local"] = {}
                else:
                    material = None
                with pytest.raises(FinalSelectedEvidenceError):
                    source_local.build_source_local_proof(**bad, flattened_leaf=material)
            bad_operation = deepcopy(flat_operation)
            bad_operation.request_payload[source_partial.ARCHIVE_KEY]["job"]["id"] = "foreign"
            before_reads = len(flat_reads)
            with pytest.raises(FinalSelectedEvidenceError):
                _run(source_local.build_source_local_manifest_proof(None, source_operation=bad_operation, **flat_args))
            assert len(flat_reads) == before_reads

        # Resume an old final-selected parent with completed local/global groups.
        # Its absent marker stays legacy even when the process defaults to v2.
        from services.generation_core.drivers.svg import quality_recovery as recovery
        from services.authored_checkpoint_errors import AuthoredStageError
        resume_context = deepcopy(legacy_frozen_context)
        resume_context.update(runtimeBinding='current-runtime', mandatoryEvidence=[True],
            binding=dict(jobId='current-job', presentationId='current-presentation', ownerId='owner', tenantId='tenant'),
            priorCandidates={}, composition=dict(pages=[{}], groups=[]))
        old_producer_gate = deepcopy(legacy_gate)
        old_producer_record = advisory_record({**legacy_vision, 'designGate': old_producer_gate}, old_producer_gate['pageDigests'])
        control = dict(review_call_limit=2, local_group_size=5, global_format_retry_limit=0, physical_offset=1)
        resume_plan = {**plan, 'final_selected_review': control}
        local_context = deck_quality._local_group_context(resume_context, [0], False)
        global_context = {**resume_context, 'reviewScope': 'global', 'representativeIndices': [],
                          'examples': [], 'exampleDigests': []}
        local_page = deepcopy(legacy_review['pages'][0])
        local_page['axes'] = {name: local_page['axes'][name] for name in prompt_mod.DESIGN_LOCAL_AXES}
        global_page = dict(index=0, evaluated=True, issues=[], designTargets=[],
            axes={'repetition_deck_flow': deepcopy(legacy_review['pages'][0]['axes']['repetition_deck_flow'])})
        records = {}
        for ordinal, (group_context, group_vision) in enumerate([
            (local_context, dict(designReview=dict(pages=[local_page], candidateComparisons=[]))),
            (global_context, dict(designReview=dict(pages=[global_page], candidateComparisons=[],
                deckFlow=dict(evaluated=True, visibleEvidence='The source sequence remains coherent.', repetitionGroups=[])))),
        ]):
            group_input = deck_quality._group_review_input_digest(group_context, [png], 0, ordinal, [0])
            body = dict(groupId=f'phase-0/group-{ordinal}', globalPageIndices=[0], inputDigest=group_input,
                        reviewScope=group_context['reviewScope'], vision=group_vision)
            records[str(ordinal)] = dict(status='completed', input_digest=group_input,
                advisory={**body, 'digest': content_digest(body)})
        proofs = [dict(round_ordinal=0, producer_gate_digest=old_producer_gate['digest'],
            producer_input_digest=old_producer_gate['inputDigest'], producer_binding=deepcopy(old_producer_gate['binding']))]
        identity = dict(selected=[dict(svg=sha256_hex(page['svg_content'].encode()), png=sha256_hex(png),
                            input_digest=page.get('input_digest'))], producer_proofs=proofs,
            runtime_binding='current-runtime', snapshot_digest=recovery._digest(fixture_ctx.snapshot), control=control)
        old_execution = dict(rounds=[dict(status='completed', advisory=old_producer_record)],
            final_selected_review=dict(namespace=NAMESPACE, status='review_inflight',
                source_proof_digest=recovery._digest(identity), groups=records))
        resume_calls, persisted = [], []
        async def authority(*args):
            return None
        async def persist_resume():
            persisted.append(True)
        async def forbid_resume_provider(*args, **kwargs):
            resume_calls.append(True)
            raise AssertionError('A saved group cannot purchase another review.')
        def frozen_resume_context(*args, review_principles_version=None, **kwargs):
            result = deepcopy(resume_context)
            if review_principles_version is not None:
                result['reviewPrinciplesVersion'] = review_principles_version
            return result
        resume_ctx = SimpleNamespace(snapshot=fixture_ctx.snapshot, quality_recovery_runtime_binding='current-runtime',
            quality_review_principles_version=prompt_mod.DESIGN_PRINCIPLES_VERSION)
        with monkeypatch.context() as resume_scope:
            resume_scope.setattr(recovery, 'ensure_recovery_authority', authority)
            resume_scope.setattr(recovery, 'read_quality_recovery_execution_plan', lambda *args, **kwargs: resume_plan)
            resume_scope.setattr(svg_authoring_profile, 'read_quality_recovery_execution_plan', lambda *args, **kwargs: resume_plan)
            resume_scope.setattr(deck_quality, '_design_context', frozen_resume_context)
            resume_scope.setattr(deck_quality, '_seal_design_gate', seal_design_gate)
            resumed = deepcopy(old_execution)
            result = _run(recovery._review_live_final_selected(resume_ctx, valid_pages, job,
                SimpleNamespace(read_png=lambda _: png), forbid_resume_provider, resumed, persist_resume))
            assert resumed['final_selected_review']['status'] == 'completed'
            assert 'reviewPrinciplesVersion' not in resumed['final_selected_review']
            assert 'reviewPrinciplesVersion' not in result['vision']['designGate']
            assert resumed['final_selected_review']['groups'] == records
            assert resume_ctx.quality_review_principles_version == prompt_mod.DESIGN_PRINCIPLES_VERSION
            assert resume_calls == [] and old_execution['final_selected_review']['status'] == 'review_inflight'
            unknown = deepcopy(old_execution)
            unknown['final_selected_review']['groups']['0']['status'] = 'review_inflight'
            before_unknown_groups = deepcopy(unknown['final_selected_review']['groups'])
            with pytest.raises(AuthoredStageError) as unknown_error:
                _run(recovery._review_live_final_selected(resume_ctx, valid_pages, job,
                    SimpleNamespace(read_png=lambda _: png), forbid_resume_provider, unknown, persist_resume))
            assert unknown_error.value.code == 'public_provider_result_unknown'
            assert unknown['final_selected_review']['groups'] == before_unknown_groups
            assert 'reviewPrinciplesVersion' not in unknown['final_selected_review']
            assert resume_calls == []
            forged_scope = deepcopy(old_execution)
            forged_scope['final_selected_review'].update(status='completed', reviewPrinciplesVersion='unrecognized-principles')
            with pytest.raises(ValueError, match='svg_quality_review_principles_version_invalid'):
                _run(recovery._review_live_final_selected(resume_ctx, valid_pages, job,
                    SimpleNamespace(read_png=lambda _: png), forbid_resume_provider, forged_scope, persist_resume))
        assert recovery._review_scope_principles({}) is None
        assert recovery._review_scope_principles({'reviewPrinciplesVersion': prompt_mod.DESIGN_PRINCIPLES_VERSION}) == prompt_mod.DESIGN_PRINCIPLES_VERSION

        # One hundred complete source units exceed the text envelope because
        # they carry local render styles; the global view keeps source facts.
        source_runs = ["source "] * 600 + ["  매출 17.5억원\n원가 9.25억원 → 이익 8.25억원  "]
        text = "".join(source_runs)

        def extracted_styles(runs):
            return [dict(sizePt=18.0, bold=False, italic=False, color="#000000", fontFamily="Pretendard",
                sourceParagraphIndex=0, sourceRunIndex=index, sourceFlatRunIndex=index,
                textStart=0, textEnd=len(run)) for index, run in enumerate(runs)]

        layout = dict(marginLeftEmu=91440, marginRightEmu=91440, marginTopEmu=45720, marginBottomEmu=45720,
            anchor="t", wrap="square", vertical="horz", rotationDegrees=None, upright=False,
            autoFit=None, fontScalePct=None, lineSpaceReductionPct=None)
        body = dict(text=text, runs=source_runs, paragraphs=[dict(text=text, runs=source_runs,
            bulletKind="autoNum", level=1, runStyles=extracted_styles(source_runs))],
            runStyles=extracted_styles(source_runs), textBody=layout)
        cell_runs = ["합계", "\n", "17.50억원"]
        cell = dict(text="".join(cell_runs), runs=cell_runs,
            paragraphs=[dict(text="".join(cell_runs), runs=cell_runs, runStyles=extracted_styles(cell_runs))],
            runStyles=extracted_styles(cell_runs), textBody=layout,
            gridSpan=2, rowSpan=1, mergeOrigin=dict(row=0, column=0))
        payloads = [
            ("text", {**body, "geometry": {"x": 100, "y": 200},
                "futureSemanticFact": {"id": "keep-id", "version": 4, "geometry": "a source fact"}}),
            ("table", dict(rows=[[cell, dict(text="비율 50%", runs=["비율 50%"])]],
                cells=[["합계\n17.50억원", "비율 50%"]], mergedCells=[dict(row=0, column=0, columnSpan=2)],
                columnWidthsEmu=[123, 456], rowHeightsEmu=[789], geometry={"x": 50})),
            ("chart", dict(categories=["2025", "2026"], series=[dict(name="실적", values=["9.25", "17.50"])],
                unit="억원", nativeSource=dict(title="연도별 매출", axes=[dict(id="axis-1", crossAxisId="axis-2",
                    title="금액 (억원)", displayUnits="millions")]), geometry={"x": 10})),
            ("image", dict(assetId="source-image", occurrence=2, caption="원본 공정사진",
                data_url="data:image/png;base64," + "a" * 2048, geometry={"x": 20})),
            ("shape", dict(startShapeId="text-0", endShapeId="chart-0", preset="line",
                customGeometry={"path": "M 0 0 L 1 1"}, geometry={"rotationDegrees": 45})),
            ("source_note", dict(text="각주: VAT 포함, 단위 억원", runs=["각주: VAT 포함, 단위 억원"])),
        ]
        source_unit = dict(source_unit_id="source-0", source_index=0, blocks=[
            dict(source_block_id=f"block-{ordinal}", kind=kind, semantic_role=kind, order=ordinal,
                 payload=payload, comparison_projection={"unicodeForm": "NFC"},
                 content_digest="d" * 64, native_candidate=True, fallback_asset_id=None)
            for ordinal, (kind, payload) in enumerate(payloads)])
        large_global = {**deepcopy(authority_context), "reviewScope": "global", "representativeIndices": [],
            "examples": [], "exampleDigests": [], "priorCandidates": {},
            "pages": [{**deepcopy(authority_context["pages"][0]), "index": index,
                "content": json.dumps({**source_unit, "source_unit_id": f"source-{index}", "source_index": index},
                                      ensure_ascii=False)} for index in range(100)],
            "roles": ["content"] * 100, "mandatoryEvidence": [True] * 100, "deckComplete": True,
            "composition": {"groups": [{"pages": list(range(100)), "representative": 0,
                "comparisonBasis": ["source_parallel_groups"]}],
                "pages": [{"comparisonBasis": ["source_parallel_groups"]} for _ in range(100)]}}
        original_global = deepcopy(large_global)
        assert len(json.dumps(large_global, ensure_ascii=False).encode()) > 8 * 1024 * 1024
        full_images = [_png()] * 100
        packed = shared_advisory._prepare_global_input(b"complete-100-page-sheet", full_images, large_global)
        global_messages = prompt_mod.build_advisory_messages(b"complete-100-page-sheet", slide_count=100,
            required=True, pngs=full_images, design_context=packed)
        global_schema = prompt_mod.baseline_review_json_schema(slide_count=100, context=packed)
        envelope = shared_advisory._check_design_input(global_messages, global_schema)
        assert envelope["serializedContextBytes"] < envelope["maxSerializedContextBytes"]
        panel_images = packed["_globalPagePanelImages"]
        panel_metadata = packed["globalPagePanels"]
        actual_images = [part.data for part in global_messages[1].content
            if type(part).__name__ == "ImageContentPart"]
        assert len(panel_images) > 1 and actual_images[:1 + len(panel_images)] == [b"complete-100-page-sheet", *panel_images]
        assert envelope["imageCount"] == len(actual_images)
        assert [index for panel in panel_metadata for index in panel["pageIndices"]] == list(range(100))
        import hashlib
        from PIL import Image
        for panel, data in zip(panel_metadata, panel_images, strict=True):
            assert panel["thumbnailWidthPx"] >= 640
            assert panel["sourcePngDigests"] == [hashlib.sha256(full_images[index]).hexdigest()
                for index in panel["pageIndices"]]
            assert panel["sheetDigest"] == hashlib.sha256(data).hexdigest()
            with Image.open(io.BytesIO(data)) as image:
                assert image.width // min(2, len(panel["pageIndices"])) >= 640
        assert "There is no second deck or A/B pair" in global_messages[0].content
        assert "the alternative is B" not in global_messages[0].content
        assert "alternativeReview covers exactly compared" not in global_messages[0].content
        # GLOBAL wire references are 1-based and form one exact partition.
        # The LOCAL branch, including its principles digest, stays untouched.
        assert "explicit-wire-partition/v1" in global_messages[0].content
        assert "context index + 1" in global_messages[0].content
        assert "explicit-wire-partition/v1" not in messages[0].content
        partition_context = dict(policy={"version": 2}, reviewScope="global", priorCandidates={},
            composition=dict(groups=[], pages=[{}, {}, {}]))
        partition_schema = prompt_mod.baseline_review_json_schema(slide_count=3, context=partition_context)
        review_schema = partition_schema["properties"]["variantReview"]
        for key in ("cleanPages", "unreviewedPages"):
            assert review_schema["properties"][key]["items"]["enum"] == [1, 2, 3]
        partition_payload = dict(variantReview=dict(cleanPages=[1, 2, 3], unreviewedPages=[], groups=[],
            cleanEvidence="The current page relationships are clear."), findings=[], designTargets=[])
        validate(partition_payload, partition_schema)
        assert [page["index"] for page in shared_advisory._parse_baseline_review(
            partition_payload, slide_count=3, context=partition_context)["pages"]] == [0, 1, 2]
        for key, references, diagnostic in (
                ("cleanPages", [1, 2, 2, 3], "duplicateReferenceCount"),
                ("cleanPages", [0, 1, 2, 3, 4], "outOfRangeReferenceCount"),
                ("cleanPages", [1, 2, True, "private-model-text"], "invalidIndexTypeCount"),
                ("cleanPages", [1, 2], "missingPageCount"),
                ("unreviewedPages", [1], "duplicateReferenceCount")):
            broken = deepcopy(partition_payload)
            broken["variantReview"][key] = references
            reasons, fields = [], {}
            assert shared_advisory._parse_baseline_review(broken, slide_count=3,
                context=partition_context, rejection_reasons=reasons, rejection_fields=fields) is None
            assert reasons == ["index_coverage"]
            assert fields["partitionCoverage"][diagnostic] > 0
            assert "private-model-text" not in json.dumps(fields)
        group_broken = deepcopy(partition_payload)
        group_broken["variantReview"].update(cleanPages=[1, 2], groups=[
            dict(pages=[2, 3], findingId=1, retainedPages=[])])
        group_broken["findings"] = [dict(id=1, intentional=False, sourceEvidence="",
            axis=dict(verdict="passed", severity="none", repairScope="none",
                visibleEvidence="Related pages remain distinct."))]
        # JSON schema handles each array; the unchanged parser enforces the
        # cross-array relation and keeps overlapping partitions rejected.
        validate(group_broken, partition_schema)
        reasons, fields = [], {}
        assert shared_advisory._parse_baseline_review(group_broken, slide_count=3,
            context=partition_context, rejection_reasons=reasons, rejection_fields=fields) is None
        assert reasons == ["index_coverage"]
        assert fields["partitionField"] == "groupPages" and fields["groupOrdinal"] == 1
        assert fields["partitionCoverage"]["duplicatePageIndices"] == [2]
        bounded = shared_advisory._global_partition_diagnostics(
            dict(cleanPages=list(range(-100, 101)), unreviewedPages=[1] * 100, groups=[]), expected={0, 1, 2})
        assert bounded["indexSamplesTruncated"] is True
        assert all(len(bounded[key]) <= 16 for key in
            ("duplicatePageIndices", "outOfRangePageIndices", "missingPageIndices"))
        assert len(json.dumps(bounded)) < 2000
        async def partition_sheet(*args, **kwargs):
            return b"existing-overview"
        async def partition_answer(*args, **kwargs):
            return "existing-model", broken
        with monkeypatch.context() as partition_probe:
            partition_probe.setattr(shared_advisory, "compose_contact_sheet", partition_sheet)
            partition_probe.setattr(shared_advisory, "_request_advisory", partition_answer)
            rejected_partition = _run(shared_advisory.run_vision_advisory([png] * 3, slide_count=3,
                required=True, design_context=partition_context))
        assert rejected_partition["skipped"] == "design_review_schema_or_coverage_invalid"
        assert rejected_partition["schemaRejectionReason"] == "index_coverage"
        assert rejected_partition["parsedPageCount"] == 3
        assert rejected_partition["parsedPageIndexCoverage"] is False
        with monkeypatch.context() as existing_panels:
            def reencoding_forbidden(*args, **kwargs):
                raise AssertionError("the client-aware pass must reuse its own verified panel bytes")
            existing_panels.setattr(shared_advisory, "compose_global_page_panels_sync", reencoding_forbidden)
            repacked = shared_advisory._prepare_global_input(b"complete-100-page-sheet", full_images, packed)
            assert repacked["_globalPagePanelImages"] is panel_images
            tampered_panels = deepcopy(packed)
            tampered_panels["globalPagePanels"][0]["sourcePngDigests"][0] = "0" * 64
            with pytest.raises(shared_advisory.DesignReviewInputCapacityError):
                shared_advisory._prepare_global_input(b"complete-100-page-sheet", full_images, tampered_panels)
        with pytest.raises(shared_advisory.DesignReviewInputCapacityError):
            shared_advisory._prepare_global_input(b"complete-100-page-sheet", full_images, packed,
                client=SimpleNamespace(capabilities={"max_encoded_image_bytes": 1}))
        # The existing provider serializer preserves every image part; its
        # unchanged detail=auto setting does not prove model-side readability.
        from llmai.chatgpt.client import ChatGPTClient
        transport = object.__new__(ChatGPTClient)
        wire = transport._message_content_to_responses_content(global_messages[1].content)
        wire_images = [part for part in wire if part["type"] == "input_image"]
        assert len(wire_images) == len(actual_images)
        assert all(part["detail"] == "auto" for part in wire_images)
        import base64
        assert [base64.b64decode(part["image_url"].split(",", 1)[1]) for part in wire_images] == actual_images
        # The GLOBAL-only protocol participates before the existing group
        # digest is computed; LOCAL and paired principles keep their old bytes.
        old_principles = (prompt_mod.BASELINE_DESIGN_PRINCIPLES
            if large_global.get("reviewPrinciplesVersion") == prompt_mod.DESIGN_PRINCIPLES_VERSION
            else prompt_mod.LEGACY_BASELINE_DESIGN_PRINCIPLES)
        from utils.hashing import sha256_prefixed
        local_probe = {**large_global, "reviewScope": "local"}
        with monkeypatch.context() as old_protocol:
            old_protocol.setattr(deck_quality, "baseline_design_principles_digest",
                lambda _: sha256_prefixed(old_principles.encode("utf-8")))
            old_global_digest = deck_quality._group_review_input_digest(large_global, full_images, "initial", 20, list(range(100)))
            old_local_digest = deck_quality._group_review_input_digest(local_probe, full_images, "initial", 0, list(range(100)))
        assert deck_quality._group_review_input_digest(large_global, full_images, "initial", 20, list(range(100))) != old_global_digest
        assert deck_quality._group_review_input_digest(local_probe, full_images, "initial", 0, list(range(100))) == old_local_digest
        assert prompt_mod.baseline_design_principles(local_probe) == old_principles
        assert prompt_mod.baseline_design_principles({**large_global,
            "priorCandidates": {0: {"png": full_images[0]}}}) == old_principles
        transmitted = json.loads(global_messages[1].content[0].text)
        assert len(transmitted["pages"]) == 100 and transmitted["sourceDirectives"] == directives
        assert transmitted["composition"] == large_global["composition"]
        # Existing public headers omit these opaque metadata fields; every
        # remaining field must survive. The expected value uses no product call.
        def expected_public_header(value):
            if isinstance(value, dict):
                return {key: expected_public_header(item) for key, item in value.items()
                        if key not in {"version", "id", "revision", "profile_version"}}
            if isinstance(value, list):
                return [expected_public_header(item) for item in value]
            return value

        assert transmitted["style"]["common_header"] == expected_public_header(original_global["style"]["common_header"])
        for index, page in enumerate(transmitted["pages"]):
            unit = page["content"]
            assert set(unit) == set(source_unit) and unit["source_index"] == index
            assert unit["source_unit_id"] == f"source-{index}"
            assert page["common_header"] == expected_public_header(original_global["pages"][index]["common_header"])
            assert [block["order"] for block in unit["blocks"]] == list(range(6))
            assert unit["blocks"][0]["payload"]["text"] == text
            assert unit["blocks"][0]["payload"]["runs"] == source_runs
            assert "runStyles" not in unit["blocks"][0]["payload"] and "textBody" not in unit["blocks"][0]["payload"]
            assert unit["blocks"][0]["payload"]["paragraphs"][0]["bulletKind"] == "autoNum"
            assert unit["blocks"][0]["payload"]["futureSemanticFact"] == payloads[0][1]["futureSemanticFact"]
            table = unit["blocks"][1]["payload"]
            assert table["cells"] == payloads[1][1]["cells"] and table["mergedCells"] == payloads[1][1]["mergedCells"]
            assert table["rows"][0][0]["text"] == cell["text"]
            assert table["rows"][0][0]["runs"] == cell["runs"]
            assert table["rows"][0][0]["gridSpan"] == 2 and table["rows"][0][0]["mergeOrigin"] == cell["mergeOrigin"]
            chart = unit["blocks"][2]["payload"]
            assert chart["categories"] == payloads[2][1]["categories"] and chart["series"] == payloads[2][1]["series"]
            assert chart["unit"] == "억원" and chart["nativeSource"] == payloads[2][1]["nativeSource"]
            assert unit["blocks"][3]["payload"]["assetId"] == "source-image"
            assert unit["blocks"][3]["payload"]["caption"] == "원본 공정사진"
            assert unit["blocks"][4]["payload"]["startShapeId"] == "text-0"
            assert unit["blocks"][4]["payload"]["endShapeId"] == "chart-0"
            assert unit["blocks"][5]["payload"]["text"] == payloads[5][1]["text"]
            assert all({key: value for key, value in block.items() if key != "payload"}
                == {key: value for key, value in source_unit["blocks"][ordinal].items() if key != "payload"}
                for ordinal, block in enumerate(unit["blocks"]))
        # Same-name semantic extensions are not extractor render metadata.
        extended = deepcopy(source_unit)
        extended["blocks"][0]["payload"].update(geometry="source semantic fact",
            textBody={"futureSemanticFact": "keep every word"},
            runStyles=[{"futureSemanticFact": "keep every value"}])
        extended["blocks"][1]["payload"].update(rowHeightsEmu={"unit": "억원", "values": ["17.50"]},
            columnWidthsEmu="source relationship")
        extended["blocks"][1]["payload"]["rows"][0][0]["runStyles"] = [{"semanticCellRelation": "keep"}]
        extended["blocks"][4]["payload"]["customGeometry"] = {"futureSemanticFact": "source relation"}
        projected = prompt_mod._global_source_context({"pages": [{"content": json.dumps(extended)}]})["pages"][0]["content"]
        for key in ("geometry", "textBody", "runStyles"):
            assert projected["blocks"][0]["payload"][key] == extended["blocks"][0]["payload"][key]
        for key in ("rowHeightsEmu", "columnWidthsEmu"):
            assert projected["blocks"][1]["payload"][key] == extended["blocks"][1]["payload"][key]
        assert projected["blocks"][1]["payload"]["rows"][0][0]["runStyles"] == [{"semanticCellRelation": "keep"}]
        assert projected["blocks"][4]["payload"]["customGeometry"] == extended["blocks"][4]["payload"]["customGeometry"]
        local_full = deck_quality._local_group_context(large_global, list(range(5)), False)
        local_messages = prompt_mod.build_advisory_messages(b"", slide_count=5, required=True,
            pngs=full_images[:5], design_context=local_full)
        local_sent = json.loads(local_messages[1].content[0].text)
        assert [page["content"] for page in local_sent["pages"]] == [page["content"] for page in large_global["pages"][:5]]
        assert [part.data for part in local_messages[1].content if type(part).__name__ == "ImageContentPart"] == full_images[:5]
        for unknown in ({**source_unit, "future_schema": {"keep": "every fact"}},
                        {**source_unit, "blocks": [{**source_unit["blocks"][0], "kind": "future-kind"}]}):
            original_content = json.dumps(unknown, ensure_ascii=False)
            assert prompt_mod._global_source_context({"pages": [{"content": original_content}]})["pages"][0]["content"] == original_content
        assert large_global == original_global
        # This is the wire projection only. Activation in the driver requires
        # the actual source manifest/validated units and existing local gates.
        scoped_global = deepcopy(large_global)
        scoped_global["globalScopeRevision"] = "source-bound-deck-flow/v1"
        scoped_global["localReviewSummary"] = dict(coverageComplete=True, pageCount=100,
            evaluatedPageCount=100, failedAxisCount=100, designTargetCount=100)
        geometry = dict(x=10, y=20, cx=300, cy=400, source="slide",
            sources={key: "slide" for key in ("x", "y", "cx", "cy")}, sourceBlockId="source-block")
        known_binding = dict(source_id="source-block", kind="text", element_id="actual-svg-text",
            text="원문 17.50억원", start=0, end=10)
        unknown_binding = {**known_binding, "futureSemanticRelation": {"dependsOn": "source-chart"}}
        for index, page in enumerate(scoped_global["pages"]):
            unit = json.loads(page["content"])
            for block in unit["blocks"]:
                block["payload"]["geometry"] = deepcopy(geometry)
            page["content"] = json.dumps(unit, ensure_ascii=False)
            page["renderedText"] = ["  실제 SVG 원문 17.50억원\n", "", "1단계 → 2단계"]
            page["localReview"] = dict(index=index, evaluated=True,
                axes={name: dict(verdict="failed" if name == names[0] else "passed") for name in names},
                designTargets=[dict(axis=names[0], improvementGoal="원문 결론을 더 명확히 표현")])
            page["localSourcePreservation"] = dict(verified=True, sourceDigest="source", manifestDigest="manifest")
            page["preservation"] = dict(contract_version="svg-source-preservation/v1", rendering_verified=True,
                bindings=[deepcopy(known_binding), deepcopy(unknown_binding)],
                futureSemanticRelation={"imageGroup": ["source-image", "source-chart"]})
        scoped_before = deepcopy(scoped_global)
        scoped_messages = prompt_mod.build_advisory_messages(b"complete-100-page-sheet", slide_count=100,
            required=True, pngs=full_images, design_context=scoped_global)
        scoped_sent = json.loads(scoped_messages[1].content[0].text)
        assert "source-bound GLOBAL scope" in scoped_messages[0].content
        assert scoped_sent["localReviewSummary"]["failedAxisCount"] == 100
        assert scoped_sent["sourceDirectives"] == directives
        assert scoped_sent["composition"] == scoped_before["composition"]
        for index, page in enumerate(scoped_sent["pages"]):
            assert page["renderedText"] == scoped_before["pages"][index]["renderedText"]
            assert page["localReview"] == scoped_before["pages"][index]["localReview"]
            assert page["common_header"] == expected_public_header(scoped_before["pages"][index]["common_header"])
            assert page["preservation"]["bindings"] == [unknown_binding]
            assert page["preservation"]["localBindingVerification"]["counts"] == {"text": 1}
            assert page["preservation"]["futureSemanticRelation"] == scoped_before["pages"][index]["preservation"]["futureSemanticRelation"]
            blocks = page["content"]["blocks"]
            assert all("geometry" not in block["payload"] for block in blocks)
            assert blocks[0]["payload"]["text"] == text
            assert blocks[1]["payload"]["rows"][0][0]["text"] == cell["text"]
            assert blocks[1]["payload"]["rows"][0][0]["mergeOrigin"] == cell["mergeOrigin"]
            assert blocks[1]["payload"]["mergedCells"] == payloads[1][1]["mergedCells"]
            assert "columnWidthsEmu" not in blocks[1]["payload"] and "rowHeightsEmu" not in blocks[1]["payload"]
            assert blocks[2]["payload"]["series"] == payloads[2][1]["series"]
            assert blocks[2]["payload"]["unit"] == "억원"
            assert blocks[2]["payload"]["nativeSource"] == payloads[2][1]["nativeSource"]
            assert blocks[3]["payload"]["caption"] == "원본 공정사진"
            assert blocks[4]["payload"]["startShapeId"] == "text-0" and blocks[4]["payload"]["endShapeId"] == "chart-0"
            assert blocks[0]["payload"]["futureSemanticFact"] == payloads[0][1]["futureSemanticFact"]
        assert scoped_global == scoped_before and large_global == original_global
        assert [part.data for part in scoped_messages[1].content
            if type(part).__name__ == "ImageContentPart"] == [b"complete-100-page-sheet"]
        # The new global scope sends the actual SVG wording once, retaining
        # source relations while all full native/local inputs stay unchanged.
        focused_global = deepcopy(scoped_global)
        focused_global["globalScopeRevision"] = "rendered-source-deck-flow/v2"
        for page in focused_global["pages"]:
            unit = json.loads(page["content"])
            unit["blocks"][0]["payload"].pop("futureSemanticFact")
            page["content"] = json.dumps(unit, ensure_ascii=False)
            page["renderedText"] = [text, cell["text"], "차트 17.50억원 → 9.25억원 → 8.25억원"]
            page["alternativeRenderedText"] = ["이전 실제 SVG 17.50억원 / 9.25억원"]
        focused_before = deepcopy(focused_global)
        focused_messages = prompt_mod.build_advisory_messages(b"complete-100-page-sheet", slide_count=100,
            required=True, pngs=full_images, design_context=focused_global)
        focused_sent = json.loads(focused_messages[1].content[0].text)
        assert "renderedText is the complete current SVG text" in focused_messages[0].content
        assert focused_sent["composition"] == scoped_sent["composition"]
        assert focused_sent["sourceDirectives"] == directives
        assert focused_sent["localReviewSummary"] == scoped_sent["localReviewSummary"]
        import re
        for index, page in enumerate(focused_sent["pages"]):
            expected = focused_before["pages"][index]
            assert page["renderedText"] == expected["renderedText"]
            assert page["alternativeRenderedText"] == expected["alternativeRenderedText"]
            assert [re.findall(r"\d+(?:[.,]\d+)*", value) for value in page["renderedText"]] == [
                re.findall(r"\d+(?:[.,]\d+)*", value) for value in expected["renderedText"]]
            assert text in page["renderedText"] and cell["text"] in page["renderedText"]
            assert page["common_header"] == expected_public_header(expected["common_header"])
            assert page["localReview"] == expected["localReview"]
            blocks = page["content"]["blocks"]
            assert all(key not in blocks[0]["payload"] for key in ("text", "runs", "runStyles", "textBody"))
            assert blocks[0]["payload"]["paragraphs"] == [dict(bulletKind="autoNum", level=1)]
            assert "text" not in blocks[1]["payload"]["rows"][0][0]
            assert blocks[1]["payload"]["cells"] == payloads[1][1]["cells"]
            # The second source cell is intentionally absent from renderedText;
            # its exact value and position must remain in the source matrix.
            assert payloads[1][1]["cells"][0][1] not in page["renderedText"]
            assert blocks[1]["payload"]["cells"][0][1] == payloads[1][1]["cells"][0][1]
            assert blocks[1]["payload"]["rows"][0][0]["mergeOrigin"] == cell["mergeOrigin"]
            assert blocks[1]["payload"]["mergedCells"] == payloads[1][1]["mergedCells"]
            source_table = payloads[1][1]
            assert len(blocks[1]["payload"]["rows"]) == len(source_table["rows"])
            for row_index, source_row in enumerate(source_table["rows"]):
                assert len(blocks[1]["payload"]["rows"][row_index]) == len(source_row)
                for column_index, source_cell in enumerate(source_row):
                    projected_cell = blocks[1]["payload"]["rows"][row_index][column_index]
                    assert blocks[1]["payload"]["cells"][row_index][column_index] == source_cell["text"]
                    for key in ("row", "column", "sourceCellId", "gridSpan", "rowSpan", "hMerge", "vMerge", "mergeOrigin", "mergeCovered"):
                        assert (key in projected_cell) == (key in source_cell)
                        if key in source_cell:
                            assert projected_cell[key] == source_cell[key]
            assert blocks[2]["payload"]["series"] == payloads[2][1]["series"]
            assert blocks[2]["payload"]["nativeSource"] == payloads[2][1]["nativeSource"]
            assert blocks[4]["payload"]["startShapeId"] == "text-0"
            assert blocks[4]["payload"]["endShapeId"] == "chart-0"
            original_blocks = json.loads(expected["content"])["blocks"]
            assert [(block["source_block_id"], block["kind"], block["semantic_role"], block["order"],
                     block["comparison_projection"]) for block in blocks] == [
                (block["source_block_id"], block["kind"], block["semantic_role"], block["order"],
                 block["comparison_projection"]) for block in original_blocks]
            assert all("content_digest" not in block and "native_candidate" not in block for block in blocks)
            assert page["preservation"]["bindings"] == [unknown_binding]
        assert len(json.dumps(focused_sent, ensure_ascii=False).encode()) < len(json.dumps(scoped_sent, ensure_ascii=False).encode())
        assert [part.data for part in focused_messages[1].content
            if type(part).__name__ == "ImageContentPart"] == [b"complete-100-page-sheet"]
        focused_unknown = deepcopy(focused_global)
        focused_unknown["pages"][0]["content"] = json.dumps(extended, ensure_ascii=False)
        unknown_result = prompt_mod._global_source_context(focused_unknown)["pages"][0]["content"]
        for key in ("geometry", "textBody", "runStyles"):
            assert unknown_result["blocks"][0]["payload"][key] == extended["blocks"][0]["payload"][key]
        assert unknown_result["blocks"][1]["payload"]["rowHeightsEmu"] == extended["blocks"][1]["payload"]["rowHeightsEmu"]
        assert unknown_result["blocks"][4]["payload"]["customGeometry"] == extended["blocks"][4]["payload"]["customGeometry"]
        for matrix_state in ("missing", "mismatched"):
            unbound_matrix = deepcopy(focused_global)
            unit = json.loads(unbound_matrix["pages"][0]["content"])
            if matrix_state == "missing":
                unit["blocks"][1]["payload"].pop("cells")
            else:
                unit["blocks"][1]["payload"]["cells"][0][0] = "different original source cell"
            unbound_matrix["pages"][0]["content"] = json.dumps(unit, ensure_ascii=False)
            retained = prompt_mod._global_source_context(unbound_matrix)["pages"][0]["content"]["blocks"][1]["payload"]
            assert retained["rows"][0][0]["text"] == cell["text"]
            if matrix_state == "mismatched":
                assert retained["cells"] == unit["blocks"][1]["payload"]["cells"]
        assert focused_global == focused_before and scoped_global == scoped_before and large_global == original_global
        local_digest = deck_quality._group_review_input_digest(local_full, full_images[:5], "initial", 0, list(range(5)))
        lean_global = deepcopy(focused_global)
        lean_global["globalWireProjectionRevision"] = "source-relations/v3"
        fallback_diagnostic = dict(schema="svg-template-glyph-fallback/v1", canonical_svg_digest="a" * 64,
            derived_svg_digest="b" * 64, fallback_family="fallback", fallback_font_digest="c" * 64,
            fallback_weight=400, offset_unit="unicode", primary_family="primary", primary_font_digest="d" * 64,
            proof_digest="e" * 64, runs=[])
        lf_diagnostic = dict(schema="svg-source-linefeed-restoration/v1", bindings=[], canonical_svg_digest="a" * 64,
            input_svg_digest="b" * 64, manifest_digest="c" * 64, raw_output_digest="d" * 64,
            restored_lf_count=0, restored_svg_digest="e" * 64, source_page_id="source-0")
        for index, page in enumerate(lean_global["pages"]):
            page["visualIntent"] = dict(contract_version="svg-visual-intent/v1", page_index=index, page_role="content",
                source_digest="f" * 64, emphasis=dict(focal_text="actual title", supporting_facts=["17.50억원"]),
                reading_flow=dict(intent="ordered_sequence"), density=dict(level="balanced"),
                source_evidence=dict(basis="source_lines", source_kinds=[], semantics="keep all actual values",
                    blocks=[dict(source_path="source_content.line[0]", text=text),
                            dict(futureSemanticRelation={"dependsOn": "source-chart"})], message_candidates=[],
                    comparisonBasis=["source_parallel_groups"]))
            page["preservation"].update(render_font_fallback=deepcopy(fallback_diagnostic),
                source_lf_restoration=deepcopy(lf_diagnostic))
        lean_global["pages"][0]["visualIntent"]["neighboring_intents"] = [dict(page_index=1,
            focal_text="actual title", reading_flow="ordered_sequence", density="balanced", page_role="content", source_digest="f" * 64)]
        lean_before = deepcopy(lean_global)
        previous_wire_policy = {key: value for key, value in lean_global.items() if key != "globalWireProjectionRevision"}
        assert deck_quality._group_review_input_digest(previous_wire_policy, full_images, "initial", 20, list(range(100))) != \
            deck_quality._group_review_input_digest(lean_global, full_images, "initial", 20, list(range(100)))
        lean_messages = prompt_mod.build_advisory_messages(b"complete-100-page-sheet", slide_count=100,
            required=True, pngs=full_images, design_context=lean_global)
        lean_sent = json.loads(lean_messages[1].content[0].text)
        for index, page in enumerate(lean_sent["pages"]):
            assert page["renderedText"] == lean_before["pages"][index]["renderedText"]
            assert page["alternativeRenderedText"] == lean_before["pages"][index]["alternativeRenderedText"]
            assert page["localReview"] == lean_before["pages"][index]["localReview"]
            assert page["content"]["blocks"][1]["payload"]["cells"] == payloads[1][1]["cells"]
            assert page["content"]["blocks"][1]["payload"]["mergedCells"] == payloads[1][1]["mergedCells"]
            assert page["content"]["blocks"][1]["payload"]["rows"][0][0]["mergeOrigin"] == cell["mergeOrigin"]
            assert "paragraphs" not in page["content"]["blocks"][1]["payload"]["rows"][0][0]
            assert "render_font_fallback" not in page["preservation"] and "source_lf_restoration" not in page["preservation"]
            assert page["visualIntent"]["source_evidence"]["blocks"] == [dict(futureSemanticRelation={"dependsOn": "source-chart"})]
            assert page["visualIntent"]["source_evidence"]["comparisonBasis"] == ["source_parallel_groups"]
            assert page["visualIntent"]["source_evidence"]["localSourceBodyBlockCount"] == 1
            assert page["content"]["blocks"][0]["payload"]["paragraphs"] == [dict(text=text, bulletKind="autoNum", level=1)]
        assert lean_sent["pages"][0]["visualIntent"]["neighboring_intents"] == [dict(page_index=1)]
        unknown_diagnostic = deepcopy(lean_global)
        unknown_diagnostic["pages"][0]["preservation"]["render_font_fallback"]["futureSemanticFact"] = "keep"
        retained_diagnostic = prompt_mod._global_source_context(unknown_diagnostic)["pages"][0]["preservation"]
        assert retained_diagnostic["render_font_fallback"] == unknown_diagnostic["pages"][0]["preservation"]["render_font_fallback"]
        mismatch_paragraph = deepcopy(lean_global)
        unit = json.loads(mismatch_paragraph["pages"][0]["content"])
        unit["blocks"][1]["payload"]["rows"][0][0]["paragraphs"][0]["text"] = "다른 원문 문단 50%"
        mismatch_paragraph["pages"][0]["content"] = json.dumps(unit, ensure_ascii=False)
        retained = prompt_mod._global_source_context(mismatch_paragraph)["pages"][0]["content"]["blocks"][1]["payload"]
        assert retained["cells"] == payloads[1][1]["cells"]
        assert retained["rows"][0][0]["paragraphs"][0]["text"] == "다른 원문 문단 50%"
        unknown_body = deepcopy(lean_global)
        unit = json.loads(unknown_body["pages"][0]["content"])
        homonyms = dict(text="visible 17.50", runs=["different 50%"], alignment="l", dominantSizePt=3,
            futureSemanticRelation={"keepEveryValue": True})
        unit["blocks"][0]["payload"] = deepcopy(homonyms)
        unknown_body["pages"][0]["content"] = json.dumps(unit, ensure_ascii=False)
        assert prompt_mod._global_source_context(unknown_body)["pages"][0]["content"]["blocks"][0]["payload"] == homonyms
        assert [part.data for part in lean_messages[1].content if type(part).__name__ == "ImageContentPart"] == [b"complete-100-page-sheet"]
        assert lean_global == lean_before and focused_global == focused_before
        assert deck_quality._group_review_input_digest(local_full, full_images[:5], "initial", 0, list(range(5))) == local_digest
        for weak_ctx in (SimpleNamespace(), SimpleNamespace(source_reflow=True,
                source_document=SimpleNamespace(source_digest="source", manifest_digest="manifest"), source_units=[])):
            unmarked = deck_quality._global_review_context(weak_ctx, scoped_global, [(page, b"png") for page in scoped_global["pages"]], {}, [])
            assert "globalScopeRevision" not in unmarked
            unmarked_focused = deck_quality._global_review_context(weak_ctx, focused_global,
                [(page, b"png") for page in focused_global["pages"]], {}, [])
            assert "globalScopeRevision" not in unmarked_focused
            fallback = prompt_mod._global_source_context(unmarked_focused)["pages"][0]["content"]
            assert fallback["blocks"][0]["payload"]["text"] == text
            assert fallback["blocks"][0]["content_digest"] == "d" * 64
            unmarked_lean = deck_quality._global_review_context(weak_ctx, lean_global,
                [(page, b"png") for page in lean_global["pages"]], {}, [])
            assert "globalWireProjectionRevision" not in unmarked_lean
        with pytest.raises(shared_advisory.DesignReviewInputCapacityError) as capacity:
            shared_advisory._check_design_input(global_messages, global_schema,
                client=SimpleNamespace(capabilities={"max_context_bytes": 1}))
        safe_envelope = capacity.value.input_envelope
        assert len(safe_envelope) == 9 and all(type(value) is int for value in safe_envelope.values())
        assert safe_envelope["maxSerializedContextBytes"] == 1

        async def unavailable_input(*args, **kwargs):
            raise shared_advisory.DesignReviewInputCapacityError(
                "review_input_capacity_unavailable", input_envelope=safe_envelope)

        async def existing_sheet(*args, **kwargs):
            return b"complete-100-page-sheet"

        with monkeypatch.context() as capacity_scope:
            capacity_scope.setattr(shared_advisory, "_request_advisory", unavailable_input)
            capacity_scope.setattr(shared_advisory, "compose_contact_sheet", existing_sheet)
            skipped = _run(shared_advisory.run_vision_advisory(full_images, slide_count=100,
                required=True, design_context=packed))
        assert skipped == {"skipped": "review_input_capacity_unavailable",
            "reasonCode": "review_input_capacity_unavailable", "inputEnvelope": safe_envelope}

        # Preserve the original complete comparison JSON byte stream, not just
        # a new equivalent projection. Only its fixed encoding is shared locally.
        import hashlib
        real_sha256, real_dumps = hashlib.sha256, json.dumps
        for page_count, include_directives in ((1, False), (100, True)):
            hash_pages = [(dict(svg_content=f"<svg><text>원문 {index}</text></svg>",
                png_sha256=sha256_hex(png), input_digest=f"input-{index}"), png) for index in range(page_count)]
            hash_context = dict(policy={"version": 2}, binding=dict(jobId="job", operationId="operation",
                presentationId="presentation", ownerId="owner", tenantId="tenant"),
                style=dict(canvas=dict(width=1280, height=720)), styleDigest="style", snapshotDigest="snapshot",
                runtimeBinding="runtime", exampleDigests=["원본-example"], roles=["content"] * page_count,
                deckComplete=True, mandatoryEvidence=[True] * page_count, priorCandidates={}, examples=[],
                reviewPrinciplesVersion=prompt_mod.DESIGN_PRINCIPLES_VERSION,
                pages=[dict(index=index, content='한글 😀 "neighbours":[] \\ /\n',
                    nested={"z": [0, -0.0, 1, 1.0, float("nan"), float("inf"), -float("inf")], "a": (True, None)})
                    for index in range(page_count)])
            if include_directives:
                hash_context["sourceDirectives"] = '  원문 "neighbours":[] / 지시\n그대로 유지  '
            hash_vision = dict(promptVersion=prompt_mod.DESIGN_REVIEW_PROMPT_VERSION,
                reviewedPageIndices=list(range(page_count)), reviewedPngDigests=[sha256_hex(png)] * page_count,
                designReview=dict(deckFlow=dict(evaluated=True), pages=[dict(index=index, evaluated=True,
                    axes={name: dict(verdict="failed" if index == 0 and name == names[0] else "passed",
                        severity="major" if index == 0 and name == names[0] else "none",
                        visibleEvidence="원문 근거", repairScope="local_repair" if index == 0 and name == names[0] else "none")
                        for name in prompt_mod.DESIGN_AXES}, issues=[],
                    designTargets=[dict(axis=names[0], improvementGoal="원문을 유지한 개선")] if index == 0 else [])
                    for index in range(page_count)]))
            page_hashes = [sha256_hex(payload["svg_content"].encode()) for payload, _ in hash_pages]
            legacy_values = [dict(sources=hash_context["pages"], style=hash_context["styleDigest"],
                examples=hash_context["exampleDigests"], policy=hash_context["policy"], roles=hash_context["roles"],
                binding=hash_context["binding"],
                **({"sourceDirectives": hash_context["sourceDirectives"]} if include_directives else {}),
                neighbours=[value for other, value in enumerate(page_hashes) if other != index])
                for index in range(page_count)]
            expected_streams = [real_dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
                                for value in legacy_values]
            before_context = real_dumps(hash_context, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
            streams, fixed_source_encodings = [], []

            class ComparisonHash:
                def __init__(self, value):
                    self.hash, self.parts = real_sha256(value), (value,)
                def copy(self):
                    copied = object.__new__(ComparisonHash)
                    copied.hash, copied.parts = self.hash.copy(), self.parts
                    return copied
                def update(self, value):
                    self.hash.update(value)
                    self.parts += (value,)
                def hexdigest(self):
                    streams.append(b"".join(self.parts))
                    return self.hash.hexdigest()

            def capture_hash(value=b"", *args, **kwargs):
                if isinstance(value, bytes) and value.endswith(b',"neighbours":'):
                    return ComparisonHash(value)
                return real_sha256(value, *args, **kwargs)

            def count_fixed_sources(value, *args, **kwargs):
                suffix_keys = {"policy", "roles", "sources", "style"} | ({"sourceDirectives"} if include_directives else set())
                if isinstance(value, dict) and set(value) == suffix_keys and value.get("sources") is hash_context["pages"]:
                    fixed_source_encodings.append(1)
                return real_dumps(value, *args, **kwargs)

            with monkeypatch.context() as hash_scope:
                hash_scope.setattr(hashlib, "sha256", capture_hash)
                hash_scope.setattr(json, "dumps", count_fixed_sources)
                gate = seal_design_gate(hash_context, hash_pages, [png] * page_count, hash_vision)
            assert fixed_source_encodings == [1]
            assert len(streams) == page_count and all(actual == expected for actual, expected in zip(streams, expected_streams, strict=True))
            expected_digests = [content_digest(value) for value in legacy_values]
            assert [page["comparisonContextDigest"] for page in gate["pages"]] == expected_digests
            legacy_gate = {**gate, "pages": [{**page, "comparisonContextDigest": expected_digests[index]}
                                           for index, page in enumerate(gate["pages"])]}
            legacy_gate["digest"] = content_digest({key: value for key, value in legacy_gate.items() if key != "digest"})
            assert gate == legacy_gate and gate["blockingPages"] == [0] and gate["unreviewedPages"] == []
            assert gate["pages"][0]["designTargets"] == hash_vision["designReview"]["pages"][0]["designTargets"]
            assert real_dumps(hash_context, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode() == before_context
        changed_context = deepcopy(hash_context)
        changed_context["pages"][0]["content"] += " changed next invocation"
        changed_gate = seal_design_gate(changed_context, hash_pages, [png] * page_count, hash_vision)
        for index, page in enumerate(changed_gate["pages"]):
            assert page["comparisonContextDigest"] == content_digest({**legacy_values[index], "sources": changed_context["pages"]})
            assert page["comparisonContextDigest"] != gate["pages"][index]["comparisonContextDigest"]
        # The same content_digest defaults remain in force, including strict
        # UTF-8 and rejection of unsupported/circular/mixed-key JSON inputs.
        cyclic = {}; cyclic["self"] = cyclic
        for invalid in (object(), cyclic, "\ud800", {1: "number", "1": "string"}):
            invalid_context = deepcopy(hash_context)
            invalid_context["pages"][0]["invalid"] = invalid
            with pytest.raises((TypeError, ValueError, UnicodeEncodeError)) as legacy_error:
                content_digest({key: value for key, value in invalid_context.items() if key not in {"examples", "priorCandidates"}})
            with pytest.raises(type(legacy_error.value)) as optimized_error:
                seal_design_gate(invalid_context, hash_pages, [png] * page_count, hash_vision)
            assert optimized_error.value.args == legacy_error.value.args

        # Explicit optional advice stays visible without purchasing a rewrite.
        advice_snapshot = deepcopy(fixture_ctx.snapshot)
        advice_context = deepcopy(hash_context)
        advice_context["policy"] = deck_quality._quality_policy(advice_snapshot)
        advice_context["snapshotDigest"] = content_digest(advice_snapshot)
        advice_vision = deepcopy(hash_vision)
        for item in advice_vision["designReview"]["pages"]:
            item["axes"] = {name: dict(verdict="passed", severity="none", repairScope="none",
                                       visibleEvidence="The original evidence is readable.") for name in prompt_mod.DESIGN_AXES}
            item["designTargets"] = []
        optional_axis = prompt_mod.DESIGN_AXES[-1]
        advice_target = dict(axis=optional_axis, improvementGoal="Optional hierarchy refinement; preserve original content.")
        advice_vision["designReview"]["pages"][0]["axes"][optional_axis].update(verdict="advisory", severity="minor")
        advice_vision["designReview"]["pages"][0]["designTargets"] = [advice_target]
        advice_gate = seal_design_gate(advice_context, hash_pages, [png] * page_count, advice_vision)
        assert advice_gate["status"] == "passed" and advice_gate["blockingPages"] == []
        assert advice_gate["designImprovementStatus"] == "satisfied" and advice_gate["advisoryPages"] == [0]
        assert advice_gate["pages"][0]["designImprovementStatus"] == "advisory"
        assert advice_gate["pages"][0]["designTargets"] == [advice_target]
        advice_record = advisory_record({**advice_vision, "designGate": advice_gate}, advice_gate["pageDigests"])
        advice_ctx = SimpleNamespace(snapshot=advice_snapshot, quality_recovery_runtime_binding=advice_context["runtimeBinding"])
        with monkeypatch.context() as optional_scope:
            optional_scope.setattr(owner_context, "get_current_owner_id", lambda: "owner")
            optional_scope.setattr(owner_context, "get_current_tenant_id", lambda: "tenant")
            assert deck_quality.quality_recovery_decisions(advice_ctx, hash_pages, advice_record) == {}
            mixed_vision = deepcopy(advice_vision)
            blocking_target = dict(axis=prompt_mod.DESIGN_AXES[0], improvementGoal="A separate actionable target.")
            mixed_targets = [advice_target, blocking_target]
            mixed_vision["designReview"]["pages"][0]["designTargets"] = deepcopy(mixed_targets)
            mixed_gate = seal_design_gate(advice_context, hash_pages, [png] * page_count, mixed_vision)
            mixed_record = advisory_record({**mixed_vision, "designGate": mixed_gate}, mixed_gate["pageDigests"])
            mixed_decision = deck_quality.quality_recovery_decisions(advice_ctx, hash_pages, mixed_record)[0]
            assert deck_quality._blocking_design_targets(mixed_gate["pages"][0]) == mixed_targets
            assert mixed_gate["designImprovementStatus"] == "needed"
            assert mixed_decision["design_targets"] == mixed_targets
            assert mixed_decision["target_axes"] == [optional_axis, prompt_mod.DESIGN_AXES[0]]
            # Exercise the actual raw-target authorization comparison; asset
            # restoration is a separate, unchanged boundary in this fixture.
            from services.generation_core.drivers.svg import partial_quality_recovery as repair_authority
            from services.svg_cached_author_render_recovery import author_witness
            mixed_job = SimpleNamespace(id="job", workspace_operation_id="operation", presentation_id="presentation")
            repair_payload = deepcopy(hash_pages[0][0])
            repair_payload["attempts"] = [dict(kind="provider", raw_response=repair_payload["svg_content"], receipt={})]
            witness = dict(svg_sha256=mixed_gate["pageDigests"][0], input_digest=repair_payload["input_digest"],
                           author_receipt_digest=author_witness(repair_payload))
            repair_ctx = SimpleNamespace(snapshot=advice_snapshot, count=page_count,
                quality_recovery_runtime_binding=advice_context["runtimeBinding"],
                partial_recovery_manifest=dict(known_indices=[0], pages=[witness]))
            repair_payload.update(partial_recovery={"operation_id": "operation"}, correction_mode="design_rewrite",
                quality_recovery=dict(status="repair_pending", decision=deepcopy(mixed_decision),
                                      prior_candidate={"svg_digest": witness["svg_sha256"]}))
            async def mixed_checkpoint(job, index):
                assert job is mixed_job and index == 0
                return {"quality_recovery_execution": {"binding": advice_context["runtimeBinding"],
                    "rounds": [{"status": "completed", "advisory": mixed_record}]}}
            def mixed_cached_page(prior, manifest, index, input_digest):
                assert manifest is repair_ctx.partial_recovery_manifest and index == 0
                assert prior["svg_content"] == repair_payload["svg_content"] and input_digest == witness["input_digest"]
            with monkeypatch.context() as mixed_authority_scope:
                mixed_authority_scope.setattr(source_recovery, "_restore_candidate", lambda *args: deepcopy(repair_payload))
                mixed_authority_scope.setattr(source_partial, "verify_cached_page", mixed_cached_page)
                assert _run(repair_authority.current_quality_repair_authorized(
                    repair_ctx, repair_payload, 0, mixed_job, mixed_checkpoint)) is True
                partial_decision = deepcopy(repair_payload)
                partial_decision["quality_recovery"]["decision"]["design_targets"] = [blocking_target]
                assert _run(repair_authority.current_quality_repair_authorized(
                    repair_ctx, partial_decision, 0, mixed_job, mixed_checkpoint)) is False
            for mutation in ("actionable", "major", "failed", "missing", "invalid", "mandatory", "unreviewed"):
                changed_vision, changed_context = deepcopy(advice_vision), deepcopy(advice_context)
                selected = changed_vision["designReview"]["pages"][0]
                axis = selected["axes"][optional_axis]
                if mutation == "actionable":
                    axis["repairScope"] = "design_rewrite"
                elif mutation == "major":
                    axis["severity"] = "major"
                elif mutation == "failed":
                    selected["axes"][prompt_mod.DESIGN_AXES[0]].update(verdict="failed", severity="major", repairScope="local_repair")
                elif mutation == "missing":
                    axis.pop("repairScope")
                elif mutation == "invalid":
                    axis["repairScope"] = "unknown_future_scope"
                elif mutation == "mandatory":
                    changed_context["mandatoryEvidence"][0] = False
                else:
                    selected["evaluated"] = False
                changed_gate = seal_design_gate(changed_context, hash_pages, [png] * page_count, changed_vision)
                assert changed_gate["pages"][0]["designTargets"] == [advice_target]
                assert changed_gate["designImprovementStatus"] != "satisfied"
                assert deck_quality._blocking_design_targets(changed_gate["pages"][0]) == [advice_target]
                if mutation not in {"mandatory", "unreviewed"}:
                    changed_record = advisory_record({**changed_vision, "designGate": changed_gate}, changed_gate["pageDigests"])
                    assert 0 in deck_quality.quality_recovery_decisions(advice_ctx, hash_pages, changed_record)


def test_the_system_prompt_shows_the_model_the_json_it_must_answer_with():
    assert prompt_mod.ADVISORY_EXAMPLE in prompt_mod.SYSTEM_PROMPT
    assert all(issue in prompt_mod.SYSTEM_PROMPT for issue in advisory_mod.ISSUE_VOCABULARY)
    assert advisory_mod.parse_advisory_payload(
        json.loads(prompt_mod.ADVISORY_EXAMPLE), slide_count=24
    ) == [
        {"index": 2, "issues": ["empty_band", "unbalanced"]},
        {"index": 10, "issues": ["broken_figure"]},
    ]


def test_the_output_budget_covers_reasoning_as_well_as_the_answer():
    # Inert on codex (llmai drops max_tokens there) but read by every other
    # provider, where 1200 would truncate a long deck's answer.
    assert prompt_mod.ADVISORY_MAX_OUTPUT_TOKENS >= 1200
    assert advisory_mod.ADVISORY_MAX_OUTPUT_TOKENS is prompt_mod.ADVISORY_MAX_OUTPUT_TOKENS


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.content = text


class _FakeClient:
    """Stands in for the llmai client; records the kwargs it is handed."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.kwargs: dict = {}

    def generate(self, **kwargs):
        self.kwargs = kwargs
        return _FakeResponse(self.text)


def _install_fake_client(monkeypatch, client):
    import llmai

    monkeypatch.setattr(llmai, "get_client", lambda config=None: client)
    monkeypatch.setattr("utils.llm_config.get_llm_config", lambda *a, **k: None)
    # get_generate_kwargs resolves the configured provider for extra_body; this
    # test has no configured provider and none is needed to pin the shape.
    monkeypatch.setattr("utils.llm_utils.get_extra_body", lambda *a, **k: None)
    monkeypatch.setattr("utils.llm_config.enforce_llm_egress_policy", lambda: None)
    monkeypatch.setattr("utils.llm_provider.get_model", lambda *a, **k: "gpt-6-astra")
    monkeypatch.setattr("utils.llm_provider.is_codex_selected", lambda *a, **k: False)
    monkeypatch.setattr(
        "utils.llm_provider.get_llm_provider",
        lambda *a, **k: SimpleNamespace(value="codex"),
    )


def test_the_advisory_call_sends_the_sheet_and_the_budget(monkeypatch):
    client = _FakeClient('{"slides":[{"index":2,"issues":["cramped"]}]}')
    _install_fake_client(monkeypatch, client)

    model, payload = _run(advisory_mod._request_advisory(b"sheet", slide_count=6))

    assert model == "gpt-6-astra"
    assert payload == {"slides": [{"index": 2, "issues": ["cramped"]}]}
    assert client.kwargs["max_tokens"] == prompt_mod.ADVISORY_MAX_OUTPUT_TOKENS
    assert _part_types(client.kwargs["messages"][1]) == [
        "TextContentPart",
        "ImageContentPart",
    ]
    assert client.kwargs["messages"][1].content[1].data == b"sheet"


def test_the_advisory_accepts_a_provider_parsed_json_object(monkeypatch):
    from services.generation_core.quality import vision_advisory as shared_advisory

    client = _FakeClient({"slides": []})
    _install_fake_client(monkeypatch, client)

    model, payload = _run(shared_advisory._request_advisory(b"sheet", slide_count=1))

    assert model == "gpt-6-astra"
    assert payload == {"slides": []}


def test_an_empty_model_answer_is_a_named_skip_not_a_raise(monkeypatch):
    """``public_provider_empty_output`` is our own admission wrapper refusing a
    text-free response; the advisory records which outcome it was."""

    _install_fake_client(monkeypatch, _FakeClient("   "))

    result = _run(advisory_mod.run_vision_advisory([_png()], slide_count=1))

    assert result == {"skipped": "provider_outcome:public_provider_empty_output"}


# ---------------------------------------------------------------------------
# Sheet budget
# ---------------------------------------------------------------------------


def test_the_sheet_stays_inside_the_byte_budget_a_vision_request_allows():
    from PIL import Image

    def noisy(index: int) -> bytes:
        image = Image.effect_noise((1280, 720), 96).convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    sheet = sheet_mod.compose_contact_sheet_sync([noisy(index) for index in range(40)])

    assert len(sheet) <= sheet_mod.SHEET_MAX_BYTES
    with Image.open(io.BytesIO(sheet)) as image:
        assert image.width <= sheet_mod.SHEET_MAX_WIDTH_PX <= 1600


def test_a_sheet_over_the_budget_spends_quality_and_then_pixels(monkeypatch):
    from PIL import Image

    def noisy() -> bytes:
        buffer = io.BytesIO()
        Image.effect_noise((1280, 720), 96).convert("RGB").save(buffer, format="PNG")
        return buffer.getvalue()

    monkeypatch.setattr(sheet_mod, "SHEET_MAX_BYTES", 250_000)

    sheet = sheet_mod.compose_contact_sheet_sync([noisy() for _ in range(40)])

    assert len(sheet) <= 250_000
    with Image.open(io.BytesIO(sheet)) as image:
        # Thumbnails survive: the numbering the model answers with is drawn on
        # them, so the sheet must never shrink past legibility.
        assert image.width >= 640
