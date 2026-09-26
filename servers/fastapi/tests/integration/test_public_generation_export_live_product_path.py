"""Opt-in Windows-local live HTTP proof for the public v2 Fidelity export path.

Unlike ``test_public_generation_export_product_path.py``, this test neither
injects a transport nor creates operation/admission/publication rows.  It
expects an already-started, isolated RC stack and only observes the resulting
PostgreSQL and object-store state after the public HTTP requests complete.

It is deliberately opt-in: a missing real provider, PostgreSQL, configured
artifact authority, or concrete supervised trusted-worker transport is a
blocked preflight, never a fake PASS.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import time
from typing import Any
import uuid
from urllib.parse import urlsplit

import pytest
import requests
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation import PresentationModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.public_artifact import PublicArtifact, PublicArtifactPublication
from models.sql.public_admission import PublicOperationAdmission
from models.sql.workspace import WorkspaceOperation, WorkspaceOperationEvent
from services.workspace_quality_operation_handler import (
    validate_export_release_review_receipt,
)


_ENABLE_ENV = "PUBLIC_V2_WINDOWS_PRODUCT_E2E"
_BASE_URL_ENV = "PUBLIC_V2_WINDOWS_PRODUCT_E2E_BASE_URL"
_DATABASE_URL_ENV = "PUBLIC_V2_WINDOWS_PRODUCT_E2E_DATABASE_URL"
_USERNAME_ENV = "PUBLIC_V2_WINDOWS_PRODUCT_E2E_USERNAME"
_PASSWORD_ENV = "PUBLIC_V2_WINDOWS_PRODUCT_E2E_PASSWORD"
_TIMEOUT_ENV = "PUBLIC_V2_WINDOWS_PRODUCT_E2E_TIMEOUT_SECONDS"
_ARTIFACT_DIR_ENV = "PUBLIC_V2_WINDOWS_PRODUCT_E2E_ARTIFACT_DIR"
_GENERATION_QUALITY_POLICY_ENV = (
    "PUBLIC_V2_WINDOWS_PRODUCT_E2E_GENERATION_QUALITY_POLICY"
)
_DEVAX_KOREAN_ENV = "PUBLIC_V2_WINDOWS_PRODUCT_E2E_DEVAX_KOREAN"


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        pytest.fail(f"live_product_path_preflight_missing:{name}")
    return value


def _timeout_seconds() -> float:
    raw = os.getenv(_TIMEOUT_ENV, "900").strip()
    try:
        value = float(raw)
    except ValueError:
        pytest.fail("live_product_path_preflight_invalid_timeout")
    if value <= 0:
        pytest.fail("live_product_path_preflight_invalid_timeout")
    return value


def _json(response: requests.Response) -> dict[str, Any]:
    try:
        value = response.json()
    except ValueError as error:
        raise AssertionError(f"unexpected_non_json_response:{response.status_code}") from error
    assert isinstance(value, dict)
    return value


def _http_evidence(response: requests.Response) -> dict[str, Any]:
    """Return safe method/path/status/body evidence for an RC route failure."""

    parsed = urlsplit(response.url)
    if parsed.query:
        body: object = "signed_response_body_not_logged"
    else:
        try:
            body = response.json()
        except ValueError:
            body = response.text[:2048]
    return {
        "method": response.request.method,
        "path": parsed.path,
        "statusCode": response.status_code,
        "allow": response.headers.get("Allow"),
        "body": body,
    }


def _require_status(response: requests.Response, expected: int) -> None:
    assert response.status_code == expected, _http_evidence(response)


def _verify_export_method_contract(
    client: requests.Session, *, base_url: str, presentation_id: str
) -> None:
    """Persist proof that export submission is POST, not a GET status request."""

    url = f"{base_url}/api/v2/presentations/{presentation_id}/exports"
    probe = client.get(url, timeout=30)
    evidence = _http_evidence(probe)
    print("public_v2_export_route_probe=" + json.dumps(evidence, sort_keys=True))
    _require_status(probe, 405)
    assert "POST" in (probe.headers.get("Allow") or "")


def _poll_terminal(
    client: requests.Session,
    *,
    base_url: str,
    operation_id: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(
            f"{base_url}/api/v2/operations/{operation_id}", timeout=30
        )
        _require_status(response, 200)
        status = _json(response)
        if status.get("status") in {"succeeded", "failed", "cancelled"}:
            return status
        time.sleep(1)
    raise AssertionError("live_product_path_operation_timeout")


def _database_state(
    database_url: str,
    *,
    operation_id: str,
) -> tuple[
    WorkspaceOperation,
    PresentationModel,
    PublicOperationAdmission,
    list[WorkspaceOperationEvent],
]:
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        try:
            parsed_operation_id = uuid.UUID(operation_id)
        except ValueError as error:
            raise AssertionError("live_product_path_operation_id_not_uuid") from error
        with Session(engine) as session:
            operation = session.scalar(
                select(WorkspaceOperation).where(
                    WorkspaceOperation.id == parsed_operation_id
                )
            )
            assert operation is not None
            presentation = session.get(PresentationModel, operation.presentation_id)
            assert presentation is not None
            admission = session.scalar(
                select(PublicOperationAdmission).where(
                    PublicOperationAdmission.operation_id == operation.id
                )
            )
            assert admission is not None
            events = list(
                session.scalars(
                    select(WorkspaceOperationEvent)
                    .where(WorkspaceOperationEvent.operation_id == operation.id)
                    .order_by(WorkspaceOperationEvent.sequence)
                )
            )
            session.expunge(operation)
            session.expunge(presentation)
            session.expunge(admission)
            for event in events:
                session.expunge(event)
            return operation, presentation, admission, events
    finally:
        engine.dispose()


def _login_live_client(base_url: str, *, username: str, password: str) -> requests.Session:
    client = requests.Session()
    login = client.post(
        f"{base_url}/api/v1/auth/login",
        json={"username": username, "password": password},
        timeout=30,
    )
    _require_status(login, 200)
    assert _json(login).get("authenticated") is True
    return client


def _assert_claim_and_terminal_evidence(
    operation: WorkspaceOperation,
    admission: PublicOperationAdmission,
    events: list[WorkspaceOperationEvent],
) -> None:
    assert operation.state == "succeeded"
    assert operation.attempt_number >= 1
    assert operation.fencing_token >= 1
    assert operation.started_at is not None
    assert operation.completed_at is not None
    assert admission.state == "succeeded"
    assert admission.fence == operation.fencing_token
    assert any(event.event_type == "operation.progress" for event in events)
    assert any(event.event_type == "operation.result" for event in events)


def _assert_supervised_export_output(
    *, operation_id: str, storage_key: str, digest: str, size_bytes: int
) -> None:
    staging_root = Path(_required("PUBLIC_TRUSTED_WORKER_STAGING_ROOT"))
    assert staging_root.is_absolute()
    candidates = list(
        (staging_root / operation_id / "export").glob(
            f"fence-*/output/{storage_key}"
        )
    )
    output = next((candidate for candidate in candidates if candidate.is_file()), None)
    assert output is not None, "live_product_path_supervised_export_output_missing"
    digest_state = sha256()
    observed_size = 0
    with output.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest_state.update(chunk)
            observed_size += len(chunk)
    assert digest_state.hexdigest() == digest
    assert observed_size == size_bytes


def _assert_publication_artifact(
    database_url: str,
    *,
    operation: WorkspaceOperation,
    presentation: PresentationModel,
    artifact: dict[str, Any],
) -> PublicArtifact:
    theme = presentation.theme if isinstance(presentation.theme, dict) else {}
    quality_gate = theme.get("authoredQualityGate")
    receipt = (
        quality_gate.get("qualityReceiptV3")
        if isinstance(quality_gate, dict)
        else None
    )
    assert isinstance(receipt, dict), "live_product_path_quality_receipt_missing"
    receipt_digest = receipt.get("receiptDigest")
    assert isinstance(receipt_digest, str) and receipt_digest.startswith("sha256:")
    assert len(receipt_digest) == 71
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with Session(engine) as session:
            publication = session.scalar(
                select(PublicArtifactPublication).where(
                    PublicArtifactPublication.operation_id == operation.id
                )
            )
            assert publication is not None
            assert publication.fencing_token == operation.fencing_token
            request_payload = (
                operation.request_payload
                if isinstance(operation.request_payload, dict)
                else {}
            )
            review_operation_id = uuid.UUID(
                str(request_payload.get("release_review_operation_id"))
            )
            review_operation = session.get(WorkspaceOperation, review_operation_id)
            assert review_operation is not None, "live_product_path_review_missing"
            review_result = (
                review_operation.result
                if isinstance(review_operation.result, dict)
                else {}
            )
            review_receipt = review_result.get("release_review_receipt")
            assert isinstance(
                review_receipt, dict
            ), "live_product_path_review_receipt_missing"
            review_digest = validate_export_release_review_receipt(
                review_receipt,
                operation_id=review_operation.id,
                source_revision=int(review_operation.expected_revision),
                result_revision=int(review_operation.result_revision),
            )
            base_quality_digest = review_receipt.get("baseQualityReceiptDigest")
            assert (
                isinstance(base_quality_digest, str)
                and base_quality_digest.startswith("sha256:")
                and len(base_quality_digest) == 71
            )
            if review_receipt.get("baseEvidenceKind") == "generation":
                assert base_quality_digest == receipt_digest
            expected_publication_digest = sha256(
                (
                    base_quality_digest.removeprefix("sha256:")
                    + ":"
                    + review_digest
                ).encode("ascii")
            ).hexdigest()
            assert publication.quality_digest == expected_publication_digest
            public_artifact = session.get(PublicArtifact, publication.artifact_id)
            assert public_artifact is not None
            assert public_artifact.lifecycle_state == "committed"
            assert f"sha256:{public_artifact.sha256}" == artifact["digest"]
            assert public_artifact.size_bytes == artifact["sizeBytes"]
            session.expunge(public_artifact)
            return public_artifact
    finally:
        engine.dispose()


def _assert_generation_checkpoint_binding(
    database_url: str,
    *,
    operation: WorkspaceOperation,
    presentation: PresentationModel,
) -> None:
    """Observe the durable authored job/checkpoint ownership after terminal publication."""
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with Session(engine) as session:
            jobs = list(
                session.scalars(
                    select(PresentationGenerationJob).where(
                        PresentationGenerationJob.workspace_operation_id == operation.id,
                        PresentationGenerationJob.presentation_id == presentation.id,
                    )
                )
            )
            assert len(jobs) == 1, "live_product_path_generation_job_binding_missing"
            job = jobs[0]
            assert job.workspace_operation_id == operation.id
            assert job.presentation_id == presentation.id
            assert job.state == "published"
            assert job.attempt_number == operation.attempt_number
            # Terminal publication intentionally clears the job's raw lease token.
            assert job.attempt_token is None

            checkpoints = list(
                session.scalars(
                    select(AuthoredSlideCheckpoint)
                    .where(AuthoredSlideCheckpoint.job_id == job.id)
                    .order_by(AuthoredSlideCheckpoint.slide_index)
                )
            )
            assert len(checkpoints) == presentation.n_slides
            assert [checkpoint.slide_index for checkpoint in checkpoints] == list(
                range(presentation.n_slides)
            )
            assert all(
                checkpoint.render_status == "completed"
                and checkpoint.persist_status == "completed"
                for checkpoint in checkpoints
            )
            checkpoint_tokens = {checkpoint.attempt_token for checkpoint in checkpoints}
            assert None not in checkpoint_tokens and len(checkpoint_tokens) == 1
    finally:
        engine.dispose()


def _download_published_artifact(
    client: requests.Session,
    *,
    base_url: str,
    artifact: dict[str, Any],
    operation_id: str,
) -> dict[str, Any]:
    """Prove 303/206/full digest once and retain a COM-consumable artifact."""

    artifact_root = Path(_required(_ARTIFACT_DIR_ENV))
    assert artifact_root.is_absolute()
    artifact_root.mkdir(parents=True, exist_ok=True)
    suffix = ".pptx" if artifact["kind"] == "presentation-pptx" else ".pdf"
    target = artifact_root / f"{operation_id}{suffix}"
    assert not target.exists(), "live_product_path_artifact_target_already_exists"

    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    grant = client.get(
        f"{base_url}{artifact['download']}", allow_redirects=False, timeout=30
    )
    _require_status(grant, 303)
    redirected_at = datetime.now(timezone.utc).isoformat()
    signed_url = grant.headers.get("Location")
    assert signed_url and signed_url.startswith(("http://", "https://"))
    ranged = requests.get(
        signed_url,
        headers={"Range": "bytes=0-1023"},
        stream=True,
        timeout=30,
    )
    _require_status(ranged, 206)
    assert ranged.headers.get("Content-Range", "").startswith("bytes 0-")
    range_body = b"".join(ranged.iter_content(chunk_size=64 * 1024))
    assert len(range_body) == min(1024, artifact["sizeBytes"])
    range_completed_at = datetime.now(timezone.utc).isoformat()

    streamed = requests.get(signed_url, stream=True, timeout=30)
    _require_status(streamed, 200)
    digest = sha256()
    size_bytes = 0
    with target.open("xb") as output:
        for chunk in streamed.iter_content(chunk_size=1024 * 1024):
            if chunk:
                output.write(chunk)
                digest.update(chunk)
                size_bytes += len(chunk)
    assert f"sha256:{digest.hexdigest()}" == artifact["digest"]
    assert size_bytes == artifact["sizeBytes"]
    completed_at = datetime.now(timezone.utc).isoformat()
    return {
        "operationId": operation_id,
        "artifactId": artifact["artifactId"],
        "kind": artifact["kind"],
        "digest": artifact["digest"],
        "sizeBytes": artifact["sizeBytes"],
        "path": str(target),
        "downloadEvidence": {
            "redirectStatus": grant.status_code,
            "rangeStatus": ranged.status_code,
            "fullStatus": streamed.status_code,
            "startedAt": started_at,
            "redirectedAt": redirected_at,
            "rangeCompletedAt": range_completed_at,
            "completedAt": completed_at,
            "durationMillis": round((time.monotonic() - started) * 1000, 3),
            "locationDigest": "sha256:" + sha256(signed_url.encode()).hexdigest(),
            "contentRange": ranged.headers["Content-Range"],
            "rangeBytes": len(range_body),
            "rangeDigest": "sha256:" + sha256(range_body).hexdigest(),
            "fullDigest": artifact["digest"],
        },
    }


def _source_manifest_observation() -> dict[str, Any]:
    """Bind listed source bytes at actual observation time, never retroactively."""
    raw_path = os.getenv("PUBLIC_V2_RC_SOURCE_MANIFEST_PATH", "").strip()
    if not raw_path:
        return {"status": "not-collected"}
    repo = Path(__file__).resolve().parents[4]
    source_path = Path(raw_path).resolve(strict=True)
    assert source_path.is_relative_to(repo / "artifacts")
    encoded = source_path.read_bytes()
    manifest = json.loads(encoded)
    assert manifest["schemaVersion"] == "public-service-source-manifest/v1"
    for item in manifest["files"]:
        source = (repo / item["path"]).resolve(strict=True)
        assert source.is_relative_to(repo)
        assert "sha256:" + sha256(source.read_bytes()).hexdigest() == item["sha256"], item["path"]
    return {
        "status": "listed-source-bytes-verified",
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "releaseCandidate": "sha256:" + sha256(encoded).hexdigest(),
        "sourceCommit": manifest["sourceTreeCommit"],
        "manifestPath": str(source_path),
        "fileCount": len(manifest["files"]),
    }


def _terminal_database_observation(operation, admission, events) -> dict[str, Any]:
    def instant(value):
        return value.isoformat() if value is not None else None

    return {
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "operationId": str(operation.id), "state": operation.state,
        "attemptNumber": operation.attempt_number, "fencingToken": operation.fencing_token,
        "startedAt": instant(operation.started_at), "completedAt": instant(operation.completed_at),
        "resultRevision": operation.result_revision, "admissionState": admission.state,
        "events": [{"sequence": event.sequence, "type": event.event_type,
                    "payloadDigest": "sha256:" + sha256(json.dumps(event.payload, sort_keys=True,
                        ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()}
                   for event in events],
        "workerDispatchEvidence": (operation.result or {}).get("_worker_dispatch_evidence", []),
        "workerDispatchEvidenceGaps": (operation.result or {}).get("_worker_dispatch_evidence_gaps", []),
    }


def _assert_verified_public_quality(status: dict[str, Any]) -> None:
    """Require bound checker evidence, not merely a successful export state."""

    quality = status.get("qualityReceipt")
    assert isinstance(quality, dict), "live_product_path_quality_receipt_missing"
    assert quality["schemaVersion"] == "quality-receipt/v3"
    assert quality["operationId"] == status["operationId"]
    assert quality["presentationId"] == status["presentationId"]
    assert quality["sourceRevision"] == status["sourceRevision"]
    assert len(status["artifacts"]) == 1
    assert quality["artifactDigest"] == status["artifacts"][0]["digest"]
    for name in (
        "contentBrief", "authoring", "geometryFont", "imageContentIntegrity",
        "visualReview", "performance", "nativeCoverageOoxml",
    ):
        domain = quality[name]
        assert isinstance(domain["required"], bool)
        if name in {"authoring", "geometryFont"}:
            assert domain["required"] is True
        if domain["required"]:
            assert domain["status"] == "passed", {"domain": name, "result": domain}
        if domain["status"] == "passed":
            evidence = domain["evidence"]
            assert evidence["checkerVersion"]
            assert evidence["measurements"] and evidence["thresholds"]
            for field in ("inputDigest", "outputDigest"):
                digest = evidence[field]
                assert isinstance(digest, str) and digest.startswith("sha256:")
                assert len(digest) == 71
                assert all(char in "0123456789abcdef" for char in digest[7:])
    # This is the artifact's required-checker verdict, not operational release
    # promotion, external isolation proof, or deployment authorization.
    assert quality["releaseReadiness"] == "ready"
    assert status["releaseReadiness"] == "ready"


def test_live_http_generation_then_fidelity_export_reaches_artifact_publication() -> None:
    if os.getenv(_ENABLE_ENV, "").strip() != "1":
        pytest.skip(f"set {_ENABLE_ENV}=1 for the owned live RC stack")

    devax_korean = os.getenv(_DEVAX_KOREAN_ENV, "").strip() == "1"

    source_before = _source_manifest_observation()

    # The launcher supplies random credentials through its child environment and
    # deliberately never emits either the password or the session cookie.
    base_url = _required(_BASE_URL_ENV).rstrip("/")
    database_url = _required(_DATABASE_URL_ENV)
    username = _required(_USERNAME_ENV)
    password = _required(_PASSWORD_ENV)
    timeout_seconds = _timeout_seconds()

    client = _login_live_client(base_url, username=username, password=password)

    generation = client.post(
        f"{base_url}/api/v2/presentations/generations",
        headers={"Idempotency-Key": "live-rc-generation-001"},
        json={
            "schemaVersion": "presentation-generation-request/v2",
            "content": (
                "DevAX SI 구축 제안서를 한국어로 정확히 5장 작성한다. 대상은 기업 CIO와 사업 책임자다. "
                "화이트 바탕, 블루·네이비 포인트, 얇은 시안 구분선, 넓은 여백, 큰 한 줄 제목의 "
                "기업 제안서 스타일을 사용한다. 1장 DevAX SI 구축 제안, 2장 기업 AI 전환 과제, "
                "3장 요구사항부터 테스트까지의 추적 체계, 4장 착수·구축·확산 계획, 5장 기대효과와 "
                "파일럿 승인 제안으로 구성한다. 각 장에는 짧은 제목 하나, 7단어 이내의 짧은 근거 "
                "정확히 3개, 짧은 가정 수치 하나만 넣는다. 표, 차트, 다이어그램, 각주, 긴 문장, "
                "추가 설명은 사용하지 않는다. 수치는 반드시 '가정'이라고 표시한다."
            ) if devax_korean else (
                "Create exactly 5 slides about trusted PPT exports. "
                "Each slide has exactly one title of at most 30 characters, exactly three bullets of at most five words, "
                "and one short numeric fact. Use no tables, charts, diagrams, footnotes, "
                "subtitles, hero copy, or extra prose."
            ),
            "slideCount": 5,
            "language": "Korean" if devax_korean else "English",
            "qualityPolicy": os.getenv(
                _GENERATION_QUALITY_POLICY_ENV, "release-v1"
            ).strip()
            or "release-v1",
        },
        timeout=30,
    )
    _require_status(generation, 202)
    generation_receipt = _json(generation)
    generation_operation_id = generation_receipt["operationId"]
    generation_status = _poll_terminal(
        client,
        base_url=base_url,
        operation_id=generation_operation_id,
        timeout_seconds=timeout_seconds,
    )
    assert generation_status["status"] == "succeeded", generation_status.get("error")
    assert generation_status.get("promotionReady") is False
    assert generation_status.get("deploymentAuthorized") is False

    generation_operation, presentation, generation_admission, generation_events = _database_state(
        database_url, operation_id=generation_operation_id
    )
    _assert_claim_and_terminal_evidence(
        generation_operation, generation_admission, generation_events
    )
    _assert_generation_checkpoint_binding(
        database_url,
        operation=generation_operation,
        presentation=presentation,
    )
    generation_result = (
        generation_operation.result
        if isinstance(generation_operation.result, dict)
        else {}
    )
    if os.getenv("DECK_IR_GENERATION_ENABLED", "").strip().lower() in {
        "1", "true", "yes", "on"
    }:
        deck_ir_evidence = generation_result.get("deckIr")
        assert isinstance(deck_ir_evidence, dict), "deck_ir_product_evidence_missing"
        assert deck_ir_evidence.get("strategy") == "deck-ir"
        assert deck_ir_evidence.get("slideCount") == 5
        assert 1 <= deck_ir_evidence.get("providerLogicalCalls", 0) <= 4
        for key in (
            "strategyReceiptDigest",
            "deckIrDigest",
            "detachedCheckpointDigest",
            "grammarDigest",
            "candidateSelectionReceiptDigest",
        ):
            value = deck_ir_evidence.get(key)
            assert isinstance(value, str) and value.startswith("sha256:"), key
        assert len(deck_ir_evidence.get("browserClosureReceiptDigests", [])) == 5
        assert isinstance(
            generation_result.get("generation_timing_receipt"), dict
        ), "deck_ir_generation_timing_receipt_missing"

    public_source_revision = generation_status.get("sourceRevision")
    assert isinstance(public_source_revision, int)
    assert public_source_revision >= 0
    assert public_source_revision == presentation.revision
    _verify_export_method_contract(
        client, base_url=base_url, presentation_id=str(presentation.id)
    )
    export = client.post(
        f"{base_url}/api/v2/presentations/{presentation.id}/exports",
        headers={"Idempotency-Key": "live-rc-export-001"},
        json={
            "schemaVersion": "presentation-export-request/v2",
            "sourceRevision": public_source_revision,
            "output": {"format": "pptx", "mode": "fidelity-raster"},
            "qualityPolicy": "release-v1",
        },
        timeout=30,
    )
    _require_status(export, 202)
    export_operation_id = _json(export)["operationId"]
    export_status = _poll_terminal(
        client,
        base_url=base_url,
        operation_id=export_operation_id,
        timeout_seconds=timeout_seconds,
    )
    assert export_status["status"] == "succeeded", export_status.get("error")
    _assert_verified_public_quality(export_status)
    assert len(export_status["artifacts"]) == 1
    assert export_status["executionReceipt"]["publication"] == "published"
    assert export_status["executionReceipt"]["export"] == "completed"
    pptx_timing = export_status["executionReceipt"]["timing"]
    assert pptx_timing["slideCount"] == 5
    assert pptx_timing["renderExecutionCount"] == 5
    assert pptx_timing["renderBatchExecutionCount"] == 1
    assert pptx_timing["chromiumProcessLaunchCount"] == 1
    assert pptx_timing["browserReuseCount"] == 4
    artifact = export_status["artifacts"][0]

    export_operation, reviewed_presentation, export_admission, export_events = _database_state(
        database_url, operation_id=export_operation_id
    )
    _assert_claim_and_terminal_evidence(export_operation, export_admission, export_events)
    reviewed_source_revision = export_status.get("sourceRevision")
    assert isinstance(reviewed_source_revision, int)
    assert reviewed_source_revision == reviewed_presentation.revision

    public_artifact = _assert_publication_artifact(
        database_url,
        operation=export_operation,
        presentation=reviewed_presentation,
        artifact=artifact,
    )

    _assert_supervised_export_output(
        operation_id=export_operation_id,
        storage_key=public_artifact.storage_key,
        digest=public_artifact.sha256,
        size_bytes=public_artifact.size_bytes,
    )

    # Each format proves one authorized 303, one bounded 206, and one full
    # streaming digest. The full stream is retained for the independent
    # PowerPoint/readback collector without exposing the signed URL.
    pptx_evidence = _download_published_artifact(
        client,
        base_url=base_url,
        artifact=artifact,
        operation_id=export_operation_id,
    )
    pptx_evidence["executionReceipt"] = export_status.get("executionReceipt")
    pptx_evidence["sourceRevision"] = reviewed_source_revision
    pptx_evidence["qualityReceipt"] = export_status["qualityReceipt"]

    pdf_export = client.post(
        f"{base_url}/api/v2/presentations/{presentation.id}/exports",
        headers={"Idempotency-Key": "live-rc-export-pdf-001"},
        json={
            "schemaVersion": "presentation-export-request/v2",
            "sourceRevision": reviewed_source_revision,
            "output": {"format": "pdf", "mode": "fidelity-raster"},
            "qualityPolicy": "release-v1",
        },
        timeout=30,
    )
    _require_status(pdf_export, 202)
    pdf_operation_id = _json(pdf_export)["operationId"]
    pdf_status = _poll_terminal(
        client,
        base_url=base_url,
        operation_id=pdf_operation_id,
        timeout_seconds=timeout_seconds,
    )
    assert pdf_status["status"] == "succeeded", pdf_status.get("error")
    _assert_verified_public_quality(pdf_status)
    assert len(pdf_status["artifacts"]) == 1
    assert pdf_status["executionReceipt"]["publication"] == "published"
    assert pdf_status["executionReceipt"]["export"] == "completed"
    pdf_timing = pdf_status["executionReceipt"]["timing"]
    assert pdf_timing["slideCount"] == 5
    assert pdf_timing["renderExecutionCount"] == 0
    assert pdf_timing["renderBatchExecutionCount"] == 0
    assert pdf_timing["chromiumProcessLaunchCount"] == 0
    assert pdf_timing["browserReuseCount"] == 0
    assert pdf_timing["renderCheckpointReuseCount"] == 5
    pdf_artifact = pdf_status["artifacts"][0]
    assert pdf_artifact["kind"] == "presentation-pdf"
    pdf_operation, pdf_presentation, pdf_admission, pdf_events = _database_state(
        database_url, operation_id=pdf_operation_id
    )
    _assert_claim_and_terminal_evidence(
        pdf_operation, pdf_admission, pdf_events
    )
    _assert_publication_artifact(
        database_url,
        operation=pdf_operation,
        presentation=pdf_presentation,
        artifact=pdf_artifact,
    )
    pdf_evidence = _download_published_artifact(
        client,
        base_url=base_url,
        artifact=pdf_artifact,
        operation_id=pdf_operation_id,
    )
    pdf_evidence["executionReceipt"] = pdf_status.get("executionReceipt")
    pdf_evidence["sourceRevision"] = pdf_status.get("sourceRevision")
    pdf_evidence["qualityReceipt"] = pdf_status["qualityReceipt"]

    # A distinct operation must publish its own authorized ref while reusing
    # the completed canonical PPTX bytes without another render/export worker.
    repeated_export = client.post(
        f"{base_url}/api/v2/presentations/{presentation.id}/exports",
        headers={"Idempotency-Key": "live-rc-export-pptx-reuse-001"},
        json={
            "schemaVersion": "presentation-export-request/v2",
            "sourceRevision": reviewed_source_revision,
            "output": {"format": "pptx", "mode": "fidelity-raster"},
            "qualityPolicy": "release-v1",
        },
        timeout=30,
    )
    _require_status(repeated_export, 202)
    repeated_operation_id = _json(repeated_export)["operationId"]
    assert repeated_operation_id != export_operation_id
    repeated_status = _poll_terminal(
        client,
        base_url=base_url,
        operation_id=repeated_operation_id,
        timeout_seconds=timeout_seconds,
    )
    assert repeated_status["status"] == "succeeded", repeated_status.get("error")
    assert repeated_status["sourceRevision"] == reviewed_source_revision
    _assert_verified_public_quality(repeated_status)
    assert repeated_status["executionReceipt"]["publication"] == "published"
    assert repeated_status["executionReceipt"]["export"] == "completed"
    repeated_timing = repeated_status["executionReceipt"]["timing"]
    assert repeated_timing["slideCount"] == 5
    assert repeated_timing["renderExecutionCount"] == 0
    assert repeated_timing["renderBatchExecutionCount"] == 0
    assert repeated_timing["chromiumProcessLaunchCount"] == 0
    assert repeated_timing["renderCheckpointReuseCount"] == 5
    assert repeated_timing["exportWorkerMillis"] == 0
    assert len(repeated_status["artifacts"]) == 1
    repeated_artifact = repeated_status["artifacts"][0]
    assert repeated_artifact["artifactId"] != artifact["artifactId"]
    assert repeated_artifact["digest"] == artifact["digest"]
    assert repeated_artifact["sizeBytes"] == artifact["sizeBytes"]
    repeated_operation, repeated_presentation, repeated_admission, repeated_events = _database_state(
        database_url, operation_id=repeated_operation_id
    )
    _assert_claim_and_terminal_evidence(
        repeated_operation, repeated_admission, repeated_events
    )
    assert repeated_operation.result["worker_execution_count"] == 0
    repeated_public_artifact = _assert_publication_artifact(
        database_url,
        operation=repeated_operation,
        presentation=repeated_presentation,
        artifact=repeated_artifact,
    )
    assert repeated_public_artifact.storage_key == public_artifact.storage_key
    repeated_evidence = _download_published_artifact(
        client,
        base_url=base_url,
        artifact=repeated_artifact,
        operation_id=repeated_operation_id,
    )
    repeated_evidence["executionReceipt"] = repeated_status["executionReceipt"]
    repeated_evidence["sourceRevision"] = reviewed_source_revision
    repeated_evidence["reuseOfOperationId"] = export_operation_id
    repeated_evidence["qualityReceipt"] = repeated_status["qualityReceipt"]
    assert (repeated_status["qualityReceipt"]["authoring"]["evidence"]["inputDigest"]
            != export_status["qualityReceipt"]["authoring"]["evidence"]["inputDigest"])
    source_after = _source_manifest_observation()
    assert source_after.get("releaseCandidate") == source_before.get("releaseCandidate")

    evidence_manifest = {
        "schemaVersion": "public-fidelity-export-evidence/v1",
        "generationOperationId": generation_operation_id,
        "presentationId": str(presentation.id),
        "generationSourceRevision": public_source_revision,
        "generationExecutionReceipt": generation_status.get("executionReceipt"),
        "generationTimingReceipt": generation_result.get(
            "generation_timing_receipt"
        ),
        "generationDeckIr": generation_result.get("deckIr"),
        "generationQualityReceipt": generation_result.get(
            "generationQualityReceipt"
        ),
        "requirementCoverageReceipt": generation_result.get(
            "requirementCoverageReceipt"
        ),
        "sourceRevision": reviewed_source_revision,
        "slideCount": 5,
        "exports": [pptx_evidence, pdf_evidence, repeated_evidence],
        "powerPointReadback": "pending-independent-collector",
        "scope": "windows-local-supervised-development",
        "promotionReady": False,
        "deploymentAuthorized": False,
        "sourceBefore": source_before,
        "sourceAfter": source_after,
        "databaseObservations": [
            _terminal_database_observation(generation_operation, generation_admission, generation_events),
            _terminal_database_observation(export_operation, export_admission, export_events),
            _terminal_database_observation(pdf_operation, pdf_admission, pdf_events),
            _terminal_database_observation(repeated_operation, repeated_admission, repeated_events),
        ],
    }
    manifest_path = (
        Path(_required(_ARTIFACT_DIR_ENV))
        / f"manifest-{generation_operation_id}.json"
    )
    assert not manifest_path.exists(), "live_product_path_manifest_already_exists"
    manifest_path.write_text(
        json.dumps(evidence_manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print("public_v2_artifact_manifest=" + str(manifest_path))


def test_live_http_fidelity_export_only_uses_existing_presentation() -> None:
    """Permit an export-only RC proof when a real provider is unavailable.

    The caller supplies an already-generated presentation in the isolated
    PostgreSQL fixture.  The test still uses only the public v2 HTTP export and
    download APIs; direct SQL is post-condition observation, not setup.
    """

    if os.getenv(_ENABLE_ENV, "").strip() != "1" or os.getenv(
        "PUBLIC_V2_WINDOWS_PRODUCT_E2E_EXPORT_ONLY", ""
    ).strip() != "1":
        pytest.skip("export-only live RC mode is not enabled")

    base_url = _required(_BASE_URL_ENV).rstrip("/")
    database_url = _required(_DATABASE_URL_ENV)
    username = _required(_USERNAME_ENV)
    password = _required(_PASSWORD_ENV)
    presentation_id = _required("PUBLIC_V2_WINDOWS_PRODUCT_E2E_EXISTING_PRESENTATION_ID")
    raw_revision = _required("PUBLIC_V2_WINDOWS_PRODUCT_E2E_EXISTING_SOURCE_REVISION")
    try:
        source_revision = int(raw_revision)
    except ValueError:
        pytest.fail("live_product_path_preflight_invalid_existing_source_revision")
    if source_revision < 0:
        pytest.fail("live_product_path_preflight_invalid_existing_source_revision")

    client = _login_live_client(base_url, username=username, password=password)
    _verify_export_method_contract(
        client, base_url=base_url, presentation_id=presentation_id
    )
    response = client.post(
        f"{base_url}/api/v2/presentations/{presentation_id}/exports",
        headers={"Idempotency-Key": "live-rc-export-only-001"},
        json={
            "schemaVersion": "presentation-export-request/v2",
            "sourceRevision": source_revision,
            "output": {"format": "pptx", "mode": "fidelity-raster"},
            "qualityPolicy": "interactive-v1",
        },
        timeout=30,
    )
    _require_status(response, 202)
    status = _poll_terminal(
        client,
        base_url=base_url,
        operation_id=_json(response)["operationId"],
        timeout_seconds=_timeout_seconds(),
    )
    assert status["status"] == "succeeded", status.get("error")
    assert len(status["artifacts"]) == 1
    artifact = status["artifacts"][0]

    operation, presentation, admission, events = _database_state(
        database_url, operation_id=status["operationId"]
    )
    _assert_claim_and_terminal_evidence(operation, admission, events)
    assert str(presentation.id) == presentation_id
    assert presentation.revision == source_revision

    grant = client.get(
        f"{base_url}{artifact['download']}", allow_redirects=False, timeout=30
    )
    _require_status(grant, 303)
    signed_url = grant.headers.get("Location")
    assert signed_url and signed_url.startswith(("http://", "https://"))
    ranged = requests.get(
        signed_url,
        headers={"Range": "bytes=0-1023"},
        stream=True,
        timeout=30,
    )
    _require_status(ranged, 206)
    assert ranged.headers.get("Content-Range", "").startswith("bytes 0-")
    assert sum(len(chunk) for chunk in ranged.iter_content(chunk_size=64 * 1024)) > 0
