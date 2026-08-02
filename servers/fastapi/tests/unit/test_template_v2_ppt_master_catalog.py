from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
import uuid

import pytest
from pydantic import ValidationError

from api.v1.ppt.endpoints.structured_templates import (
    _response,
    list_structured_templates,
)
from api.v1.ppt.endpoints import structured_templates as structured_templates_module
from models.sql.template_v2 import TemplateV2
from templates.v2 import catalog as catalog_module
from templates.v2.catalog import (
    CATALOG_SCHEMA_VERSION,
    DEFAULT_CATALOG_PATH,
    DEFAULT_RIGHTS_PATH,
    RIGHTS_SCHEMA_VERSION,
    TemplateRightsManifest,
    TemplateRightsRecord,
    TemplateV2CatalogEntry,
    TemplateV2CatalogManifest,
    catalog_entry_sha256,
    evaluate_rights_readiness,
    get_template_v2_catalog_entry,
    is_template_v2_catalog_entry_rights_ready,
    list_template_v2_catalog_entries,
    load_template_rights_manifest,
    load_template_v2_catalog,
    rights_evidence_set_sha256,
)


PINNED_PPT_MASTER_COMMIT = "8858c4960b870f12baf952994fbd0c76ab5ba4ec"
CATALOG_IDS = {
    "ppt-master-swiss-grid-systems",
    "ppt-master-editorial-magazine",
    "ppt-master-data-journalism",
    "ppt-master-glassmorphism-saas",
}


def _native_clone_assets() -> dict[str, object]:
    return {
        "native_pptx": {
            "schema_version": "presenton.template-v2-native-source/v1",
            "preservation_mode": "native_clone",
            "storage_key": "template-v2/native/safe/source.pptx",
            "source_sha256": "a" * 64,
            "source_size_bytes": 2048,
            "layout_source_parts": {"title": "ppt/slides/slide1.xml"},
            "capabilities": {
                "native_clone": True,
                "text_patch": True,
                "table_patch": True,
                "master_layout_theme": True,
                "relationships_media": True,
            },
            "publication_status": "publishable",
        }
    }


def _template(template_id: str, assets: dict | None) -> TemplateV2:
    return TemplateV2(
        id=template_id,
        presentation_id=uuid.uuid4(),
        name=template_id,
        layouts=None,
        assets=assets,
    )


def test_ppt_master_catalog_records_pinned_file_level_provenance():
    manifest = load_template_v2_catalog()

    assert manifest.schema_version == CATALOG_SCHEMA_VERSION
    assert {entry.id for entry in manifest.entries} == CATALOG_IDS
    assert {entry.template_kind for entry in manifest.entries} == {
        "brand",
        "layout",
        "deck",
    }
    for entry in manifest.entries:
        assert entry.source.repository == "https://github.com/hugohe3/ppt-master"
        assert entry.source.commit_sha == PINNED_PPT_MASTER_COMMIT
        assert entry.source.path.startswith("examples/")
        assert {item.role for item in entry.source.files} >= {
            "design_spec",
            "spec_lock",
            "candidate_pptx",
            "workflow",
            "repository_license",
        }
        assert all(len(item.sha256) == 64 for item in entry.source.files)
        assert entry.provenance.importer_version == "1.0.0"
        assert entry.provenance.runtime_network_fetch is False
        assert entry.provenance.license.repository_spdx == "MIT"
        assert entry.provenance.license.attribution.startswith("PPT Master by Hugo He")
        assert entry.preservation_mode in {"native_clone", "reconstructed"}
        assert entry.capabilities.master_layout_theme is True


def test_unverified_embedded_assets_are_blocked_and_never_copied():
    reviewed = list_template_v2_catalog_entries(include_unpublished=True)

    assert len(reviewed) == 4
    assert list_template_v2_catalog_entries() == ()
    for entry in reviewed:
        license_evidence = entry.provenance.license
        rights_review = entry.provenance.asset_rights_review
        assert entry.publication_status == "blocked"
        assert license_evidence.repository_license_verified is True
        assert license_evidence.embedded_assets_license_verified is False
        assert license_evidence.redistribution_approved is False
        assert rights_review.copied_asset_count == 0
        assert rights_review.font_file_count == 0
        assert rights_review.unresolved_rights

    manifest_dir = DEFAULT_CATALOG_PATH.parent
    copied_binaries = [
        path
        for path in manifest_dir.rglob("*")
        if path.suffix.lower() in {".pptx", ".png", ".jpg", ".svg", ".ttf", ".otf"}
    ]
    assert copied_binaries == []


