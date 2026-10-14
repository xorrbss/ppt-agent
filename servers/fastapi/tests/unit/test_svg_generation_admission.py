import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from models.authored_vnext.deck import MasterDigestPin
from models.authored_vnext.master.contract import DeckMasterContract
from models.generate_presentation_request import AuthoredMasterPinInput, GeneratePresentationRequest
from services.authored_vnext.generation.errors import PublishedMasterRequiredError
from services.authored_vnext.generation.legacy_pin import ResolvedLegacyMasterPin
from services.generation_core.style.design_pack_projection import DesignPackProjectionError
from services.svg_generation_admission import (
    _resolve_design_reference,
    _resolve_master_rules,
    require_svg_creation_admission,
    source_svg_capability,
)
from tests.support.authored_vnext_master_fixture import master_contract_payload


def test_partial_recovery_page_input_digest_matches_new_and_legacy_svg_inputs():
    from services.svg_partial_recovery import _expected_page_input_digest
    from services.svg_presentation_checkpoint import _digest
    from services.ppt_writing_policy import WRITING_POLICY_ID

    legacy = GeneratePresentationRequest(content="Quarterly update")
    current = legacy.model_copy(update={"writing_policy": WRITING_POLICY_ID})
    job = SimpleNamespace(
        authored_outline={"slides": [{"content": "Page one"}]},
        svg_style_snapshot={"digest": "frozen-style"},
        total_slides=1,
    )
    page_assets = {"asset-1": {"sha256": "frozen-asset"}}
    page_input = {
        "content": "Page one", "style": job.svg_style_snapshot, "index": 0,
        "count": 1, "instructions": None, "language": None, "preset": None,
    }
    legacy_digest = _digest({"page": _digest(page_input), "assets": page_assets})
    current_digest = _digest({
        "page": _digest({**page_input, "writing_policy": WRITING_POLICY_ID}),
        "assets": page_assets,
    })

    assert _expected_page_input_digest(legacy, job, 0, page_assets) == legacy_digest
    assert _expected_page_input_digest(current, job, 0, page_assets) == current_digest
    assert current_digest != legacy_digest

    # A frozen common header must bind the same title, content and source in
    # initial authoring and durable recovery; changing either is not a replay.
    from copy import deepcopy
    from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
    from services.authored_canvas import canvas_metadata_for_aspect_ratio
    from services.svg_common_header import prepare_common_header
    from services.svg_generation_inputs import svg_page_input_digest
    from services.svg_contract import finalize_svg

    outline = PresentationOutlineModel(slides=[
        SlideOutlineModel(content="원본 운영 현황", title="운영 현황"),
        SlideOutlineModel(content="승인 전 수정본은 비공개다.",
            title="승인 전 수정본은 공개하지 않고 게시본을 유지한다"),
    ])
    source_style = {"tokens": {}, "common_header": {"title_width_px": 470}}
    before = deepcopy(source_style)
    canvas = canvas_metadata_for_aspect_ratio("16:9")
    contract, bindings = prepare_common_header(source_style, outline, canvas)
    assert source_style == before
    assert len(bindings[0]["lines"]) == 1
    assert len(bindings[1]["lines"]) == 2
    assert contract["title_slot_lines"] == 2
    style = {"tokens": {}, "common_header": contract}
    frozen = SimpleNamespace(svg_style_snapshot=style, total_slides=2,
        authored_outline={"slides": [
            {"content": slide.content, "title": slide.title, "common_header": binding}
            for slide, binding in zip(outline.slides, bindings)
        ]})
    for index, binding in enumerate(bindings):
        initial = svg_page_input_digest(content=outline.slides[index].content, style=style,
            index=index, count=2, instructions=None, language=None, preset=None,
            writing_policy=WRITING_POLICY_ID, header_page=binding, assets=page_assets)
        assert initial == _expected_page_input_digest(current, frozen, index, page_assets)
    broken = deepcopy(frozen)
    broken.authored_outline["slides"][0]["content"] = "원본과 다른 내용"
    with pytest.raises(ValueError, match="svg_common_header"):
        _expected_page_input_digest(current, broken, 0, page_assets)
    body = '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720"><rect x="64" y="400" width="200" height="60" fill="#2563EB"/></svg>'
    first = finalize_svg(body, index=0, n=2, style_snapshot=style, header_page=bindings[0])
    with pytest.raises(ValueError, match="svg_common_header"):
        finalize_svg(first, index=0, n=2, style_snapshot=style, header_page=bindings[0])
    assert finalize_svg(first, index=0, n=2, style_snapshot=style,
        header_page=bindings[0], allow_existing_common_header=True) == first
    preserved = SimpleNamespace(content="원본 보존", strategy="svg",
        source_mode="raster_reconstruction", common_header_mode="uniform",
        full_page_preserve_pages=(0,))
    with pytest.raises(HTTPException) as blocked:
        require_svg_creation_admission(preserved)
    assert blocked.value.detail == "svg_common_header_source_preservation_conflict"


def test_external_header_title_plan_classifies_page_labels_and_restores_exact_budget():
    from services.presentation_generation_staging import (
        _durable_request_payload, _request_from_durable_payload,
    )
    from services.public_operation_admission_models import plan_provider_requests
    from services.public_v2_orchestrator import _staged_svg_provider_request_plan
    from services.svg_common_header_title_budget import (
        INTERNAL_HEADER_TITLE_PLAN_KEY, seal_new_external_header_title_plan,
        svg_header_title_admission_budget,
    )
    from utils.outline_utils import explicit_outline_title

    slides = [
        "# 원본 1페이지\n운영 계획은 검토 중이다.",
        "# 원본 21/88페이지\n서비스 흐름을 설명한다.",
        "# Original page 38\nThe table lists requirements.",
        "# source page 59\nThe screen shows the approval state.",
        "# 원본 88페이지\n차트와 구성도는 입력 자료를 따른다.",
        "# 원본 이미지 보존\n원본 이미지는 승인 전까지 보존한다.",
    ]
    assert all(explicit_outline_title(page) is None for page in slides[:5])
    assert explicit_outline_title(slides[5]) == "원본 이미지 보존"
    request = GeneratePresentationRequest(
        content="입력 문서 재구성", template="authored", strategy="svg",
        common_header_mode="uniform", slides_markdown=slides,
    )
    plan = seal_new_external_header_title_plan(request)
    assert plan["missing_indices"] == [0, 1, 2, 3, 4]
    assert svg_header_title_admission_budget(request, len(slides)) == 1

    payload = _durable_request_payload(request)
    restored = _request_from_durable_payload(payload)
    assert payload[INTERNAL_HEADER_TITLE_PLAN_KEY] == plan
    assert restored._svg_header_title_plan == plan
    assert svg_header_title_admission_budget(restored, len(slides)) == 1
    legacy = dict(payload)
    legacy.pop(INTERNAL_HEADER_TITLE_PLAN_KEY)
    assert _request_from_durable_payload(legacy)._svg_header_title_plan is None

    standard = plan_provider_requests(
        slide_count=len(slides), image_budget=0, repair_budget=0, vqa_budget=0,
    )
    funded = plan_provider_requests(
        slide_count=len(slides), image_budget=0, repair_budget=0, vqa_budget=0,
        header_title_budget=1,
    )
    assert funded.initial_request_limit == standard.initial_request_limit + 1
    staged = {"illustration_budget": 0, "partitions": []}
    assert _staged_svg_provider_request_plan(len(slides), staged, header_title_budget=1).initial_request_limit == (
        _staged_svg_provider_request_plan(len(slides), staged).initial_request_limit + 1
    )


