from __future__ import annotations

from copy import deepcopy
import asyncio
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException

from models.authored_vnext.deck import MasterDigestPin
from models.authored_vnext.master.contract import DeckMasterContract
from models.authored_vnext.master.operations import MasterRevisionResponse
from models.generate_presentation_request import GeneratePresentationRequest
from services.authored_vnext.generation.errors import StaleMasterError
from services.authored_vnext.generation.legacy_bridge import (
    _prepare_body_regeneration,
)
from services.authored_vnext.generation.legacy_pin import (
    canonical_legacy_master_pin_identity,
    resolve_legacy_master_pin,
)
from services.authored_vnext.master.digests import seal_master_contract
from tests.support.authored_vnext_master_fixture import master_contract_payload


def _request(master: MasterRevisionResponse) -> GeneratePresentationRequest:
    return GeneratePresentationRequest.model_validate(
        {
            "content": "Pinned deck",
            "strategy": "authored",
            "masterPin": {
                "masterId": master.master_id,
                "revision": master.revision,
                "digest": master.digest,
            },
        }
    )


class _Repository:
    def __init__(self, revision: MasterRevisionResponse, head: MasterRevisionResponse):
        self.revision = revision
        self.head = head

    def get_master(self, master_id: str, revision: int | None = None):
        assert master_id == self.revision.master_id
        assert revision == self.revision.revision
        return self.revision

    def get_master_head(self, master_id: str):
        assert master_id == self.head.master_id
        return self.head


def _published(revision: int = 1) -> MasterRevisionResponse:
    payload = deepcopy(master_contract_payload())
    payload["masterId"] = "master:legacy-pin-test"
    contract = seal_master_contract(DeckMasterContract.model_validate(payload))
    return MasterRevisionResponse(
        master_id=contract.master_id,
        revision=revision,
        digest=contract.digest,
        status="published",
        contract=contract,
    )


def test_existing_generation_request_resolves_server_owned_pin_digests(monkeypatch):
    published = _published()
    request = _request(published)
    monkeypatch.setattr(
        "services.authored_vnext.generation.legacy_pin.generation_repository_for_owner",
        lambda _owner_id: _Repository(published, published),
    )

    resolved = resolve_legacy_master_pin(
        owner_id=uuid.uuid4(), requested=request.master_pin
    )

    assert request.master_pin.master_id == published.master_id
    assert resolved.pin.master_digest == published.digest
    assert resolved.pin.layout_registry_digest.startswith("sha256:")
    assert resolved.pin.font_manifest_digest == published.contract.font_manifest.digest


def test_existing_generation_request_rejects_non_head_revision(monkeypatch):
    requested = _published(1)
    head = requested.model_copy(update={"revision": 2})
    request = _request(requested)
    monkeypatch.setattr(
        "services.authored_vnext.generation.legacy_pin.generation_repository_for_owner",
        lambda _owner_id: _Repository(requested, head),
    )

    with pytest.raises(StaleMasterError):
        resolve_legacy_master_pin(owner_id=uuid.uuid4(), requested=request.master_pin)


def test_persisted_keep_checkpoint_accepts_non_head_revision(monkeypatch):
    requested = _published(1)
    head = requested.model_copy(update={"revision": 2})
    request = _request(requested)
    monkeypatch.setattr(
        "services.authored_vnext.generation.legacy_pin.generation_repository_for_owner",
        lambda _owner_id: _Repository(requested, head),
    )

    resolved = resolve_legacy_master_pin(
        owner_id=uuid.uuid4(),
        requested=request.master_pin,
        require_current_head=False,
    )

    assert resolved.pin.master_revision == 1
    assert resolved.pin.master_digest == requested.digest


