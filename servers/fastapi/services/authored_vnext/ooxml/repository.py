"""Durable owner-scoped OOXML state and artifact repository."""

from __future__ import annotations

from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Iterator

from .adapter import NodeOoxmlProductAdapter
from .errors import (
    OoxmlAccountFenceError,
    OoxmlPackageConflict,
    OoxmlPackageError,
    OoxmlPackageNotFound,
)

MAX_UPLOAD_BYTES = 256 * 1024 * 1024
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _prefixed_digest(value: object) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise OoxmlPackageError("authored_vnext_ooxml_invalid_adapter_digest")
    return "sha256:" + value.lower()


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _process_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


class OoxmlPackageRepository:
    """Owner-isolated metadata and content-addressed immutable PPTX storage."""

    def __init__(
        self,
        root: Path,
        *,
        owner_id: str,
        adapter: NodeOoxmlProductAdapter | None = None,
    ) -> None:
        if not owner_id or len(owner_id) > 200:
            raise ValueError("owner_id is required")
        self.owner_id = owner_id
        self.app_data_root = root.resolve()
        owner_hash = hashlib.sha256(owner_id.encode("utf-8")).hexdigest()
        self.root = self.app_data_root / "authored-vnext" / "ooxml-packages-v1"
        self.owner_root = self.root / "owners" / owner_hash
        self.artifact_root = self.owner_root / "artifacts"
        self.staging_root = self.owner_root / "staging"
        self.state_path = self.owner_root / "state.json"
        self.lock_path = self.owner_root / ".state.lock"
        self.adapter = adapter or NodeOoxmlProductAdapter()
        self.owner_root.mkdir(parents=True, exist_ok=True)
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.staging_root.mkdir(parents=True, exist_ok=True)
        self._lock = _thread_lock(self.lock_path)

    def _empty_state(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "ownerId": self.owner_id,
            "accountGeneration": 0,
            "deleting": False,
            "packages": {},
            "operations": {},
            "idempotency": {},
        }

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return self._empty_state()
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise OoxmlPackageError(
                "authored_vnext_ooxml_state_corrupt", str(error)
            ) from error
        if (
            not isinstance(state, dict)
            or state.get("schemaVersion") != 1
            or state.get("ownerId") != self.owner_id
        ):
            raise OoxmlPackageError("authored_vnext_ooxml_owner_state_mismatch")
        return state

    def _save_state(self, state: dict[str, Any]) -> None:
        payload = json.dumps(
            state, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".state.", suffix=".json", dir=self.owner_root
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.state_path)
            if os.name != "nt":
                directory = os.open(self.owner_root, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            temporary_path.unlink(missing_ok=True)

    @contextmanager
    def _state(self, *, write: bool) -> Iterator[dict[str, Any]]:
        with self._lock, _process_lock(self.lock_path):
            state = self._load_state()
            yield state
            if write:
                self._save_state(state)

    def _require_account(
        self, state: dict[str, Any], expected_generation: int
    ) -> None:
        if state["deleting"] or state["accountGeneration"] != expected_generation:
            raise OoxmlAccountFenceError(
                "authored_vnext_ooxml_account_fence_stale"
            )

    def _package(
        self, state: dict[str, Any], package_id: str
    ) -> dict[str, Any]:
        record = state["packages"].get(package_id)
        if not isinstance(record, dict) or record.get("ownerId") != self.owner_id:
            raise OoxmlPackageNotFound("authored_vnext_ooxml_package_not_found")
        return record

    @staticmethod
    def _active_version(record: dict[str, Any]) -> dict[str, Any]:
        revision = int(record["activeRevision"])
        return record["versions"][str(revision)]

    @staticmethod
    def _assert_cas(
        record: dict[str, Any], expected_revision: int, expected_digest: str
    ) -> dict[str, Any]:
        active = OoxmlPackageRepository._active_version(record)
        if (
            record["activeRevision"] != expected_revision
            or active["artifactDigest"] != expected_digest
        ):
            raise OoxmlPackageConflict("authored_vnext_ooxml_package_cas_conflict")
        return active

    def _artifact_path(self, digest: str) -> Path:
        if not digest.startswith("sha256:") or len(digest) != 71:
            raise OoxmlPackageError("authored_vnext_ooxml_invalid_artifact_digest")
        return self.artifact_root / f"{digest.removeprefix('sha256:')}.pptx"

    def _store_artifact(self, content: bytes) -> tuple[str, Path]:
        digest = _sha256_bytes(content)
        destination = self._artifact_path(digest)
        if destination.is_file():
            if _sha256_bytes(destination.read_bytes()) != digest:
                raise OoxmlPackageError("authored_vnext_ooxml_artifact_collision")
            return digest, destination
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".artifact.", suffix=".pptx", dir=self.artifact_root
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, destination)
        finally:
            temporary_path.unlink(missing_ok=True)
        return digest, destination

    @staticmethod
    def _normalize_inspection(value: dict[str, Any]) -> dict[str, Any]:
        return {
            "classification": value["classification"],
            "partCount": int(value["partCount"]),
            "slideCount": int(value["slideCount"]),
            "masterCount": int(value["masterCount"]),
            "layoutCount": int(value["layoutCount"]),
            "externalRelationshipCount": int(value["externalRelationshipCount"]),
            "externalRelationshipWarnings": list(value["externalRelationshipWarnings"]),
            "packageDigest": _prefixed_digest(value["packageDigest"]),
            "relationshipDigest": _prefixed_digest(value["relationshipDigest"]),
            "closureDigest": _prefixed_digest(value["closureDigest"]),
            "macroDetected": bool(value["macroDetected"]),
            "quarantineReasons": list(value["quarantineReasons"]),
        }

    @staticmethod
    def _part_digests_from_original(value: dict[str, Any]) -> dict[str, str]:
        return {
            item["name"]: _prefixed_digest(item["sha256"])
            for item in value["parts"]
        }

    @staticmethod
    def _part_digests_from_preservation(value: dict[str, Any]) -> dict[str, str]:
        return {
            item["name"]: _prefixed_digest(item["currentSha256"])
            for item in value["parts"]
            if item.get("currentSha256") is not None
        }

    @staticmethod
    def _changed_parts(
        source_parts: dict[str, str], current_parts: dict[str, str]
    ) -> list[str]:
        return sorted(
            name
            for name in set(source_parts) | set(current_parts)
            if source_parts.get(name) != current_parts.get(name)
        )

    @staticmethod
    def _public_version(record: dict[str, Any]) -> dict[str, Any]:
        active = OoxmlPackageRepository._active_version(record)
        return {
            "schemaVersion": "authored-vnext-ooxml-package/v1",
            "packageId": record["packageId"],
            "sourcePackageId": record["sourcePackageId"],
            "ownerId": record["ownerId"],
            "state": record["state"],
            "mode": record["mode"],
            "origin": record["origin"],
            "revision": active["revision"],
            "artifactDigest": active["artifactDigest"],
            "archiveByteLength": active["archiveByteLength"],
            "inspection": copy.deepcopy(active["inspection"]),
            "createdAt": active["createdAt"],
        }

    @staticmethod
    def _idempotency_slot(namespace: str, key: str) -> str:
        return hashlib.sha256(f"{namespace}\0{key}".encode()).hexdigest()

    def get_package(self, package_id: str) -> dict[str, Any]:
        with self._state(write=False) as state:
            return self._public_version(self._package(state, package_id))

    def read_back(self, package_id: str) -> dict[str, Any]:
        with self._state(write=False) as state:
            record = self._package(state, package_id)
            active = self._active_version(record)
            changed_parts = self._changed_parts(
                record["sourcePartDigests"], active["partDigests"]
            )
            untouched_verified = all(
                active["partDigests"].get(name) == digest
                for name, digest in record["sourcePartDigests"].items()
                if name not in changed_parts
            )
            return {
                "schemaVersion": "authored-vnext-ooxml-readback/v1",
                "package": self._public_version(record),
                "sourceArtifactDigest": record["sourceArtifactDigest"],
                "sourceArchiveRetained": True,
                "unsupportedPartsPassthrough": record["mode"] == "PRESERVE_EXACT",
                "untouchedPartsVerified": untouched_verified,
                "preservedExactPartCount": active["preservedExactPartCount"],
                "patchSlicePartCount": active["patchSlicePartCount"],
                "generatedPartCount": active["generatedPartCount"],
                "silentLossCount": active["silentLossCount"],
                "changedParts": changed_parts,
            }

    def open_artifact(
        self, package_id: str, *, revision: int | None = None
    ) -> tuple[Path, str]:
        with self._state(write=False) as state:
            record = self._package(state, package_id)
            selected = (
                self._active_version(record)
                if revision is None
                else record["versions"].get(str(revision))
            )
            if not isinstance(selected, dict):
                raise OoxmlPackageNotFound(
                    "authored_vnext_ooxml_revision_not_found"
                )
            digest = selected["artifactDigest"]
        path = self._artifact_path(digest)
        if not path.is_file() or _sha256_bytes(path.read_bytes()) != digest:
            raise OoxmlPackageError("authored_vnext_ooxml_artifact_invalid")
        return path, digest

    def fence_account_deletion(self, *, expected_generation: int) -> int:
        with self._state(write=True) as state:
            if state["deleting"]:
                if state["accountGeneration"] != expected_generation + 1:
                    raise OoxmlAccountFenceError(
                        "authored_vnext_ooxml_account_fence_stale"
                    )
                return 0
            self._require_account(state, expected_generation)
            state["deleting"] = True
            state["accountGeneration"] = expected_generation + 1
            cancelled = 0
            for receipt in state["operations"].values():
                if receipt["state"] in {"queued", "running", "failed"}:
                    receipt["state"] = "cancelled"
                    receipt["resumable"] = False
                    receipt["fencingToken"] += 1
                    receipt["errorCode"] = "authored_vnext_ooxml_account_deleting"
                    receipt["updatedAt"] = _now()
                    cancelled += 1
            return cancelled

__all__ = [
    "MAX_UPLOAD_BYTES",
    "OoxmlPackageRepository",
    "_canonical_digest",
    "_now",
    "_prefixed_digest",
    "_sha256_bytes",
]
