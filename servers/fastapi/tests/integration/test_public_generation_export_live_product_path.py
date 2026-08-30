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

from hashlib import sha256
import json
import os
from pathlib import Path
import time
from typing import Any
import uuid

import pytest
import requests
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from models.sql.presentation import PresentationModel
from models.sql.public_artifact import PublicArtifact, PublicArtifactPublication
from models.sql.public_admission import PublicOperationAdmission
from models.sql.workspace import WorkspaceOperation, WorkspaceOperationEvent


_ENABLE_ENV = "PUBLIC_V2_WINDOWS_PRODUCT_E2E"
_BASE_URL_ENV = "PUBLIC_V2_WINDOWS_PRODUCT_E2E_BASE_URL"
_DATABASE_URL_ENV = "PUBLIC_V2_WINDOWS_PRODUCT_E2E_DATABASE_URL"
_USERNAME_ENV = "PUBLIC_V2_WINDOWS_PRODUCT_E2E_USERNAME"
_PASSWORD_ENV = "PUBLIC_V2_WINDOWS_PRODUCT_E2E_PASSWORD"
_TIMEOUT_ENV = "PUBLIC_V2_WINDOWS_PRODUCT_E2E_TIMEOUT_SECONDS"


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

    try:
        body: object = response.json()
    except ValueError:
        body = response.text[:2048]
    return {
        "method": response.request.method,
        "url": response.url,
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


def test_live_http_generation_then_fidelity_export_reaches_artifact_publication() -> None:
    if os.getenv(_ENABLE_ENV, "").strip() != "1":
        pytest.skip(f"set {_ENABLE_ENV}=1 for the owned live RC stack")

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
                "Create exactly 5 slides about trusted PPT exports. "
                "Each slide has one short title, exactly three bullets of at most five words, "
                "and one short numeric fact. Use no tables, charts, diagrams, footnotes, "
                "or extra prose."
            ),
            "slideCount": 5,
            "language": "English",
            "qualityPolicy": "release-v1",
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

    generation_operation, presentation, generation_admission, generation_events = _database_state(
        database_url, operation_id=generation_operation_id
    )
    _assert_claim_and_terminal_evidence(
        generation_operation, generation_admission, generation_events
    )

    # v2 currently requires sourceRevision but its public operation projection
    # does not expose it.  This harness reads the observed durable revision for
    # its next HTTP request; a user-facing revision projection remains a
    # separate public-contract gap and is not hidden by this assertion.
    _verify_export_method_contract(
        client, base_url=base_url, presentation_id=str(presentation.id)
    )
    export = client.post(
        f"{base_url}/api/v2/presentations/{presentation.id}/exports",
        headers={"Idempotency-Key": "live-rc-export-001"},
        json={
            "schemaVersion": "presentation-export-request/v2",
            "sourceRevision": presentation.revision,
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
    assert export_status["releaseReadiness"] in {"blocked", "ready"}
    assert len(export_status["artifacts"]) == 1
    artifact = export_status["artifacts"][0]

    export_operation, _presentation, export_admission, export_events = _database_state(
        database_url, operation_id=export_operation_id
    )
    _assert_claim_and_terminal_evidence(export_operation, export_admission, export_events)

    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with Session(engine) as session:
            publication = session.scalar(
                select(PublicArtifactPublication).where(
                    PublicArtifactPublication.operation_id == export_operation.id
                )
            )
            assert publication is not None
            assert publication.fencing_token == export_operation.fencing_token
            public_artifact = session.get(PublicArtifact, publication.artifact_id)
            assert public_artifact is not None
            assert public_artifact.lifecycle_state == "committed"
            assert f"sha256:{public_artifact.sha256}" == artifact["digest"]
            assert public_artifact.size_bytes == artifact["sizeBytes"]
            storage_key = public_artifact.storage_key
            output_digest = public_artifact.sha256
            output_size = public_artifact.size_bytes
    finally:
        engine.dispose()

    _assert_supervised_export_output(
        operation_id=export_operation_id,
        storage_key=storage_key,
        digest=output_digest,
        size_bytes=output_size,
    )

    # The v2 endpoint authorizes first and redirects to a short-lived MinIO/S3
    # signed URL.  Both a bounded Range read and a streaming full digest prove
    # that the published bytes, rather than a local compatibility path, won.
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

    streamed = requests.get(signed_url, stream=True, timeout=30)
    _require_status(streamed, 200)
    digest = sha256()
    size_bytes = 0
    for chunk in streamed.iter_content(chunk_size=1024 * 1024):
        if chunk:
            digest.update(chunk)
            size_bytes += len(chunk)
    assert f"sha256:{digest.hexdigest()}" == artifact["digest"]
    assert size_bytes == artifact["sizeBytes"]

    streamed = requests.get(signed_url, stream=True, timeout=30)
    _require_status(streamed, 200)
    digest = sha256()
    size_bytes = 0
    for chunk in streamed.iter_content(chunk_size=1024 * 1024):
        if chunk:
            digest.update(chunk)
            size_bytes += len(chunk)
    assert f"sha256:{digest.hexdigest()}" == artifact["digest"]
    assert size_bytes == artifact["sizeBytes"]


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
