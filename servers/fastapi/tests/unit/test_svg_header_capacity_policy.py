"""New header policy, legacy replay and real fenced fallback publication."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
from services import svg_common_header_design_plan as policy
from services import svg_common_header_generation as generation
from services.authored_canvas import request_canvas_metadata
from services.svg_common_header_design_checkpoint import (
    _envelope, publish_header_design, read_update_header_design,
)
from services.svg_generation_admission import pin_fresh_common_header_mode, require_svg_creation_admission
from utils.hashing import canonical_json_digest


def _request(**kwargs):
    return GeneratePresentationRequest(content="Operations", template="authored", strategy="svg",
        common_header_mode="uniform", **kwargs)


def _inputs(title="Required exact title", *, constrained=False):
    request = _request(slides_markdown=["# " + title])
    if constrained:
        from models.common_header_design import CommonHeaderDesignInput
        request.common_header_design = CommonHeaderDesignInput(instructions="Keep a shared header")
    plan = policy.seal_new_header_design_plan(request)
    snapshot = {"tokens": {"heading_font": "Noto Sans KR", "foreground": "#123456", "background": "#FFFFFF"},
                "common_header_design_plan": plan}
    snapshot["digest"] = canonical_json_digest(snapshot)
    outline = PresentationOutlineModel(slides=[SlideOutlineModel(content="# " + title, title=title,
                                                                page_id=str(uuid.uuid4()))])
    return request, plan, snapshot, outline, request_canvas_metadata(request)


def test_real_units_admit_large_bytes_and_reject_small_high_token_input():
    _, plan, _, _, _ = _inputs()
    envelope = {"serializedContextBytes": 40000, "serializedRequestBytes": 45000}
    accepted = policy.evaluate_header_capacity(plan, text="a" * 40000, schema={}, envelope=envelope)
    assert accepted["estimatedInputTokens"] < plan["input_token_limit"]
    assert accepted["providerCapacityVerified"] is False
    assert accepted["serializedContextBytes"] > 31744


def test_schema_images_and_separate_transport_guards_are_counted():
    from io import BytesIO
    from PIL import Image
    _, plan, _, _, _ = _inputs()
    image = BytesIO()
    Image.new("RGB", (1024, 1024)).save(image, format="PNG")
    envelope = {"serializedContextBytes": 100, "serializedRequestBytes": 10000}
    result = policy.evaluate_header_capacity(plan, text="hello", schema={"description": "schema"},
                                            images=[image.getvalue()], envelope=envelope)
    assert result["estimatedImageTokens"] == 765
    assert result["estimatedSchemaTokens"] > 0
    large_image = BytesIO()
    Image.new("RGB", (4096, 2048)).save(large_image, format="PNG")
    with pytest.raises(ValueError, match="input_capacity_exceeded"):
        policy.evaluate_header_capacity(plan, text="!" * 22000, schema={},
            images=[large_image.getvalue()] * 2,
            envelope={"serializedContextBytes": 22100, "serializedRequestBytes": 100000})
    for field, value in (("serializedContextBytes", 8388609), ("serializedRequestBytes", 33554433)):
        with pytest.raises(ValueError, match="input_capacity_exceeded"):
            policy.evaluate_header_capacity(plan, text="hello", schema={}, envelope={**envelope, field: value})


@pytest.mark.parametrize("source_mode", ["source_content_restyle", "raster_reconstruction"])
def test_restyle_omission_defaults_to_free_and_explicit_uniform_rejects(source_mode):
    request = GeneratePresentationRequest(content="Source", template="authored", strategy="svg")
    request.source_mode = source_mode
    pin_fresh_common_header_mode(request)
    assert request.common_header_mode == "free"
    request.common_header_mode = "uniform"
    with pytest.raises(Exception) as error:
        require_svg_creation_admission(request)
    assert error.value.detail == "svg_common_header_source_preservation_conflict"


def test_legacy_plan_replays_exactly_and_retains_byte_semantics():
    request, plan, _, _, _ = _inputs()
    legacy = {key: value for key, value in plan.items() if key not in {"policy", "free_compatible", "plan_digest"}}
    legacy["schema"] = policy.LEGACY_HEADER_DESIGN_PLAN_VERSION
    legacy["plan_digest"] = canonical_json_digest(legacy)
    request._svg_header_design_plan = legacy
    before = deepcopy(legacy)
    assert policy.seal_new_header_design_plan(request) == before
    assert request._svg_header_design_plan == before
    assert policy.validate_header_design_plan(legacy) == before
    with pytest.raises(ValueError, match="input_capacity_exceeded"):
        policy.evaluate_header_capacity(legacy, text="a" * 40000, schema={},
            envelope={"serializedContextBytes": 40000, "serializedRequestBytes": 40000})
    assert not policy.permits_header_fallback(legacy, "svg_common_header_candidates_ineligible", {})


def test_exact_titles_measured_and_incompatible_known_titles_reject_at_admission():
    request, plan, snapshot, outline, canvas = _inputs("필수 원문 제목")
    outcome = policy.reduced_header_outcome(plan, request, snapshot, outline, canvas)
    assert outcome["mode"] == "reduced"
    assert outcome["bindings"][0]["title"] == "필수 원문 제목"
    assert outcome["contract"]["color"] == "#123456"
    assert len(outcome["bindings"][0]["lines"]) <= 2
    request, _, snapshot, _, canvas = _inputs("길" * 200, constrained=True)
    with pytest.raises(ValueError, match="fallback_incompatible"):
        policy.preflight_admitted_header(snapshot, request, canvas)


def test_fixed_metadata_is_preserved_by_the_deterministic_reduction():
    from models.common_header_design import CommonHeaderDesignInput
    request = _request(common_header_design=CommonHeaderDesignInput(document_values={"document_name": "Exact Name"}))
    plan = policy.seal_new_header_design_plan(request)
    outline = PresentationOutlineModel(slides=[SlideOutlineModel(content="# Title", title="Title", page_id="page-1")])
    outcome = policy.reduced_header_outcome(plan, request, {"tokens": {}}, outline, request_canvas_metadata(request))
    fields = outcome["bindings"][0]["fields"]
    assert {field["value_ref"]: field["value"] for field in fields} == {
        "title": "Title", "document_name": "Exact Name"}
    assert next(field for field in fields if field["value_ref"] == "document_name")["user_fixed"] is True


def test_unbound_request_never_reaches_fallback_or_checkpoint(monkeypatch):
    request, _, snapshot, outline, canvas = _inputs()
    request.instructions = "Changed after admission"
    read = AsyncMock()
    monkeypatch.setattr(generation, "read_update_header_design", read)
    with pytest.raises(ValueError, match="design_plan_conflict"):
        asyncio.run(generation.prepare_document_header(SimpleNamespace(snapshot=snapshot, canvas=canvas),
            request, uuid.uuid4(), outline, "en", SimpleNamespace()))
    read.assert_not_awaited()


def test_known_incompatible_titles_reject_on_actual_admission_path():
    from fastapi import HTTPException
    from models.common_header_design import CommonHeaderDesignInput
    from services.presentation_generation_staging import _durable_request_payload
    request = _request(slides_markdown=["# " + "길" * 200],
        common_header_design=CommonHeaderDesignInput(instructions="Keep uniform"))
    with pytest.raises(HTTPException) as error:
        _durable_request_payload(request)
    assert error.value.detail == "svg_common_header_fallback_incompatible"


def test_actual_call_builder_projects_prompt_schema_and_keeps_transport_guard(monkeypatch):
    from services import svg_common_header_design_calls as calls
    _, plan, _, _, _ = _inputs()
    transport = SimpleNamespace()
    transport.with_options = lambda **kwargs: transport
    client = SimpleNamespace(_client=transport)
    monkeypatch.setattr(calls, "enforce_llm_egress_policy", lambda: None)
    monkeypatch.setattr(calls, "get_llm_config", lambda: None)
    monkeypatch.setattr(calls, "get_client", lambda **kwargs: client)
    monkeypatch.setattr(calls, "get_model", lambda: "fixture")
    monkeypatch.setattr(calls, "get_llm_provider", lambda: SimpleNamespace(value="fixture"))
    monkeypatch.setattr(calls, "preflight_header_selector", lambda: {})
    monkeypatch.setattr(calls, "get_generate_kwargs", lambda **kwargs: kwargs)
    provider = AsyncMock()
    monkeypatch.setattr(calls, "_generate_structured_content", provider)
    values = [{"page_id": "page-1", "values": {"title": {
        "value": "Title", "user_fixed": False, "source_ref": canonical_json_digest("# Title")}}}]
    execute = calls.build_header_call({"context": "a" * 40000, "available_values": values},
                                     plan=plan, stage="selection", ordinal=1)
    assert execute.input_envelope["serializedContextBytes"] > 31744
    assert execute.input_envelope["estimatedSchemaTokens"] > 0
    assert execute.input_envelope["estimatedPromptTokens"] > policy.estimate_header_text_tokens("a" * 40000)
    with pytest.raises(ValueError, match="input_capacity_exceeded"):
        calls.build_header_call({"context": "!" * 40000, "available_values": values},
                                plan=plan, stage="selection", ordinal=1)
    provider.assert_not_awaited()


@pytest.mark.parametrize("status", ["started", "unknown", "failed"])
def test_unsettled_provider_steps_cannot_reduce(status):
    _, plan, _, _, _ = _inputs()
    assert not policy.permits_header_fallback(plan, "svg_common_header_candidates_ineligible",
                                               {"steps": {"design-1": {"status": status}}})


@pytest.mark.parametrize("reason", ["svg_common_header_checkpoint_input_conflict",
    "svg_common_header_candidate_identity_invalid", "svg_common_header_value_source_invalid",
    "svg_common_header_provider_result_unknown"])
def test_integrity_and_unbound_inputs_are_never_bounded_failures(reason):
    _, plan, _, _, _ = _inputs()
    assert not policy.permits_header_fallback(plan, reason, {"steps": {}})
    assert not policy.known_header_design_rejection(plan, ValueError(reason))


@pytest.mark.parametrize("mode", ["reduced", "free"])
def test_capacity_fallback_real_cas_replays_without_provider_spend(tmp_path, monkeypatch, mode):
    from test_authored_checkpoint_service import _checkpoint_database, _running_authored_job
    from models.sql.presentation_generation_job import PresentationGenerationJob

    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            _, job = await _running_authored_job(maker, total_slides=1)
            request, plan, snapshot, outline, canvas = _inputs("Title" if mode == "reduced" else "길" * 200)
            async with maker() as session:
                row = await session.get(PresentationGenerationJob, job.id)
                row.svg_style_snapshot = deepcopy(snapshot)
                row.authored_outline = outline.model_dump(mode="json")
                session.add(row)
                await session.commit()
            ctx = SimpleNamespace(snapshot=snapshot, canvas=canvas, header_pages=None)
            designed = AsyncMock(side_effect=ValueError("svg_common_header_input_capacity_exceeded"))
            monkeypatch.setattr(generation, "_prepare_designed_document_header", designed)
            result = await generation.prepare_document_header(ctx, request, job.presentation_id, outline,
                                                               "en", job, async_session_maker=maker)
            selection = result.snapshot["common_header_selection"]
            assert selection["mode"] == mode
            assert selection["notice"] == "svg_common_header_" + mode
            assert outline.slides[0].title == ("Title" if mode == "reduced" else "길" * 200)
            persisted = await read_update_header_design(job, session_maker=maker)
            assert persisted["status"] == "published"
            before = deepcopy(result.snapshot)
            changed = deepcopy(persisted)
            changed["fallback"]["mode"] = "free" if mode == "reduced" else "reduced"
            with pytest.raises(ValueError, match="fallback_conflict"):
                await read_update_header_design(job, payload=changed, session_maker=maker)
            designed.reset_mock()
            await generation.prepare_document_header(result, request, job.presentation_id, outline,
                                                       "en", job, async_session_maker=maker)
            designed.assert_not_awaited()
            assert result.snapshot == before
            async with maker() as session:
                saved = await session.get(PresentationGenerationJob, job.id)
                assert saved.svg_style_snapshot == before
                assert saved.authored_outline == outline.model_dump(mode="json")
                assert saved.svg_header_design_checkpoint["candidate_pages"] == {}
        finally:
            await engine.dispose()
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["unknown", "root_unknown", "candidate_unknown", "inflight",
                                      "title", "plan", "legacy"])
def test_real_cas_rejects_unsettled_tampered_and_legacy_fallback(tmp_path, monkeypatch, failure):
    from test_authored_checkpoint_service import _checkpoint_database, _running_authored_job
    from models.sql.presentation_generation_job import PresentationGenerationJob

    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            _, job = await _running_authored_job(maker, total_slides=1)
            request, plan, base, outline, canvas = _inputs()
            outcome = policy.reduced_header_outcome(plan, request, base, outline, canvas)
            selection = {"version": "svg-common-header-fallback/v1", "mode": "reduced",
                "plan_digest": plan["plan_digest"], "input_digest": plan["input_digest"],
                "reason": "svg_common_header_candidates_ineligible", "outcome": outcome,
                "notice": "svg_common_header_reduced"}
            selection["digest"] = canonical_json_digest(selection)
            document = {"status": "reduced", "fallback": selection, "steps": {},
                        "plan_digest": plan["plan_digest"], "input_digest": plan["input_digest"]}
            envelope = _envelope(job, None)
            envelope["document"] = document
            if failure == "unknown":
                document["steps"]["design-1"] = {"status": "unknown"}
            if failure == "root_unknown":
                document["state"] = "unknown"
            if failure == "inflight":
                envelope["candidate_pages"]["candidate"] = {"inflight": {"call": "running"}}
            if failure == "candidate_unknown":
                envelope["candidate_pages"]["candidate"] = {"review_execution": {"state": "unknown"}}
            if failure == "legacy":
                base["common_header_design_plan"] = {"schema": policy.LEGACY_HEADER_DESIGN_PLAN_VERSION}
            async with maker() as session:
                row = await session.get(PresentationGenerationJob, job.id)
                row.svg_style_snapshot = deepcopy(base)
                row.authored_outline = outline.model_dump(mode="json")
                row.svg_header_design_checkpoint = envelope
                session.add(row)
                await session.commit()
            proposed = {**base, "common_header": outcome["contract"], "common_header_selection": selection}
            outline.slides[0].common_header = outcome["bindings"][0]
            if failure == "title":
                outline.slides[0].title = "Changed"
            if failure == "plan":
                proposed["common_header_design_plan"] = {**plan, "input_token_limit": 999999}
            proposed["digest"] = canonical_json_digest({k: v for k, v in proposed.items() if k != "digest"})
            with pytest.raises(ValueError):
                await publish_header_design(job, snapshot=proposed, outline=outline, session_maker=maker)
            async with maker() as session:
                saved = await session.get(PresentationGenerationJob, job.id)
                assert saved.svg_style_snapshot == base
        finally:
            await engine.dispose()
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["capacity", "bounded", "unknown"])
def test_real_header_flow_never_repurchases_unknown_and_reduces_known_failure(tmp_path, monkeypatch, failure):
    from test_authored_checkpoint_service import _checkpoint_database, _running_authored_job
    from models.sql.presentation_generation_job import PresentationGenerationJob

    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            _, job = await _running_authored_job(maker, total_slides=1)
            request, _, snapshot, outline, canvas = _inputs()
            async with maker() as session:
                row = await session.get(PresentationGenerationJob, job.id)
                row.svg_style_snapshot = deepcopy(snapshot)
                row.authored_outline = outline.model_dump(mode="json")
                session.add(row)
                await session.commit()
            ctx = SimpleNamespace(snapshot=snapshot, canvas=canvas, header_pages=None, count=1,
                source_reflow=False, raster_reconstruction=False, manifests=[{}])
            monkeypatch.setattr(generation, "preflight_header_selector", lambda: {"model": "fixture"})
            monkeypatch.setattr(generation, "_design_input", lambda *args: {"context": "bound"})
            monkeypatch.setattr(generation, "_design_reference_images", lambda *args: [])
            calls = []

            def build(payload, *, plan, **kwargs):
                if failure == "capacity":
                    policy.evaluate_header_capacity(plan, text="!" * 40000, schema={},
                        envelope={"serializedContextBytes": 40000, "serializedRequestBytes": 40000})

                async def execute():
                    calls.append("provider")
                    if failure == "unknown":
                        raise ValueError("svg_common_header_provider_result_unknown")
                    return {"result": {"candidates": []}}
                return execute

            monkeypatch.setattr(generation, "build_header_call", build)
            arguments = dict(checkpoint=AsyncMock(), async_session_maker=maker, read_png=None,
                             write_png=None, page_runner=AsyncMock(), page_hooks_factory=None, state_factory=None)
            if failure == "unknown":
                for _ in range(2):
                    with pytest.raises(ValueError, match="provider_result_unknown"):
                        await generation.prepare_document_header(ctx, request, job.presentation_id, outline,
                                                                  "en", job, **arguments)
                assert calls == ["provider"]
                assert "fallback" not in await read_update_header_design(job, session_maker=maker)
            else:
                result = await generation.prepare_document_header(ctx, request, job.presentation_id, outline,
                                                                  "en", job, **arguments)
                assert result.snapshot["common_header_selection"]["mode"] == "reduced"
                assert len(calls) == (0 if failure == "capacity" else 2)
                assert result.header_pages[0]["title"] == "Required exact title"
            arguments["page_runner"].assert_not_awaited()
        finally:
            await engine.dispose()
    asyncio.run(scenario())


def test_legacy_publication_still_requires_and_reuses_selected_body(tmp_path, monkeypatch):
    from test_authored_checkpoint_service import _checkpoint_database, _running_authored_job
    from models.common_header_design import CommonHeaderDesignInput
    from models.sql.presentation_generation_job import PresentationGenerationJob
    from services.svg_common_header_design_checkpoint import _candidate_key

    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            _, job = await _running_authored_job(maker, total_slides=1)
            request = _request(common_header_design=CommonHeaderDesignInput(document_values={"name": "Exact"}))
            plan = policy.seal_new_header_design_plan(request)
            outline = PresentationOutlineModel(slides=[
                SlideOutlineModel(content="# Title", title="Title", page_id="p")])
            outcome = policy.reduced_header_outcome(plan, request, {"tokens": {}}, outline,
                                                    request_canvas_metadata(request))
            legacy = {key: value for key, value in plan.items()
                      if key not in {"policy", "free_compatible", "plan_digest"}}
            legacy["schema"] = policy.LEGACY_HEADER_DESIGN_PLAN_VERSION
            legacy["plan_digest"] = canonical_json_digest(legacy)
            base = {"common_header_design_plan": legacy}
            header = outcome["contract"]
            selection = {"version": "svg-common-header-selection/v2", "plan_digest": legacy["plan_digest"],
                "selected_candidate_id": header["candidate_id"], "selected_revision": header["revision"],
                "representative_page_id": "p", "selection_input_digest": canonical_json_digest({"selection": "bound"})}
            envelope = _envelope(job, None)
            envelope["document"] = {"status": "selected", "selection": selection,
                "selected_candidate_id": header["candidate_id"],
                "selection_input_digest": selection["selection_input_digest"]}
            async with maker() as session:
                row = await session.get(PresentationGenerationJob, job.id)
                row.svg_style_snapshot = base
                row.authored_outline = outline.model_dump(mode="json")
                row.svg_header_design_checkpoint = deepcopy(envelope)
                session.add(row)
                await session.commit()
            proposed = {**base, "common_header": header, "common_header_selection": selection}
            proposed["digest"] = canonical_json_digest(proposed)
            outline.slides[0].common_header = outcome["bindings"][0]
            with pytest.raises(ValueError, match="selected_body_missing"):
                await publish_header_design(job, snapshot=proposed, outline=outline, session_maker=maker)
            key = _candidate_key(0, {"candidate_revision": str(header["revision"]),
                                     "candidate_id": header["candidate_id"], "page_id": "p"})
            envelope["candidate_pages"][key] = {"author_body_svg": '<svg xmlns="http://www.w3.org/2000/svg"/>'}
            async with maker() as session:
                row = await session.get(PresentationGenerationJob, job.id)
                row.svg_header_design_checkpoint = envelope
                session.add(row)
                await session.commit()
            await publish_header_design(job, snapshot=proposed, outline=outline, session_maker=maker)
            assert job.svg_style_snapshot == proposed
            assert job.svg_header_design_checkpoint["document"] == envelope["document"]
            assert job.svg_style_snapshot["common_header_design_plan"] == legacy
        finally:
            await engine.dispose()
    asyncio.run(scenario())
