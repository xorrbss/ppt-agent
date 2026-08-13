"""Durable owner-scoped OOXML import and editable-fork facade."""

from __future__ import annotations

import copy
import base64
from pathlib import Path
import tempfile
from typing import Any
import uuid

from models.authored_vnext.master.contract import DeckMasterContract
from services.authored_vnext.integration.export_payload import worker_master_contract
from services.authored_vnext.master.assets import MasterAssetError, OwnerMasterAssetStore

from .adapter import OoxmlAdapterError
from .errors import (
    OoxmlPackageConflict,
    OoxmlPackageError,
    OoxmlPackageNotFound,
    OoxmlPackageQuarantined,
)
from .operations import OoxmlPackageOperationMixin
from .slide_extraction import extract_imported_slides, map_imported_slides
from .repository import (
    MAX_UPLOAD_BYTES,
    OoxmlPackageRepository,
    _canonical_digest,
    _now,
    _sha256_bytes,
)


class OoxmlPackageService(OoxmlPackageOperationMixin, OoxmlPackageRepository):
    """Public import, fork, and rollback workflows over durable storage."""

    @staticmethod
    def _normalize_master_payload(value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise OoxmlPackageError("authored_vnext_ooxml_master_contract_required")
        if "masterId" not in value or "canvas" not in value:
            return copy.deepcopy(value)
        try:
            contract = DeckMasterContract.model_validate(value)
            references = {
                asset.asset_id: asset.digest.removeprefix("sha256:")
                for asset in (contract.brand_assets.logo, contract.brand_assets.wordmark)
                if asset is not None
            }
            furniture_ids = {
                element.asset_id
                for element in (
                    contract.furniture.logo,
                    contract.furniture.header,
                    contract.furniture.footer,
                    contract.furniture.divider,
                    contract.furniture.page_number,
                )
                if element.asset_id is not None
            }
            if references.keys() != furniture_ids:
                raise ValueError("authored_vnext_master_asset_reference_mismatch")
            mapped = worker_master_contract(contract, references)
            for layout in mapped["layouts"]:
                title_limit = (
                    contract.fit_policy.long_title_max_lines
                    if layout["kind"] == "long-title"
                    else contract.fit_policy.standard_max_lines
                )
                for placeholder in layout["placeholders"]:
                    if placeholder["type"] == "title":
                        placeholder["textStyle"]["maxLines"] = min(
                            placeholder["textStyle"]["maxLines"], title_limit
                        )
            return mapped
        except Exception as error:
            raise OoxmlPackageError(
                "authored_vnext_ooxml_master_contract_invalid", str(error)
            ) from error

    def _worker_assets_for_master(
        self, master_contract: dict[str, Any]
    ) -> list[dict[str, str]]:
        references = {
            item["assetId"]: item["assetSha256"]
            for item in master_contract.get("furniture", [])
            if item.get("assetId") is not None
        }
        if not references:
            return []
        store = OwnerMasterAssetStore(
            self.app_data_root
            / "authored_vnext"
            / "master-assets"
            / "users"
            / self.owner_id
        )
        assets = []
        try:
            for asset_id, sha256 in sorted(references.items()):
                value = store.open_verified(asset_id, f"sha256:{sha256}")
                assets.append(
                    {
                        "assetId": asset_id,
                        "mediaType": "image/png",
                        "sha256": sha256,
                        "bytesBase64": base64.b64encode(value).decode("ascii"),
                    }
                )
        except MasterAssetError as error:
            raise OoxmlPackageError(error.code) from error
        return assets

    def import_package(
        self,
        content: bytes,
        *,
        idempotency_key: str,
        expected_account_generation: int = 0,
    ) -> dict[str, Any]:
        if not content or len(content) > MAX_UPLOAD_BYTES:
            raise OoxmlPackageError("authored_vnext_ooxml_upload_size_invalid")
        request_digest = _sha256_bytes(content)
        slot = self._idempotency_slot("import", idempotency_key)
        with self._state(write=False) as state:
            self._require_account(state, expected_account_generation)
            replay = state["idempotency"].get(slot)
            if replay is not None:
                if replay["requestDigest"] != request_digest:
                    raise OoxmlPackageConflict(
                        "authored_vnext_ooxml_idempotency_conflict"
                    )
                record = self._package(state, replay["packageId"])
                return self._import_receipt(record, replayed=True)

        with tempfile.TemporaryDirectory(
            prefix="import-", dir=self.staging_root
        ) as temporary_directory:
            source = Path(temporary_directory) / "source.pptx"
            source.write_bytes(content)
            try:
                adapter_receipt = self.adapter.inspect(source)
            except OoxmlAdapterError as error:
                raise OoxmlPackageError(
                    f"authored_vnext_ooxml_{error.code.lower()}", str(error)
                ) from error
        inspection = self._normalize_inspection(adapter_receipt["inspection"])
        part_digests = self._part_digests_from_original(
            adapter_receipt["originalPackage"]
        )
        artifact_digest, _ = self._store_artifact(content)
        if artifact_digest != request_digest:
            raise OoxmlPackageError("authored_vnext_ooxml_upload_digest_changed")
        now = _now()
        package_id = f"package:{uuid.uuid4().hex}"
        try:
            extracted_slides = extract_imported_slides(content)
        except ValueError as error:
            raise OoxmlPackageError(str(error)) from error
        version = {
            "revision": 1,
            "artifactDigest": artifact_digest,
            "archiveByteLength": len(content),
            "inspection": inspection,
            "partDigests": part_digests,
            "changedParts": [],
            "preservedExactPartCount": len(part_digests),
            "patchSlicePartCount": 0,
            "generatedPartCount": 0,
            "silentLossCount": 0,
            "masterContract": None,
            "slides": extracted_slides,
            "masterDeleted": False,
            "createdAt": now,
        }
        record = {
            "packageId": package_id,
            "sourcePackageId": None,
            "ownerId": self.owner_id,
            "state": (
                "quarantined"
                if inspection["classification"] == "QUARANTINED"
                else "imported"
            ),
            "mode": "PRESERVE_EXACT",
            "origin": "IMPORTED",
            "activeRevision": 1,
            "sourceArtifactDigest": artifact_digest,
            "sourcePartDigests": part_digests,
            "versions": {"1": version},
            "createdAt": now,
        }
        with self._state(write=True) as state:
            self._require_account(state, expected_account_generation)
            replay = state["idempotency"].get(slot)
            if replay is not None:
                if replay["requestDigest"] != request_digest:
                    raise OoxmlPackageConflict(
                        "authored_vnext_ooxml_idempotency_conflict"
                    )
                record = self._package(state, replay["packageId"])
                replayed = True
            else:
                state["packages"][package_id] = record
                state["idempotency"][slot] = {
                    "requestDigest": request_digest,
                    "packageId": package_id,
                }
                replayed = False
        return self._import_receipt(record, replayed=replayed)

    def _import_receipt(
        self, record: dict[str, Any], *, replayed: bool
    ) -> dict[str, Any]:
        return {
            "schemaVersion": "authored-vnext-ooxml-import/v1",
            "replayed": replayed,
            "editable": record["state"] == "editable",
            "sourceArchived": True,
            "package": self._public_version(record),
        }

    def fork_editable(
        self, package_id: str, command: dict[str, Any]
    ) -> dict[str, Any]:
        command = copy.deepcopy(command)
        mode = command["mode"]
        if mode == "CORPORATE_NORMALIZE":
            command["masterContract"] = self._normalize_master_payload(
                command.get("masterContract")
            )
        request_digest = _canonical_digest(command)
        slot = self._idempotency_slot(
            f"fork:{package_id}", command["idempotencyKey"]
        )
        with self._state(write=False) as state:
            self._require_account(state, command["expectedAccountGeneration"])
            replay = state["idempotency"].get(slot)
            if replay is not None:
                if replay["requestDigest"] != request_digest:
                    raise OoxmlPackageConflict(
                        "authored_vnext_ooxml_idempotency_conflict"
                    )
                return self._import_receipt(
                    self._package(state, replay["packageId"]), replayed=True
                )
            source_record = self._package(state, package_id)
            source_active = self._assert_cas(
                source_record,
                command["expectedRevision"],
                command["expectedArtifactDigest"],
            )
            if source_record["state"] == "quarantined":
                raise OoxmlPackageQuarantined(
                    "authored_vnext_ooxml_quarantined_fork_forbidden"
                )
            source_snapshot = copy.deepcopy(source_record)
            active_snapshot = copy.deepcopy(source_active)

        now = _now()
        if mode == "PRESERVE_EXACT":
            version = copy.deepcopy(active_snapshot)
            artifact_digest = active_snapshot["artifactDigest"]
            part_digests = copy.deepcopy(active_snapshot["partDigests"])
            origin = "IMPORTED"
        elif mode == "CORPORATE_NORMALIZE":
            if "slides" in command:
                slides = copy.deepcopy(command["slides"])
                if not slides:
                    raise OoxmlPackageError(
                        "authored_vnext_ooxml_normalize_slides_required"
                    )
            else:
                try:
                    slides = map_imported_slides(
                        copy.deepcopy(active_snapshot.get("slides", [])),
                        command["masterContract"],
                    )
                except ValueError as error:
                    raise OoxmlPackageError(str(error)) from error
            with tempfile.TemporaryDirectory(
                prefix="fork-", dir=self.staging_root
            ) as temporary_directory:
                candidate = Path(temporary_directory) / "candidate.pptx"
                try:
                    result = self.adapter.generate_save(
                        candidate,
                        master_contract=command["masterContract"],
                        slides=slides,
                        assets=self._worker_assets_for_master(
                            command["masterContract"]
                        ),
                    )
                except OoxmlAdapterError as error:
                    raise OoxmlPackageError(
                        f"authored_vnext_ooxml_{error.code.lower()}", str(error)
                    ) from error
                candidate_bytes = candidate.read_bytes()
            artifact_digest, _ = self._store_artifact(candidate_bytes)
            preservation = result["readBack"]["preservation"]
            if int(preservation["silentLossCount"]) != 0:
                raise OoxmlPackageError("authored_vnext_ooxml_silent_loss_detected")
            part_digests = self._part_digests_from_preservation(preservation)
            version = {
                "artifactDigest": artifact_digest,
                "archiveByteLength": len(candidate_bytes),
                "inspection": self._normalize_inspection(
                    result["readBack"]["package"]
                ),
                "partDigests": part_digests,
                "changedParts": self._changed_parts(
                    active_snapshot["partDigests"], part_digests
                ),
                "preservedExactPartCount": int(
                    preservation["preservedExactPartCount"]
                ),
                "patchSlicePartCount": int(preservation["patchSlicePartCount"]),
                "generatedPartCount": int(preservation["generatedPartCount"]),
                "silentLossCount": 0,
                "masterContract": copy.deepcopy(command["masterContract"]),
                "slides": slides,
                "masterDeleted": False,
            }
            origin = "GENERATED"
        else:
            raise OoxmlPackageError("authored_vnext_ooxml_fork_mode_invalid")

        fork_id = f"package:{uuid.uuid4().hex}"
        version["revision"] = 1
        version["createdAt"] = now
        record = {
            "packageId": fork_id,
            "sourcePackageId": package_id,
            "ownerId": self.owner_id,
            "state": "editable",
            "mode": mode,
            "origin": origin,
            "activeRevision": 1,
            "sourceArtifactDigest": active_snapshot["artifactDigest"],
            "sourcePartDigests": copy.deepcopy(active_snapshot["partDigests"]),
            "versions": {"1": version},
            "createdAt": now,
        }
        with self._state(write=True) as state:
            self._require_account(state, command["expectedAccountGeneration"])
            replay = state["idempotency"].get(slot)
            if replay is not None:
                if replay["requestDigest"] != request_digest:
                    raise OoxmlPackageConflict(
                        "authored_vnext_ooxml_idempotency_conflict"
                    )
                record = self._package(state, replay["packageId"])
                replayed = True
            else:
                live_source = self._package(state, package_id)
                self._assert_cas(
                    live_source,
                    command["expectedRevision"],
                    command["expectedArtifactDigest"],
                )
                if live_source["activeRevision"] != source_snapshot["activeRevision"]:
                    raise OoxmlPackageConflict(
                        "authored_vnext_ooxml_package_cas_conflict"
                    )
                state["packages"][fork_id] = record
                state["idempotency"][slot] = {
                    "requestDigest": request_digest,
                    "packageId": fork_id,
                }
                replayed = False
        return self._import_receipt(record, replayed=replayed)

    def rollback(
        self, package_id: str, command: dict[str, Any]
    ) -> dict[str, Any]:
        request_digest = _canonical_digest(command)
        slot = self._idempotency_slot(
            f"rollback:{package_id}", command["idempotencyKey"]
        )
        with self._state(write=True) as state:
            self._require_account(state, command["expectedAccountGeneration"])
            replay = state["idempotency"].get(slot)
            if replay is not None:
                if replay["requestDigest"] != request_digest:
                    raise OoxmlPackageConflict(
                        "authored_vnext_ooxml_idempotency_conflict"
                    )
                return self._public_version(self._package(state, package_id))
            record = self._package(state, package_id)
            self._assert_cas(
                record,
                command["expectedRevision"],
                command["expectedArtifactDigest"],
            )
            target = record["versions"].get(str(command["targetRevision"]))
            if not isinstance(target, dict):
                raise OoxmlPackageNotFound(
                    "authored_vnext_ooxml_revision_not_found"
                )
            artifact_path = self._artifact_path(target["artifactDigest"])
            if (
                not artifact_path.is_file()
                or _sha256_bytes(artifact_path.read_bytes())
                != target["artifactDigest"]
            ):
                raise OoxmlPackageError(
                    "authored_vnext_ooxml_rollback_artifact_invalid"
                )
            revision = record["activeRevision"] + 1
            version = copy.deepcopy(target)
            version["revision"] = revision
            version["createdAt"] = _now()
            record["versions"][str(revision)] = version
            record["activeRevision"] = revision
            record["state"] = "editable"
            state["idempotency"][slot] = {
                "requestDigest": request_digest,
                "packageId": package_id,
                "revision": revision,
            }
            return self._public_version(record)


__all__ = ["MAX_UPLOAD_BYTES", "OoxmlPackageService"]
