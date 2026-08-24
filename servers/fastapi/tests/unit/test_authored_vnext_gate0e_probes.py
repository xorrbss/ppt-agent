from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
import uuid

import pytest

from models.sql.authored_vnext_repository import (
    AuthoredVNextExportArtifactReceipt,
    AuthoredVNextExportJob,
    AuthoredVNextPublicationPointer,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from services.authored_vnext.generation.gate0e_probes import (
    Gate0eBaseline,
    Gate0eProbeError,
    Gate0eSlideBaseline,
    _load_current_baseline,
    evaluate_gate0e_cases,
)


def test_gate0e_manifest_cases_compare_malformed_payloads_to_current_baseline() -> None:
    digest = lambda character: "sha256:" + character * 64
    baseline = Gate0eBaseline(
        owner_scope="owner:current",
        tenant_id="tenant:current",
        deck_id="deck:semantic-0123456789abcdef0123456789abcdef",
        deck_revision=3,
        deck_digest=digest("1"),
        scene_digest=digest("2"),
        slides=(
            Gate0eSlideBaseline(
                slide_id="slide:current",
                revision=3,
                slide_digest=digest("3"),
                owner_ids=("q-title", "q-body", "q-shape", "q-image", "q-table"),
            ),
        ),
        capabilities=(
            ("text", "supported"),
            ("shape", "supported"),
            ("image", "supported"),
            ("table", "supported"),
            ("chart", "partial"),
            ("connector", "unsupported"),
            ("group", "unsupported"),
        ),
        export_attempt_id="12345678-1234-1234-1234-123456789012",
        publication_revision=4,
        pointer_digest=digest("4"),
        artifact_id="87654321-4321-4321-4321-210987654321",
        artifact_digest=digest("4"),
        artifact_size_bytes=4096,
        completion_count=0,
        completion_id=None,
        completion_result_digest=None,
        completion_cas_version=0,
        signer_nonce_digest=None,
        completion_subject_unique=True,
        completion_nonce_unique=True,
        completion_append_only=True,
        attempt={
            "presentationId": "01234567-89ab-cdef-0123-456789abcdef",
            "presentationRevision": 3,
            "generationAttemptId": "generation:current",
            "hybridExportAttemptId": "12345678-1234-1234-1234-123456789012",
            "sceneDigest": digest("2"),
            "activeManifestDigest": digest("1"),
            "gateMinus1eResultSetDigest": digest("6"),
            "gate0eResultSetDigest": digest("7"),
            "sourceHeadCommit": "8" * 40,
            "expectedQids": ["q-body", "q-image", "q-shape", "q-table", "q-title"],
        },
    )
    before = baseline.payload()

    cases, result_digest = evaluate_gate0e_cases(baseline)

    assert len(cases) == 15
    assert [case.ordinal for case in cases] == list(range(1, 16))
    assert all(
        case.observed_http_status == case.expected_http_status
        and case.observed_error_code == case.expected_error_code
        and case.fail_closed
        for case in cases
    )
    assert cases[0].mutation_payload["expectedSceneDigest"] != baseline.scene_digest
    assert cases[1].mutation_payload["expectedSlideRevision"] != baseline.slides[0].revision
    assert cases[2].mutation_payload == {"claimedOwnerScope": "owner:foreign"}
    assert cases[3].mutation_payload == {"claimedTenantId": "tenant:foreign"}
    assert cases[5].mutation_payload["ownerIds"] == ["q-title", "q-title"]
    assert cases[7].mutation_payload["capabilityKind"] == "connector"
    assert cases[10].mutation_payload["artifactDigest"] != baseline.artifact_digest
    assert (
        cases[11].mutation_payload["firstSignerNonceDigest"]
        == cases[11].mutation_payload["secondSignerNonceDigest"]
    )
    assert cases[12].mutation_payload["expectedCompletionCasVersion"] == 1
    assert cases[13].mutation_payload["existingCompletionCount"] == 0
    assert baseline.completion_count == 0
    assert baseline.completion_cas_version == 0
    assert baseline.public_payload()["publicationRevision"] == 4
    assert baseline.public_payload()["pointerDigest"] == digest("4")
    assert baseline.public_payload()["artifactId"] == baseline.artifact_id
    assert result_digest.startswith("sha256:")
    assert baseline.payload() == before

    owner_id = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    job_id = uuid.UUID(baseline.export_attempt_id)
    artifact_id = uuid.UUID(baseline.artifact_id)
    pointer = SimpleNamespace(
        artifact_id=artifact_id,
        revision=baseline.publication_revision,
        digest=baseline.pointer_digest,
    )
    artifact = SimpleNamespace(
        id=artifact_id,
        job_id=job_id,
        artifact_digest=baseline.artifact_digest,
        size_bytes=baseline.artifact_size_bytes,
    )
    export_job = SimpleNamespace(
        id=job_id,
        expected_publication_revision=baseline.publication_revision - 1,
        request_payload={},
    )
    generation_job = SimpleNamespace(task_id="task-actual-generation")
    entities_seen: list[type] = []

    class _Rows:
        def all(self) -> list[object]:
            return []

    class _Session:
        def scalars(self, _statement: object) -> _Rows:
            return _Rows()

        def scalar(self, statement: object) -> object:
            entity = statement.column_descriptions[0]["entity"]
            entities_seen.append(entity)
            return {
                AuthoredVNextPublicationPointer: pointer,
                AuthoredVNextExportArtifactReceipt: artifact,
                AuthoredVNextExportJob: export_job,
                PresentationGenerationJob: generation_job,
            }[entity]

    scene = SimpleNamespace(
        hybrid_profile_id="authored-editable-hybrid-ko-en-basic-v1",
        scene_digest=baseline.scene_digest,
        slides=[
            SimpleNamespace(
                slide_id=baseline.slides[0].slide_id,
                revision=baseline.slides[0].revision,
                slide_digest=baseline.slides[0].slide_digest,
                elements=[SimpleNamespace(element_id=value) for value in baseline.slides[0].owner_ids],
            )
        ],
        capabilities=[
            SimpleNamespace(kind=kind, native_pptx_export=state)
            for kind, state in baseline.capabilities
        ],
    )
    deck = SimpleNamespace(
        deck_id=baseline.deck_id,
        revision=baseline.deck_revision,
        digest=baseline.deck_digest,
        scene=scene,
    )
    repository = SimpleNamespace(
        get_deck=lambda deck_id: deck,
        _base=SimpleNamespace(
            owner_id=owner_id,
            owner_scope=baseline.owner_scope,
            tenant_id=baseline.tenant_id,
        ),
        _sessions=lambda: nullcontext(_Session()),
    )

    loaded = _load_current_baseline(repository, baseline.deck_id)

    assert loaded.completion_count == 0
    assert loaded.completion_cas_version == 0
    assert loaded.publication_revision == baseline.publication_revision
    assert loaded.pointer_digest == baseline.pointer_digest
    assert loaded.artifact_id == baseline.artifact_id
    assert entities_seen == [
        AuthoredVNextPublicationPointer,
        AuthoredVNextExportArtifactReceipt,
        AuthoredVNextExportJob,
        PresentationGenerationJob,
    ]
    assert loaded.attempt["generationAttemptId"] == "task-actual-generation"

    pointer.digest = digest("5")
    with pytest.raises(Gate0eProbeError, match="editable_mvp_gate0e_active_publication_drift"):
        _load_current_baseline(repository, baseline.deck_id)
