from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.authored_vnext.semantic.release_authority_v3 import (
    DurableArtifactRefV3,
    EvidenceBundleCompletionCreateRequestV3,
    EvidenceBundleCompletionSealRequestV3,
    EvidenceGraphRootCreateRequestV3,
    EvidenceIssuerEnvelopeV3,
    GATE1_GRAPH_BASE_ARTIFACT_IDS_DIGEST_V3,
    GATE1_GRAPH_BASE_ARTIFACT_IDS_V3,
    GATE1_REQUIRED_ARTIFACT_IDS_DIGEST_V3,
)
from models.sql.authored_capability_receipt import AuthoredCapabilityReceiptRecordV2
from models.sql.authored_release_evidence_v3 import (
    AuthoredComQualificationJobV3,
    AuthoredEvidenceBundleCompletionV3,
    AuthoredEvidenceGraphRootV3,
    AuthoredReleaseArtifactV3,
    AuthoredReleaseNonceV3,
)
from models.sql.authored_semantic_bundle import (
    AuthoredDeckEmissionActivePointer,
    AuthoredSemanticExportBinding,
)
from models.sql.presentation import PresentationModel
from models.sql.presentation_export_attempt import PresentationExportAttempt
from models.sql.user import User
from models.sql.workspace import WorkspaceOperation  # noqa: F401
from services.authored_vnext.semantic import completion_v3
from services.authored_vnext.semantic.canonical import SemanticContractError, canonical_digest
from services.authored_vnext.semantic.completion_v3 import (
    create_evidence_bundle_completion_v3,
    create_evidence_graph_root_v3,
    seal_evidence_bundle_completion_v3,
)
from services.authored_vnext.semantic.readiness_v3 import (
    load_release_profile_definition_v3,
    load_release_profile_model_projection_digest_v3,
)
from services.authored_vnext.semantic.release_authority_v3 import (
    actual_product_authority_digest,
    actual_product_canonical_digest_v2,
)


def _graph_request_values() -> dict[str, object]:
    required_ids = list(GATE1_GRAPH_BASE_ARTIFACT_IDS_V3)
    export_attempt_id = uuid.uuid4()
    inventory = [
        DurableArtifactRefV3(
            export_attempt_id=export_attempt_id,
            artifact_id=artifact_id,
            version_id=uuid.uuid4(),
            sha256="sha256:" + f"{index + 1:064x}",
            byte_length=index + 1,
            media_type="application/octet-stream",
            encryption_key_ref="kms:test/key-1",
            storage_generation=1,
            state="bound",
        )
        for index, artifact_id in enumerate(required_ids)
    ]
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return {
        "presentation_id": uuid.uuid4(),
        "presentation_revision": 1,
        "export_attempt_id": export_attempt_id,
        "evidence_policy_digest": "sha256:" + "1" * 64,
        "active_manifest_digest": "sha256:" + "2" * 64,
        "source_tree_census_digest": "sha256:" + "3" * 64,
        "source_head_commit": "4" * 40,
        "release_profile_digest": "sha256:" + "5" * 64,
        "feature_set_digest": "sha256:" + "6" * 64,
        "gate_minus1_manifest_digest": "sha256:" + "7" * 64,
        "gate_minus1_result_set_digest": "sha256:" + "8" * 64,
        "gate_minus1_receipt_artifact_digest": "sha256:" + "9" * 64,
        "gate_minus1_validator_contract_digest": "sha256:" + "a" * 64,
        "environment_attestation_digest": "sha256:" + "b" * 64,
        "worker_attestation_digest": "sha256:" + "c" * 64,
        "com_qualification_job_id": uuid.uuid4(),
        "com_qualification_result_digest": "sha256:" + "e" * 64,
        "base_artifact_ids": required_ids,
        "base_artifact_ids_digest": GATE1_GRAPH_BASE_ARTIFACT_IDS_DIGEST_V3,
        "final_required_artifact_ids_digest": GATE1_REQUIRED_ARTIFACT_IDS_DIGEST_V3,
        "artifact_inventory": inventory,
        "artifact_inventory_digest": actual_product_canonical_digest_v2(
            [item.model_dump(mode="json", by_alias=True) for item in inventory]
        ),
        "root_digest": "sha256:" + "d" * 64,
        "authority": EvidenceIssuerEnvelopeV3(
            authority_domain="actual_product",
            evidence_class="actual_product_completion",
            issuer_id="completion.issuer",
            signer_key_id="completion.key-1",
            environment_id="completion-test",
            key_purpose="actual-product-completion",
            rotation_epoch=1,
            issued_at=now,
            expires_at=now + timedelta(minutes=15),
            nonce="completion_graph_nonce_0001",
            signature_algorithm="Ed25519",
            signature_base64=base64.b64encode(b"0" * 64).decode("ascii"),
        ),
    }


