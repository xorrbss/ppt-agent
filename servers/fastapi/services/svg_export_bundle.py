"""The svg export bundle handed to the isolated worker, and its export identity."""
from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import dataclass

from services.svg_contract import SVG_CONTRACT_VERSION
from workers.public_svg_export_worker import (
    CONVERTER_VERSION,
    SVG_EXPORT_SCHEMA,
    SVG_EXPORT_SCHEMA_V2,
    SVG_EXPORT_SCHEMA_V3,
    SVG_EXPORT_SCHEMA_V4,
    converter_digest,
)
from utils.hashing import sha256_hex


def canonical_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value) -> str:
    return sha256_hex(canonical_bytes(value))


@dataclass(slots=True)
class ExportIoCounters:
    """Operation-scoped I/O counters: one instance per export operation (never module state)."""

    cas_reads: int = 0
    cas_bytes_hashed: int = 0
    manifest_encodes: int = 0

    def log(self, logger, phase: str, operation_id) -> None:
        logger.info("svg_export_io phase=%s operation=%s cas_reads=%d cas_bytes_hashed=%d manifest_encodes=%d",
                    phase, operation_id, self.cas_reads, self.cas_bytes_hashed, self.manifest_encodes)


class _FrozenDict(dict):
    """JSON object that rejects mutation; copy/deepcopy/pickle yield a plain mutable dict."""

    __slots__ = ()

    def _deny(self, *args, **kwargs):
        raise TypeError("sealed_manifest_immutable")

    __setitem__ = __delitem__ = __ior__ = clear = pop = popitem = setdefault = update = _deny

    def __reduce__(self):
        return (dict, (dict(self),))

    def __copy__(self):
        return dict(self)

    def __deepcopy__(self, memo):
        return copy.deepcopy(dict(self), memo)


class _FrozenList(list):
    __slots__ = ()

    def _deny(self, *args, **kwargs):
        raise TypeError("sealed_manifest_immutable")

    __setitem__ = __delitem__ = __iadd__ = __imul__ = append = extend = insert = _deny
    clear = pop = remove = sort = reverse = _deny

    def __reduce__(self):
        return (list, (list(self),))

    def __copy__(self):
        return list(self)

    def __deepcopy__(self, memo):
        return copy.deepcopy(list(self), memo)