def test_rights_manifest_pins_import_receipts_but_keeps_all_candidates_pending():
    catalog = {entry.id: entry for entry in load_template_v2_catalog().entries}
    rights = load_template_rights_manifest()

    assert rights.schema_version == RIGHTS_SCHEMA_VERSION
    assert {record.template_id for record in rights.records} == CATALOG_IDS
    for record in rights.records:
        entry = catalog[record.template_id]
        candidate = next(
            item for item in entry.source.files if item.role == "candidate_pptx"
        )
        assert record.catalog_entry_sha256 == catalog_entry_sha256(entry)
        assert record.source.repository == entry.source.repository
        assert record.source.revision == entry.source.commit_sha
        assert record.source.candidate_pptx_path == candidate.path
        assert record.source.candidate_pptx_sha256 == candidate.sha256
        assert record.source.candidate_pptx_size_bytes > 0
        assert set(record.required_scopes) >= {"candidate_pptx", "embedded_media"}
        assert set(record.required_permissions) == {
            "redistribute",
            "modify",
            "commercial_use",
        }
        assert record.evidence == ()
        assert record.approval.status == "pending"

        report = evaluate_rights_readiness(entry, record)
        assert report.ready_for_publication_review is False
        assert "rights approval is not approved" in report.blockers
        assert any("embedded_media" in blocker for blocker in report.blockers)
        assert entry.publication_status == "blocked"


def _approved_rights_record(tmp_path: Path) -> tuple[TemplateRightsRecord, Path]:
    payload = json.loads(DEFAULT_RIGHTS_PATH.read_text(encoding="utf-8"))["records"][0]
    evidence_path = tmp_path / "evidence" / "rights-holder-grant.txt"
    evidence_path.parent.mkdir()
    evidence_path.write_text(
        "The rights holder grants redistribution, modification, and commercial use "
        "of the identified PPTX and all embedded media.",
        encoding="utf-8",
    )
    payload["evidence"] = [
        {
            "id": "rights-holder-grant-001",
            "evidence_type": "rights_holder_grant",
            "document_path": "evidence/rights-holder-grant.txt",
            "document_sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
            "issuer": "Verified rights holder",
            "license_expression": "Written redistribution grant",
            "covered_scopes": ["candidate_pptx", "embedded_media"],
            "covered_artifact_sha256": payload["source"]["candidate_pptx_sha256"],
            "granted_permissions": ["redistribute", "modify", "commercial_use"],
            "attribution_text": "PPT Master by Hugo He",
            "coverage_statement": "Grant explicitly covers the complete pinned PPTX package.",
        }
    ]
    pending = TemplateRightsRecord.model_validate(payload)
    payload["approval"] = {
        "status": "approved",
        "reviewer": "rights-review@example.invalid",
        "reviewed_at": "2026-08-02T12:00:00+09:00",
        "evidence_set_sha256": rights_evidence_set_sha256(pending),
        "decision": "Issuer and scope verified against the pinned package digest.",
    }
    return TemplateRightsRecord.model_validate(payload), evidence_path


def test_verified_local_evidence_can_reach_review_ready_without_auto_publishing(
    tmp_path: Path,
):
    entry = load_template_v2_catalog().entries[0]
    record, _ = _approved_rights_record(tmp_path)

    report = evaluate_rights_readiness(entry, record, evidence_root=tmp_path)

    assert report.ready_for_publication_review is True
    assert report.blockers == ()
    assert entry.publication_status == "blocked"


def _published_catalog_entry() -> TemplateV2CatalogEntry:
    payload = load_template_v2_catalog().entries[0].model_dump(mode="json")
    payload["publication_status"] = "published"
    payload["provenance"]["license"]["embedded_assets_license_verified"] = True
    payload["provenance"]["license"]["redistribution_approved"] = True
    return TemplateV2CatalogEntry.model_validate(payload)


def test_catalog_publication_status_cannot_bypass_pending_rights_manifest():
    entry = _published_catalog_entry()

    assert is_template_v2_catalog_entry_rights_ready(entry) is False