def test_release_profile_authority_digest_matches_gate1_sealed_raw_contract() -> None:
    _, authority_digest = load_release_profile_definition_v3()

    assert authority_digest == (
        "sha256:92335e102787f38a5ecd996e790264083a4e65737bdc83a58f09e4e9a799c870"
    )
    assert load_release_profile_model_projection_digest_v3() == (
        "sha256:2e2ead14dc6f72a952323f2625a9f4e539a3295e6f58141753fb2b8d6d82edd9"
    )
    assert authority_digest != load_release_profile_model_projection_digest_v3()


def test_attempt22_graph_request_matches_gate1_node_canonical_v2() -> None:
    """Regression for the real attempt22 graph request shape that exposed integer drift."""

    request_without_authority = {
        "schemaVersion": "authored-evidence-graph-root-create/v3",
        "presentationId": "ea18b250-a58c-473c-a120-3bb64d689e7b",
        "presentationRevision": 3,
        "exportAttemptId": "4bb66bf4-28e1-490b-a18f-66dfeaa113f7",
        "artifactInventory": [
            {
                "schemaVersion": "authored-durable-artifact-ref/v3",
                "exportAttemptId": "4bb66bf4-28e1-490b-a18f-66dfeaa113f7",
                "artifactId": "semantic-source-json",
                "versionId": "ab1c016f-665d-4876-b3e7-86b179e4c7de",
                "sha256": "sha256:1ef2ac4dc2d83e02cac1bc6bfd43db9f697138c4cbdee0b0eadf2be3985f399c",
                "byteLength": 69_329,
                "mediaType": "application/json",
                "encryptionKeyRef": "local-evidence/gate1-v3",
                "storageGeneration": 1,
                "state": "bound",
            }
        ],
        "rootDigest": "sha256:ce3163fddcf008eca61c3d80ba631805ce647555e58b4d156f342dfa818bb0c9",
    }
    node_script = """
import fs from "node:fs";
import { canonicalSha256 } from "./scripts/authored-bounded-release-contracts.mjs";
process.stdout.write(canonicalSha256(JSON.parse(fs.readFileSync(0, "utf8"))));
"""
    repository_root = Path(__file__).resolve().parents[4]
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", node_script],
        cwd=repository_root,
        input=json.dumps(request_without_authority, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=True,
    )

    assert actual_product_canonical_digest_v2(request_without_authority) == completed.stdout
    assert actual_product_canonical_digest_v2(request_without_authority) != canonical_digest(
        request_without_authority
    )


