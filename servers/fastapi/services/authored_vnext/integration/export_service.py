"""Durable export orchestration across SQL, the OOXML worker, and artifact storage."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import json
from typing import Any
import uuid

from services.authored_vnext.integration.artifact_store import (
    ArtifactStoreError,
    BufferedArtifactStore,
)
from services.authored_vnext.integration.export_payload import (
    build_worker_request,
    semantic_asset_references,
)
from services.authored_vnext.integration.sql_repository import (
    AuthoredVNextSqlRepository,
    ExportArtifactInput,
    OperationFenceError,
    PublicationConflictError,
    SourceDigestMismatchError,
)
from services.authored_vnext.integration.worker import OoxmlWorkerTransport, WorkerError
from services.authored_vnext.master.assets import MasterAssetError, OwnerMasterAssetStore
from services.authored_vnext.generation.ai_text_zones import (
    ResolvedAITextZoneSet,
    materialize_ai_text_zone_contract,
)
from services.authored_vnext.generation.effective_style import (
    materialize_authored_composition,
)


class ExportServiceError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def _assert_worker_readback(
    receipt: dict[str, Any], payload: dict[str, Any]
) -> None:
    """Reject a syntactically successful worker response with any silent loss."""
    package = receipt.get("package")
    preservation = receipt.get("preservation")
    master = receipt.get("master")
    if (
        receipt.get("validation") != "candidate-reopened"
        or receipt.get("candidateDigestVerified") is not True
        or not isinstance(package, dict)
        or not isinstance(preservation, dict)
        or not isinstance(master, dict)
        or not isinstance(receipt.get("slides"), list)
    ):
        raise ExportServiceError("authored_vnext_worker_read_back_invalid")
    furniture = payload["master"].get("furniture", [])
    font_files = payload["master"].get("fontManifest", {}).get("files", [])
    expected_master = {
        "furnitureCount": len(furniture),
        "logoCount": sum(item.get("kind") == "logo" for item in furniture),
        "pageNumberCount": sum(
            item.get("kind") == "pageNumber" for item in furniture
        ),
        "fontFileCount": len(font_files),
        "duplicateFurnitureCount": 0,
        "fontFilesMode": "REFERENCE_METADATA_VERIFIED",
    }
    if any(master.get(key) != value for key, value in expected_master.items()):
        raise ExportServiceError("authored_vnext_worker_master_read_back_mismatch")
    if (
        preservation.get("silentLossCount") != 0
        or preservation.get("missingParts") != []
        or preservation.get("unexpectedChangedParts") != []
        or package.get("macroDetected") is not False
        or package.get("quarantineReasons") != []
        or package.get("slideCount") != len(payload["slides"])
        or len(receipt["slides"]) != len(payload["slides"])
    ):
        raise ExportServiceError("authored_vnext_worker_preservation_mismatch")
    for page_number, (expected_slide, actual_slide) in enumerate(
        zip(payload["slides"], receipt["slides"], strict=True),
        start=1,
    ):
        encoded = expected_slide.get("values", {}).get(
            "__resolvedSnapshotEvidence"
        )
        if encoded is None:
            continue
        try:
            expected = json.loads(encoded)
        except (TypeError, ValueError) as error:
            raise ExportServiceError(
                "authored_vnext_resolved_snapshot_evidence_invalid"
            ) from error
        checks = {
            "bodyGrammarDigest": expected.get("bodyGrammarDigest"),
            "referenceLayoutDigest": expected.get(
                "resolvedReferenceLayoutDigest"
            ),
            "resolvedTemplateId": expected.get("resolvedTemplateId"),
            "resolvedCompositionDigest": expected.get(
                "resolvedCompositionDigest"
            ),
            "resolvedRuleDigest": expected.get("resolvedRuleDigest"),
            "pageNumber": page_number,
            "slideCount": len(payload["slides"]),
            "semanticRolesUsed": expected.get("semanticRolesUsed"),
            "layoutRolesUsed": expected.get("layoutRolesUsed"),
        }
        if any(actual_slide.get(key) != value for key, value in checks.items()):
            raise ExportServiceError(
                "authored_vnext_resolved_snapshot_read_back_mismatch"
            )


@dataclass(frozen=True)
class CreateJobCommand:
    deck_id: str
    expected_deck_revision: int
    expected_deck_digest: str
    expected_master_revision: int
    expected_master_digest: str
    expected_active_artifact_revision: int
    expected_active_artifact_digest: str | None
    expected_source_package_digest: str | None
    expected_asset_digests: list[str]
    expected_font_digest: str
    idempotency_key: str
    timeout_ms: int
    max_attempts: int
    request_payload: dict[str, Any]
    operation_fence: int = 1


class AuthoredVNextExportService:
    def __init__(
        self,
        repository: AuthoredVNextSqlRepository,
        worker: OoxmlWorkerTransport,
        artifact_store: BufferedArtifactStore,
        master_asset_store: OwnerMasterAssetStore | None = None,
    ) -> None:
        self.repository = repository
        self.worker = worker
        self.artifact_store = artifact_store
        self.master_asset_store = master_asset_store

    def _materialize_ai_text_zones(
        self,
        contract,
        request_payload: dict[str, Any],
        *,
        expected_master_digest: str,
    ):
        payload = request_payload.get("aiTextZones")
        if payload is None:
            return contract
        # Legacy semantic AI text placeholders are compatibility-read only.
        # A furniture-only effective contract exposes geometry-only aiEditZones,
        # so persisted text-zone values cannot mutate template-owned content.
        if not any(
            element.content.role == "ai-text-zone"
            for element in contract.semantic_elements
        ):
            return contract
        try:
            resolved = ResolvedAITextZoneSet.model_validate(payload)
        except ValueError as error:
            raise ExportServiceError("authored_vnext_ai_text_zones_invalid") from error
        try:
            return materialize_ai_text_zone_contract(
                contract,
                resolved,
                expected_master_digest=expected_master_digest,
            )
        except ValueError as error:
            raise ExportServiceError("authored_vnext_ai_text_zones_mismatch") from error

    def _materialize_composition(
        self,
        contract,
        deck,
        request_payload: dict[str, Any],
        *,
        expected_master_digest: str,
    ):
        """Replay the persisted composition before any runtime-only AI text binding."""

        pinned = getattr(deck, "composition", None)
        payload = request_payload.get("compositionPin")
        if payload is not None:
            try:
                from models.authored_vnext.deck.composition import AuthoredCompositionPin

                requested = AuthoredCompositionPin.model_validate(payload)
            except ValueError as error:
                raise ExportServiceError("authored_vnext_composition_invalid") from error
            if pinned is None or requested != pinned:
                raise ExportServiceError("authored_vnext_composition_mismatch")
        if pinned is None:
            return contract
        if pinned.master_digest != expected_master_digest:
            raise ExportServiceError("authored_vnext_composition_master_mismatch")
        try:
            return materialize_authored_composition(contract, pinned)
        except ValueError as error:
            raise ExportServiceError("authored_vnext_composition_mismatch") from error

    def _worker_assets(self, master) -> list[dict[str, str]]:
        references = {
            asset.asset_id: asset.digest
            for asset in (master.brand_assets.logo, master.brand_assets.wordmark)
            if asset is not None
        }
        semantic_references = semantic_asset_references(master)
        furniture_ids = {
            item.asset_id
            for item in (
                master.furniture.logo,
                master.furniture.header,
                master.furniture.footer,
                master.furniture.divider,
                master.furniture.page_number,
            )
            if item.asset_id is not None
        }
        if not references and not furniture_ids and not semantic_references:
            return []
        if references.keys() != furniture_ids or self.master_asset_store is None:
            raise ExportServiceError("authored_vnext_master_asset_reference_mismatch")
        for asset_id, digest in semantic_references.items():
            if asset_id in references and references[asset_id] != digest:
                raise ExportServiceError("authored_vnext_master_asset_reference_mismatch")
            references[asset_id] = digest
        assets = []
        try:
            for asset_id, digest in sorted(references.items()):
                value = self.master_asset_store.open_verified(asset_id, digest)
                assets.append({
                    "assetId": asset_id,
                    "mediaType": "image/png",
                    "sha256": digest.removeprefix("sha256:"),
                    "bytesBase64": base64.b64encode(value).decode("ascii"),
                })
        except MasterAssetError as error:
            raise ExportServiceError(error.code) from error
        return assets

    def create_job(self, command: CreateJobCommand):
        deck = self.repository.get_deck(command.deck_id)
        if (deck.revision, deck.digest) != (
            command.expected_deck_revision,
            command.expected_deck_digest,
        ):
            raise ExportServiceError("authored_vnext_deck_digest_mismatch")
        master_pin = getattr(deck, "master", None)
        deck_master_revision = (
            master_pin.master_revision
            if master_pin is not None
            else deck.master_revision
        )
        deck_master_digest = (
            master_pin.master_digest if master_pin is not None else deck.master_digest
        )
        deck_master_id = master_pin.master_id if master_pin is not None else deck.master_id
        if (deck_master_revision, deck_master_digest) != (
            command.expected_master_revision,
            command.expected_master_digest,
        ):
            raise ExportServiceError("authored_vnext_master_digest_mismatch")
        master = self.repository.get_master(deck_master_id, deck_master_revision)
        if master.status != "published" or master.digest != deck_master_digest:
            raise ExportServiceError("authored_vnext_master_not_published")
        composed_contract = self._materialize_composition(
            master.contract,
            deck,
            command.request_payload,
            expected_master_digest=master.digest,
        )
        export_contract = self._materialize_ai_text_zones(
            composed_contract,
            command.request_payload,
            expected_master_digest=master.digest,
        )
        try:
            assets = self._worker_assets(export_contract)
            _, source_digests = build_worker_request(
                operation_id="digest-probe",
                fence_token=1,
                master=export_contract,
                deck=deck,
                expected_package_digest=command.expected_source_package_digest,
                assets=assets,
            )
        except ValueError as error:
            raise ExportServiceError(str(error)) from error
        # The public CAS pin is the sealed manifest digest exposed by the
        # published master.  The worker payload digest below intentionally
        # covers the serialized manifest including that seal and is retained
        # separately for worker/read-back integrity.
        if master.contract.font_manifest.digest != command.expected_font_digest:
            raise ExportServiceError("authored_vnext_font_digest_mismatch")
        expected_asset_manifest = source_digests["assetManifest"]
        actual_asset_digests = sorted(
            f"sha256:{asset['sha256']}" for asset in assets
        )
        if sorted(set(command.expected_asset_digests)) != actual_asset_digests:
            raise ExportServiceError("authored_vnext_asset_digest_mismatch")
        active = self.repository.get_active_artifact(deck.deck_id)
        actual_publication = (
            (active[0].revision, active[0].digest) if active is not None else (0, None)
        )
        if actual_publication != (
            command.expected_active_artifact_revision,
            command.expected_active_artifact_digest,
        ):
            raise ExportServiceError("authored_vnext_publication_conflict")
        expected_revision = command.expected_active_artifact_revision or None
        return self.repository.create_export_job(
            deck_id=deck.deck_id,
            deck_revision=deck.revision,
            deck_digest=deck.digest,
            master_revision=deck_master_revision,
            master_digest=source_digests["master"],
            master_id=deck_master_id,
            spec_digest=source_digests["spec"],
            asset_manifest_digest=expected_asset_manifest,
            font_manifest_digest=source_digests["fontManifest"],
            expected_package_digest=command.expected_source_package_digest,
            expected_publication_revision=expected_revision,
            expected_publication_digest=command.expected_active_artifact_digest,
            timeout_ms=command.timeout_ms,
            max_attempts=command.max_attempts,
            operation_fence=command.operation_fence,
            idempotency_key=command.idempotency_key,
            request_payload=command.request_payload,
        )

    async def run_job(self, job_id: uuid.UUID):
        claimed = self.repository.claim_export_job(
            job_id,
            worker_id=f"fastapi:{uuid.uuid4()}",
            lease_seconds=600,
        )
        try:
            deck = self.repository.get_deck(claimed.deck_id, claimed.deck_revision)
            master_pin = getattr(deck, "master", None)
            master = self.repository.get_master(
                master_pin.master_id if master_pin is not None else deck.master_id,
                master_pin.master_revision if master_pin is not None else deck.master_revision,
            )
            composed_contract = self._materialize_composition(
                master.contract,
                deck,
                claimed.request_payload,
                expected_master_digest=master.digest,
            )
            export_contract = self._materialize_ai_text_zones(
                composed_contract,
                claimed.request_payload,
                expected_master_digest=master.digest,
            )
            assets = self._worker_assets(export_contract)
            payload, digests = build_worker_request(
                operation_id=str(claimed.id),
                fence_token=claimed.fence_token,
                master=export_contract,
                deck=deck,
                expected_package_digest=claimed.expected_package_digest,
                assets=assets,
            )
            actual = (
                digests["spec"],
                digests["assetManifest"],
                digests["fontManifest"],
            )
            expected = (
                claimed.spec_digest,
                claimed.asset_manifest_digest,
                claimed.font_manifest_digest,
            )
            if actual != expected or digests["master"] != claimed.master_digest:
                raise ExportServiceError("authored_vnext_source_digest_mismatch")
            result = await self.worker.execute(payload, claimed.timeout_ms)
            artifact = result.get("artifact")
            receipt = result.get("receipt")
            if (
                result.get("schemaVersion") != "authored-vnext-ooxml-worker/v1"
                or result.get("operation") != "EXPORT_PPTX"
                or result.get("status") != "SUCCEEDED"
                or result.get("operationId") != str(claimed.id)
                or result.get("fencingToken") != claimed.fence_token
                or not isinstance(artifact, dict)
                or not isinstance(receipt, dict)
            ):
                raise ExportServiceError("authored_vnext_worker_receipt_mismatch")
            _assert_worker_readback(receipt, payload)
            try:
                content = base64.b64decode(
                    artifact["contentBase64"], validate=True
                )
            except (KeyError, TypeError, ValueError, binascii.Error) as error:
                raise ExportServiceError("authored_vnext_worker_artifact_invalid") from error
            artifact_digest = f"sha256:{artifact.get('sha256', '')}"
            if (
                artifact.get("byteLength") != len(content)
                or self.artifact_store.digest_bytes(content) != artifact_digest
            ):
                raise ExportServiceError("authored_vnext_worker_artifact_digest_mismatch")
            storage_key, byte_length = self.artifact_store.publish(
                content,
                artifact_digest,
                storage_namespace=claimed.id.hex,
            )
            reopened = self.artifact_store.open_verified(
                storage_key, artifact_digest, byte_length
            ).read_bytes()
            package = receipt.get("package")
            if not isinstance(package, dict) or not isinstance(
                package.get("packageDigest"), str
            ):
                raise ExportServiceError("authored_vnext_worker_read_back_invalid")
            package_digest = f"sha256:{package['packageDigest']}"
            self.repository.complete_export_job(
                claimed.id,
                fence_token=claimed.fence_token,
                artifact=ExportArtifactInput(
                    storage_key=storage_key,
                    artifact_digest=artifact_digest,
                    readback_digest=self.artifact_store.digest_bytes(reopened),
                    package_digest=package_digest,
                    size_bytes=byte_length,
                    master_digest=digests["master"],
                    spec_digest=digests["spec"],
                    asset_manifest_digest=digests["assetManifest"],
                    font_manifest_digest=digests["fontManifest"],
                ),
            )
        except (
            WorkerError,
            ExportServiceError,
            ArtifactStoreError,
            OperationFenceError,
            PublicationConflictError,
            SourceDigestMismatchError,
        ) as error:
            code = getattr(error, "code", str(error))
            try:
                self.repository.fail_export_job(
                    claimed.id,
                    fence_token=claimed.fence_token,
                    failure_code=code,
                    failure_detail=None,
                    cancelled=code in {"authored_vnext_worker_cancelled"},
                )
            except Exception:
                pass
        except Exception:
            # Never strand a claimed durable job in ``running`` because an
            # unexpected adapter/runtime error escaped the known taxonomy.
            try:
                self.repository.fail_export_job(
                    claimed.id,
                    fence_token=claimed.fence_token,
                    failure_code="authored_vnext_export_failed",
                    failure_detail=None,
                )
            except Exception:
                pass
        return self.repository.get_export_job(claimed.id)
