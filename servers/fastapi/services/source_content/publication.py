"""Pristine receipt sealing and atomic source-content publication gate."""

from __future__ import annotations

import inspect
import base64
import hashlib
import os
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, TypeVar

from services.authored_vnext.master.digests import canonical_digest
from services.source_content.coverage import (
    SceneCoverageResult,
    SourceContentPreservationInventory,
    SourceContentCoverageError,
    verify_scene_coverage,
)
from services.source_content.export_readback import (
    ExportReadbackResult,
    render_candidate_powerpoint_evidence,
    verify_candidate_export,
)


T = TypeVar("T")


@dataclass(frozen=True)
class CompositionApplicationReceipt:
    style_token_digest_matched: Literal[True] = True
    master_geometry_digest_matched: Literal[True] = True


@dataclass(frozen=True)
class SourceContentCoverageReceiptV1:
    contract_version: Literal["source-content-coverage-receipt/v1"]
    presentation_id: str
    presentation_revision: int
    manifest_digest: str
    plan_digest: str
    scene_digest: str
    candidate_artifact_digest: str
    export_readback_digest: str
    discovered_object_coverage: str
    unclassified_object_count: int
    unsupported_content_object_count: int
    text_block_coverage: str
    table_cell_coverage: str
    chart_value_coverage: str
    image_occurrence_coverage: str
    order_violations: int
    fragment_gap_count: int
    fragment_overlap_count: int
    content_digest_mismatches: int
    export_relationship_mismatches: int
    render_clipping_count: int
    render_overflow_count: int
    composition_application: CompositionApplicationReceipt
    unplaced_block_ids: tuple[str, ...]
    preservation_inventory: SourceContentPreservationInventory
    edit_state: Literal["pristine"]
    status: Literal["passed"]
    receipt_digest: str

    def as_json(self) -> dict[str, Any]:
        payload = asdict(self)
        return {
            "contractVersion": payload["contract_version"],
            "presentationId": payload["presentation_id"],
            "presentationRevision": payload["presentation_revision"],
            "manifestDigest": payload["manifest_digest"],
            "planDigest": payload["plan_digest"],
            "sceneDigest": payload["scene_digest"],
            "candidateArtifactDigest": payload["candidate_artifact_digest"],
            "exportReadbackDigest": payload["export_readback_digest"],
            "discoveredObjectCoverage": payload["discovered_object_coverage"],
            "unclassifiedObjectCount": payload["unclassified_object_count"],
            "unsupportedContentObjectCount": payload[
                "unsupported_content_object_count"
            ],
            "textBlockCoverage": payload["text_block_coverage"],
            "tableCellCoverage": payload["table_cell_coverage"],
            "chartValueCoverage": payload["chart_value_coverage"],
            "imageOccurrenceCoverage": payload["image_occurrence_coverage"],
            "orderViolations": payload["order_violations"],
            "fragmentGapCount": payload["fragment_gap_count"],
            "fragmentOverlapCount": payload["fragment_overlap_count"],
            "contentDigestMismatches": payload["content_digest_mismatches"],
            "exportRelationshipMismatches": payload[
                "export_relationship_mismatches"
            ],
            "renderClippingCount": payload["render_clipping_count"],
            "renderOverflowCount": payload["render_overflow_count"],
            "compositionApplication": {
                "styleTokenDigestMatched": True,
                "masterGeometryDigestMatched": True,
            },
            "unplacedBlockIds": list(payload["unplaced_block_ids"]),
            "sourceContent": _source_content_payload(self.preservation_inventory),
            "editState": payload["edit_state"],
            "status": payload["status"],
            "receiptDigest": payload["receipt_digest"],
        }


def _source_content_payload(
    inventory: SourceContentPreservationInventory,
) -> dict[str, Any]:
    return {
        **inventory.as_payload(),
        "textOccurrenceCount": inventory.text_occurrence_count,
        "imageOccurrenceCount": inventory.image_occurrence_count,
        "occurrenceInventoryDigest": inventory.occurrence_inventory_digest,
    }


