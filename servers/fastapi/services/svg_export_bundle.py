"""The svg export bundle handed to the isolated worker, and its export identity."""
from __future__ import annotations

import json

from services.svg_contract import SVG_CONTRACT_VERSION
from workers.public_svg_export_worker import (
    CONVERTER_VERSION,
    SVG_EXPORT_SCHEMA,
    SVG_EXPORT_SCHEMA_V2,
    SVG_EXPORT_SCHEMA_V3,
    converter_digest,
)
from utils.hashing import sha256_hex


def digest(value) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return sha256_hex(encoded)


def _manifest_digests(assets):
    return [(a["sourceManifestDigest"], a["placementManifestDigest"]) if a else None for a in assets]


def build_svg_export_bundle(*, claim, slide_refs, source_digests, notes, canvas, export_format, font_embedding,
                            assets, source_preservation=None, native_objects=None) -> dict:
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
        payload.update(schemaVersion=SVG_EXPORT_SCHEMA_V3, contractVersion="svg-static/v2",
                       presentationId=str(claim.presentation_id), sourceRevision=claim.expected_revision)
        for entry, asset, receipt in zip(payload["slides"], assets, source_preservation, strict=True):
            entry["assets"] = asset
            entry["sourcePreservation"] = receipt
        payload["canonicalExportIdentity"] = digest({
            "legacyIdentity": payload["canonicalExportIdentity"],
            "sourcePreservation": [digest(receipt) for receipt in source_preservation],
            "manifestDigests": _manifest_digests(assets),
        })
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


__all__ = ["build_svg_export_bundle", "digest"]
