"""Focused domain tests for DeckMasterContract and immutable publication."""

from __future__ import annotations

from copy import deepcopy
import inspect

from pydantic import ValidationError
import pytest

from models.authored_vnext.master.contract import DeckMasterContract, VisualDirection
from models.authored_vnext.master.slide import SlideDraft
from models.authored_vnext.master.operations import (
    CreateDraftRequest,
    MasterCasRequest,
    PreviewRequest,
)
from services.authored_vnext.master.compiler_port import (
    OoxmlCompileArtifact,
    OoxmlMasterCompilerPort,
)
from services.authored_vnext.master.digests import seal_master_contract
from services.authored_vnext.master.errors import MasterRevisionConflictError
from services.authored_vnext.master.file_repository import (
    FileAuthoredVNextMasterRepository,
)
from services.authored_vnext.master.service import AuthoredVNextMasterService
from services.authored_vnext.master.validation import validate_master
from tests.support.authored_vnext_master_fixture import (
    ZERO_DIGEST,
    long_title_slide_payload,
    master_contract_payload,
)


def test_contract_is_strict_nfc_and_digest_is_server_recomputed() -> None:
    first_payload = master_contract_payload()
    first_payload["name"] = "Cafe\u0301 Master"
    first = seal_master_contract(DeckMasterContract.model_validate(first_payload))

    second_payload = master_contract_payload()
    second_payload["name"] = "Café Master"
    second = seal_master_contract(DeckMasterContract.model_validate(second_payload))

    assert first.name == "Café Master"
    assert first.digest == second.digest
    assert seal_master_contract(first).digest == first.digest
    assert first.digest != ZERO_DIGEST
    assert first.font_manifest.digest != ZERO_DIGEST

    bad = master_contract_payload()
    bad["canvas"]["widthPx"] = "1280"
    with pytest.raises(ValidationError):
        DeckMasterContract.model_validate(bad)

    extra = master_contract_payload()
    extra["visualDirection"] = {"furniture": []}
    with pytest.raises(ValidationError):
        DeckMasterContract.model_validate(extra)

    with pytest.raises(ValidationError):
        VisualDirection.model_validate({"tone": [], "furniture": []})

    slide_with_furniture = long_title_slide_payload()
    slide_with_furniture["furniture"] = [{"id": "furniture:footer"}]
    with pytest.raises(ValidationError):
        SlideDraft.model_validate(slide_with_furniture)


def test_contract_rejects_ownership_namespace_and_role_drift() -> None:
    writable_furniture = master_contract_payload()
    writable_furniture["furniture"]["footer"]["ownership"] = {
        "locked": False,
        "modelWritable": True,
    }
    with pytest.raises(ValidationError, match="furniture must be locked"):
        DeckMasterContract.model_validate(writable_furniture)

    wrong_namespace = master_contract_payload()
    wrong_namespace["layouts"]["standard"]["slots"][0]["id"] = "furniture:title"
    with pytest.raises(ValidationError):
        DeckMasterContract.model_validate(wrong_namespace)

    duplicate_placeholder = master_contract_payload()
    duplicate_placeholder["layouts"]["longTitle"]["slots"][1][
        "placeholderIndex"
    ] = 0
    with pytest.raises(ValidationError, match="placeholderIndex"):
        DeckMasterContract.model_validate(duplicate_placeholder)

    wrong_role = master_contract_payload()
    wrong_role["furniture"]["logo"]["kind"] = "rect"
    with pytest.raises(ValidationError, match="role and kind"):
        DeckMasterContract.model_validate(wrong_role)


def test_contract_requires_four_distinct_semantic_layouts() -> None:
    for public_name in ("cover", "section", "standard", "longTitle"):
        missing = master_contract_payload()
        del missing["layouts"][public_name]
        with pytest.raises(ValidationError):
            DeckMasterContract.model_validate(missing)

    wrong_kind = master_contract_payload()
    wrong_kind["layouts"]["cover"]["kind"] = "standard"
    with pytest.raises(ValidationError, match="registry key"):
        DeckMasterContract.model_validate(wrong_kind)