def _freeze(value):
    if isinstance(value, dict):
        return _FrozenDict({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return _FrozenList([_freeze(item) for item in value])
    return value


@dataclass(frozen=True, slots=True)
class SealedManifestEncoding:
    """Immutable value, canonical bytes and digest of one operation's sealed source manifest.

    The value is deep-frozen when the encoding is made and the bytes are produced from that
    frozen value, so the digest cannot go stale: the operation threads ``encoding.value``
    (not the dict it was built from) through identity, source snapshot and bundle, and each
    consumer reuses the bytes only for that very object.  It is created per export operation
    and passed down that operation's own call chain; the worker never receives it and
    validates the manifest it reads independently.
    """

    value: object
    canonical: bytes
    digest: str
    counters: object = None


def encode_sealed_manifest(value, *, counters=None) -> SealedManifestEncoding:
    frozen = _freeze(value)
    encoded = canonical_bytes(frozen)
    if counters is not None:
        counters.manifest_encodes += 1
    return SealedManifestEncoding(value=frozen, canonical=encoded, digest=sha256_hex(encoded), counters=counters)


async def encode_sealed_manifest_async(value, *, counters=None) -> SealedManifestEncoding:
    """``encode_sealed_manifest`` on a worker thread: freezing and encoding a large manifest is pure CPU."""
    encoding = await asyncio.to_thread(encode_sealed_manifest, value)
    if counters is not None:
        counters.manifest_encodes += 1
    return SealedManifestEncoding(value=encoding.value, canonical=encoding.canonical,
                                  digest=encoding.digest, counters=counters)


def _recomputed(encoding):
    if encoding is not None and encoding.counters is not None:
        encoding.counters.manifest_encodes += 1


def sealed_manifest_digest(value, encoding) -> str:
    """``digest(value)``, reusing the operation's encoding when it is for this very object."""
    if encoding is not None and encoding.value is value:
        return encoding.digest
    _recomputed(encoding)
    return digest(value)


def digest_embedding(outer: dict, key: str, value, encoding) -> str:
    """``digest({**outer, key: value})`` without re-encoding ``value`` when ``encoding`` is its own.

    Canonical JSON sorts keys, so the embedded canonical bytes are spliced at the
    key's sorted position; the result is byte-identical to encoding the whole dict.
    """
    if key in outer:
        raise ValueError("digest_embedding_key_collision")
    if encoding is None or encoding.value is not value:
        _recomputed(encoding)
        return digest({**outer, key: value})
    parts = []
    for name in sorted([*outer, key]):
        body = encoding.canonical if name == key else canonical_bytes(outer[name])
        parts.append(canonical_bytes(name) + b":" + body)
    return sha256_hex(b"{" + b",".join(parts) + b"}")


def _manifest_digests(assets):
    return [(a["sourceManifestDigest"], a["placementManifestDigest"]) if a else None for a in assets]


def build_svg_export_bundle(*, claim, slide_refs, source_digests, notes, canvas, export_format, font_embedding,
                            assets, source_preservation=None, native_objects=None,
                            source_manifest=None, source_manifest_encoding=None) -> dict:
    """Assemble the bundle payload plus its ``canonicalExportIdentity``.

    ``slide_refs`` are the per-slide worker artifact-ref wires, ``source_digests``
    their sha256s. ``native_objects`` is the per-slide ``nativeObjects`` entry
    list (``export_native_wire``) or ``None``; it rides in the bundle and the
    identity only for PPTX -- the vector PDF prints the drawn groups and has no
    native objects, so a PDF bundle never carries them.
    """
    payload = {
        "schemaVersion": SVG_EXPORT_SCHEMA,
        "contractVersion": SVG_CONTRACT_VERSION,
        "converterVersion": CONVERTER_VERSION,
        "converterDigest": converter_digest(),
        "exportFormat": export_format,
        "canvas": canvas,
        "fontEmbedding": bool(font_embedding),
        "slides": [{"slideIndex": i, "artifactRef": ref, "notes": notes[i]} for i, ref in enumerate(slide_refs)],
    }
    payload["canonicalExportIdentity"] = digest({
        "revision": claim.expected_revision, "sources": list(source_digests), "notes": notes,
        "canvas": canvas, "format": export_format, "converter": payload["converterDigest"],
        "contract": SVG_CONTRACT_VERSION, "fontEmbedding": payload["fontEmbedding"],
    })
    if source_preservation is not None:
        payload.update(schemaVersion=(SVG_EXPORT_SCHEMA_V4 if source_manifest is not None else SVG_EXPORT_SCHEMA_V3),
                       contractVersion="svg-static/v2",
                       presentationId=str(claim.presentation_id), sourceRevision=claim.expected_revision)
        for entry, asset, receipt in zip(payload["slides"], assets, source_preservation, strict=True):
            entry["assets"] = asset
            entry["sourcePreservation"] = receipt
        payload["canonicalExportIdentity"] = digest({
            "legacyIdentity": payload["canonicalExportIdentity"],
            "sourcePreservation": [digest(receipt) for receipt in source_preservation],
            "manifestDigests": _manifest_digests(assets),
        })
        if source_manifest is not None:
            from services.svg_export_whole_page_verifier import whole_page_verifier_digest
            payload["sourceManifest"] = source_manifest
            payload["wholePageVerifierDigest"] = whole_page_verifier_digest()
            payload["canonicalExportIdentity"] = digest_embedding({
                "legacyIdentity": payload["canonicalExportIdentity"],
                "wholePageVerifierDigest": payload["wholePageVerifierDigest"]},
                "sourceManifest", source_manifest, source_manifest_encoding)
    elif source_manifest is not None:
        raise ValueError("svg_source_export_manifest_without_receipts")
    elif any(asset is not None for asset in assets):
        payload.update(schemaVersion=SVG_EXPORT_SCHEMA_V2, contractVersion="svg-static/v2",
                       presentationId=str(claim.presentation_id), sourceRevision=claim.expected_revision)
        for entry, asset in zip(payload["slides"], assets, strict=True):
            entry["assets"] = asset
        payload["canonicalExportIdentity"] = digest({"legacyIdentity": payload["canonicalExportIdentity"],
                                                     "manifestDigests": _manifest_digests(assets)})
    if export_format == "pptx" and native_objects is not None:
        payload["nativeObjects"] = list(native_objects)
        payload["canonicalExportIdentity"] = digest({"legacyIdentity": payload["canonicalExportIdentity"],
                                                     "nativeObjects": payload["nativeObjects"]})
    return payload


__all__ = ["build_svg_export_bundle", "digest", "ExportIoCounters", "encode_sealed_manifest",
           "encode_sealed_manifest_async", "sealed_manifest_digest", "digest_embedding"]
