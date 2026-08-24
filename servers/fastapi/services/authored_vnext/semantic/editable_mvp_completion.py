"""Strict Gate 1E seal import and current-Scene readiness matching."""

from __future__ import annotations

import base64
import binascii
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
from typing import Any
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from models.authored_vnext.deck import DeckGenerationSnapshot
from models.authored_vnext.semantic.editable_mvp_completion import (
    EditableMvpCompletionSealImportRequestV1,
    EditableMvpCompletionSealImportResponseV1,
)
from models.sql.authored_editable_hybrid_mvp_completion import (
    AuthoredEditableHybridMvpCompletion,
)
from models.sql.authored_vnext_repository import (
    AuthoredVNextDeckRevision,
    AuthoredVNextEntityHead,
    AuthoredVNextExportJob,
)
from services.authored_vnext.generation.sql_repository import SqlGenerationRepository


PROFILE_ID = "authored-editable-hybrid-ko-en-basic-v1"
PROFILE_DIGEST = "sha256:498fee109aa0c0b2b6920c0f01dd5d16f86bc2bb089e83ab14d473416077df54"
GATE1E_MANIFEST_DIGEST = "sha256:9a9b4ce37108c60a38811c92b46ee29be321516a67b515eab9bd959c936f2023"
TRUST_DIGEST_ENV = "AUTHORED_EDITABLE_MVP_TRUST_SHA256"
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
HEAD_RE = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_ARTIFACT_IDS = (
    "source-prompt-json",
    "semantic-source-json",
    "scene-before-json",
    "scene-after-json",
    "scene-mutation-receipt-json",
    "active-manifest-json",
    "native-admission-json",
    "hybrid-input-pptx",
    "hybrid-saved-pptx",
    "hybrid-quality-json",
    "input-ooxml-readback-json",
    "saved-ooxml-readback-json",
    "first-open-com-json",
    "reopen-com-json",
    "powerpoint-com-report-json",
    "repair-observation-json",
    "powerpoint-process-delta-json",
    "independent-visual-metrics-json",
    "attempt-sidecar-json",
    "completion-seal-json",
    "browser-slide-01-png",
    "browser-slide-02-png",
    "browser-slide-03-png",
    "browser-slide-04-png",
    "browser-slide-05-png",
    "first-open-slide-01-png",
    "first-open-slide-02-png",
    "first-open-slide-03-png",
    "first-open-slide-04-png",
    "first-open-slide-05-png",
    "reopen-slide-01-png",
    "reopen-slide-02-png",
    "reopen-slide-03-png",
    "reopen-slide-04-png",
    "reopen-slide-05-png",
)


