"""Turn a deck's uploads into its persisted source-image candidate set.

Ordinary authored generation may place a picture the user already owns instead
of an invented illustration. This module is the single place that decides which
uploaded pictures qualify: it collects them (uploaded directly, or extracted
from an uploaded PPTX), applies the content filters, and persists the survivors
as uploaded ``ImageAsset`` rows so later stages can address them by a stable id.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from io import BytesIO
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.image_asset import ImageAsset
from services.source_images.extract import MAX_PPTX_BYTES, _content_group, extract_pptx_images
from services.source_images.extract_documents import extract_document_images
from services.source_images.models import (
    FULL_SLIDE_COVERAGE_THRESHOLD,
    FURNITURE_REPEAT_THRESHOLD,
    MIN_SOURCE_IMAGE_EDGE_PX,
    SUPPORTED_SOURCE_IMAGE_MIME_TYPES,
    SourceImageCandidate,
)
from utils.path_helpers import get_writable_path
from utils.hashing import sha256_hex

#: Uploads we open as decks rather than as pictures. ``.ppt`` is listed because
#: the upload surface accepts it; the extractor decides whether it can read one.
_SOURCE_DECK_SUFFIXES = frozenset({".pptx", ".pptm", ".ppt"})

#: Storage sits beside the other writable caches, one subdirectory per deck, so
#: a deleted presentation's pictures can be swept as a unit.
_STORAGE_ROOT = "source_images"

_MIME_TO_SUFFIX = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}

_DIGEST_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class _RawImage:
    """One picture before filtering, from either kind of upload.

    The two sources hand us the bytes differently -- a direct upload is already
    a file we can copy, an extracted picture only exists in memory -- so the
    payload is carried either way and resolved once, at persist time.
    """

    mime_type: str
    width: int
    height: int
    digest: str
    source_path: Optional[Path] = None
    payload: Optional[bytes] = None
    source_slide_index: Optional[int] = None
    #: Share of its slide the picture was placed over, when the deck said.
    slide_coverage: Optional[float] = None
    source_heading: Optional[str] = None
    source_text: Optional[str] = None
    # Present only for strict source-content reflow.  Two drawing occurrences
    # may intentionally point at the same bytes and still need distinct slots.
    occurrence_id: Optional[str] = None
    original_name: Optional[str] = None
    ocr_text: Optional[str] = None
    source_locator: Optional[dict] = None


@dataclass(frozen=True)
class ManifestImageOccurrence:
    """One immutable image occurrence projected from a source manifest."""

    occurrence_id: str
    payload: bytes
    mime_type: str
    content_digest: str
    source_slide_index: int
    source_heading: Optional[str] = None
    source_text: Optional[str] = None


async def register_source_images(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    upload_paths: Sequence[Path],
    upload_metadata: Mapping[str, dict] | None = None,
    registration_report: list[dict] | None = None,
    include_document_images: bool = False,
) -> list[SourceImageCandidate]:
    """Collect, filter and persist the pictures a deck may reuse.

    The returned candidates are in a deterministic order (upload order, then the
    order the extractor reported inside each deck) and carry the persisted
    ``ImageAsset`` id, which is the only identifier later stages resolve.
    """

    collected = await _collect_raw_images(
        upload_paths, include_document_images=include_document_images, registration_report=registration_report
    )
    collected = [
        _with_upload_context(raw, upload_metadata or {}, include_document_images=include_document_images)
        for raw in collected
    ]
    if include_document_images:
        collected = await asyncio.to_thread(
            _attach_text_image_contexts,
            collected,
            upload_paths,
            upload_metadata or {},
            presentation_id,
            registration_report,
        )
    kept = (
        [raw for raw in collected if raw.mime_type in SUPPORTED_SOURCE_IMAGE_MIME_TYPES]
        if include_document_images
        else _apply_source_image_filters(collected)
    )
    if registration_report is not None:
        kept_digests = {raw.digest for raw in kept}
        seen_digests: set[str] = set()
        for raw in collected:
            if raw.mime_type not in SUPPORTED_SOURCE_IMAGE_MIME_TYPES:
                reason = "unsupported_format"
            elif include_document_images:
                reason = "registered"
            elif min(raw.width, raw.height) < MIN_SOURCE_IMAGE_EDGE_PX:
                reason = "below_size_floor"
            elif raw.digest not in kept_digests:
                reason = "source_furniture_or_backplate"
            elif raw.digest in seen_digests:
                reason = "duplicate_content"
            else:
                reason = "registered"
            seen_digests.add(raw.digest)
            registration_report.append({"digest": raw.digest, "original_name": raw.original_name, "status": reason})
        collected_paths = {raw.source_path for raw in collected if raw.source_path is not None}
        for upload_path in upload_paths:
            path = Path(upload_path)
            if (
                path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif"}
                and path not in collected_paths
            ):
                registration_report.append(
                    {
                        "original_name": (upload_metadata or {}).get(str(path), {}).get("original_name") or path.name,
                        "status": "image_read_failed",
                    }
                )
    if not kept:
        return []

    storage_dir = Path(await asyncio.to_thread(get_writable_path, f"{_STORAGE_ROOT}/{presentation_id}"))
    registered = [
        await _persist_raw_image(
            session,
            storage_dir=storage_dir,
            presentation_id=presentation_id,
            raw=raw,
        )
        for raw in kept
    ]
    # One flush for the whole set: the caller owns the surrounding transaction.
    await session.flush()
    return registered


def _with_upload_context(
    raw: _RawImage, upload_metadata: Mapping[str, dict], *, include_document_images: bool
) -> _RawImage:
    if raw.source_path is None:
        return raw
    metadata = upload_metadata.get(str(raw.source_path), {})
    original_name = str(metadata.get("original_name") or raw.source_path.name)
    ocr_text = str(metadata.get("ocr_text") or "")[:20000] or None
    locator = raw.source_locator
    if include_document_images and locator is None and raw.source_path.suffix.lower() not in _SOURCE_DECK_SUFFIXES:
        locator = {
            "sourceDocumentSha256": raw.digest,
            "sourceFormat": "image",
            "pageIndex": None,
            "objectLocator": "direct-upload",
            "extractionKind": "uploaded_raster",
            "contentGroup": _content_group(raw.digest, "direct-upload", "direct_upload", ocr_text, original_name),
        }
    if locator is not None and locator.get("sourceFormat") == "image" and metadata.get("workspace_asset_id"):
        try:
            workspace_asset_id = str(uuid.UUID(str(metadata["workspace_asset_id"])))
        except (ValueError, TypeError, AttributeError):
            pass
        else:
            locator = {"workspaceAssetId": workspace_asset_id, **locator}
    return replace(raw, original_name=original_name, ocr_text=ocr_text, source_locator=locator)


_TEXT_SOURCE_SUFFIXES = frozenset({".md", ".markdown", ".txt"})
_MARKDOWN_IMAGE = re.compile(
    r'!\[(?P<caption>(?:\\.|[^\]\\])*)\](?:'
    r'\(\s*(?:<(?P<angle>[^>]+)>|(?P<inline>[^\s()]+(?:\([^\s()]*\)[^\s()]*)*))(?:\s+["\'][^"\']*["\'])?\s*\)'
    r'|\[(?P<reference>[^\]]*)\])?(?![\[(])'
)
_MARKDOWN_DEFINITION = re.compile(
    r'^[ \t]{0,3}\[([^\]]+)\]:[ \t]*(?:<([^>]+)>|([^\s]+))(?:[ \t]+.*)?$', re.MULTILINE
)


def _image_reference_keys(value: str) -> tuple[str, ...]:
    """Lexical upload aliases only; never open or fetch an image target."""

    value = unquote(value.strip()).replace("\\", "/")
    if value.startswith("//") or re.match(r"^[a-z][a-z0-9+.-]*://", value, re.I) and not value.lower().startswith(("asset://", "workspace-asset://")):
        return ()
    if re.match(r"^(?:https?|file|data|ftp):", value, re.I):
        return ()
    value = re.sub(r"^(?:asset|workspace-asset):(?:/{2})?", "", value, flags=re.I)
    basename = value.rsplit("/", 1)[-1]
    keys = [value.casefold(), basename.casefold()]
    if Path(basename).suffix:
        keys.append(Path(basename).stem.casefold())
    return tuple(dict.fromkeys(key for key in keys if key))


def _attach_text_image_contexts(
    images: list[_RawImage],
    upload_paths: Sequence[Path],
    upload_metadata: Mapping[str, dict],
    presentation_id: uuid.UUID,
    report: list[dict] | None,
) -> list[_RawImage]:
    aliases: dict[str, set[int]] = defaultdict(set)
    for index, image in enumerate(images):
        if not image.source_locator or image.source_locator.get("sourceFormat") != "image":
            continue
        metadata = upload_metadata.get(str(image.source_path), {})
        identifiers = [image.original_name or "", image.source_path.name if image.source_path else "",
                       image.digest, f"sha256:{image.digest}", str(source_image_asset_id(presentation_id, image.digest))]
        identifiers.extend(str(metadata.get(key) or "") for key in ("asset_id", "workspace_asset_id"))
        for identifier in identifiers:
            for key in _image_reference_keys(identifier):
                aliases[key].add(index)
    contexts: dict[int, list[dict]] = defaultdict(list)

    def diagnose(path: Path, block_index: int | None, status: str) -> None:
        if report is not None:
            report.append({"original_name": upload_metadata.get(str(path), {}).get("original_name") or path.name,
                           "block_index": block_index, "status": status})

    for upload_path in upload_paths:
        path = Path(upload_path)
        if path.suffix.lower() not in _TEXT_SOURCE_SUFFIXES:
            continue
        try:
            if path.stat().st_size > MAX_PPTX_BYTES:
                raise ValueError("document_byte_budget_exceeded")
            with path.open("rb") as source:
                payload = source.read(MAX_PPTX_BYTES + 1)
            if len(payload) > MAX_PPTX_BYTES:
                raise ValueError("document_byte_budget_exceeded")
            try:
                text = payload.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = payload.decode("cp949")
        except (OSError, UnicodeDecodeError, ValueError) as error:
            diagnose(path, None, str(error) if isinstance(error, ValueError) and not isinstance(error, UnicodeDecodeError)
                     else "text_source_read_failed")
            continue
        digest = sha256_hex(payload)
        definitions: dict[str, set[str]] = defaultdict(set)
        definition_lines = []
        in_fence = False
        for line in text.splitlines():
            if re.match(r"^\s*(?:```|~~~)", line):
                in_fence = not in_fence
            elif not in_fence:
                definition_lines.append(line)
        for definition in _MARKDOWN_DEFINITION.finditer("\n".join(definition_lines)):
            label = " ".join(definition.group(1).split()).casefold()
            definitions[label].add(definition.group(2) or definition.group(3))
        local_text = ""
        local_anchor = 0
        reference_order = 0
        in_fence = False
        for block_index, block in enumerate(re.split(r"\n\s*\n", text)):
            visible_lines = []
            for line in block.splitlines():
                if re.match(r"^\s*(?:```|~~~)", line):
                    in_fence = not in_fence
                elif not in_fence:
                    visible_lines.append(line)
            block = "\n".join(visible_lines)
            block = _MARKDOWN_DEFINITION.sub("", block).strip()
            if not block:
                continue
            references = []
            matches = list(_MARKDOWN_IMAGE.finditer(block))
            for match in matches:
                target = match.group("angle") or match.group("inline")
                if target is None:
                    label = " ".join((match.group("reference") or match.group("caption")).split()).casefold()
                    targets = definitions.get(label, set())
                    if len(targets) != 1:
                        diagnose(path, block_index, "image_reference_ambiguous" if targets else "image_reference_not_uploaded")
                        continue
                    target = next(iter(targets))
                references.append((target, re.sub(r"\\(.)", r"\1", match.group("caption"))))
            remainder = _MARKDOWN_IMAGE.sub("", block)
            if path.suffix.lower() == ".txt" and not references:
                remaining_lines = []
                for line in remainder.splitlines():
                    literal = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", "", line).strip().strip("`")
                    if re.fullmatch(r"[^\n]+\.(?:png|jpe?g|webp)", literal, re.I):
                        references.append((literal, ""))
                    else:
                        remaining_lines.append(line)
                remainder = "\n".join(remaining_lines)
            explanation = " ".join(
                re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", "", line).strip()
                for line in remainder.splitlines()
            ).strip()
            if explanation:
                local_anchor = block_index
                local_text = "" if re.match(r"^\s*#{1,6}\s", explanation) else explanation
            for image_order, (target, caption) in enumerate(references):
                reference_order += 1
                matched = set()
                for key in _image_reference_keys(target):
                    if key in aliases:
                        matched = aliases[key]
                        break
                if len(matched) != 1:
                    diagnose(path, block_index, "image_reference_ambiguous" if matched else "image_reference_not_uploaded")
                    continue
                index = next(iter(matched))
                group = _content_group(
                    digest,
                    f"block/{local_anchor}" if local_text else f"block/{block_index}/image/{image_order}",
                    "local_explanation" if local_text else "caption" if caption else "unresolved",
                    local_text,
                    caption,
                    reference_order,
                )
                group.update(contextDocumentSha256=digest, contextSourceFormat=path.suffix.lower().lstrip("."),
                             contextBlockPath=f"block/{local_anchor if local_text else block_index}")
                contexts[index].append(group)
    affected_group_ids = {
        group["groupId"]
        for groups in contexts.values()
        if len({item["groupId"] for item in groups}) > 1
        for group in groups
    }
    result = list(images)
    for index, groups in contexts.items():
        affected = sorted({group["groupId"] for group in groups} & affected_group_ids)
        if affected:
            if report is not None:
                report.append({"original_name": images[index].original_name, "status": "text_image_context_ambiguous",
                               "affected_group_ids": affected})
            locator = {**images[index].source_locator, "contentGroup": {
                **images[index].source_locator["contentGroup"], "referenceStatus": "ambiguous",
                "affectedGroupIds": affected,
            }}
        else:
            group = {**groups[0], "captionText": " ".join(dict.fromkeys(item["captionText"] for item in groups))[:600]}
            locator = {**images[index].source_locator, "contentGroup": group}
        result[index] = replace(images[index], source_locator=locator)
    return result


async def register_manifest_image_occurrences(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    occurrences: Sequence[ManifestImageOccurrence],
    source_identity_presentation_id: uuid.UUID | None = None,
) -> list[SourceImageCandidate]:
    """Persist every admitted manifest occurrence without relevance filtering.

    This path is intentionally separate from ordinary authored source-image
    reuse.  A source-content reflow already made the preservation decision in
    its immutable manifest, so minimum-size, furniture and digest dedup filters
    would violate that decision.
    """

    raw_images: list[_RawImage] = []
    seen_occurrence_ids: set[str] = set()
    for occurrence in occurrences:
        if not occurrence.occurrence_id or occurrence.occurrence_id in seen_occurrence_ids:
            raise ValueError("source_content_image_occurrence_invalid")
        seen_occurrence_ids.add(occurrence.occurrence_id)
        mime_type = occurrence.mime_type.strip().lower()
        if mime_type not in SUPPORTED_SOURCE_IMAGE_MIME_TYPES:
            raise ValueError("source_content_image_format_not_supported")
        payload = bytes(occurrence.payload)
        expected = occurrence.content_digest.removeprefix("sha256:").lower()
        actual = sha256_hex(payload)
        if len(expected) != 64 or actual != expected:
            raise ValueError("source_content_image_digest_mismatch")
        try:
            with Image.open(BytesIO(payload)) as image:
                image.load()
                width, height = image.size
                measured_mime = Image.MIME.get((image.format or "").upper(), "")
        except (OSError, ValueError) as error:
            raise ValueError("source_content_image_invalid") from error
        if measured_mime != mime_type or width <= 0 or height <= 0:
            raise ValueError("source_content_image_invalid")
        raw_images.append(
            _RawImage(
                mime_type=mime_type,
                width=width,
                height=height,
                digest=actual,
                payload=payload,
                source_slide_index=occurrence.source_slide_index,
                source_heading=occurrence.source_heading,
                source_text=occurrence.source_text,
                occurrence_id=occurrence.occurrence_id,
            )
        )

    storage_dir = Path(await asyncio.to_thread(get_writable_path, f"{_STORAGE_ROOT}/{presentation_id}"))
    registered = [
        await _persist_raw_image(
            session,
            storage_dir=storage_dir,
            presentation_id=presentation_id,
            raw=raw,
            source_identity_presentation_id=source_identity_presentation_id,
        )
        for raw in raw_images
    ]
    await session.flush()
    return registered


async def _collect_raw_images(
    upload_paths: Sequence[Path],
    *,
    include_document_images: bool = False,
    registration_report: list[dict] | None = None,
) -> list[_RawImage]:
    """Expand every upload into the pictures it contributes, in upload order."""

    collected: list[_RawImage] = []
    for upload_path in upload_paths:
        path = Path(upload_path)
        if include_document_images and path.suffix.lower() in {".docx", ".pdf"}:
            result = await asyncio.to_thread(extract_document_images, path)
            if registration_report is not None:
                registration_report.append(
                    {
                        "original_name": path.name,
                        "status": result.status,
                        "coverage": result.coverage,
                        "diagnostics": result.diagnostics,
                        "asset_count": len(result.assets),
                        "occurrence_count": len(result.occurrences),
                    }
                )
            collected.extend(
                _RawImage(
                    mime_type=item.mime_type,
                    width=item.width,
                    height=item.height,
                    digest=item.digest,
                    payload=item.payload,
                    source_path=path,
                    source_slide_index=item.locator.get("pageIndex"),
                    source_text=item.source_text,
                    occurrence_id=item.occurrence_id,
                    source_locator=item.locator,
                )
                for item in result.occurrences
            )
            continue
        if path.suffix.lower() in _SOURCE_DECK_SUFFIXES:
            # Zip reading plus per-picture decoding is blocking work; keep it
            # off the event loop that is driving the rest of generation.
            extracted = await asyncio.to_thread(extract_pptx_images, path)
            document_digest = await asyncio.to_thread(_file_digest, path) if include_document_images else None
            collected.extend(
                _RawImage(
                    mime_type=image.mime_type,
                    width=image.width,
                    height=image.height,
                    digest=image.digest,
                    payload=image.payload,
                    source_slide_index=image.source_slide_index,
                    source_heading=image.source_heading,
                    source_text=image.source_text,
                    slide_coverage=image.slide_coverage,
                    source_path=path if include_document_images else None,
                    occurrence_id=(
                        sha256_hex(f"{document_digest}:slide/{image.source_slide_index}/image/{index}".encode())
                        if include_document_images
                        else None
                    ),
                    source_locator=(
                        {
                            "sourceDocumentSha256": document_digest,
                            "sourceFormat": "pptx",
                            "pageIndex": image.source_slide_index,
                            "objectLocator": f"slide/{image.source_slide_index}/image/{index}",
                            "extractionKind": "embedded_raster",
                            "contentGroup": image.content_group,
                        }
                        if include_document_images
                        else None
                    ),
                )
                for index, image in enumerate(extracted)
            )
            continue
        raw = await asyncio.to_thread(_probe_uploaded_image, path)
        if raw is not None:
            collected.append(raw)
    return collected


def _probe_uploaded_image(path: Path) -> Optional[_RawImage]:
    """Describe one directly uploaded file, or ``None`` if it is not a picture."""

    try:
        with Image.open(path) as image:
            # Read the format from the bytes rather than the extension: a
            # mislabelled upload must be filtered on what it actually is.
            mime_type = Image.MIME.get((image.format or "").upper(), "")
            width, height = image.size
    except OSError:
        # A non-image upload (a PDF, a stray text file) simply contributes no
        # candidate; generation must not fail over one.
        return None

    return _RawImage(
        mime_type=mime_type,
        width=width,
        height=height,
        digest=_file_digest(path),
        source_path=path,
    )


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(_DIGEST_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _apply_source_image_filters(collected: Sequence[_RawImage]) -> list[_RawImage]:
    """Reduce the raw pictures to the ones worth offering to the slide author."""

    supported = [raw for raw in collected if raw.mime_type in SUPPORTED_SOURCE_IMAGE_MIME_TYPES]
    large_enough = [raw for raw in supported if min(raw.width, raw.height) >= MIN_SOURCE_IMAGE_EDGE_PX]
    # A picture covering its whole slide is that slide's backplate. Decks this
    # tool exports through the authored-hybrid path are built that way, so a
    # deck rebuilt from such an export would otherwise offer the author a
    # photograph of an entire old slide as content for a new one.
    content_sized = [
        raw for raw in large_enough if raw.slide_coverage is None or raw.slide_coverage < FULL_SLIDE_COVERAGE_THRESHOLD
    ]

    first_by_digest: dict[str, _RawImage] = {}
    slides_by_digest: dict[str, set[int]] = defaultdict(set)
    for raw in content_sized:
        first_by_digest.setdefault(raw.digest, raw)
        if raw.source_slide_index is not None:
            slides_by_digest[raw.digest].add(raw.source_slide_index)

    # Insertion order carries the deterministic result order for free.
    return [
        raw
        for digest, raw in first_by_digest.items()
        # Repeated across slides means chrome the master contract already owns
        # (a logo, a watermark), never content to re-place on a new slide.
        if len(slides_by_digest[digest]) < FURNITURE_REPEAT_THRESHOLD
    ]


#: Namespace for deriving a source image's asset id from the deck it belongs to
#: and the bytes it carries. Fixed forever: changing it renames every id and
#: strands the slide HTML of decks generated before the change.
_SOURCE_IMAGE_NAMESPACE = uuid.UUID("2f8a6d1e-6c3b-5f47-9d21-0a7c4b8e5d10")


def source_image_asset_id(presentation_id: uuid.UUID, digest: str) -> uuid.UUID:
    """Return the stable asset id for one picture within one deck.

    Deriving the id from (deck, bytes) rather than allocating a random one makes
    registration idempotent across the durable retries this pipeline performs.
    """
    return uuid.uuid5(_SOURCE_IMAGE_NAMESPACE, f"{presentation_id}:{digest}")


def source_image_occurrence_asset_id(
    presentation_id: uuid.UUID,
    occurrence_id: str,
    digest: str,
) -> uuid.UUID:
    """Stable per-occurrence identity for strict source-content reflow."""

    return uuid.uuid5(
        _SOURCE_IMAGE_NAMESPACE,
        f"{presentation_id}:occurrence:{occurrence_id}:{digest}",
    )


_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def source_image_storage_directory() -> Path:
    """Return the root every deck's registered pictures are materialised under."""

    return Path(get_writable_path(_STORAGE_ROOT))


