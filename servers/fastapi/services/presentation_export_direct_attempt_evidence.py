"""Pure evidence validation helpers for direct presentation export attempts."""

from __future__ import annotations

from typing import Any
import uuid

from models.sql.presentation import PresentationModel
from models.sql.presentation_export_attempt import (
    ExportAtomicBinding,
    ExportPowerPointQaEvidence,
)
from services.authored_quality_receipt_v2 import (
    release_quality_policy,
    validate_current_quality_receipt_payload,
)
from services.presentation_export_attempt_service import sha256_canonical_json
from services.presentation_export_capabilities import PresentationExportContractError


def published_quality_receipt(
    presentation: PresentationModel,
) -> dict[str, Any] | None:
    """Return the published quality binding, rejecting invalid release receipts."""

    theme = presentation.theme
    if not isinstance(theme, dict):
        return None
    receipt = theme.get("authoredQualityGate")
    if not isinstance(receipt, dict):
        return None
    digest = receipt.get("receiptDigest")
    attempt_id = receipt.get("attemptId")
    revision = receipt.get("revision")
    profile = receipt.get(
        "requestedReleaseProfile",
        receipt.get(
            "requestedQualityProfile",
            receipt.get("releaseProfile", "development"),
        ),
    )
    invalid = (
        not isinstance(digest, str)
        or not digest.startswith("sha256:")
        or len(digest) != 71
        or not all(character in "0123456789abcdef" for character in digest[7:].lower())
        or not isinstance(attempt_id, str)
        or not attempt_id.strip()
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
        or not isinstance(profile, str)
        or not profile.strip()
        or not isinstance(receipt.get("gatePassed"), bool)
    )
    if invalid:
        profile_value = profile if isinstance(profile, str) else "development"
        if release_quality_policy(profile_value).require_native_coverage:
            raise PresentationExportContractError(
                "presentation_export_quality_receipt_invalid"
            )
        # Legacy development receipts predate the versioned receipt digest.
        return None
    requested_policy = release_quality_policy(profile)
    try:
        current = validate_current_quality_receipt_payload(receipt)
    except (TypeError, ValueError) as error:
        if requested_policy.require_native_coverage:
            raise PresentationExportContractError(
                "presentation_export_quality_receipt_invalid"
            ) from error
        current = None
    if not receipt["gatePassed"] and requested_policy.require_native_coverage:
        raise PresentationExportContractError(
            "presentation_export_release_quality_receipt_blocked"
        )
    binding = {
        "receipt_digest": digest.lower(),
        "generation_attempt_id": attempt_id.strip(),
        "generation_revision": revision,
        "release_profile": requested_policy.profile_name,
        "gate_passed": receipt["gatePassed"],
    }
    if current is not None:
        binding.update(
            {
                "requested_release_profile": current.release_profile,
                "pre_export_evaluation_profile": current.pre_export_evaluation_profile,
                "requested_release_policy_digest": current.requested_release_policy_digest,
                "pre_export_evaluation_policy_digest": current.pre_export_evaluation_policy_digest,
                "scene_envelope_digest": current.scene_envelope_digest,
                "scene_source_digest": current.scene_source_digest,
                "scene_qid_digest": current.scene_qid_digest,
                "source_readback_status": current.source_readback_status,
                "visual_review_status": current.visual_review_status,
                "performance_status": current.performance_status,
                "require_source_readback": requested_policy.require_source_readback,
                "require_visual_review": requested_policy.require_visual_review,
                "require_performance": requested_policy.require_performance,
                "require_native_coverage": requested_policy.require_native_coverage,
                "minimum_native_coverage_ratio": requested_policy.minimum_native_coverage_ratio,
            }
        )
    return binding


def visual_parity_status_from_powerpoint_qa(
    qa: ExportPowerPointQaEvidence,
) -> str:
    """Return only the visual claim justified by desktop QA alone."""

    return "failed" if qa.status == "failed" else "not-verified"


def expected_qid_owners_from_quality(
    quality: dict[str, Any],
) -> frozenset[tuple[int, str, str]]:
    owners: set[tuple[int, str, str]] = set()
    slides = quality.get("slides")
    if not isinstance(slides, list):
        return frozenset()
    for slide in slides:
        if not isinstance(slide, dict):
            continue
        slide_number = slide.get("slideNumber")
        if isinstance(slide_number, bool) or not isinstance(slide_number, int):
            continue
        coverage = slide.get("qidCoverage")
        if not isinstance(coverage, list):
            continue
        for item in coverage:
            if (
                isinstance(item, dict)
                and item.get("ooxml") == "present"
                and isinstance(item.get("elementId"), str)
                and item.get("logical") in {"native", "raster"}
            ):
                owners.add((slide_number, item["elementId"], item["logical"]))
    return frozenset(owners)


