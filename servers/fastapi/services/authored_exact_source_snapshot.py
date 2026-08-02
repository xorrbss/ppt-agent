"""Private, immutable source snapshots for authored exact reconstruction.

Retemplating must use the HTML visible in the editor at admission time.  The
persisted authored preview PNG can legitimately lag behind later HTML edits, so
it is not a safe exact-reconstruction source.  This module captures the current
HTML outside the public ``/app_data`` tree and materializes immutable PNGs for
the background worker without exposing paths or content in API payloads.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from models.sql.slide import SlideModel
from PIL import Image, UnidentifiedImageError
from utils.authored_styles import AuthoredStyle
from utils.get_env import get_app_data_directory_env
from utils.slide_capture import RENDER_CACHE_VERSION, render_html_to_png

from services.authored_checkpoint_service import atomic_write

SNAPSHOT_SCHEMA_VERSION = 1
_SNAPSHOT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_HTML_BYTES = 8 * 1024 * 1024


class ExactSourceSnapshotError(ValueError):
    """The private exact source snapshot cannot be safely reproduced."""


def private_exact_snapshot_root() -> Path:
    """Return a private root outside FastAPI's public app-data mount."""

    raw_app_data = (get_app_data_directory_env() or "").strip()
    if not raw_app_data:
        raise ExactSourceSnapshotError(
            "Exact reconstruction source snapshot storage is unavailable"
        )
    app_data = Path(raw_app_data).resolve()
    if app_data == app_data.parent:
        raise ExactSourceSnapshotError(
            "Exact reconstruction source snapshot storage is unavailable"
        )
    private_base = app_data.parent / f"{app_data.name}-private"
    root = private_base / "authored-exact-sources"
    if private_base.is_symlink() or root.is_symlink():
        raise ExactSourceSnapshotError(
            "Exact reconstruction source snapshot storage is unavailable"
        )
    return root


def _snapshot_directory(snapshot_id: str) -> Path:
    if not isinstance(snapshot_id, str) or not _SNAPSHOT_ID_PATTERN.fullmatch(
        snapshot_id
    ):
        raise ExactSourceSnapshotError(
            "Exact reconstruction source snapshot identity is invalid"
        )
    root = private_exact_snapshot_root().resolve()
    candidate = (root / snapshot_id).resolve()
    if not candidate.is_relative_to(root):
        raise ExactSourceSnapshotError(
            "Exact reconstruction source snapshot identity is invalid"
        )
    return candidate


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest_path(snapshot_id: str) -> Path:
    return _snapshot_directory(snapshot_id) / "manifest.json"


async def create_exact_source_snapshot(
    style_id: str,
    slides: Sequence[SlideModel],
    *,
    expected_slide_count: int,
) -> str:
    """Capture current HTML and its rendered pixels, then publish one manifest."""

    if expected_slide_count < 1 or len(slides) != expected_slide_count:
        raise ExactSourceSnapshotError(
            "Exact reconstruction source slide count does not match"
        )
    if not isinstance(style_id, str) or not style_id:
        raise ExactSourceSnapshotError(
            "Exact reconstruction source style identity is invalid"
        )

    snapshot_id = uuid.uuid4().hex
    directory = _snapshot_directory(snapshot_id)
    captured: list[tuple[int, bytes, str, str]] = []
    for expected_index, slide in enumerate(slides):
        if slide.index != expected_index:
            raise ExactSourceSnapshotError(
                "Exact reconstruction source slide order does not match"
            )
        html = slide.html_content
        if not isinstance(html, str) or not html.strip():
            raise ExactSourceSnapshotError(
                "Exact reconstruction source HTML is unavailable"
            )
        html_bytes = html.encode("utf-8")
        if len(html_bytes) > _MAX_HTML_BYTES:
            raise ExactSourceSnapshotError(
                "Exact reconstruction source HTML exceeds the safe limit"
            )
        digest = hashlib.sha256(html_bytes).hexdigest()
        filename = f"slide-{expected_index:04d}-{digest}.html"
        captured.append((expected_index, html_bytes, digest, filename))

    try:
        rendered = await asyncio.gather(
            *(
                render_html_to_png(html_bytes.decode("utf-8"))
                for _, html_bytes, _, _ in captured
            )
        )
    except (OSError, UnicodeError, RuntimeError, ValueError):
        raise ExactSourceSnapshotError(
            "Exact reconstruction source rendering failed"
        ) from None

    renderer_digest = hashlib.sha256(RENDER_CACHE_VERSION.encode("utf-8")).hexdigest()[
        :12
    ]
    entries: list[dict[str, Any]] = []
    for (expected_index, html_bytes, digest, filename), png in zip(captured, rendered):
        try:
            with Image.open(io.BytesIO(png)) as image:
                if image.size != (1280, 720):
                    raise ExactSourceSnapshotError(
                        "Exact reconstruction source render size is invalid"
                    )
                image.verify()
        except (OSError, UnidentifiedImageError):
            raise ExactSourceSnapshotError(
                "Exact reconstruction source rendering failed"
            ) from None
        png_digest = hashlib.sha256(png).hexdigest()
        png_filename = (
            f"slide-{expected_index:04d}-{digest}-{renderer_digest}-{png_digest}.png"
        )
        try:
            atomic_write(directory / filename, html_bytes)
            atomic_write(directory / png_filename, png)
        except OSError:
            raise ExactSourceSnapshotError(
                "Exact reconstruction source snapshot storage failed"
            ) from None
        entries.append(
            {
                "slide_index": expected_index,
                "html_sha256": digest,
                "html_file": filename,
                "source_png_sha256": png_digest,
                "source_png_file": png_filename,
            }
        )

    body = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "style_id": style_id,
        "renderer_version": RENDER_CACHE_VERSION,
        "viewport": {"width": 1280, "height": 720},
        "slides": entries,
    }
    manifest = {**body, "manifest_fingerprint": _canonical_sha256(body)}
    try:
        atomic_write(
            _manifest_path(snapshot_id),
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
        )
    except OSError:
        raise ExactSourceSnapshotError(
            "Exact reconstruction source snapshot storage failed"
        ) from None
    return snapshot_id


