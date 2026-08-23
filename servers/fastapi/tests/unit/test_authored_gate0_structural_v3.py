from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.authored_vnext.semantic.release_authority_v3 import (
    Gate0StructuralCaseExecutionV1,
)
from models.sql.authored_capability_receipt import AuthoredCapabilityReceiptRecordV2
from models.sql.authored_release_evidence_v3 import (
    AuthoredEvidenceBundleCompletionV3,
    AuthoredEvidenceGraphRootV3,
    AuthoredGate0StructuralCompletionV3,
    AuthoredGate0StructuralEventV3,
    AuthoredGate0StructuralSubjectV3,
    AuthoredGate0StructuralTraceV3,
    AuthoredReleaseArtifactV3,
    AuthoredReleaseNonceV3,
)
from models.sql.authored_semantic_bundle import (
    AuthoredDeckEmissionActivePointer,
    AuthoredDeckEmissionManifestRecord,
    AuthoredEmissionBundleRecord,
    AuthoredSemanticArtifact,
    AuthoredSemanticExportBinding,
)
from models.sql.presentation import PresentationModel
from models.sql.presentation_export_attempt import PresentationExportAttempt
from models.sql.user import User
from models.sql.workspace import WorkspaceOperation  # noqa: F401
from services.authored_vnext.semantic.canonical import canonical_digest
from services.authored_vnext.semantic.gate0_structural_v3 import (
    execute_gate0_structural_case_v3,
    gate0_structural_sealed_completion_count_v3,
)


def test_gate0_structural_34_cases_are_signed_and_never_promote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_key = Ed25519PrivateKey.generate()
    manifest_digest = "sha256:" + "a" * 64
    monkeypatch.setenv(
        "AUTHORED_GATE0_STRUCTURAL_ED25519_PRIVATE_KEY_BASE64",
        base64.b64encode(private_key.private_bytes_raw()).decode("ascii"),
    )
    monkeypatch.setenv("AUTHORED_GATE0_STRUCTURAL_SIGNER_KEY_ID", "gate0.structural.key-1")
    monkeypatch.setenv("AUTHORED_GATE0_STRUCTURAL_MANIFEST_DIGEST", manifest_digest)
    monkeypatch.setenv("AUTHORED_RELEASE_EVIDENCE_POLICY_DIGEST", "sha256:" + "b" * 64)
    monkeypatch.setenv("AUTHORED_RELEASE_SOURCE_TREE_CENSUS_DIGEST", "sha256:" + "c" * 64)
    manifest = json.loads(
        (
            Path(__file__).resolve().parents[4]
            / "docs"
            / "authored-bounded-hybrid-release-v3-contracts"
            / "gate0-case-manifest.v1.json"
        ).read_text(encoding="utf-8")
    )

    async def scenario() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gate0.db'}")
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(
                SQLModel.metadata.create_all,
                tables=[
                    User.__table__,
                    PresentationModel.__table__,
                    PresentationExportAttempt.__table__,
                    AuthoredSemanticArtifact.__table__,
                    AuthoredEmissionBundleRecord.__table__,
                    AuthoredDeckEmissionManifestRecord.__table__,
                    AuthoredDeckEmissionActivePointer.__table__,
                    AuthoredSemanticExportBinding.__table__,
                    AuthoredCapabilityReceiptRecordV2.__table__,
                    AuthoredReleaseArtifactV3.__table__,
                    AuthoredReleaseNonceV3.__table__,
                    AuthoredEvidenceGraphRootV3.__table__,
                    AuthoredEvidenceBundleCompletionV3.__table__,
                    AuthoredGate0StructuralSubjectV3.__table__,
                    AuthoredGate0StructuralEventV3.__table__,
                    AuthoredGate0StructuralCompletionV3.__table__,
                    AuthoredGate0StructuralTraceV3.__table__,
                ],
            )
        cases = [manifest["positiveCase"], *manifest["mutationRegistry"]]
        run_id = uuid.uuid4()
        async with maker() as session:
            for case in cases:
                case_id = case["caseId"]
                ordinal = case["ordinal"]
                expected_status = case["expectedHttpStatus"]
                expected_error = case.get("expectedErrorCode")
                request = Gate0StructuralCaseExecutionV1(
                    run_id=run_id,
                    manifest_digest=manifest_digest,
                    case_id=case_id,
                    ordinal=ordinal,
                    mutation=(
                        None
                        if ordinal == 1
                        else {
                            "jsonPaths": case["jsonPaths"],
                            "operator": case["operator"],
                            "phase": case.get("mutationPhase"),
                        }
                    ),
                )
                status_code, payload = await execute_gate0_structural_case_v3(session, request=request)
                assert status_code == expected_status
                assert payload["errorCode"] == expected_error
                assert payload["authorityDomain"] == "structural_test"
                assert payload["productionAuthorityGranted"] is False
                assert payload["productionValidatorAccepted"] is False
                assert payload["releaseReportStatus"] == "not-verified"
                assert len(payload["executionTrace"]["spans"]) == 7
                for span in payload["executionTrace"]["spans"]:
                    assert span["serverObserved"] is True
                    assert span["operationId"]
                    assert span["inputDigest"].startswith("sha256:")
                    assert span["outputDigest"].startswith("sha256:")
                    assert span["evidenceRef"]
                if ordinal > 1:
                    evidence = payload["mutationEvidence"]
                    assert evidence["baselineSubjectDigest"] != evidence["mutatedSubjectDigest"]
                    digest_value = dict(evidence)
                    mutation_digest = digest_value.pop("mutationDigest")
                    assert canonical_digest(digest_value) == mutation_digest
                assert payload["postState"]["sealedCompletionCount"] == 0
                assert payload["postState"]["actualProductReceiptCount"] == 0
                assert payload["postState"]["actualProductPromotionReady"] is False
                if ordinal == 34:
                    attempts = payload["executionTrace"]["completionCasAttempts"]
                    assert [item["outcome"] for item in attempts] == [
                        "sealed",
                        "conflict",
                    ]
                    assert attempts[0]["durableRecordId"] == attempts[1]["durableRecordId"]
                    assert attempts[1]["errorCode"] == "completion-seal-conflict"
                    assert payload["postState"]["structuralCompletionState"] == "sealed"
                trace = dict(payload["executionTrace"])
                signature = base64.b64decode(trace.pop("signatureBase64"), validate=True)
                trace_digest = trace.pop("traceDigest")
                assert canonical_digest(trace) == trace_digest
                private_key.public_key().verify(signature, trace_digest.encode("ascii"))
                assert await gate0_structural_sealed_completion_count_v3(session, run_id=run_id, case_id=case_id) == 0
            await session.commit()
        await engine.dispose()

    asyncio.run(scenario())
