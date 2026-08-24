"""Cross-runtime canonical JSON, digest graph sealing, and strict QID checks."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from html.parser import HTMLParser
import json
import math
import re
import unicodedata
from typing import Any, Mapping

from pydantic import BaseModel
from utils.authored_dom_quality import validate_declared_authored_qids

from models.authored_vnext.semantic.contracts import (
    AssetManifestV1,
    AuthoredDeckEmissionManifestV1,
    AuthoredEmissionBundleV1,
    AuthoredSemanticSourceV1,
    BrowserObservationReceiptV1,
    CapabilityReceiptV1,
    ElementIdentityRegistryV1,
    ExportAttemptDigestBindingV1,
    ExportAttemptDigestBindingV2,
    FontManifestV1,
    HybridExportPlanV1,
    LegacyHtmlBundleV1,
    NativeAdmissionReceiptV1,
    QID_PATTERN,
    SemanticUnavailableReceiptV1,
)


_NUMBER_RE = re.compile(
    r"^(?P<whole>\d+)(?:\.(?P<fraction>\d+))?(?:e(?P<exponent>[+-]?\d+))?$",
    re.I,
)
_QID_RE = re.compile(QID_PATTERN)


class SemanticContractError(ValueError):
    """Stable fail-closed semantic boundary error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def canonical_number(value: int | float) -> str:
    if isinstance(value, bool):
        raise TypeError("booleans are not numbers")
    if isinstance(value, float) and not math.isfinite(value):
        raise SemanticContractError("authored_semantic_non_finite_number")
    if value == 0:
        return "0"
    negative = value < 0
    source = repr(abs(value)).lower()
    match = _NUMBER_RE.fullmatch(source)
    if match is None:
        source = format(Decimal(abs(value)), "f")
        match = _NUMBER_RE.fullmatch(source)
    if match is None:
        raise SemanticContractError("authored_semantic_number_invalid")
    whole = match.group("whole")
    fraction = match.group("fraction") or ""
    explicit_exponent = int(match.group("exponent") or "0")
    digits = whole + fraction
    first_non_zero = next((index for index, char in enumerate(digits) if char != "0"), None)
    if first_non_zero is None:
        return "0"
    digits = digits[first_non_zero:].rstrip("0")
    exponent = explicit_exponent + len(whole) - first_non_zero - 1
    coefficient = digits if len(digits) == 1 else f"{digits[0]}.{digits[1:]}"
    body = coefficient if exponent == 0 else f"{coefficient}e{exponent}"
    return f"-{body}" if negative else body


