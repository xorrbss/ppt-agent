from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import subprocess
from pathlib import Path
import zipfile

import pytest

from services.authored_vnext.ooxml.errors import (
    OoxmlAccountFenceError,
    OoxmlPackageConflict,
    OoxmlPackageError,
    OoxmlPackageNotFound,
    SimulatedOoxmlWorkerCrash,
)
from services.authored_vnext.ooxml.service import OoxmlPackageService
from services.authored_vnext.ooxml.slide_extraction import extract_imported_slides
from services.authored_vnext.master.assets import OwnerMasterAssetStore
from services.authored_vnext.ooxml.account_lifecycle import (
    fence_authored_vnext_ooxml_packages_for_account,
)
from tests.support.authored_vnext_master_fixture import (
    master_contract_payload,
    master_contract_with_logo_payload,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
NEXTJS_ROOT = REPOSITORY_ROOT / "servers" / "nextjs"


def _generate_demo(path: Path, slides: int = 2) -> bytes:
    subprocess.run(
        [
            "node",
            str(NEXTJS_ROOT / "lib" / "ooxml-vnext" / "cli.mjs"),
            "generate-demo",
            str(path),
            str(slides),
        ],
        cwd=NEXTJS_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        check=True,
    )
    return path.read_bytes()


def _generate_existing_90(path: Path) -> bytes:
    subprocess.run(
        [
            "node",
            "--experimental-strip-types",
            "--import",
            "./scripts/register-node-test-loader.mjs",
            "./lib/ooxml-vnext/fixtures/build-existing-90.mjs",
            str(path),
        ],
        cwd=NEXTJS_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        check=True,
    )
    return path.read_bytes()


def test_slide_extraction_rejects_active_xml_without_outer_inspection() -> None:
    content = BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            b'<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///private">]>'
            b'<p:sld xmlns:p="p" xmlns:a="a"><a:t>&secret;</a:t></p:sld>',
        )
    with pytest.raises(ValueError, match="slide_xml_active_content"):
        extract_imported_slides(content.getvalue())


def _import_and_fork(service: OoxmlPackageService, content: bytes) -> dict[str, object]:
    imported = service.import_package(content, idempotency_key="import-1")["package"]
    return service.fork_editable(
        imported["packageId"],
        {
            "schemaVersion": "authored-vnext-ooxml-fork-command/v1",
            "mode": "PRESERVE_EXACT",
            "expectedRevision": imported["revision"],
            "expectedArtifactDigest": imported["artifactDigest"],
            "idempotencyKey": "fork-1",
            "expectedAccountGeneration": 0,
        },
    )["package"]


def _operation(
    package: dict[str, object],
    operation: str,
    key: str,
    **extra: object,
) -> dict[str, object]:
    return {
        "schemaVersion": "authored-vnext-ooxml-operation-command/v1",
        "operation": operation,
        "expectedRevision": package["revision"],
        "expectedArtifactDigest": package["artifactDigest"],
        "idempotencyKey": key,
        "expectedAccountGeneration": 0,
        **extra,
    }