def registered_source_image_locator(
    asset_id: uuid.UUID,
    extras: object,
) -> tuple[uuid.UUID, str, str] | None:
    """Return ``(presentation_id, file_name, digest)`` for a registry-written row.

    The answer is derived only from what :func:`_persist_raw_image` records:
    the deterministic asset id must re-derive from the row's own deck id,
    digest and occurrence id, and the file name is the digest plus the suffix
    of the recorded MIME type.  Any row this module did not write -- or whose
    metadata no longer matches its id -- yields ``None``.
    """

    if not isinstance(extras, Mapping):
        return None
    try:
        presentation_id = uuid.UUID(str(extras.get("presentation_id")))
    except (ValueError, TypeError, AttributeError):
        return None
    digest = extras.get("digest")
    if not isinstance(digest, str) or not _SHA256_HEX.match(digest):
        return None
    mime_type = extras.get("mime_type")
    suffix = _MIME_TO_SUFFIX.get(mime_type) if isinstance(mime_type, str) else None
    if suffix is None:
        return None
    occurrence_id = extras.get("source_occurrence_id")
    if occurrence_id is None:
        expected_id = source_image_asset_id(presentation_id, digest)
    elif isinstance(occurrence_id, str) and occurrence_id:
        expected_id = source_image_occurrence_asset_id(presentation_id, occurrence_id, digest)
    else:
        return None
    if expected_id != asset_id:
        return None
    return presentation_id, f"{digest}{suffix}", digest


