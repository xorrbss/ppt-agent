"""Pre-export evidence for the svg export identity (``svg-native`` output mode).

The five ``ExportEvidenceDigests`` are part of the single-flight join key
(``CanonicalExportIdentity.export_key()``), so every one of them is built here
from inputs known *before* the worker runs; nothing the export produces feeds
them. That is what lets a duplicate submission of the same revision and format
find the completed identity and reuse its bytes instead of re-exporting:

* ``scene``        -- the svg sources, speaker notes, canvas, the per-slide
  source asset manifests and the source-preservation receipts: everything the
  worker draws from.
* ``semantic``     -- ``scene`` plus the per-slide NativeObjects sidecars when
  the export carries them (PPTX only -- the vector PDF prints the drawn groups
  and has no native objects, so a PDF never binds them). Without sidecars it
  equals ``scene``: svg has no semantic pass separate from its scene.
* ``quality``      -- the quality evidence the receipt is judged from that
  exists before export: each slide's stored ``svgReceipt.quality``, the deck's
  vision advisory record and the requested quality policy.
* ``exporter``     -- converter version and digest plus the svg contract version.
* ``fontManifest`` -- the trusted font registry (``services.svg_trusted_fonts``)
  and whether this export embeds fonts into the PPTX.

The post-export quality receipt (``services.svg_export_quality``) is separate:
it carries the artifact digest and the worker's readback, and its own digest is
the publication's ``public_quality_digest``, never an identity input. The
receipt records these identity digests at ``evidenceDigests``.
"""

from __future__ import annotations

import json
from utils.hashing import sha256_hex

#: Stage version for an svg slide checkpoint (the slide's svg source bytes).
SVG_SLIDE_CHECKPOINT_STAGE = "svg-slide/v1"


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return sha256_hex(encoded)


def svg_slide_quality(slide):
    """One slide's stored quality evidence (``svgReceipt.quality``), as the receipt reads it."""

    return ((slide.content or {}).get("svgReceipt") or {}).get("quality")


def svg_quality_inputs(*, slides, advisory, quality_policy) -> dict:
    """The quality evidence an svg export is judged from, as known pre-export."""

    return {"policy": quality_policy, "advisory": advisory, "slides": [svg_slide_quality(slide) for slide in slides]}


def svg_slide_checkpoint_digests(*, index, svg_sha256, note, asset_manifest, native_object,
                                 source_preservation, quality) -> tuple[str, str]:
    """``(input_digest, validation_digest)`` for one svg slide checkpoint.

    Every value here is also bound by a deck-level digest (the svg, note, asset
    manifest and source-preservation receipt by ``scene``, the sidecar by
    ``semantic``, the slide quality by ``quality``). The unique identity key
    does not include the canonical digest, so a slide input that the five
    digests did not also cover would turn a legitimate re-export into an
    ``export.identity_source_conflict``.
    """

    input_digest = _digest({
        "svgDigest": svg_sha256, "notes": note,
        "assetManifest": asset_manifest, "nativeObjects": native_object,
    })
    validation_digest = _digest({
        "slideIndex": index, "inputDigest": input_digest,
        "sourcePreservation": source_preservation, "quality": quality,
    })
    return input_digest, validation_digest


def build_svg_export_evidence_digests(
    *,
    svgs,
    notes,
    canvas,
    source_assets=None,
    source_preservation=None,
    native_objects=None,
    quality_inputs: dict,
    converter_version: str,
    converter_digest: str,
    contract_version: str,
    font_embedding: bool = False,
):
    """Build the five identity evidence digests for one svg export request.

    ``native_objects`` is the bundle's per-slide ``nativeObjects`` entry list
    for a PPTX export, or ``None`` (no sidecar on any slide, or a PDF).
    ``quality_inputs`` comes from :func:`svg_quality_inputs`.
    """
    from services.public_export_identity import ExportEvidenceDigests
    from services.svg_trusted_fonts import read_trusted_pretendard_faces

    scene_digest = _digest({
        "svgs": list(svgs), "notes": list(notes), "canvas": canvas,
        "assets": source_assets, "sourcePreservation": source_preservation,
    })
    font_manifest_digest = _digest({
        "family": "Pretendard",
        "faces": [{"weight": weight, "digest": digest} for weight, digest, _payload in read_trusted_pretendard_faces()],
        "fontEmbedding": bool(font_embedding),
    })
    return ExportEvidenceDigests(
        scene_digest=scene_digest,
        semantic_digest=scene_digest if native_objects is None else _digest({
            "scene": scene_digest, "nativeObjects": list(native_objects),
        }),
        quality_digest=_digest(quality_inputs),
        exporter_digest=_digest({"converterVersion": converter_version, "converterDigest": converter_digest,
                                 "contractVersion": contract_version}),
        font_manifest_digest=font_manifest_digest,
    )


__all__ = [
    "SVG_SLIDE_CHECKPOINT_STAGE",
    "build_svg_export_evidence_digests",
    "svg_quality_inputs",
    "svg_slide_checkpoint_digests",
    "svg_slide_quality",
]