def _seal_receipt(
    *,
    presentation_id: str,
    presentation_revision: int,
    scene: SceneCoverageResult,
    export: ExportReadbackResult,
) -> SourceContentCoverageReceiptV1:
    if presentation_revision < 1:
        raise SourceContentCoverageError(
            "source_content_coverage_failed",
            detail="coverage receipt requires a positive publication revision",
        )
    values: dict[str, Any] = {
        "contractVersion": "source-content-coverage-receipt/v1",
        "presentationId": presentation_id,
        "presentationRevision": presentation_revision,
        "manifestDigest": scene.manifest_digest,
        "planDigest": scene.plan_digest,
        "sceneDigest": scene.scene_digest,
        "candidateArtifactDigest": export.candidate_artifact_digest,
        "exportReadbackDigest": export.export_readback_digest,
        "discoveredObjectCoverage": scene.discovered_object_coverage,
        "unclassifiedObjectCount": scene.unclassified_object_count,
        "unsupportedContentObjectCount": scene.unsupported_content_object_count,
        "textBlockCoverage": scene.text_block_coverage,
        "tableCellCoverage": scene.table_cell_coverage,
        "chartValueCoverage": scene.chart_value_coverage,
        "imageOccurrenceCoverage": scene.image_occurrence_coverage,
        "orderViolations": scene.order_violations,
        "fragmentGapCount": scene.fragment_gap_count,
        "fragmentOverlapCount": scene.fragment_overlap_count,
        "contentDigestMismatches": scene.content_digest_mismatches,
        "exportRelationshipMismatches": export.export_relationship_mismatches,
        "renderClippingCount": export.render_clipping_count,
        "renderOverflowCount": export.render_overflow_count,
        "compositionApplication": {
            "styleTokenDigestMatched": scene.style_token_digest_matched
            and export.style_token_digest_matched,
            "masterGeometryDigestMatched": scene.master_geometry_digest_matched
            and export.master_geometry_digest_matched,
        },
        "unplacedBlockIds": list(scene.unplaced_block_ids),
        "sourceContent": _source_content_payload(scene.preservation_inventory),
        "editState": "pristine",
        "status": "passed",
    }
    receipt_digest = canonical_digest(values)
    return SourceContentCoverageReceiptV1(
        contract_version="source-content-coverage-receipt/v1",
        presentation_id=presentation_id,
        presentation_revision=presentation_revision,
        manifest_digest=scene.manifest_digest,
        plan_digest=scene.plan_digest,
        scene_digest=scene.scene_digest,
        candidate_artifact_digest=export.candidate_artifact_digest,
        export_readback_digest=export.export_readback_digest,
        discovered_object_coverage=scene.discovered_object_coverage,
        unclassified_object_count=scene.unclassified_object_count,
        unsupported_content_object_count=scene.unsupported_content_object_count,
        text_block_coverage=scene.text_block_coverage,
        table_cell_coverage=scene.table_cell_coverage,
        chart_value_coverage=scene.chart_value_coverage,
        image_occurrence_coverage=scene.image_occurrence_coverage,
        order_violations=scene.order_violations,
        fragment_gap_count=scene.fragment_gap_count,
        fragment_overlap_count=scene.fragment_overlap_count,
        content_digest_mismatches=scene.content_digest_mismatches,
        export_relationship_mismatches=export.export_relationship_mismatches,
        render_clipping_count=export.render_clipping_count,
        render_overflow_count=export.render_overflow_count,
        composition_application=CompositionApplicationReceipt(),
        unplaced_block_ids=scene.unplaced_block_ids,
        preservation_inventory=scene.preservation_inventory,
        edit_state="pristine",
        status="passed",
        receipt_digest=receipt_digest,
    )


