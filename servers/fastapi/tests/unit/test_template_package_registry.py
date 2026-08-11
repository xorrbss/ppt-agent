from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_revision import TemplateV2Revision
from templates.packages import defaults as package_defaults
from templates.packages import (
    PackageAssetInventory,
    TemplatePackageAssetStore,
    TemplatePackageRegistry,
    TemplatePackageValidationError,
    validate_template_package,
)
from templates.packages.defaults import _template_from_package
from templates.packages.registry import (
    is_template_package_release_ready,
    template_package_assets_binding,
)
from templates.packages.evidence_validation import validate_rights_evidence
from templates.packages.models import PackageFile, TemplatePackageManifest


ROOT = Path(__file__).resolve().parents[4]
MANIFEST = Path("templates/manifests/editorial.package.v1.json")


def test_editorial_is_verified_ready_and_discoverable() -> None:
    registry = TemplatePackageRegistry(ROOT, Path("templates/package-registry.v1.json"))
    editorial = registry.get("editorial")

    assert editorial.receipt.asset_count == 116
    assert editorial.receipt.total_size_bytes == 5_096_023
    assert editorial.receipt.publication_status == "ready"
    assert editorial.receipt.blockers == ()
    assert editorial.manifest.license.status == "approved"
    assert editorial.manifest.license.rights_evidence_id == (
        "presenton-editorial-assets-apache-2.0"
    )
    assert editorial.manifest.fonts.unresolved_aliases == ()
    assert tuple(package.manifest.template_id for package in registry.discoverable()) == (
        "dynamic",
        "editorial",
        "executive",
        "general",
        "modern",
        "momentum",
        "standard",
        "swift",
    )


def test_every_ready_package_materializes_to_a_network_free_generation_payload() -> None:
    registry = TemplatePackageRegistry(ROOT, Path("templates/package-registry.v1.json"))

    for package in registry.discoverable():
        payload = registry.materialize(package.manifest.template_id)
        persisted = _template_from_package(package, payload)

        assert len(persisted.layouts["layouts"]) > 0
        assert persisted.assets["template_package"] == template_package_assets_binding(
            package
        )
        assert is_template_package_release_ready(
            persisted.id,
            persisted.assets,
        )
        assert "http://" not in json.dumps(payload)
        assert "https://" not in json.dumps(payload)


def test_package_release_binding_rejects_a_stale_entrypoint_receipt() -> None:
    registry = TemplatePackageRegistry(ROOT, Path("templates/package-registry.v1.json"))
    package = registry.get("general")
    assets = {"template_package": template_package_assets_binding(package)}
    assets["template_package"]["entrypoint_sha256"] = "0" * 64

    assert not is_template_package_release_ready("general", assets)


def test_startup_import_uses_only_registry_admitted_packages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.templates: dict[str, TemplateV2] = {}
            self.revisions: dict[tuple[str, int], TemplateV2Revision] = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, model, identity):
            if model is TemplateV2:
                return self.templates.get(identity)
            if model is TemplateV2Revision:
                return self.revisions.get(identity)
            return None

        def add(self, value) -> None:
            if isinstance(value, TemplateV2):
                self.templates[value.id] = value
            elif isinstance(value, TemplateV2Revision):
                self.revisions[(value.template_id, value.revision)] = value

        async def flush(self) -> None:
            return None

        async def commit(self) -> None:
            return None

        async def scalars(self, _statement):
            return list(self.templates.values())

        async def delete(self, value) -> None:
            self.templates.pop(value.id, None)

    session = FakeSession()
    monkeypatch.setattr(package_defaults, "async_session_maker", lambda: session)
    monkeypatch.setattr(
        package_defaults,
        "get_app_data_directory_env",
        lambda: str(tmp_path / "app-data"),
    )
    registry = TemplatePackageRegistry(ROOT, Path("templates/package-registry.v1.json"))

    asyncio.run(package_defaults.import_template_packages_on_startup(registry))

    assert set(session.templates) == {
        package.manifest.template_id for package in registry.discoverable()
    }
    assert set(template_id for template_id, _revision in session.revisions) == set(
        session.templates
    )
    for template_id in session.templates:
        assert (
            tmp_path
            / "app-data"
            / "templates"
            / template_id
            / "static"
            / "thumbnail.png"
        ).is_file()


