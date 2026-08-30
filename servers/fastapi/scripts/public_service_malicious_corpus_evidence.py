"""Produce same-RC product-path security evidence for the public PPT service.

The runner intentionally exercises production modules and one-shot worker
subprocesses.  It does not import pytest fixtures and it never opens a public
listener.  The resulting JSON is consumed by ``public-service-release-gate``.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable
import uuid
import zipfile

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from api.v1.ppt.endpoints.authored_vnext.export import router as export_router
from models.authored_vnext.master.operations import (
    CreateDeckRequest,
    CreateDraftRequest,
    MasterCasRequest,
)
from models.sql.public_artifact import artifact_storage_key
from models.sql.user import User
from services.isolated_worker_contracts import (
    ArtifactRef,
    CapabilityReplayGuard,
    IsolatedWorkerContractError,
    IsolatedWorkerJob,
    issue_worker_capability,
    verify_and_consume_worker_capability,
)
from services.isolated_worker_process import (
    NETWORK_NONE_ATTESTATION,
    IsolatedWorkerProcessError,
    execute_isolated_worker_job,
    worker_environment,
)
from services.public_worker_profiles import (
    get_public_worker_profile,
    validate_public_worker_environment,
)
from services.authored_vnext.integration.artifact_store import BufferedArtifactStore
from services.authored_vnext.integration.export_service import AuthoredVNextExportService
from services.authored_vnext.integration.sql_repository import (
    AuthoredVNextSqlRepository,
    ExportArtifactInput,
)
from services.authored_vnext.master.service import AuthoredVNextMasterService
from services.authored_vnext.master.catalog_seed import catalog_seed_contracts
from templates.v2.pptx.package_reader import PptxPackageReader, UnsafePptxPackage
from utils.owner_context import reset_current_owner_id, set_current_owner_id


SCHEMA_VERSION = "public-service-malicious-corpus-evidence/v1"
SOURCE_MANIFEST_SCHEMA = "public-service-source-manifest/v1"
EVIDENCE_CLASS = "product-path"
TENANT_ID = "public-security-tenant"
OWNER_A = uuid.uuid5(uuid.NAMESPACE_URL, "presenton:public-security:owner-a")
OWNER_B = uuid.uuid5(uuid.NAMESPACE_URL, "presenton:public-security:owner-b")
SAFE_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;width:1280px;height:720px;background:#fff}.card{position:absolute;left:20px;top:20px;width:420px;height:120px;background:#2563eb;color:white;padding:12px}
</style></head><body><main class="card"><h1>Public worker security probe</h1></main></body></html>"""
PRESENTATION_XML = b"""<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst/></p:presentation>"""
PRESENTATION_RELS = b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
RUNNER_PATH = "servers/fastapi/scripts/public_service_malicious_corpus_evidence.py"
RENDERER_SECURITY_PRODUCER_FILES = (
    "docs/experiments/authored-vnext/04-renderer-security/harness/run-harness.ps1",
    "docs/experiments/authored-vnext/04-renderer-security/harness/verify_container_inspect.py",
    "docs/experiments/authored-vnext/04-renderer-security/harness/verify_product_resilience.py",
    "docs/experiments/authored-vnext/04-renderer-security/ci/security_gate.py",
    "contracts/authored-renderer/attestation.py",
    "contracts/authored-renderer/invoke.py",
    "contracts/authored-renderer/compose.authored-renderer.json",
    "Dockerfile",
)
SECURITY_SOURCE_FILES = (
    "servers/fastapi/services/isolated_worker_contracts.py",
    "servers/fastapi/services/isolated_worker_process.py",
    "servers/fastapi/services/artifact_store.py",
    "servers/fastapi/workers/public_worker_runtime.py",
    "servers/fastapi/workers/public_render_worker.py",
    "servers/fastapi/templates/v2/pptx/package_reader.py",
    "servers/fastapi/api/v1/ppt/endpoints/authored_vnext/export/router.py",
    "scripts/public-service-release-gate.mjs",
    *RENDERER_SECURITY_PRODUCER_FILES,
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _value_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{_sha256(payload)}"


def _uuid(label: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"presenton:public-security:{label}")


def _source_digest(path: Path) -> str:
    return f"sha256:{_sha256(path.read_bytes())}"


def _git_head(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.stdout.strip()


def _load_source_manifest(
    path: Path,
    *,
    repo_root: Path,
    release_candidate: str,
    source_tree_commit: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    manifest_path = path.resolve()
    payload = manifest_path.read_bytes()
    exact_digest = f"sha256:{_sha256(payload)}"
    if exact_digest != release_candidate:
        raise ValueError("source_manifest_release_candidate_mismatch")
    manifest = json.loads(payload)
    if manifest.get("schemaVersion") != SOURCE_MANIFEST_SCHEMA:
        raise ValueError("source_manifest_schema_invalid")
    if manifest.get("sourceTreeCommit") != source_tree_commit:
        raise ValueError("source_manifest_tree_commit_mismatch")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("source_manifest_files_required")
    file_digests: dict[str, str] = {}
    for entry in entries:
        relative = entry.get("path") if isinstance(entry, dict) else None
        digest = entry.get("sha256") if isinstance(entry, dict) else None
        if (
            not isinstance(relative, str)
            or not relative
            or "\\" in relative
            or relative.startswith("/")
            or ".." in Path(relative).parts
            or relative in file_digests
            or not isinstance(digest, str)
            or len(digest) != 71
            or not digest.startswith("sha256:")
            or any(character not in "0123456789abcdef" for character in digest[7:])
        ):
            raise ValueError("source_manifest_file_entry_invalid")
        absolute = (repo_root / relative).resolve()
        try:
            absolute.relative_to(repo_root)
        except ValueError as error:
            raise ValueError("source_manifest_file_path_escape") from error
        if _source_digest(absolute) != digest:
            raise ValueError(f"source_manifest_file_digest_mismatch:{relative}")
        file_digests[relative] = digest
    missing = sorted(set((RUNNER_PATH, *SECURITY_SOURCE_FILES)) - set(file_digests))
    if missing:
        raise ValueError(f"source_manifest_security_sources_missing:{','.join(missing)}")
    return manifest, file_digests


def _storage_key(owner_id: uuid.UUID, payload: bytes) -> str:
    return artifact_storage_key(
        owner_id=owner_id,
        tenant_id=TENANT_ID,
        digest=_sha256(payload),
    )


def _artifact(
    *,
    label: str,
    operation_id: uuid.UUID,
    owner_id: uuid.UUID,
    payload: bytes,
    kind: str,
    media_type: str,
) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=_uuid(f"artifact:{label}"),
        owner_id=owner_id,
        tenant_id=TENANT_ID,
        kind=kind,
        sha256=_sha256(payload),
        size_bytes=len(payload),
        media_type=media_type,
        storage_key=_storage_key(owner_id, payload),
        created_by_operation_id=operation_id,
        retention_class="ephemeral",
    )


def _store(root: Path, reference: ArtifactRef, payload: bytes) -> None:
    target = root / reference.storage_key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)


def _job(
    *,
    label: str,
    role: str,
    artifact: ArtifactRef,
    signing_key: bytes,
) -> IsolatedWorkerJob:
    now = int(time.time())
    job = IsolatedWorkerJob(
        role=role,
        operation_id=artifact.created_by_operation_id,
        attempt_token=f"attempt-{_sha256(label.encode())[:32]}",
        fencing_token=7,
        input_digest=artifact.sha256,
        input_artifact=artifact,
        capability="pending",
        deadline_epoch_seconds=now + 180,
    )
    return replace(
        job,
        capability=issue_worker_capability(
            job,
            signing_key=signing_key,
            ttl_seconds=120,
            now=now,
            nonce=_sha256(f"nonce:{label}".encode())[:32],
        ),
    )


def _worker_source_environment(
    *, root: Path, key_file: Path, fastapi_root: Path, chrome_path: str
) -> dict[str, str]:
    allowed = {
        name: value
        for name, value in os.environ.items()
        if name.upper()
        in {
            "COMSPEC",
            "LANG",
            "LANGUAGE",
            "LC_ALL",
            "LOCALAPPDATA",
            "PATH",
            "PATHEXT",
            "PYTHONHOME",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "TMPDIR",
            "VIRTUAL_ENV",
            "WINDIR",
        }
    }
    allowed.update(
        {
            "CHROME_PATH": chrome_path,
            "PYTHONPATH": str(fastapi_root),
            "PUBLIC_WORKER_CAPABILITY_KEY_FILE": str(key_file),
            "PUBLIC_WORKER_OBJECT_STORE_ROOT": str(root),
            "PUBLIC_WORKER_OS_ISOLATION": NETWORK_NONE_ATTESTATION,
            # Deliberate canaries: the product launcher must not copy these.
            "COOKIE": "must-not-cross-worker-boundary",
            "DATABASE_URL": "must-not-cross-worker-boundary",
            "OPENAI_API_KEY": "must-not-cross-worker-boundary",
        }
    )
    return allowed


async def _run_render_job(
    *,
    label: str,
    html: str,
    root: Path,
    signing_key: bytes,
    source_environment: dict[str, str],
) -> tuple[bool, str | None, ArtifactRef | None]:
    payload = html.encode("utf-8")
    operation_id = _uuid(f"operation:html:{label}")
    reference = _artifact(
        label=f"html:{label}",
        operation_id=operation_id,
        owner_id=OWNER_A,
        payload=payload,
        kind="slide-html",
        media_type="text/html",
    )
    _store(root, reference, payload)
    job = _job(label=f"render:{label}", role="render", artifact=reference, signing_key=signing_key)
    try:
        result = await execute_isolated_worker_job(
            job,
            source_environment=source_environment,
            command=[str(Path(sys.executable).resolve()), "-m", "workers.public_render_worker"],
        )
    except (IsolatedWorkerProcessError, IsolatedWorkerContractError) as error:
        return False, str(error), None
    output = ArtifactRef.from_wire(dict(result.output_artifact or {}))
    return True, None, output


async def _html_corpus(
    *, root: Path, signing_key: bytes, source_environment: dict[str, str]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cases = (
        ("network-script-fetch", "network", '<script>fetch("https://egress.invalid/x")</script>'),
        ("network-img-https", "network", '<img src="https://egress.invalid/pixel.png">'),
        ("network-loopback-iframe", "network", '<iframe src="http://127.0.0.1:9/private"></iframe>'),
        ("network-css-url", "network", '<div style="background:url(https://egress.invalid/x)">x</div>'),
        ("network-obfuscated-css", "network", r'<style>.x{background-image:u\72l(http://127.0.0.1:9/x)}</style>'),
        ("network-svg-paint", "network", '<svg><rect fill="url(https://egress.invalid/x)"></rect></svg>'),
        ("file-img", "file", '<img src="file:///etc/passwd">'),
        ("file-css-mask", "file", '<div style="mask-image:url(file:///etc/passwd)">x</div>'),
        ("file-object-relative", "file", '<object data="../../secret"></object>'),
        ("metadata-aws", "metadata", '<img src="http://169.254.169.254/latest/meta-data/iam/security-credentials/">'),
        ("metadata-gcp", "metadata", '<script>fetch("http://metadata.google.internal/computeMetadata/v1/")</script>'),
        ("metadata-azure", "metadata", '<iframe src="http://169.254.169.254/metadata/instance"></iframe>'),
    )
    observations: list[dict[str, Any]] = []
    for case_id, category, html in cases:
        succeeded, error_code, _ = await _run_render_job(
            label=case_id,
            html=html,
            root=root,
            signing_key=signing_key,
            source_environment=source_environment,
        )
        observations.append(
            {
                "caseId": case_id,
                "category": category,
                "attemptedProductPath": "public-render-worker-subprocess",
                "expectedErrorCode": "public_render_policy_rejected",
                "accessSucceeded": succeeded,
                "observedErrorCode": error_code,
                "pass": not succeeded and error_code == "public_render_policy_rejected",
            }
        )
    safe_succeeded, safe_error, safe_output = await _run_render_job(
        label="safe-html-control",
        html=SAFE_HTML,
        root=root,
        signing_key=signing_key,
        source_environment=source_environment,
    )
    safe_control = {
        "attemptedProductPath": "public-render-worker-subprocess",
        "expectedOutcome": "image/png",
        "observedErrorCode": safe_error,
        "outputMediaType": safe_output.media_type if safe_output else None,
        "pass": bool(
            safe_succeeded
            and safe_output is not None
            and safe_output.media_type == "image/png"
        ),
    }
    return observations, safe_control


def _write_zip(
    path: Path,
    *,
    extra: list[tuple[str | zipfile.ZipInfo, bytes]] | None = None,
    compression: int = zipfile.ZIP_DEFLATED,
    required_parts: bool = True,
) -> None:
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        if required_parts:
            archive.writestr("[Content_Types].xml", b"<Types/>")
            archive.writestr("ppt/presentation.xml", PRESENTATION_XML)
            archive.writestr("ppt/_rels/presentation.xml.rels", PRESENTATION_RELS)
        for name, payload in extra or []:
            archive.writestr(name, payload)


def _pptx_corpus(root: Path) -> list[dict[str, Any]]:
    symlink = zipfile.ZipInfo("ppt/media/link.png")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    xxe = '<!DOCTYPE foo [<!ENTITY secret SYSTEM "file:///secret">]><foo>&secret;</foo>'
    cases: list[tuple[str, str, Callable[[Path], None], str | None]] = [
        ("zip-traversal", "unsafe_zip_member_path", lambda path: _write_zip(path, extra=[("../escape.xml", b"x")]), None),
        ("zip-absolute", "unsafe_zip_member_path", lambda path: _write_zip(path, extra=[("/absolute.xml", b"x")]), None),
        ("zip-drive", "unsafe_zip_member_path", lambda path: _write_zip(path, extra=[("C:/drive.xml", b"x")]), None),
        ("zip-duplicate", "duplicate_zip_member", lambda path: _write_zip(path, extra=[("ppt/custom.xml", b"a"), ("PPT/CUSTOM.XML", b"b")]), None),
        ("zip-bzip2", "unsupported_zip_compression", lambda path: _write_zip(path, compression=zipfile.ZIP_BZIP2), None),
        ("zip-macro", "macro_enabled_package_forbidden", lambda path: _write_zip(path, extra=[("ppt/vbaProject.bin", b"macro")]), None),
        ("zip-required-parts", "pptx_required_parts_missing", lambda path: _write_zip(path, required_parts=False, extra=[("ppt/other.xml", b"x")]), None),
        ("zip-symlink", "symlink_zip_member", lambda path: _write_zip(path, extra=[(symlink, b"target")]), None),
        ("zip-ratio", "zip_compression_ratio_exceeded", lambda path: _write_zip(path, extra=[("ppt/huge.bin", b"0" * 1_000_000)]), None),
        ("xml-xxe-utf8", "unsafe_xml_declaration", lambda path: _write_zip(path, extra=[("ppt/custom.xml", xxe.encode("utf-8"))]), "ppt/custom.xml"),
        ("xml-xxe-utf16", "unsafe_xml_declaration", lambda path: _write_zip(path, extra=[("ppt/custom.xml", xxe.encode("utf-16"))]), "ppt/custom.xml"),
    ]
    observations: list[dict[str, Any]] = []
    for case_id, expected, builder, xml_member in cases:
        path = root / f"{case_id}.pptx"
        builder(path)
        observed = None
        try:
            reader = PptxPackageReader(path)
            reader.preflight()
            if xml_member:
                reader.read_xml(xml_member)
        except UnsafePptxPackage as error:
            observed = error.code
        observations.append(
            {
                "caseId": case_id,
                "attemptedProductPath": "template-v2-pptx-package-reader",
                "expectedErrorCode": expected,
                "observedErrorCode": observed,
                "pass": observed == expected,
            }
        )
    return observations


class _UnusedWorkerTransport:
    async def execute(self, _payload: dict[str, Any], _timeout_ms: int) -> dict[str, Any]:
        raise RuntimeError("cross_owner_attack_must_not_reach_worker")


def _http_error_code(response) -> str | None:
    try:
        detail = response.json().get("detail")
    except Exception:
        return None
    if isinstance(detail, dict):
        return detail.get("code")
    return detail if isinstance(detail, str) else None


def _cross_owner_artifact_checks(
    root: Path,
) -> tuple[list[dict[str, Any]], bool, dict[str, Any]]:
    engine = create_engine(
        f"sqlite:///{root / 'public-api-owner-isolation.sqlite3'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    User.__table__.create(engine, checkfirst=True)
    with Session(engine) as session:
        session.add_all(
            [
                User(id=OWNER_A, username="security-owner-a", hashed_password="test-only"),
                User(id=OWNER_B, username="security-owner-b", hashed_password="test-only"),
            ]
        )
        session.commit()
    AuthoredVNextSqlRepository.create_schema(engine)
    repositories = {
        str(owner): AuthoredVNextSqlRepository(
            engine,
            owner_scope=str(owner),
            tenant_id=TENANT_ID,
            owner_id=owner,
        )
        for owner in (OWNER_A, OWNER_B)
    }
    owner_a = repositories[str(OWNER_A)]
    master_service = AuthoredVNextMasterService(owner_a)
    catalog_contract = catalog_seed_contracts(
        {
            "assetId": "asset:public-security-seed",
            "digest": f"sha256:{'0' * 64}",
        }
    )[1]
    draft = master_service.create_draft(
        CreateDraftRequest(contract=catalog_contract)
    )
    master = master_service.publish(
        draft.master_id,
        MasterCasRequest(expected_revision=draft.revision, expected_digest=draft.digest),
    )
    deck = master_service.create_deck(
        CreateDeckRequest(
            deck_id="deck:public-security-owner-a",
            master_id=master.master_id,
            master_revision=master.revision,
            master_digest=master.digest,
            slides=[
                {
                    "slideId": "slide:public-security-owner-a",
                    "layoutId": "layout:standard",
                    "title": {
                        "text": "Owner-bound artifact control",
                        "lines": ["Owner-bound artifact control"],
                        "lineBreakPolicy": "canonical-explicit-uax14-v1",
                        "uax14Version": "17.0.0",
                        "tailoring": "ko-space-first-with-id-fallback-v1",
                        "kinsokuPolicy": "cjk-no-leading-closing-no-trailing-opening",
                    },
                    "body": "Owner B must not read or mutate owner A publication state.",
                }
            ],
        )
    )
    digests = {
        "spec": deck.digest,
        "assetManifest": master.digest,
        "fontManifest": master.contract.font_manifest.digest or master.digest,
    }
    job = owner_a.create_export_job(
        deck_id=deck.deck_id,
        deck_revision=deck.revision,
        deck_digest=deck.digest,
        master_revision=deck.master_revision,
        master_digest=deck.master_digest,
        spec_digest=digests["spec"],
        asset_manifest_digest=digests["assetManifest"],
        font_manifest_digest=digests["fontManifest"],
        idempotency_key="public-security-owner-a",
        request_payload={"deckId": deck.deck_id},
        timeout_ms=1_000,
        max_attempts=1,
    )
    claimed = owner_a.claim_export_job(job.id, worker_id="security-seed-worker")
    artifact_bytes = b"PK\x03\x04owner-a-security-artifact"
    artifact_store_a = BufferedArtifactStore(root / "api-artifacts" / str(OWNER_A))
    artifact_digest = artifact_store_a.digest_bytes(artifact_bytes)
    storage_key, size_bytes = artifact_store_a.publish(
        artifact_bytes,
        artifact_digest,
    )
    receipt = owner_a.complete_export_job(
        job.id,
        fence_token=claimed.fence_token,
        artifact=ExportArtifactInput(
            storage_key=storage_key,
            artifact_digest=artifact_digest,
            readback_digest=artifact_digest,
            package_digest=artifact_digest,
            size_bytes=size_bytes,
            master_digest=deck.master_digest,
            spec_digest=digests["spec"],
            asset_manifest_digest=digests["assetManifest"],
            font_manifest_digest=digests["fontManifest"],
        ),
    )
    stores = {
        str(OWNER_A): artifact_store_a,
        str(OWNER_B): BufferedArtifactStore(root / "api-artifacts" / str(OWNER_B)),
    }
    services = {
        owner: AuthoredVNextExportService(
            repositories[owner],
            _UnusedWorkerTransport(),
            stores[owner],
        )
        for owner in repositories
    }
    app = FastAPI()

    @app.middleware("http")
    async def bind_owner(request: Request, call_next):
        raw_owner = request.headers.get("x-security-owner")
        token = set_current_owner_id(uuid.UUID(raw_owner) if raw_owner else None)
        try:
            return await call_next(request)
        finally:
            reset_current_owner_id(token)

    app.include_router(export_router.AUTHORED_VNEXT_EXPORT_ROUTER, prefix="/api/v1/ppt")
    previous_factory = export_router._service_for_scope
    previous_enabled = os.environ.get("AUTHORED_VNEXT_EXPORT_ENABLED")
    export_router._service_for_scope = lambda owner_scope, _owner_id: services[owner_scope]
    os.environ["AUTHORED_VNEXT_EXPORT_ENABLED"] = "true"
    prefix = "/api/v1/ppt/authored-vnext/export"
    headers_a = {"x-security-owner": str(OWNER_A)}
    headers_b = {"x-security-owner": str(OWNER_B)}
    try:
        with TestClient(app) as client:
            same_owner = client.get(
                f"{prefix}/decks/{deck.deck_id}/artifact", headers=headers_a
            )
            attacks = (
                (
                    "api-read-artifact",
                    "read",
                    client.get(f"{prefix}/decks/{deck.deck_id}/artifact", headers=headers_b),
                    "authored_vnext_artifact_not_found",
                ),
                (
                    "api-read-job",
                    "read",
                    client.get(f"{prefix}/jobs/{job.id}", headers=headers_b),
                    "authored_vnext_export_job_not_found",
                ),
                (
                    "api-cancel-job",
                    "write",
                    client.post(
                        f"{prefix}/jobs/{job.id}/cancel",
                        headers=headers_b,
                        json={"operationFence": 1},
                    ),
                    "authored_vnext_export_job_not_found",
                ),
                (
                    "api-rollback-artifact",
                    "write",
                    client.post(
                        f"{prefix}/decks/{deck.deck_id}/artifacts/{receipt.id}/rollback",
                        headers=headers_b,
                        json={
                            "expectedPublicationRevision": 1,
                            "expectedArtifactDigest": artifact_digest,
                        },
                    ),
                    "authored_vnext_artifact_not_found",
                ),
            )
    finally:
        export_router._service_for_scope = previous_factory
        if previous_enabled is None:
            os.environ.pop("AUTHORED_VNEXT_EXPORT_ENABLED", None)
        else:
            os.environ["AUTHORED_VNEXT_EXPORT_ENABLED"] = previous_enabled
    active = owner_a.get_active_artifact(deck.deck_id)
    owner_control = bool(
        same_owner.status_code == 200
        and same_owner.content == artifact_bytes
        and active is not None
        and active[0].revision == 1
        and active[0].digest == artifact_digest
    )
    observations = []
    for case_id, operation, response, expected_code in attacks:
        observed_code = _http_error_code(response)
        observations.append(
            {
                "caseId": case_id,
                "operation": operation,
                "attemptedProductPath": "authored-vnext-export-http-api",
                "expectedHttpStatus": 404,
                "observedHttpStatus": response.status_code,
                "expectedErrorCode": expected_code,
                "observedErrorCode": observed_code,
                "accessSucceeded": 200 <= response.status_code < 300,
                "pass": response.status_code == 404 and observed_code == expected_code,
            }
        )
    engine.dispose()
    return observations, owner_control, {
        "artifactId": str(receipt.id),
        "deckId": deck.deck_id,
        "digest": artifact_digest,
        "ownerBound": owner_control,
    }


def _capability_checks(signing_key: bytes) -> list[dict[str, Any]]:
    payload = b"immutable capability input"
    artifact = _artifact(
        label="capability-control",
        operation_id=_uuid("operation:capability-control"),
        owner_id=OWNER_A,
        payload=payload,
        kind="slide-html",
        media_type="text/html",
    )
    valid = _job(label="capability-control", role="render", artifact=artifact, signing_key=signing_key)
    cases = (
        ("operation", replace(valid, operation_id=_uuid("operation:tampered"))),
        ("digest", replace(valid, input_digest="f" * 64)),
        ("attempt", replace(valid, attempt_token="tampered-attempt-token")),
        ("fence", replace(valid, fencing_token=valid.fencing_token + 1)),
    )
    observations: list[dict[str, Any]] = []
    for label, tampered in cases:
        rejected = False
        try:
            verify_and_consume_worker_capability(
                tampered,
                signing_key=signing_key,
                replay_guard=CapabilityReplayGuard(),
            )
        except IsolatedWorkerContractError:
            rejected = True
        observations.append({"caseId": f"capability-{label}-tamper", "pass": rejected})
    guard = CapabilityReplayGuard()
    verify_and_consume_worker_capability(valid, signing_key=signing_key, replay_guard=guard)
    replay_rejected = False
    try:
        verify_and_consume_worker_capability(valid, signing_key=signing_key, replay_guard=guard)
    except IsolatedWorkerContractError:
        replay_rejected = True
    observations.append({"caseId": "capability-replay", "pass": replay_rejected})
    return observations


def _worker_boundary_checks(source_environment: dict[str, str]) -> dict[str, bool]:
    filtered = worker_environment("render", source_environment)
    forbidden_markers = (
        "API_KEY",
        "COOKIE",
        "DATABASE_URL",
        "PASSWORD",
        "PROVIDER",
        "SESSION",
    )
    sessionless = filtered.get("PUBLIC_WORKER_SESSIONLESS") == "1" and not any(
        key != "PUBLIC_WORKER_SESSIONLESS"
        and any(marker in key.upper() for marker in forbidden_markers)
        for key in filtered
    )
    missing_attestation_rejected = False
    try:
        worker_environment("render", {})
    except IsolatedWorkerProcessError:
        missing_attestation_rejected = True
    profile = get_public_worker_profile("render")
    isolated_environment = {
        "PPT_WORKER_NETWORK_MODE": "none",
        "PPT_WORKER_ROOT_FILESYSTEM": "read-only",
        "PPT_WORKER_SCRATCH": "tmpfs",
        "PPT_WORKER_RUN_AS_NON_ROOT": "true",
        "PPT_WORKER_MEMORY_LIMIT_BYTES": "1073741824",
        "PPT_WORKER_CPU_MILLIS": "1000",
        "PPT_WORKER_PID_LIMIT": "64",
        "PPT_WORKER_OPEN_FILE_LIMIT": "256",
        "PPT_WORKER_OUTPUT_LIMIT_BYTES": "268435456",
        "PPT_WORKER_WALL_CLOCK_SECONDS": "180",
        "PPT_WORKER_REPLICAS": "1",
    }
    validate_public_worker_environment(profile, isolated_environment)
    return {
        "sessionlessEnvironmentAllowlistVerified": sessionless,
        "missingIsolationAttestationRejected": missing_attestation_rejected,
        "renderProfileNetworkModeNone": profile.network_mode == "none",
        "renderProfileNoCanonicalDatabase": not profile.canonical_database_access,
        "renderProfileResourceContractVerified": True,
    }


def _renderer_attestation(repo_root: Path) -> dict[str, str]:
    completed = subprocess.run(
        [sys.executable, str(repo_root / "contracts/authored-renderer/attestation.py")],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    value = json.loads(completed.stdout)
    source_tree = value.get("AUTHORED_RENDERER_SOURCE_TREE_DIGEST")
    contract = value.get("AUTHORED_RENDERER_CONTRACT_DIGEST")
    for label, digest in (("source tree", source_tree), ("contract", contract)):
        if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError(f"renderer_{label.replace(' ', '_')}_digest_invalid")
    return {"sourceTreeDigest": source_tree, "contractDigest": contract}


def _network_isolation_observation(
    evidence_dir: Path | None,
    *,
    release_candidate: str,
    source_tree_commit: str,
    manifest_file_digests: dict[str, str],
    repo_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    unavailable = {
        "osNetworkNamespaceObserved": False,
        "containerNetworkMode": None,
        "egressSinkConnectionCount": None,
        "cdpBlockedRequestCount": None,
        "sourceRevisionMatches": False,
        "producerSourceBindingObserved": False,
        "evidenceExactByteChainObserved": False,
        "immutableImageIdentityObserved": False,
        "observationDigests": {},
        "pass": False,
        "reason": "same_rc_renderer_security_evidence_required",
    }
    safe_control = {
        "attemptedProductPath": "network-none-authored-renderer-product-worker",
        "expectedOutcome": "normal HTML rendered successfully",
        "pass": False,
        "reason": "same_rc_renderer_security_evidence_required",
    }
    if evidence_dir is None:
        return unavailable, safe_control
    root = evidence_dir.resolve()
    security_path = root / "security-observation.json"
    inspect_path = root / "container-inspect.json"
    gate_path = root / "gate-result.json"
    try:
        security = json.loads(security_path.read_text(encoding="utf-8"))
        inspected = json.loads(inspect_path.read_text(encoding="utf-8"))
        gate_result = json.loads(gate_path.read_text(encoding="utf-8"))
        container = inspected[0]
    except (OSError, IndexError, KeyError, TypeError, json.JSONDecodeError):
        unavailable["reason"] = "renderer_security_evidence_invalid"
        safe_control["reason"] = unavailable["reason"]
        return unavailable, safe_control
    labels = ((container.get("Config") or {}).get("Labels") or {})
    source_revision_matches = labels.get("org.opencontainers.image.revision") == source_tree_commit
    producer_source_binding = all(
        manifest_file_digests.get(source_path) == _source_digest(repo_root / source_path)
        for source_path in RENDERER_SECURITY_PRODUCER_FILES
    )
    security_digest = _source_digest(security_path)
    inspect_digest = _source_digest(inspect_path)
    gate_digest = _source_digest(gate_path)
    gate_source_digest = str(gate_result.get("source_sha256") or "")
    evidence_exact_byte_chain = gate_source_digest in {
        security_digest,
        security_digest.removeprefix("sha256:"),
    }
    image_id = str(container.get("Image") or "")
    configured_image = str((container.get("Config") or {}).get("Image") or "")
    compose_image = str(labels.get("com.docker.compose.image") or "")
    immutable_image_identity = bool(
        re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
        and configured_image == image_id
        and compose_image == image_id
    )
    try:
        expected_attestation = _renderer_attestation(repo_root)
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError):
        expected_attestation = {"sourceTreeDigest": "", "contractDigest": ""}
    source_tree_digest_matches = (
        labels.get("io.presenton.authored-renderer.source-tree-digest")
        == expected_attestation["sourceTreeDigest"]
    )
    contract_digest_matches = (
        labels.get("io.presenton.authored-renderer.contract-digest")
        == expected_attestation["contractDigest"]
    )
    network_mode = (container.get("HostConfig") or {}).get("NetworkMode")
    boundaries = security.get("boundaries") or {}
    observed_boundaries = [
        boundaries.get("external_egress") or {},
        boundaries.get("loopback_egress") or {},
        boundaries.get("metadata_egress") or {},
    ]
    boundary_pass = all(
        isinstance(item.get("attempts"), int)
        and item["attempts"] > 0
        and item.get("blocked") == item["attempts"]
        and item.get("violations") == 0
        and item.get("observed") is True
        for item in observed_boundaries
    )
    cdp_blocked = sum(
        item.get("blocked", 0)
        for item in observed_boundaries
        if isinstance(item.get("blocked"), int)
    )
    deployment = security.get("deployment") or {}
    containment = security.get("containment") or {}
    gate_pass = gate_result.get("status") == "PASS"
    normal_success = deployment.get("product_worker_smoke") is True
    passed = bool(
        security.get("schema_version") == "authored-security-observation/v1"
        and source_revision_matches
        and producer_source_binding
        and evidence_exact_byte_chain
        and immutable_image_identity
        and source_tree_digest_matches
        and contract_digest_matches
        and network_mode == "none"
        and deployment.get("network_none") is True
        and containment.get("os_egress_deny") is True
        and containment.get("csp_enforced") is True
        and boundary_pass
        and normal_success
        and gate_pass
    )
    observation = {
        "osNetworkNamespaceObserved": network_mode == "none",
        "containerNetworkMode": network_mode,
        "egressSinkConnectionCount": sum(
            item.get("violations", 0)
            for item in observed_boundaries
            if isinstance(item.get("violations"), int)
        ),
        "cdpBlockedRequestCount": cdp_blocked,
        "sourceRevisionMatches": source_revision_matches,
        "producerSourceBindingObserved": producer_source_binding,
        "evidenceExactByteChainObserved": evidence_exact_byte_chain,
        "immutableImageIdentityObserved": immutable_image_identity,
        "imageId": image_id if immutable_image_identity else None,
        "sourceTreeDigest": expected_attestation["sourceTreeDigest"] or None,
        "sourceTreeDigestMatches": source_tree_digest_matches,
        "contractDigest": expected_attestation["contractDigest"] or None,
        "contractDigestMatches": contract_digest_matches,
        "sourceManifestDigest": release_candidate,
        "observationDigests": {
            "securityObservation": security_digest,
            "containerInspect": inspect_digest,
            "gateResult": gate_digest,
        },
        "pass": passed,
        "reason": None if passed else "renderer_security_observation_not_promotable",
    }
    safe_control = {
        "attemptedProductPath": "network-none-authored-renderer-product-worker",
        "expectedOutcome": "normal HTML rendered successfully",
        "productWorkerSmoke": normal_success,
        "pass": normal_success and passed,
        "reason": None if normal_success and passed else observation["reason"],
    }
    return observation, safe_control


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    fastapi_root = Path(__file__).resolve().parents[1]
    repo_root = fastapi_root.parents[1]
    git_head = _git_head(repo_root)
    source_manifest, manifest_file_digests = _load_source_manifest(
        args.source_manifest,
        repo_root=repo_root,
        release_candidate=args.release_candidate,
        source_tree_commit=git_head,
    )
    chrome_path = args.chrome_path or next(
        (
            path
            for path in (
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                "/usr/bin/google-chrome",
                "/usr/bin/chromium",
            )
            if Path(path).is_file()
        ),
        "",
    )
    if not chrome_path:
        raise RuntimeError("chrome_executable_unavailable")

    with tempfile.TemporaryDirectory(prefix="presenton-public-security-") as temporary:
        root = Path(temporary).resolve()
        object_root = root / "object-store"
        object_root.mkdir()
        signing_key = hashlib.sha256(
            f"presenton-public-security:{args.release_candidate}".encode()
        ).digest()
        key_file = root / "capability.key"
        key_file.write_bytes(signing_key)
        source_environment = _worker_source_environment(
            root=object_root,
            key_file=key_file,
            fastapi_root=fastapi_root,
            chrome_path=chrome_path,
        )
        worker_checks = _worker_boundary_checks(source_environment)
        html_cases, local_safe_control = await _html_corpus(
            root=object_root,
            signing_key=signing_key,
            source_environment=source_environment,
        )
        pptx_root = root / "pptx-corpus"
        pptx_root.mkdir()
        pptx_cases = _pptx_corpus(pptx_root)
        cross_owner, same_owner_control, owner_bound_ref = _cross_owner_artifact_checks(root)
        capability = _capability_checks(signing_key)

    html_pass = all(case["pass"] for case in html_cases)
    pptx_pass = all(case["pass"] for case in pptx_cases)
    cross_owner_pass = all(case["pass"] for case in cross_owner)
    capability_pass = all(case["pass"] for case in capability)
    output_binding = bool(
        same_owner_control
        and owner_bound_ref.get("ownerBound") is True
    )
    sessionless = worker_checks["sessionlessEnvironmentAllowlistVerified"]
    network_isolation, safe_html_control = _network_isolation_observation(
        args.renderer_security_evidence_dir,
        release_candidate=args.release_candidate,
        source_tree_commit=git_head,
        manifest_file_digests=manifest_file_digests,
        repo_root=repo_root,
    )
    safe_html_control["localSubprocessDiagnostic"] = local_safe_control
    network_none = network_isolation["pass"] is True
    status = "PASS" if all(
        (
            html_pass,
            pptx_pass,
            cross_owner_pass,
            capability_pass,
            output_binding,
            sessionless,
            network_none,
            safe_html_control["pass"],
        )
    ) else "FAIL"
    observations = {
        "safeHtmlControl": safe_html_control,
        "networkIsolation": network_isolation,
        "htmlCorpus": html_cases,
        "pptxCorpus": pptx_cases,
        "crossOwner": cross_owner,
        "capabilityBinding": capability,
        "workerBoundary": worker_checks,
        "ownerBoundArtifactControl": owner_bound_ref,
    }
    runner_path = RUNNER_PATH
    current_source_digests = {
        name: _source_digest(repo_root / name) for name in SECURITY_SOURCE_FILES
    }
    runner_digest = _source_digest(repo_root / runner_path)
    provenance = {
        "runnerPath": runner_path,
        "runnerSha256": runner_digest,
        "runnerIncludedInSourceManifest": (
            manifest_file_digests.get(runner_path) == runner_digest
        ),
        "gitHead": git_head,
        "sourceManifestDigest": args.release_candidate,
        "sourceManifestSchemaVersion": source_manifest["schemaVersion"],
        "pythonExecutable": Path(sys.executable).name,
        "observationDigest": _value_digest(observations),
        "sourceDigests": current_source_digests,
        "sourcesMatchSourceManifest": all(
            manifest_file_digests.get(name) == digest
            for name, digest in current_source_digests.items()
        ),
    }
    provenance["provenanceDigest"] = _value_digest(provenance)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "releaseCandidate": args.release_candidate,
        "evidenceClass": EVIDENCE_CLASS,
        "status": status,
        "maliciousHtmlCaseCount": len(html_cases),
        "maliciousPptxCaseCount": len(pptx_cases),
        "successfulNetworkAccessCount": sum(
            1 for case in html_cases if case["category"] == "network" and case["accessSucceeded"]
        ),
        "successfulFileAccessCount": sum(
            1 for case in html_cases if case["category"] == "file" and case["accessSucceeded"]
        ),
        "successfulMetadataAccessCount": sum(
            1 for case in html_cases if case["category"] == "metadata" and case["accessSucceeded"]
        ),
        "crossOwnerAttemptCount": len(cross_owner),
        "successfulCrossOwnerReadCount": sum(
            1 for case in cross_owner if case["operation"] == "read" and case["accessSucceeded"]
        ),
        "successfulCrossOwnerWriteCount": sum(
            1 for case in cross_owner if case["operation"] == "write" and case["accessSucceeded"]
        ),
        "sessionlessWorkerVerified": sessionless,
        "networkNoneRenderVerified": network_none,
        "operationDigestCapabilityBindingVerified": capability_pass,
        "artifactOwnerBindingVerified": output_binding and cross_owner_pass,
        "observations": observations,
        "provenance": provenance,
        "residualRisks": [
            *(
                []
                if network_none
                else [
                    "No same-RC OS network-none renderer observation was available; network-none and normal render controls remain failed closed."
                ]
            ),
            "The PPTX corpus covers OOXML ZIP/XML structural attacks; a production malware scanner signature corpus was outside this runner.",
        ],
    }


def _write_evidence(path: Path, report: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(payload, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-candidate", required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chrome-path", default="")
    parser.add_argument("--renderer-security-evidence-dir", type=Path)
    args = parser.parse_args()
    try:
        report = asyncio.run(_run(args))
    except Exception as error:
        report = {
            "schemaVersion": SCHEMA_VERSION,
            "releaseCandidate": args.release_candidate,
            "evidenceClass": EVIDENCE_CLASS,
            "status": "FAIL",
            "reason": f"{type(error).__name__}:{error}",
            "maliciousHtmlCaseCount": 0,
            "maliciousPptxCaseCount": 0,
            "successfulNetworkAccessCount": 0,
            "successfulFileAccessCount": 0,
            "successfulMetadataAccessCount": 0,
            "crossOwnerAttemptCount": 0,
            "successfulCrossOwnerReadCount": 0,
            "successfulCrossOwnerWriteCount": 0,
            "sessionlessWorkerVerified": False,
            "networkNoneRenderVerified": False,
            "operationDigestCapabilityBindingVerified": False,
            "artifactOwnerBindingVerified": False,
        }
    _write_evidence(args.output, report)
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
