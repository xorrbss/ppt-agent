"""File-backed immutable authored vNext master/deck repository."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import RLock
from typing import Iterator
from uuid import uuid4

from models.authored_vnext.master.base import stable_slug
from models.authored_vnext.master.operations import MasterCatalogPage, MasterRevisionResponse
from models.authored_vnext.master.slide import DeckSnapshot

from .errors import DeckNotFoundError, MasterNotFoundError
from .repository import (
    _check_deck_cas,
    _check_master_cas,
    decode_catalog_cursor,
    encode_catalog_cursor,
    master_catalog_item,
)
from .visibility import is_internal_authored_master_id


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
    def _read_pointer(
        entity_dir: Path, filename: str = "head.json"
    ) -> dict[str, object] | None:
        path = entity_dir / filename
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _atomic_pointer(
        entity_dir: Path,
        payload: dict[str, object],
        filename: str = "head.json",
    ) -> None:
        temporary = entity_dir / f".{filename}-{uuid4().hex}.tmp"
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, entity_dir / filename)

    @staticmethod
    def _immutable_write(path: Path, payload: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

    def allocate_master_id(self) -> str:
        while True:
            master_id = f"master:{uuid4().hex}"
            if not self._entity_dir("masters", master_id).exists():
                return master_id

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

    def get_published_master(self, master_id: str) -> MasterRevisionResponse:
        entity_dir = self._entity_dir("masters", master_id)
        pointer = self._read_pointer(entity_dir, "published.json")
        if pointer is not None:
            return self.get_master(master_id, int(pointer["revision"]))

        revisions_dir = entity_dir / "revisions"
        if revisions_dir.exists():
            for path in sorted(revisions_dir.glob("*.json"), reverse=True):
                record = MasterRevisionResponse.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                if record.status == "published":
                    return record
        raise MasterNotFoundError()

    def list_masters(
        self, *, cursor: str | None, limit: int
    ) -> MasterCatalogPage:
        after = decode_catalog_cursor(cursor)
        masters_dir = self.root / "masters"
        candidates: list[
            tuple[
                str,
                MasterRevisionResponse,
                MasterRevisionResponse | None,
                datetime,
                bool,
            ]
        ] = []
        if masters_dir.exists():
            for entity_dir in masters_dir.iterdir():
                if not entity_dir.is_dir():
                    continue
                try:
                    pointer_path = entity_dir / "published.json"
                    head_pointer = self._read_pointer(entity_dir)
                    if head_pointer is None:
                        continue
                    head_revision = int(head_pointer["revision"])
                    head_path = (
                        entity_dir / "revisions" / f"{head_revision:010d}.json"
                    )
                    if not head_path.exists():
                        continue
                    head_record = MasterRevisionResponse.model_validate_json(
                        head_path.read_text(encoding="utf-8")
                    )
                    if is_internal_authored_master_id(head_record.master_id):
                        continue
                    if after is not None and head_record.master_id <= after:
                        continue
                    try:
                        published = self.get_published_master_from_dir(entity_dir)
                    except MasterNotFoundError:
                        published = None
                    display = published or head_record
                    has_draft = published is None or head_revision > published.revision
                    head_pointer_path = entity_dir / "head.json"
                    timestamp_path = head_pointer_path if head_pointer_path.exists() else (
                        pointer_path
                        if pointer_path.exists()
                        else entity_dir
                        / "revisions"
                        / f"{display.revision:010d}.json"
                    )
                    updated_time = datetime.fromtimestamp(
                        timestamp_path.stat().st_mtime, tz=timezone.utc
                    )
                    candidates.append(
                        (
                            display.master_id,
                            display,
                            published,
                            updated_time,
                            has_draft,
                        )
                    )
                except MasterNotFoundError:
                    continue
        candidates.sort(key=lambda item: item[0])
        selected = candidates[: limit + 1]
        has_more = len(selected) > limit
        selected = selected[:limit]
        return MasterCatalogPage(
            items=[
                master_catalog_item(
                    display,
                    published=published,
                    has_draft=has_draft,
                    updated_time=updated_time,
                )
                for _, display, published, updated_time, has_draft in selected
            ],
            next_cursor=(
                encode_catalog_cursor(selected[-1][0])
                if has_more and selected
                else None
            ),
        )

    def get_published_master_from_dir(
        self, entity_dir: Path
    ) -> MasterRevisionResponse:
        pointer = self._read_pointer(entity_dir, "published.json")
        if pointer is not None:
            selected = int(pointer["revision"])
            path = entity_dir / "revisions" / f"{selected:010d}.json"
            if path.exists():
                return MasterRevisionResponse.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
        revisions_dir = entity_dir / "revisions"
        if revisions_dir.exists():
            for path in sorted(revisions_dir.glob("*.json"), reverse=True):
                record = MasterRevisionResponse.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                if record.status == "published":
                    return record
        raise MasterNotFoundError()

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
            if record.status == "published":
                self._atomic_pointer(
                    entity_dir,
                    {"revision": record.revision, "digest": record.digest},
                    "published.json",
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
