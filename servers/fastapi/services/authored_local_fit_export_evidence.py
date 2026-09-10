"""Validate fresh exporter font observations against durable original runs."""
from __future__ import annotations

import hashlib
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


def _reject(reason: str) -> None:
    raise ValueError("authored_local_fit_export:" + reason)


def load_export_evidence(path: str | Path | bytes, payload: bytes,
                         receipts: Sequence[Mapping[str, Any]],
                         supplied: Mapping[str, Any] | None = None) -> Mapping[str, Any] | None:
    evidence = supplied
    if evidence is None and not isinstance(path, bytes):
        sidecar = Path(str(path) + ".local-fit.json")
        if sidecar.is_file():
            evidence = json.loads(sidecar.read_text(encoding="utf-8"))
    if evidence is None:
        return None
    if (not isinstance(evidence, Mapping)
            or evidence.get("schemaVersion") != "authored-local-fit-export-evidence/v1"
            or evidence.get("pptxDigest") != "sha256:" + hashlib.sha256(payload).hexdigest()
            or not evidence.get("sourceDigest")):
        _reject("export_evidence_identity_mismatch")
    slides = evidence.get("slides")
    if not isinstance(slides, list) or len(slides) > len(receipts):
        _reject("slide_count_mismatch")
    ordered: list[Mapping[str, Any] | None] = [None] * len(receipts)
    for slide in slides:
        number = slide.get("slideNumber")
        if (not isinstance(number, int) or isinstance(number, bool)
                or not 1 <= number <= len(receipts) or ordered[number - 1] is not None):
            _reject("slide_count_mismatch")
        if slide.get("htmlDigest") != receipts[number - 1].get("finalHtmlDigest"):
            _reject("export_source_identity_mismatch")
        ordered[number - 1] = slide
    return {**evidence, "slides": ordered}


def _key(run: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(run.get(field) for field in (
        "owner_qid", "text_node_ordinal", "start_offset", "end_offset", "content_digest"))


def validate_observation(observation: Any, runs: Sequence[Mapping[str, Any]],
                         manifest: Mapping[str, Any], *,
                         policy_revision: str = POLICY_REVISION,
                         owners: Sequence[str] | None = None,
                         enforce_font_policy: bool = True) -> None:
    if (not isinstance(observation, Mapping) or observation.get("fonts_ready") is not True
            or observation.get("unowned_text_count") != 0):
        _reject("unverifiable_font_size")
    observed = observation.get("runs")
    viewport = observation.get("viewport", {})
    if not isinstance(observed, list):
        _reject("unverifiable_font_size")
    conversion = pt_per_css_px(
        width_pt=manifest["width_pt"], height_pt=manifest["height_pt"],
        viewport_width=viewport.get("width", 0), viewport_height=viewport.get("height", 0))
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


def validate_raster(digest: str, evidence: Mapping[str, Any] | None,
                     runs: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any], *,
                     policy_revision: str = POLICY_REVISION,
                     enforce_font_policy: bool = True) -> None:
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
            raster.get("observation"), runs, manifest,
            policy_revision=policy_revision, owners=raster.get("ownerQids"),
            enforce_font_policy=enforce_font_policy,
        )


def validate_local_image(digest: str, actual: Mapping[str, float],
                          evidence: Mapping[str, Any] | None,
                          runs: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any], *,
                          policy_revision: str = POLICY_REVISION,
                          enforce_font_policy: bool = True) -> None:
    if evidence is None:
        _reject("raster_font_evidence_unavailable")
    candidates = []
    for candidate in evidence.get("local_images", ()):
        if candidate.get("pngDigest") != digest:
            continue
        try:
            matches = all(abs(float(candidate["placement"][key]) - actual[key]) <= 1 / 12700
                          for key in ("x", "y", "widthPt", "heightPt"))
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
            output, declared_value, source_value = float(actual[target_key]), float(declared[target_key]), float(source[source_key]) * conversion
        except (KeyError, TypeError, ValueError):
            _reject("image_placement_mismatch")
        if (not all(math.isfinite(value) for value in (output, declared_value, source_value))
                or abs(output - declared_value) > 1 / 12700
                or abs(output - source_value) > 1 / 12700):
            _reject("image_placement_mismatch")
    if enforce_font_policy:
        validate_observation(
            item.get("observation"), runs, manifest,
            policy_revision=policy_revision, owners=item.get("ownerQids"),
            enforce_font_policy=enforce_font_policy,
        )