def canonical_json(value: Any) -> str:
    """Emit the repository's versioned Python/TypeScript canonical JSON form."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True)
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        normalized = unicodedata.normalize("NFC", value)
        return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, (int, float)):
        return canonical_number(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, Mapping):
        normalized = [
            (unicodedata.normalize("NFC", str(key)), item) for key, item in value.items()
        ]
        normalized.sort(key=lambda entry: entry[0].encode("utf-16-be", "surrogatepass"))
        return "{" + ",".join(
            json.dumps(key, ensure_ascii=False, separators=(",", ":"))
            + ":"
            + canonical_json(item)
            for key, item in normalized
        ) + "}"
    raise SemanticContractError("authored_semantic_canonical_type_unsupported")


def canonical_digest(value: Any) -> str:
    return "sha256:" + sha256(canonical_json(value).encode("utf-8")).hexdigest()


def bytes_digest(value: bytes) -> str:
    return "sha256:" + sha256(value).hexdigest()


def seal_component(component: BaseModel, digest_field: str = "digest") -> BaseModel:
    payload = component.model_dump(mode="json", by_alias=True)
    payload.pop(_camel(digest_field), None)
    return component.model_copy(update={digest_field: canonical_digest(payload)})


def seal_identity_registry(registry: ElementIdentityRegistryV1) -> ElementIdentityRegistryV1:
    return seal_component(registry)  # type: ignore[return-value]


def seal_semantic_source(source: AuthoredSemanticSourceV1) -> AuthoredSemanticSourceV1:
    registry = seal_identity_registry(source.identity_registry)
    candidate = source.model_copy(update={"identity_registry": registry, "digest": None})
    return seal_component(candidate)  # type: ignore[return-value]


def seal_asset_manifest(manifest: AssetManifestV1) -> AssetManifestV1:
    return seal_component(manifest)  # type: ignore[return-value]


def seal_font_manifest(manifest: FontManifestV1) -> FontManifestV1:
    return seal_component(manifest)  # type: ignore[return-value]


def seal_unavailable_receipt(
    receipt: SemanticUnavailableReceiptV1,
) -> SemanticUnavailableReceiptV1:
    return seal_component(receipt)  # type: ignore[return-value]


def seal_bundle(
    bundle: AuthoredEmissionBundleV1 | LegacyHtmlBundleV1,
) -> AuthoredEmissionBundleV1 | LegacyHtmlBundleV1:
    if isinstance(bundle, AuthoredEmissionBundleV1):
        component = bundle.semantic_component
        if isinstance(component, AuthoredSemanticSourceV1):
            component = seal_semantic_source(component)
            registry_digest = component.identity_registry.digest
        else:
            component = seal_unavailable_receipt(component)
            registry_digest = bundle.element_identity_registry_digest
        candidate = bundle.model_copy(
            update={"semantic_component": component, "bundle_digest": None}
        )
    else:
        component = bundle.compatibility_component
        component = seal_component(component)
        registry_digest = bundle.element_identity_registry_digest
        candidate = bundle.model_copy(
            update={"compatibility_component": component, "bundle_digest": None}
        )
    if registry_digest != candidate.element_identity_registry_digest:
        raise SemanticContractError("authored_semantic_registry_digest_mismatch")
    component_digest = getattr(component, "digest", None)
    if component_digest is None:
        raise SemanticContractError("authored_semantic_component_digest_missing")
    payload = {
        "schemaVersion": candidate.schema_version,
        "bundleKind": candidate.bundle_kind,
        "presentationId": str(candidate.presentation_id),
        "slideId": str(candidate.slide_id),
        "slideRevision": candidate.slide_revision,
        "operationId": str(candidate.operation_id),
        "originGenerationAttemptId": (
            str(candidate.origin_generation_attempt_id)
            if candidate.origin_generation_attempt_id
            else None
        ),
        "authoredHtmlDigest": candidate.authored_html.digest,
        "semanticComponentDigest": component_digest,
        "assetManifestDigest": candidate.asset_manifest.digest,
        "fontManifestDigest": candidate.font_manifest.digest,
        "masterContractDigest": candidate.master_contract_digest,
        "compositionDigest": candidate.composition_digest,
        "elementIdentityRegistryDigest": candidate.element_identity_registry_digest,
        "qualityPolicyVersion": candidate.quality_policy_version,
    }
    return candidate.model_copy(update={"bundle_digest": canonical_digest(payload)})


def seal_deck_manifest(
    manifest: AuthoredDeckEmissionManifestV1,
) -> AuthoredDeckEmissionManifestV1:
    candidate = manifest.model_copy(update={"canonical_digest": None})
    payload = candidate.model_dump(mode="json", by_alias=True)
    payload.pop("canonicalDigest", None)
    return candidate.model_copy(update={"canonical_digest": canonical_digest(payload)})


def seal_derived_receipt(
    receipt: BrowserObservationReceiptV1
    | NativeAdmissionReceiptV1
    | CapabilityReceiptV1,
) -> BrowserObservationReceiptV1 | NativeAdmissionReceiptV1 | CapabilityReceiptV1:
    return seal_component(receipt, "receipt_digest")  # type: ignore[return-value]


def seal_hybrid_plan(plan: HybridExportPlanV1) -> HybridExportPlanV1:
    return seal_component(plan, "plan_digest")  # type: ignore[return-value]


def seal_export_binding(
    binding: ExportAttemptDigestBindingV1 | ExportAttemptDigestBindingV2,
) -> ExportAttemptDigestBindingV1 | ExportAttemptDigestBindingV2:
    return seal_component(binding, "binding_digest")  # type: ignore[return-value]


class _QidParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.qids: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._collect(attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._collect(attrs)

    def _collect(self, attrs: list[tuple[str, str | None]]) -> None:
        for key, value in attrs:
            if key.casefold() == "data-qid":
                if value is None or not _QID_RE.fullmatch(value):
                    raise SemanticContractError("authored_semantic_qid_invalid")
                self.qids.append(value)


def validate_declared_qids(html: str, source: AuthoredSemanticSourceV1) -> tuple[str, ...]:
    """Reject invalid/duplicate/mismatched declared identity without rewriting HTML."""

    parser = _QidParser()
    try:
        parser.feed(html)
        parser.close()
    except SemanticContractError:
        raise
    except Exception as error:
        raise SemanticContractError("authored_semantic_html_invalid") from error
    try:
        strict_qids = validate_declared_authored_qids(html)
    except (TypeError, ValueError) as error:
        code = str(error)
        if not code.startswith("authored_semantic_"):
            code = "authored_semantic_qid_invalid"
        raise SemanticContractError(code) from error
    if len(parser.qids) != len(set(parser.qids)):
        raise SemanticContractError("authored_semantic_qid_duplicate")
    declared = [element.element_id for element in source.elements]
    if set(parser.qids) != set(declared) or len(parser.qids) != len(declared):
        raise SemanticContractError("authored_semantic_qid_mismatch")
    if tuple(parser.qids) != strict_qids:
        raise SemanticContractError("authored_semantic_qid_mismatch")
    return strict_qids


def require_digest(actual: str | None, expected: str | None, code: str) -> None:
    if actual is None or expected is None or actual != expected:
        raise SemanticContractError(code)


def _camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(item.capitalize() for item in rest)


@dataclass(frozen=True)
class SealedCandidate:
    html_bytes: bytes
    bundle: AuthoredEmissionBundleV1 | LegacyHtmlBundleV1


def validate_and_seal_candidate(
    html_bytes: bytes,
    bundle: AuthoredEmissionBundleV1 | LegacyHtmlBundleV1,
) -> SealedCandidate:
    if bytes_digest(html_bytes) != bundle.authored_html.digest:
        raise SemanticContractError("authored_semantic_html_digest_mismatch")
    if bundle.authored_html.byte_length != len(html_bytes):
        raise SemanticContractError("authored_semantic_html_length_mismatch")
    if isinstance(bundle, AuthoredEmissionBundleV1) and isinstance(
        bundle.semantic_component, AuthoredSemanticSourceV1
    ):
        try:
            html = html_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise SemanticContractError("authored_semantic_html_utf8_invalid") from error
        validate_declared_qids(html, bundle.semantic_component)
    sealed = seal_bundle(bundle)
    if bundle.bundle_digest is not None and sealed.bundle_digest != bundle.bundle_digest:
        raise SemanticContractError("authored_semantic_bundle_digest_mismatch")
    return SealedCandidate(html_bytes=html_bytes, bundle=sealed)


__all__ = [
    "SemanticContractError",
    "bytes_digest",
    "canonical_digest",
    "canonical_json",
    "seal_asset_manifest",
    "seal_bundle",
    "seal_deck_manifest",
    "seal_export_binding",
    "seal_font_manifest",
    "seal_hybrid_plan",
    "seal_semantic_source",
    "validate_and_seal_candidate",
    "validate_declared_qids",
]