def test_owner_idempotency_noop_edit_reopen_rollback_and_stale_cas(
    tmp_path: Path,
) -> None:
    source = _generate_demo(tmp_path / "source.pptx")
    owner = OoxmlPackageService(tmp_path / "data", owner_id="owner-a")
    other = OoxmlPackageService(tmp_path / "data", owner_id="owner-b")
    imported = owner.import_package(source, idempotency_key="import-same")
    replay = owner.import_package(source, idempotency_key="import-same")
    assert replay["replayed"] is True
    assert replay["package"]["packageId"] == imported["package"]["packageId"]
    with pytest.raises(OoxmlPackageConflict):
        owner.import_package(source + b"different", idempotency_key="import-same")
    with pytest.raises(OoxmlPackageNotFound):
        other.get_package(imported["package"]["packageId"])

    editable = _import_and_fork(owner, source)
    noop = owner.create_operation(
        editable["packageId"], _operation(editable, "NOOP_SAVE", "noop")
    )
    stale = owner.create_operation(
        editable["packageId"], _operation(editable, "SAVE_EXPORT", "stale")
    )
    nooped = owner.run_operation(noop["operationId"])
    assert nooped["state"] == "succeeded"
    assert nooped["resultArtifactDigest"] == editable["artifactDigest"]
    with pytest.raises(OoxmlPackageConflict):
        owner.run_operation(stale["operationId"])

    current = owner.get_package(editable["packageId"])
    edited = owner.create_operation(
        current["packageId"],
        _operation(
            current,
            "PATCH_TEXT_SLICE",
            "edit",
            partName="ppt/slides/slide1.xml",
            selector={"kind": "placeholder", "idx": 0, "role": "standard-title"},
            expectedText="OOXML vNext demo 1",
            replacementText="Targeted edit",
        ),
    )
    edit_receipt = owner.run_operation(edited["operationId"])
    assert edit_receipt["changedParts"] == ["ppt/slides/slide1.xml"]
    read_back = owner.read_back(current["packageId"])
    assert read_back["untouchedPartsVerified"] is True
    assert read_back["silentLossCount"] == 0
    assert read_back["unsupportedPartsPassthrough"] is True
    edited_path, _ = owner.open_artifact(current["packageId"])
    with zipfile.ZipFile(edited_path) as archive:
        assert b"Targeted edit" in archive.read("ppt/slides/slide1.xml")

    after_edit = owner.get_package(current["packageId"])
    export = owner.create_operation(
        after_edit["packageId"],
        _operation(after_edit, "SAVE_EXPORT", "save-export-success"),
    )
    exported = owner.run_operation(export["operationId"])
    assert exported["state"] == "succeeded"
    assert exported["resultArtifactDigest"] == after_edit["artifactDigest"]
    assert exported["changedParts"] == []

    after_export = owner.get_package(current["packageId"])
    rolled_back = owner.rollback(
        after_export["packageId"],
        {
            "schemaVersion": "authored-vnext-ooxml-rollback-command/v1",
            "targetRevision": 1,
            "expectedRevision": after_export["revision"],
            "expectedArtifactDigest": after_export["artifactDigest"],
            "idempotencyKey": "rollback-1",
            "expectedAccountGeneration": 0,
        },
    )
    assert rolled_back["revision"] == after_export["revision"] + 1
    rollback_path, rollback_digest = owner.open_artifact(rolled_back["packageId"])
    assert rollback_digest == editable["artifactDigest"]
    assert rollback_path.read_bytes() == source


def test_crash_resume_and_account_deletion_fence(tmp_path: Path) -> None:
    source = _generate_demo(tmp_path / "source.pptx", 1)
    service = OoxmlPackageService(tmp_path / "data", owner_id="owner-crash")
    editable = _import_and_fork(service, source)
    queued = service.create_operation(
        editable["packageId"], _operation(editable, "NOOP_SAVE", "crash")
    )
    with pytest.raises(SimulatedOoxmlWorkerCrash):
        service.run_operation(
            queued["operationId"], simulate_crash_after_candidate=True
        )
    assert service.get_operation(queued["operationId"])["state"] == "running"
    resumed = service.resume_operation(queued["operationId"])
    assert resumed["state"] == "succeeded"
    assert resumed["attemptCount"] == 2
    assert resumed["fencingToken"] == 2

    current = service.get_package(editable["packageId"])
    pending = service.create_operation(
        current["packageId"], _operation(current, "NOOP_SAVE", "deleting")
    )
    assert (
        fence_authored_vnext_ooxml_packages_for_account(
            tmp_path / "data",
            owner_id="owner-crash",
            expected_generation=1,
        )
        == 1
    )
    assert service.get_operation(pending["operationId"])["state"] == "cancelled"
    assert (
        fence_authored_vnext_ooxml_packages_for_account(
            tmp_path / "data",
            owner_id="owner-crash",
            expected_generation=1,
        )
        == 0
    )
    with pytest.raises(OoxmlAccountFenceError):
        service.import_package(source, idempotency_key="after-delete")