def test_validation_fails_closed_on_geometry_overlap_and_stress_contract() -> None:
    valid = seal_master_contract(
        DeckMasterContract.model_validate(master_contract_payload())
    )
    summary = validate_master(valid)
    assert summary.valid is True
    assert summary.furniture_body_duplicates == 0
    assert summary.writable_overlaps == 0
    assert summary.title_fit_failures == 0

    overlap_payload = master_contract_payload()
    overlap_payload["furniture"]["divider"]["bounds"] = {
        "x": 100.0,
        "y": 420.0,
        "width": 1000.0,
        "height": 2.0,
    }
    overlapping = seal_master_contract(
        DeckMasterContract.model_validate(overlap_payload)
    )
    rejected = validate_master(overlapping)
    assert rejected.valid is False
    assert rejected.writable_overlaps == 2
    assert {
        issue.code for issue in rejected.errors
    } == {"authored_vnext_master_furniture_body_geometry_overlap"}

    broken_stress = master_contract_payload()
    broken_stress["titleStressCorpus"][0]["expectedLines"] = ["다른 제목"]
    with pytest.raises(ValidationError, match="stress text"):
        DeckMasterContract.model_validate(broken_stress)


def test_file_repository_keeps_immutable_revisions_and_never_saves_preview_html(
    tmp_path,
) -> None:
    store = tmp_path / "authored-vnext-store"
    repository = FileAuthoredVNextMasterRepository(store)
    service = AuthoredVNextMasterService(repository)
    draft = service.create_draft(
        CreateDraftRequest(
            contract=DeckMasterContract.model_validate(master_contract_payload())
        )
    )
    published = service.publish(
        draft.master_id,
        MasterCasRequest(
            expected_revision=draft.revision,
            expected_digest=draft.digest,
        ),
    )
    preview = service.preview(
        PreviewRequest(
            master_id=published.master_id,
            master_revision=published.revision,
            master_digest=published.digest,
            slide=long_title_slide_payload(),
        )
    )
    repeated_preview = service.preview(
        PreviewRequest(
            master_id=published.master_id,
            master_revision=published.revision,
            master_digest=published.digest,
            slide=long_title_slide_payload(),
        )
    )

    revision_files = sorted(store.glob("masters/*/revisions/*.json"))
    assert [path.name for path in revision_files] == [
        "0000000001.json",
        "0000000002.json",
    ]
    assert preview.html.startswith("<!doctype html>")
    assert repeated_preview == preview
    assert all("<!doctype html>" not in path.read_text("utf-8") for path in store.rglob("*.json"))
    assert not (store / "candidates").exists()

    with pytest.raises(MasterRevisionConflictError):
        service.publish(
            draft.master_id,
            MasterCasRequest(
                expected_revision=draft.revision,
                expected_digest=draft.digest,
            ),
        )


def test_ooxml_port_accepts_only_canonical_contract_slides_and_export_plan() -> None:
    class FakeCompiler:
        def compile(self, *, master, slides, export_plan):
            assert master.digest == export_plan.master_digest
            assert tuple(slide.spec_digest for slide in slides) == tuple(
                item.spec_digest for item in export_plan.slides
            )
            return OoxmlCompileArtifact(
                artifact_digest="sha256:" + ("1" * 64),
                storage_reference="artifact://pptx/test",
            )

    compiler = FakeCompiler()
    assert isinstance(compiler, OoxmlMasterCompilerPort)
    parameters = inspect.signature(OoxmlMasterCompilerPort.compile).parameters
    assert set(parameters) == {"self", "master", "slides", "export_plan"}
    assert "html" not in parameters


def test_model_dump_uses_only_camel_case_public_fields() -> None:
    sealed = seal_master_contract(
        DeckMasterContract.model_validate(deepcopy(master_contract_payload()))
    )
    payload = sealed.model_dump(mode="json", by_alias=True)
    assert "contractVersion" in payload
    assert "fontManifest" in payload
    assert "titleStressCorpus" in payload
    assert "contract_version" not in payload