def build_pristine_coverage_receipt(
    *,
    presentation_id: str,
    presentation_revision: int,
    manifest: object,
    placement_plan: object,
    materialized_scene: object,
    candidate_pptx: bytes | bytearray | memoryview | str | Path,
    render_evidence: object,
) -> SourceContentCoverageReceiptV1:
    """Run both gates and seal the exact presentation revision/artifact tuple."""

    scene_result = verify_scene_coverage(
        manifest=manifest,
        placement_plan=placement_plan,
        materialized_scene=materialized_scene,
    )
    export_result = verify_candidate_export(
        candidate_pptx=candidate_pptx,
        materialized_scene=materialized_scene,
        render_evidence=render_evidence,
    )
    return _seal_receipt(
        presentation_id=presentation_id,
        presentation_revision=presentation_revision,
        scene=scene_result,
        export=export_result,
    )


def require_publication_ready(
    receipt: SourceContentCoverageReceiptV1,
    *,
    presentation_id: str,
    presentation_revision: int,
    candidate_artifact_digest: str,
) -> None:
    """Reject stale, copied, failed, or artifact-mismatched receipts."""

    receipt_payload = receipt.as_json()
    sealed_receipt_digest = receipt_payload.pop("receiptDigest", None)
    if sealed_receipt_digest != canonical_digest(receipt_payload):
        raise SourceContentCoverageError(
            "source_content_publication_blocked",
            detail="coverage receipt digest does not bind its source-content inventory",
        )

    if (
        receipt.contract_version != "source-content-coverage-receipt/v1"
        or receipt.status != "passed"
        or receipt.edit_state != "pristine"
        or receipt.presentation_id != presentation_id
        or receipt.presentation_revision != presentation_revision
        or receipt.candidate_artifact_digest != candidate_artifact_digest
        or receipt.unplaced_block_ids
        or receipt.preservation_inventory.source_mode != "text-image"
        or receipt.preservation_inventory.source_complete_claim is not False
        or receipt.preservation_inventory.preserved != ("text", "image-occurrences")
        or receipt.preservation_inventory.excluded != (
            "tables",
            "charts",
            "original-layout",
        )
        or receipt.text_block_coverage
        != f"{receipt.preservation_inventory.text_occurrence_count}/"
        f"{receipt.preservation_inventory.text_occurrence_count}"
        or receipt.image_occurrence_coverage
        != f"{receipt.preservation_inventory.image_occurrence_count}/"
        f"{receipt.preservation_inventory.image_occurrence_count}"
        or any(
            value != 0
            for value in (
                receipt.unclassified_object_count,
                receipt.unsupported_content_object_count,
                receipt.order_violations,
                receipt.fragment_gap_count,
                receipt.fragment_overlap_count,
                receipt.content_digest_mismatches,
                receipt.export_relationship_mismatches,
                receipt.render_clipping_count,
                receipt.render_overflow_count,
            )
        )
        or receipt.composition_application.style_token_digest_matched is not True
        or receipt.composition_application.master_geometry_digest_matched is not True
    ):
        raise SourceContentCoverageError(
            "source_content_coverage_failed",
            detail="publication requires the pristine passed receipt for this exact revision and artifact",
        )


async def publish_verified_source_content(
    *,
    presentation_id: str,
    presentation_revision: int,
    manifest: object,
    placement_plan: object,
    materialized_scene: object,
    candidate_pptx: bytes | bytearray | memoryview | str | Path,
    render_evidence: object,
    atomic_publish: Callable[[SourceContentCoverageReceiptV1], T | Awaitable[T]],
) -> T:
    """Verify then hand one sealed receipt to the caller's publication transaction.

    ``atomic_publish`` is the only mutation callback. It must persist the receipt,
    presentation pointer, and candidate artifact in one transaction; this helper
    never publishes or falls back on verification failure.
    """

    receipt = build_pristine_coverage_receipt(
        presentation_id=presentation_id,
        presentation_revision=presentation_revision,
        manifest=manifest,
        placement_plan=placement_plan,
        materialized_scene=materialized_scene,
        candidate_pptx=candidate_pptx,
        render_evidence=render_evidence,
    )
    require_publication_ready(
        receipt,
        presentation_id=presentation_id,
        presentation_revision=presentation_revision,
        candidate_artifact_digest=receipt.candidate_artifact_digest,
    )
    result = atomic_publish(receipt)
    if inspect.isawaitable(result):
        return await result
    return result