def test_external_header_titles_checkpoint_one_call_resume_unknown_and_source_guard():
    import asyncio

    from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
    from services.svg_common_header_title_budget import (
        INTERNAL_HEADER_TITLE_PLAN_KEY, seal_new_external_header_title_plan,
    )
    from services.svg_common_header_titles import prepare_missing_header_titles
    from services.provider_usage_instrumentation import _ACTIVE_SCOPE

    slides = [
        "# 원본 1페이지\n운영 계획은 검토 중이다.",
        "# 서비스 구성\n서비스 구성은 승인 이후 확정한다.",
    ]
    request = GeneratePresentationRequest(
        content="입력 문서 재구성", template="authored", strategy="svg",
        common_header_mode="uniform", slides_markdown=slides,
    )
    plan = seal_new_external_header_title_plan(request)
    outline = PresentationOutlineModel(slides=[SlideOutlineModel(content=page) for page in slides])

    class MemorySession:
        def __init__(self):
            self.row = None
            self.commits = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def scalar(self, _statement):
            return self.row

        def add(self, row):
            from models.sql.public_admission import AuthoredDeckPlanCheckpoint
            assert len(row.input_digest) == AuthoredDeckPlanCheckpoint.__table__.c.input_digest.type.length
            assert len(row.output_digest) == AuthoredDeckPlanCheckpoint.__table__.c.output_digest.type.length
            self.row = row

        async def execute(self, statement):
            values = statement.compile().params
            self.row.payload = values["payload"]
            self.row.output_digest = values["output_digest"]
            return SimpleNamespace(rowcount=1)

        async def commit(self):
            self.commits += 1

    async def owned_job(_session, _id, _token, *, fencing_token=None):
        return job

    async def prepared_title(payload, *, plan):
        assert _ACTIVE_SCOPE.get().workspace_operation_id is None
        calls.append(payload)
        return {
            "source_digest": payload["source_digest"],
            "page_count": payload["page_count"],
            "titles": [{"page_index": 0, "source_digest": payload["pages"][0]["source_digest"],
                        "title": "운영 계획", "source_quote": "운영 계획"}],
        }

    session = MemorySession()
    calls = []
    job = SimpleNamespace(
        id=uuid.uuid4(), attempt_token="first", attempt_number=1,
        request_sha256="a" * 64, workspace_operation_id=uuid.uuid4(),
        request_payload={INTERNAL_HEADER_TITLE_PLAN_KEY: plan},
        _workspace_fencing_token=None, public_only=False,
    )
    run = lambda source, callback: asyncio.run(prepare_missing_header_titles(
        request, job, source, session_maker=lambda: session,
        owned_job=owned_job, provider_callback=callback,
    ))
    assert calls == [] and session.row is None
    prepared = run(outline, prepared_title)
    assert len(calls) == 1
    assert session.row.payload["state"] == "succeeded"
    assert prepared.slides[0].title == "운영 계획"
    assert prepared.slides[1].title == "서비스 구성"
    assert [slide.content for slide in prepared.slides] == slides
    assert [slide.content for slide in outline.slides] == slides

    job.attempt_token = "resumed"
    job.attempt_number = 2
    assert run(outline, prepared_title).slides[0].title == "운영 계획"
    assert len(calls) == 1
    changed = PresentationOutlineModel(slides=[
        SlideOutlineModel(content=slides[0] + "\n새로운 사실"),
        SlideOutlineModel(content=slides[1]),
    ])
    with pytest.raises(ValueError, match="svg_common_header_title_plan_conflict"):
        run(changed, prepared_title)
    assert len(calls) == 1

    session = MemorySession()
    job = SimpleNamespace(**{**vars(job), "id": uuid.uuid4(), "attempt_number": 1,
                             "attempt_token": "unknown", "request_sha256": "b" * 64})

    async def unknown_provider(_payload, *, plan):
        assert _ACTIVE_SCOPE.get().workspace_operation_id is None
        calls.append("attempted")
        raise RuntimeError("provider result unknown")

    with pytest.raises(RuntimeError, match="provider result unknown"):
        run(outline, unknown_provider)
    assert session.row.payload["state"] == "unknown"
    assert len(calls) == 2
    with pytest.raises(ValueError, match="svg_common_header_title_outcome_unresolved"):
        run(outline, prepared_title)
    assert len(calls) == 2


@pytest.mark.parametrize("slide_count", [1, 2, 5, 6, 12, 80])
def test_restoration_policy_reserves_final_review_without_changing_legacy(slide_count):
    from copy import deepcopy
    from services.svg_authoring_profile import (
        ART_DIRECTION_PROFILE_V8, read_quality_recovery_execution_plan,
        seal_new_quality_recovery_plan, svg_quality_review_admission_budget,
        validate_quality_recovery_agreement,
    )
    from utils.hashing import canonical_json_digest

    def frozen(request, plan):
        value = dict(authoring_profile=dict(ART_DIRECTION_PROFILE_V8),
                     quality_recovery_policy=dict(request._svg_quality_recovery_policy),
                     quality_recovery_plan=deepcopy(plan))
        value["digest"] = canonical_json_digest(value, allow_nan=True)
        return value

    current = SimpleNamespace(vision_qa=False)
    plan = seal_new_quality_recovery_plan(current, slide_count)
    assert current._svg_quality_recovery_policy["version"] == 2
    snapshot = frozen(current, plan)
    current._svg_style_snapshot = snapshot
    execution = read_quality_recovery_execution_plan(snapshot)
    final = execution.get("final_selected_review")
    if slide_count > 1:
        assert final["physical_offset"] == execution["review_call_limit"]
        assert final["run_limit"] == 1
        assert final["review_call_limit"] == (slide_count + 4) // 5 + 2
    else:
        assert final is None
    total = execution["review_call_limit"] + (final["review_call_limit"] if final else 0)
    assert svg_quality_review_admission_budget(current, slide_count) == total
    if slide_count == 80:
        assert total == 54

    from services.svg_partial_recovery import _recovery_review_allocation
    regular, format_control, recovered_final = _recovery_review_allocation(current, snapshot, slide_count)
    assert regular == execution["review_call_limit"]
    assert recovered_final == final
    assert regular + (recovered_final["review_call_limit"] if recovered_final else 0) == total
    manifest = {}
    if format_control is not None:
        manifest["global_format_retry_control"] = format_control
    if recovered_final is not None:
        manifest["final_selected_review_control"] = recovered_final
    assert read_quality_recovery_execution_plan(snapshot, manifest=manifest) == execution

    legacy = SimpleNamespace(vision_qa=False, _svg_quality_recovery_policy={"id": "svg-quality-first", "version": 1})
    old_plan = seal_new_quality_recovery_plan(legacy, slide_count)
    old_snapshot = frozen(legacy, old_plan)
    legacy._svg_style_snapshot = old_snapshot
    original = deepcopy(old_snapshot)
    assert "final_selected_review_control" not in old_plan
    assert svg_quality_review_admission_budget(legacy, slide_count) == old_plan["review_call_limit"]
    legacy_regular, legacy_format, legacy_final = _recovery_review_allocation(legacy, old_snapshot, slide_count)
    assert legacy_regular == (legacy_format["review_call_limit"] if legacy_format else old_plan["review_call_limit"])
    assert legacy_final["physical_offset"] == legacy_regular
    assert seal_new_quality_recovery_plan(legacy, slide_count) == old_plan
    assert old_snapshot == original
    with pytest.raises(ValueError, match="svg_quality_recovery_policy_conflict"):
        validate_quality_recovery_agreement(current, old_snapshot)


