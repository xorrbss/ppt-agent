"""Validate fresh exporter font observations against durable original runs."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from services.authored_local_fit_policy import (
    POLICY_REVISION,
    font_size_allowed,
    pt_per_css_px,
)
from utils.hashing import sha256_hex, sha256_prefixed


def _reject(reason: str) -> None:
    raise ValueError("authored_local_fit_export:" + reason)


class RasterFragmentVerificationError(ValueError):
    """Content-free, bounded evidence for a rejected packaged fragment."""

    def __init__(self, reason: str, diagnostics: Mapping[str, Any]):
        self.diagnostics = dict(diagnostics)
        super().__init__("authored_local_fit_export:" + reason)


def load_export_evidence(
    path: str | Path | bytes,
    payload: bytes,
    receipts: Sequence[Mapping[str, Any]],
    supplied: Mapping[str, Any] | None = None,
) -> Mapping[str, Any] | None:
    evidence = supplied
    if evidence is None and not isinstance(path, bytes):
        sidecar = Path(str(path) + ".local-fit.json")
        if sidecar.is_file():
            evidence = json.loads(sidecar.read_text(encoding="utf-8"))
    if evidence is None:
        return None
    if (
        not isinstance(evidence, Mapping)
        or evidence.get("schemaVersion") != "authored-local-fit-export-evidence/v1"
        or evidence.get("pptxDigest") != sha256_prefixed(payload)
        or not evidence.get("sourceDigest")
    ):
        _reject("export_evidence_identity_mismatch")
    slides = evidence.get("slides")
    if not isinstance(slides, list) or len(slides) > len(receipts):
        _reject("slide_count_mismatch")
    ordered: list[Mapping[str, Any] | None] = [None] * len(receipts)
    for slide in slides:
        number = slide.get("slideNumber")
        if (
            not isinstance(number, int)
            or isinstance(number, bool)
            or not 1 <= number <= len(receipts)
            or ordered[number - 1] is not None
        ):
            _reject("slide_count_mismatch")
        if slide.get("htmlDigest") != receipts[number - 1].get("finalHtmlDigest"):
            _reject("export_source_identity_mismatch")
        ordered[number - 1] = slide
    return {**evidence, "slides": ordered}


def _key(run: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(
        run.get(field) for field in ("owner_qid", "text_node_ordinal", "start_offset", "end_offset", "content_digest")
    )


def validate_observation(
    observation: Any,
    runs: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    *,
    policy_revision: str = POLICY_REVISION,
    owners: Sequence[str] | None = None,
    enforce_font_policy: bool = True,
) -> None:
    if (
        not isinstance(observation, Mapping)
        or observation.get("fonts_ready") is not True
        or observation.get("unowned_text_count") != 0
    ):
        _reject("unverifiable_font_size")
    observed = observation.get("runs")
    viewport = observation.get("viewport", {})
    if not isinstance(observed, list):
        _reject("unverifiable_font_size")
    conversion = pt_per_css_px(
        width_pt=manifest["width_pt"],
        height_pt=manifest["height_pt"],
        viewport_width=viewport.get("width", 0),
        viewport_height=viewport.get("height", 0),
    )
    expected = {_key(run): run for run in runs}
    measured = {_key(run): run for run in observed}
    if len(measured) != len(observed) or set(measured) != set(expected):
        _reject("export_run_identity_mismatch")
    # The observation precedes raster suppression. Validate all source runs;
    # ownership then binds the visible subset to the exact packaged image.
    for key, original in expected.items():
        current = measured[key]
        if enforce_font_policy and current.get("font_loaded") is not True:
            _reject("unverifiable_font_size")
        try:
            font = float(current["font_px"])
            scale = float(current["effective_text_scale"])
        except (KeyError, TypeError, ValueError):
            _reject("unverifiable_font_size")
        if not math.isfinite(font) or not math.isfinite(scale) or font <= 0 or scale <= 0:
            _reject("unverifiable_font_size")
        if enforce_font_policy and not font_size_allowed(
            float(original["original_pt"]),
            font * scale * conversion,
            policy_revision=policy_revision,
        ):
            _reject("font_floor_or_shrink_limit")
    if owners is not None:
        # Non-text owners (backgrounds, images, shapes) legitimately occur too;
        # every text owner's raster/native disposition is checked by the
        # exporter's existing native ownership receipt, retained by this gate.
        if not isinstance(owners, (list, tuple)) or not all(isinstance(qid, str) for qid in owners):
            _reject("raster_owner_unavailable")


def validate_raster(
    digest: str,
    evidence: Mapping[str, Any] | None,
    runs: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    *,
    policy_revision: str = POLICY_REVISION,
    enforce_font_policy: bool = True,
) -> None:
    if evidence is None:
        _reject("raster_font_evidence_unavailable")
    candidates = [raster for raster in evidence.get("raster", ()) if raster.get("pngDigest") == digest]
    if len(candidates) != 1:
        _reject("raster_evidence_mismatch")
    raster = candidates[0]
    placement = raster.get("placement", {})
    if placement != {"x": 0, "y": 0, "widthPt": manifest["width_pt"], "heightPt": manifest["height_pt"]}:
        _reject("image_placement_mismatch")
    if enforce_font_policy:
        validate_observation(
            raster.get("observation"),
            runs,
            manifest,
            policy_revision=policy_revision,
            owners=raster.get("ownerQids"),
            enforce_font_policy=enforce_font_policy,
        )


def validate_local_image(
    digest: str,
    actual: Mapping[str, float],
    evidence: Mapping[str, Any] | None,
    runs: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    *,
    policy_revision: str = POLICY_REVISION,
    enforce_font_policy: bool = True,
) -> None:
    if evidence is None:
        _reject("raster_font_evidence_unavailable")
    candidates = []
    for candidate in evidence.get("local_images", ()):
        if candidate.get("pngDigest") != digest:
            continue
        try:
            matches = all(
                abs(float(candidate["placement"][key]) - actual[key]) <= 1 / 12700
                for key in ("x", "y", "widthPt", "heightPt")
            )
        except (KeyError, TypeError, ValueError):
            matches = False
        if matches:
            candidates.append(candidate)
    if len(candidates) != 1:
        _reject("local_image_evidence_mismatch")
    item = candidates[0]
    declared, source = item.get("placement", {}), item.get("sourceBoundsPx", {})
    conversion = pt_per_css_px(width_pt=manifest["width_pt"], height_pt=manifest["height_pt"])
    for target_key, source_key in (("x", "x"), ("y", "y"), ("widthPt", "width"), ("heightPt", "height")):
        try:
            output, declared_value, source_value = (
                float(actual[target_key]),
                float(declared[target_key]),
                float(source[source_key]) * conversion,
            )
        except (KeyError, TypeError, ValueError):
            _reject("image_placement_mismatch")
        if (
            not all(math.isfinite(value) for value in (output, declared_value, source_value))
            or abs(output - declared_value) > 1 / 12700
            or abs(output - source_value) > 1 / 12700
        ):
            _reject("image_placement_mismatch")
    if enforce_font_policy:
        validate_observation(
            item.get("observation"),
            runs,
            manifest,
            policy_revision=policy_revision,
            owners=item.get("ownerQids"),
            enforce_font_policy=enforce_font_policy,
        )


def validate_raster_fragments(
    images: Sequence[tuple[str, Mapping[str, float], bytes]],
    evidence: Mapping[str, Any] | None,
    runs: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    *,
    policy_revision: str = POLICY_REVISION,
    enforce_font_policy: bool = True,
) -> set[int]:
    """Bind every declared crop once to packaged bytes and its physical rect."""
    import struct

    matched: set[int] = set()
    # Shared-edge rounding can differ by exactly one EMU in an extent;
    # subtraction in point units must not turn that boundary into a rejection.
    placement_tolerance_pt = 1 / 12700 + 1e-9
    for band_index, band in enumerate((evidence or {}).get("raster", ())):
        if band.get("schemaVersion") != "authored-raster-band/v2":
            continue
        width, height = band.get("captureWidthPx"), band.get("captureHeightPx")
        if (
            type(width) is not int
            or type(height) is not int
            or min(width, height) <= 0
            or width * height > 32_000_000
            or not isinstance(band.get("sourceDigest"), str)
            or band.get("sourceDigest") != band.get("pngDigest")
        ):
            _reject("raster_fragment_identity_mismatch")
        fragments = band.get("fragments")
        if not isinstance(fragments, list) or len(fragments) > 128:
            _reject("raster_fragment_limit_invalid")
        rects: list[Mapping[str, int]] = []
        ids: set[str] = set()
        for order, fragment in enumerate(fragments):
            rect = fragment.get("rect", {})
            identifier = fragment.get("id")
            if (
                not isinstance(identifier, str)
                or not identifier
                or identifier in ids
                or fragment.get("order") != order
                or any(type(rect.get(k)) is not int for k in ("x", "y", "width", "height"))
            ):
                _reject("raster_fragment_rect_invalid")
            ids.add(identifier)
            x, y, w, h = (rect[k] for k in ("x", "y", "width", "height"))
            if (
                min(x, y) < 0
                or min(w, h) <= 0
                or x + w > width
                or y + h > height
                or any(
                    x < r["x"] + r["width"] and r["x"] < x + w and y < r["y"] + r["height"] and r["y"] < y + h
                    for r in rects
                )
            ):
                _reject("raster_fragment_rect_invalid")
            rects.append(rect)
            expected = {
                "x": x / width * manifest["width_pt"],
                "y": y / height * manifest["height_pt"],
                "widthPt": w / width * manifest["width_pt"],
                "heightPt": h / height * manifest["height_pt"],
            }
            declared = fragment.get("placement", {})
            try:
                valid = all(
                    math.isfinite(float(declared[k])) and abs(float(declared[k]) - v) <= 1 / 12700
                    for k, v in expected.items()
                )
            except (KeyError, TypeError, ValueError):
                valid = False
            if not valid:
                _reject("raster_fragment_placement_mismatch")

            def placement_matches(placement: Mapping[str, float], expected=expected) -> bool:
                try:
                    return all(
                        math.isfinite(float(placement[k])) and abs(float(placement[k]) - v) <= placement_tolerance_pt
                        for k, v in expected.items()
                    )
                except (KeyError, TypeError, ValueError):
                    return False

            available = [index for index in range(len(images)) if index not in matched]
            digest_indexes = [index for index in available if images[index][0] == fragment.get("pngDigest")]
            placement_indexes = [index for index in available if placement_matches(images[index][1])]
            candidates = [(index, images[index][2]) for index in digest_indexes if index in placement_indexes]
            if not candidates:
                # A known image at another rectangle is a placement defect; an
                # image at this rectangle with other bytes is a digest defect.
                # Neither condition proves a missing image, and none authorizes
                # moving/inserting pictures without their stacking provenance.
                reason = (
                    "raster_fragment_placement_mismatch"
                    if digest_indexes
                    else "raster_fragment_digest_mismatch"
                    if placement_indexes
                    else "raster_fragment_missing"
                )
                candidate_indexes = (digest_indexes or placement_indexes)[:4]
                diagnostic_candidates = []
                for candidate_index in candidate_indexes:
                    _, actual, payload = images[candidate_index]
                    diagnostic_candidates.append(
                        {
                            "imageIndex": candidate_index,
                            "digestSha256": sha256_hex(payload),
                            "placementPt": {
                                key: float(actual[key])
                                for key in expected
                                if isinstance(actual.get(key), (int, float)) and math.isfinite(actual[key])
                            },
                        }
                    )
                expected_digest = fragment.get("pngDigest", "")
                raise RasterFragmentVerificationError(
                    reason,
                    {
                        "schemaVersion": "raster-fragment-diagnostic/v1",
                        "bandIndex": band_index,
                        "fragmentOrder": order,
                        "fragmentIdSha256": sha256_hex(identifier.encode("utf-8")),
                        "expectedDigest": expected_digest
                        if (
                            isinstance(expected_digest, str)
                            and len(expected_digest) == 71
                            and expected_digest.startswith("sha256:")
                            and all(char in "0123456789abcdef" for char in expected_digest[7:])
                        )
                        else None,
                        "expectedPlacementPt": expected,
                        "placementTolerancePt": placement_tolerance_pt,
                        "packagedImageCount": len(images),
                        "digestMatchCount": len(digest_indexes),
                        "placementMatchCount": len(placement_indexes),
                        "candidates": diagnostic_candidates,
                    },
                )
            index, payload = candidates[0]
            if (
                len(payload) != fragment.get("sizeBytes")
                or len(payload) < 24
                or payload[:8] != b"\x89PNG\r\n\x1a\n"
                or struct.unpack(">II", payload[16:24]) != (w, h)
            ):
                _reject("raster_fragment_bytes_mismatch")
            matched.add(index)
        if enforce_font_policy:
            validate_observation(
                band.get("observation"),
                runs,
                manifest,
                policy_revision=policy_revision,
                owners=band.get("ownerQids"),
                enforce_font_policy=True,
            )
    return matched