def _candidate_assets(scene: Any, master_contract: Any, *, owner_id: uuid.UUID) -> list[dict[str, Any]]:
    """Resolve only digest-pinned Scene and master assets into worker bytes."""

    from services.authored_vnext.integration.export_payload import (
        semantic_asset_references,
    )
    from services.authored_vnext.master.assets import OwnerMasterAssetStore

    result: dict[str, dict[str, Any]] = {}
    for entry in scene.asset_manifest:
        prefix = f"data:{entry.media_type};base64,"
        if not entry.storage_reference.startswith(prefix):
            raise SourceContentCoverageError(
                "source_content_export_readback_failed",
                detail="candidate export requires locally sealed Scene asset bytes",
            )
        content = base64.b64decode(
            entry.storage_reference.removeprefix(prefix), validate=True
        )
        result[entry.asset_id] = {
            "assetId": entry.asset_id,
            "mediaType": entry.media_type,
            "sha256": entry.asset_digest.removeprefix("sha256:"),
            "sizeBytes": len(content),
            "bytesBase64": base64.b64encode(content).decode("ascii"),
        }
    master_refs = {
        asset.asset_id: asset.digest
        for asset in (master_contract.brand_assets.logo, master_contract.brand_assets.wordmark)
        if asset is not None
    }
    master_refs.update(semantic_asset_references(master_contract))
    store = OwnerMasterAssetStore(
        Path(os.getenv("APP_DATA_DIRECTORY", "app_data"))
        / "authored_vnext"
        / "master-assets"
        / "users"
        / str(owner_id)
    )
    for asset_id, digest in master_refs.items():
        content = store.open_verified(asset_id, digest)
        existing = result.get(asset_id)
        if existing is not None and existing["sha256"] != digest.removeprefix("sha256:"):
            raise SourceContentCoverageError(
                "source_content_export_readback_failed",
                detail="Scene and master reuse an asset ID with different bytes",
            )
        result[asset_id] = {
            "assetId": asset_id,
            "mediaType": "image/png",
            "sha256": digest.removeprefix("sha256:"),
            "sizeBytes": len(content),
            "bytesBase64": base64.b64encode(content).decode("ascii"),
        }
    return [result[key] for key in sorted(result)]