def test_graph_persists_gate1_canonical_v2_request_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = _graph_request_values()
    export_attempt_id = values["export_attempt_id"]
    artifacts: list[AuthoredReleaseArtifactV3] = []
    inventory: list[DurableArtifactRefV3] = []
    for index, artifact_id in enumerate(GATE1_GRAPH_BASE_ARTIFACT_IDS_V3):
        content = bytes([65 + index % 26]) * (20 + index)
        digest = "sha256:" + sha256(content).hexdigest()
        version_id = uuid.uuid4()
        artifacts.append(
            AuthoredReleaseArtifactV3(
                version_id=version_id,
                tenant_id="t",
                export_attempt_id=export_attempt_id,
                artifact_id=artifact_id,
                sha256=digest,
                byte_length=len(content),
                media_type="application/octet-stream",
                encryption_key_ref="kms:test/key-1",
                storage_generation=1,
                state="bound",
                content_bytes=content,
            )
        )
        inventory.append(
            DurableArtifactRefV3(
                export_attempt_id=export_attempt_id,
                artifact_id=artifact_id,
                version_id=version_id,
                sha256=digest,
                byte_length=len(content),
                media_type="application/octet-stream",
                encryption_key_ref="kms:test/key-1",
                storage_generation=1,
                state="bound",
            )
        )
    values["artifact_inventory"] = inventory
    values["artifact_inventory_digest"] = actual_product_canonical_digest_v2(
        [item.model_dump(mode="json", by_alias=True) for item in inventory]
    )
    provisional = EvidenceGraphRootCreateRequestV3(**values)
    values["root_digest"] = actual_product_canonical_digest_v2(
        completion_v3._graph_projection(provisional)
    )
    request = EvidenceGraphRootCreateRequestV3(**values)
    signed_payload = request.model_dump(mode="json", by_alias=True)
    signed_payload.pop("authority")
    expected_request_digest = actual_product_canonical_digest_v2(signed_payload)
    assert expected_request_digest != canonical_digest(signed_payload)

    async def no_nonce(*args, **kwargs) -> None:
        return None

    async def no_revalidation(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(completion_v3, "validate_actual_product_authority_v3", lambda *args, **kwargs: "ok")
    monkeypatch.setattr(completion_v3, "consume_authority_nonce_v3", no_nonce)
    monkeypatch.setattr(completion_v3, "_revalidate_subject", no_revalidation)

    async def scenario() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'graph-digest.db'}")
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(
                SQLModel.metadata.create_all,
                tables=[AuthoredReleaseArtifactV3.__table__, AuthoredEvidenceGraphRootV3.__table__],
            )
        async with maker() as session:
            session.add_all(artifacts)
            await session.flush()
            response = await create_evidence_graph_root_v3(
                session,
                request=request,
                owner_id=uuid.uuid4(),
                tenant_id="t",
            )
            assert response.request_digest == expected_request_digest
            stored = await session.get(AuthoredEvidenceGraphRootV3, response.graph_root_id)
            assert stored is not None
            assert stored.request_digest == expected_request_digest
        await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate"])
def test_evidence_graph_rejects_non_exact_gate1_artifact_set(mutation: str) -> None:
    values = _graph_request_values()
    required_ids = list(values["base_artifact_ids"])
    inventory = list(values["artifact_inventory"])
    if mutation == "missing":
        required_ids.pop()
        inventory.pop()
    elif mutation == "extra":
        required_ids.append("unexpected-artifact-json")
        inventory.append(
            inventory[-1].model_copy(
                update={
                    "artifact_id": "unexpected-artifact-json",
                    "version_id": uuid.uuid4(),
                }
            )
        )
    else:
        required_ids[-1] = required_ids[-2]
        inventory[-1] = inventory[-1].model_copy(update={"artifact_id": inventory[-2].artifact_id})
    values["base_artifact_ids"] = required_ids
    values["artifact_inventory"] = inventory
    with pytest.raises(ValueError):
        EvidenceGraphRootCreateRequestV3(**values)


@pytest.mark.parametrize(
    "future_artifact_id",
    ["phase-result-set-json", "evidence-graph-root-json", "completion-seal-json"],
)
def test_evidence_graph_rejects_self_or_future_artifact_cycle(
    future_artifact_id: str,
) -> None:
    values = _graph_request_values()
    base_ids = list(values["base_artifact_ids"])
    inventory = list(values["artifact_inventory"])
    base_ids[-1] = future_artifact_id
    inventory[-1] = inventory[-1].model_copy(update={"artifact_id": future_artifact_id, "version_id": uuid.uuid4()})
    values["base_artifact_ids"] = base_ids
    values["artifact_inventory"] = inventory
    with pytest.raises(ValueError, match="authored_evidence_graph_required_artifacts_invalid"):
        EvidenceGraphRootCreateRequestV3(**values)