def test_durable_pin_identity_requires_and_normalizes_all_five_fields():
    digest = f"sha256:{'a' * 64}"
    layout_digest = f"sha256:{'b' * 64}"
    font_digest = f"sha256:{'c' * 64}"

    assert canonical_legacy_master_pin_identity(
        {
            "master_id": "master:company",
            "revision": 7,
            "digest": digest,
            "layout_registry_digest": layout_digest,
            "font_manifest_digest": font_digest,
        }
    ) == (
        "master:company",
        7,
        digest,
        layout_digest,
        font_digest,
    )
    assert (
        canonical_legacy_master_pin_identity(
            {
                "masterId": "master:company",
                "masterRevision": 7,
                "masterDigest": digest,
            }
        )
        is None
    )


def test_body_regeneration_rejects_projection_pin_drift_before_mutation():
    pin = MasterDigestPin(
        master_id="master:company",
        master_revision=7,
        master_digest=f"sha256:{'a' * 64}",
        layout_registry_digest=f"sha256:{'b' * 64}",
        font_manifest_digest=f"sha256:{'c' * 64}",
    )
    deck = SimpleNamespace(
        revision=3,
        digest=f"sha256:{'d' * 64}",
        master=pin,
        slides=[SimpleNamespace()],
    )
    repository = SimpleNamespace(get_deck=lambda _deck_id: deck)

    with pytest.raises(RuntimeError, match="authored_vnext_regenerate_master_changed"):
        _prepare_body_regeneration(
            repository=repository,
            operation_id="operation:test",
            idempotency_key="test",
            deck_id="deck:test",
            expected_deck_revision=3,
            expected_deck_digest=deck.digest,
            expected_master_pin=(
                pin.master_id,
                pin.master_revision,
                pin.master_digest,
                pin.layout_registry_digest,
                f"sha256:{'e' * 64}",
            ),
            slide_index=0,
            layout_id="layout:standard",
            body="Updated body",
        )


def test_master_flag_preserves_template_only_authored_admission(monkeypatch):
    from api.v1.ppt.endpoints.presentation_generate import (
        _validate_authored_master_pin_admission,
    )

    monkeypatch.setenv("AUTHORED_VNEXT_MASTER_ENABLED", "true")
    request = GeneratePresentationRequest(
        content="Pinned deck",
        strategy="authored",
        template="authored",
    )

    asyncio.run(_validate_authored_master_pin_admission(request))

    assert request._authored_master_resolution is None


def test_master_flag_off_preserves_legacy_authored_admission(monkeypatch):
    from api.v1.ppt.endpoints.presentation_generate import (
        _validate_authored_master_pin_admission,
    )

    monkeypatch.delenv("AUTHORED_VNEXT_MASTER_ENABLED", raising=False)
    request = GeneratePresentationRequest(
        content="Legacy authored deck",
        strategy="authored",
        template="authored",
    )

    asyncio.run(_validate_authored_master_pin_admission(request))

    assert request._authored_master_resolution is None


def test_master_pin_is_rejected_for_non_authored_generation(monkeypatch):
    from api.v1.ppt.endpoints.presentation_generate import (
        _validate_authored_master_pin_admission,
    )

    monkeypatch.setenv("AUTHORED_VNEXT_MASTER_ENABLED", "true")
    request = _request(_published()).model_copy(
        update={"strategy": "adaptive", "template": "adaptive"}
    )

    with pytest.raises(HTTPException) as raised:
        asyncio.run(_validate_authored_master_pin_admission(request))

    assert raised.value.status_code == 400
    assert raised.value.detail == "generation_strategy_conflict"


def test_legacy_bridge_and_http_boundaries_import():
    from api.v1.ppt.endpoints.authored_vnext.master import router as master_router
    from services.authored_vnext.generation import legacy_bridge
    from services import workspace_page_remake_operation_handler
    from services import workspace_quality_export_operation_handlers

    assert legacy_bridge.generate_legacy_master_owned_deck
    assert master_router.apply_master_to_presentation
    assert workspace_page_remake_operation_handler.WorkspacePageRemakeOperationHandler
    assert workspace_quality_export_operation_handlers.WorkspaceExportOperationHandler