def _load_manifest(snapshot_id: str) -> tuple[Path, Mapping[str, Any]]:
    directory = _snapshot_directory(snapshot_id)
    try:
        payload = json.loads((directory / "manifest.json").read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ExactSourceSnapshotError(
            "Exact reconstruction source snapshot is unavailable"
        ) from None
    if not isinstance(payload, Mapping):
        raise ExactSourceSnapshotError(
            "Exact reconstruction source snapshot is invalid"
        )
    fingerprint = payload.get("manifest_fingerprint")
    body = {
        key: value for key, value in payload.items() if key != "manifest_fingerprint"
    }
    if (
        payload.get("schema_version") != SNAPSHOT_SCHEMA_VERSION
        or payload.get("snapshot_id") != snapshot_id
        or not isinstance(fingerprint, str)
        or fingerprint != _canonical_sha256(body)
    ):
        raise ExactSourceSnapshotError(
            "Exact reconstruction source snapshot is invalid"
        )
    return directory, payload


def _validated_entries(
    manifest: Mapping[str, Any],
    *,
    style_id: str,
    expected_slide_count: int,
) -> list[Mapping[str, Any]]:
    if manifest.get("style_id") != style_id:
        raise ExactSourceSnapshotError(
            "Exact reconstruction source style identity changed"
        )
    renderer_version = manifest.get("renderer_version")
    if not isinstance(renderer_version, str) or not renderer_version:
        raise ExactSourceSnapshotError(
            "Exact reconstruction source renderer identity is invalid"
        )
    if manifest.get("viewport") != {"width": 1280, "height": 720}:
        raise ExactSourceSnapshotError(
            "Exact reconstruction source viewport is invalid"
        )
    entries = manifest.get("slides")
    if not isinstance(entries, list) or len(entries) != expected_slide_count:
        raise ExactSourceSnapshotError(
            "Exact reconstruction source slide count changed"
        )
    for expected_index, entry in enumerate(entries):
        if not isinstance(entry, Mapping) or entry.get("slide_index") != expected_index:
            raise ExactSourceSnapshotError(
                "Exact reconstruction source slide order changed"
            )
        digest = entry.get("html_sha256")
        expected_filename = (
            f"slide-{expected_index:04d}-{digest}.html"
            if isinstance(digest, str)
            else None
        )
        if (
            not isinstance(digest, str)
            or not _SHA256_PATTERN.fullmatch(digest)
            or entry.get("html_file") != expected_filename
        ):
            raise ExactSourceSnapshotError(
                "Exact reconstruction source snapshot is invalid"
            )
        png_digest = entry.get("source_png_sha256")
        renderer_digest = hashlib.sha256(renderer_version.encode("utf-8")).hexdigest()[
            :12
        ]
        expected_png_filename = (
            f"slide-{expected_index:04d}-{digest}-{renderer_digest}-{png_digest}.png"
            if isinstance(png_digest, str)
            else None
        )
        if (
            not isinstance(png_digest, str)
            or not _SHA256_PATTERN.fullmatch(png_digest)
            or entry.get("source_png_file") != expected_png_filename
        ):
            raise ExactSourceSnapshotError(
                "Exact reconstruction source snapshot is invalid"
            )
    return entries


async def materialize_exact_source_style(
    style: AuthoredStyle,
    snapshot_id: str,
    *,
    expected_slide_count: int,
) -> AuthoredStyle:
    """Materialize a style from the immutable pixels rendered at admission."""

    directory, manifest = _load_manifest(snapshot_id)
    entries = _validated_entries(
        manifest,
        style_id=style.id,
        expected_slide_count=expected_slide_count,
    )
    paths: list[Path] = []
    for entry in entries:
        html_digest = str(entry["html_sha256"])
        html_path = directory / str(entry["html_file"])
        try:
            html_bytes = html_path.read_bytes()
        except OSError:
            raise ExactSourceSnapshotError(
                "Exact reconstruction source HTML is unavailable"
            ) from None
        if hashlib.sha256(html_bytes).hexdigest() != html_digest:
            raise ExactSourceSnapshotError("Exact reconstruction source HTML changed")

        png_path = directory / str(entry["source_png_file"])
        try:
            png = png_path.read_bytes()
            if hashlib.sha256(png).hexdigest() != entry["source_png_sha256"]:
                raise ExactSourceSnapshotError(
                    "Exact reconstruction source image changed"
                )
            with Image.open(io.BytesIO(png)) as image:
                if image.size != (1280, 720):
                    raise ExactSourceSnapshotError(
                        "Exact reconstruction source render size is invalid"
                    )
                image.verify()
        except (OSError, UnidentifiedImageError):
            raise ExactSourceSnapshotError(
                "Exact reconstruction source image is unavailable"
            ) from None
        paths.append(png_path)

    return replace(
        style,
        slide_reference_images=tuple(paths),
        slide_reference_indices=tuple(range(expected_slide_count)),
        assets_root=directory,
    )
