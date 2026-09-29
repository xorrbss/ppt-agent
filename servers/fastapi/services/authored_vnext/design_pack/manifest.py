"""Sealed package manifest (v1)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from .base import Digest, PackModel, canonical_digest


MANIFEST_V1 = "source-native-design-pack/v1"
PACK_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{1,119}$"
VERSION_PATTERN = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"


class PackageFile(PackModel):
    path: str = Field(min_length=1, max_length=500)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str = Field(min_length=1, max_length=200)
    role: Literal["structure", "source", "representative-render"]


class DesignPackManifest(PackModel):
    schema_version: Literal["source-native-design-pack/v1"] = MANIFEST_V1
    pack_id: str = Field(pattern=PACK_ID_PATTERN)
    package_version: str = Field(pattern=VERSION_PATTERN)
    source_kind: Literal["pptx", "authored"]
    source_filename: str = Field(min_length=1, max_length=255)
    source_digest: Digest = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    structure_path: Literal["structure.json"] = "structure.json"
    structure_digest: Digest = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    inventory: tuple[PackageFile, ...] = Field(min_length=2, max_length=5000)
    inventory_digest: Digest = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    package_digest: Digest = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _sealed(self) -> "DesignPackManifest":
        paths = [item.path for item in self.inventory]
        if len(paths) != len(set(paths)) or "structure.json" not in paths:
            raise ValueError("design_pack_inventory_invalid")
        inventory_payload = [
            item.model_dump(mode="json", by_alias=True) for item in self.inventory
        ]
        if self.inventory_digest != canonical_digest(inventory_payload):
            raise ValueError("design_pack_inventory_digest_mismatch")
        payload = self.model_dump(mode="json", by_alias=True, exclude={"package_digest"})
        if self.package_digest != canonical_digest(payload):
            raise ValueError("design_pack_package_digest_mismatch")
        return self


def seal_manifest(**values: Any) -> DesignPackManifest:
    inventory = tuple(values["inventory"])
    inventory_payload = [
        item.model_dump(mode="json", by_alias=True)
        if isinstance(item, PackageFile)
        else item
        for item in inventory
    ]
    values = {
        **values,
        "inventory": inventory,
        "inventory_digest": canonical_digest(inventory_payload),
    }
    provisional = DesignPackManifest.model_construct(
        **values, package_digest="sha256:" + "0" * 64
    )
    payload = provisional.model_dump(
        mode="json", by_alias=True, exclude={"package_digest"}
    )
    return DesignPackManifest(**values, package_digest=canonical_digest(payload))


__all__ = [
    "DesignPackManifest",
    "MANIFEST_V1",
    "PackageFile",
    "seal_manifest",
]