def test_manifest_path_escape_and_hash_tampering_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(TemplatePackageValidationError, match="path_invalid"):
        validate_template_package(ROOT, Path("../editorial.package.v1.json"))

    package_root = tmp_path / "repo"
    shutil.copytree(ROOT / "templates" / "editorial", package_root / "templates" / "editorial")
    shutil.copytree(ROOT / "templates" / "manifests", package_root / "templates" / "manifests")
    shutil.copytree(ROOT / "contracts" / "template_package", package_root / "contracts" / "template_package")
    thumbnail = package_root / "templates" / "editorial" / "static" / "thumbnail.png"
    thumbnail.write_bytes(thumbnail.read_bytes() + b"tampered")

    with pytest.raises(TemplatePackageValidationError, match="size_mismatch"):
        validate_template_package(package_root, MANIFEST)


def test_content_addressed_asset_store_deduplicates_verified_bytes(tmp_path: Path) -> None:
    inventory = PackageAssetInventory.model_validate_json(
        (ROOT / "templates" / "manifests" / "editorial.assets.v1.json").read_bytes()
    )
    subset = PackageAssetInventory(
        schemaVersion="presenton.template-package-assets/v1",
        templateId="editorial",
        files=(inventory.files[0],),
    )
    store = TemplatePackageAssetStore(tmp_path / "private-cas")

    first = store.import_inventory(ROOT / "templates" / "editorial", subset)
    second = store.import_inventory(ROOT / "templates" / "editorial", subset)

    assert first == second
    digest = inventory.files[0].sha256
    blob = tmp_path / "private-cas" / "blobs" / "sha256" / digest[:2] / digest
    assert hashlib.sha256(blob.read_bytes()).hexdigest() == digest


def test_provenance_evidence_tampering_fails_closed(tmp_path: Path) -> None:
    test_root = tmp_path / "repo"
    shutil.copytree(ROOT / "templates", test_root / "templates")
    shutil.copytree(ROOT / "contracts" / "template_package", test_root / "contracts" / "template_package")
    renderer = test_root / "servers" / "nextjs" / "lib" / "template-v2-json-to-html.ts"
    renderer.parent.mkdir(parents=True)
    shutil.copy2(ROOT / renderer.relative_to(test_root), renderer)
    evidence = test_root / "templates" / "manifests" / "editorial.provenance.v1.json"
    evidence.write_bytes(evidence.read_bytes() + b"\n")

    with pytest.raises(TemplatePackageValidationError, match="provenance_hash_mismatch"):
        TemplatePackageRegistry(test_root, Path("templates/package-registry.v1.json"))


def _copied_package_root(tmp_path: Path) -> Path:
    test_root = tmp_path / "repo"
    shutil.copytree(ROOT / "templates", test_root / "templates")
    shutil.copytree(
        ROOT / "contracts" / "template_package",
        test_root / "contracts" / "template_package",
    )
    renderer = test_root / "servers" / "nextjs" / "lib" / "template-v2-json-to-html.ts"
    renderer.parent.mkdir(parents=True)
    shutil.copy2(ROOT / renderer.relative_to(test_root), renderer)
    return test_root


def _rewrite_provenance(test_root: Path, mutate: object) -> None:
    evidence_path = test_root / "templates" / "manifests" / "editorial.provenance.v1.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert callable(mutate)
    mutate(evidence)
    evidence_bytes = (json.dumps(evidence, indent=2) + "\n").encode()
    evidence_path.write_bytes(evidence_bytes)
    manifest_path = test_root / MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["provenance"]["evidenceSha256"] = hashlib.sha256(evidence_bytes).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def test_provenance_rejects_unknown_evidence_source_reference(tmp_path: Path) -> None:
    test_root = _copied_package_root(tmp_path)
    _rewrite_provenance(
        test_root,
        lambda evidence: evidence["claims"][0]["evidenceSourceIds"].append("missing-source"),
    )

    with pytest.raises(
        TemplatePackageValidationError,
        match="evidence_source_reference_invalid",
    ):
        validate_template_package(test_root, MANIFEST)