def test_rights_readiness_requires_evidence_for_operator_added_scope(tmp_path: Path):
    entry = load_template_v2_catalog().entries[0]
    record, _ = _approved_rights_record(tmp_path)
    payload = record.model_dump(mode="json")
    payload["required_scopes"].append("fonts")
    payload["approval"] = {"status": "pending"}
    pending = TemplateRightsRecord.model_validate(payload)
    payload["approval"] = {
        "status": "approved",
        "reviewer": "rights-review@example.invalid",
        "reviewed_at": "2026-08-02T12:00:00+09:00",
        "evidence_set_sha256": rights_evidence_set_sha256(pending),
        "decision": "Additional font scope requires its own evidence.",
    }

    report = evaluate_rights_readiness(
        entry,
        TemplateRightsRecord.model_validate(payload),
        evidence_root=tmp_path,
    )

    assert report.ready_for_publication_review is False
    assert "no complete verified rights evidence covers scope: fonts" in report.blockers


def test_rights_readiness_fails_closed_for_tampering_or_stale_approval(tmp_path: Path):
    entry = load_template_v2_catalog().entries[0]
    record, evidence_path = _approved_rights_record(tmp_path)
    evidence_path.write_text("tampered", encoding="utf-8")

    tampered = evaluate_rights_readiness(entry, record, evidence_root=tmp_path)
    assert tampered.ready_for_publication_review is False
    assert any("document digest mismatch" in blocker for blocker in tampered.blockers)

    payload = record.model_dump(mode="json")
    payload["approval"]["evidence_set_sha256"] = "f" * 64
    stale = evaluate_rights_readiness(
        entry,
        TemplateRightsRecord.model_validate(payload),
        evidence_root=tmp_path,
    )
    assert stale.ready_for_publication_review is False
    assert "rights approval digest does not match the evidence set" in stale.blockers


def test_rights_schema_rejects_network_paths_incomplete_review_and_duplicates():
    payload = json.loads(DEFAULT_RIGHTS_PATH.read_text(encoding="utf-8"))
    record = deepcopy(payload["records"][0])
    record["evidence"] = [
        {
            "id": "unsafe-evidence",
            "evidence_type": "rights_holder_grant",
            "document_path": "https://example.invalid/grant.pdf",
            "document_sha256": "a" * 64,
            "issuer": "Unknown",
            "license_expression": "Unknown",
            "covered_scopes": ["candidate_pptx"],
            "covered_artifact_sha256": record["source"]["candidate_pptx_sha256"],
            "granted_permissions": ["redistribute"],
            "coverage_statement": "Incomplete and remote.",
        }
    ]
    with pytest.raises(ValidationError):
        TemplateRightsRecord.model_validate(record)

    record = deepcopy(payload["records"][0])
    record["approval"] = {"status": "approved"}
    with pytest.raises(ValidationError):
        TemplateRightsRecord.model_validate(record)

    for field in ("reviewer", "decision"):
        record = deepcopy(payload["records"][0])
        record["approval"] = {
            "status": "approved",
            "reviewer": "rights-reviewer",
            "reviewed_at": "2026-08-02T00:00:00+09:00",
            "evidence_set_sha256": "a" * 64,
            "decision": "approved for publication",
        }
        record["approval"][field] = "   "
        with pytest.raises(ValidationError):
            TemplateRightsRecord.model_validate(record)

    duplicate = deepcopy(payload)
    duplicate["records"].append(deepcopy(duplicate["records"][0]))
    with pytest.raises(ValidationError):
        TemplateRightsManifest.model_validate(duplicate)


def test_catalog_loader_has_no_runtime_network_fallback():
    source = inspect.getsource(catalog_module)

    assert "requests" not in source
    assert "httpx" not in source
    assert "aiohttp" not in source
    assert "urllib" not in source
    assert all(
        entry.provenance.runtime_network_fetch is False
        for entry in load_template_v2_catalog().entries
    )