def test_graph_revalidation_rejects_cross_attempt_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, definition_digest = load_release_profile_definition_v3()
    owner_id = uuid.uuid4()
    presentation_id = uuid.uuid4()
    export_attempt_id = uuid.uuid4()
    manifest_digest = "sha256:" + "2" * 64
    pin_values = {
        "AUTHORED_RELEASE_SOURCE_TREE_CENSUS_DIGEST": "sha256:" + "3" * 64,
        "AUTHORED_RELEASE_SOURCE_HEAD_COMMIT": "4" * 40,
        "AUTHORED_RELEASE_EVIDENCE_POLICY_DIGEST": "sha256:" + "1" * 64,
        "AUTHORED_RELEASE_FEATURE_SET_DIGEST": "sha256:" + "5" * 64,
        "AUTHORED_RELEASE_GATE_MINUS1_MANIFEST_DIGEST": "sha256:" + "6" * 64,
        "AUTHORED_RELEASE_GATE_MINUS1_RESULT_SET_DIGEST": "sha256:" + "7" * 64,
        "AUTHORED_RELEASE_GATE_MINUS1_RECEIPT_ARTIFACT_DIGEST": "sha256:" + "8" * 64,
        "AUTHORED_RELEASE_GATE_MINUS1_VALIDATOR_CONTRACT_DIGEST": "sha256:" + "9" * 64,
        "AUTHORED_RELEASE_ENVIRONMENT_ATTESTATION_DIGEST": "sha256:" + "a" * 64,
        "AUTHORED_RELEASE_WORKER_ATTESTATION_DIGEST": "sha256:" + "b" * 64,
    }
    for name, value in pin_values.items():
        monkeypatch.setenv(name, value)
    base_ids = list(GATE1_GRAPH_BASE_ARTIFACT_IDS_V3)
    inventory = [{"artifactId": item} for item in base_ids]
    claim_receipts = ["sha256:" + "e" * 64]
    now = datetime.now(timezone.utc).replace(microsecond=0)
    com_job_id = uuid.uuid4()

    async def scenario() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'binding.db'}")
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(
                SQLModel.metadata.create_all,
                tables=[
                    User.__table__,
                    PresentationModel.__table__,
                    PresentationExportAttempt.__table__,
                    AuthoredDeckEmissionActivePointer.__table__,
                    AuthoredSemanticExportBinding.__table__,
                    AuthoredEvidenceGraphRootV3.__table__,
                    AuthoredComQualificationJobV3.__table__,
                ],
            )
        graph = AuthoredEvidenceGraphRootV3(
            tenant_id="t",
            owner_id=owner_id,
            presentation_id=presentation_id,
            presentation_revision=1,
            export_attempt_id=export_attempt_id,
            authority_domain="actual_product",
            evidence_policy_digest=pin_values["AUTHORED_RELEASE_EVIDENCE_POLICY_DIGEST"],
            active_manifest_digest=manifest_digest,
            source_tree_census_digest=pin_values["AUTHORED_RELEASE_SOURCE_TREE_CENSUS_DIGEST"],
            source_head_commit=pin_values["AUTHORED_RELEASE_SOURCE_HEAD_COMMIT"],
            release_profile_digest=definition_digest,
            feature_set_digest=pin_values["AUTHORED_RELEASE_FEATURE_SET_DIGEST"],
            gate_minus1_manifest_digest=pin_values["AUTHORED_RELEASE_GATE_MINUS1_MANIFEST_DIGEST"],
            gate_minus1_result_set_digest=pin_values["AUTHORED_RELEASE_GATE_MINUS1_RESULT_SET_DIGEST"],
            gate_minus1_receipt_artifact_digest=pin_values["AUTHORED_RELEASE_GATE_MINUS1_RECEIPT_ARTIFACT_DIGEST"],
            gate_minus1_validator_contract_digest=pin_values["AUTHORED_RELEASE_GATE_MINUS1_VALIDATOR_CONTRACT_DIGEST"],
            environment_attestation_digest=pin_values["AUTHORED_RELEASE_ENVIRONMENT_ATTESTATION_DIGEST"],
            worker_attestation_digest=pin_values["AUTHORED_RELEASE_WORKER_ATTESTATION_DIGEST"],
            com_qualification_job_id=com_job_id,
            com_qualification_result_digest=actual_product_canonical_digest_v2({}),
            base_artifact_ids=base_ids,
            base_artifact_ids_digest=GATE1_GRAPH_BASE_ARTIFACT_IDS_DIGEST_V3,
            final_required_artifact_ids_digest=GATE1_REQUIRED_ARTIFACT_IDS_DIGEST_V3,
            artifact_inventory=inventory,
            artifact_inventory_digest=actual_product_canonical_digest_v2(inventory),
            root_digest="sha256:" + "d" * 64,
            request_digest="sha256:" + "e" * 64,
        )
        async with maker() as session:
            session.add_all(
                [
                    PresentationModel(
                        id=presentation_id,
                        owner_id=owner_id,
                        content="binding",
                        n_slides=1,
                        language="ko",
                        mode="authored",
                        revision=1,
                    ),
                    AuthoredDeckEmissionActivePointer(
                        owner_id=owner_id,
                        tenant_id="t",
                        presentation_id=presentation_id,
                        canonical_mode="bundleGraph",
                        manifest_id=uuid.uuid4(),
                        presentation_revision=1,
                        canonical_digest=manifest_digest,
                        generation=1,
                        operation_id=uuid.uuid4(),
                    ),
                    PresentationExportAttempt(
                        id=export_attempt_id,
                        owner_id=owner_id,
                        owner_scope=str(owner_id),
                        presentation_id=presentation_id,
                        format="pptx",
                        state="succeeded",
                        source_revision=1,
                        source_snapshot_sha256="1" * 64,
                        request_hash="2" * 64,
                        idempotency_key_hash="3" * 64,
                        requested_adapter="general",
                        selected_adapter="general",
                        artifact_sha256="a" * 64,
                        artifact_size_bytes=1,
                        provenance_schema_version=1,
                        provenance={},
                        started_at=now,
                        completed_at=now,
                    ),
                    AuthoredSemanticExportBinding(
                        owner_id=owner_id,
                        tenant_id="t",
                        export_attempt_id=export_attempt_id,
                        presentation_id=presentation_id,
                        presentation_revision=1,
                        canonical_digest=manifest_digest,
                        renderer_source_digest="sha256:" + "f" * 64,
                        mode="hybrid",
                        serializer_id="pptxgenjs-powerpoint",
                        serializer_version="v3",
                        pptx_digest="sha256:" + "a" * 64,
                        binding_digest="sha256:" + "c" * 64,
                        payload={
                            "exportAttemptId": str(export_attempt_id),
                            "manifestDigest": manifest_digest,
                            "bindingDigest": "sha256:" + "c" * 64,
                            "receiptDigests": ["sha256:" + "d" * 64],
                            "releaseProjectionDigest": "sha256:" + "f" * 64,
                            "releaseAuthorityReceiptDigests": claim_receipts,
                            "exportAuthorityClaim": {
                                "kind": "promotion",
                                "profileDefinitionDigest": definition_digest,
                                "projectionDigest": "sha256:" + "f" * 64,
                                "receiptDigests": claim_receipts,
                            },
                        },
                    ),
                    AuthoredComQualificationJobV3(
                        id=com_job_id,
                        tenant_id="t",
                        export_attempt_id=export_attempt_id,
                        evidence_policy_digest=pin_values["AUTHORED_RELEASE_EVIDENCE_POLICY_DIGEST"],
                        authority_domain="actual_product",
                        state="sealed",
                        evidence={},
                    ),
                    graph,
                ]
            )
            await session.flush()
            await completion_v3._revalidate_subject(session, graph)
            graph.export_attempt_id = uuid.uuid4()
            with pytest.raises(
                SemanticContractError,
                match="authored_evidence_completion_export_binding_changed",
            ):
                await completion_v3._revalidate_subject(session, graph)
        await engine.dispose()

    asyncio.run(scenario())