def test_restoration_final_review_control_rejects_tampering():
    from copy import deepcopy
    from services.svg_authoring_profile import (
        ART_DIRECTION_PROFILE_V8, global_format_retry_control,
        read_quality_recovery_execution_plan, read_quality_recovery_plan,
        seal_new_quality_recovery_plan,
    )
    from utils.hashing import canonical_json_digest

    request = SimpleNamespace(vision_qa=False)
    plan = seal_new_quality_recovery_plan(request, 12)
    snapshot = dict(authoring_profile=dict(ART_DIRECTION_PROFILE_V8),
                    quality_recovery_policy=dict(request._svg_quality_recovery_policy),
                    quality_recovery_plan=plan)
    snapshot["digest"] = canonical_json_digest(snapshot, allow_nan=True)
    for field, value in (("physical_offset", 999), ("run_limit", True), ("namespace", "unadmitted")):
        changed = deepcopy(snapshot)
        changed["quality_recovery_plan"]["final_selected_review_control"][field] = value
        changed["digest"] = canonical_json_digest({k: v for k, v in changed.items() if k != "digest"}, allow_nan=True)
        with pytest.raises(ValueError, match="svg_quality_recovery_plan_invalid"):
            read_quality_recovery_plan(changed)
    manifest = dict(global_format_retry_control=global_format_retry_control(snapshot))
    manifest["global_format_retry_control"]["global_retry_limit"] = True
    with pytest.raises(ValueError, match="svg_quality_recovery_execution_plan_invalid"):
        read_quality_recovery_execution_plan(snapshot, manifest=manifest)
    with pytest.raises(ValueError, match="svg_quality_recovery_execution_plan_invalid"):
        read_quality_recovery_execution_plan(snapshot, manifest={"final_selected_review_control": {}})


