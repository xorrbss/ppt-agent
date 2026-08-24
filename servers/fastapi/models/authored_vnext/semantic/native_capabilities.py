"""Versioned, fail-closed native capability admission/readiness projection."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from models.authored_vnext.master.base import Digest, StrictCamelModel


CapabilityAdmissionState = Literal[
    "nativeEditable", "rasterIsland", "publicationBlocked"
]
CapabilityOwnerDomain = Literal["native", "raster", "blocked"]
CapabilityReadinessState = Literal[
    "notEvaluated", "notVerified", "ready", "fidelityFallback", "blocked"
]


class NativeCapabilityAdmissionV1(StrictCamelModel):
    slide_id: str = Field(pattern=r"^slide:[a-z0-9][a-z0-9._-]*$")
    owner_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")
    capability_id: str = Field(
        pattern=r"^(?:editable|css)\.[a-z0-9][a-z0-9._-]*\.v[0-9]+$"
    )
    version: str = Field(pattern=r"^v[0-9]+$")
    admission: CapabilityAdmissionState
    owner_domain: CapabilityOwnerDomain
    reason_code: str | None = Field(
        default=None, pattern=r"^[a-z0-9][a-z0-9._-]*$"
    )

    @model_validator(mode="after")
    def _domain_and_reason_match_admission(self) -> "NativeCapabilityAdmissionV1":
        expected_domain = {
            "nativeEditable": "native",
            "rasterIsland": "raster",
            "publicationBlocked": "blocked",
        }[self.admission]
        if self.owner_domain != expected_domain:
            raise ValueError("capability ownerDomain does not match admission")
        if self.admission == "nativeEditable" and self.reason_code is not None:
            raise ValueError("native capability admission cannot carry a fallback reason")
        if self.admission != "nativeEditable" and self.reason_code is None:
            raise ValueError("non-native capability admission requires reasonCode")
        if not self.capability_id.endswith(f".{self.version}"):
            raise ValueError("capability version does not match capabilityId")
        return self


class NativeCapabilityReadinessV1(StrictCamelModel):
    capability_id: str = Field(
        pattern=r"^(?:editable|css)\.[a-z0-9][a-z0-9._-]*\.v[0-9]+$"
    )
    version: str = Field(pattern=r"^v[0-9]+$")
    status: CapabilityReadinessState
    ready: bool
    reason_code: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    native_owner_count: int = Field(ge=0)
    raster_owner_count: int = Field(ge=0)
    blocked_owner_count: int = Field(ge=0)
    receipt_digest: Digest | None = None

    @model_validator(mode="after")
    def _truthful_readiness(self) -> "NativeCapabilityReadinessV1":
        if not self.capability_id.endswith(f".{self.version}"):
            raise ValueError("capability readiness version does not match capabilityId")
        total = (
            self.native_owner_count
            + self.raster_owner_count
            + self.blocked_owner_count
        )
        expected_status: CapabilityReadinessState
        if self.blocked_owner_count:
            expected_status = "blocked"
        elif self.raster_owner_count:
            expected_status = "fidelityFallback"
        elif self.native_owner_count and self.receipt_digest is not None:
            expected_status = "ready"
        elif self.native_owner_count:
            expected_status = "notVerified"
        else:
            expected_status = "notEvaluated"
        if self.status != expected_status or self.ready != (expected_status == "ready"):
            raise ValueError("capability readiness is not derived from owner evidence")
        if self.ready != (self.receipt_digest is not None and total > 0):
            raise ValueError("ready capability requires a receipt and admitted owners")
        return self


class NativeCapabilityProjectionV1(StrictCamelModel):
    schema_version: Literal["authored-native-capability-projection/v1"] = (
        "authored-native-capability-projection/v1"
    )
    capability_set_version: Literal["v1"] = "v1"
    admission_version: Literal["v1"] = "v1"
    readiness_version: Literal["v1"] = "v1"
    scene_digest: Digest | None = None
    admissions: list[NativeCapabilityAdmissionV1] = Field(max_length=5000)
    capabilities: list[NativeCapabilityReadinessV1] = Field(
        min_length=1, max_length=128
    )
    duplicate_owner_count: Literal[0] = 0
    missing_owner_count: Literal[0] = 0
    full_native_parity_ready: Literal[False] = False
    promotion_ready: Literal[False] = False
    deployment_authorized: Literal[False] = False

    @model_validator(mode="after")
    def _owner_and_capability_sets_are_closed(self) -> "NativeCapabilityProjectionV1":
        owner_keys = [(item.slide_id, item.owner_id) for item in self.admissions]
        if len(owner_keys) != len(set(owner_keys)):
            raise ValueError("capability projection contains duplicate owners")
        capability_ids = [item.capability_id for item in self.capabilities]
        if capability_ids != sorted(set(capability_ids)):
            raise ValueError("capability readiness entries must be unique and ordered")
        declared = set(capability_ids)
        if any(item.capability_id not in declared for item in self.admissions):
            raise ValueError("capability admission is absent from readiness projection")
        return self


__all__ = [
    "NativeCapabilityAdmissionV1",
    "NativeCapabilityProjectionV1",
    "NativeCapabilityReadinessV1",
]