def test_completion_is_exact_set_single_cas_and_second_seal_conflicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setenv("AUTHORED_RELEASE_AUTHORITY_ENVIRONMENT_ID", "completion-test")
    monkeypatch.setenv(
        "AUTHORED_RELEASE_SIGNER_TRUST_BUNDLE_JSON",
        json.dumps(
            [
                {
                    "authorityDomain": "actual_product",
                    "issuerId": "completion.issuer",
                    "signerKeyId": "completion.key-1",
                    "environmentId": "completion-test",
                    "keyPurpose": "actual-product-completion",
                    "rotationEpoch": 1,
                    "algorithm": "Ed25519",
                    "revoked": False,
                    "publicKeyBase64": base64.b64encode(public_key).decode("ascii"),
                }
            ]
        ),
    )
    now = datetime.now(timezone.utc).replace(microsecond=0)

    def authority(payload: dict, nonce: str) -> tuple[EvidenceIssuerEnvelopeV3, str]:
        envelope = EvidenceIssuerEnvelopeV3(
            authority_domain="actual_product",
            evidence_class="actual_product_completion",
            issuer_id="completion.issuer",
            signer_key_id="completion.key-1",
            environment_id="completion-test",
            key_purpose="actual-product-completion",
            rotation_epoch=1,
            issued_at=now,
            expires_at=now + timedelta(minutes=15),
            nonce=nonce,
            signature_algorithm="Ed25519",
            signature_base64=base64.b64encode(b"0" * 64).decode("ascii"),
        )
        digest = actual_product_authority_digest(payload, envelope)
        return (
            envelope.model_copy(
                update={"signature_base64": base64.b64encode(private_key.sign(digest.encode("ascii"))).decode("ascii")}
            ),
            digest,
        )

    async def scenario() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'completion.db'}")
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(
                SQLModel.metadata.create_all,
                tables=[
                    AuthoredCapabilityReceiptRecordV2.__table__,
                    AuthoredReleaseNonceV3.__table__,
                    AuthoredEvidenceGraphRootV3.__table__,
                    AuthoredEvidenceBundleCompletionV3.__table__,
                    AuthoredSemanticExportBinding.__table__,
                ],
            )
        definition, definition_digest = load_release_profile_definition_v3()
        export_attempt_id = uuid.uuid4()
        binding_digest = "sha256:" + "0" * 64
        graph = AuthoredEvidenceGraphRootV3(
            tenant_id="t",
            owner_id=uuid.uuid4(),
            presentation_id=uuid.uuid4(),
            presentation_revision=1,
            export_attempt_id=export_attempt_id,
            authority_domain="actual_product",
            evidence_policy_digest="sha256:" + "1" * 64,
            active_manifest_digest="sha256:" + "2" * 64,
            source_tree_census_digest="sha256:" + "3" * 64,
            source_head_commit="4" * 40,
            release_profile_digest=definition_digest,
            feature_set_digest="sha256:" + "5" * 64,
            gate_minus1_manifest_digest="sha256:" + "6" * 64,
            gate_minus1_result_set_digest="sha256:" + "7" * 64,
            gate_minus1_receipt_artifact_digest="sha256:" + "8" * 64,
            gate_minus1_validator_contract_digest="sha256:" + "9" * 64,
            environment_attestation_digest="sha256:" + "a" * 64,
            worker_attestation_digest="sha256:" + "b" * 64,
            com_qualification_job_id=uuid.uuid4(),
            com_qualification_result_digest="sha256:" + "e" * 64,
            base_artifact_ids=list(GATE1_GRAPH_BASE_ARTIFACT_IDS_V3),
            base_artifact_ids_digest=GATE1_GRAPH_BASE_ARTIFACT_IDS_DIGEST_V3,
            final_required_artifact_ids_digest=GATE1_REQUIRED_ARTIFACT_IDS_DIGEST_V3,
            artifact_inventory=[],
            artifact_inventory_digest="sha256:" + "c" * 64,
            root_digest="sha256:" + "d" * 64,
            request_digest="sha256:" + "f" * 64,
        )

        async def subject_ok(*args, **kwargs) -> None:
            return None

        monkeypatch.setattr(completion_v3, "_revalidate_subject", subject_ok)
        monkeypatch.setattr(completion_v3, "validate_actual_product_record_v3", lambda *args, **kwargs: True)
        cas_calls: list[type] = []
        shared_cas = completion_v3.seal_completion_row_cas_v3

        async def observed_shared_cas(*args, **kwargs):
            cas_calls.append(type(kwargs["record"]))
            return await shared_cas(*args, **kwargs)

        monkeypatch.setattr(completion_v3, "seal_completion_row_cas_v3", observed_shared_cas)
        receipt_digests = []
        async with maker() as session:
            session.add(graph)
            session.add(
                AuthoredSemanticExportBinding(
                    owner_id=graph.owner_id,
                    tenant_id="t",
                    export_attempt_id=export_attempt_id,
                    presentation_id=graph.presentation_id,
                    presentation_revision=1,
                    canonical_digest=graph.active_manifest_digest,
                    renderer_source_digest="sha256:" + "1" * 64,
                    mode="hybrid",
                    serializer_id="pptxgenjs-powerpoint",
                    serializer_version="v3",
                    pptx_digest="sha256:" + "2" * 64,
                    binding_digest=binding_digest,
                    payload={},
                )
            )
            for index, capability_id in enumerate(definition.all_required_capability_ids):
                digest = "sha256:" + f"{index + 20:064x}"
                receipt_digests.append(digest)
                session.add(
                    AuthoredCapabilityReceiptRecordV2(
                        tenant_id="t",
                        capability_id=capability_id,
                        state="ready",
                        scope_digest="sha256:" + "e" * 64,
                        environment_digest="sha256:" + "f" * 64,
                        serializer_id="pptxgenjs-powerpoint",
                        serializer_version="v1",
                        issuer_principal_id=uuid.uuid4(),
                        issuer_authority="authenticated-admin",
                        evaluated_at=now,
                        expires_at=now + timedelta(minutes=10),
                        transition_kind="issue",
                        receipt_digest=digest,
                        payload={},
                        authority_domain="actual_product",
                        evidence_class="actual_product_capability",
                        export_attempt_id=export_attempt_id,
                        binding_digest=binding_digest,
                    )
                )
            await session.flush()
            pending = await create_evidence_bundle_completion_v3(
                session,
                request=EvidenceBundleCompletionCreateRequestV3(
                    graph_root_id=graph.id,
                    required_capability_ids=definition.all_required_capability_ids,
                ),
                tenant_id="t",
            )
            assert pending.create_request_digest == actual_product_canonical_digest_v2(
                {
                    "schemaVersion": "authored-evidence-bundle-completion-create/v3",
                    "graphRootId": str(graph.id),
                    "requiredCapabilityIds": definition.all_required_capability_ids,
                }
            )
            signed_payload = {
                "schemaVersion": "authored-evidence-bundle-completion-seal/v3",
                "expectedCasVersion": 0,
                "receiptDigests": receipt_digests,
            }
            envelope, authority_digest = authority(signed_payload, "completion_nonce_0000000001")
            completion_digest = actual_product_canonical_digest_v2(
                {
                    "graphRootDigest": graph.root_digest,
                    "requiredCapabilityIds": definition.all_required_capability_ids,
                    "receiptDigests": receipt_digests,
                    "authorityDigest": authority_digest,
                    "state": "sealed",
                }
            )
            seal_request = EvidenceBundleCompletionSealRequestV3(
                expected_cas_version=0,
                receipt_digests=receipt_digests,
                completion_digest=completion_digest,
                authority=envelope,
            )
            sealed = await seal_evidence_bundle_completion_v3(
                session,
                completion_id=pending.completion_id,
                request=seal_request,
                tenant_id="t",
                now=now,
            )
            assert sealed.state == "sealed"
            assert sealed.seal_request_digest == actual_product_canonical_digest_v2(
                seal_request.model_dump(mode="json", by_alias=True)
            )
            assert (
                await seal_evidence_bundle_completion_v3(
                    session,
                    completion_id=pending.completion_id,
                    request=seal_request,
                    tenant_id="t",
                    now=now,
                )
            ).completion_digest == sealed.completion_digest

            reversed_receipts = list(reversed(receipt_digests))
            second_payload = {
                "schemaVersion": "authored-evidence-bundle-completion-seal/v3",
                "expectedCasVersion": 1,
                "receiptDigests": reversed_receipts,
            }
            second_authority, second_authority_digest = authority(second_payload, "completion_nonce_0000000002")
            second_digest = actual_product_canonical_digest_v2(
                {
                    "graphRootDigest": graph.root_digest,
                    "requiredCapabilityIds": definition.all_required_capability_ids,
                    "receiptDigests": reversed_receipts,
                    "authorityDigest": second_authority_digest,
                    "state": "sealed",
                }
            )
            with pytest.raises(
                SemanticContractError,
                match="authored_evidence_completion_second_seal_conflict",
            ):
                await seal_evidence_bundle_completion_v3(
                    session,
                    completion_id=pending.completion_id,
                    request=EvidenceBundleCompletionSealRequestV3(
                        expected_cas_version=1,
                        receipt_digests=reversed_receipts,
                        completion_digest=second_digest,
                        authority=second_authority,
                    ),
                    tenant_id="t",
                    now=now,
                )
            assert cas_calls == [
                AuthoredEvidenceBundleCompletionV3,
                AuthoredEvidenceBundleCompletionV3,
                AuthoredEvidenceBundleCompletionV3,
            ]
        await engine.dispose()

    asyncio.run(scenario())