async def _append_workbench_snapshot(
    session: Any,
    *,
    owner_id: uuid.UUID,
    deck_snapshot: Any,
    master_contract: Any,
) -> None:
    """Append the pinned master and initial Workbench head in one transaction."""

    from sqlalchemy import select
    from models.authored_vnext.master.operations import MasterRevisionResponse
    from models.sql.authored_vnext_repository import (
        AuthoredVNextDeckRevision,
        AuthoredVNextEntityHead,
        AuthoredVNextMasterRevision,
        AuthoredVNextPublicationPointer,
        AuthoredVNextSlideRevision,
    )

    owner_scope = str(owner_id)
    tenant_id = os.getenv("AUTHORED_VNEXT_TENANT_ID", "default")
    master_pin = deck_snapshot.master
    if (
        master_contract.master_id != master_pin.master_id
        or master_contract.digest != master_pin.master_digest
    ):
        raise SourceContentCoverageError(
            "source_content_composition_not_applied",
            detail="Workbench master contract does not match the sealed deck pin",
        )
    master_row = await session.scalar(
        select(AuthoredVNextMasterRevision).where(
            AuthoredVNextMasterRevision.owner_scope == owner_scope,
            AuthoredVNextMasterRevision.tenant_id == tenant_id,
            AuthoredVNextMasterRevision.master_id == master_pin.master_id,
            AuthoredVNextMasterRevision.revision == master_pin.master_revision,
        )
    )
    master_head = await session.scalar(
        select(AuthoredVNextEntityHead).where(
            AuthoredVNextEntityHead.owner_scope == owner_scope,
            AuthoredVNextEntityHead.tenant_id == tenant_id,
            AuthoredVNextEntityHead.entity_kind == "master",
            AuthoredVNextEntityHead.entity_id == master_pin.master_id,
        )
    )
    master_publication = await session.scalar(
        select(AuthoredVNextPublicationPointer).where(
            AuthoredVNextPublicationPointer.owner_scope == owner_scope,
            AuthoredVNextPublicationPointer.tenant_id == tenant_id,
            AuthoredVNextPublicationPointer.publication_kind == "master",
            AuthoredVNextPublicationPointer.entity_id == master_pin.master_id,
        )
    )
    expected_master = (
        master_pin.master_revision,
        master_pin.master_digest,
    )
    if master_row is not None:
        if (
            (master_row.revision, master_row.digest) != expected_master
            or master_row.status != "published"
            or master_head is None
            or (master_head.revision, master_head.digest) != expected_master
            or master_publication is None
            or (
                master_publication.revision,
                master_publication.digest,
            )
            != expected_master
        ):
            raise SourceContentCoverageError(
                "source_content_composition_not_applied",
                detail="Workbench master publication pin conflicts with stored state",
            )
    elif master_pin.master_id.startswith("master:source-content-"):
        if (
            master_head is not None
            or master_publication is not None
            or master_pin.master_revision != 1
        ):
            raise SourceContentCoverageError(
                "source_content_composition_not_applied",
                detail="synthetic Workbench master has conflicting stored state",
            )
        master_record = MasterRevisionResponse(
            master_id=master_pin.master_id,
            revision=master_pin.master_revision,
            digest=master_pin.master_digest,
            status="published",
            contract=master_contract,
        )
        session.add(
            AuthoredVNextMasterRevision(
                owner_id=owner_id,
                owner_scope=owner_scope,
                tenant_id=tenant_id,
                master_id=master_record.master_id,
                revision=master_record.revision,
                digest=master_record.digest,
                status=master_record.status,
                payload=master_record.model_dump(mode="json", by_alias=True),
            )
        )
        session.add(
            AuthoredVNextEntityHead(
                owner_id=owner_id,
                owner_scope=owner_scope,
                tenant_id=tenant_id,
                entity_kind="master",
                entity_id=master_record.master_id,
                revision=master_record.revision,
                digest=master_record.digest,
                generation=1,
            )
        )
        session.add(
            AuthoredVNextPublicationPointer(
                owner_id=owner_id,
                owner_scope=owner_scope,
                tenant_id=tenant_id,
                publication_kind="master",
                entity_id=master_record.master_id,
                revision=master_record.revision,
                digest=master_record.digest,
                generation=1,
            )
        )
    else:
        raise SourceContentCoverageError(
            "source_content_composition_not_applied",
            detail="selected company master is not published for this owner",
        )

    head = await session.scalar(
        select(AuthoredVNextEntityHead).where(
            AuthoredVNextEntityHead.owner_scope == owner_scope,
            AuthoredVNextEntityHead.tenant_id == tenant_id,
            AuthoredVNextEntityHead.entity_kind == "deck",
            AuthoredVNextEntityHead.entity_id == deck_snapshot.deck_id,
        )
    )
    if head is not None:
        if head.revision == deck_snapshot.revision and head.digest == deck_snapshot.digest:
            return
        raise SourceContentCoverageError(
            "source_content_coverage_failed",
            detail="Workbench deck publication fence failed",
        )
    if deck_snapshot.revision != 1:
        raise SourceContentCoverageError(
            "source_content_coverage_failed",
            detail="initial Workbench snapshot must start at revision one",
        )
    session.add(
        AuthoredVNextDeckRevision(
            owner_id=owner_id,
            owner_scope=owner_scope,
            tenant_id=tenant_id,
            deck_id=deck_snapshot.deck_id,
            revision=deck_snapshot.revision,
            digest=deck_snapshot.digest,
            master_id=deck_snapshot.master.master_id,
            master_revision=deck_snapshot.master.master_revision,
            master_digest=deck_snapshot.master.master_digest,
            payload=deck_snapshot.model_dump(mode="json", by_alias=True),
        )
    )
    for item in deck_snapshot.slides:
        slide = item.slide
        session.add(
            AuthoredVNextSlideRevision(
                owner_id=owner_id,
                owner_scope=owner_scope,
                tenant_id=tenant_id,
                deck_id=deck_snapshot.deck_id,
                slide_id=slide.slide_id,
                revision=slide.revision,
                spec_digest=slide.spec_digest,
                master_digest=slide.master_digest,
                payload=item.model_dump(mode="json", by_alias=True),
            )
        )
    session.add(
        AuthoredVNextEntityHead(
            owner_id=owner_id,
            owner_scope=owner_scope,
            tenant_id=tenant_id,
            entity_kind="deck",
            entity_id=deck_snapshot.deck_id,
            revision=deck_snapshot.revision,
            digest=deck_snapshot.digest,
            generation=1,
        )
    )
    await session.flush()


