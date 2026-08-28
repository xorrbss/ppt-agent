"""Atomic, attempt-scoped evidence checkpoints for bounded slide repair."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Iterator, Mapping

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SCHEMA = "authored-slide-checkpoint/v1"


class SlideCheckpointError(RuntimeError):
    """Base error for durable slide checkpoint operations."""


class SlideCheckpointConflict(SlideCheckpointError):
    """The expected revision or supplied digest does not match."""


class SlideCheckpointCorrupt(SlideCheckpointError):
    """A checkpoint exists but cannot be trusted; never fall back silently."""


@dataclass(frozen=True)
class SlideCheckpointBundle:
    attempt_id: str
    slide_index: int
    revision: int
    html: str
    png: bytes
    metrics: dict[str, Any]
    digest: str

    @property
    def checkpoint_revision(self) -> int:
        return self.revision

    def to_payload(self) -> dict[str, Any]:
        """Return a JSON-safe, self-contained durable payload."""
        return {
            "schema": _SCHEMA,
            "attemptId": self.attempt_id,
            "slideIndex": self.slide_index,
            "revision": self.revision,
            "html": self.html,
            "pngBase64": base64.b64encode(self.png).decode("ascii"),
            "metrics": self.metrics,
            "digest": self.digest,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "SlideCheckpointBundle":
        """Validate and decode a JSON-safe sidecar payload."""
        try:
            if payload.get("schema") != _SCHEMA:
                raise ValueError("checkpoint_schema_invalid")
            attempt_id = payload["attemptId"]
            slide_index = payload["slideIndex"]
            revision = payload["revision"]
            html = payload["html"]
            metrics = payload["metrics"]
            if (
                not isinstance(attempt_id, str)
                or type(slide_index) is not int or slide_index < 0
                or type(revision) is not int or revision < 0
                or not isinstance(html, str) or not isinstance(metrics, dict)
            ):
                raise ValueError("checkpoint_payload_invalid")
            png = base64.b64decode(payload["pngBase64"], validate=True)
            digest = cls._digest_for_payload(html, png, metrics)
            if digest != payload["digest"]:
                raise ValueError("checkpoint_digest_invalid")
            return cls(attempt_id, slide_index, revision, html, png, metrics, digest)
        except (KeyError, TypeError, ValueError) as exc:
            raise SlideCheckpointCorrupt("checkpoint_payload_invalid") from exc

    @staticmethod
    def _digest_for_payload(html: str, png: bytes, metrics: Mapping[str, Any]) -> str:
        metrics_bytes = json.dumps(
            dict(metrics), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        digest = hashlib.sha256()
        for content in (html.encode("utf-8"), png, metrics_bytes):
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
        return digest.hexdigest()


class AtomicSlideCheckpointStore:
    """Store one complete bundle per atomic file replacement.

    Revision files are retained without a cache limit for the lifetime of the
    owning attempt. ``current.json`` contains the complete committed bundle,
    not references to independently mutable HTML, PNG or metrics files. A
    writer lock fences revision checks across threads and processes. The
    existing job/attempt lease still decides which attempt may publish a deck.
    """

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def commit(
        self,
        *,
        attempt_id: str,
        slide_index: int,
        revision: int,
        html: str,
        png: bytes,
        metrics: Mapping[str, Any],
        expected_revision: int | None = None,
        digest: str | None = None,
    ) -> SlideCheckpointBundle:
        """Commit one slide, retaining complete previous revisions.

        Identical repeats are idempotent. ``expected_revision`` fences a
        repair against the revision it read; stale repair commits fail.
        """
        self._validate_identity(attempt_id, slide_index, revision)
        if expected_revision is not None:
            self._validate_identity(attempt_id, slide_index, expected_revision)
        if not isinstance(html, str) or not isinstance(png, bytes):
            raise TypeError("checkpoint_requires_html_string_and_png_bytes")
        if not isinstance(metrics, Mapping):
            raise TypeError("checkpoint_metrics_must_be_mapping")
        metrics_bytes = self._json(dict(metrics))
        calculated = self._digest(html.encode("utf-8"), png, metrics_bytes)
        if digest is not None and digest != calculated:
            raise SlideCheckpointConflict("checkpoint_digest_mismatch")
        bundle = SlideCheckpointBundle(
            attempt_id, slide_index, revision, html, png,
            json.loads(metrics_bytes), calculated,
        )
        content = self._json(bundle.to_payload())
        slide_dir = self._slide_dir(attempt_id, slide_index, create=True)
        with self._writer_lock(slide_dir):
            # A previous process may have died between writing the prepared
            # record and publishing ``current.json``. Reconcile only complete
            # revision files; never overwrite an existing pointer here.
            self._reconcile(slide_dir, attempt_id, slide_index)
            current = self._read(slide_dir / "current.json", attempt_id, slide_index)
            current_revision = current.revision if current else None
            if current_revision == revision:
                if current.digest != calculated:
                    raise SlideCheckpointConflict("checkpoint_revision_reused")
                return current
            if expected_revision is not None and current_revision != expected_revision:
                raise SlideCheckpointConflict("checkpoint_revision_conflict")
            if current_revision is not None and revision <= current_revision:
                raise SlideCheckpointConflict("checkpoint_revision_regressed")
            revision_path = slide_dir / f"revision-{revision}.json"
            existing = self._read(revision_path, attempt_id, slide_index)
            if existing is not None and existing.digest != calculated:
                raise SlideCheckpointConflict("checkpoint_revision_reused")
            if existing is None:
                prepared = slide_dir / f".prepared-r{revision}-{calculated[:16]}.json"
                self._atomic_file(
                    prepared,
                    self._json({"revision": revision, "digest": calculated}),
                )
                self._atomic_file(revision_path, content)
            # Publish the commit journal before advancing the mutable pointer.
            # If the process dies after this marker is fsynced but before the
            # pointer replace, startup reconciliation promotes the verified
            # immutable revision.  A pointer write without a marker is never
            # needed to establish commit ownership.
            marker = slide_dir / f".committed-r{revision}-{calculated[:16]}.json"
            self._atomic_file(
                marker,
                self._json({"revision": revision, "digest": calculated}),
            )
            self._atomic_file(slide_dir / "current.json", content)
            prepared = slide_dir / f".prepared-r{revision}-{calculated[:16]}.json"
            try:
                prepared.unlink()
            except FileNotFoundError:
                pass
            self._fsync_directory(slide_dir)
        return bundle

    def commit_repair(
        self,
        *,
        attempt_id: str,
        slide_index: int,
        base_revision: int,
        html: str,
        png: bytes,
        metrics: Mapping[str, Any],
    ) -> SlideCheckpointBundle:
        """Advance exactly one slide by one revision; no deck-wide operation."""
        self._validate_identity(attempt_id, slide_index, base_revision)
        return self.commit(
            attempt_id=attempt_id, slide_index=slide_index,
            revision=base_revision + 1, expected_revision=base_revision,
            html=html, png=png, metrics=metrics,
        )

    def recover(
        self, *, attempt_id: str, slide_index: int, revision: int | None = None,
    ) -> SlideCheckpointBundle:
        """Recover a committed bundle, rejecting corruption without fallback."""
        self._validate_identity(attempt_id, slide_index, 0 if revision is None else revision)
        slide_dir = self._slide_dir(attempt_id, slide_index)
        # Explicit historical recovery must remain available even when the
        # current pointer is corrupt; only current recovery performs startup
        # reconciliation and fail-closed validation.
        if revision is None:
            self._reconcile(slide_dir, attempt_id, slide_index)
        name = "current.json" if revision is None else f"revision-{revision}.json"
        bundle = self._read(slide_dir / name, attempt_id, slide_index)
        if bundle is None:
            raise FileNotFoundError("slide_checkpoint_not_found")
        if revision is not None and bundle.revision != revision:
            raise SlideCheckpointCorrupt("checkpoint_revision_mismatch")
        return bundle

    def latest_revision(self, *, attempt_id: str, slide_index: int) -> int | None:
        self._validate_identity(attempt_id, slide_index, 0)
        slide_dir = self._slide_dir(attempt_id, slide_index)
        self._reconcile(slide_dir, attempt_id, slide_index)
        bundle = self._read(
            slide_dir / "current.json",
            attempt_id, slide_index,
        )
        return bundle.revision if bundle else None

    def sweep_orphans(
        self, *, max_age_seconds: float = 24 * 60 * 60, max_files: int = 256
    ) -> int:
        """Remove stale transactional markers and temp files.

        Only files created by this store are considered. Immutable
        ``revision-*.json`` and ``current.json`` payloads are never removed;
        callers can apply attempt retention separately after receipt sealing.
        """
        if max_age_seconds < 0 or max_files <= 0:
            raise ValueError("invalid_orphan_sweep_limits")
        now = time.time()
        removed = 0
        for path in self.root.rglob(".prepared-r*.json"):
            if removed >= max_files:
                break
            try:
                if now - path.stat().st_mtime >= max_age_seconds:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        for path in self.root.rglob(".committed-r*.json"):
            if removed >= max_files:
                break
            try:
                # A committed marker is retained while its revision/current
                # payload exists. Remove only malformed or detached markers.
                payload = json.loads(path.read_text("utf-8"))
                revision = payload["revision"]
                digest = payload["digest"]
                revision_path = path.parent / f"revision-{revision}.json"
                if revision_path.exists():
                    continue
                if now - path.stat().st_mtime >= max_age_seconds and isinstance(digest, str):
                    path.unlink()
                    removed += 1
            except (OSError, ValueError, KeyError, TypeError):
                try:
                    if now - path.stat().st_mtime >= max_age_seconds:
                        path.unlink()
                        removed += 1
                except OSError:
                    continue
        # ``_atomic_file`` can leave a uniquely named temporary file if the
        # process dies after fsync and before ``os.replace``.  These files are
        # deliberately distinguishable by the double-dot prefix; sweep only
        # stale files created for known checkpoint payloads and never touch
        # immutable revisions or writer locks.
        temp_pattern = re.compile(
            r"^\.\.(?:current\.json|revision-[0-9]+\.json|prepared-r|committed-r)"
        )
        for path in self.root.rglob("*"):
            if removed >= max_files:
                break
            try:
                if (
                    not path.is_file()
                    or path.is_symlink()
                    or not temp_pattern.match(path.name)
                    or now - path.stat().st_mtime < max_age_seconds
                ):
                    continue
                path.unlink()
                removed += 1
            except OSError:
                continue
        return removed

    def _slide_dir(self, attempt_id: str, slide_index: int, *, create: bool = False) -> Path:
        path = self.root / attempt_id / f"slide-{slide_index}"
        # Attempt IDs and slide indices cannot contain separators. Refuse a
        # symlinked attempt or slide rather than escaping the private root.
        if path.parent.is_symlink() or path.is_symlink():
            raise ValueError("invalid_checkpoint_path")
        if create:
            path.mkdir(parents=True, exist_ok=True)
        if self.root not in path.resolve().parents:
            raise ValueError("invalid_checkpoint_path")
        return path

    @classmethod
    def _reconcile(cls, slide_dir: Path, attempt_id: str, slide_index: int) -> None:
        """Recover a pointer after a process crash using prepared markers.

        A committed marker is accepted only when both the marker digest and
        the immutable revision payload digest agree. If ``current.json`` is
        present but corrupt, reconciliation deliberately leaves it untouched;
        callers then fail closed instead of silently falling back.
        """
        if not slide_dir.is_dir():
            return
        current_path = slide_dir / "current.json"
        if current_path.is_symlink():
            raise SlideCheckpointCorrupt("invalid_checkpoint_path")
        current: SlideCheckpointBundle | None = None
        if current_path.exists():
            current = cls._read(current_path, attempt_id, slide_index)
        # A crash can leave more than one valid committed marker when a
        # repair advanced the revision after writing its immutable bundle.
        # Reconcile the highest valid marker so restart never revives an
        # older revision merely because it was enumerated first.
        newest_committed: SlideCheckpointBundle | None = None
        for marker in sorted(slide_dir.glob(".committed-r*.json")):
            try:
                marker_payload = json.loads(marker.read_text("utf-8"))
                revision = marker_payload["revision"]
                digest = marker_payload["digest"]
                if type(revision) is not int or revision < 0 or not isinstance(digest, str):
                    raise ValueError
                revision_path = slide_dir / f"revision-{revision}.json"
                bundle = cls._read(revision_path, attempt_id, slide_index)
                if bundle is None or bundle.digest != digest:
                    continue
                if newest_committed is None or bundle.revision > newest_committed.revision:
                    newest_committed = bundle
            except (OSError, ValueError, KeyError, SlideCheckpointCorrupt):
                # Invalid markers are orphan metadata and are safe to remove
                # only when they are not the sole surviving revision record.
                continue
        if newest_committed is not None and (
            current is None or newest_committed.revision > current.revision
        ):
            # The marker is the commit record; its immutable revision payload
            # is already digest verified above. Publishing this pointer is
            # therefore safe even when current.json was absent or stale.
            cls._atomic_file(current_path, cls._json(newest_committed.to_payload()))
            current = newest_committed
        # Prepared records are transactional intent. Once the immutable
        # revision exists they can be retired; otherwise they remain for the
        # bounded orphan sweeper to remove after its age threshold.
        for prepared in slide_dir.glob(".prepared-r*.json"):
            try:
                payload = json.loads(prepared.read_text("utf-8"))
                revision = payload["revision"]
                digest = payload["digest"]
                revision_path = slide_dir / f"revision-{revision}.json"
                bundle = cls._read(revision_path, attempt_id, slide_index)
                if bundle is not None and bundle.digest == digest:
                    prepared.unlink()
                    marker = slide_dir / f".committed-r{revision}-{digest[:16]}.json"
                    if not marker.exists():
                        cls._atomic_file(
                            marker,
                            cls._json({"revision": revision, "digest": digest}),
                        )
            except (OSError, ValueError, KeyError, TypeError, SlideCheckpointCorrupt):
                continue

    @staticmethod
    def _validate_identity(attempt_id: str, slide_index: int, revision: int) -> None:
        if not isinstance(attempt_id, str) or not _SAFE_COMPONENT.fullmatch(attempt_id):
            raise ValueError("invalid_attempt_id")
        if type(slide_index) is not int or slide_index < 0:
            raise ValueError("slide_index_must_be_nonnegative")
        if type(revision) is not int or revision < 0:
            raise ValueError("revision_must_be_nonnegative")

    @staticmethod
    def _json(value: Any) -> bytes:
        try:
            return json.dumps(
                value, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise TypeError("checkpoint_payload_must_be_json_safe") from exc

    @staticmethod
    def _digest(html: bytes, png: bytes, metrics: bytes) -> str:
        digest = hashlib.sha256()
        for content in (html, png, metrics):
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
        return digest.hexdigest()

    @classmethod
    def _read(cls, path: Path, attempt_id: str, slide_index: int) -> SlideCheckpointBundle | None:
        try:
            if path.is_symlink():
                raise SlideCheckpointCorrupt("invalid_checkpoint_path")
            payload = json.loads(path.read_text("utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise SlideCheckpointCorrupt("checkpoint_payload_unreadable") from exc
        try:
            if (
                payload["schema"] != _SCHEMA or payload["attemptId"] != attempt_id
                or payload["slideIndex"] != slide_index
                or type(payload["revision"]) is not int or payload["revision"] < 0
                or not isinstance(payload["html"], str)
                or not isinstance(payload["metrics"], dict)
            ):
                raise ValueError("checkpoint_identity_invalid")
            png = base64.b64decode(payload["pngBase64"], validate=True)
            calculated = cls._digest(
                payload["html"].encode("utf-8"), png, cls._json(payload["metrics"]),
            )
            if calculated != payload["digest"]:
                raise ValueError("checkpoint_digest_invalid")
            return SlideCheckpointBundle(
                attempt_id, slide_index, payload["revision"], payload["html"],
                png, payload["metrics"], calculated,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SlideCheckpointCorrupt("checkpoint_payload_invalid") from exc

    @staticmethod
    @contextmanager
    def _writer_lock(slide_dir: Path) -> Iterator[None]:
        # OS advisory locks are released on process exit, unlike sentinel files.
        with (slide_dir / ".writer.lock").open("a+b") as handle:
            if os.name == "nt":
                import msvcrt
                handle.seek(0, os.SEEK_END)
                if not handle.tell():
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _atomic_file(path: Path, content: bytes) -> None:
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt":
            return  # Windows does not expose directory fsync through os.open.
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
