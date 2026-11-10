"""Durable storage for generation quality and timing receipts.

The receipt root, the per-job manifest path and its cross-process lock, the
manifest digest and the public attempt identifier. Moved unchanged from
``authored_quality_receipt_v2`` so the generation timing receipts every driver
writes do not import the authored publication gate;
``authored_quality_receipt_v2`` re-exports every name.
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path

from constants.timeouts import BUSY_WAIT_POLL_INTERVAL_SECONDS
from utils.env_settings.authored_runtime import (
    get_authored_quality_receipt_directory,
)
from utils.hashing import canonical_json_digest, sha256_hex


def quality_receipt_root() -> Path:
    """Return the durable root for QualityReceipt v2 manifests.

    Receipt manifests intentionally live outside the transient checkpoint
    directory: published checkpoint cleanup must not erase the idempotency
    record that protects a retry after a worker restart.  Deployments may
    provide ``AUTHORED_QUALITY_RECEIPT_DIRECTORY``; otherwise use the same
    app-data fallback as authored checkpoints.
    """

    configured = get_authored_quality_receipt_directory()
    if configured:
        root = Path(configured)
    else:
        # Keep this import lazy so the receipt contract remains usable by small
        # unit-test/CLI consumers that do not initialise the full settings
        # registry at import time.
        from utils.get_env import get_app_data_directory_env

        app_data = (get_app_data_directory_env() or "").strip()
        root = Path(app_data or os.path.join(tempfile.gettempdir(), "presenton")) / "authored-quality-receipts"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def quality_receipt_manifest_path(job_id: object) -> Path:
    """Build a stable, path-safe manifest location for one generation job.

    The job identifier is hashed before it reaches the filesystem.  The
    manifest itself stores only public attempt digests, never a worker lease
    token or other private execution credential.
    """

    value = str(job_id or "").strip()
    if not value:
        raise ValueError("quality_receipt_job_id_missing")
    job_digest = sha256_hex(value.encode("utf-8"))[:32]
    return quality_receipt_root() / f"job-{job_digest}.json"


def public_attempt_id(value: object) -> str:
    """Return the opaque attempt identifier used on all public contracts.

    Worker lease tokens and generation attempt UUIDs are intentionally never
    emitted in receipts, themes, or sidecars.  Values already carrying the
    canonical digest form are preserved; every other value is reduced to a
    stable 128-bit hexadecimal digest suitable for filenames and wire payloads.
    """

    text = str(value or "").strip()
    if not text:
        raise ValueError("attempt_id must not be empty")
    candidate = text[7:] if text.startswith("sha256:") else text
    if len(candidate) == 64 and all(char in "0123456789abcdefABCDEF" for char in candidate):
        return "sha256:" + candidate.lower()
    if len(candidate) == 32 and all(char in "0123456789abcdefABCDEF" for char in candidate):
        return candidate.lower()
    return sha256_hex(text.encode("utf-8"))[:32]


def _optional_prefixed_sha256(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if (
        len(normalized) != 71
        or not normalized.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in normalized[7:])
    ):
        raise ValueError(f"quality_receipt_{field_name}_invalid")
    return normalized


def _manifest_digest(records: Mapping[str, object]) -> str:
    """Return the digest for the canonical manifest envelope.

    ``manifestDigest`` is calculated without itself in the payload so a
    partially written or hand-edited records map cannot be accepted after a
    worker restart.  Individual receipt digests remain the second integrity
    boundary for each record.
    """

    canonical = {
        "schemaVersion": "quality-receipt-manifest/v1",
        "receipts": records,
    }
    return canonical_json_digest(canonical, allow_nan=True)


@contextmanager
def _manifest_lock(path: Path, *, timeout_seconds: float = 5.0):
    """Serialize read/modify/write cycles across worker processes.

    A lock file is deliberately separate from the manifest so replacing the
    JSON file remains atomic.  The file is retained after release; only the
    OS advisory lock is transient and therefore a crashed worker cannot leave
    a permanent stale lock artifact.
    """

    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with lock_path.open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        locked = False
        try:
            if os.name == "nt":
                import msvcrt

                while not locked:
                    try:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        locked = True
                    except OSError as busy:
                        if time.monotonic() - started >= timeout_seconds:
                            raise ValueError(
                                "quality_receipt_manifest_lock_timeout"
                            ) from busy
                        time.sleep(BUSY_WAIT_POLL_INTERVAL_SECONDS)
            else:
                import fcntl

                while not locked:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        locked = True
                    except OSError as busy:
                        if time.monotonic() - started >= timeout_seconds:
                            raise ValueError(
                                "quality_receipt_manifest_lock_timeout"
                            ) from busy
                        time.sleep(BUSY_WAIT_POLL_INTERVAL_SECONDS)
            yield
        finally:
            if locked:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
