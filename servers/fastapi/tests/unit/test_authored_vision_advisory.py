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
        from services.svg_final_selected_continuation import _consume_local_producer, INITIAL_NAMESPACE
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
                    _consume_local_producer(fresh_context, valid_pages, [png], evidence,
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
