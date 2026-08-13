"""Resumable, fenced OOXML package mutation operations."""

from __future__ import annotations

import copy
from pathlib import Path
import tempfile
from typing import Any
import uuid

from .adapter import OoxmlAdapterError
from .errors import (
    OoxmlPackageConflict,
    OoxmlPackageError,
    OoxmlPackageNotFound,
    OoxmlPackageQuarantined,
    SimulatedOoxmlWorkerCrash,
)
from .repository import _canonical_digest, _now


_MASTER_OPERATIONS = {
    "CREATE_MASTER",
    "UPDATE_MASTER",
    "DELETE_MASTER",
    "CREATE_LAYOUT",
    "UPDATE_LAYOUT",
    "DELETE_LAYOUT",
}


class OoxmlPackageOperationMixin:
    """CAS-checked operation queue and crash-resume publication workflow."""

    def _validate_master_transition(
        self,
        operation: str,
        active: dict[str, Any],
        command: dict[str, Any],
    ) -> None:
        current = active.get("masterContract")
        candidate = command.get("masterContract")
        if operation in _MASTER_OPERATIONS - {"DELETE_MASTER"} and not isinstance(
            candidate, dict
        ):
            raise OoxmlPackageError("authored_vnext_ooxml_master_contract_required")
        if operation == "CREATE_MASTER" and not active.get("masterDeleted"):
            raise OoxmlPackageConflict("authored_vnext_ooxml_master_already_exists")
        if operation == "UPDATE_MASTER" and active.get("masterDeleted"):
            raise OoxmlPackageConflict("authored_vnext_ooxml_master_missing")
        if operation == "DELETE_MASTER" and active.get("slides"):
            raise OoxmlPackageConflict("authored_vnext_ooxml_master_in_use")
        if operation not in {"CREATE_LAYOUT", "UPDATE_LAYOUT", "DELETE_LAYOUT"}:
            return
        if not isinstance(current, dict):
            raise OoxmlPackageConflict("authored_vnext_ooxml_master_missing")
        old_layouts = {item["id"]: item for item in current.get("layouts", [])}
        new_layouts = {item["id"]: item for item in candidate.get("layouts", [])}
        added = set(new_layouts) - set(old_layouts)
        removed = set(old_layouts) - set(new_layouts)
        changed = {
            key
            for key in set(old_layouts) & set(new_layouts)
            if old_layouts[key] != new_layouts[key]
        }
        valid = (
            operation == "CREATE_LAYOUT"
            and len(added) == 1
            and not removed
            and not changed
        ) or (
            operation == "UPDATE_LAYOUT"
            and not added
            and not removed
            and len(changed) == 1
        ) or (
            operation == "DELETE_LAYOUT"
            and not added
            and len(removed) == 1
            and not changed
        )
        if not valid:
            raise OoxmlPackageError("authored_vnext_ooxml_layout_delta_invalid")
        if operation == "DELETE_LAYOUT":
            deleted = next(iter(removed))
            if any(slide.get("layoutId") == deleted for slide in active.get("slides", [])):
                raise OoxmlPackageConflict("authored_vnext_ooxml_layout_in_use")

    def create_operation(
        self, package_id: str, command: dict[str, Any]
    ) -> dict[str, Any]:
        command = copy.deepcopy(command)
        operation = command["operation"]
        if operation in _MASTER_OPERATIONS - {"DELETE_MASTER"}:
            command["masterContract"] = self._normalize_master_payload(
                command.get("masterContract")
            )
        request_digest = _canonical_digest(command)
        slot = self._idempotency_slot(
            f"operation:{package_id}", command["idempotencyKey"]
        )
        with self._state(write=True) as state:
            self._require_account(state, command["expectedAccountGeneration"])
            replay = state["idempotency"].get(slot)
            if replay is not None:
                if replay["requestDigest"] != request_digest:
                    raise OoxmlPackageConflict(
                        "authored_vnext_ooxml_idempotency_conflict"
                    )
                receipt = state["operations"].get(replay["operationId"])
                if not isinstance(receipt, dict):
                    raise OoxmlPackageError("authored_vnext_ooxml_operation_state_corrupt")
                public = self._public_operation(receipt)
                public["replayed"] = True
                return public
            record = self._package(state, package_id)
            active = self._assert_cas(
                record,
                command["expectedRevision"],
                command["expectedArtifactDigest"],
            )
            if record["state"] == "quarantined" and operation != "ARCHIVE":
                raise OoxmlPackageQuarantined(
                    "authored_vnext_ooxml_quarantined_operation_forbidden"
                )
            if record["state"] == "archived" and operation != "ARCHIVE":
                raise OoxmlPackageConflict("authored_vnext_ooxml_package_archived")
            if operation == "PATCH_TEXT_SLICE":
                if record["mode"] != "PRESERVE_EXACT":
                    raise OoxmlPackageError(
                        "authored_vnext_ooxml_patch_requires_preserve_mode"
                    )
                required = ("partName", "selector", "expectedText", "replacementText")
                if any(key not in command for key in required):
                    raise OoxmlPackageError(
                        "authored_vnext_ooxml_text_patch_fields_required"
                    )
            if operation in _MASTER_OPERATIONS:
                if record["mode"] != "CORPORATE_NORMALIZE":
                    raise OoxmlPackageError(
                        "authored_vnext_ooxml_master_requires_normalize_mode"
                    )
                self._validate_master_transition(operation, active, command)
            now = _now()
            operation_id = f"operation:{uuid.uuid4().hex}"
            receipt = {
                "schemaVersion": "authored-vnext-ooxml-operation/v1",
                "operationId": operation_id,
                "packageId": package_id,
                "ownerId": self.owner_id,
                "operation": operation,
                "state": "queued",
                "attemptCount": 0,
                "fencingToken": 1,
                "resumable": True,
                "replayed": False,
                "expectedRevision": command["expectedRevision"],
                "expectedArtifactDigest": command["expectedArtifactDigest"],
                "expectedAccountGeneration": command["expectedAccountGeneration"],
                "resultRevision": None,
                "resultArtifactDigest": None,
                "changedParts": [],
                "preservedExactPartCount": 0,
                "silentLossCount": 0,
                "errorCode": None,
                "command": command,
                "createdAt": now,
                "updatedAt": now,
            }
            state["operations"][operation_id] = receipt
            state["idempotency"][slot] = {
                "requestDigest": request_digest,
                "operationId": operation_id,
            }
            return self._public_operation(receipt)

    @staticmethod
    def _public_operation(receipt: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "schemaVersion", "operationId", "packageId", "operation", "state",
            "attemptCount", "fencingToken", "resumable", "replayed",
            "expectedRevision", "expectedArtifactDigest", "resultRevision",
            "resultArtifactDigest", "changedParts", "preservedExactPartCount",
            "silentLossCount", "errorCode", "createdAt", "updatedAt",
        )
        return {key: copy.deepcopy(receipt[key]) for key in keys}

    def get_operation(self, operation_id: str) -> dict[str, Any]:
        with self._state(write=False) as state:
            receipt = state["operations"].get(operation_id)
            if not isinstance(receipt, dict) or receipt.get("ownerId") != self.owner_id:
                raise OoxmlPackageNotFound("authored_vnext_ooxml_operation_not_found")
            return self._public_operation(receipt)

    def _fail_operation(self, operation_id: str, fence: int, code: str) -> None:
        with self._state(write=True) as state:
            receipt = state["operations"].get(operation_id)
            if not isinstance(receipt, dict) or receipt.get("fencingToken") != fence:
                return
            receipt["state"] = "failed"
            receipt["resumable"] = code not in {
                "authored_vnext_ooxml_package_cas_conflict",
                "authored_vnext_ooxml_account_fence_stale",
            }
            receipt["errorCode"] = code
            receipt["updatedAt"] = _now()

    def _build_candidate(
        self,
        record: dict[str, Any],
        active: dict[str, Any],
        command: dict[str, Any],
        candidate: Path,
    ) -> dict[str, Any] | None:
        operation = command["operation"]
        if operation == "ARCHIVE":
            candidate.write_bytes(self._artifact_path(active["artifactDigest"]).read_bytes())
            return None
        if record["mode"] == "PRESERVE_EXACT":
            patch = None
            if operation == "PATCH_TEXT_SLICE":
                patch = {
                    "partName": command["partName"],
                    "selector": command["selector"],
                    "expectedText": command["expectedText"],
                    "replacementText": command["replacementText"],
                }
            return self.adapter.preserve_save(
                self._artifact_path(active["artifactDigest"]), candidate, patch=patch
            )
        master_contract = active.get("masterContract")
        if operation in _MASTER_OPERATIONS - {"DELETE_MASTER"}:
            master_contract = command["masterContract"]
        if not isinstance(master_contract, dict):
            raise OoxmlPackageError("authored_vnext_ooxml_master_contract_required")
        return self.adapter.generate_save(
            candidate,
            master_contract=master_contract,
            slides=command.get("slides", active.get("slides", [])),
            delete_master=operation == "DELETE_MASTER",
            assets=self._worker_assets_for_master(master_contract),
        )

    def run_operation(
        self,
        operation_id: str,
        *,
        simulate_crash_after_candidate: bool = False,
    ) -> dict[str, Any]:
        with self._state(write=True) as state:
            receipt = state["operations"].get(operation_id)
            if not isinstance(receipt, dict) or receipt.get("ownerId") != self.owner_id:
                raise OoxmlPackageNotFound("authored_vnext_ooxml_operation_not_found")
            if receipt["state"] == "succeeded":
                return self._public_operation(receipt)
            if receipt["state"] == "cancelled":
                raise OoxmlPackageConflict("authored_vnext_ooxml_operation_cancelled")
            self._require_account(state, receipt["expectedAccountGeneration"])
            record = self._package(state, receipt["packageId"])
            active = self._assert_cas(
                record, receipt["expectedRevision"], receipt["expectedArtifactDigest"]
            )
            receipt["state"] = "running"
            receipt["attemptCount"] += 1
            if receipt["attemptCount"] > 1:
                receipt["fencingToken"] += 1
            receipt["errorCode"] = None
            receipt["updatedAt"] = _now()
            fence = receipt["fencingToken"]
            expected_generation = receipt["expectedAccountGeneration"]
            record_snapshot = copy.deepcopy(record)
            active_snapshot = copy.deepcopy(active)
            command = copy.deepcopy(receipt["command"])

        with tempfile.TemporaryDirectory(
            prefix="operation-", dir=self.staging_root
        ) as temporary_directory:
            candidate = Path(temporary_directory) / "candidate.pptx"
            try:
                result = self._build_candidate(
                    record_snapshot, active_snapshot, command, candidate
                )
                candidate_bytes = candidate.read_bytes()
            except OoxmlAdapterError as error:
                code = f"authored_vnext_ooxml_{error.code.lower()}"
                self._fail_operation(operation_id, fence, code)
                raise OoxmlPackageError(code, str(error)) from error
            except OoxmlPackageError as error:
                self._fail_operation(operation_id, fence, error.code)
                raise
        artifact_digest, _ = self._store_artifact(candidate_bytes)
        if simulate_crash_after_candidate:
            raise SimulatedOoxmlWorkerCrash(
                "candidate persisted before metadata publication"
            )
        return self._publish_operation(
            operation_id=operation_id,
            fence=fence,
            expected_generation=expected_generation,
            command=command,
            active_snapshot=active_snapshot,
            candidate_bytes=candidate_bytes,
            artifact_digest=artifact_digest,
            result=result,
        )

    def _publish_operation(
        self,
        *,
        operation_id: str,
        fence: int,
        expected_generation: int,
        command: dict[str, Any],
        active_snapshot: dict[str, Any],
        candidate_bytes: bytes,
        artifact_digest: str,
        result: dict[str, Any] | None,
    ) -> dict[str, Any]:
        operation = command["operation"]
        with self._state(write=True) as state:
            self._require_account(state, expected_generation)
            live = state["operations"].get(operation_id)
            if (
                not isinstance(live, dict)
                or live["state"] != "running"
                or live["fencingToken"] != fence
            ):
                raise OoxmlPackageConflict("authored_vnext_ooxml_operation_fence_stale")
            record = self._package(state, live["packageId"])
            self._assert_cas(
                record, live["expectedRevision"], live["expectedArtifactDigest"]
            )
            revision = record["activeRevision"] + 1
            if operation == "ARCHIVE":
                version = copy.deepcopy(active_snapshot)
                changed_parts: list[str] = []
                operation_changed_parts: list[str] = []
                record["state"] = "archived"
            else:
                if result is None:
                    raise OoxmlPackageError("authored_vnext_ooxml_adapter_receipt_missing")
                preservation = result["readBack"]["preservation"]
                silent_loss = int(preservation["silentLossCount"])
                if silent_loss != 0:
                    raise OoxmlPackageError("authored_vnext_ooxml_silent_loss_detected")
                part_digests = self._part_digests_from_preservation(preservation)
                changed_parts = self._changed_parts(
                    record["sourcePartDigests"], part_digests
                )
                operation_changed_parts = self._changed_parts(
                    active_snapshot["partDigests"], part_digests
                )
                if record["mode"] == "PRESERVE_EXACT":
                    allowed = [command["partName"]] if operation == "PATCH_TEXT_SLICE" else []
                    if any(name not in allowed for name in operation_changed_parts):
                        raise OoxmlPackageError(
                            "authored_vnext_ooxml_untouched_part_changed"
                        )
                master_contract = active_snapshot.get("masterContract")
                master_deleted = active_snapshot.get("masterDeleted", False)
                if operation in _MASTER_OPERATIONS - {"DELETE_MASTER"}:
                    master_contract = copy.deepcopy(command["masterContract"])
                    master_deleted = False
                elif operation == "DELETE_MASTER":
                    master_deleted = True
                version = {
                    "artifactDigest": artifact_digest,
                    "archiveByteLength": len(candidate_bytes),
                    "inspection": self._normalize_inspection(result["readBack"]["package"]),
                    "partDigests": part_digests,
                    "changedParts": changed_parts,
                    "preservedExactPartCount": int(preservation["preservedExactPartCount"]),
                    "patchSlicePartCount": int(preservation["patchSlicePartCount"]),
                    "generatedPartCount": int(preservation["generatedPartCount"]),
                    "silentLossCount": silent_loss,
                    "masterContract": master_contract,
                    "slides": copy.deepcopy(
                        command.get("slides", active_snapshot.get("slides", []))
                    ),
                    "masterDeleted": master_deleted,
                }
            version["revision"] = revision
            version["createdAt"] = _now()
            record["versions"][str(revision)] = version
            record["activeRevision"] = revision
            live.update(
                state="succeeded",
                resumable=False,
                resultRevision=revision,
                resultArtifactDigest=artifact_digest,
                changedParts=operation_changed_parts,
                preservedExactPartCount=version["preservedExactPartCount"],
                silentLossCount=version["silentLossCount"],
                errorCode=None,
                updatedAt=_now(),
            )
            return self._public_operation(live)

    def resume_operation(self, operation_id: str) -> dict[str, Any]:
        with self._state(write=False) as state:
            receipt = state["operations"].get(operation_id)
            if not isinstance(receipt, dict) or receipt.get("ownerId") != self.owner_id:
                raise OoxmlPackageNotFound("authored_vnext_ooxml_operation_not_found")
            if receipt["state"] == "failed" and not receipt["resumable"]:
                raise OoxmlPackageConflict(
                    "authored_vnext_ooxml_operation_not_resumable"
                )
            if receipt["state"] not in {"running", "failed", "queued", "succeeded"}:
                raise OoxmlPackageConflict(
                    "authored_vnext_ooxml_operation_not_resumable"
                )
        return self.run_operation(operation_id)


__all__ = ["OoxmlPackageOperationMixin"]