async def _persist_raw_image(
    session: AsyncSession,
    *,
    storage_dir: Path,
    presentation_id: uuid.UUID,
    raw: _RawImage,
    source_identity_presentation_id: uuid.UUID | None = None,
) -> SourceImageCandidate:
    """Materialise one picture in deck storage and record it as an asset."""

    suffix = _MIME_TO_SUFFIX[raw.mime_type]
    stored_path = storage_dir / f"{raw.digest}{suffix}"
    asset_id = (
        source_image_occurrence_asset_id(
            source_identity_presentation_id or presentation_id,
            raw.occurrence_id,
            raw.digest,
        )
        if raw.occurrence_id is not None
        else source_image_asset_id(presentation_id, raw.digest)
    )
    # A generation attempt is durable and resumable: the pipeline replays
    # checkpointed slide HTML that already names an asset id, while this
    # function runs again from the top. A fresh random id per attempt would
    # leave that HTML pointing at an identifier the retry never offers, so the
    # picture would be dropped and a row leaked on every attempt.
    asset = await session.get(ImageAsset, asset_id)
    if source_identity_presentation_id is not None:
        from utils.owner_context import get_current_owner_id

        owner = get_current_owner_id()
        extras = (asset.extras or {}) if asset is not None else {}
        expected = dict(presentation_id=str(source_identity_presentation_id), digest=raw.digest,
            mime_type=raw.mime_type, width=raw.width, height=raw.height,
            source_slide_index=raw.source_slide_index, source_occurrence_id=raw.occurrence_id)
        if (asset is None or owner is None or asset.owner_id != owner or not asset.is_uploaded
                or raw.occurrence_id is None or any(extras.get(key) != value for key, value in expected.items())):
            raise ValueError("source_content_image_identity_unproven")
        try:
            original_digest = sha256_hex(await asyncio.to_thread(Path(asset.path).read_bytes))
        except OSError as error:
            raise ValueError("source_content_image_identity_unproven") from error
        if original_digest != raw.digest:
            raise ValueError("source_content_image_digest_mismatch")
    # Only child storage is materialized. Reusing an inherited identity never
    # changes its original ImageAsset row, path, metadata, or source bytes.
    if not await asyncio.to_thread(stored_path.exists):
        await asyncio.to_thread(_write_stored_bytes, raw, stored_path)
    if asset is None:
        asset = ImageAsset(
            id=asset_id,
            path=str(stored_path),
            is_uploaded=True,
            extras={
                "presentation_id": str(presentation_id),
                "digest": raw.digest,
                "mime_type": raw.mime_type,
                "width": raw.width,
                "height": raw.height,
                "source_slide_index": raw.source_slide_index,
                "source_heading": raw.source_heading,
                "source_text": raw.source_text,
                "source_occurrence_id": raw.occurrence_id,
                "source_locator": raw.source_locator,
            },
        )
        session.add(asset)

    if raw.occurrence_id is None:
        asset.extras = {**(asset.extras or {}), "original_name": raw.original_name, "ocr_text": raw.ocr_text}

    return SourceImageCandidate(
        asset_id=str(asset_id),
        path=stored_path,
        mime_type=raw.mime_type,
        width=raw.width,
        height=raw.height,
        digest=raw.digest,
        source_slide_index=raw.source_slide_index,
        source_heading=raw.source_heading,
        source_text=raw.source_text,
        original_name=raw.original_name,
        ocr_text=raw.ocr_text,
        occurrence_id=raw.occurrence_id,
        source_locator=raw.source_locator,
    )


def _write_stored_bytes(raw: _RawImage, stored_path: Path) -> None:
    if raw.payload is not None:
        stored_path.write_bytes(raw.payload)
        return
    if raw.source_path is None:
        raise ValueError("source image carries neither payload nor source path")
    shutil.copyfile(raw.source_path, stored_path)