async def publish_source_content_generation(
    *,
    request: Any,
    presentation_id: uuid.UUID,
    manifest: Any,
    placement_plan: Any,
    materialized_scene: Any,
    deck_snapshot: Any,
    master_contract: Any,
    async_status: Any,
    export_cookie_header: str | None,
    generation_job: Any,
    progress_reporter: Any,
    sql_session: Any,
) -> Any:
    """Create, render-readback, and atomically publish the source-content deck."""

    del export_cookie_header, progress_reporter
    from sqlalchemy import delete
    from models.presentation_and_path import PresentationPathAndEditPath
    from models.sql.presentation import (
        PRESENTATION_LIFECYCLE_STAGING,
        PresentationModel,
    )
    from models.sql.slide import SlideModel
    from services.authored_checkpoint_service import atomic_write
    from services.authored_vnext.integration.export_payload import worker_master_contract
    from services.authored_vnext.ooxml.native_scene import materialize_scene_pptx
    from services.presentation_generation_export_orchestration import publish_staged_presentation
    from services.source_content.errors import SourceContentError
    from services.source_content.manifest_store import (
        append_source_content_coverage_receipt,
        bind_source_content_generation,
    )
    from utils.asset_directory_utils import get_exports_directory
    from utils.owner_context import get_current_owner_id

    owner_id = get_current_owner_id()
    if owner_id is None:
        raise SourceContentError("source_content_owner_required", status_code=401)
    if (
        deck_snapshot is None
        or master_contract is None
        or deck_snapshot.scene != materialized_scene.scene
    ):
        raise SourceContentError("source_content_coverage_failed", status_code=409)
    try:
        assets = _candidate_assets(
            materialized_scene.scene,
            master_contract,
            owner_id=owner_id,
        )
        raw_asset_digests = {
            item["assetId"]: item["sha256"] for item in assets
        }
        authored_template = getattr(
            getattr(deck_snapshot, "composition", None),
            "authored_template",
            None,
        )
        master_value = worker_master_contract(
            master_contract,
            raw_asset_digests,
            authored_template=authored_template,
        )
        candidate_bytes, _native_receipt = materialize_scene_pptx(
            # The OOXML worker consumes the sealed transport mapping. Passing
            # the Pydantic Scene object directly is rejected before schema
            # validation because the worker intentionally has no model-layer
            # dependency.
            materialized_scene.scene.model_dump(mode="json", by_alias=True),
            assets,
            master_value,
        )
        artifact_digest = f"sha256:{hashlib.sha256(candidate_bytes).hexdigest()}"
        # Workspace artifact publication intentionally accepts only sources
        # inside APP_DATA_DIRECTORY.  Keep the short-lived render candidate
        # there as well; the PowerPoint QA helper may use the process temp
        # directory internally, but the file handed to the durable publisher
        # must remain within the owned application-data root.
        staging_root = (
            Path(get_exports_directory())
            / "source-content"
            / ".staging"
            / str(presentation_id)
        )
        staging_root.mkdir(parents=True, exist_ok=True)
        candidate_context = tempfile.TemporaryDirectory(
            prefix="source-content-candidate-",
            dir=str(staging_root),
        )
        candidate_path = Path(candidate_context.name) / "candidate.pptx"
        atomic_write(candidate_path, candidate_bytes)
        render_evidence = await render_candidate_powerpoint_evidence(
            candidate_pptx=candidate_path,
            materialized_scene=materialized_scene,
        )
        # Do not hold SQLite's single writer lock across the multi-minute
        # PowerPoint render/reopen QA. Bind only after the external evidence is
        # available, then continue with the short atomic publication write.
        await bind_source_content_generation(
            sql_session,
            owner_id=owner_id,
            manifest=manifest,
            composition_pin=placement_plan.composition_pin.model_dump(
                mode="json", by_alias=True
            ),
            content_policy=request.content_policy.model_dump(
                mode="json", by_alias=True
            ),
            generation_engine_version="source-content-deterministic-scene/v1",
            presentation_id=presentation_id,
        )
        presentation = await sql_session.get(PresentationModel, presentation_id)
        if presentation is None:
            presentation = PresentationModel(
                id=presentation_id,
                owner_id=owner_id,
                content="",
                n_slides=len(materialized_scene.scene.slides),
                language=request.language or "Korean",
                lifecycle_status=PRESENTATION_LIFECYCLE_STAGING,
            )
            sql_session.add(presentation)
            await sql_session.flush()
        if presentation.owner_id != owner_id:
            raise SourceContentCoverageError(
                "source_content_coverage_failed", detail="presentation owner changed"
            )
        publication_revision = presentation.revision + 1
        receipt = build_pristine_coverage_receipt(
            presentation_id=str(presentation_id),
            presentation_revision=publication_revision,
            manifest=manifest,
            placement_plan=placement_plan,
            materialized_scene=materialized_scene,
            candidate_pptx=candidate_bytes,
            render_evidence=render_evidence,
        )
        require_publication_ready(
            receipt,
            presentation_id=str(presentation_id),
            presentation_revision=publication_revision,
            candidate_artifact_digest=artifact_digest,
        )
        presentation.content = ""
        presentation.n_slides = len(materialized_scene.scene.slides)
        presentation.language = request.language or presentation.language or "Korean"
        presentation.title = presentation.title or "원본 콘텐츠 재디자인"
        presentation.reference_snapshot_ids = [manifest.reference_snapshot_id]
        presentation.mode = "authored"
        presentation.lifecycle_status = PRESENTATION_LIFECYCLE_STAGING
        presentation.theme = {
            "mode": "authored",
            "style": placement_plan.composition_pin.authored_style_id,
            "sourceContent": {
                "sourceMode": "source_content_restyle",
                "manifestDigest": manifest.manifest_digest,
                "planDigest": placement_plan.plan_digest,
                "sourceCoverageStatus": "passed",
                "coverageReceiptDigest": receipt.receipt_digest,
                "editState": "pristine",
            },
            "authored_vnext": {
                "schemaVersion": "authored-vnext-scene-projection/v1",
                "deckId": deck_snapshot.deck_id,
                "deckRevision": deck_snapshot.revision,
                "deckDigest": deck_snapshot.digest,
                "sourceDigest": deck_snapshot.source_digest,
                "sceneDigest": materialized_scene.scene.scene_digest,
                "scene": materialized_scene.scene.model_dump(mode="json", by_alias=True),
                "masterPin": deck_snapshot.master.model_dump(mode="json", by_alias=True),
                "status": "ready",
                "reasonCode": "source_content_pristine_passed",
                "promotionReady": False,
                "deploymentAuthorized": False,
            },
        }
        await sql_session.execute(
            delete(SlideModel).where(SlideModel.presentation == presentation_id)
        )
        for index, scene_slide in enumerate(materialized_scene.scene.slides):
            sql_session.add(
                SlideModel(
                    owner_id=owner_id,
                    presentation=presentation_id,
                    layout_group="authored",
                    layout=f"authored:source-content:{index + 1}",
                    index=index,
                    content={
                        "__authored__": True,
                        "sceneSlideDigest": scene_slide.slide_digest,
                        "sceneSlide": scene_slide.model_dump(mode="json", by_alias=True),
                    },
                )
            )
        await _append_workbench_snapshot(
            sql_session,
            owner_id=owner_id,
            deck_snapshot=deck_snapshot,
            master_contract=master_contract,
        )
        await append_source_content_coverage_receipt(
            sql_session,
            owner_id=owner_id,
            presentation_id=presentation_id,
            presentation_revision=publication_revision,
            receipt=receipt.as_json(),
        )
        publication_source = candidate_path
        durable_candidate_created = False
        workspace_operation_id = getattr(
            generation_job, "workspace_operation_id", None
        )
        if workspace_operation_id is not None:
            # The shared workspace publisher reserves its artifact intent in a
            # short-lived independent DB session.  Close the source-content
            # staging transaction first so SQLite does not hold a write lock
            # across that hand-off (the final publication commit remains in
            # publish_staged_presentation).
            await sql_session.commit()
        if workspace_operation_id is None:
            publication_source = (
                Path(get_exports_directory())
                / "source-content"
                / str(owner_id)
                / str(presentation_id)
                / f"{artifact_digest.removeprefix('sha256:')}.pptx"
            )
            if publication_source.exists():
                if hashlib.sha256(publication_source.read_bytes()).digest() != hashlib.sha256(
                    candidate_bytes
                ).digest():
                    raise SourceContentCoverageError(
                        "source_content_export_readback_failed",
                        detail="content-addressed publication path contains different bytes",
                    )
            else:
                atomic_write(publication_source, candidate_bytes)
                durable_candidate_created = True
        try:
            published_path = await publish_staged_presentation(
                sql_session,
                presentation,
                str(publication_source),
                generation_job=generation_job,
                async_status=async_status,
                published_edit_path=(
                    "/authored-vnext/workbench?authoredDeckId="
                    f"{deck_snapshot.deck_id}"
                ),
            )
        except BaseException:
            if durable_candidate_created:
                publication_source.unlink(missing_ok=True)
            raise
        result = PresentationPathAndEditPath(
            presentation_id=presentation_id,
            path=published_path,
            edit_path=(
                "/authored-vnext/workbench?authoredDeckId="
                f"{deck_snapshot.deck_id}"
            ),
        )
        candidate_context.cleanup()
        return result
    except SourceContentCoverageError as error:
        await sql_session.rollback()
        raise SourceContentError(error.code, status_code=422, detail={"reason": error.detail}) from error
    finally:
        if "candidate_context" in locals():
            candidate_context.cleanup()


__all__ = [
    "SourceContentCoverageReceiptV1",
    "build_pristine_coverage_receipt",
    "publish_verified_source_content",
    "publish_source_content_generation",
    "require_publication_ready",
]