def qid_owner_census(
    quality: dict[str, Any],
    *,
    required: bool,
) -> tuple[tuple[int, str, str], ...]:
    """Return every QID's single declared native/raster owner, fail closed."""

    census: list[tuple[int, str, str]] = []
    seen: set[tuple[int, str]] = set()
    slides = quality.get("slides")
    if not isinstance(slides, list):
        if required:
            raise PresentationExportContractError(
                "presentation_export_qid_owner_census_missing"
            )
        return ()
    for slide in slides:
        if not isinstance(slide, dict):
            if required:
                raise PresentationExportContractError(
                    "presentation_export_qid_owner_census_invalid"
                )
            continue
        slide_number = slide.get("slideNumber")
        coverage = slide.get("qidCoverage")
        if (
            isinstance(slide_number, bool)
            or not isinstance(slide_number, int)
            or not isinstance(coverage, list)
        ):
            if required:
                raise PresentationExportContractError(
                    "presentation_export_qid_owner_census_invalid"
                )
            continue
        for item in coverage:
            if not isinstance(item, dict):
                raise PresentationExportContractError(
                    "presentation_export_qid_owner_census_invalid"
                )
            element_id = item.get("elementId")
            owner = item.get("logical")
            if (
                not isinstance(element_id, str)
                or not element_id.strip()
                or owner not in {"native", "raster"}
                or (required and item.get("ooxml") != "present")
            ):
                raise PresentationExportContractError(
                    "presentation_export_qid_owner_census_invalid"
                )
            identity = (slide_number, element_id)
            if identity in seen:
                raise PresentationExportContractError(
                    "presentation_export_qid_owner_census_duplicate"
                )
            seen.add(identity)
            census.append((slide_number, element_id, owner))
    if required and not census:
        raise PresentationExportContractError(
            "presentation_export_qid_owner_census_missing"
        )
    return tuple(sorted(census))


def atomic_run_digest(payload: dict[str, Any]) -> str:
    return "sha256:" + sha256_canonical_json(payload)


def require_scene_envelope_binding(
    presentation: PresentationModel,
    generation_receipt: dict[str, Any] | None,
    *,
    required: bool,
) -> None:
    if generation_receipt is None:
        if required:
            raise PresentationExportContractError(
                "presentation_export_scene_envelope_binding_missing"
            )
        return
    theme = presentation.theme if isinstance(presentation.theme, dict) else {}
    quality_receipt = theme.get("authoredQualityGate")
    scene = quality_receipt.get("sceneEnvelope") if isinstance(quality_receipt, dict) else None
    expected = (
        generation_receipt.get("scene_envelope_digest"),
        generation_receipt.get("scene_source_digest"),
        generation_receipt.get("scene_qid_digest"),
    )
    observed = (
        scene.get("sceneDigest") if isinstance(scene, dict) else None,
        scene.get("sourceDigest") if isinstance(scene, dict) else None,
        scene.get("qidDigest") if isinstance(scene, dict) else None,
    )
    if required and (
        not isinstance(scene, dict)
        or scene.get("schemaVersion") != "authored-deck-scene-envelope/v1"
        or any(value is None for value in expected)
        or observed != expected
    ):
        raise PresentationExportContractError(
            "presentation_export_scene_envelope_binding_mismatch"
        )


def expected_geometry_from_quality(
    quality: dict[str, Any],
) -> tuple[
    dict[tuple[int, str], tuple[float, float, float, float]],
    str | None,
]:
    geometry: dict[tuple[int, str], tuple[float, float, float, float]] = {}
    digests: list[dict[str, object]] = []
    slides = quality.get("slides")
    if not isinstance(slides, list):
        return geometry, None
    for slide in slides:
        if not isinstance(slide, dict) or not isinstance(slide.get("slideNumber"), int):
            continue
        contract = slide.get("geometryContract")
        if (
            not isinstance(contract, dict)
            or contract.get("schemaVersion") != "presenton.hybrid-geometry/v1"
            or contract.get("ooxmlMatch") is not True
            or not isinstance(contract.get("chromiumDigest"), str)
            or not isinstance(contract.get("ooxmlDigest"), str)
            or not isinstance(contract.get("elements"), list)
        ):
            continue
        slide_number = slide["slideNumber"]
        digests.append({
            "slideNumber": slide_number,
            "chromiumDigest": contract["chromiumDigest"],
            "ooxmlDigest": contract["ooxmlDigest"],
        })
        for item in contract["elements"]:
            if not isinstance(item, dict) or not isinstance(item.get("elementId"), str):
                continue
            values = tuple(item.get(field) for field in ("x", "y", "width", "height"))
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
                continue
            geometry[(slide_number, item["elementId"])] = tuple(float(value) for value in values)  # type: ignore[assignment]
    digest = (
        f"sha256:{sha256_canonical_json(sorted(digests, key=lambda item: int(item['slideNumber'])))}"
        if digests
        else None
    )
    return geometry, digest


def validate_hybrid_atomic_binding(
    value: Any,
    expected: dict[str, Any],
    *,
    export_attempt_id: uuid.UUID,
) -> ExportAtomicBinding:
    """Reject sidecars whose atomic identity is not exactly this export."""

    try:
        if not isinstance(value, dict) or set(value) != {
            "presentationId",
            "presentationRevision",
            "canonicalSourceDigest",
            "rendererSourceDigest",
            "pptxSha256",
        }:
            raise ValueError("hybrid atomic binding keys are invalid")
        supplied = ExportAtomicBinding.model_validate(
            {
                "presentation_id": value["presentationId"],
                "export_attempt_id": export_attempt_id,
                "presentation_revision": value["presentationRevision"],
                "canonical_source_sha256": value["canonicalSourceDigest"],
                "renderer_source_sha256": value["rendererSourceDigest"],
                "pptx_sha256": value["pptxSha256"],
            }
        )
        expected_model = ExportAtomicBinding.model_validate(
            {
                "presentation_id": expected["presentationId"],
                "export_attempt_id": export_attempt_id,
                "presentation_revision": expected["presentationRevision"],
                "canonical_source_sha256": expected["canonicalSourceDigest"],
                "renderer_source_sha256": expected["rendererSourceDigest"],
                "pptx_sha256": expected["pptxSha256"],
            }
        )
    except (TypeError, ValueError) as error:
        raise PresentationExportContractError(
            "presentation_export_atomic_binding_mismatch"
        ) from error
    if supplied != expected_model:
        raise PresentationExportContractError(
            "presentation_export_atomic_binding_mismatch"
        )
    return supplied
