"""Fail-closed actual-product signer authority for bounded-Hybrid v3."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import os
from functools import cmp_to_key
from typing import Any
import unicodedata

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.authored_vnext.semantic.release_authority_v3 import (
    CapabilityReceiptIssueRequestV3,
    EvidenceIssuerEnvelopeV3,
)
from models.authored_vnext.semantic.readiness import (
    CapabilityReceiptIssueRequestV2,
    CapabilityReceiptV2,
)
from models.sql.authored_capability_receipt import AuthoredCapabilityReceiptRecordV2
from models.sql.authored_release_evidence_v3 import AuthoredReleaseNonceV3
from models.sql.authored_semantic_bundle import AuthoredSemanticExportBinding
from services.authored_vnext.semantic.canonical import (
    SemanticContractError,
)
from services.authored_vnext.semantic.readiness import CapabilityEvidenceValidatorV2
from services.authored_vnext.semantic.readiness_v3 import (
    ReleaseAuthorityTargetV3,
    issue_capability_receipt_v3,
    release_authority_target_v3_from_environment,
)
from utils.datetime_utils import get_current_utc_datetime


_MAX_FUTURE_SKEW = timedelta(seconds=30)
_MAX_AUTHORITY_TTL = timedelta(minutes=30)
_READBACK_TTL = timedelta(minutes=5)
READBACK_SIGNER_KEY_ID_V3 = "authored-release-readback-v3-20260823"
READBACK_PUBLIC_KEY_DIGEST_V3 = "sha256:4e757e6f367fda82dc57b42d952d6d7bd5a75de53b4cff806f7434eb54e75f87"


class BoundEvidenceSubjectRejectionV3(SemanticContractError):
    def __init__(self, status_code: int, code: str) -> None:
        super().__init__(code)
        self.status_code = status_code


def validate_bound_evidence_subject_v3(
    candidate: dict[str, Any],
    *,
    expected: dict[str, Any],
    phase: str,
    now: datetime | None = None,
) -> None:
    """Validate one evidence subject against persisted product identity pins."""

    def reject(status_code: int, code: str) -> None:
        raise BoundEvidenceSubjectRejectionV3(status_code, code)

    is_post_issue = phase.startswith("post-")
    if candidate.get("schemaVersion") != "authored-capability-receipt-bound-subject/v3":
        reject(422, "evidence-schema-version-unsupported")
    if candidate["authorityDomain"] != expected["authorityDomain"]:
        reject(403, "evidence-authority-domain-not-allowed")
    if candidate["evidenceClass"] != expected["evidenceClass"]:
        reject(422, "evidence-class-mismatch")
    equality_rules = (
        ("evidencePolicyId", 422, "evidence-policy-id-mismatch"),
        ("evidencePolicyDigest", 422, "evidence-policy-digest-mismatch"),
        ("issuerId", 403, "evidence-issuer-not-allowed"),
        ("signerKeyId", 403, "evidence-signer-key-purpose-mismatch"),
        ("tenantId", 403, "evidence-tenant-scope-mismatch"),
        ("ownerId", 403, "evidence-owner-scope-mismatch"),
        ("presentationId", 422, "evidence-presentation-mismatch"),
        ("presentationRevision", 409, "evidence-presentation-revision-stale"),
        ("operationId", 422, "evidence-operation-mismatch"),
        ("generationAttemptId", 422, "evidence-generation-attempt-mismatch"),
        ("exportAttemptId", 422, "evidence-export-attempt-mismatch"),
        ("bindingDigest", 409, "evidence-binding-digest-mismatch"),
        ("rendererSourceDigest", 409, "evidence-renderer-source-mismatch"),
        ("inputPptxDigest", 409, "evidence-input-pptx-mismatch"),
        ("savedCopyPptxDigest", 409, "evidence-saved-copy-pptx-mismatch"),
        ("reopenInputPptxDigest", 409, "evidence-reopen-input-pptx-mismatch"),
        ("releaseProfileId", 422, "evidence-release-profile-id-mismatch"),
        ("releaseProfileDigest", 409, "evidence-release-profile-digest-mismatch"),
        ("featureSetDigest", 409, "evidence-feature-set-digest-mismatch"),
        ("environmentAttestationDigest", 403, "evidence-environment-attestation-mismatch"),
        ("workerAttestationDigest", 403, "evidence-worker-attestation-mismatch"),
    )
    for field, status_code, code in equality_rules:
        if candidate[field] != expected[field]:
            reject(status_code, code)
    observed_at = _utc(now or get_current_utc_datetime())
    issued_at = datetime.fromisoformat(candidate["issuedAt"].replace("Z", "+00:00"))
    expires_at = datetime.fromisoformat(candidate["expiresAt"].replace("Z", "+00:00"))
    if issued_at > observed_at + _MAX_FUTURE_SKEW:
        reject(422, "evidence-issued-at-future")
    if expires_at <= observed_at:
        reject(422, "evidence-expired")
    if candidate["manifestDigest"] != expected["manifestDigest"]:
        reject(409, "active-manifest-changed" if is_post_issue else "evidence-manifest-digest-mismatch")
    if (
        candidate["fontInventory"]["faces"][0]["sourceContainerSha256"]
        != expected["fontInventory"]["faces"][0]["sourceContainerSha256"]
    ):
        reject(409, "font-source-byte-mismatch")
    if candidate["allRequiredCapabilityIds"] != expected["allRequiredCapabilityIds"]:
        reject(422, "capability-set-incomplete")
    if candidate.get("receipt") != expected.get("receipt"):
        reject(422, "evidence-receipt-payload-mismatch")
    if is_post_issue and candidate["artifactReference"]["versionId"] != expected["artifactReference"]["versionId"]:
        reject(409, "durable-artifact-version-mismatch")


def build_bound_capability_subject_v3(
    *,
    receipt_wire: dict[str, Any],
    authority: EvidenceIssuerEnvelopeV3,
    binding: AuthoredSemanticExportBinding,
    target: ReleaseAuthorityTargetV3,
    tenant_id: str,
) -> dict[str, Any]:
    payload = dict(binding.payload)
    font_digest = os.getenv("AUTHORED_RELEASE_FONT_INVENTORY_DIGEST", "").strip()
    environment_digest = os.getenv("AUTHORED_RELEASE_ENVIRONMENT_ATTESTATION_DIGEST", "").strip()
    worker_digest = os.getenv("AUTHORED_RELEASE_WORKER_ATTESTATION_DIGEST", "").strip()
    if not all(value.startswith("sha256:") for value in (font_digest, environment_digest, worker_digest)):
        raise SemanticContractError("authored_bound_evidence_runtime_pins_unconfigured")
    return {
        "schemaVersion": "authored-capability-receipt-bound-subject/v3",
        "authorityDomain": authority.authority_domain,
        "evidenceClass": authority.evidence_class,
        "evidencePolicyId": receipt_wire["evidencePolicyId"],
        "evidencePolicyDigest": receipt_wire["policyDigest"],
        "issuerId": authority.issuer_id,
        "signerKeyId": authority.signer_key_id,
        "issuedAt": authority.issued_at.isoformat().replace("+00:00", "Z"),
        "expiresAt": authority.expires_at.isoformat().replace("+00:00", "Z"),
        "nonce": authority.nonce,
        "tenantId": tenant_id,
        "ownerId": str(binding.owner_id),
        "presentationId": str(binding.presentation_id),
        "presentationRevision": binding.presentation_revision,
        "operationId": payload.get("operationId"),
        "generationAttemptId": payload.get("generationAttemptId"),
        "manifestDigest": binding.canonical_digest,
        "exportAttemptId": str(binding.export_attempt_id),
        "bindingDigest": binding.binding_digest,
        "rendererSourceDigest": binding.renderer_source_digest,
        "inputPptxDigest": binding.pptx_digest,
        "savedCopyPptxDigest": binding.pptx_digest,
        "reopenInputPptxDigest": binding.pptx_digest,
        "releaseProfileId": target.profile_id,
        "releaseProfileDigest": target.profile_definition_digest,
        "featureSetDigest": receipt_wire["featureSetDigest"],
        "environmentAttestationDigest": environment_digest,
        "workerAttestationDigest": worker_digest,
        "fontInventory": {"faces": [{"sourceContainerSha256": font_digest}]},
        "allRequiredCapabilityIds": list(target.profile_definition.all_required_capability_ids),
        "artifactReference": {
            "artifactId": "semantic-export-binding",
            "versionId": str(binding.id),
            "sha256": binding.binding_digest,
        },
        "completionSeal": None,
        "receipt": receipt_wire,
    }


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _trust_bundle() -> list[dict[str, Any]]:
    raw = os.getenv("AUTHORED_RELEASE_SIGNER_TRUST_BUNDLE_JSON", "")
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as error:
        raise SemanticContractError("authored_evidence_signer_trust_unconfigured") from error
    if not isinstance(parsed, list) or not parsed:
        raise SemanticContractError("authored_evidence_signer_trust_unconfigured")
    return [item for item in parsed if isinstance(item, dict)]


def sign_server_readback_v3(
    payload: dict[str, Any], *, execution_nonce: str, now: datetime | None = None
) -> dict[str, Any]:
    expected_nonce = os.getenv("AUTHORED_RELEASE_GATE1_EXECUTION_NONCE", "").strip()
    if (
        not expected_nonce
        or execution_nonce != expected_nonce
        or not 16 <= len(execution_nonce) <= 128
        or not execution_nonce.replace("-", "").replace("_", "").isalnum()
    ):
        raise SemanticContractError("authored_release_readback_execution_nonce_invalid")
    encoded_key = os.getenv("AUTHORED_RELEASE_READBACK_ED25519_PRIVATE_KEY_BASE64", "")
    try:
        private_key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(encoded_key, validate=True))
    except (ValueError, TypeError) as error:
        raise SemanticContractError("authored_release_readback_signer_unconfigured") from error
    public_key_bytes = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    if "sha256:" + sha256(public_key_bytes).hexdigest() != READBACK_PUBLIC_KEY_DIGEST_V3:
        raise SemanticContractError("authored_release_readback_signer_digest_mismatch")
    issued_at = _utc(now or get_current_utc_datetime()).replace(microsecond=0)
    unsigned = dict(payload)
    unsigned.pop("serverReadbackDigest", None)
    unsigned.pop("readbackSignerKeyId", None)
    unsigned.pop("readbackSignatureBase64", None)
    unsigned.update(
        {
            "issuedAt": issued_at.isoformat().replace("+00:00", "Z"),
            "expiresAt": (issued_at + _READBACK_TTL).isoformat().replace("+00:00", "Z"),
            "executionNonce": execution_nonce,
        }
    )
    digest = actual_product_canonical_digest_v2(unsigned)
    return {
        **unsigned,
        "serverReadbackDigest": digest,
        "readbackSignerKeyId": READBACK_SIGNER_KEY_ID_V3,
        "readbackSignatureBase64": base64.b64encode(private_key.sign(digest.encode("ascii"))).decode("ascii"),
    }


def release_trust_readback_v3(*, execution_nonce: str) -> dict[str, Any]:
    environment_id = os.getenv("AUTHORED_RELEASE_AUTHORITY_ENVIRONMENT_ID", "").strip()
    keys = []
    for item in _trust_bundle():
        try:
            public_bytes = base64.b64decode(str(item["publicKeyBase64"]), validate=True)
        except (KeyError, ValueError, TypeError) as error:
            raise SemanticContractError("authored_evidence_signer_trust_unconfigured") from error
        keys.append(
            {
                "authorityDomain": item.get("authorityDomain"),
                "issuerId": item.get("issuerId"),
                "signerKeyId": item.get("signerKeyId"),
                "environmentId": item.get("environmentId"),
                "keyPurpose": item.get("keyPurpose"),
                "rotationEpoch": item.get("rotationEpoch"),
                "algorithm": item.get("algorithm"),
                "revoked": item.get("revoked"),
                "publicKeyDigest": "sha256:" + sha256(public_bytes).hexdigest(),
            }
        )
    body = {
        "schemaVersion": "authored-release-trust-readback/v3",
        "environmentId": environment_id,
        "registeredKeys": keys,
        "trustBundleDigest": actual_product_canonical_digest_v2(keys),
    }
    return sign_server_readback_v3(body, execution_nonce=execution_nonce)


def validate_server_readback_v3(
    payload: dict[str, Any], *, execution_nonce: str, public_key_bytes: bytes, now: datetime | None = None
) -> None:
    observed_at = _utc(now or get_current_utc_datetime())
    if (
        payload.get("executionNonce") != execution_nonce
        or payload.get("readbackSignerKeyId") != READBACK_SIGNER_KEY_ID_V3
        or "sha256:" + sha256(public_key_bytes).hexdigest() != READBACK_PUBLIC_KEY_DIGEST_V3
    ):
        raise SemanticContractError("authored_release_readback_identity_invalid")
    try:
        issued_at = datetime.fromisoformat(str(payload["issuedAt"]).replace("Z", "+00:00"))
        expires_at = datetime.fromisoformat(str(payload["expiresAt"]).replace("Z", "+00:00"))
        signature = base64.b64decode(str(payload["readbackSignatureBase64"]), validate=True)
        digest = str(payload["serverReadbackDigest"])
    except (KeyError, ValueError, TypeError) as error:
        raise SemanticContractError("authored_release_readback_invalid") from error
    if (
        issued_at > observed_at + _MAX_FUTURE_SKEW
        or expires_at <= observed_at
        or expires_at - issued_at != _READBACK_TTL
    ):
        raise SemanticContractError("authored_release_readback_stale")
    unsigned = dict(payload)
    for field in ("serverReadbackDigest", "readbackSignerKeyId", "readbackSignatureBase64"):
        unsigned.pop(field, None)
    if actual_product_canonical_digest_v2(unsigned) != digest:
        raise SemanticContractError("authored_release_readback_digest_mismatch")
    try:
        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(signature, digest.encode("ascii"))
    except (ValueError, InvalidSignature) as error:
        raise SemanticContractError("authored_release_readback_signature_invalid") from error


def _signed_payload(receipt_payload: dict[str, Any], authority: EvidenceIssuerEnvelopeV3) -> dict[str, Any]:
    authority_payload = authority.model_dump(mode="json", by_alias=True)
    authority_payload.pop("signatureBase64", None)
    return {"receipt": receipt_payload, "authority": authority_payload}


_JS_SAFE_INTEGER_MAX = (1 << 53) - 1
_ACTUAL_PRODUCT_ARRAY_SORT_KEYS_V2 = {
    "$.capabilityIds": ("scalar",),
    "$.receiptDigests": ("scalar",),
    "$.orderedResults": ("ordinal", "caseId"),
    "$.cases": ("ordinal", "caseId"),
    "$.artifacts": ("artifactId", "sha256"),
    "$.fontFaces": ("family", "subfamily", "weight", "italic", "ttcCollectionIndex"),
}


def _js_string_v2(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    if isinstance(value, dict):
        return "[object Object]"
    if isinstance(value, (list, tuple)):
        return ",".join(_js_string_v2(item) for item in value)
    return str(value)


def _compare_actual_product_array_entries_v2(left: Any, right: Any, fields: tuple[str, ...]) -> int:
    if fields == ("scalar",):
        left_bytes = _js_string_v2(left).encode("utf-8")
        right_bytes = _js_string_v2(right).encode("utf-8")
        return (left_bytes > right_bytes) - (left_bytes < right_bytes)
    for field in fields:
        left_value = left.get(field) if isinstance(left, dict) else None
        right_value = right.get(field) if isinstance(right, dict) else None
        if (
            isinstance(left_value, (int, float))
            and not isinstance(left_value, bool)
            and isinstance(right_value, (int, float))
            and not isinstance(right_value, bool)
            and left_value != right_value
        ):
            return (left_value > right_value) - (left_value < right_value)
        left_bytes = _js_string_v2(left_value if left_value is not None else "").encode("utf-8")
        right_bytes = _js_string_v2(right_value if right_value is not None else "").encode("utf-8")
        result = (left_bytes > right_bytes) - (left_bytes < right_bytes)
        if result:
            return result
    return 0


def _actual_product_canonical_json_v2(value: Any, *, path: str = "$") -> str:
    """Match the Gate 1 JavaScript canonical-v2 wire contract exactly.

    Actual-product signatures cross a Python/JavaScript authority boundary.  They
    therefore use the release runner's integer-only wire canonicalization rather
    than the more permissive semantic-model canonicalization used elsewhere.
    """

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True)
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        normalized = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
        return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, (int, float)):
        if isinstance(value, float):
            if not math.isfinite(value):
                raise SemanticContractError(f"authored_release_canonical_number_non_finite:{path}")
            if value == 0 and math.copysign(1.0, value) < 0:
                raise SemanticContractError(f"authored_release_canonical_number_negative_zero:{path}")
            if not value.is_integer():
                raise SemanticContractError(f"authored_release_canonical_number_fractional:{path}")
        integer = int(value)
        if abs(integer) > _JS_SAFE_INTEGER_MAX:
            raise SemanticContractError(f"authored_release_canonical_number_out_of_safe_range:{path}")
        return str(integer)
    if isinstance(value, (list, tuple)):
        fields = _ACTUAL_PRODUCT_ARRAY_SORT_KEYS_V2.get(path)
        entries = (
            sorted(
                value,
                key=cmp_to_key(
                    lambda left, right: _compare_actual_product_array_entries_v2(left, right, fields)
                ),
            )
            if fields is not None
            else value
        )
        return "[" + ",".join(
            _actual_product_canonical_json_v2(item, path=f"{path}[{index}]")
            for index, item in enumerate(entries)
        ) + "]"
    if isinstance(value, dict):
        normalized: list[tuple[str, Any]] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise SemanticContractError(f"authored_release_canonical_key_invalid:{path}")
            normalized_key = unicodedata.normalize("NFC", key.replace("\r\n", "\n").replace("\r", "\n"))
            normalized.append((normalized_key, item))
        if len({key for key, _ in normalized}) != len(normalized):
            raise SemanticContractError(f"authored_release_canonical_key_nfc_collision:{path}")
        normalized.sort(key=lambda entry: entry[0].encode("utf-8"))
        return "{" + ",".join(
            json.dumps(key, ensure_ascii=False, separators=(",", ":"))
            + ":"
            + _actual_product_canonical_json_v2(item, path=f"{path}.{key}")
            for key, item in normalized
        ) + "}"
    raise SemanticContractError(f"authored_release_canonical_type_unsupported:{path}")


def actual_product_canonical_digest_v2(value: Any) -> str:
    """Digest an actual-product wire value with the Gate1 JS canonical-v2 contract."""

    canonical = _actual_product_canonical_json_v2(value)
    return "sha256:" + sha256(canonical.encode("utf-8")).hexdigest()


def actual_product_authority_digest(receipt_payload: dict[str, Any], authority: EvidenceIssuerEnvelopeV3) -> str:
    return actual_product_canonical_digest_v2(_signed_payload(receipt_payload, authority))


def validate_actual_product_authority_v3(
    receipt_payload: dict[str, Any],
    authority: EvidenceIssuerEnvelopeV3,
    *,
    now: datetime | None = None,
) -> str:
    observed_at = _utc(now or get_current_utc_datetime())
    issued_at = _utc(authority.issued_at)
    expires_at = _utc(authority.expires_at)
    expected_environment = os.getenv("AUTHORED_RELEASE_AUTHORITY_ENVIRONMENT_ID", "").strip()
    if (
        not expected_environment
        or authority.environment_id != expected_environment
        or issued_at > observed_at + _MAX_FUTURE_SKEW
        or expires_at <= observed_at
        or expires_at - issued_at > _MAX_AUTHORITY_TTL
    ):
        raise SemanticContractError("authored_evidence_authority_scope_invalid")
    matches = [
        item
        for item in _trust_bundle()
        if item.get("authorityDomain") == "actual_product"
        and item.get("issuerId") == authority.issuer_id
        and item.get("signerKeyId") == authority.signer_key_id
        and item.get("environmentId") == authority.environment_id
        and item.get("keyPurpose") == authority.key_purpose
        and item.get("rotationEpoch") == authority.rotation_epoch
        and item.get("algorithm") == "Ed25519"
        and item.get("revoked") is False
    ]
    if len(matches) != 1:
        raise SemanticContractError("authored_evidence_signer_not_allowed")
    try:
        public_key_bytes = base64.b64decode(str(matches[0]["publicKeyBase64"]), validate=True)
        signature = base64.b64decode(authority.signature_base64, validate=True)
        public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        authority_digest = actual_product_authority_digest(receipt_payload, authority)
        public_key.verify(signature, authority_digest.encode("ascii"))
    except (KeyError, ValueError, TypeError, InvalidSignature) as error:
        raise SemanticContractError("authored_evidence_signature_invalid") from error
    return authority_digest


def authority_metadata_v3(
    receipt_payload: dict[str, Any], authority: EvidenceIssuerEnvelopeV3, *, now: datetime | None = None
) -> dict[str, Any]:
    digest = validate_actual_product_authority_v3(receipt_payload, authority, now=now)
    return {
        "authority_domain": authority.authority_domain,
        "evidence_class": authority.evidence_class,
        "issuer_id": authority.issuer_id,
        "signer_key_id": authority.signer_key_id,
        "environment_id": authority.environment_id,
        "rotation_epoch": authority.rotation_epoch,
        "nonce": authority.nonce,
        "signature_algorithm": authority.signature_algorithm,
        "signature_base64": authority.signature_base64,
        "authority_digest": digest,
        "authority_payload": {
            "envelope": authority.model_dump(mode="json", by_alias=True),
            "signedReceipt": receipt_payload,
        },
    }


def validate_actual_product_record_v3(
    record: AuthoredCapabilityReceiptRecordV2,
    *,
    now: datetime | None = None,
) -> bool:
    if record.authority_domain != "actual_product" or record.authority_payload is None:
        return False
    try:
        envelope_payload = record.authority_payload.get("envelope")
        receipt_payload = record.authority_payload.get("signedReceipt")
        signed_subject = record.authority_payload.get("signedSubject", receipt_payload)
        if (
            not isinstance(envelope_payload, dict)
            or not isinstance(receipt_payload, dict)
            or not isinstance(signed_subject, dict)
        ):
            return False
        authority = EvidenceIssuerEnvelopeV3.model_validate(envelope_payload, strict=False)
        signed_request = CapabilityReceiptIssueRequestV2.model_validate(receipt_payload, strict=False)
        persisted_receipt = CapabilityReceiptV2.model_validate(record.payload, strict=False)
        digest = validate_actual_product_authority_v3(signed_subject, authority, now=now)
    except (ValidationError, SemanticContractError, ValueError, TypeError):
        return False
    return bool(
        record.evidence_class == authority.evidence_class
        and record.issuer_id == authority.issuer_id
        and record.signer_key_id == authority.signer_key_id
        and record.environment_id == authority.environment_id
        and record.rotation_epoch == authority.rotation_epoch
        and record.nonce == authority.nonce
        and record.signature_algorithm == authority.signature_algorithm
        and record.signature_base64 == authority.signature_base64
        and record.authority_digest == digest
        and signed_request.capability_id == persisted_receipt.capability_id
        and signed_request.state == persisted_receipt.state
        and signed_request.scope == persisted_receipt.scope
        and signed_request.environment == persisted_receipt.environment
        and signed_request.serializer_id == persisted_receipt.serializer_id
        and signed_request.serializer_version == persisted_receipt.serializer_version
        and signed_request.feature_set_digest == persisted_receipt.feature_set_digest
        and signed_request.evidence_policy_id == persisted_receipt.evidence_policy_id
        and signed_request.policy_digest == persisted_receipt.policy_digest
        and signed_request.evidence_bundle_id == persisted_receipt.evidence_bundle_id
        and signed_request.evidence_digest == persisted_receipt.evidence_digest
        and _utc(signed_request.expires_at) == _utc(persisted_receipt.expires_at)
        and signed_request.reason_code == persisted_receipt.reason_code
        and signed_request.visual_evidence_authority == persisted_receipt.visual_evidence_authority
        and record.export_attempt_id is not None
        and record.binding_digest is not None
        and signed_subject.get("exportAttemptId") == str(record.export_attempt_id)
        and signed_subject.get("bindingDigest") == record.binding_digest
    )


async def consume_authority_nonce_v3(
    session: AsyncSession,
    *,
    authority: EvidenceIssuerEnvelopeV3,
    subject_digest: str,
) -> bool:
    existing = await session.scalar(
        select(AuthoredReleaseNonceV3).where(
            AuthoredReleaseNonceV3.issuer_id == authority.issuer_id,
            AuthoredReleaseNonceV3.signer_key_id == authority.signer_key_id,
            AuthoredReleaseNonceV3.nonce == authority.nonce,
        )
    )
    if existing is not None:
        if (
            existing.authority_domain == authority.authority_domain
            and existing.evidence_class == authority.evidence_class
            and existing.subject_digest == subject_digest
        ):
            return False
        raise SemanticContractError("authored_evidence_nonce_replay")
    session.add(
        AuthoredReleaseNonceV3(
            authority_domain=authority.authority_domain,
            evidence_class=authority.evidence_class,
            issuer_id=authority.issuer_id,
            signer_key_id=authority.signer_key_id,
            nonce=authority.nonce,
            subject_digest=subject_digest,
        )
    )
    await session.flush()
    return True


async def issue_actual_product_capability_receipt_v3(
    session: AsyncSession,
    *,
    request: CapabilityReceiptIssueRequestV3,
    issuer_principal_id,
    issuer_is_admin: bool,
    tenant_id: str = "default",
    now: datetime | None = None,
    target: ReleaseAuthorityTargetV3 | None = None,
    capability_validators: dict[str, CapabilityEvidenceValidatorV2] | None = None,
) -> CapabilityReceiptV2:
    if (
        request.authority.evidence_class != "actual_product_capability"
        or request.authority.key_purpose != "actual-product-capability"
    ):
        raise SemanticContractError("authored_capability_receipt_authority_class_invalid")
    observed_at = _utc(now or get_current_utc_datetime())
    receipt_wire = request.receipt.model_dump(mode="json", by_alias=True)
    if _utc(request.receipt.expires_at) > _utc(request.authority.expires_at):
        raise SemanticContractError("authored_evidence_receipt_outlives_authority")
    binding = await session.scalar(
        select(AuthoredSemanticExportBinding).where(
            AuthoredSemanticExportBinding.tenant_id == tenant_id,
            AuthoredSemanticExportBinding.export_attempt_id == request.export_attempt_id,
            AuthoredSemanticExportBinding.binding_digest == request.binding_digest,
        )
    )
    if binding is None:
        raise SemanticContractError("authored_capability_receipt_export_binding_invalid")
    effective_target = target or release_authority_target_v3_from_environment()
    signed_subject = build_bound_capability_subject_v3(
        receipt_wire=receipt_wire,
        authority=request.authority,
        binding=binding,
        target=effective_target,
        tenant_id=tenant_id,
    )
    persisted_subject = build_bound_capability_subject_v3(
        receipt_wire=receipt_wire,
        authority=request.authority,
        binding=binding,
        target=effective_target,
        tenant_id=tenant_id,
    )
    validate_bound_evidence_subject_v3(
        signed_subject,
        expected=persisted_subject,
        phase="payload-before-issue",
        now=observed_at,
    )
    metadata = authority_metadata_v3(signed_subject, request.authority, now=observed_at)
    metadata["authority_payload"]["signedReceipt"] = receipt_wire
    metadata["authority_payload"]["signedSubject"] = signed_subject
    await consume_authority_nonce_v3(
        session,
        authority=request.authority,
        subject_digest=str(metadata["authority_digest"]),
    )
    replay = await session.scalar(
        select(AuthoredCapabilityReceiptRecordV2).where(
            AuthoredCapabilityReceiptRecordV2.issuer_id == request.authority.issuer_id,
            AuthoredCapabilityReceiptRecordV2.signer_key_id == request.authority.signer_key_id,
            AuthoredCapabilityReceiptRecordV2.nonce == request.authority.nonce,
        )
    )
    if replay is not None:
        if replay.authority_digest == metadata["authority_digest"] and validate_actual_product_record_v3(
            replay, now=observed_at
        ):
            return CapabilityReceiptV2.model_validate(replay.payload, strict=False)
        raise SemanticContractError("authored_evidence_nonce_replay")
    receipt = await issue_capability_receipt_v3(
        session,
        request=request.receipt,
        issuer_principal_id=issuer_principal_id,
        issuer_is_admin=issuer_is_admin,
        tenant_id=tenant_id,
        now=observed_at,
        target=effective_target,
        capability_validators=capability_validators,
        authority_metadata=metadata,
    )
    record = await session.scalar(
        select(AuthoredCapabilityReceiptRecordV2).where(
            AuthoredCapabilityReceiptRecordV2.tenant_id == tenant_id,
            AuthoredCapabilityReceiptRecordV2.receipt_digest == receipt.receipt_digest,
        )
    )
    assert record is not None
    record.export_attempt_id = request.export_attempt_id
    record.binding_digest = request.binding_digest
    await session.flush()
    return receipt


__all__ = [
    "BoundEvidenceSubjectRejectionV3",
    "READBACK_PUBLIC_KEY_DIGEST_V3",
    "READBACK_SIGNER_KEY_ID_V3",
    "actual_product_authority_digest",
    "actual_product_canonical_digest_v2",
    "authority_metadata_v3",
    "build_bound_capability_subject_v3",
    "consume_authority_nonce_v3",
    "issue_actual_product_capability_receipt_v3",
    "release_trust_readback_v3",
    "sign_server_readback_v3",
    "validate_actual_product_authority_v3",
    "validate_actual_product_record_v3",
    "validate_bound_evidence_subject_v3",
    "validate_server_readback_v3",
]
