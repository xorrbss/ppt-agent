"""On-disk icon packs: one directory per template/style, one JSON index.

A pack lives under ``<APP_DATA_DIRECTORY>/authored-icon-packs/<pack key>``
next to the other authored sidecars (quality receipts, checkpoints). It is a
cache, not an owner-scoped asset: icons are inlined into slides as data
URIs and are never served over ``/app_data``, so a pack is safely shared by
every deck that uses the same template and style.

The index is the only thing two writers can race on. It is written by
rename, which is atomic on Windows and POSIX alike, and every write first
re-reads the live index and merges, so a concurrent appender in another
process loses nothing: the first entry recorded for a concept wins and a
duplicate file is discarded. In-process callers additionally serialize on
a per-pack lock so one deck never generates the same concept twice.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from utils.get_env import get_app_data_directory_env

LOGGER = logging.getLogger(__name__)

ICON_PACK_SCHEMA_VERSION = "authored-icon-pack/v1"
ICON_PACK_ROOT_NAME = "authored-icon-packs"
_INDEX_NAME = "index.json"
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")
_WHITESPACE = re.compile(r"\s+")
_REPLACE_ATTEMPTS = 8
_REPLACE_BACKOFF_SECONDS = 0.05


def concept_key(concept: str) -> str:
    """The cache key of a concept: whitespace-collapsed, case-folded."""
    return _WHITESPACE.sub(" ", (concept or "").strip()).casefold()


def icon_file_name(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16] + ".png"


def pack_directory_name(pack_key: str) -> str:
    """A filesystem-safe, collision-resistant directory name for a pack key."""
    slug = _SAFE_SEGMENT.sub("-", (pack_key or "default").strip()).strip("-")[:48]
    digest = hashlib.sha1((pack_key or "default").encode("utf-8")).hexdigest()[:10]
    return f"{slug or 'pack'}-{digest}"


def icon_pack_root(app_data: Optional[str] = None) -> Path:
    base = app_data or get_app_data_directory_env()
    if not base:
        raise RuntimeError("APP_DATA_DIRECTORY is not configured")
    return Path(base) / ICON_PACK_ROOT_NAME


@dataclass(frozen=True)
class IconEntry:
    concept: str
    file: str
    width: int
    height: int
    sheet: Optional[str] = None
    cell: Optional[int] = None
    checks: Mapping[str, Any] = field(default_factory=dict)
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept": self.concept,
            "file": self.file,
            "width": self.width,
            "height": self.height,
            "sheet": self.sheet,
            "cell": self.cell,
            "checks": dict(self.checks),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "IconEntry":
        return cls(
            concept=str(raw.get("concept", "")),
            file=str(raw.get("file", "")),
            width=int(raw.get("width", 0) or 0),
            height=int(raw.get("height", 0) or 0),
            sheet=raw.get("sheet") if isinstance(raw.get("sheet"), str) else None,
            cell=raw.get("cell") if isinstance(raw.get("cell"), int) else None,
            checks=dict(raw.get("checks") or {}),
            created_at=float(raw.get("created_at", 0.0) or 0.0),
        )


class IconPack:
    """One template/style's icons on disk, addressed by concept key."""

    def __init__(self, pack_key: str, *, app_data: Optional[str] = None):
        self.pack_key = pack_key
        self.directory = icon_pack_root(app_data) / pack_directory_name(pack_key)
        self.index_path = self.directory / _INDEX_NAME

    # -- reading -----------------------------------------------------------

    def load(self) -> dict[str, IconEntry]:
        """The live index; a missing or unreadable index is an empty pack."""
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            LOGGER.warning("icon pack index unreadable, treating as empty: %s", error)
            return {}
        icons = raw.get("icons") if isinstance(raw, Mapping) else None
        if not isinstance(icons, Mapping):
            return {}
        entries: dict[str, IconEntry] = {}
        for key, value in icons.items():
            if isinstance(value, Mapping):
                entries[str(key)] = IconEntry.from_dict(value)
        return entries

    def lookup(self, concepts: list[str]) -> tuple[dict[str, IconEntry], list[str]]:
        """Split concepts into (found with file present, missing)."""
        index = self.load()
        found: dict[str, IconEntry] = {}
        missing: list[str] = []
        for concept in concepts:
            key = concept_key(concept)
            entry = index.get(key)
            if entry is not None and (self.directory / entry.file).is_file():
                found[key] = entry
            else:
                missing.append(concept)
        return found, missing

    def path_for(self, entry: IconEntry) -> Path:
        return self.directory / entry.file

    # -- writing -----------------------------------------------------------

    def write_icon(self, key: str, payload: bytes) -> str:
        """Store an icon's bytes under its deterministic name; return the name."""
        self.directory.mkdir(parents=True, exist_ok=True)
        name = icon_file_name(key)
        _atomic_write_bytes(self.directory / name, payload)
        return name

    def write_sheet(self, payload: bytes) -> str:
        """Keep the raw sheet for provenance; return its pack-relative name."""
        sheets = self.directory / "sheets"
        sheets.mkdir(parents=True, exist_ok=True)
        name = f"sheets/{uuid.uuid4().hex}.png"
        _atomic_write_bytes(self.directory / name, payload)
        return name

    def append(self, new_entries: Mapping[str, IconEntry]) -> dict[str, IconEntry]:
        """Merge entries into the live index atomically; return the merged index.

        An entry another writer recorded first is kept and our duplicate file
        is removed, so two processes generating the same concept converge on
        one file.
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        current = self.load()
        for key, entry in new_entries.items():
            existing = current.get(key)
            if existing is not None and existing.file != entry.file:
                _discard(self.directory / entry.file)
                continue
            current[key] = entry
        document = {
            "schema_version": ICON_PACK_SCHEMA_VERSION,
            "pack_key": self.pack_key,
            "updated_at": time.time(),
            "icons": {key: entry.to_dict() for key, entry in sorted(current.items())},
        }
        _atomic_write_bytes(
            self.index_path,
            json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        return current


def _discard(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _atomic_write_bytes(target: Path, payload: bytes) -> None:
    """Write via a sibling temp file and rename, retrying a Windows share lock."""
    temp = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    with open(temp, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(temp, target)
            return
        except PermissionError:
            # A reader holds the destination open (Windows); back off and retry.
            if attempt == _REPLACE_ATTEMPTS - 1:
                _discard(temp)
                raise
            time.sleep(_REPLACE_BACKOFF_SECONDS * (attempt + 1))


_PACK_LOCKS: dict[tuple[int, str], asyncio.Lock] = {}


def pack_lock(pack_key: str) -> asyncio.Lock:
    """One in-process lock per pack and event loop."""
    loop_id = id(asyncio.get_running_loop())
    key = (loop_id, pack_key)
    lock = _PACK_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _PACK_LOCKS[key] = lock
    return lock


__all__ = [
    "ICON_PACK_ROOT_NAME",
    "ICON_PACK_SCHEMA_VERSION",
    "IconEntry",
    "IconPack",
    "concept_key",
    "icon_file_name",
    "icon_pack_root",
    "pack_directory_name",
    "pack_lock",
]
