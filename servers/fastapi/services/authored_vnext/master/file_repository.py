"""File-backed immutable authored vNext master/deck repository."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
from threading import RLock
from typing import Iterator
from uuid import uuid4

from models.authored_vnext.master.base import stable_slug
from models.authored_vnext.master.operations import MasterRevisionResponse
from models.authored_vnext.master.slide import DeckSnapshot

from .errors import DeckNotFoundError, MasterNotFoundError
from .repository import _check_deck_cas, _check_master_cas


@contextmanager
def _process_file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


class FileAuthoredVNextMasterRepository:
    """Append immutable revision files and atomically move small head pointers."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._thread_lock = RLock()

    def _entity_dir(self, kind: str, entity_id: str) -> Path:
        return self.root / kind / stable_slug(entity_id)

    @staticmethod
    def _read_pointer(entity_dir: Path) -> dict[str, object] | None:
        path = entity_dir / "head.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _atomic_pointer(entity_dir: Path, payload: dict[str, object]) -> None:
        temporary = entity_dir / f".head-{uuid4().hex}.tmp"
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, entity_dir / "head.json")

    @staticmethod
    def _immutable_write(path: Path, payload: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

    def get_master(
        self, master_id: str, revision: int | None = None
    ) -> MasterRevisionResponse:
        entity_dir = self._entity_dir("masters", master_id)
        selected = revision
        if selected is None:
            pointer = self._read_pointer(entity_dir)
            selected = int(pointer["revision"]) if pointer else None
        if selected is None:
            raise MasterNotFoundError()
        path = entity_dir / "revisions" / f"{selected:010d}.json"
        if not path.exists():
            raise MasterNotFoundError()
        return MasterRevisionResponse.model_validate_json(path.read_text(encoding="utf-8"))

    def append_master(
        self,
        record: MasterRevisionResponse,
        *,
        expected_revision: int | None,
        expected_digest: str | None,
    ) -> None:
        entity_dir = self._entity_dir("masters", record.master_id)
        with self._thread_lock, _process_file_lock(entity_dir / ".lock"):
            pointer = self._read_pointer(entity_dir)
            current = self.get_master(record.master_id) if pointer else None
            _check_master_cas(current, expected_revision, expected_digest)
            expected_next = 1 if current is None else current.revision + 1
            if record.revision != expected_next:
                from .errors import MasterRevisionConflictError

                raise MasterRevisionConflictError()
            path = entity_dir / "revisions" / f"{record.revision:010d}.json"
            self._immutable_write(path, record.model_dump_json(by_alias=True))
            self._atomic_pointer(
                entity_dir,
                {"revision": record.revision, "digest": record.digest},
            )

    def get_deck(self, deck_id: str, revision: int | None = None) -> DeckSnapshot:
        entity_dir = self._entity_dir("decks", deck_id)
        selected = revision
        if selected is None:
            pointer = self._read_pointer(entity_dir)
            selected = int(pointer["revision"]) if pointer else None
        if selected is None:
            raise DeckNotFoundError()
        path = entity_dir / "revisions" / f"{selected:010d}.json"
        if not path.exists():
            raise DeckNotFoundError()
        return DeckSnapshot.model_validate_json(path.read_text(encoding="utf-8"))

    def append_deck(
        self,
        deck: DeckSnapshot,
        *,
        expected_revision: int | None,
        expected_digest: str | None,
    ) -> None:
        entity_dir = self._entity_dir("decks", deck.deck_id)
        with self._thread_lock, _process_file_lock(entity_dir / ".lock"):
            pointer = self._read_pointer(entity_dir)
            current = self.get_deck(deck.deck_id) if pointer else None
            _check_deck_cas(current, expected_revision, expected_digest)
            expected_next = 1 if current is None else current.revision + 1
            if deck.revision != expected_next:
                from .errors import DeckRevisionConflictError

                raise DeckRevisionConflictError()
            path = entity_dir / "revisions" / f"{deck.revision:010d}.json"
            self._immutable_write(path, deck.model_dump_json(by_alias=True))
            self._atomic_pointer(
                entity_dir,
                {"revision": deck.revision, "digest": deck.digest},
            )

    def save_candidate(self, candidate_digest: str, payload: dict[str, object]) -> None:
        digest = candidate_digest.removeprefix("sha256:")
        path = self.root / "candidates" / f"{digest}.json"
        if path.exists():
            return
        try:
            self._immutable_write(
                path,
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        except FileExistsError:
            return
