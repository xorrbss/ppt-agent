"""Validated, network-free Template V2 design catalog metadata.

The catalog records where an externally inspired design was inspected without
making the external repository a runtime dependency.  It intentionally stores
metadata only: no fonts, logos, images, SVGs, or PPTX packages are vendored by
this module.  Publication of persisted Template V2 rows is a separate concern
handled by :mod:`templates.v2.publication`.
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Collection, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CATALOG_SCHEMA_VERSION = "presenton.template-v2-design-catalog/v1"
RIGHTS_SCHEMA_VERSION = "presenton.template-v2-rights-evidence/v1"
DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parent
    / "manifests"
    / "ppt_master_catalog.v1.json"
)
DEFAULT_RIGHTS_PATH = (
    Path(__file__).resolve().parent
    / "manifests"
    / "ppt_master_rights.v1.json"
)
TEMPLATE_V2_RIGHTS_APPROVER_ALLOWLIST = (
    "TEMPLATE_V2_RIGHTS_APPROVER_ALLOWLIST"
)
TEMPLATE_V2_RIGHTS_ATTESTATION_KEY = "TEMPLATE_V2_RIGHTS_ATTESTATION_KEY"

TemplateKind = Literal["brand", "layout", "deck"]
PreservationMode = Literal["native_clone", "reconstructed"]
PublicationStatus = Literal["published", "blocked"]
RightsScope = Literal["candidate_pptx", "embedded_media", "fonts"]
RequiredPermission = Literal["redistribute", "modify", "commercial_use"]
EvidenceType = Literal[
    "asset_license_manifest",
    "rights_holder_grant",
    "public_domain_declaration",
]
ApprovalStatus = Literal["pending", "approved", "rejected"]
SourceFileRole = Literal[
    "design_spec",
    "spec_lock",
    "candidate_pptx",
    "workflow",
    "repository_license",
]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_relative_source_path(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\\" in value
        or "://" in value
    ):
        raise ValueError("source path must be a normalized repository-relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("source path must be a normalized repository-relative path")
    return value


class SourceFileEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    sha256: str
    role: SourceFileRole

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _validate_relative_source_path(value)

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return value


class CatalogSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repository: str
    commit_sha: str
    path: str
    files: tuple[SourceFileEvidence, ...] = Field(min_length=1)

    @field_validator("repository")
    @classmethod
    def validate_repository(cls, value: str) -> str:
        if value != "https://github.com/hugohe3/ppt-master":
            raise ValueError("catalog source repository is not approved")
        return value

    @field_validator("commit_sha")
    @classmethod
    def validate_commit_sha(cls, value: str) -> str:
        if not _GIT_SHA_RE.fullmatch(value):
            raise ValueError("commit_sha must be a pinned 40-character git SHA")
        return value

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _validate_relative_source_path(value)

    @model_validator(mode="after")
    def require_candidate_evidence(self) -> "CatalogSource":
        roles = {item.role for item in self.files}
        required = {
            "design_spec",
            "spec_lock",
            "candidate_pptx",
            "workflow",
            "repository_license",
        }
        if not required.issubset(roles):
            raise ValueError("catalog source evidence is incomplete")
        return self


class LicenseEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repository_spdx: Literal["MIT"]
    repository_license_verified: bool
    embedded_assets_license_verified: bool
    redistribution_approved: bool
    attribution: str = Field(min_length=1, max_length=500)


class AssetRightsReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    raster_image_count: int = Field(ge=0)
    vector_image_count: int = Field(ge=0)
    pptx_count: int = Field(ge=0)
    font_file_count: int = Field(ge=0)
    copied_asset_count: Literal[0]
    unresolved_rights: tuple[str, ...] = Field(min_length=1)


class ImportCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    native_clone: bool
    text_patch: bool
    table_patch: bool
    master_layout_theme: bool
    relationships_media: bool
    image_replacement: bool


class CatalogProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    importer_version: str
    runtime_network_fetch: Literal[False]
    license: LicenseEvidence
    asset_rights_review: AssetRightsReview
    decision_reasons: tuple[str, ...] = Field(min_length=1)

    @field_validator("importer_version")
    @classmethod
    def validate_importer_version(cls, value: str) -> str:
        if not _SEMVER_RE.fullmatch(value):
            raise ValueError("importer_version must be semantic version x.y.z")
        return value


class TemplateV2CatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    template_kind: TemplateKind
    preservation_mode: PreservationMode
    publication_status: PublicationStatus
    source: CatalogSource
    provenance: CatalogProvenance
    capabilities: ImportCapabilities

    @model_validator(mode="after")
    def require_publication_rights(self) -> "TemplateV2CatalogEntry":
        license_evidence = self.provenance.license
        if self.publication_status == "published" and not (
            license_evidence.repository_license_verified
            and license_evidence.embedded_assets_license_verified
            and license_evidence.redistribution_approved
        ):
            raise ValueError("published catalog entries require verified redistribution rights")
        if self.provenance.asset_rights_review.copied_asset_count != 0:
            raise ValueError("external catalog assets must not be copied")
        return self

    def api_metadata(self) -> dict[str, Any]:
        """Return a detached API projection without any filesystem paths."""

        return {
            "source": self.source.model_dump(mode="json"),
            "provenance": self.provenance.model_dump(mode="json"),
            "capabilities": self.capabilities.model_dump(mode="json"),
            "preservation_mode": self.preservation_mode,
            "template_kind": self.template_kind,
            "publication_status": self.publication_status,
        }


class TemplateV2CatalogManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[CATALOG_SCHEMA_VERSION]
    default_template_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    entries: tuple[TemplateV2CatalogEntry, ...]

    @model_validator(mode="after")
    def require_valid_default(self) -> "TemplateV2CatalogManifest":
        identifiers = [entry.id for entry in self.entries]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("catalog template ids must be unique")
        if self.default_template_id is not None:
            default_entry = next(
                (
                    entry
                    for entry in self.entries
                    if entry.id == self.default_template_id
                ),
                None,
            )
            if default_entry is None:
                raise ValueError("catalog default_template_id must reference an entry")
            if default_entry.publication_status != "published":
                raise ValueError("catalog default template must be published")
        return self


class RightsSourceReceipt(BaseModel):
    """Immutable source coordinates prepared before any binary is imported."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repository: Literal["https://github.com/hugohe3/ppt-master"]
    revision: str
    candidate_pptx_path: str
    candidate_pptx_sha256: str
    candidate_pptx_size_bytes: int = Field(gt=0)

    @field_validator("revision")
    @classmethod
    def validate_revision(cls, value: str) -> str:
        if not _GIT_SHA_RE.fullmatch(value):
            raise ValueError("revision must be a pinned 40-character git SHA")
        return value

    @field_validator("candidate_pptx_path")
    @classmethod
    def validate_candidate_path(cls, value: str) -> str:
        return _validate_relative_source_path(value)

    @field_validator("candidate_pptx_sha256")
    @classmethod
    def validate_candidate_sha256(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("candidate_pptx_sha256 must be lowercase SHA-256")
        return value


class RightsEvidenceItem(BaseModel):
    """A local, digest-pinned document that grants rights for one source package."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,127}$")
    evidence_type: EvidenceType
    document_path: str
    document_sha256: str
    issuer: str = Field(min_length=1, max_length=300)
    license_expression: str = Field(min_length=1, max_length=300)
    covered_scopes: tuple[RightsScope, ...] = Field(min_length=1)
    covered_artifact_sha256: str
    granted_permissions: tuple[RequiredPermission, ...] = Field(min_length=1)
    attribution_text: str | None = Field(default=None, max_length=1000)
    coverage_statement: str = Field(min_length=1, max_length=1000)

    @field_validator("document_path")
    @classmethod
    def validate_document_path(cls, value: str) -> str:
        value = _validate_relative_source_path(value)
        if not value.startswith("evidence/"):
            raise ValueError("rights evidence must live below the local evidence/ directory")
        return value

    @field_validator("document_sha256", "covered_artifact_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("rights evidence digests must be lowercase SHA-256")
        return value

    @model_validator(mode="after")
    def require_unique_claims(self) -> "RightsEvidenceItem":
        if len(self.covered_scopes) != len(set(self.covered_scopes)):
            raise ValueError("covered_scopes must be unique")
        if len(self.granted_permissions) != len(set(self.granted_permissions)):
            raise ValueError("granted_permissions must be unique")
        return self


class RightsApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    status: ApprovalStatus
    reviewer: str | None = Field(default=None, min_length=1, max_length=300)
    reviewed_at: datetime | None = None
    evidence_set_sha256: str | None = None
    attestation_hmac_sha256: str | None = None
    decision: str | None = Field(default=None, min_length=1, max_length=1000)

    @field_validator("evidence_set_sha256", "attestation_hmac_sha256")
    @classmethod
    def validate_evidence_set_sha256(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256_RE.fullmatch(value):
            raise ValueError("evidence_set_sha256 must be lowercase SHA-256")
        return value

    @model_validator(mode="after")
    def require_review_metadata(self) -> "RightsApproval":
        review_fields = (
            self.reviewer,
            self.reviewed_at,
            self.evidence_set_sha256,
            self.attestation_hmac_sha256,
            self.decision,
        )
        if self.status == "pending" and any(value is not None for value in review_fields):
            raise ValueError("pending approval must not carry review metadata")
        if self.status in {"approved", "rejected"} and any(
            value is None for value in review_fields
        ):
            raise ValueError("completed approval requires reviewer, time, digest, and decision")
        if self.reviewed_at is not None and self.reviewed_at.tzinfo is None:
            raise ValueError("reviewed_at must include a timezone")
        return self


class TemplateRightsRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    template_id: str
    catalog_entry_sha256: str
    source: RightsSourceReceipt
    required_scopes: tuple[RightsScope, ...] = Field(min_length=1)
    required_permissions: tuple[RequiredPermission, ...] = Field(min_length=1)
    evidence: tuple[RightsEvidenceItem, ...]
    approval: RightsApproval

    @field_validator("catalog_entry_sha256")
    @classmethod
    def validate_catalog_entry_sha256(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("catalog_entry_sha256 must be lowercase SHA-256")
        return value

    @model_validator(mode="after")
    def require_unique_items(self) -> "TemplateRightsRecord":
        if len(self.required_scopes) != len(set(self.required_scopes)):
            raise ValueError("required_scopes must be unique")
        if len(self.required_permissions) != len(set(self.required_permissions)):
            raise ValueError("required_permissions must be unique")
        evidence_ids = [item.id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("rights evidence ids must be unique per template")
        return self


class TemplateRightsManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[RIGHTS_SCHEMA_VERSION]
    records: tuple[TemplateRightsRecord, ...]

    @model_validator(mode="after")
    def require_unique_template_ids(self) -> "TemplateRightsManifest":
        template_ids = [record.template_id for record in self.records]
        if len(template_ids) != len(set(template_ids)):
            raise ValueError("rights records must have unique template ids")
        return self


class RightsReadinessReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    template_id: str
    ready_for_publication_review: bool
    evidence_set_sha256: str
    blockers: tuple[str, ...]


def catalog_entry_sha256(entry: TemplateV2CatalogEntry) -> str:
    return _canonical_sha256(entry.model_dump(mode="json"))


def rights_evidence_set_sha256(record: TemplateRightsRecord) -> str:
    """Digest all approval inputs except the approval decision itself."""

    return _canonical_sha256(
        {
            "template_id": record.template_id,
            "catalog_entry_sha256": record.catalog_entry_sha256,
            "source": record.source.model_dump(mode="json"),
            "required_scopes": record.required_scopes,
            "required_permissions": record.required_permissions,
            "evidence": [item.model_dump(mode="json") for item in record.evidence],
        }
    )


def get_trusted_rights_approvers(
    environ: Mapping[str, str] | None = None,
) -> frozenset[str]:
    """Load operator-controlled approvers without trusting repository data alone."""

    values = os.environ if environ is None else environ
    raw_value = values.get(TEMPLATE_V2_RIGHTS_APPROVER_ALLOWLIST, "")
    entries = [item.strip() for item in raw_value.split(",") if item.strip()]
    if len(entries) != len(set(entries)):
        return frozenset()
    return frozenset(entries)


def get_rights_attestation_key(
    environ: Mapping[str, str] | None = None,
) -> bytes | None:
    """Load the deployment secret that keeps approval outside repository control."""

    values = os.environ if environ is None else environ
    raw_value = values.get(TEMPLATE_V2_RIGHTS_ATTESTATION_KEY)
    if raw_value is None:
        return None
    encoded = raw_value.encode("utf-8")
    return encoded if len(encoded) >= 32 else None


def rights_approval_attestation_hmac(
    *,
    template_id: str,
    status: Literal["approved", "rejected"],
    reviewer: str,
    reviewed_at: datetime,
    evidence_set_sha256: str,
    decision: str,
    key: bytes,
) -> str:
    """Sign the immutable approval coordinates with an operator-held secret."""

    payload = json.dumps(
        {
            "schema_version": "presenton.template-v2-rights-approval/v2",
            "template_id": template_id,
            "status": status,
            "reviewer": reviewer,
            "reviewed_at": reviewed_at.isoformat(),
            "evidence_set_sha256": evidence_set_sha256,
            "decision": decision,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def evaluate_rights_readiness(
    entry: TemplateV2CatalogEntry,
    record: TemplateRightsRecord,
    *,
    evidence_root: Path | None = None,
    trusted_approvers: Collection[str] = (),
    attestation_key: bytes | None = None,
) -> RightsReadinessReport:
    """Fail closed unless source, rights evidence, and human approval all agree."""

    blockers: list[str] = []
    evidence_digest = rights_evidence_set_sha256(record)
    candidate_files = [item for item in entry.source.files if item.role == "candidate_pptx"]
    candidate = candidate_files[0] if len(candidate_files) == 1 else None

    if record.template_id != entry.id:
        blockers.append("template_id does not match the catalog entry")
    if record.catalog_entry_sha256 != catalog_entry_sha256(entry):
        blockers.append("catalog entry digest is stale or mismatched")
    if record.source.repository != entry.source.repository:
        blockers.append("source repository does not match the catalog")
    if record.source.revision != entry.source.commit_sha:
        blockers.append("source revision does not match the pinned catalog commit")
    if candidate is None:
        blockers.append("catalog must contain exactly one candidate PPTX receipt")
    elif (
        record.source.candidate_pptx_path != candidate.path
        or record.source.candidate_pptx_sha256 != candidate.sha256
    ):
        blockers.append("candidate PPTX path or digest does not match the catalog")

    expected_scopes = {"candidate_pptx"}
    asset_review = entry.provenance.asset_rights_review
    if asset_review.raster_image_count or asset_review.vector_image_count:
        expected_scopes.add("embedded_media")
    if asset_review.font_file_count:
        expected_scopes.add("fonts")
    if not expected_scopes.issubset(set(record.required_scopes)):
        blockers.append("required rights scopes do not cover every cataloged asset class")

    expected_permissions = {"redistribute", "modify", "commercial_use"}
    if not expected_permissions.issubset(set(record.required_permissions)):
        blockers.append("required permissions are incomplete")

    root = (
        DEFAULT_RIGHTS_PATH.parent if evidence_root is None else Path(evidence_root)
    ).resolve()
    usable_evidence: list[RightsEvidenceItem] = []
    for item in record.evidence:
        try:
            evidence_path = (
                root / Path(*PurePosixPath(item.document_path).parts)
            ).resolve(strict=True)
            if not evidence_path.is_relative_to(root):
                blockers.append(f"rights evidence escapes the evidence root: {item.id}")
                continue
            document_bytes = evidence_path.read_bytes()
        except OSError:
            blockers.append(f"rights evidence document is missing: {item.id}")
            continue
        if hashlib.sha256(document_bytes).hexdigest() != item.document_sha256:
            blockers.append(f"rights evidence document digest mismatch: {item.id}")
            continue
        if item.covered_artifact_sha256 != record.source.candidate_pptx_sha256:
            blockers.append(f"rights evidence targets a different artifact: {item.id}")
            continue
        usable_evidence.append(item)

    for scope in set(record.required_scopes):
        if not any(
            scope in item.covered_scopes
            and expected_permissions.issubset(set(item.granted_permissions))
            for item in usable_evidence
        ):
            blockers.append(f"no complete verified rights evidence covers scope: {scope}")

    approval = record.approval
    if approval.status != "approved":
        blockers.append("rights approval is not approved")
    elif approval.evidence_set_sha256 != evidence_digest:
        blockers.append("rights approval digest does not match the evidence set")
    elif approval.reviewer not in frozenset(trusted_approvers):
        blockers.append("rights approval reviewer is not operator-trusted")
    elif attestation_key is None:
        blockers.append("rights approval attestation key is unavailable")
    else:
        expected_attestation = rights_approval_attestation_hmac(
            template_id=record.template_id,
            status="approved",
            reviewer=approval.reviewer,
            reviewed_at=approval.reviewed_at,
            evidence_set_sha256=approval.evidence_set_sha256,
            decision=approval.decision,
            key=attestation_key,
        )
        if not hmac.compare_digest(
            approval.attestation_hmac_sha256,
            expected_attestation,
        ):
            blockers.append("rights approval attestation is invalid")

    return RightsReadinessReport(
        template_id=entry.id,
        ready_for_publication_review=not blockers,
        evidence_set_sha256=evidence_digest,
        blockers=tuple(blockers),
    )


def load_template_v2_catalog(
    manifest_path: Path | None = None,
) -> TemplateV2CatalogManifest:
    """Load one local manifest; URLs and remote fallbacks are not accepted."""

    path = DEFAULT_CATALOG_PATH if manifest_path is None else Path(manifest_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return TemplateV2CatalogManifest.model_validate(payload)


def load_template_rights_manifest(
    manifest_path: Path | None = None,
) -> TemplateRightsManifest:
    """Load local rights claims; existence and digests are checked during evaluation."""

    path = DEFAULT_RIGHTS_PATH if manifest_path is None else Path(manifest_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return TemplateRightsManifest.model_validate(payload)


@lru_cache(maxsize=1)
def _default_catalog() -> TemplateV2CatalogManifest:
    return load_template_v2_catalog()


@lru_cache(maxsize=1)
def _default_rights_manifest() -> TemplateRightsManifest:
    return load_template_rights_manifest()


def get_template_v2_catalog_entry(
    template_id: str,
) -> TemplateV2CatalogEntry | None:
    return next(
        (entry for entry in _default_catalog().entries if entry.id == template_id),
        None,
    )


def get_template_v2_default_template_id() -> str | None:
    """Return the single catalog-owned default identifier, if configured."""

    return _default_catalog().default_template_id


def get_template_rights_record(template_id: str) -> TemplateRightsRecord | None:
    return next(
        (
            record
            for record in _default_rights_manifest().records
            if record.template_id == template_id
        ),
        None,
    )


def is_template_v2_catalog_entry_rights_ready(
    entry: TemplateV2CatalogEntry,
) -> bool:
    """Require verified local evidence before a catalog row can be discovered."""

    try:
        record = get_template_rights_record(entry.id)
        if record is None:
            return False
        return evaluate_rights_readiness(
            entry,
            record,
            trusted_approvers=get_trusted_rights_approvers(),
            attestation_key=get_rights_attestation_key(),
        ).ready_for_publication_review
    except (OSError, ValueError):
        return False


def is_template_v2_catalog_source_bound(
    entry: TemplateV2CatalogEntry,
    assets: Any,
) -> bool:
    """Bind a published catalog identity to its exact reviewed PPTX receipt."""

    record = get_template_rights_record(entry.id)
    candidate_files = [
        item for item in entry.source.files if item.role == "candidate_pptx"
    ]
    if record is None or len(candidate_files) != 1 or not isinstance(assets, dict):
        return False
    native_source = assets.get("native_pptx")
    if not isinstance(native_source, dict):
        return False
    candidate = candidate_files[0]
    return (
        native_source.get("preservation_mode") == entry.preservation_mode
        and native_source.get("source_sha256") == candidate.sha256
        and native_source.get("source_size_bytes")
        == record.source.candidate_pptx_size_bytes
        and record.source.candidate_pptx_sha256 == candidate.sha256
        and record.source.candidate_pptx_path == candidate.path
    )


def is_template_v2_release_ready(template_id: str, assets: Any) -> bool:
    """Apply the shared discovery/generation release gate fail-closed."""

    from templates.v2.publication import evaluate_template_v2_publication

    if not evaluate_template_v2_publication(assets).publishable:
        return False
    entry = get_template_v2_catalog_entry(template_id)
    if entry is None:
        return True
    return (
        entry.publication_status == "published"
        and is_template_v2_catalog_entry_rights_ready(entry)
        and is_template_v2_catalog_source_bound(entry, assets)
    )


def is_template_v2_default_release(template_id: str, assets: Any) -> bool:
    """Derive default authority from the catalog and the full release gate."""

    return (
        get_template_v2_default_template_id() == template_id
        and is_template_v2_release_ready(template_id, assets)
    )


def list_template_v2_catalog_entries(
    *,
    include_unpublished: bool = False,
) -> tuple[TemplateV2CatalogEntry, ...]:
    entries = _default_catalog().entries
    if include_unpublished:
        return entries
    return tuple(
        entry
        for entry in entries
        if entry.publication_status == "published"
        and is_template_v2_catalog_entry_rights_ready(entry)
    )


__all__ = [
    "CATALOG_SCHEMA_VERSION",
    "DEFAULT_CATALOG_PATH",
    "DEFAULT_RIGHTS_PATH",
    "RIGHTS_SCHEMA_VERSION",
    "CatalogProvenance",
    "CatalogSource",
    "ImportCapabilities",
    "PreservationMode",
    "PublicationStatus",
    "TemplateKind",
    "TemplateV2CatalogEntry",
    "TemplateV2CatalogManifest",
    "TemplateRightsManifest",
    "TemplateRightsRecord",
    "RightsReadinessReport",
    "catalog_entry_sha256",
    "evaluate_rights_readiness",
    "get_rights_attestation_key",
    "get_trusted_rights_approvers",
    "get_template_rights_record",
    "get_template_v2_catalog_entry",
    "get_template_v2_default_template_id",
    "is_template_v2_catalog_entry_rights_ready",
    "is_template_v2_catalog_source_bound",
    "is_template_v2_default_release",
    "is_template_v2_release_ready",
    "list_template_v2_catalog_entries",
    "load_template_rights_manifest",
    "load_template_v2_catalog",
    "rights_evidence_set_sha256",
    "rights_approval_attestation_hmac",
]
