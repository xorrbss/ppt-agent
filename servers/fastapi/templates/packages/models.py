"""Manifest contracts for immutable, verified TemplatePackage imports."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PackageModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


class PackageFile(PackageModel):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(alias="sizeBytes", ge=0)
    media_type: str = Field(alias="mediaType")
    role: str
    license_ref: str = Field(alias="licenseRef")


class PackageAssetInventory(PackageModel):
    schema_version: Literal["presenton.template-package-assets/v1"] = Field(
        alias="schemaVersion"
    )
    template_id: str = Field(alias="templateId")
    files: tuple[PackageFile, ...]


class Entrypoint(PackageModel):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(alias="sizeBytes", ge=1)
    media_type: Literal["application/json"] = Field(alias="mediaType")


class EngineContract(PackageModel):
    id: Literal["presenton.template-v2-json"]
    contract_version: Literal["1"] = Field(alias="contractVersion")
    contract_path: str = Field(alias="contractPath")
    contract_sha256: str = Field(alias="contractSha256", pattern=r"^[0-9a-f]{64}$")
    required_capabilities: tuple[str, ...] = Field(alias="requiredCapabilities")


class PresentationSize(PackageModel):
    width: int = Field(gt=0, le=7680)
    height: int = Field(gt=0, le=4320)


class AssetInventoryReference(PackageModel):
    inventory_path: str = Field(alias="inventoryPath")
    inventory_sha256: str = Field(alias="inventorySha256", pattern=r"^[0-9a-f]{64}$")
    count: int = Field(ge=0, le=10_000)
    total_size_bytes: int = Field(alias="totalSizeBytes", ge=0, le=512 * 1024 * 1024)


class FontContract(PackageModel):
    faces: tuple[str, ...]
    aliases: dict[str, str]
    unresolved_aliases: tuple[str, ...] = Field(alias="unresolvedAliases")


class LicenseContract(PackageModel):
    status: Literal["approved", "review-required", "rejected"]
    expression: str
    rights_evidence_id: str | None = Field(alias="rightsEvidenceId")


class Provenance(PackageModel):
    repository: str
    commit_sha: str = Field(alias="commitSha", pattern=r"^[0-9a-f]{40}$")
    source_path: str = Field(alias="sourcePath")
    source_file_count: int = Field(alias="sourceFileCount", ge=1)
    source_inventory_sha256: str = Field(
        alias="sourceInventorySha256", pattern=r"^[0-9a-f]{64}$"
    )
    evidence_path: str = Field(alias="evidencePath")
    evidence_sha256: str = Field(alias="evidenceSha256", pattern=r"^[0-9a-f]{64}$")


class Thumbnail(PackageModel):
    asset_path: str = Field(alias="assetPath")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class RendererSmoke(PackageModel):
    receipt_path: str = Field(alias="receiptPath")
    receipt_sha256: str = Field(
        alias="receiptSha256", pattern=r"^[0-9a-f]{64}$"
    )


class BrowserGolden(PackageModel):
    receipt_path: str = Field(alias="receiptPath")
    receipt_sha256: str = Field(
        alias="receiptSha256", pattern=r"^[0-9a-f]{64}$"
    )


class Publication(PackageModel):
    status: Literal["ready", "blocked", "retired"]
    default: bool
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def _ready_has_no_blockers(self) -> "Publication":
        if self.status == "ready" and self.blockers:
            raise ValueError("ready_package_cannot_have_blockers")
        return self


class TemplatePackageManifest(PackageModel):
    schema_version: Literal["presenton.template-package/v1"] = Field(
        alias="schemaVersion"
    )
    package_version: str = Field(
        alias="packageVersion", pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
    )
    template_id: str = Field(alias="templateId", pattern=r"^[a-z0-9][a-z0-9-]*$")
    source_root: str = Field(alias="sourceRoot")
    entrypoint: Entrypoint
    engine: EngineContract
    presentation: PresentationSize
    assets: AssetInventoryReference
    fonts: FontContract
    license: LicenseContract
    provenance: Provenance
    thumbnail: Thumbnail
    renderer_smoke: RendererSmoke = Field(alias="rendererSmoke")
    browser_golden: BrowserGolden = Field(alias="browserGolden")
    publication: Publication


class PackageValidationReceipt(PackageModel):
    schema_version: Literal["presenton.template-package-receipt/v1"] = Field(
        alias="schemaVersion"
    )
    template_id: str = Field(alias="templateId")
    package_version: str = Field(alias="packageVersion")
    manifest_sha256: str = Field(alias="manifestSha256")
    engine_contract_sha256: str = Field(alias="engineContractSha256")
    entrypoint_sha256: str = Field(alias="entrypointSha256")
    inventory_sha256: str = Field(alias="inventorySha256")
    renderer_smoke_sha256: str = Field(alias="rendererSmokeSha256")
    asset_count: int = Field(alias="assetCount")
    total_size_bytes: int = Field(alias="totalSizeBytes")
    publication_status: Literal["ready", "blocked", "retired"] = Field(
        alias="publicationStatus"
    )
    blockers: tuple[str, ...]


__all__ = [
    "PackageAssetInventory",
    "PackageFile",
    "PackageValidationReceipt",
    "TemplatePackageManifest",
]