def _svg_request(**overrides):
    base = dict(
        strategy="svg", source_mode=None, design_pack_pin=None,
        generation_engine="authored", generation_mode="auto", master_pin=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_design_pack_pin_is_rejected_with_a_specific_structural_reason():
    request = _svg_request(design_pack_pin={"designPackId": "acme", "version": 1})
    with pytest.raises(HTTPException) as excinfo:
        require_svg_creation_admission(request)
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail == "svg_design_pack_requires_source_native_patch_target"


def test_design_pack_projection_error_names_the_real_gap():
    from services.generation_core.style.design_pack_projection import assert_design_pack_projects

    with pytest.raises(DesignPackProjectionError):
        assert_design_pack_projects()


def test_master_pin_alone_no_longer_blocks_admission():
    request = _svg_request(
        master_pin=AuthoredMasterPinInput(master_id="master:acme-1", revision=1, digest="sha256:" + "1" * 64)
    )
    # No exception: master_pin admissibility is decided later, once resolved
    # and projected (see _resolve_master_rules), not by presence alone.
    require_svg_creation_admission(request)


def test_svg_design_reference_does_not_reinterpret_legacy_pack_execution():
    pin = {"designPackId": "acme", "version": 1, "digest": "sha256:" + "1" * 64}
    require_svg_creation_admission(_svg_request(svg_design_reference_pin=pin))
    with pytest.raises(HTTPException) as error:
        require_svg_creation_admission(_svg_request(svg_design_reference_pin=pin, master_pin=pin))
    assert error.value.detail == "svg_design_reference_conflict"


def test_svg_design_reference_requires_owner_and_pins_target_geometry():
    pin = {"designPackId": "acme", "version": 1, "digest": "sha256:" + "1" * 64}
    request = _svg_request(svg_design_reference_pin=pin)
    canvas = SimpleNamespace(width_px=720, height_px=1280)
    with patch("utils.owner_context.get_current_owner_id", return_value=None):
        with pytest.raises(HTTPException) as error:
            _resolve_design_reference(request, canvas)
    assert error.value.status_code == 401
    owner = uuid.uuid4()
    with patch("utils.owner_context.get_current_owner_id", return_value=owner), patch(
        "services.generation_core.style.design_pack_projection.resolve_design_pack_reference",
        return_value={"reference_semantics": "design-identity"},
    ) as resolve:
        assert _resolve_design_reference(request, canvas)["reference_semantics"] == "design-identity"
        resolve.assert_called_once_with(owner_id=owner, pin=pin, width=720, height=1280)


def test_source_svg_reference_capability_allows_company_design_and_selected_canvas():
    capability = source_svg_capability(
        strategy="svg", source_content=True, has_company_standard=True,
        reference_mode="exact_reconstruction", style_id="default",
    )
    assert capability["enabled"] is True
    assert capability["canvasSource"] == "selected-svg"


def _resolved_master(contract):
    digest = contract.digest or ("sha256:" + "0" * 64)
    pin = MasterDigestPin(
        master_id="master:acme-1", master_revision=1, master_digest=digest,
        layout_registry_digest="sha256:" + "0" * 64, font_manifest_digest="sha256:" + "0" * 64,
    )
    return ResolvedLegacyMasterPin(owner_id=uuid.uuid4(), pin=pin, contract=contract)


def test_resolve_master_rules_returns_none_without_a_pin():
    request = _svg_request()
    canvas = SimpleNamespace(width_px=1280, height_px=720)
    assert _resolve_master_rules(request, canvas) == (None, None)


def test_resolve_master_rules_projects_the_published_master():
    contract = DeckMasterContract.model_validate(master_contract_payload())
    resolved = _resolved_master(contract)
    request = _svg_request(
        master_pin=AuthoredMasterPinInput(master_id="master:acme-1", revision=1, digest="sha256:" + "1" * 64)
    )
    canvas = SimpleNamespace(width_px=1280, height_px=720)
    with patch("services.svg_generation_admission.is_authored_vnext_master_enabled", return_value=True), \
         patch("utils.owner_context.get_current_owner_id", return_value=uuid.uuid4()), \
         patch("services.authored_vnext.generation.legacy_pin.resolve_legacy_master_pin", return_value=resolved):
        rules, pin = _resolve_master_rules(request, canvas)
    assert rules["tokens"]["accent"] == "#1463FF"
    assert pin["masterId"] == "master:acme-1"


def test_resolve_master_rules_requires_an_owner():
    request = _svg_request(
        master_pin=AuthoredMasterPinInput(master_id="master:acme-1", revision=1, digest="sha256:" + "1" * 64)
    )
    canvas = SimpleNamespace(width_px=1280, height_px=720)
    with patch("services.svg_generation_admission.is_authored_vnext_master_enabled", return_value=True), \
         patch("utils.owner_context.get_current_owner_id", return_value=None):
        with pytest.raises(HTTPException) as excinfo:
            _resolve_master_rules(request, canvas)
    assert excinfo.value.status_code == 401


def test_resolve_master_rules_maps_pipeline_errors_to_409():
    request = _svg_request(
        master_pin=AuthoredMasterPinInput(master_id="master:acme-1", revision=1, digest="sha256:" + "1" * 64)
    )
    canvas = SimpleNamespace(width_px=1280, height_px=720)
    with patch("services.svg_generation_admission.is_authored_vnext_master_enabled", return_value=True), \
         patch("utils.owner_context.get_current_owner_id", return_value=uuid.uuid4()), \
         patch(
             "services.authored_vnext.generation.legacy_pin.resolve_legacy_master_pin",
             side_effect=PublishedMasterRequiredError(),
         ):
        with pytest.raises(HTTPException) as excinfo:
            _resolve_master_rules(request, canvas)
    assert excinfo.value.status_code == 409


@pytest.mark.parametrize("profile_id", [None, 'current-v4', 'art-direction-v1', 'art-direction-v1@2', 'art-direction-v1@3', 'art-direction-v1@4', 'art-direction-v1@5', 'art-direction-v1@6', 'art-direction-v1@7', 'art-direction-v1@8'])
def test_authoring_profile_staging_preserves_legacy_and_frozen_new_identity(monkeypatch, profile_id):
    from models.generate_presentation_request import GeneratePresentationRequest
    from services.presentation_generation_staging import _durable_request_payload, _request_from_durable_payload
    from services.svg_authoring_profile import (
        ART_DIRECTION_PROFILE, ART_DIRECTION_PROFILE_V2, ART_DIRECTION_PROFILE_V3, ART_DIRECTION_PROFILE_V4, ART_DIRECTION_PROFILE_V5, ART_DIRECTION_PROFILE_V6, ART_DIRECTION_PROFILE_V7, INTERNAL_SVG_AUTHORING_PROFILE_KEY, select_svg_authoring_profile,
    )
    from services.svg_generation_admission import pin_svg_style
    from utils.hashing import canonical_json_digest

    legacy_request = GeneratePresentationRequest(content='deck', strategy='svg')
    select_svg_authoring_profile(legacy_request, "current-v4", trusted_internal=True)
    baseline = _durable_request_payload(legacy_request)
    request = GeneratePresentationRequest(content="deck", strategy="svg")
    monkeypatch.setenv("CANVAS_BENCH_ENABLED", "true")
    if profile_id is not None:
        select_svg_authoring_profile(request, profile_id)
    payload = _durable_request_payload(request)
    monkeypatch.delenv("CANVAS_BENCH_ENABLED")
    restored = _request_from_durable_payload(payload)
    assert pin_svg_style(restored) == payload["_internal_svg_style_snapshot"]
    if profile_id == "current-v4":
        assert payload == baseline
        assert INTERNAL_SVG_AUTHORING_PROFILE_KEY not in payload
        return
    snapshot = payload["_internal_svg_style_snapshot"]
    expected = {'art-direction-v1': ART_DIRECTION_PROFILE, 'art-direction-v1@2': ART_DIRECTION_PROFILE_V2, 'art-direction-v1@3': ART_DIRECTION_PROFILE_V3, 'art-direction-v1@4': ART_DIRECTION_PROFILE_V4, 'art-direction-v1@5': ART_DIRECTION_PROFILE_V5, 'art-direction-v1@6': ART_DIRECTION_PROFILE_V6, 'art-direction-v1@7': ART_DIRECTION_PROFILE_V7, None: {'id': 'art-direction-v1', 'version': 8}, 'art-direction-v1@8': {'id': 'art-direction-v1', 'version': 8}}[profile_id]
    assert payload[INTERNAL_SVG_AUTHORING_PROFILE_KEY] == expected
    assert snapshot["authoring_profile"] == expected
    new_contract_keys = {"readability_policy"} if profile_id is None else set()
    if profile_id is None:
        from services.document_canvas_generation import validate_svg_readability_policy

        assert validate_svg_readability_policy(snapshot["readability_policy"])["minimum_font_pt"] == 9
    else:
        assert "readability_policy" not in snapshot
    assert {key: value for key, value in snapshot.items() if key not in {"authoring_profile", "digest"} | new_contract_keys} == {
        key: value for key, value in baseline["_internal_svg_style_snapshot"].items() if key != "digest"
    }
    assert snapshot["digest"] == canonical_json_digest(
        {key: value for key, value in snapshot.items() if key != "digest"}, allow_nan=True,
    )
    assert snapshot["digest"] != baseline["_internal_svg_style_snapshot"]["digest"]
    assert request.model_dump(mode="json") == GeneratePresentationRequest(content="deck", strategy="svg").model_dump(mode="json")


@pytest.mark.parametrize("gate", [None, "false", "TRUE", "1"])
@pytest.mark.parametrize("profile_id", ["art-direction-v1", "art-direction-v1@2", "art-direction-v1@3", "art-direction-v1@4", "art-direction-v1@5", "art-direction-v1@6", "art-direction-v1@7"])
def test_new_authoring_selection_requires_exact_bench_gate(monkeypatch, gate, profile_id):
    from services.svg_authoring_profile import select_svg_authoring_profile

    monkeypatch.delenv("CANVAS_BENCH_ENABLED", raising=False)
    if gate is not None:
        monkeypatch.setenv("CANVAS_BENCH_ENABLED", gate)
    with pytest.raises(ValueError, match="bench_field_disabled"):
        select_svg_authoring_profile(_svg_request(), profile_id)


@pytest.mark.parametrize("profile_id", ["art-direction-v1", "art-direction-v1@2", "art-direction-v1@3", "art-direction-v1@4", "art-direction-v1@5", "art-direction-v1@6", "art-direction-v1@7"])
def test_new_authoring_selection_cannot_rewrite_old_snapshot(monkeypatch, profile_id):
    from models.generate_presentation_request import GeneratePresentationRequest
    from services.presentation_generation_staging import _durable_request_payload, _request_from_durable_payload
    from services.svg_authoring_profile import INTERNAL_SVG_AUTHORING_PROFILE_KEY, select_svg_authoring_profile, validate_authoring_profile
    from services.svg_generation_admission import pin_svg_style

    request = GeneratePresentationRequest(content="deck", strategy="svg")
    legacy_snapshot = pin_svg_style(request)
    monkeypatch.setenv("CANVAS_BENCH_ENABLED", "true")
    select_svg_authoring_profile(request, profile_id)
    with pytest.raises(HTTPException) as error:
        pin_svg_style(request)
    assert error.value.detail == "svg_authoring_profile_conflict"
    assert request._svg_style_snapshot == legacy_snapshot
    payload = _durable_request_payload(GeneratePresentationRequest(content="deck", strategy="svg"))
    payload[INTERNAL_SVG_AUTHORING_PROFILE_KEY] = validate_authoring_profile(profile_id)
    with pytest.raises(ValueError, match="svg_authoring_profile_conflict"):
        _request_from_durable_payload(payload)


@pytest.mark.parametrize("target_profile", ["art-direction-v1@2", "art-direction-v1@3", "art-direction-v1@4", "art-direction-v1@5", "art-direction-v1@6", "art-direction-v1@7"])
def test_frozen_v1_authoring_cannot_be_replayed_as_v2(monkeypatch, target_profile):
    from models.generate_presentation_request import GeneratePresentationRequest
    from services.presentation_generation_staging import _durable_request_payload, _request_from_durable_payload
    from services.svg_authoring_profile import INTERNAL_SVG_AUTHORING_PROFILE_KEY, select_svg_authoring_profile, validate_authoring_profile
    from services.svg_generation_admission import pin_svg_style

    monkeypatch.setenv("CANVAS_BENCH_ENABLED", "true")
    request = GeneratePresentationRequest(content="deck", strategy="svg")
    select_svg_authoring_profile(request, "art-direction-v1")
    payload = _durable_request_payload(request)
    frozen_digest = request._svg_style_snapshot["digest"]
    select_svg_authoring_profile(request, target_profile)
    with pytest.raises(HTTPException) as error:
        pin_svg_style(request)
    assert error.value.detail == "svg_authoring_profile_conflict"
    assert request._svg_style_snapshot["digest"] == frozen_digest
    payload[INTERNAL_SVG_AUTHORING_PROFILE_KEY] = validate_authoring_profile(target_profile)
    monkeypatch.delenv("CANVAS_BENCH_ENABLED")
    with pytest.raises(ValueError, match="svg_authoring_profile_conflict"):
        _request_from_durable_payload(payload)


@pytest.mark.parametrize("profile", ["future-v2", "art-direction-v1@9", {"id": "art-direction-v1", "version": 9}, {"id": "art-direction-v1", "version": True}])
def test_unknown_durable_authoring_profile_fails_closed(profile):
    from services.presentation_generation_staging import _request_from_durable_payload
    from services.svg_authoring_profile import INTERNAL_SVG_AUTHORING_PROFILE_KEY

    with pytest.raises(ValueError, match="svg_authoring_profile_invalid"):
        _request_from_durable_payload({"content": "deck", "strategy": "svg", INTERNAL_SVG_AUTHORING_PROFILE_KEY: profile})


@pytest.mark.parametrize("profile_id", ["art-direction-v1", "art-direction-v1@2", "art-direction-v1@3", "art-direction-v1@4", "art-direction-v1@5", "art-direction-v1@6", "art-direction-v1@7"])
def test_frozen_authoring_profile_replay_checks_digest_and_independent_speed_arm(monkeypatch, profile_id):
    from models.generate_presentation_request import GeneratePresentationRequest
    from services.presentation_generation_staging import _durable_request_payload, _request_from_durable_payload
    from services.svg_authoring_profile import INTERNAL_SVG_AUTHORING_PROFILE_KEY, select_svg_authoring_profile, validate_authoring_profile

    monkeypatch.setenv("CANVAS_BENCH_ENABLED", "true")
    request = GeneratePresentationRequest(content="deck", strategy="svg", bench_speed_arm="brief_off")
    select_svg_authoring_profile(request, profile_id)
    payload = _durable_request_payload(request)
    payload.pop(INTERNAL_SVG_AUTHORING_PROFILE_KEY)
    monkeypatch.delenv("CANVAS_BENCH_ENABLED")
    restored = _request_from_durable_payload(payload)
    assert restored._svg_authoring_profile == validate_authoring_profile(profile_id)
    assert restored._bench_speed_arm == "brief_off"
    payload["_internal_svg_style_snapshot"]["tokens"]["primary"] = "#123456"
    with pytest.raises(ValueError, match="svg_authoring_profile_snapshot_digest_mismatch"):
        _request_from_durable_payload(payload)


@pytest.mark.parametrize("profile_id", ["art-direction-v1", "art-direction-v1@2", "art-direction-v1@3", "art-direction-v1@4", "art-direction-v1@5", "art-direction-v1@6", "art-direction-v1@7"])
def test_public_generation_hash_distinguishes_only_new_authoring_profile(profile_id):
    from models.public_v2_contracts import PresentationGenerationRequestV2
    from services.public_v2_orchestrator import _canonical_hash, _generation_request_hash_payload
    from services.svg_authoring_profile import INTERNAL_SVG_AUTHORING_PROFILE_KEY, validate_authoring_profile

    request = PresentationGenerationRequestV2(content="deck", strategy="svg")
    baseline = _generation_request_hash_payload(request)
    assert _generation_request_hash_payload(request, svg_authoring_profile="current-v4") == baseline
    selected = _generation_request_hash_payload(request, svg_authoring_profile=profile_id)
    assert selected[INTERNAL_SVG_AUTHORING_PROFILE_KEY] == validate_authoring_profile(profile_id)
    assert _canonical_hash(selected) != _canonical_hash(baseline)
    if profile_id in {"art-direction-v1@2", "art-direction-v1@3", "art-direction-v1@4", "art-direction-v1@5", "art-direction-v1@6", "art-direction-v1@7"}:
        assert _canonical_hash(selected) != _canonical_hash(_generation_request_hash_payload(request, svg_authoring_profile="art-direction-v1"))
    if profile_id in {"art-direction-v1@3", "art-direction-v1@4", "art-direction-v1@5", "art-direction-v1@6", "art-direction-v1@7"}:
        assert _canonical_hash(selected) != _canonical_hash(_generation_request_hash_payload(request, svg_authoring_profile="art-direction-v1@2"))
    if profile_id in {"art-direction-v1@4", "art-direction-v1@5", "art-direction-v1@6", "art-direction-v1@7"}:
        assert _canonical_hash(selected) != _canonical_hash(_generation_request_hash_payload(request, svg_authoring_profile="art-direction-v1@3"))
    if profile_id in {"art-direction-v1@5", "art-direction-v1@6", "art-direction-v1@7"}:
        assert _canonical_hash(selected) != _canonical_hash(_generation_request_hash_payload(request, svg_authoring_profile="art-direction-v1@4"))
    if profile_id in {"art-direction-v1@6", "art-direction-v1@7"}:
        assert _canonical_hash(selected) != _canonical_hash(_generation_request_hash_payload(request, svg_authoring_profile="art-direction-v1@5"))
    if profile_id == "art-direction-v1@7":
        assert _canonical_hash(selected) != _canonical_hash(_generation_request_hash_payload(request, svg_authoring_profile="art-direction-v1@6"))
    with pytest.raises(ValueError, match="svg_authoring_profile_invalid"):
        _generation_request_hash_payload(request, svg_authoring_profile="future-v2")


@pytest.mark.parametrize("profile_id", [None, "art-direction-v1", "art-direction-v1@2", "art-direction-v1@3", "art-direction-v1@4", "art-direction-v1@5", "art-direction-v1@6", "art-direction-v1@7"])
def test_public_generation_integrity_replay_keeps_authoring_selection(monkeypatch, profile_id):
    import asyncio
    from unittest.mock import AsyncMock

    from sqlalchemy.exc import IntegrityError
    from models.public_v2_contracts import PresentationGenerationRequestV2
    from services import public_v2_orchestrator
    from services.public_v2_orchestration_types import PublicRequestActor

    orchestrator = object.__new__(public_v2_orchestrator.SqlPublicV2Orchestrator)
    orchestrator._submit_generation_once = AsyncMock(side_effect=IntegrityError("test", {}, Exception()))
    receipt = object()
    orchestrator._replay_generation = AsyncMock(return_value=receipt)
    monkeypatch.setattr(public_v2_orchestrator, "wake_workspace_operation_runner", lambda: None)
    actor = PublicRequestActor(owner_id=uuid.uuid4(), tenant_id="test", correlation_id="profile", scopes=frozenset())
    request = PresentationGenerationRequestV2(content="deck", strategy="svg")
    kwargs = dict(actor=actor, idempotency_key="profile-test", request=request)
    if profile_id is not None:
        kwargs["svg_authoring_profile"] = profile_id
    assert asyncio.run(orchestrator.submit_generation(**kwargs)) is receipt
    orchestrator._submit_generation_once.assert_awaited_once_with(**kwargs)
    orchestrator._replay_generation.assert_awaited_once_with(**kwargs)


@pytest.mark.parametrize("conflict", [None, "profile", "snapshot"])
def test_prepare_uses_fenced_current_job_snapshot_before_live_adaptation(conflict, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, Mock

    from services.svg_presentation_prepare import _pin_owned_svg_style

    frozen = {"digest": "legacy-frozen", "tokens": {"primary": "#123456"}}
    stale_job = SimpleNamespace(id=uuid.uuid4(), attempt_token="attempt", _workspace_fencing_token=9,
                                svg_style_snapshot={"digest": "stale-job-object"})
    current = SimpleNamespace(svg_style_snapshot=frozen)
    request = _svg_request(_svg_style_snapshot=None)
    if conflict == "profile":
        request._svg_authoring_profile = "art-direction-v1"
    elif conflict == "snapshot":
        request._svg_style_snapshot = {"digest": "conflicting-staged"}
    checkpoint_session = AsyncMock()
    checkpoint_session.add = Mock()
    session_maker = Mock(return_value=checkpoint_session)
    checkpoint_session.__aenter__.return_value = checkpoint_session
    owned_job = AsyncMock(return_value=current)
    pin_style = Mock(side_effect=lambda selected: selected._svg_style_snapshot)
    operation = _pin_owned_svg_style(request, stale_job, pin_style=pin_style,
                                    session_maker=session_maker, owned_job=owned_job)
    if conflict is not None:
        with pytest.raises((ValueError, RuntimeError), match="svg_authoring_profile_conflict|svg_style_snapshot_conflict"):
            asyncio.run(operation)
        pin_style.assert_not_called()
        checkpoint_session.commit.assert_not_awaited()
    else:
        assert asyncio.run(operation) == frozen
        assert request._svg_style_snapshot is frozen
        pin_style.assert_called_once_with(request)
        checkpoint_session.commit.assert_awaited_once()
    owned_job.assert_awaited_once_with(checkpoint_session, stale_job.id, "attempt", fencing_token=9)
    if conflict is None:
        from copy import deepcopy
        import json
        from xml.etree import ElementTree as ET
        from services import svg_partial_recovery as recovery
        from services.svg_authoring_profile import ART_DIRECTION_PROFILE_V8, read_quality_recovery_policy
        from services.svg_generation_admission import pin_svg_style as official_pin_svg_style
        from services import artifact_store, svg_asset_persistence
        from models.generate_presentation_request import GeneratePresentationRequest
        from utils.hashing import sha256_hex
        owner, operation_id, job_id, presentation_id = (uuid.uuid4() for _ in range(4))
        tenant = "fixture-tenant"
        monkeypatch.setattr("utils.owner_context.get_current_owner_id", lambda: owner)
        monkeypatch.setattr("utils.owner_context.get_current_tenant_id", lambda: tenant)
        source_request = GeneratePresentationRequest(content="Fixture message", strategy="svg",
                                                     n_slides=1, language="en")
        snapshot = official_pin_svg_style(source_request)
        assert read_quality_recovery_policy(snapshot) == {"id": "svg-quality-first", "version": 2}
        immutable_request = source_request.model_dump(mode="json")
        parent = SimpleNamespace(id=operation_id, owner_id=owner, error_code="svg_source_quality_failed",
            request_payload={"generation_request": immutable_request})
        source_job = SimpleNamespace(id=job_id, workspace_operation_id=operation_id,
            request_payload=immutable_request, svg_style_snapshot=snapshot, total_slides=1,
            authored_outline={"slides": [{"content": "Fixture message"}]})
        asset = SimpleNamespace(digest="sha256:" + "a" * 64, offered_pages=[], slots=[],
                                presentation_id=presentation_id, source_revision=1)
        source_pointer = {"ref": str(uuid.uuid4()), "sha256": "a" * 64}
        service = SimpleNamespace(resolve_ref=AsyncMock(return_value=SimpleNamespace(created_by_operation_id=operation_id)))
        monkeypatch.setattr(artifact_store, "configured_public_artifact_service", lambda: service)
        monkeypatch.setattr(svg_asset_persistence, "load_source_manifest_pointer", AsyncMock(return_value=asset))
        input_digest = recovery.digest(dict(content="Fixture message", style=snapshot, index=0, count=1,
            instructions=None, language="en", preset=None))
        input_digest = recovery.digest({"page": input_digest, "assets": {}})
        raw = '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720"><text x="80" y="100" font-family="Noto Sans KR" font-size="32" font-weight="400" fill="#123456" text-anchor="start">Fixture message</text></svg>'
        original = dict(svg_content=None, input_digest=input_digest, inflight=None,
            source_manifest_ref=source_pointer, source_manifest_digest=asset.digest,
            initial_quality={"passed": False, "failures": [{"code": "required_text_missing"}]})
        normalized, first_svg, author_digest, _, _, advisories = recovery._replay_sealed_author_transform(
            original, raw, request=source_request, job=source_job, asset=asset, index=0)
        author_receipt = dict(contract_version="svg-static/v2", provider="fixture", requested_model="fixture-model",
            style_digest=snapshot["digest"], authoring_profile=dict(ART_DIRECTION_PROFILE_V8),
            raw_digest="sha256:" + sha256_hex(raw.encode()), source_digest="sha256:" + author_digest,
            validation_error=None, native_declarations=None, vector_icons_used=[], attempt_ordinal=1)
        metrics = {"owned_text": [{"text": "Fixture message"}]}
        measured = dict(passed=False, measurement_complete=True,
            failures=[{"code": "required_text_missing"}], source_digest="sha256:" + sha256_hex(first_svg.encode()),
            browser_metrics=metrics, metrics_digest="sha256:" + sha256_hex(json.dumps(metrics,
                sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()),
            png_digest="sha256:" + "b" * 64, wrapper_digest="sha256:" + "c" * 64)
        original.update(source_digest=author_digest, last_valid_svg="historical-invalid-layout",
            fontAdvisories=advisories, attempts=[dict(kind="provider", raw_response=raw, receipt=author_receipt,
                                                    quality=deepcopy(measured))], initial_quality=deepcopy(measured))
        terminal = dict(schema="svg-provider-terminal/v1", terminal_event="response.completed",
            operation_id=str(operation_id), job_id=str(job_id), owner_id=str(owner), tenant_id=tenant,
            input_digest=input_digest, raw_response=raw, output_digest=sha256_hex(raw.encode()), usage={})
        terminal["receipt_digest"] = recovery.digest(terminal)
        original["provider_terminal_responses"] = {"author/1": terminal}
        row = SimpleNamespace(slide_index=0, author_status="running", render_status="pending",
                              persist_status="pending", svg_checkpoint=original)
        calls = [SimpleNamespace(slide_index=0, stage="author", outcome="succeeded", state="reported",
            reconciled_at=object(), provider_id="fixture", model_id="fixture-model",
            model_dump=lambda **kwargs: {"stage": "author", "slide_index": 0, "outcome": "succeeded"})]
        kwargs = dict(job=source_job, operation=parent, episode_calls=calls, completed=terminal,
            session=object(), request=source_request, binding=None, vectors={}, vector_seed_witness=None)
        before = deepcopy(original)
        projected, witness = asyncio.run(recovery._completed_source_author_projection(original, row, **kwargs))
        assert original == before and projected["attempts"] == original["attempts"]
        assert projected["svg_content"] == normalized and witness["phase"] == "author-only"
        assert "quality" not in projected and "initial_quality" not in projected and "png_sha256" not in projected
        page = dict(slide_index=0, category="known", row_digest="fixture-row", phase="author-only",
            svg_sha256=witness["render_svg_sha256"], source_svg_sha256=witness["source_svg_sha256"],
            final_render_svg_sha256=witness["render_svg_sha256"], input_digest=input_digest,
            author_receipt_digest=recovery.author_witness(projected))
        manifest = {"pages": [page]}
        plan = SimpleNamespace(manifest=manifest, manifest_hash=recovery.digest(manifest),
                               baseline_payloads={0: projected})
        projected_before_stale_case = deepcopy(projected)
        plan.baseline_payloads[0] = deepcopy(plan.baseline_payloads[0])
        plan.archive = {"job": {"svg_style_snapshot": deepcopy(snapshot)}}
        plan.baseline_payloads[0]["final_selected_render_evidence"] = {"schemaVersion": "fixture-stale-final-review"}
        stale_source_before = deepcopy(plan.baseline_payloads[0])
        child = recovery.cached_page_payload(row, plan, uuid.uuid4())
        assert "final_selected_render_evidence" not in child
        assert plan.baseline_payloads[0] == stale_source_before
        assert projected == projected_before_stale_case
        assert not child.get("quality") and not child.get("png_sha256")
        invalid_final_child = deepcopy(child)
        invalid_final_child["final_selected_render_evidence"] = deepcopy(stale_source_before["final_selected_render_evidence"])
        with pytest.raises(ValueError, match="svg_final_selected_evidence_unproven"):
            recovery.verify_cached_page(invalid_final_child, plan.manifest, 0, input_digest)
        assert recovery.verify_cached_page(child, manifest, 0, input_digest) is page
        assert "quality" not in child and "png_sha256" not in child and original == before
        bad_latest = deepcopy(original)
        bad_raw = raw.replace('<text ', '<text xml:space="preserve" ')
        bad_author_digest = sha256_hex(ET.tostring(ET.fromstring(bad_raw), encoding="unicode").encode())
        bad_receipt = {**deepcopy(author_receipt), "attempt_ordinal": 2,
            "raw_digest": "sha256:" + sha256_hex(bad_raw.encode()), "source_digest": "sha256:" + bad_author_digest}
        bad_terminal = {**deepcopy(terminal), "raw_response": bad_raw, "output_digest": sha256_hex(bad_raw.encode())}
        bad_terminal["receipt_digest"] = recovery.digest({key: value for key, value in bad_terminal.items() if key != "receipt_digest"})
        bad_latest["attempts"].append(dict(kind="provider", raw_response=bad_raw, receipt=bad_receipt))
        bad_latest["provider_terminal_responses"]["author/2"] = bad_terminal
        bad_latest.update(source_digest=bad_author_digest, last_valid_svg=first_svg)
        bad_before = deepcopy(bad_latest)
        fallback, selected_witness = asyncio.run(recovery._completed_source_author_projection(
            bad_latest, row, **{**kwargs, "completed": bad_terminal}))
        assert bad_latest == bad_before and fallback["attempts"][:-1] == bad_latest["attempts"]
        assert fallback["svg_content"] == normalized and selected_witness["phase"] == "author-only"
        assert selected_witness["completed_receipt_digest"] == terminal["receipt_digest"]
        replay = fallback["attempts"][-1]
        assert replay["kind"] == "sealed_terminal_replay" and replay["raw_response"] == raw
        assert replay["receipt"]["terminal_recovery_witness"]["attempt_ordinal"] == 1
        assert "quality" not in fallback and "png_sha256" not in fallback
        fallback_page = {**page, "author_receipt_digest": recovery.author_witness(fallback)}
        fallback_manifest = {"pages": [fallback_page]}
        fallback_plan = SimpleNamespace(manifest=fallback_manifest, manifest_hash=recovery.digest(fallback_manifest),
                                        baseline_payloads={0: fallback})
        fallback_child = recovery.cached_page_payload(row, fallback_plan, uuid.uuid4())
        assert recovery.verify_cached_page(fallback_child, fallback_manifest, 0, input_digest) is fallback_page
        unproven = deepcopy(bad_latest)
        unproven["attempts"][0]["quality"]["source_digest"] = "sha256:" + "0" * 64
        with pytest.raises(recovery.PartialRecoveryRejected, match="svg_partial_recovery_local_output_requires_repair"):
            asyncio.run(recovery._completed_source_author_projection(unproven, row,
                **{**kwargs, "completed": bad_terminal}))
        all_bad = deepcopy(bad_latest)
        all_bad["attempts"] = [all_bad["attempts"][-1]]
        with pytest.raises(recovery.PartialRecoveryRejected, match="svg_partial_recovery_local_output_requires_repair"):
            asyncio.run(recovery._completed_source_author_projection(all_bad, row,
                **{**kwargs, "completed": bad_terminal}))
        for violation in ("unknown", "body", "receipt", "input", "owner"):
            candidate = deepcopy(original)
            if violation == "unknown":
                candidate["provider_failures"] = [{"code": "public_provider_result_unknown"}]
            elif violation == "body":
                candidate["attempts"][-1]["receipt"]["raw_digest"] = "sha256:" + "0" * 64
            elif violation == "receipt":
                candidate["attempts"][-1]["receipt"]["validation_error"] = "static-validation-failed"
            elif violation == "input":
                candidate["input_digest"] = "unbound-input"
            else:
                monkeypatch.setattr("utils.owner_context.get_current_owner_id", lambda: uuid.uuid4())
            with pytest.raises(recovery.PartialRecoveryRejected):
                asyncio.run(recovery._completed_source_author_projection(candidate, row, **kwargs))
            monkeypatch.setattr("utils.owner_context.get_current_owner_id", lambda: owner)
        legacy_request = GeneratePresentationRequest(content="Fixture message", strategy="svg",
                                                     n_slides=1, language="en")
        legacy_request._svg_quality_recovery_policy = {"id": "svg-quality-first", "version": 1}
        source_job.svg_style_snapshot = official_pin_svg_style(legacy_request)
        legacy_cache_plan = deepcopy(plan)
        legacy_cache_plan.archive = {"job": {"svg_style_snapshot": deepcopy(source_job.svg_style_snapshot)}}
        legacy_cached_child = recovery.cached_page_payload(row, legacy_cache_plan, uuid.uuid4())
        assert legacy_cached_child["final_selected_render_evidence"] == stale_source_before["final_selected_render_evidence"]
        with pytest.raises(ValueError, match="svg_final_selected_evidence_unproven"):
            recovery.verify_cached_page(legacy_cached_child, legacy_cache_plan.manifest, 0, input_digest)
        from utils.hashing import canonical_json_digest
        from services.svg_authoring_profile import read_authoring_profile
        no_policy_snapshot = deepcopy(snapshot)
        no_policy_snapshot.pop("quality_recovery_policy", None)
        no_policy_snapshot.pop("quality_recovery_plan", None)
        no_policy_snapshot["digest"] = canonical_json_digest({k: v for k, v in no_policy_snapshot.items() if k != "digest"}, allow_nan=True)
        assert read_authoring_profile(no_policy_snapshot) == ART_DIRECTION_PROFILE_V8
        assert read_quality_recovery_policy(no_policy_snapshot) is None
        no_policy_cache_plan = deepcopy(plan)
        no_policy_cache_plan.archive = {"job": {"svg_style_snapshot": no_policy_snapshot}}
        no_policy_cached_child = recovery.cached_page_payload(row, no_policy_cache_plan, uuid.uuid4())
        assert no_policy_cached_child["final_selected_render_evidence"] == stale_source_before["final_selected_render_evidence"]
        assert no_policy_cache_plan.baseline_payloads[0] == stale_source_before
        with pytest.raises(ValueError, match="svg_final_selected_evidence_unproven"):
            recovery.verify_cached_page(no_policy_cached_child, no_policy_cache_plan.manifest, 0, input_digest)
        assert read_quality_recovery_policy(source_job.svg_style_snapshot) == {"id": "svg-quality-first", "version": 1}
        assert asyncio.run(recovery._completed_source_author_projection(original, row, **kwargs)) is None
        assert original == before
        from services.generation_core.drivers.svg import partial_quality_recovery, quality_recovery
        from services.generation_core.quality.advisory_record import advisory_record, content_digest
        source_job.svg_style_snapshot = snapshot
        source_job.presentation_id = presentation_id
        prior = {**deepcopy(projected), "svg_content": first_svg, "last_valid_svg": first_svg}
        source_hash = sha256_hex(first_svg.encode())
        source_page = dict(category="known", phase="finalized", svg_sha256=source_hash,
            input_digest=input_digest, author_receipt_digest=recovery.author_witness(prior), row_digest="original-source-row")
        source_manifest = {"known_indices": [0], "pages": [source_page]}
        prior["partial_recovery"] = dict(schema="svg-partial-recovery-page/v1", operation_id=str(operation_id),
            manifest_hash=recovery.digest(source_manifest), slide_index=0, source_row_digest=source_page["row_digest"], category="known")
        target = dict(axis="hierarchy_emphasis", evidenceRegion="Topic and claim",
            visibleEvidence="Generic topic dominates the source claim.", sourceEvidence="Source states the primary claim.",
            improvementGoal="Give the primary source claim visual priority.")
        runtime = "fixture-quality-runtime"
        gate_body = dict(schemaVersion="svg-quality-first-design-gate/v1", policy=snapshot["quality_recovery_policy"],
            snapshotDigest=content_digest(snapshot), runtimeBinding=runtime, pageCoverageComplete=True,
            pageDigests=[source_hash], pngDigests=["b" * 64],
            binding=dict(jobId=str(job_id), operationId=str(operation_id), presentationId=str(presentation_id),
                         ownerId=str(owner), tenantId=tenant),
            pages=[dict(index=0, evaluated=True, failedAxes=[],
                axes={"hierarchy_emphasis": {"verdict": "passed", "repairScope": "none"}}, designTargets=[target])])
        gate = {**gate_body, "digest": content_digest(gate_body)}
        current_review = advisory_record({"designGate": gate, "slides": []}, [source_hash])
        decision = dict(repair_scope="design_rewrite", target_axes=["hierarchy_emphasis"], issues=[], design_targets=[target])
        candidate = deepcopy(prior)
        candidate.update(svg_content=None, correction_mode="design_rewrite", quality_recovery=dict(status="repair_pending", decision=decision,
            prior_candidate={"svg_digest": source_hash}))
        repair_ctx = SimpleNamespace(snapshot=snapshot, count=1, partial_recovery_manifest=source_manifest,
            quality_recovery_runtime_binding=runtime, quality_recovery_input_binding=runtime)
        seed = {"quality_recovery_execution": {"binding": runtime,
            "rounds": [{"status": "completed", "advisory": current_review}]}}
        async def current_checkpoint(*args):
            return deepcopy(seed)
        monkeypatch.setattr(quality_recovery, "_restore_candidate", lambda *args: deepcopy(prior))
        assert asyncio.run(partial_quality_recovery.current_quality_repair_authorized(
            repair_ctx, candidate, 0, source_job, current_checkpoint)) is True
        assert partial_quality_recovery.current_episode_attempt_count(repair_ctx, candidate, 0) == 0
        from services.generation_core.quality.vision_advisory_prompt import DESIGN_AXES
        retained_selection = {"selected": "prior", "reason": "comparison_improvement_unjudged"}
        compared_gate = deepcopy(gate)
        compared_gate["pages"][0]["mandatoryEvidenceValid"] = True
        compared_gate["pageDigests"] = ["sha256:" + "c" * 64]
        compared_gate["pngDigests"] = ["sha256:" + "d" * 64]
        paired_body = {
            "index": 0, "evaluated": True, "priorPageDigest": gate["pageDigests"][0],
            "priorPngDigest": gate["pngDigests"][0], "candidatePageDigest": compared_gate["pageDigests"][0],
            "candidatePngDigest": compared_gate["pngDigests"][0], "targetAxes": decision["target_axes"],
            "targetGoals": deepcopy(decision["design_targets"]), "priorInputDigest": input_digest,
            "priorMandatoryEvidenceValid": True, "comparisonMode": "joint-targets-current-source-context/v1",
            "comparisonCohortDigest": "fixture-current-comparison-cohort", "deckContextDigest": compared_gate.get("deckContextDigest"),
            "priorAxes": {axis: {"verdict": "passed", "repairScope": "none"} for axis in DESIGN_AXES},
            "axisChanges": {axis: {"change": "unjudged" if axis == "information_structure" else "improved",
                                      "visibleEvidence": "Synthetic comparison evidence."} for axis in DESIGN_AXES},
            "blindedPresentation": {"candidateLabel": "B", "priorLabel": "A", "displayOrder": ["A", "B"],
                                    "chronologyExposed": False, "versionExposed": False},
        }
        compared_gate["candidateComparisons"] = [{**paired_body, "digest": content_digest(paired_body)}]
        compared_gate["digest"] = content_digest({k: v for k, v in compared_gate.items() if k != "digest"})
        assert recovery._v2_retained_comparison_matches(retained_selection, decision, gate, compared_gate, 0, input_digest)
        assert not recovery._v2_retained_comparison_matches({"selected": "prior", "reason": "target_goal_not_improved"}, decision, gate, compared_gate, 0, input_digest)
        for violation in ("source", "png", "goal", "blind", "unknown", "unmeasured", "missing_prior_axes", "invalid_prior_axes"):
            altered = deepcopy(compared_gate)
            proof = altered["candidateComparisons"][0]
            if violation == "source":
                proof["priorPageDigest"] = "sha256:" + "0" * 64
            elif violation == "png":
                proof["candidatePngDigest"] = "sha256:" + "0" * 64
            elif violation == "goal":
                proof["targetGoals"][0]["improvementGoal"] = "Different unauthorized goal."
            elif violation == "blind":
                proof["blindedPresentation"]["versionExposed"] = True
            elif violation == "missing_prior_axes":
                proof.pop("priorAxes")
            elif violation == "invalid_prior_axes":
                proof["priorAxes"] = []
            elif violation == "unmeasured":
                altered["pages"][0]["mandatoryEvidenceValid"] = False
            else:
                altered["pages"][0]["evaluated"] = False
            proof["digest"] = content_digest({k: v for k, v in proof.items() if k != "digest"})
            altered["digest"] = content_digest({k: v for k, v in altered.items() if k != "digest"})
            assert not recovery._v2_retained_comparison_matches(retained_selection, decision, gate, altered, 0, input_digest)
        assert not recovery._v2_retained_comparison_matches(retained_selection, decision, gate, compared_gate, 0, "different-input")
        not_improved = deepcopy(compared_gate)
        proof = not_improved["candidateComparisons"][0]
        for axis_change in proof["axisChanges"].values():
            axis_change["change"] = "equivalent"
        proof["digest"] = content_digest({k: v for k, v in proof.items() if k != "digest"})
        not_improved["digest"] = content_digest({k: v for k, v in not_improved.items() if k != "digest"})
        assert recovery._v2_retained_comparison_matches({"selected": "prior", "reason": "target_goal_not_improved"}, decision, gate, not_improved, 0, input_digest)
        assert not recovery._v2_retained_comparison_matches(retained_selection, decision, gate, not_improved, 0, input_digest)
        purchased = deepcopy(candidate)
        purchased["attempts"].append({"kind": "provider", "raw_response": "new-candidate", "receipt": {}})
        assert partial_quality_recovery.current_episode_attempt_count(repair_ctx, purchased, 0) == 1
        # Initial cached bytes remain strict; only this exact current repair is authorized.
        with pytest.raises(recovery.PartialRecoveryRejected, match="cached_source_changed"):
            recovery.verify_cached_page(candidate, source_manifest, 0, input_digest)
        for mismatch in ("decision", "scope", "archive", "input", "operation", "history", "review"):
            invalid = deepcopy(candidate)
            before_seed = deepcopy(seed)
            if mismatch == "decision":
                invalid["quality_recovery"]["decision"]["design_targets"][0]["improvementGoal"] = "Unapproved different goal."
            elif mismatch == "scope":
                invalid["correction_mode"] = "local_repair"
            elif mismatch == "archive":
                invalid["quality_recovery"]["prior_candidate"]["svg_digest"] = "0" * 64
            elif mismatch == "input":
                invalid["input_digest"] = "changed-input"
            elif mismatch == "operation":
                invalid["partial_recovery"]["operation_id"] = str(uuid.uuid4())
            elif mismatch == "history":
                invalid["attempts"][0]["raw_response"] = "changed-original-history"
            else:
                seed["quality_recovery_execution"]["rounds"][0]["status"] = "review_unknown"
            if mismatch == "history":
                with pytest.raises(ValueError, match="cached_source_changed"):
                    asyncio.run(partial_quality_recovery.current_quality_repair_authorized(
                        repair_ctx, invalid, 0, source_job, current_checkpoint))
            else:
                assert asyncio.run(partial_quality_recovery.current_quality_repair_authorized(
                    repair_ctx, invalid, 0, source_job, current_checkpoint)) is False
            seed.clear()
            seed.update(before_seed)
        legacy_gate = {**deepcopy(gate_body), "policy": {"id": "svg-quality-first", "version": 1},
                       "snapshotDigest": content_digest(source_job.svg_style_snapshot)}
        # A fully sealed v1 review with only design targets grants no new repair authority.
        source_job.svg_style_snapshot = official_pin_svg_style(legacy_request)
        repair_ctx.snapshot = source_job.svg_style_snapshot
        legacy_gate["snapshotDigest"] = content_digest(repair_ctx.snapshot)
        legacy_gate["digest"] = content_digest(legacy_gate)
        seed["quality_recovery_execution"]["rounds"][0]["advisory"] = advisory_record({"designGate": legacy_gate, "slides": []}, [source_hash])
        assert not recovery._v2_retained_comparison_matches(retained_selection, decision, legacy_gate, compared_gate, 0, input_digest)
        assert asyncio.run(partial_quality_recovery.current_quality_repair_authorized(
            repair_ctx, candidate, 0, source_job, current_checkpoint)) is False
        assert original == before