class EditableMvpCompletionError(ValueError):
    def __init__(self, code: str, *, status_code: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise EditableMvpCompletionError(
            "editable_mvp_non_canonical_json", status_code=422
        ) from error


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _without(value: dict[str, Any], *fields: str) -> dict[str, Any]:
    result = deepcopy(value)
    for field in fields:
        result.pop(field, None)
    return result


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise EditableMvpCompletionError(
                "editable_mvp_duplicate_json_key", status_code=422
            )
        value[key] = item
    return value


def _json_bytes(value: bytes, label: str, *, maximum: int) -> dict[str, Any]:
    if not value or len(value) > maximum:
        raise EditableMvpCompletionError(
            f"editable_mvp_{label}_size_invalid", status_code=422
        )
    try:
        parsed = json.loads(
            value.decode("utf-8"), object_pairs_hook=_pairs_no_duplicates
        )
    except EditableMvpCompletionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EditableMvpCompletionError(
            f"editable_mvp_{label}_json_invalid", status_code=422
        ) from error
    if not isinstance(parsed, dict):
        raise EditableMvpCompletionError(
            f"editable_mvp_{label}_object_required", status_code=422
        )
    return parsed


def _decode(encoded: str, label: str, *, maximum: int) -> bytes:
    try:
        value = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise EditableMvpCompletionError(
            f"editable_mvp_{label}_base64_invalid", status_code=422
        ) from error
    if not value or len(value) > maximum:
        raise EditableMvpCompletionError(
            f"editable_mvp_{label}_size_invalid", status_code=422
        )
    return value


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise EditableMvpCompletionError(
            f"editable_mvp_{label}_invalid", status_code=422
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise EditableMvpCompletionError(
            f"editable_mvp_{label}_invalid", status_code=422
        ) from error
    if parsed.tzinfo is None:
        raise EditableMvpCompletionError(
            f"editable_mvp_{label}_timezone_required", status_code=422
        )
    return parsed.astimezone(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _require_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or DIGEST_RE.fullmatch(value) is None:
        raise EditableMvpCompletionError(
            f"editable_mvp_{label}_digest_invalid", status_code=422
        )
    return value


def _validate_result(result: dict[str, Any], deck_id: str) -> dict[str, Any]:
    deck_subject = deck_id.removeprefix("deck:semantic-")
    try:
        presentation_id = str(uuid.UUID(hex=deck_subject))
    except ValueError as error:
        raise EditableMvpCompletionError(
            "editable_mvp_deck_subject_invalid", status_code=422
        ) from error
    if (
        result.get("schemaVersion")
        != "authored-editable-hybrid-gate1e-result-set/v1"
        or result.get("profileId") != PROFILE_ID
        or result.get("profileDigest") != PROFILE_DIGEST
        or result.get("manifestDigest") != GATE1E_MANIFEST_DIGEST
        or result.get("authorityDomain") != "actual_product"
        or result.get("suiteStatus") != "accepted"
        or result.get("executedCount") != 35
        or result.get("skippedCount") != 0
        or result.get("promotionReady") not in (None, False)
        or result.get("deploymentAuthorized") not in (None, False)
    ):
        raise EditableMvpCompletionError(
            "editable_mvp_result_contract_invalid", status_code=422
        )
    attempt = result.get("attempt")
    if not isinstance(attempt, dict):
        raise EditableMvpCompletionError(
            "editable_mvp_attempt_invalid", status_code=422
        )
    required_attempt_strings = (
        "presentationId",
        "generationAttemptId",
        "hybridExportAttemptId",
        "sceneDigest",
        "activeManifestDigest",
        "gateMinus1eResultSetDigest",
        "gate0eResultSetDigest",
        "sourceHeadCommit",
        "sourceCensusDigest",
    )
    if any(not isinstance(attempt.get(field), str) or not attempt[field] for field in required_attempt_strings):
        raise EditableMvpCompletionError(
            "editable_mvp_attempt_pin_missing", status_code=422
        )
    if attempt["presentationId"] != presentation_id or not isinstance(attempt.get("presentationRevision"), int) or attempt["presentationRevision"] < 1:
        raise EditableMvpCompletionError(
            "editable_mvp_attempt_subject_invalid", status_code=422
        )
    for field in (
        "sceneDigest",
        "activeManifestDigest",
        "gateMinus1eResultSetDigest",
        "gate0eResultSetDigest",
        "sourceCensusDigest",
    ):
        _require_digest(attempt[field], field)
    if HEAD_RE.fullmatch(attempt["sourceHeadCommit"]) is None:
        raise EditableMvpCompletionError(
            "editable_mvp_source_head_invalid", status_code=422
        )
    if not isinstance(attempt.get("sourceDirtyFileCount"), int) or attempt["sourceDirtyFileCount"] < 1:
        raise EditableMvpCompletionError(
            "editable_mvp_source_dirty_file_count_invalid", status_code=422
        )
    qids = attempt.get("expectedQids")
    if (
        not isinstance(qids, list)
        or len(qids) < 5
        or any(not isinstance(item, str) or not item for item in qids)
        or qids != sorted(set(qids))
    ):
        raise EditableMvpCompletionError(
            "editable_mvp_expected_qids_invalid", status_code=422
        )

    ordered = result.get("orderedResults")
    if not isinstance(ordered, list) or len(ordered) != 35:
        raise EditableMvpCompletionError(
            "editable_mvp_ordered_results_invalid", status_code=422
        )
    inventory = result.get("artifactInventory")
    if not isinstance(inventory, list) or len(inventory) != 35:
        raise EditableMvpCompletionError(
            "editable_mvp_artifact_inventory_invalid", status_code=422
        )
    inventory_ids = [item.get("artifactId") for item in inventory if isinstance(item, dict)]
    if inventory_ids != sorted(REQUIRED_ARTIFACT_IDS):
        raise EditableMvpCompletionError(
            "editable_mvp_artifact_inventory_membership_invalid", status_code=422
        )
    inventory_by_id = {item["artifactId"]: item for item in inventory}
    for item in inventory:
        if (
            not isinstance(item.get("versionId"), str)
            or not item["versionId"]
            or _require_digest(item.get("sha256"), "artifact") != item["sha256"]
            or not isinstance(item.get("byteLength"), int)
            or item["byteLength"] < 1
            or not isinstance(item.get("mediaType"), str)
            or not item["mediaType"]
        ):
            raise EditableMvpCompletionError(
                "editable_mvp_artifact_inventory_entry_invalid", status_code=422
            )
    for index, (expected_id, item) in enumerate(zip(REQUIRED_ARTIFACT_IDS, ordered, strict=True), start=1):
        if (
            not isinstance(item, dict)
            or item.get("ordinal") != index
            or item.get("artifactId") != expected_id
            or item.get("state") != "accepted"
            or item.get("artifactDigest") != inventory_by_id[expected_id]["sha256"]
        ):
            raise EditableMvpCompletionError(
                "editable_mvp_ordered_result_binding_invalid", status_code=422
            )
    inventory_digest = _digest(inventory)
    if result.get("artifactInventoryDigest") != inventory_digest:
        raise EditableMvpCompletionError(
            "editable_mvp_artifact_inventory_digest_invalid", status_code=422
        )
    result_core_digest = _digest(
        _without(
            result,
            "semanticValidationReceiptRef",
            "resultCoreDigest",
            "resultSetDigest",
        )
    )
    if result.get("resultCoreDigest") != result_core_digest:
        raise EditableMvpCompletionError(
            "editable_mvp_result_core_digest_invalid", status_code=422
        )
    result_set_digest = _digest(_without(result, "resultSetDigest"))
    if result.get("resultSetDigest") != result_set_digest:
        raise EditableMvpCompletionError(
            "editable_mvp_result_set_digest_invalid", status_code=422
        )
    return attempt


def _validate_trust_and_receipt(
    *,
    trust_bytes: bytes,
    receipt_bytes: bytes,
    result: dict[str, Any],
    attempt: dict[str, Any],
    now: datetime,
) -> tuple[dict[str, Any], datetime, datetime, str]:
    configured_trust_digest = os.getenv(TRUST_DIGEST_ENV, "").strip()
    if DIGEST_RE.fullmatch(configured_trust_digest) is None:
        raise EditableMvpCompletionError("editable_mvp_verifier_trust_not_configured")
    trust_digest = _sha256_bytes(trust_bytes)
    if trust_digest != configured_trust_digest:
        raise EditableMvpCompletionError(
            "editable_mvp_verifier_trust_pin_mismatch", status_code=403
        )
    trust = _json_bytes(trust_bytes, "verifier_trust", maximum=64_000)
    receipt = _json_bytes(receipt_bytes, "validation_receipt", maximum=64_000)
    if (
        trust.get("schemaVersion") != "authored-editable-hybrid-verifier-trust/v1"
        or trust.get("revoked") is not False
        or trust.get("keyPurpose") != "editable-hybrid-mvp-gate1e"
        or trust.get("algorithm", trust.get("signatureAlgorithm", "Ed25519")) != "Ed25519"
        or trust.get("authorityScope") not in ("local-shadow", "external-independent")
        or trust.get("promotionAuthorized") is not False
        or trust.get("deploymentAuthorized") is not False
        or not isinstance(trust.get("issuerId"), str)
        or not isinstance(trust.get("signerKeyId"), str)
    ):
        raise EditableMvpCompletionError(
            "editable_mvp_verifier_trust_invalid", status_code=403
        )
    if receipt.get("schemaVersion") != "authored-editable-hybrid-validation-receipt/v1" or receipt.get("issuerId") != trust["issuerId"] or receipt.get("signerKeyId") != trust["signerKeyId"] or receipt.get("authorityScope") != trust["authorityScope"] or receipt.get("promotionAuthorized") is not False or receipt.get("deploymentAuthorized") is not False:
        raise EditableMvpCompletionError(
            "editable_mvp_validation_receipt_issuer_invalid", status_code=403
        )
    nonce = receipt.get("nonce")
    if not isinstance(nonce, str) or not 8 <= len(nonce) <= 256:
        raise EditableMvpCompletionError(
            "editable_mvp_validation_receipt_nonce_invalid", status_code=422
        )
    expected_subject = {
        "subjectManifestDigest": result["manifestDigest"],
        "subjectProfileDigest": result["profileDigest"],
        "subjectResultCoreDigest": result["resultCoreDigest"],
        "subjectArtifactInventoryDigest": result["artifactInventoryDigest"],
        "presentationId": attempt["presentationId"],
        "presentationRevision": attempt["presentationRevision"],
        "generationAttemptId": attempt["generationAttemptId"],
        "hybridExportAttemptId": attempt["hybridExportAttemptId"],
        "authorityScope": trust["authorityScope"],
        "promotionAuthorized": False,
        "deploymentAuthorized": False,
    }
    if any(receipt.get(field) != value for field, value in expected_subject.items()):
        raise EditableMvpCompletionError(
            "editable_mvp_validation_receipt_subject_mismatch", status_code=403
        )
    receipt_digest = _digest(_without(receipt, "receiptDigest", "signatureBase64"))
    if receipt.get("receiptDigest") != receipt_digest:
        raise EditableMvpCompletionError(
            "editable_mvp_validation_receipt_digest_invalid", status_code=403
        )
    try:
        public_key = serialization.load_der_public_key(
            base64.b64decode(trust.get("publicKeySpkiBase64", ""), validate=True)
        )
        signature = base64.b64decode(receipt.get("signatureBase64", ""), validate=True)
    except (TypeError, ValueError, binascii.Error) as error:
        raise EditableMvpCompletionError(
            "editable_mvp_validation_receipt_key_or_signature_invalid",
            status_code=403,
        ) from error
    if not isinstance(public_key, Ed25519PublicKey):
        raise EditableMvpCompletionError(
            "editable_mvp_validation_receipt_key_algorithm_invalid",
            status_code=403,
        )
    try:
        public_key.verify(signature, receipt_digest.encode("ascii"))
    except InvalidSignature as error:
        raise EditableMvpCompletionError(
            "editable_mvp_validation_receipt_signature_invalid", status_code=403
        ) from error
    issued_at = _timestamp(receipt.get("issuedAt"), "receipt_issued_at")
    expires_at = _timestamp(receipt.get("expiresAt"), "receipt_expires_at")
    if issued_at > now + timedelta(seconds=30) or expires_at <= now or expires_at - issued_at > timedelta(minutes=30):
        raise EditableMvpCompletionError(
            "editable_mvp_validation_receipt_expired_or_outside_window",
            status_code=403,
        )
    if trust.get("notBefore") is not None and _timestamp(trust["notBefore"], "trust_not_before") > now:
        raise EditableMvpCompletionError(
            "editable_mvp_verifier_trust_not_current", status_code=403
        )
    if trust.get("expiresAt") is not None:
        trust_expires_at = _timestamp(trust["expiresAt"], "trust_expires_at")
        if trust_expires_at <= now or expires_at > trust_expires_at:
            raise EditableMvpCompletionError(
                "editable_mvp_verifier_trust_expired", status_code=403
            )
    receipt_ref = result.get("semanticValidationReceiptRef")
    if (
        not isinstance(receipt_ref, dict)
        or receipt_ref.get("artifactId") != "gate1e-semantic-validation-receipt"
        or receipt_ref.get("sha256") != _sha256_bytes(receipt_bytes)
        or receipt_ref.get("byteLength") != len(receipt_bytes)
        or receipt_ref.get("mediaType") != "application/json"
        or receipt_ref.get("receiptDigest") != receipt_digest
        or receipt_ref.get("issuerId") != trust["issuerId"]
        or receipt_ref.get("signerKeyId") != trust["signerKeyId"]
        or receipt_ref.get("authorityScope") != trust["authorityScope"]
        or receipt_ref.get("promotionAuthorized") is not False
        or receipt_ref.get("deploymentAuthorized") is not False
    ):
        raise EditableMvpCompletionError(
            "editable_mvp_validation_receipt_reference_invalid", status_code=422
        )
    return receipt, issued_at, expires_at, trust_digest


def _response(
    record: AuthoredEditableHybridMvpCompletion, *, idempotent: bool
) -> EditableMvpCompletionSealImportResponseV1:
    return EditableMvpCompletionSealImportResponseV1(
        completionId=record.id,
        deckId=record.deck_id,
        deckRevision=record.deck_revision,
        deckDigest=record.deck_digest,
        sceneDigest=record.scene_digest,
        hybridExportAttemptId=record.hybrid_export_attempt_id,
        profileId=PROFILE_ID,
        resultSetDigest=record.result_set_digest,
        artifactInventoryDigest=record.artifact_inventory_digest,
        expiresAt=_as_utc(record.expires_at),
        idempotent=idempotent,
        editableHybridMvpReady=True,
        nativeEditableReady=True,
        promotionReady=False,
        deploymentAuthorized=False,
    )


def seal_import_editable_mvp_completion(
    repository: SqlGenerationRepository,
    *,
    deck_id: str,
    request: EditableMvpCompletionSealImportRequestV1,
    now: datetime | None = None,
) -> EditableMvpCompletionSealImportResponseV1:
    evaluated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    result_bytes = _decode(request.sealed_result_set_base64, "result_set", maximum=4_000_000)
    receipt_bytes = _decode(request.validation_receipt_base64, "validation_receipt", maximum=64_000)
    trust_bytes = _decode(request.verifier_trust_base64, "verifier_trust", maximum=64_000)
    result = _json_bytes(result_bytes, "result_set", maximum=4_000_000)
    attempt = _validate_result(result, deck_id)
    receipt, issued_at, expires_at, trust_digest = _validate_trust_and_receipt(
        trust_bytes=trust_bytes,
        receipt_bytes=receipt_bytes,
        result=result,
        attempt=attempt,
        now=evaluated_at,
    )
    if (
        request.expected_deck_revision != attempt["presentationRevision"]
        or request.expected_deck_digest != attempt["activeManifestDigest"]
        or request.expected_scene_digest != attempt["sceneDigest"]
        or request.expected_hybrid_export_attempt_id != attempt["hybridExportAttemptId"]
    ):
        raise EditableMvpCompletionError("editable_mvp_request_attempt_pin_mismatch")
    try:
        export_attempt_uuid = uuid.UUID(attempt["hybridExportAttemptId"])
    except ValueError as error:
        raise EditableMvpCompletionError(
            "editable_mvp_export_attempt_id_invalid", status_code=422
        ) from error
    base = repository._base
    sessions = repository._sessions
    scope = (
        AuthoredVNextEntityHead.owner_id == base.owner_id,
        AuthoredVNextEntityHead.owner_scope == base.owner_scope,
        AuthoredVNextEntityHead.tenant_id == base.tenant_id,
    )
    result_sha256 = _sha256_bytes(result_bytes)
    with sessions() as session:
        head = session.scalar(
            select(AuthoredVNextEntityHead)
            .where(
                *scope,
                AuthoredVNextEntityHead.entity_kind == "deck",
                AuthoredVNextEntityHead.entity_id == deck_id,
            )
            .with_for_update()
        )
        if head is None:
            raise EditableMvpCompletionError(
                "editable_mvp_deck_not_found", status_code=404
            )
        if head.revision != request.expected_deck_revision or head.digest != request.expected_deck_digest:
            raise EditableMvpCompletionError("editable_mvp_stale_deck_cas")
        deck_row = session.scalar(
            select(AuthoredVNextDeckRevision).where(
                AuthoredVNextDeckRevision.owner_id == base.owner_id,
                AuthoredVNextDeckRevision.owner_scope == base.owner_scope,
                AuthoredVNextDeckRevision.tenant_id == base.tenant_id,
                AuthoredVNextDeckRevision.deck_id == deck_id,
                AuthoredVNextDeckRevision.revision == head.revision,
                AuthoredVNextDeckRevision.digest == head.digest,
            )
        )
        scene = deck_row.payload.get("scene") if deck_row is not None else None
        if (
            deck_row is None
            or not isinstance(scene, dict)
            or scene.get("sceneDigest") != request.expected_scene_digest
            or scene.get("hybridProfileId") != PROFILE_ID
        ):
            raise EditableMvpCompletionError("editable_mvp_stale_scene_cas")
        export_job = session.scalar(
            select(AuthoredVNextExportJob).where(
                AuthoredVNextExportJob.id == export_attempt_uuid,
                AuthoredVNextExportJob.owner_id == base.owner_id,
                AuthoredVNextExportJob.owner_scope == base.owner_scope,
                AuthoredVNextExportJob.tenant_id == base.tenant_id,
                AuthoredVNextExportJob.deck_id == deck_id,
                AuthoredVNextExportJob.deck_revision == head.revision,
                AuthoredVNextExportJob.deck_digest == head.digest,
                AuthoredVNextExportJob.state == "succeeded",
            )
        )
        if export_job is None:
            raise EditableMvpCompletionError("editable_mvp_export_attempt_not_current")
        existing = session.scalar(
            select(AuthoredEditableHybridMvpCompletion).where(
                AuthoredEditableHybridMvpCompletion.owner_id == base.owner_id,
                AuthoredEditableHybridMvpCompletion.owner_scope == base.owner_scope,
                AuthoredEditableHybridMvpCompletion.tenant_id == base.tenant_id,
                AuthoredEditableHybridMvpCompletion.deck_id == deck_id,
                AuthoredEditableHybridMvpCompletion.deck_revision == head.revision,
            )
        )
        if existing is not None:
            if existing.sealed_result_sha256 == result_sha256 and existing.sealed_result_bytes == result_bytes:
                return _response(existing, idempotent=True)
            raise EditableMvpCompletionError("editable_mvp_completion_already_sealed")
        record = AuthoredEditableHybridMvpCompletion(
            owner_id=base.owner_id,
            owner_scope=base.owner_scope,
            tenant_id=base.tenant_id,
            deck_id=deck_id,
            deck_revision=head.revision,
            deck_digest=head.digest,
            scene_digest=request.expected_scene_digest,
            profile_id=PROFILE_ID,
            profile_digest=PROFILE_DIGEST,
            gate_manifest_digest=GATE1E_MANIFEST_DIGEST,
            generation_attempt_id=attempt["generationAttemptId"],
            hybrid_export_attempt_id=attempt["hybridExportAttemptId"],
            artifact_inventory_digest=result["artifactInventoryDigest"],
            result_core_digest=result["resultCoreDigest"],
            result_set_digest=result["resultSetDigest"],
            sealed_result_sha256=result_sha256,
            sealed_result_bytes=result_bytes,
            validation_receipt_bytes=receipt_bytes,
            verifier_trust_bytes=trust_bytes,
            verifier_trust_sha256=trust_digest,
            receipt_digest=receipt["receiptDigest"],
            issuer_id=receipt["issuerId"],
            signer_key_id=receipt["signerKeyId"],
            receipt_nonce=receipt["nonce"],
            issued_at=issued_at,
            expires_at=expires_at,
        )
        session.add(record)
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            concurrent = session.scalar(
                select(AuthoredEditableHybridMvpCompletion).where(
                    AuthoredEditableHybridMvpCompletion.owner_id == base.owner_id,
                    AuthoredEditableHybridMvpCompletion.owner_scope == base.owner_scope,
                    AuthoredEditableHybridMvpCompletion.tenant_id == base.tenant_id,
                    AuthoredEditableHybridMvpCompletion.deck_id == deck_id,
                    AuthoredEditableHybridMvpCompletion.deck_revision == head.revision,
                )
            )
            if concurrent is not None and concurrent.sealed_result_sha256 == result_sha256 and concurrent.sealed_result_bytes == result_bytes:
                return _response(concurrent, idempotent=True)
            raise EditableMvpCompletionError(
                "editable_mvp_completion_cas_or_nonce_conflict"
            ) from error
        session.refresh(record)
        return _response(record, idempotent=False)


def match_current_editable_mvp_completion(
    repository: SqlGenerationRepository,
    *,
    deck: DeckGenerationSnapshot,
    now: datetime | None = None,
) -> tuple[AuthoredEditableHybridMvpCompletion | None, str]:
    evaluated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if os.getenv("AUTHORED_EDITABLE_MVP_KILL_SWITCH", "false").strip().lower() == "true":
        return None, "editable_hybrid_mvp_kill_switch_active"
    scene = deck.scene
    if scene is None or scene.hybrid_profile_id != PROFILE_ID:
        return None, "editable_hybrid_mvp_not_evaluated"
    configured_trust_digest = os.getenv(TRUST_DIGEST_ENV, "").strip()
    if DIGEST_RE.fullmatch(configured_trust_digest) is None:
        return None, "editable_hybrid_mvp_trust_not_configured"
    base = repository._base
    with repository._sessions() as session:
        record = session.scalar(
            select(AuthoredEditableHybridMvpCompletion).where(
                AuthoredEditableHybridMvpCompletion.owner_id == base.owner_id,
                AuthoredEditableHybridMvpCompletion.owner_scope == base.owner_scope,
                AuthoredEditableHybridMvpCompletion.tenant_id == base.tenant_id,
                AuthoredEditableHybridMvpCompletion.deck_id == deck.deck_id,
                AuthoredEditableHybridMvpCompletion.deck_revision == deck.revision,
            )
        )
        if record is None:
            return None, "editable_hybrid_mvp_completion_not_sealed"
        if (
            record.deck_digest != deck.digest
            or record.scene_digest != scene.scene_digest
            or record.profile_id != PROFILE_ID
            or record.profile_digest != PROFILE_DIGEST
            or record.gate_manifest_digest != GATE1E_MANIFEST_DIGEST
            or record.artifact_count != 35
            or record.skipped_count != 0
            or record.cas_version != 1
        ):
            return None, "editable_hybrid_mvp_completion_binding_stale"
        if (
            _sha256_bytes(record.sealed_result_bytes) != record.sealed_result_sha256
            or _sha256_bytes(record.verifier_trust_bytes)
            != record.verifier_trust_sha256
        ):
            return None, "editable_hybrid_mvp_completion_bytes_changed"
        if _as_utc(record.expires_at) <= evaluated_at:
            return None, "editable_hybrid_mvp_completion_expired"
        if record.verifier_trust_sha256 != configured_trust_digest:
            return None, "editable_hybrid_mvp_trust_pin_changed"
        try:
            export_attempt_uuid = uuid.UUID(record.hybrid_export_attempt_id)
        except ValueError:
            return None, "editable_hybrid_mvp_export_attempt_invalid"
        export_job = session.scalar(
            select(AuthoredVNextExportJob).where(
                AuthoredVNextExportJob.id == export_attempt_uuid,
                AuthoredVNextExportJob.owner_id == base.owner_id,
                AuthoredVNextExportJob.owner_scope == base.owner_scope,
                AuthoredVNextExportJob.tenant_id == base.tenant_id,
                AuthoredVNextExportJob.deck_id == deck.deck_id,
                AuthoredVNextExportJob.deck_revision == deck.revision,
                AuthoredVNextExportJob.deck_digest == deck.digest,
                AuthoredVNextExportJob.state == "succeeded",
            )
        )
        if export_job is None:
            return None, "editable_hybrid_mvp_export_attempt_not_current"
        return record, "editable_hybrid_mvp_completion_current"
