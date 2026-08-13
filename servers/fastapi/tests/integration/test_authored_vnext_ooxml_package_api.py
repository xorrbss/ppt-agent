from __future__ import annotations

from io import BytesIO
import subprocess
from pathlib import Path
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v1.ppt.endpoints.authored_vnext.package.router import (
    AUTHORED_VNEXT_PACKAGE_ROUTER,
    get_ooxml_package_service,
)
from services.authored_vnext.ooxml.service import OoxmlPackageService


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
NEXTJS_ROOT = REPOSITORY_ROOT / "servers" / "nextjs"


def _source(path: Path) -> bytes:
    subprocess.run(
        [
            "node",
            str(NEXTJS_ROOT / "lib" / "ooxml-vnext" / "cli.mjs"),
            "generate-demo",
            str(path),
            "2",
        ],
        cwd=NEXTJS_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        check=True,
    )
    return path.read_bytes()


def _macro_enabled(source: bytes) -> bytes:
    with zipfile.ZipFile(BytesIO(source), "r") as archive:
        entries = {
            item.filename: archive.read(item.filename)
            for item in archive.infolist()
            if not item.is_dir()
        }
    content_types = entries["[Content_Types].xml"].decode("utf-8")
    entries["[Content_Types].xml"] = content_types.replace(
        "</Types>",
        '<Override PartName="/ppt/vbaProject.bin" '
        'ContentType="application/vnd.ms-office.vbaProject"/></Types>',
    ).encode("utf-8")
    entries["ppt/vbaProject.bin"] = b"inert-hostile-macro-fixture"
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(entries.items()):
            archive.writestr(name, content)
    return output.getvalue()


def test_public_import_fork_save_receipt_download_rollback_and_archive(
    tmp_path: Path, monkeypatch
) -> None:
    service = OoxmlPackageService(tmp_path / "data", owner_id="api-owner")
    app = FastAPI()
    app.include_router(AUTHORED_VNEXT_PACKAGE_ROUTER)
    app.dependency_overrides[get_ooxml_package_service] = lambda: service
    monkeypatch.setenv("AUTHORED_VNEXT_OOXML_PACKAGE_ENABLED", "true")
    client = TestClient(app)
    source = _source(tmp_path / "source.pptx")

    imported_response = client.post(
        "/authored-vnext/packages/imports",
        headers={
            "Idempotency-Key": "api-import",
            "X-Account-Deletion-Generation": "0",
        },
        files={
            "file": (
                "source.pptx",
                source,
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            )
        },
    )
    assert imported_response.status_code == 201, imported_response.text
    imported = imported_response.json()["package"]
    assert imported["inspection"]["slideCount"] == 2

    fork_response = client.post(
        f"/authored-vnext/packages/{imported['packageId']}/forks",
        json={
            "schemaVersion": "authored-vnext-ooxml-fork-command/v1",
            "mode": "PRESERVE_EXACT",
            "expectedRevision": imported["revision"],
            "expectedArtifactDigest": imported["artifactDigest"],
            "idempotencyKey": "api-fork",
            "expectedAccountGeneration": 0,
        },
    )
    assert fork_response.status_code == 201, fork_response.text
    package = fork_response.json()["package"]

    queued_response = client.post(
        f"/authored-vnext/packages/{package['packageId']}/operations",
        json={
            "schemaVersion": "authored-vnext-ooxml-operation-command/v1",
            "operation": "NOOP_SAVE",
            "expectedRevision": package["revision"],
            "expectedArtifactDigest": package["artifactDigest"],
            "idempotencyKey": "api-noop",
            "expectedAccountGeneration": 0,
        },
    )
    assert queued_response.status_code == 202, queued_response.text
    queued = queued_response.json()
    completed_response = client.post(
        f"/authored-vnext/packages/operations/{queued['operationId']}/run"
    )
    assert completed_response.status_code == 200, completed_response.text
    completed = completed_response.json()
    assert completed["state"] == "succeeded"
    assert completed["resultArtifactDigest"] == package["artifactDigest"]

    receipt_response = client.get(
        f"/authored-vnext/packages/{package['packageId']}/receipt"
    )
    assert receipt_response.status_code == 200
    assert receipt_response.json()["silentLossCount"] == 0
    assert receipt_response.json()["untouchedPartsVerified"] is True
    artifact_response = client.get(
        f"/authored-vnext/packages/{package['packageId']}/artifact"
    )
    assert artifact_response.status_code == 200
    assert artifact_response.content == source

    current = receipt_response.json()["package"]
    rollback_response = client.post(
        f"/authored-vnext/packages/{package['packageId']}/rollback",
        json={
            "schemaVersion": "authored-vnext-ooxml-rollback-command/v1",
            "targetRevision": 1,
            "expectedRevision": current["revision"],
            "expectedArtifactDigest": current["artifactDigest"],
            "idempotencyKey": "api-rollback",
            "expectedAccountGeneration": 0,
        },
    )
    assert rollback_response.status_code == 200, rollback_response.text
    rolled_back = rollback_response.json()
    assert rolled_back["revision"] == 3

    archive_response = client.post(
        f"/authored-vnext/packages/{package['packageId']}/archive",
        json={
            "schemaVersion": "authored-vnext-ooxml-operation-command/v1",
            "operation": "ARCHIVE",
            "expectedRevision": rolled_back["revision"],
            "expectedArtifactDigest": rolled_back["artifactDigest"],
            "idempotencyKey": "api-archive",
            "expectedAccountGeneration": 0,
        },
    )
    assert archive_response.status_code == 202, archive_response.text
    assert archive_response.json()["state"] == "succeeded"
    inspected = client.get(
        f"/authored-vnext/packages/{package['packageId']}"
    ).json()
    assert inspected["state"] == "archived"


def test_public_import_quarantines_macro_and_blocks_editable_fork(
    tmp_path: Path, monkeypatch
) -> None:
    service = OoxmlPackageService(tmp_path / "data", owner_id="api-quarantine")
    app = FastAPI()
    app.include_router(AUTHORED_VNEXT_PACKAGE_ROUTER)
    app.dependency_overrides[get_ooxml_package_service] = lambda: service
    monkeypatch.setenv("AUTHORED_VNEXT_OOXML_PACKAGE_ENABLED", "true")
    client = TestClient(app)
    source = _macro_enabled(_source(tmp_path / "safe-source.pptx"))
    response = client.post(
        "/authored-vnext/packages/imports",
        headers={
            "Idempotency-Key": "api-quarantine-import",
            "X-Account-Deletion-Generation": "0",
        },
        files={
            "file": (
                "hostile.pptx",
                source,
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            )
        },
    )
    assert response.status_code == 201, response.text
    package = response.json()["package"]
    assert package["state"] == "quarantined"
    assert package["inspection"]["classification"] == "QUARANTINED"
    fork = client.post(
        f"/authored-vnext/packages/{package['packageId']}/forks",
        json={
            "schemaVersion": "authored-vnext-ooxml-fork-command/v1",
            "mode": "PRESERVE_EXACT",
            "expectedRevision": package["revision"],
            "expectedArtifactDigest": package["artifactDigest"],
            "idempotencyKey": "blocked-quarantine-fork",
            "expectedAccountGeneration": 0,
        },
    )
    assert fork.status_code == 423, fork.text
    assert fork.json()["detail"]["code"] == "authored_vnext_ooxml_quarantined_fork_forbidden"
