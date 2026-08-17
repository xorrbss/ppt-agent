"""Focused domain tests for DeckMasterContract and immutable publication."""

from __future__ import annotations

from copy import deepcopy
import inspect

from pydantic import ValidationError
import pytest

from models.authored_vnext.master.contract import (
    DeckMasterContract,
    SemanticMasterElement,
    VisualDirection,
)
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
from utils.renderer_isolation import (
    RendererPolicyError,
    admit_authored_html,
    admit_static_output_html,
)
from tests.support.authored_vnext_master_fixture import (
    ZERO_DIGEST,
    long_title_slide_payload,
    master_contract_payload,
)


def _furniture_only_payload() -> dict[str, object]:
    payload = master_contract_payload()
    for layout in payload["layouts"].values():
        layout["slots"] = []
    payload["titleStressCorpus"] = []
    payload["aiEditZones"] = [
        {
            "id": "ai-zone:main-content",
            "scope": {
                "kind": "layout",
                "layoutId": payload["layouts"]["standard"]["id"],
            },
            "bounds": {"x": 100.0, "y": 120.0, "width": 1080.0, "height": 500.0},
        }
    ]
    return payload


def test_semantic_element_accepts_legacy_ai_editability_key_but_emits_canonical_key() -> None:
    payload = {
        "id": "element:legacy-open-tab",
        "type": "text",
        "scope": {"kind": "layout", "layoutId": "layout:standard"},
        "bounds": {"x": 100.0, "y": 100.0, "width": 240.0, "height": 86.0},
        "rotation": 0.0,
        "zIndex": 10,
        "visibility": "layoutOnly",
        "editableByUser": True,
        "editableByAi": False,
        "locked": False,
        "style": {"color": "#0F172A"},
        "content": {"text": "Fixed copy", "role": "fixed-copy"},
    }

    element = SemanticMasterElement.model_validate(payload)
    dumped = element.model_dump(mode="json", by_alias=True)

    assert element.editable_by_ai is False
    assert dumped["editableByAI"] is False
    assert "editableByAi" not in dumped


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


def test_furniture_only_contract_accepts_geometry_only_ai_edit_zones() -> None:
    payload = _furniture_only_payload()
    contract = DeckMasterContract.model_validate(payload)

    assert validate_master(contract).valid is True
    assert all(
        not layout.slots
        for layout in (
            contract.layouts.cover,
            contract.layouts.section,
            contract.layouts.standard,
            contract.layouts.long_title,
        )
    )
    assert contract.ai_edit_zones[0].model_dump(mode="json", by_alias=True) == {
        "id": "ai-zone:main-content",
        "scope": {"kind": "layout", "layoutId": "layout:standard-title"},
        "bounds": {"x": 100.0, "y": 120.0, "width": 1080.0, "height": 500.0},
    }

    content_owning_zone = _furniture_only_payload()
    content_owning_zone["aiEditZones"][0]["text"] = "master-owned content"
    with pytest.raises(ValidationError):
        DeckMasterContract.model_validate(content_owning_zone)

    unknown_layout = _furniture_only_payload()
    unknown_layout["aiEditZones"][0]["scope"]["layoutId"] = "layout:missing"
    with pytest.raises(ValidationError, match="unknown layout"):
        DeckMasterContract.model_validate(unknown_layout)


def test_ai_edit_zone_intent_policies_round_trip_without_breaking_legacy_shape() -> None:
    payload = _furniture_only_payload()
    payload["aiEditZones"][0].update(
        {
            "instruction": "핵심 요구사항을 3개의 번호 카드로 구성",
            "contentType": "cards",
            "pattern": "numberedCards",
            "sourcePolicy": "documentOnly",
            "emptyBehavior": "fallback",
            "fallbackText": "문서 근거가 없습니다.",
            "maxChars": 240,
            "maxItems": 3,
            "density": "balanced",
            "textAlign": "right",
            "stylePolicy": "inheritTemplate",
        }
    )

    contract = DeckMasterContract.model_validate(payload)
    zone = contract.ai_edit_zones[0]
    dumped = zone.model_dump(mode="json", by_alias=True)
    assert dumped["instruction"] == "핵심 요구사항을 3개의 번호 카드로 구성"
    assert dumped["contentType"] == "cards"
    assert dumped["sourcePolicy"] == "documentOnly"
    assert dumped["textAlign"] == "right"
    assert dumped["stylePolicy"] == "inheritTemplate"

    reentered = DeckMasterContract.model_validate(
        contract.model_dump(mode="json", by_alias=True)
    )
    assert reentered.ai_edit_zones[0] == zone

    missing_fallback = _furniture_only_payload()
    missing_fallback["aiEditZones"][0]["emptyBehavior"] = "fallback"
    with pytest.raises(ValidationError, match="requires fallbackText"):
        DeckMasterContract.model_validate(missing_fallback)

    whitespace_fallback = _furniture_only_payload()
    whitespace_fallback["aiEditZones"][0].update(
        {"emptyBehavior": "fallback", "fallbackText": "   "}
    )
    with pytest.raises(ValidationError, match="requires fallbackText"):
        DeckMasterContract.model_validate(whitespace_fallback)

    outside_canvas = _furniture_only_payload()
    outside_canvas["aiEditZones"][0]["bounds"] = {
        "x": 1200.0,
        "y": 120.0,
        "width": 1080.0,
        "height": 500.0,
    }
    with pytest.raises(ValidationError, match="inside the canvas"):
        DeckMasterContract.model_validate(outside_canvas)


