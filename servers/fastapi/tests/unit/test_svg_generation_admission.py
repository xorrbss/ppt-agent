import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from models.authored_vnext.deck import MasterDigestPin
from models.authored_vnext.master.contract import DeckMasterContract
from models.generate_presentation_request import AuthoredMasterPinInput
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


@pytest.mark.parametrize("profile", ["future-v2", "art-direction-v1@8", {"id": "art-direction-v1", "version": 8}, {"id": "art-direction-v1", "version": True}])
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
def test_prepare_uses_fenced_current_job_snapshot_before_live_adaptation(conflict):
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
