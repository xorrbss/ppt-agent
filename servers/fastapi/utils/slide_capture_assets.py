"""Local-asset fingerprinting for the render cache identity.

Split out of ``utils/slide_capture.py``, which owns launching Chrome and
caching what it produced. This module answers only the question that cache
identity depends on: given one authored HTML document, what is the
deterministic digest of every *local* file the renderer will read while
drawing it?

HTML alone is an insufficient cache key. A font, image or stylesheet on disk
can change without the authored Scene payload changing a byte, and the render
would then differ from the cached PNG and its geometry measurements. So every
referenced local asset is hashed, stylesheets are followed one level deeper
into the assets they themselves reference, and unreadable paths become an
identity-bearing sentinel rather than being silently skipped -- a later
readable asset must produce a *different* key, never reuse an uncertain
render.

Nothing here retains file contents: assets are hashed and dropped, and the
per-path digest cache is keyed on ``(path, size, mtime_ns)``. Stylesheets are
deliberately not cached, because their digest depends on their transitive
dependencies rather than on their own stat alone.
"""

from __future__ import annotations

import html as html_lib
import pathlib
import re
import threading
import urllib.parse
import urllib.request
from utils.hashing import sha256_hex, sha256_prefixed

_ASSET_REFERENCE_PATTERN = re.compile(
    r'''(?:\b(?:src|srcset|poster|href|data)\s*=\s*["\']([^"\']+)["\'])|'''
    r'''(?:url\(\s*["\']?([^\s)"\']+)["\']?\s*\))''',
    re.IGNORECASE,
)
_asset_digest_lock = threading.Lock()
_asset_digest_cache: dict[tuple[str, int, int], str] = {}


def _asset_references(source: str) -> list[str]:
    references: list[str] = []
    for match in _ASSET_REFERENCE_PATTERN.finditer(source):
        value = match.group(1) or match.group(2)
        if value:
            references.append(html_lib.unescape(value).strip())
    return references


def _local_asset_path(reference: str, *, base: pathlib.Path | None = None) -> pathlib.Path | None:
    """Resolve only explicit local assets; network URLs remain renderer-blocked."""

    direct = pathlib.Path(urllib.parse.unquote(reference))
    if direct.is_absolute():
        return direct.resolve()
    parsed = urllib.parse.urlparse(reference)
    if parsed.scheme == "file":
        local_path = urllib.request.url2pathname(parsed.path)
        if parsed.netloc:
            local_path = f"//{parsed.netloc}{local_path}"
        return pathlib.Path(local_path).resolve()
    if parsed.scheme or reference.startswith(("#", "//", "/")):
        return None
    candidate = pathlib.Path(reference)
    if candidate.is_absolute():
        return candidate.resolve()
    if base is not None:
        return (base / candidate).resolve()
    return None


def _fingerprint_local_asset(path: pathlib.Path, seen: set[pathlib.Path]) -> str:
    """Hash a local asset and CSS dependencies without retaining its contents."""

    resolved: pathlib.Path | None = None
    try:
        resolved = path.resolve()
        if resolved in seen:
            return f"cycle:{resolved.as_posix()}"
        seen.add(resolved)
        stat = resolved.stat()
        key = (str(resolved), int(stat.st_size), int(stat.st_mtime_ns))
        is_stylesheet = resolved.suffix.lower() in {".css", ".scss", ".sass", ".less"}
        with _asset_digest_lock:
            cached = None if is_stylesheet else _asset_digest_cache.get(key)
        if cached is None:
            content = resolved.read_bytes()
            digest = sha256_hex(content)
            if is_stylesheet:
                children = [
                    _fingerprint_local_asset(child, seen)
                    for ref in _asset_references(content.decode("utf-8", errors="replace"))
                    if (child := _local_asset_path(ref, base=resolved.parent)) is not None
                ]
                digest = sha256_hex((digest + "\0" + "\0".join(sorted(children))).encode("utf-8"))
            cached = f"sha256:{digest}"
            if not is_stylesheet:
                with _asset_digest_lock:
                    _asset_digest_cache[key] = cached
        return cached
    except OSError as error:
        # The sentinel itself is identity-bearing, so a later readable asset
        # creates a different key rather than reusing an uncertain render.
        return "unreadable:" + sha256_hex(f"{path}\0{type(error).__name__}".encode("utf-8"))
    finally:
        if resolved is not None:
            seen.discard(resolved)


def _asset_fingerprint(html: str) -> str:
    """Fingerprint referenced local font/image/CSS assets plus inline asset URLs."""

    components: list[str] = []
    for reference in _asset_references(html):
        if reference.lower().startswith("data:"):
            components.append("data:" + sha256_hex(reference.encode("utf-8")))
            continue
        path = _local_asset_path(reference)
        if path is not None:
            components.append(_fingerprint_local_asset(path, set()))
        else:
            # This covers relative/blocked remote references deterministically;
            # their literal URL is already part of HTML, but retaining it makes
            # the asset boundary explicit in identity receipts.
            components.append("reference:" + sha256_hex(reference.encode("utf-8")))
    return sha256_prefixed("\0".join(sorted(components)).encode("utf-8"))


__all__ = [
    "_asset_fingerprint",
    "_asset_references",
    "_fingerprint_local_asset",
    "_local_asset_path",
]