def test_catalog_rejects_network_fetch_path_traversal_and_unverified_publication(
    tmp_path: Path,
):
    payload = json.loads(DEFAULT_CATALOG_PATH.read_text(encoding="utf-8"))

    network_payload = deepcopy(payload)
    network_payload["entries"][0]["provenance"]["runtime_network_fetch"] = True
    traversal_payload = deepcopy(payload)
    traversal_payload["entries"][0]["source"]["files"][0]["path"] = "../asset.png"
    published_payload = deepcopy(payload)
    published_payload["entries"][0]["publication_status"] = "published"

    for index, invalid in enumerate(
        (network_payload, traversal_payload, published_payload),
        start=1,
    ):
        path = tmp_path / f"invalid-{index}.json"
        path.write_text(json.dumps(invalid), encoding="utf-8")
        with pytest.raises(ValidationError):
            load_template_v2_catalog(path)


def test_catalog_rejects_unpinned_commit_and_duplicate_ids():
    payload = json.loads(DEFAULT_CATALOG_PATH.read_text(encoding="utf-8"))

    payload["entries"][0]["source"]["commit_sha"] = "main"
    with pytest.raises(ValidationError):
        TemplateV2CatalogManifest.model_validate(payload)

    payload = json.loads(DEFAULT_CATALOG_PATH.read_text(encoding="utf-8"))
    payload["entries"][1]["id"] = payload["entries"][0]["id"]
    with pytest.raises(ValidationError):
        TemplateV2CatalogManifest.model_validate(payload)


def test_direct_api_response_exposes_optional_blocked_catalog_metadata():
    template = _template("ppt-master-swiss-grid-systems", _native_clone_assets())

    response = _response(template)

    assert response.publication_status == "blocked"
    assert response.template_kind == "layout"
    assert response.preservation_mode == "native_clone"
    assert response.source is not None
    assert response.source["commit_sha"] == PINNED_PPT_MASTER_COMMIT
    assert response.provenance is not None
    assert response.provenance["runtime_network_fetch"] is False
    assert response.capabilities is not None
    assert response.capabilities["master_layout_theme"] is True


def test_list_api_hides_legacy_and_blocked_catalog_rows_but_keeps_verified_v2(
    monkeypatch,
):
    blocked = _template("ppt-master-swiss-grid-systems", _native_clone_assets())
    legacy = _template("legacy", {"logo": {"path": "asset://logo"}})
    verified = _template("presenton-safe", _native_clone_assets())

    class ListedSession:
        async def execute(self, *_args, **_kwargs):
            class Result:
                def scalars(self):
                    return self

                def all(self):
                    return [blocked, legacy, verified]

            return Result()

    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv(
        "TEMPLATE_V2_TEMPLATE_ALLOWLIST",
        "ppt-master-swiss-grid-systems,legacy,presenton-safe",
    )

    discovered = asyncio.run(list_structured_templates(sql_session=ListedSession()))

    assert [template.id for template in discovered] == ["presenton-safe"]
    assert discovered[0].publication_status == "published"
    assert discovered[0].preservation_mode == "native_clone"
    assert discovered[0].source is None
    assert discovered[0].provenance is None


def test_list_and_response_require_rights_readiness_for_published_catalog_row(
    monkeypatch,
):
    catalog_entry = _published_catalog_entry()
    candidate = _template(catalog_entry.id, _native_clone_assets())

    class ListedSession:
        async def execute(self, *_args, **_kwargs):
            class Result:
                def scalars(self):
                    return self

                def all(self):
                    return [candidate]

            return Result()

    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", catalog_entry.id)
    monkeypatch.setattr(
        structured_templates_module,
        "get_template_v2_catalog_entry",
        lambda _template_id: catalog_entry,
    )
    monkeypatch.setattr(
        structured_templates_module,
        "is_template_v2_catalog_entry_rights_ready",
        lambda _entry: False,
    )

    assert asyncio.run(list_structured_templates(sql_session=ListedSession())) == []
    assert _response(candidate).publication_status == "blocked"

    monkeypatch.setattr(
        structured_templates_module,
        "is_template_v2_catalog_entry_rights_ready",
        lambda _entry: True,
    )
    discovered = asyncio.run(list_structured_templates(sql_session=ListedSession()))

    assert [template.id for template in discovered] == [catalog_entry.id]
    assert discovered[0].publication_status == "published"


def test_unknown_template_keeps_backward_compatible_optional_fields():
    response = _response(_template("draft", None))

    assert response.source is None
    assert response.provenance is None
    assert response.capabilities is None
    assert response.preservation_mode is None
    assert response.template_kind is None
    assert response.publication_status == "blocked"
    assert get_template_v2_catalog_entry("draft") is None
