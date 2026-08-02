from __future__ import annotations

import asyncio
from copy import deepcopy
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
from models.sql.template_v2 import TemplateV2
from templates.v2 import catalog as catalog_module
from templates.v2.catalog import (
    CATALOG_SCHEMA_VERSION,
    DEFAULT_CATALOG_PATH,
    TemplateV2CatalogManifest,
    get_template_v2_catalog_entry,
    list_template_v2_catalog_entries,
    load_template_v2_catalog,
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


def test_unknown_template_keeps_backward_compatible_optional_fields():
    response = _response(_template("draft", None))

    assert response.source is None
    assert response.provenance is None
    assert response.capabilities is None
    assert response.preservation_mode is None
    assert response.template_kind is None
    assert response.publication_status == "blocked"
    assert get_template_v2_catalog_entry("draft") is None