def test_provenance_rejects_false_upstream_blob_identity(tmp_path: Path) -> None:
    test_root = _copied_package_root(tmp_path)
    _rewrite_provenance(
        test_root,
        lambda evidence: evidence["claims"][0]["coveredAssets"][0].update(
            upstreamGitBlobSha1="0" * 40
        ),
    )

    with pytest.raises(TemplatePackageValidationError, match="rights_evidence_not_exact"):
        validate_template_package(test_root, MANIFEST)


def test_provenance_requires_immutable_declared_font_license_evidence(tmp_path: Path) -> None:
    test_root = _copied_package_root(tmp_path)
    _rewrite_provenance(
        test_root,
        lambda evidence: next(
            source
            for source in evidence["evidenceSources"]
            if source["id"] == "google-fonts-dm-sans-ofl"
        ).pop("sha256"),
    )

    with pytest.raises(
        TemplatePackageValidationError,
        match="evidence_source_not_immutable",
    ):
        validate_template_package(test_root, MANIFEST)


def test_provenance_rejects_a_claim_to_the_wrong_declared_license(tmp_path: Path) -> None:
    test_root = _copied_package_root(tmp_path)
    _rewrite_provenance(
        test_root,
        lambda evidence: next(
            source
            for source in evidence["evidenceSources"]
            if source["id"] == "google-fonts-dm-sans-ofl"
        ).update(licenseExpression="MIT"),
    )

    with pytest.raises(
        TemplatePackageValidationError,
        match="rights_evidence_invalid",
    ):
        validate_template_package(test_root, MANIFEST)


def test_rights_evidence_is_generic_for_a_fontless_mit_package(tmp_path: Path) -> None:
    source_root = tmp_path / "generic"
    source_root.mkdir()
    asset_bytes = b"generic package image bytes"
    asset_path = source_root / "image.png"
    asset_path.write_bytes(asset_bytes)
    asset_sha256 = hashlib.sha256(asset_bytes).hexdigest()
    asset_blob_sha1 = hashlib.sha1(
        f"blob {len(asset_bytes)}\0".encode() + asset_bytes,
        usedforsecurity=False,
    ).hexdigest()
    inventory = PackageAssetInventory(
        schemaVersion="presenton.template-package-assets/v1",
        templateId="generic",
        files=(
            PackageFile(
                path="image.png",
                sha256=asset_sha256,
                sizeBytes=len(asset_bytes),
                mediaType="image/png",
                role="template-asset",
                licenseRef="MIT",
            ),
        ),
    )
    editorial = TemplatePackageManifest.model_validate_json((ROOT / MANIFEST).read_bytes())
    manifest = editorial.model_copy(
        update={
            "template_id": "generic",
            "provenance": editorial.provenance.model_copy(update={"extensions": ()}),
            "license": editorial.license.model_copy(
                update={"expression": "MIT", "rights_evidence_id": "generic-assets-mit"}
            ),
        }
    )
    commit_sha = manifest.provenance.commit_sha
    license_sha256 = "1" * 64
    license_blob_sha1 = "2" * 40
    license_url = f"{manifest.provenance.repository}/blob/{commit_sha}/LICENSE"
    evidence = {
        "schemaVersion": "presenton.template-package-provenance/v1",
        "templateId": "generic",
        "upstream": {
            "repository": manifest.provenance.repository,
            "commitSha": commit_sha,
            "sourcePath": manifest.provenance.source_path,
            "license": {
                "expression": "MIT",
                "sha256": license_sha256,
                "upstreamGitBlobSha1": license_blob_sha1,
                "url": license_url,
            },
        },
        "evidenceSources": [
            {
                "id": "source-commit",
                "type": "git-commit",
                "url": f"{manifest.provenance.repository}/commit/{commit_sha}",
                "commitSha": commit_sha,
            },
            {
                "id": "source-license",
                "type": "repository-license",
                "licenseExpression": "MIT",
                "url": license_url,
                "sha256": license_sha256,
                "upstreamGitBlobSha1": license_blob_sha1,
            },
        ],
        "claims": [
            {
                "id": "generic-assets-mit",
                "status": "verified",
                "licenseExpression": "MIT",
                "evidenceSourceIds": ["source-commit", "source-license"],
                "coveredAssets": [
                    {
                        "path": "image.png",
                        "sha256": asset_sha256,
                        "upstreamGitBlobSha1": asset_blob_sha1,
                    }
                ],
            }
        ],
    }

    validate_rights_evidence(source_root, manifest, inventory, evidence)
