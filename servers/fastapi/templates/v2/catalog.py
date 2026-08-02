"""Validated, network-free Template V2 design catalog metadata.

The catalog records where an externally inspired design was inspected without
making the external repository a runtime dependency.  It intentionally stores
metadata only: no fonts, logos, images, SVGs, or PPTX packages are vendored by
this module.  Publication of persisted Template V2 rows is a separate concern
handled by :mod:`templates.v2.publication`.
"""

from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CATALOG_SCHEMA_VERSION = "presenton.template-v2-design-catalog/v1"
DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parent
    / "manifests"
    / "ppt_master_catalog.v1.json"
)

TemplateKind = Literal["brand", "layout", "deck"]
PreservationMode = Literal["native_clone", "reconstructed"]
PublicationStatus = Literal["published", "blocked"]
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
    entries: tuple[TemplateV2CatalogEntry, ...]

    @model_validator(mode="after")
    def require_unique_ids(self) -> "TemplateV2CatalogManifest":
        identifiers = [entry.id for entry in self.entries]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("catalog template ids must be unique")
        return self


def load_template_v2_catalog(
    manifest_path: Path | None = None,
) -> TemplateV2CatalogManifest:
    """Load one local manifest; URLs and remote fallbacks are not accepted."""

    path = DEFAULT_CATALOG_PATH if manifest_path is None else Path(manifest_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return TemplateV2CatalogManifest.model_validate(payload)


@lru_cache(maxsize=1)
def _default_catalog() -> TemplateV2CatalogManifest:
    return load_template_v2_catalog()


def get_template_v2_catalog_entry(
    template_id: str,
) -> TemplateV2CatalogEntry | None:
    return next(
        (entry for entry in _default_catalog().entries if entry.id == template_id),
        None,
    )


def list_template_v2_catalog_entries(
    *,
    include_unpublished: bool = False,
) -> tuple[TemplateV2CatalogEntry, ...]:
    entries = _default_catalog().entries
    if include_unpublished:
        return entries
    return tuple(entry for entry in entries if entry.publication_status == "published")


__all__ = [
    "CATALOG_SCHEMA_VERSION",
    "DEFAULT_CATALOG_PATH",
    "CatalogProvenance",
    "CatalogSource",
    "ImportCapabilities",
    "PreservationMode",
    "PublicationStatus",
    "TemplateKind",
    "TemplateV2CatalogEntry",
    "TemplateV2CatalogManifest",
    "get_template_v2_catalog_entry",
    "list_template_v2_catalog_entries",
    "load_template_v2_catalog",
]
