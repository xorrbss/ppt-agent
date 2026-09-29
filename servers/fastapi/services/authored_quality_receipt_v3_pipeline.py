"""Live authored-generation adapter for the additive QualityReceipt V3 chain."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from api.v2.contracts import QualityReceiptV3 as PublicQualityReceiptV3
from services.authored_quality_receipt_v2 import (
    QualityReceiptV2,
    QualityReceiptV3,
    QualityReceiptV3Ledger,
    create_quality_receipt_v3_from_v2,
    quality_receipt_manifest_path,
    source_readback_integrity_verified,
)
from services.authored_quality_receipt_v3_helpers import (
    capacity_checks_pass as _capacity_checks_pass,
    consumer as _consumer,
    digest_bound_check as _digest_bound_check,
    mapping as _mapping,
    prefixed_sha256 as _prefixed_sha256,
)
from services.authored_visual_review_evidence import seal_visual_review_evidence
from utils.env_settings.authored_runtime import (
    get_authored_quality_receipt_v3_profile,
)
from services.authored_vnext.capability_registry import (
    CAPABILITY_REGISTRY_DIGEST,
    prompt_capability_clause,
    require_capability_metadata,
)
from services.authored_vnext.master.digests import canonical_digest
from services.source_content.coverage import SourceContentPreservationInventory


QualityReceiptV3DurabilityProfile = Literal[
    "public", "local-development", "unit-test"
]


@runtime_checkable
class QualityReceiptV3PersistencePort(Protocol):
    """Cluster-authoritative append boundary for V3 receipts.

    Implementations must atomically enforce the tuple
    ``(attempt_id, revision, sequence, previous_receipt_digest)`` and treat a
    matching event/evidence digest as idempotent.  The port intentionally owns
    no local-file fallback: PostgreSQL/Object Storage adapters belong at the
    composition root, where their transaction/fence can be shared with
    publication.  A normal public run without this port is blocked rather than
    pretending that a worker-local manifest is durable.
    """

    async def append_quality_receipt_v3(
        self,
        *,
        receipt: QualityReceiptV3,
        evidence: Mapping[str, object],
    ) -> None: ...


class QualityReceiptV3PersistenceError(RuntimeError):
    """Fail-closed durable append error with no storage detail on the wire."""

    code = "quality_receipt_v3_authority_append_failed"
    retryable = True


def quality_receipt_v3_manifest_path(job_id: object) -> Path:
    """Keep V2/V3 durable ledgers adjacent without rewriting the V2 wire file."""

    v2_path = quality_receipt_manifest_path(job_id)
    return v2_path.with_name(f"{v2_path.stem}-v3{v2_path.suffix}")


def _prompt_consumer(design_system_prompt: object | None) -> dict[str, object]:
    """Accept only the exact shared prompt used by the completed deck plan."""

    if not isinstance(design_system_prompt, str) or not design_system_prompt:
        return _consumer(
            "prompt",
            status="unavailable",
            required=True,
            reason_code="prompt_checker_evidence_missing",
        )
    # The clause, not the registry digest: the 2026-09-05 prompt audit removed
    # that digest from the prompt, and looking for it here blocked every deck.
    if prompt_capability_clause() not in design_system_prompt:
        return _consumer(
            "prompt",
            status="unavailable",
            required=True,
            reason_code="prompt_capability_clause_missing",
        )
    return _consumer("prompt", status="passed", required=True)


def _sealed_scene_consumer(scene_envelope: object | None) -> dict[str, object]:
    scene = _mapping(scene_envelope)
    if (
        scene is None
        or scene.get("schemaVersion") != "authored-deck-scene-envelope/v1"
        or not _prefixed_sha256(scene.get("sceneDigest"))
        or not _prefixed_sha256(scene.get("sourceDigest"))
        or not _prefixed_sha256(scene.get("qidDigest"))
    ):
        return _consumer(
            "scene",
            status="unavailable",
            required=True,
            reason_code="scene_checker_evidence_missing",
        )
    return _consumer("scene", status="passed", required=True)


def _durability_profile() -> QualityReceiptV3DurabilityProfile:
    return get_authored_quality_receipt_v3_profile()


def _observed_registry_status(
    values: Sequence[object],
    *,
    consumer: str,
    reason_code: str,
) -> dict[str, object]:
    try:
        if not values:
            raise ValueError("capability evidence missing")
        for value in values:
            payload = _mapping(value)
            metadata = payload.get("capabilityRegistry") if payload is not None else None
            require_capability_metadata(metadata, consumer=consumer)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _consumer(
            consumer,
            status="unavailable",
            required=True,
            reason_code=reason_code,
        )
    return _consumer(consumer, status="passed", required=True)


def _source_content_evidence(
    *,
    source_manifest: object | None,
    source_readback: object | None,
) -> tuple[dict[str, object], dict[str, object]]:
    if source_manifest is None:
        return (
            {"status": "not_required", "required": False},
            _consumer("readback", status="not_required", required=False),
        )

    manifest = _mapping(source_manifest)
    readback = _mapping(source_readback)
    if manifest is None or readback is None or not source_readback_integrity_verified(readback):
        raise ValueError("authored_quality_v3_source_readback_missing")
    require_capability_metadata(readback.get("capabilityRegistry"), consumer="readback")

    raw_units = manifest.get("units")
    if not isinstance(raw_units, Sequence) or isinstance(raw_units, (str, bytes)):
        raise ValueError("authored_quality_v3_source_inventory_invalid")
    blocks: list[Mapping[str, Any]] = []
    for raw_unit in raw_units:
        unit = _mapping(raw_unit)
        raw_blocks = unit.get("blocks") if unit is not None else None
        if not isinstance(raw_blocks, Sequence) or isinstance(raw_blocks, (str, bytes)):
            raise ValueError("authored_quality_v3_source_inventory_invalid")
        for raw_block in raw_blocks:
            block = _mapping(raw_block)
            if block is None:
                raise ValueError("authored_quality_v3_source_inventory_invalid")
            blocks.append(block)

    def occurrences(kind: str) -> tuple[tuple[str, str], ...]:
        result: list[tuple[str, str]] = []
        for block in blocks:
            if block.get("kind") != kind:
                continue
            block_id = block.get("sourceBlockId", block.get("source_block_id"))
            digest = block.get("contentDigest", block.get("content_digest"))
            if not isinstance(block_id, str) or not block_id or not isinstance(digest, str) or not digest:
                raise ValueError("authored_quality_v3_source_inventory_invalid")
            result.append((block_id, digest))
        return tuple(result)

    inventory = SourceContentPreservationInventory(
        source_mode="text-image",
        source_complete_claim=False,
        preserved=("text", "image-occurrences"),
        excluded=("tables", "charts", "original-layout"),
        excluded_source_object_counts={
            "tables": sum(block.get("kind") == "table" for block in blocks),
            "charts": sum(block.get("kind") == "chart" for block in blocks),
            "originalLayoutShapes": sum(block.get("kind") == "shape" for block in blocks),
        },
        text_occurrences=occurrences("text"),
        image_occurrences=occurrences("image"),
    )
    readback_evidence = readback.get("evidence")
    if not isinstance(readback_evidence, Sequence) or isinstance(readback_evidence, (str, bytes)):
        raise ValueError("authored_quality_v3_source_readback_missing")
    observed_ids = {
        item.get("sourceBlockId")
        for raw_item in readback_evidence
        if (item := _mapping(raw_item)) is not None
    }
    expected_ids = {
        block_id for block_id, _ in (*inventory.text_occurrences, *inventory.image_occurrences)
    }
    if observed_ids != expected_ids:
        raise ValueError("authored_quality_v3_source_inventory_readback_mismatch")

    payload = inventory.as_payload()
    payload.update(
        {
            "textOccurrenceCount": inventory.text_occurrence_count,
            "imageOccurrenceCount": inventory.image_occurrence_count,
            "occurrenceInventoryDigest": inventory.occurrence_inventory_digest,
        }
    )
    return payload, _consumer("readback", status="passed", required=True)


def project_persisted_authored_quality_receipt_v3(
    *,
    authored_quality_gate: object,
    quality_receipt_id: str,
    operation_id: str,
    presentation_id: str,
    artifact_digest: str,
    source_revision: int,
    scene_digest: str,
    exporter_version: str,
    evaluated_at: datetime,
    generation_evidence_record: Mapping[str, Any] | None = None,
    review_evidence_record: Mapping[str, Any] | None = None,
    render_runtime_binding_digest: str | None = None,
    result_render_identity_digest: str | None = None,
    checker_policy_digest: str | None = None,
) -> PublicQualityReceiptV3:
    """Load the separate verifier without changing the producer import graph."""
    from services.public_quality_receipt_v3_projection import project_persisted_authored_quality_receipt_v3 as project

    return project(
        authored_quality_gate=authored_quality_gate, quality_receipt_id=quality_receipt_id,
        operation_id=operation_id, presentation_id=presentation_id,
        artifact_digest=artifact_digest, source_revision=source_revision,
        scene_digest=scene_digest, exporter_version=exporter_version, evaluated_at=evaluated_at,
        generation_evidence_record=generation_evidence_record,
        review_evidence_record=review_evidence_record,
        render_runtime_binding_digest=render_runtime_binding_digest,
        result_render_identity_digest=result_render_identity_digest,
        checker_policy_digest=checker_policy_digest,
    )

async def create_generation_quality_receipt_v3(
    *,
    receipt_v2: QualityReceiptV2,
    job_id: object | None,
    slide_roles: Sequence[str],
    provider_attempt_counts: Sequence[int],
    repaired_slide_indices: Sequence[int],
    capacity_evidence: Mapping[str, object],
    safety_evidence: Mapping[str, object],
    browser_metrics: Sequence[object],
    visual_review: Mapping[str, object] | None,
    source_manifest: object | None,
    source_readback: object | None,
    design_system_prompt: object | None = None,
    scene_envelope: object | None = None,
    persistence_port: QualityReceiptV3PersistencePort | None = None,
    durability_profile: QualityReceiptV3DurabilityProfile | None = None,
) -> QualityReceiptV3:
    """Seal actual generation observations and an immutable V2 transition.

    ``persistence_port`` is intentionally injected by the public composition
    root.  It is the only supported way for a public worker to claim an
    authoritative V3 append.  The legacy JSON manifest remains available only
    for an explicit local-development profile and is never selected by a
    normal/public invocation.
    """

    if len(provider_attempt_counts) != len(slide_roles):
        raise ValueError("authored_quality_v3_provider_slide_inventory_mismatch")
    source_content, readback_consumer = _source_content_evidence(
        source_manifest=source_manifest,
        source_readback=source_readback,
    )
    repaired = set(repaired_slide_indices)
    slide_evidence = [
        {"role": role, "repairApplied": index in repaired}
        for index, role in enumerate(slide_roles)
    ]
    provider_calls = {
        "slides": [
            {
                "slideIndex": index,
                "initial": 1,
                "recovery": 1 if count >= 2 else 0,
                "rescue": 1 if count >= 3 else 0,
                "total": count,
            }
            for index, count in enumerate(provider_attempt_counts)
        ]
    }
    tier = "T1" if any(
        item["repairApplied"] or str(item["role"]).lower() in {"cover", "closing", "last", "last_slide"}
        for item in slide_evidence
    ) else "T0"
    # QUAL-04: ``visual_review`` is the actual VQA outcome
    # (``services/authored_visual_review_evidence.py``); ``passed``/``fullDeck``
    # can only come from a run that passed, never from the request flag.
    visual_review_evidence = seal_visual_review_evidence(visual_review, tier=tier)
    profile = durability_profile or _durability_profile()
    scene_consumer = _sealed_scene_consumer(scene_envelope)
    sealed_scene_observed = scene_consumer["status"] == "passed"
    geometry_observed = sealed_scene_observed and _capacity_checks_pass(
        capacity_evidence, "text", "title", "body", "qid", "image"
    )
    font_observed = sealed_scene_observed and _capacity_checks_pass(
        capacity_evidence, "font"
    )
    content_observed = (
        safety_evidence.get("status") == "passed"
        and not safety_evidence.get("residuals", ())
        and sealed_scene_observed
        and _capacity_checks_pass(capacity_evidence, "text", "qid", "image")
    )
    if profile == "public" and persistence_port is None:
        persistence = {
            "status": "unavailable",
            "required": True,
            "reasonCode": "quality_receipt_v3_authority_uninstalled",
        }
    elif profile == "local-development" and persistence_port is None:
        persistence = {
            "status": "passed",
            "required": True,
            "authority": "local-development-manifest",
        }
    elif profile == "unit-test" and persistence_port is None:
        persistence = {"status": "not_required", "required": False}
    else:
        persistence = {
            "status": "passed",
            "required": True,
            "authority": "injected-durable-port",
        }

    consumers = {
        "prompt": _prompt_consumer(design_system_prompt),
        "validator": (
            _consumer("validator", status="passed", required=True)
            if geometry_observed and font_observed and content_observed
            else _consumer(
                "validator",
                status="unavailable",
                required=True,
                reason_code="validator_checker_evidence_missing",
            )
        ),
        "font": (
            _consumer("font", status="passed", required=True)
            if sealed_scene_observed and _capacity_checks_pass(capacity_evidence, "font")
            else _consumer(
                "font",
                status="unavailable",
                required=True,
                reason_code="font_checker_evidence_missing",
            )
        ),
        "chromium": _observed_registry_status(
            browser_metrics,
            consumer="chromium",
            reason_code="chromium_registry_evidence_missing",
        ),
        "scene": scene_consumer,
        "ooxml": _consumer("ooxml", status="not_required", required=False),
        "readback": readback_consumer,
    }
    evidence = {
        "qualityTier": tier,
        "capabilityRegistry": {
            "digest": CAPABILITY_REGISTRY_DIGEST,
            "consumers": consumers,
        },
        "deterministic": {
            "geometry": _digest_bound_check(
                capacity_evidence,
                observed=geometry_observed,
                reason_code="geometry_checker_evidence_missing",
            ),
            "font": _digest_bound_check(
                capacity_evidence,
                observed=font_observed,
                reason_code="font_checker_evidence_missing",
            ),
            "content": _digest_bound_check(
                safety_evidence,
                observed=content_observed,
                reason_code="content_checker_evidence_missing",
            ),
        },
        "providerCalls": provider_calls,
        "sourceContent": source_content,
        "slides": slide_evidence,
        "visualReview": visual_review_evidence,
        "sceneEvidence": (
            dict(_mapping(scene_envelope) or {}) if scene_envelope is not None else None
        ),
        "promptEvidence": (
            {"designSystemDigest": canonical_digest(design_system_prompt)}
            if isinstance(design_system_prompt, str) and design_system_prompt
            else None
        ),
        "persistence": persistence,
        "checkerPayloads": {
            "schemaVersion": "authored-generation-checker-payloads/v1",
            "deckHtmlDigest": receipt_v2.deck_html_digest,
            "capacity": dict(capacity_evidence),
            "safety": dict(safety_evidence),
        },
        # The V2 adapter hashes this transition too. Persist exactly that
        # complete snapshot, not the pre-adaptation caller dictionary.
        "versionTransition": {
            "fromSchemaVersion": "quality-receipt/v2",
            "fromReceiptDigest": receipt_v2.receipt_digest,
            "fromGatePassed": receipt_v2.gate_passed,
        },
    }
    ledger = QualityReceiptV3Ledger(
        storage_path=(
            quality_receipt_v3_manifest_path(job_id)
            if profile == "local-development" and job_id is not None and persistence_port is None
            else None
        )
    )
    receipt = create_quality_receipt_v3_from_v2(
        ledger,
        receipt_v2=receipt_v2,
        evidence=evidence,
    )
    if persistence_port is not None:
        try:
            await persistence_port.append_quality_receipt_v3(
                receipt=receipt,
                evidence=evidence,
            )
        except Exception as error:
            # A public generation may not continue after a failed authoritative
            # append. The surrounding operation transaction owns rollback and
            # retry; storage details must never be projected to clients.
            raise QualityReceiptV3PersistenceError(
                "quality_receipt_v3_authority_append_failed"
            ) from error
    return receipt


__all__ = [
    "create_generation_quality_receipt_v3",
    "project_persisted_authored_quality_receipt_v3",
    "QualityReceiptV3DurabilityProfile",
    "QualityReceiptV3PersistenceError",
    "QualityReceiptV3PersistencePort",
    "quality_receipt_v3_manifest_path",
]
