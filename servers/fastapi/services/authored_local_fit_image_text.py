"""Image-text evidence for the local fit, restated from the icon pack index.

The browser fit runner refuses to fit a slide that shows a raster image
unless the policy carries an ``image_text_evidence`` record for it that is
``text_free`` and names a ``review_digest``. The only durable, unforgeable
record of a picture being text-free is the icon pack index: an ``IconEntry``
whose ``checks["text"]`` passed by OCR. This module makes no judgment of its
own -- it re-states what the pack index already recorded, for the ``<img>``
elements whose ``src`` is that icon's inlined data URI. Scene cutouts,
generated illustrations and uploaded source pictures are not in the index
and therefore never receive a record; a heuristic-only text check is not a
verification and receives none either, so the fail-closed rule stands.

The illustration review index (``authored-illustration-reviews/index.json``)
is the second such record: a generated illustration whose OCR text check
passed *and* that was requested without labels. Records restated from it
carry ``provenance == "illustration"``.
"""

from __future__ import annotations

import json
import logging
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping, Optional

from services.authored_icon_pack.inline import icon_data_uri
from services.authored_icon_pack.store import IconEntry, icon_pack_root
from utils.get_env import get_app_data_directory_env
from utils.hashing import sha256_hex, sha256_prefixed

LOGGER = logging.getLogger(__name__)

PROVENANCE = "icon_pack"
ILLUSTRATION_PROVENANCE = "illustration"
_ILLUSTRATION_ROOT_NAME = "authored-illustration-reviews"
_VERIFIED_METHOD = "ocr"
_DATA_PREFIX = "data:"
_INDEX_NAME = "index.json"
_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
})


def src_digest(src: str) -> str:
    return sha256_prefixed(src.encode("utf-8"))


# -- pack index --------------------------------------------------------------


