"""Closed contracts for fail-closed CJK native typography admission."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from models.authored_vnext.master.base import Digest, StrictCamelModel


class NativeFontFaceReceiptV1(StrictCamelModel):
    schema_version: Literal["native-font-face-receipt/v1"] = (
        "native-font-face-receipt/v1"
    )
    family: str = Field(min_length=1, max_length=128)
    face: str = Field(min_length=1, max_length=128)
    collection_index: int = Field(ge=0, le=255)
    font_byte_digest: Digest
    size_bytes: int = Field(gt=0, le=64 * 1024 * 1024)
    face_class: Literal["sans", "serif", "mono"]
    license_policy: Literal[
        "ofl-1.1",
        "microsoft-windows-font-eula",
        "unreviewed",
        "missing",
    ]
    license_evidence_digest: Digest | None = None
    fs_type: int = Field(ge=0, le=65535)
    embedding_permitted: bool
    editable_embedding_permitted: bool
    subsetting_permitted: bool
    embedding_eligible: bool

    @model_validator(mode="after")
    def fail_closed_eligibility(self) -> "NativeFontFaceReceiptV1":
        reviewed = self.license_policy in {
            "ofl-1.1",
            "microsoft-windows-font-eula",
        }
        if self.embedding_eligible != (
            reviewed and self.editable_embedding_permitted
        ):
            raise ValueError("authored_native_font_embedding_eligibility_invalid")
        if reviewed != bool(self.license_evidence_digest):
            raise ValueError("authored_native_font_license_evidence_invalid")
        return self


class NativeTypographyRunReadbackV1(StrictCamelModel):
    run_index: int = Field(ge=0, le=511)
    text_digest: Digest
    face: str = Field(min_length=1, max_length=128)
    east_asian_typeface: str = Field(min_length=1, max_length=128)


class NativeTypographyReadbackV1(StrictCamelModel):
    schema_version: Literal["native-typography-readback/v1"] = (
        "native-typography-readback/v1"
    )
    element_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")
    runs: list[NativeTypographyRunReadbackV1] = Field(max_length=512)
    line_rect_digests: list[Digest] = Field(min_length=1, max_length=512)
    overflow: bool
    readback_digest: Digest | None = None


class NativeTypographyRunReceiptV1(StrictCamelModel):
    run_index: int = Field(ge=0, le=511)
    text_digest: Digest
    script_profile: Literal[
        "mixedKoreanLatinNumber",
        "cjkSans",
        "cjkSerif",
        "cjkMono",
        "latinNumber",
        "common",
    ]
    requested_family: str = Field(min_length=1, max_length=128)
    requested_face: str = Field(min_length=1, max_length=128)
    resolved_family: str | None = Field(default=None, max_length=128)
    resolved_face: str | None = Field(default=None, max_length=128)
    east_asian_typeface: str | None = Field(default=None, max_length=128)
    font_byte_digest: Digest | None = None
    state: Literal["admitted", "rasterFallback"]
    fallback_reason: Literal[
        "fontUnavailable",
        "fontNonEmbeddable",
        "glyphCoverageUnavailable",
        "readbackUnavailable",
        "readbackTypefaceMismatch",
        "lineReceiptUnavailable",
        "nativeOverflow",
    ] | None = None

    @model_validator(mode="after")
    def truthful_state(self) -> "NativeTypographyRunReceiptV1":
        native_fields = (
            self.resolved_family,
            self.resolved_face,
            self.east_asian_typeface,
            self.font_byte_digest,
        )
        if self.state == "admitted":
            if self.fallback_reason is not None or any(
                value is None for value in native_fields
            ):
                raise ValueError("authored_native_typography_admission_invalid")
        elif self.fallback_reason is None:
            raise ValueError("authored_native_typography_fallback_reason_required")
        return self


class NativeTypographyAdmissionReceiptV1(StrictCamelModel):
    schema_version: Literal["native-typography-admission-receipt/v1"] = (
        "native-typography-admission-receipt/v1"
    )
    element_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")
    font_registry_digest: Digest
    readback_digest: Digest | None = None
    line_count: int = Field(ge=0, le=512)
    line_rect_digests: list[Digest] = Field(default_factory=list, max_length=512)
    overflow: bool | None = None
    state: Literal["admitted", "rasterFallback"]
    runs: list[NativeTypographyRunReceiptV1] = Field(min_length=1, max_length=512)
    promotion_ready: Literal[False] = False
    full_native_parity_ready: Literal[False] = False
    independent_vnext_generation_ready: Literal[False] = False
    receipt_digest: Digest | None = None

    @model_validator(mode="after")
    def truthful_state(self) -> "NativeTypographyAdmissionReceiptV1":
        admitted = all(run.state == "admitted" for run in self.runs)
        if (self.state == "admitted") != admitted:
            raise ValueError("authored_native_typography_receipt_state_invalid")
        if self.state == "admitted" and (
            not self.readback_digest
            or not self.line_rect_digests
            or self.line_count != len(self.line_rect_digests)
            or self.overflow is not False
        ):
            raise ValueError("authored_native_typography_readback_receipt_invalid")
        return self


__all__ = [name for name in globals() if name.endswith("V1")]