def test_corporate_normalize_master_and_layout_crud(tmp_path: Path) -> None:
    source = _generate_demo(tmp_path / "source.pptx", 1)
    service = OoxmlPackageService(tmp_path / "data", owner_id="owner-master")
    imported = service.import_package(source, idempotency_key="import-master")[
        "package"
    ]
    canonical = master_contract_payload()
    normalized = service.fork_editable(
        imported["packageId"],
        {
            "schemaVersion": "authored-vnext-ooxml-fork-command/v1",
            "mode": "CORPORATE_NORMALIZE",
            "expectedRevision": imported["revision"],
            "expectedArtifactDigest": imported["artifactDigest"],
            "idempotencyKey": "normalize",
            "expectedAccountGeneration": 0,
            "masterContract": canonical,
        },
    )["package"]
    assert normalized["origin"] == "GENERATED"
    assert service.read_back(normalized["packageId"])[
        "unsupportedPartsPassthrough"
    ] is False

    worker = service._normalize_master_payload(canonical)

    def run(kind: str, key: str, contract: dict[str, object] | None = None):
        current = service.get_package(normalized["packageId"])
        command = _operation(current, kind, key)
        if contract is not None:
            command["masterContract"] = contract
        queued = service.create_operation(current["packageId"], command)
        completed = service.run_operation(queued["operationId"])
        assert completed["state"] == "succeeded"
        return service.get_package(current["packageId"])

    updated = deepcopy(worker)
    updated["name"] = "Updated Corporate Master"
    run("UPDATE_MASTER", "master-update", updated)

    with_layout = deepcopy(updated)
    added_layout = deepcopy(
        next(layout for layout in with_layout["layouts"] if layout["kind"] == "standard")
    )
    added_layout["id"] = "layout:appendix"
    added_layout["name"] = "Appendix"
    for placeholder in added_layout["placeholders"]:
        placeholder["id"] = placeholder["id"].replace(
            "placeholder:standard-", "placeholder:appendix-"
        )
    with_layout["layouts"].append(added_layout)
    run("CREATE_LAYOUT", "layout-create", with_layout)

    changed_layout = deepcopy(with_layout)
    changed_layout["layouts"][-1]["name"] = "Appendix Updated"
    run("UPDATE_LAYOUT", "layout-update", changed_layout)

    deleted_layout = deepcopy(changed_layout)
    deleted_layout["layouts"].pop()
    run("DELETE_LAYOUT", "layout-delete", deleted_layout)
    with pytest.raises(OoxmlPackageConflict, match="master_in_use"):
        run("DELETE_MASTER", "master-delete")


def test_corporate_normalize_derives_and_retains_all_90_imported_slides(
    tmp_path: Path,
) -> None:
    source = _generate_existing_90(tmp_path / "existing-90.pptx")
    owner_id = "owner-normalize-90"
    app_data = tmp_path / "data"
    logo_bytes = (NEXTJS_ROOT / "public" / "Logo.png").read_bytes()
    asset_store = OwnerMasterAssetStore(
        app_data
        / "authored_vnext"
        / "master-assets"
        / "users"
        / owner_id
    )
    asset = asset_store.publish_png(logo_bytes)
    master = master_contract_with_logo_payload()
    master["brandAssets"]["logo"]["assetId"] = asset["assetId"]
    master["brandAssets"]["logo"]["digest"] = asset["digest"]
    master["furniture"]["logo"]["assetId"] = asset["assetId"]
    service = OoxmlPackageService(app_data, owner_id=owner_id)
    imported = service.import_package(source, idempotency_key="import-90")["package"]
    assert imported["inspection"]["slideCount"] == 90
    command = {
        "schemaVersion": "authored-vnext-ooxml-fork-command/v1",
        "mode": "CORPORATE_NORMALIZE",
        "expectedRevision": imported["revision"],
        "expectedArtifactDigest": imported["artifactDigest"],
        "idempotencyKey": "normalize-derived-90",
        "expectedAccountGeneration": 0,
        "masterContract": master,
    }
    normalized = service.fork_editable(imported["packageId"], command)["package"]
    assert normalized["inspection"]["slideCount"] == 90
    assert normalized["inspection"]["layoutCount"] == 4
    read_back = service.read_back(normalized["packageId"])
    assert read_back["silentLossCount"] == 0
    normalized_path, _digest = service.open_artifact(normalized["packageId"])
    with zipfile.ZipFile(normalized_path) as archive:
        assert b"\xea\xb8\xb0\xec\x97\x85 AI \xec\xa0\x84\xed\x99\x98" in archive.read(
            "ppt/slides/slide1.xml"
        )
        relationships = {
            archive.read(f"ppt/slides/_rels/slide{index}.xml.rels")
            for index in range(1, 91)
        }
        assert len(relationships) == 4
        assert any(
            archive.read(name) == logo_bytes
            for name in archive.namelist()
            if name.startswith("ppt/media/")
        )

    with pytest.raises(OoxmlPackageError, match="normalize_slides_required"):
        service.fork_editable(
            imported["packageId"],
            {**command, "idempotencyKey": "normalize-empty", "slides": []},
        )