def _read_index(directory: Path) -> tuple[str, dict[str, IconEntry]]:
    """One pack's (key, entries); anything unreadable is an empty pack."""
    fallback = directory.name
    try:
        raw = json.loads((directory / _INDEX_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback, {}
    if not isinstance(raw, Mapping):
        return fallback, {}
    pack_key = raw.get("pack_key")
    icons = raw.get("icons")
    if not isinstance(icons, Mapping):
        return str(pack_key or fallback), {}
    entries = {
        str(key): IconEntry.from_dict(value)
        for key, value in icons.items()
        if isinstance(value, Mapping)
    }
    return str(pack_key or fallback), entries


def _ocr_verified_text_free(entry: IconEntry) -> bool:
    text = entry.checks.get("text") if isinstance(entry.checks, Mapping) else None
    if not isinstance(text, Mapping):
        return False
    return text.get("passed") is True and text.get("method") == _VERIFIED_METHOD


def _review_digest(pack_key: str, entry: IconEntry, path: Path) -> Optional[str]:
    try:
        file_hash = sha256_hex(path.read_bytes())
    except OSError:
        return None
    return f"icon-pack:{pack_key}:{entry.file}:{file_hash}"


def verified_icon_reviews(app_data: Optional[str] = None) -> dict[str, str]:
    """Map ``src`` digest -> review digest for every OCR-verified pack icon.

    Scans every pack directory under the icon pack root. A missing root or
    an unreadable pack contributes nothing. The digest is taken over the
    data URI the slide actually carries (``icon_data_uri`` may re-encode the
    file), so a match proves the ``<img>`` shows this very icon.
    """
    try:
        root = icon_pack_root(app_data)
    except RuntimeError:
        return {}
    try:
        directories = [entry for entry in root.iterdir() if entry.is_dir()]
    except OSError:
        return {}
    reviews: dict[str, str] = {}
    for directory in sorted(directories):
        pack_key, entries = _read_index(directory)
        for entry in entries.values():
            if not entry.file or not _ocr_verified_text_free(entry):
                continue
            path = directory / entry.file
            uri = icon_data_uri(path)
            review = _review_digest(pack_key, entry, path) if uri else None
            if uri and review:
                reviews.setdefault(src_digest(uri), review)
    return reviews


# -- illustration review index ----------------------------------------------


def _illustration_root(app_data: Optional[str]) -> Optional[Path]:
    """Root of the illustration review store; ``None`` when unconfigured."""
    try:
        from services.authored_illustration_reviews import illustration_review_root
    except ImportError:
        base = app_data or get_app_data_directory_env()
        return Path(base) / _ILLUSTRATION_ROOT_NAME if base else None
    try:
        return illustration_review_root(app_data)
    except RuntimeError:
        return None


def _load_illustration_index(root: Path) -> dict[str, Any]:
    """``reviews`` mapping of ``index.json``; anything unreadable is empty."""
    try:
        raw = json.loads((root / _INDEX_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, Mapping):
        return {}
    reviews = raw.get("reviews")
    return dict(reviews) if isinstance(reviews, Mapping) else {}


def _verified_illustration(entry: Any) -> Optional[str]:
    """The entry's prompt digest when it is an OCR-verified, label-free picture."""
    if not isinstance(entry, Mapping) or entry.get("labels_requested") is not False:
        return None
    text = entry.get("text")
    if not isinstance(text, Mapping):
        return None
    if text.get("passed") is not True or text.get("method") != _VERIFIED_METHOD:
        return None
    prompt_digest = entry.get("prompt_digest")
    return prompt_digest if isinstance(prompt_digest, str) and prompt_digest else None


def verified_illustration_reviews(app_data: Optional[str] = None) -> dict[str, str]:
    """Map ``src`` digest -> review digest for every OCR-verified illustration.

    Only entries generated without labels and whose text check passed by OCR
    qualify. A missing root, an unreadable index or a malformed entry
    contributes nothing; nothing raises.
    """
    root = _illustration_root(app_data)
    if root is None:
        return {}
    reviews: dict[str, str] = {}
    for key, entry in _load_illustration_index(root).items():
        prompt_digest = _verified_illustration(entry)
        if prompt_digest is None:
            continue
        digest = str(key)
        reviews[digest] = f"illustration:{digest}:{prompt_digest}"
    return reviews


# -- slide markup ------------------------------------------------------------


class _ImageCollector(HTMLParser):
    """Every ``<img>`` with the qid the browser would resolve for it.

    Resolution mirrors ``node.closest('[data-qid]')``: the image's own
    ``data-qid`` if present, else the nearest ancestor's. Unclosed elements
    are tolerated the way a browser tolerates them; void elements never
    open a scope.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._scopes: list[tuple[str, Optional[str]]] = []
        self.images: list[tuple[str, str]] = []

    def _closest_qid(self, own: Optional[str]) -> Optional[str]:
        if own:
            return own
        for _tag, qid in reversed(self._scopes):
            if qid:
                return qid
        return None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attributes = {name: value for name, value in attrs}
        if tag == "img":
            qid = self._closest_qid(attributes.get("data-qid"))
            src = attributes.get("src")
            if qid and src:
                self.images.append((qid, src))
            return
        if tag in _VOID_TAGS:
            return
        self._scopes.append((tag, attributes.get("data-qid") or None))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS and tag != "img":
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._scopes) - 1, -1, -1):
            if self._scopes[index][0] == tag:
                del self._scopes[index:]
                return


def images_with_qid(html: str) -> list[tuple[str, str]]:
    """``(qid, src)`` for every image that has a qid; ``[]`` on malformed input."""
    collector = _ImageCollector()
    try:
        collector.feed(html or "")
        collector.close()
    except Exception as error:  # noqa: BLE001 - authored markup is untrusted
        LOGGER.warning("image-text evidence: could not parse slide HTML: %s", error)
        return []
    return collector.images


# -- evidence ----------------------------------------------------------------


def image_text_evidence(html: str, *, app_data: Optional[str] = None) -> list[dict[str, Any]]:
    images = images_with_qid(html)
    candidates = [(qid, src) for qid, src in images if src.startswith(_DATA_PREFIX)]
    if not candidates:
        return []
    sources: list[tuple[dict[str, str], str]] = [
        (verified_icon_reviews(app_data), PROVENANCE),
        (verified_illustration_reviews(app_data), ILLUSTRATION_PROVENANCE),
    ]
    if not any(reviews for reviews, _provenance in sources):
        return []
    records: list[dict[str, Any]] = []
    for qid, src in candidates:
        asset_digest = src_digest(src)
        for reviews, provenance in sources:
            review = reviews.get(asset_digest)
            if review is None:
                continue
            records.append({
                "qid": qid,
                "asset_digest": asset_digest,
                "text_free": True,
                "review_digest": review,
                "provenance": provenance,
            })
            break
    return records


__all__ = [
    "ILLUSTRATION_PROVENANCE",
    "PROVENANCE",
    "image_text_evidence",
    "images_with_qid",
    "src_digest",
    "verified_icon_reviews",
    "verified_illustration_reviews",
]
