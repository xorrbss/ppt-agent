from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from templates.packages import (
    PackageAssetInventory,
    TemplatePackageAssetStore,
    TemplatePackageRegistry,
    TemplatePackageValidationError,
    validate_template_package,
)


ROOT = Path(__file__).resolve().parents[4]
MANIFEST = Path("templates/manifests/editorial.package.v1.json")


def test_editorial_is_verified_and_installed_but_publication_blocked() -> None:
    registry = TemplatePackageRegistry(ROOT, Path("templates/package-registry.v1.json"))
    editorial = registry.get("editorial")

    assert editorial.receipt.asset_count == 116
    assert editorial.receipt.total_size_bytes == 5_096_023
    assert editorial.receipt.publication_status == "blocked"
    assert set(editorial.receipt.blockers) >= {
        "remote_font_reference",
        "unresolved_font_aliases",
        "raster_rights_evidence_missing",
        "browser_golden_receipt_missing",
    }
    assert registry.discoverable() == ()


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


def test_blocked_package_cannot_be_marked_discoverable(tmp_path: Path) -> None:
    registry = json.loads((ROOT / "templates" / "package-registry.v1.json").read_text())
    registry["packages"][0]["discoverable"] = True
    test_root = tmp_path / "repo"
    shutil.copytree(ROOT / "templates", test_root / "templates")
    shutil.copytree(ROOT / "contracts" / "template_package", test_root / "contracts" / "template_package")
    (test_root / "templates" / "package-registry.v1.json").write_text(
        json.dumps(registry), encoding="utf-8"
    )

    with pytest.raises(TemplatePackageValidationError, match="blocked_discoverability"):
        TemplatePackageRegistry(test_root, Path("templates/package-registry.v1.json"))
