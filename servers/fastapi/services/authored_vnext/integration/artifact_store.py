"""Content-addressed, read-back verified storage for authored vNext exports."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
from uuid import uuid4


_SHA256 = re.compile(r"^sha256:([0-9a-f]{64})$")


class ArtifactStoreError(RuntimeError):
    pass


class BufferedArtifactStore:
    """Publish immutable buffered artifacts without exposing partial files."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def digest_bytes(value: bytes) -> str:
        return f"sha256:{hashlib.sha256(value).hexdigest()}"

    def _path(self, digest: str) -> Path:
        match = _SHA256.fullmatch(digest)
        if match is None:
            raise ArtifactStoreError("authored_vnext_artifact_digest_invalid")
        hexadecimal = match.group(1)
        return self.root / hexadecimal[:2] / f"{hexadecimal}.pptx"

    def publish(self, value: bytes, expected_digest: str) -> tuple[str, int]:
        if not isinstance(value, bytes) or not value:
            raise ArtifactStoreError("authored_vnext_artifact_must_be_buffered")
        actual_digest = self.digest_bytes(value)
        if actual_digest != expected_digest:
            raise ArtifactStoreError("authored_vnext_artifact_digest_mismatch")
        destination = self._path(actual_digest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            reopened = destination.read_bytes()
            if reopened != value:
                raise ArtifactStoreError("authored_vnext_artifact_collision")
            return str(destination), len(reopened)

        temporary = destination.parent / f".{destination.name}.{uuid4().hex}.candidate"
        try:
            with temporary.open("xb") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            reopened = temporary.read_bytes()
            if len(reopened) != len(value) or self.digest_bytes(reopened) != actual_digest:
                raise ArtifactStoreError("authored_vnext_artifact_read_back_mismatch")
            try:
                os.link(temporary, destination)
            except FileExistsError:
                existing = destination.read_bytes()
                if existing != value:
                    raise ArtifactStoreError("authored_vnext_artifact_collision") from None
            except OSError:
                # Windows may reject hard links on some volumes. Exclusive
                # creation still preserves immutable, no-overwrite semantics.
                try:
                    with destination.open("xb") as handle:
                        handle.write(reopened)
                        handle.flush()
                        os.fsync(handle.fileno())
                except FileExistsError:
                    existing = destination.read_bytes()
                    if existing != value:
                        raise ArtifactStoreError("authored_vnext_artifact_collision") from None
            published = destination.read_bytes()
            if len(published) != len(value) or self.digest_bytes(published) != actual_digest:
                raise ArtifactStoreError("authored_vnext_artifact_published_read_back_mismatch")
            return str(destination), len(published)
        finally:
            temporary.unlink(missing_ok=True)

    def open_verified(self, storage_key: str, digest: str, byte_length: int) -> Path:
        expected = self._path(digest)
        supplied = Path(storage_key).resolve()
        if supplied != expected.resolve() or not supplied.is_file():
            raise ArtifactStoreError("authored_vnext_artifact_storage_key_invalid")
        value = supplied.read_bytes()
        if len(value) != byte_length or self.digest_bytes(value) != digest:
            raise ArtifactStoreError("authored_vnext_artifact_read_back_mismatch")
        return supplied
