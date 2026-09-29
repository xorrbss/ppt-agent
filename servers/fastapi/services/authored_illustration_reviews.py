"""Server-held text-free reviews of generated illustrations.

The local-fit runner refuses to fit a slide that shows a raster image unless
the policy carries evidence that the picture is text-free. Icon-pack icons
get that evidence restated from the pack's on-disk index; a generated
illustration has no such durable record. This module is that record: one
JSON index under ``<APP_DATA_DIRECTORY>/authored-illustration-reviews``,
keyed by the digest of the data URI that ends up in the slide HTML, so the
review survives a checkpoint resume and cannot be forged by the model.

The index is written by rename and every write re-reads and merges, so a
concurrent appender in another process loses nothing; the first entry
recorded for a picture wins. In-process appends additionally serialize on a
per-root ``asyncio.Lock``. A review is advisory: ``review_illustration``
never raises, because generation must not break on a missing OCR runtime or
a full disk.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from services.authored_icon_pack.checks import check_no_text
from services.authored_local_fit_image_text import src_digest
from utils.get_env import get_app_data_directory_env
from utils.hashing import sha256_prefixed

try:
    from services.authored_icon_pack.store import _atomic_write_bytes
except ImportError:  # pragma: no cover - keep the store self-contained
    _REPLACE_ATTEMPTS = 8
    _REPLACE_BACKOFF_SECONDS = 0.05

    def _atomic_write_bytes(target: Path, payload: bytes) -> None:
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
                if attempt == _REPLACE_ATTEMPTS - 1:
                    try:
                        temp.unlink()
                    except OSError:
                        pass
                    raise
                time.sleep(_REPLACE_BACKOFF_SECONDS * (attempt + 1))


LOGGER = logging.getLogger(__name__)

ILLUSTRATION_REVIEW_ROOT_NAME = "authored-illustration-reviews"
ILLUSTRATION_REVIEW_SCHEMA_VERSION = 1
_INDEX_NAME = "index.json"
_DATA_PREFIX = "data:"
_BASE64_MARKER = ";base64,"


def illustration_review_root(app_data: Optional[str] = None) -> Path:
    base = app_data or get_app_data_directory_env()
    if not base:
        raise RuntimeError("APP_DATA_DIRECTORY is not configured")
    return Path(base) / ILLUSTRATION_REVIEW_ROOT_NAME


class IllustrationReviewStore:
    """The review index on disk, addressed by data-URI digest."""

    def __init__(self, *, app_data: Optional[str] = None):
        self.root = illustration_review_root(app_data)
        self.index_path = self.root / _INDEX_NAME

    def load(self) -> dict[str, dict[str, Any]]:
        """The live index; a missing or unreadable index is empty."""
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            LOGGER.warning(
                "illustration review index unreadable, treating as empty: %s", error
            )
            return {}
        reviews = raw.get("reviews") if isinstance(raw, Mapping) else None
        if not isinstance(reviews, Mapping):
            return {}
        return {
            str(key): dict(value)
            for key, value in reviews.items()
            if isinstance(value, Mapping)
        }

    def append(
        self, entries: Mapping[str, Mapping[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Merge entries into the live index atomically; return the merged index.

        An entry another writer recorded first is kept: the first review of a
        picture is the one every later reader sees.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        current = self.load()
        for key, entry in entries.items():
            if key in current:
                continue
            current[key] = dict(entry)
        document = {
            "schema_version": ILLUSTRATION_REVIEW_SCHEMA_VERSION,
            "updated_at": time.time(),
            "reviews": {key: current[key] for key in sorted(current)},
        }
        _atomic_write_bytes(
            self.index_path,
            json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        return current


_ROOT_LOCKS: dict[tuple[int, str], asyncio.Lock] = {}


def _root_lock(root: Path) -> asyncio.Lock:
    """One in-process lock per review root and event loop."""
    key = (id(asyncio.get_running_loop()), str(root))
    lock = _ROOT_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _ROOT_LOCKS[key] = lock
    return lock


def _decode_data_uri(data_uri: str) -> Optional[tuple[str, bytes]]:
    """``(mime, payload)`` of a base64 data URI; anything else is ``None``."""
    if not isinstance(data_uri, str) or not data_uri.startswith(_DATA_PREFIX):
        return None
    marker = data_uri.find(_BASE64_MARKER)
    if marker < 0:
        return None
    mime = data_uri[len(_DATA_PREFIX) : marker].split(";", 1)[0].strip().lower()
    if not mime:
        return None
    try:
        payload = base64.b64decode(data_uri[marker + len(_BASE64_MARKER) :], validate=True)
    except (binascii.Error, ValueError):
        return None
    if not payload:
        return None
    return mime, payload


def _prompt_digest(prompt: str) -> str:
    return sha256_prefixed((prompt or "").encode("utf-8"))


async def review_illustration(
    data_uri: str,
    *,
    labels_requested: bool,
    forbidden: Sequence[str],
    provider: Optional[str],
    prompt: str,
    app_data: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """OCR-check one illustration, record the verdict, return the entry.

    Never raises: any failure (malformed URI, OCR error, disk) is logged and
    yields ``None`` so the picture simply carries no evidence.
    """
    try:
        decoded = _decode_data_uri(data_uri)
        if decoded is None:
            LOGGER.warning("illustration review skipped: not a base64 data URI")
            return None
        mime, payload = decoded
        store = IllustrationReviewStore(app_data=app_data)
        text_check = await check_no_text(payload, forbidden=tuple(forbidden or ()))
        entry: dict[str, Any] = {
            "labels_requested": bool(labels_requested),
            "text": text_check.to_dict(),
            "provider": provider,
            "prompt_digest": _prompt_digest(prompt),
            "mime": mime,
            "created_at": time.time(),
            "schema_version": ILLUSTRATION_REVIEW_SCHEMA_VERSION,
        }
        key = src_digest(data_uri)
        async with _root_lock(store.root):
            merged = await asyncio.to_thread(store.append, {key: entry})
        return merged.get(key, entry)
    except Exception as error:  # noqa: BLE001 - a review is advisory
        LOGGER.warning(
            "illustration review failed, no evidence recorded: %s: %s",
            type(error).__name__,
            error,
        )
        return None


async def record_generated_illustration(
    data_uri: Optional[str], *, prompt: str, content: str, image_service: object
) -> None:
    """The single write point for a generated illustration's review.

    Called by ``utils.authored_illustrations`` right after a slot's picture is
    inlined. Whether labels were requested is read off the prompt the provider
    actually received -- the no-text clause is the whole of that decision, so
    there is no second source of truth -- and the slide-content words are the
    only strings a label may have copied, so they are what the text check
    looks for. Advisory: every failure is logged and the slide keeps its
    picture; ``None`` (no picture) records nothing.
    """
    if not data_uri:
        return
    try:
        from utils.authored_illustration_prompt import NO_TEXT_SUFFIX
        from utils.image_provider import get_selected_image_provider

        try:
            provider = get_selected_image_provider(getattr(image_service, "settings", None))
        except Exception:  # noqa: BLE001 - the provider name is informational
            provider = None
        forbidden = sorted({word for word in (content or "").split() if len(word) >= 2})
        await review_illustration(
            data_uri,
            labels_requested=not prompt.endswith(NO_TEXT_SUFFIX),
            forbidden=forbidden,
            provider=str(provider) if provider is not None else None,
            prompt=prompt,
        )
    except Exception as exc:  # noqa: BLE001 - a review must never cost the picture
        LOGGER.warning("illustration review not recorded: %s", exc)


__all__ = [
    "ILLUSTRATION_REVIEW_ROOT_NAME",
    "ILLUSTRATION_REVIEW_SCHEMA_VERSION",
    "IllustrationReviewStore",
    "illustration_review_root",
    "record_generated_illustration",
    "review_illustration",
]