def test_legacy_typography_slots_and_fit_stress_do_not_affect_master_validation() -> None:
    baseline_payload = master_contract_payload()
    legacy_variant = deepcopy(baseline_payload)
    for layout in legacy_variant["layouts"].values():
        for index, slot in enumerate(layout["slots"]):
            slot["bounds"] = {
                "x": float(index),
                "y": float(index),
                "width": 1.0,
                "height": 1.0,
            }
            slot["ownership"] = {"locked": True, "modelWritable": False}
    legacy_variant["fontManifest"].update(
        {
            "primary": "Legacy Display Face",
            "windowsFallback": "Unrelated Legacy Fallback",
            "fallbacks": ["Another Legacy Face"],
        }
    )
    legacy_variant["semanticStyles"]["title"].update(
        {"fontPt": 96.0, "minFontPt": 8.0, "maxLines": 12}
    )
    legacy_variant["semanticStyles"]["title"]["margins"] = {
        "leftPt": 72.0,
        "rightPt": 0.0,
        "topPt": 72.0,
        "bottomPt": 0.0,
    }
    legacy_variant["semanticStyles"]["body"].update(
        {"fontPt": 8.0, "minFontPt": 8.0, "lineHeight": 2.0}
    )
    legacy_variant["fitPolicy"].update(
        {
            "preferredFontPt": 16.0,
            "minimumFontPt": 72.0,
            "promotionLayoutId": "layout:cover",
        }
    )
    legacy_variant["titleStressCorpus"][0]["expectedLines"] = ["legacy mismatch"]

    baseline = DeckMasterContract.model_validate(baseline_payload)
    variant = DeckMasterContract.model_validate(legacy_variant)

    assert validate_master(baseline) == validate_master(variant)
    dumped = variant.model_dump(mode="json", by_alias=True)
    assert dumped["fontManifest"]["primary"] == "Legacy Display Face"
    assert dumped["semanticStyles"]["title"]["fontPt"] == 96.0
    assert dumped["layouts"]["standard"]["slots"][0]["bounds"]["width"] == 1.0
    assert dumped["fitPolicy"]["promotionLayoutId"] == "layout:cover"
    assert dumped["titleStressCorpus"][0]["expectedLines"] == ["legacy mismatch"]


def test_validation_fails_closed_on_furniture_ai_zone_overlap() -> None:
    valid = seal_master_contract(
        DeckMasterContract.model_validate(master_contract_payload())
    )
    summary = validate_master(valid)
    assert summary.valid is True
    assert summary.furniture_body_duplicates == 0
    assert summary.writable_overlaps == 0
    assert summary.title_fit_failures == 0

    overlap_payload = master_contract_payload()
    overlap_payload["aiEditZones"] = [
        {
            "id": "ai-zone:over-divider",
            "scope": {"kind": "common"},
            "bounds": {"x": 100.0, "y": 671.0, "width": 1000.0, "height": 4.0},
        }
    ]
    overlapping = seal_master_contract(
        DeckMasterContract.model_validate(overlap_payload)
    )
    rejected = validate_master(overlapping)
    assert rejected.valid is False
    assert rejected.writable_overlaps == 1
    assert {
        issue.code for issue in rejected.errors
    } == {"authored_vnext_master_furniture_ai_zone_geometry_overlap"}

    empty_copy_payload = master_contract_payload()
    empty_copy_payload["furniture"]["header"]["text"] = ""
    empty_copy_payload["furniture"]["footer"]["text"] = ""
    empty_copy_payload["aiEditZones"] = [
        {
            "id": "ai-zone:empty-header-area",
            "scope": {
                "kind": "layout",
                "layoutId": empty_copy_payload["layouts"]["standard"]["id"],
            },
            "bounds": {"x": 100.0, "y": 30.0, "width": 300.0, "height": 26.0},
        }
    ]
    empty_copy = seal_master_contract(
        DeckMasterContract.model_validate(empty_copy_payload)
    )
    assert validate_master(empty_copy).valid is True

    broken_stress = master_contract_payload()
    broken_stress["titleStressCorpus"][0]["expectedLines"] = ["다른 제목"]
    compatible = DeckMasterContract.model_validate(broken_stress)
    assert validate_master(compatible).valid is True


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
    with pytest.raises(RendererPolicyError):
        admit_authored_html(preview.html)
    admitted = admit_static_output_html(preview.html)
    assert admitted.source_digest.startswith("sha256:")
    assert "data-canonical-digest" in admitted.html
    assert "data-master-furniture" in admitted.html
    assert "data-body-slot" in admitted.html
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
