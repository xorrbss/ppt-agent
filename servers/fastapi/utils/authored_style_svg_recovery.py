"""Private, byte-bound page SVG recovery material from managed style assets.

Bitmap role references and motif SVGs are deliberately not recovery candidates.
No asset is fetched or followed outside the configured catalogue asset root.
"""
from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from io import BytesIO
from pathlib import Path

from PIL import Image

from services.svg_contract import SVG_CONTRACT_VERSION, source_digest, validate_svg
from utils.hashing import sha256_prefixed

RECOVERY_CANDIDATE_VERSION = "svg-validated-recovery/v2"
RECOVERY_PNG_MAX_BYTES = 8 * 1024 * 1024
_IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_FIT_METRICS = frozenset({"content_chars", "nonempty_lines", "title_chars", "image_group_count", "image_member_count"})
_AXES = frozenset({"source_fidelity", "screen_integrity", "readability", "design", "native_editability"})


def recovery_digest(value: object) -> str:
    return sha256_prefixed(json.dumps(value, ensure_ascii=False, sort_keys=True).encode())


def recovery_style_identity(snapshot: Mapping) -> str:
    return recovery_digest({"style_id": snapshot.get("style_id"), "tokens": snapshot.get("tokens")})


def _asset(root: Path, reference: object, suffix: str, limit: int) -> bytes:
    if not isinstance(reference, str) or not reference or "\\" in reference or ":" in reference:
        raise ValueError("svg_recovery_asset_reference_invalid")
    relative = Path(reference)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("svg_recovery_asset_reference_invalid")
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() != suffix or not path.is_file():
        raise ValueError("svg_recovery_asset_unavailable")
    if path.stat().st_size > limit:
        raise ValueError("svg_recovery_asset_size_limit")
    data = path.read_bytes()
    if len(data) > limit:
        raise ValueError("svg_recovery_asset_size_limit")
    return data


def _fit_valid(fit: object) -> bool:
    if not isinstance(fit, Mapping) or set(fit) != _FIT_METRICS | {"table_shapes", "chart_shapes", "image_group_shapes"}:
        return False
    for key in _FIT_METRICS:
        limits = fit[key]
        if (not isinstance(limits, list) or len(limits) != 2
                or any(type(x) is not int or x < 0 for x in limits) or limits[0] > limits[1]):
            return False
    for key, dimensions in (("table_shapes", 2), ("chart_shapes", 2)):
        shapes = fit[key]
        if not isinstance(shapes, list):
            return False
        if any(not isinstance(shape, list) or len(shape) != dimensions
               or any(type(x) is not int or x < 1 for x in shape) for shape in shapes):
            return False
    groups = fit["image_group_shapes"]
    return (isinstance(groups, list) and all(type(x) is int and x > 0 for x in groups)
            and groups == sorted(groups)
            and fit["image_group_count"][0] <= len(groups) <= fit["image_group_count"][1]
            and fit["image_member_count"][0] <= sum(groups) <= fit["image_member_count"][1])


def recovery_candidate_valid(candidate: object) -> bool:
    """Validate pinned material and receipt binding, never a YAML pass boolean."""
    if not isinstance(candidate, Mapping):
        return False
    try:
        if candidate.get("version") != RECOVERY_CANDIDATE_VERSION:
            return False
        if any(not isinstance(candidate.get(key), str) or not _IDENTIFIER.fullmatch(candidate[key])
               for key in ("id", "style_id")):
            return False
        if type(candidate.get("revision")) is not int or candidate["revision"] < 1:
            return False
        canvas = candidate["canvas"]
        if (not isinstance(canvas, Mapping) or set(canvas) != {"width", "height"}
                or any(type(x) is not int or not 0 < x <= 4096 for x in canvas.values())):
            return False
        if not _fit_valid(candidate["content_fit"]):
            return False
        reference = candidate.get("png_asset_ref")
        if (not isinstance(reference, str) or not reference or ":" in reference or "\\" in reference
                or Path(reference).is_absolute() or ".." in Path(reference).parts
                or Path(reference).suffix.lower() != ".png"):
            return False
        if any(not isinstance(candidate.get(key), str) or not _DIGEST.fullmatch(candidate[key])
               for key in ("svg_digest", "png_digest", "receipt_digest", "style_identity_digest")):
            return False
        svg = candidate["svg_content"]
        if not isinstance(svg, str) or source_digest(svg) != candidate["svg_digest"]:
            return False
        validate_svg(svg, width=canvas["width"], height=canvas["height"])
        evidence = candidate["verification"]
        if not isinstance(evidence, Mapping) or recovery_digest(evidence) != candidate["receipt_digest"]:
            return False
        if evidence.get("version") != RECOVERY_CANDIDATE_VERSION or evidence.get("completed") is not True:
            return False
        for key in ("id", "revision", "style_id", "style_identity_digest", "canvas", "svg_digest", "png_digest"):
            if evidence.get(key) != candidate[key]:
                return False
        if evidence.get("png_asset_ref") != reference:
            return False
        if evidence.get("content_fit_digest") != recovery_digest(candidate["content_fit"]):
            return False
        if evidence.get("contract_version") != SVG_CONTRACT_VERSION:
            return False
        for key in ("renderer_digest", "fonts_digest", "runtime_digest"):
            if not isinstance(evidence.get(key), str) or not _DIGEST.fullmatch(evidence[key]):
                return False
        axes = evidence.get("axes")
        if not isinstance(axes, Mapping) or any(
            not isinstance(axes.get(axis), Mapping)
            or axes[axis].get("evaluated") is not True
            or axes[axis].get("status") != "passed"
            or axes[axis].get("svg_digest") != candidate["svg_digest"]
            or axes[axis].get("png_digest") != candidate["png_digest"]
            or not isinstance(axes[axis].get("evidence_digest"), str)
            or not _DIGEST.fullmatch(axes[axis]["evidence_digest"])
            for axis in _AXES
        ):
            return False
        # A managed loader binds the actual PNG and receipt bytes as well as SVG.
        assets = candidate.get("asset_digests")
        return isinstance(assets, Mapping) and assets == {
            "svg": candidate["svg_digest"], "png": candidate["png_digest"],
            "receipt": candidate["receipt_asset_digest"],
        } and isinstance(candidate["receipt_asset_digest"], str) and bool(_DIGEST.fullmatch(candidate["receipt_asset_digest"]))
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def load_style_recovery_candidates(data: Mapping, source: Path, assets_directory: Path, style_id: str) -> tuple[dict, ...]:
    """Read only explicit private candidate manifests; invalid material is excluded."""
    raw = data.get("validated_recovery_candidates", [])
    if not isinstance(raw, list):
        raise ValueError(f"{source.name}: validated_recovery_candidates must be a list")
    root, result, identifiers = assets_directory.resolve(), [], set()
    for entry in raw:
        try:
            if not isinstance(entry, Mapping) or entry.get("style_id") != style_id:
                continue
            svg_bytes = _asset(root, entry.get("svg"), ".svg", 2 * 1024 * 1024)
            png_bytes = _asset(root, entry.get("png"), ".png", RECOVERY_PNG_MAX_BYTES)
            receipt_bytes = _asset(root, entry.get("receipt"), ".json", 256 * 1024)
            if (sha256_prefixed(svg_bytes) != entry.get("svg_digest")
                    or sha256_prefixed(png_bytes) != entry.get("png_digest")
                    or sha256_prefixed(receipt_bytes) != entry.get("receipt_asset_digest")):
                continue
            with Image.open(BytesIO(png_bytes)) as image:
                if image.format != "PNG" or max(image.size) > 8192 or image.width * image.height > 16_777_216:
                    continue
                png_dimensions = list(image.size)
                image.verify()
            candidate = {key: copy.deepcopy(entry.get(key)) for key in (
                "version", "id", "revision", "style_id", "style_identity_digest", "canvas", "content_fit",
                "svg_digest", "png_digest", "receipt_digest", "receipt_asset_digest",
            )}
            candidate.update(svg_content=svg_bytes.decode("utf-8"), png_asset_ref=entry.get("png"),
                             verification=json.loads(receipt_bytes),
                             asset_digests={"svg": sha256_prefixed(svg_bytes), "png": sha256_prefixed(png_bytes),
                                            "receipt": sha256_prefixed(receipt_bytes)})
            canvas = candidate["canvas"]
            if (not isinstance(canvas, Mapping) or not isinstance(candidate["verification"], Mapping)
                    or candidate["verification"].get("png_dimensions") != png_dimensions
                    or png_dimensions[0] * canvas.get("height", 0) != png_dimensions[1] * canvas.get("width", 0)):
                continue
            key = (candidate["id"], candidate["revision"])
            if key in identifiers or not recovery_candidate_valid(candidate):
                continue
            identifiers.add(key)
            result.append(candidate)
        except (OSError, ValueError, TypeError, KeyError, SyntaxError, Image.DecompressionBombError):
            continue
    return tuple(result)


def read_validated_recovery_png(candidate: Mapping, *, assets_directory: Path | None = None) -> bytes | None:
    """Read pinned managed material only after the caller's current authority check.

    Store the reference in checkpoints; avoid duplicating a page PNG in each
    candidate record. Never rebind edited or missing material to another asset.
    """
    if not recovery_candidate_valid(candidate):
        return None
    if assets_directory is None:
        from utils.authored_styles import AUTHORED_STYLE_ASSETS_DIRECTORY

        assets_directory = AUTHORED_STYLE_ASSETS_DIRECTORY
    try:
        data = _asset(assets_directory.resolve(), candidate["png_asset_ref"], ".png", RECOVERY_PNG_MAX_BYTES)
        if sha256_prefixed(data) != candidate["png_digest"] or not data.startswith(b"\x89PNG\r\n\x1a\n"):
            return None
        with Image.open(BytesIO(data)) as image:
            if (image.format != "PNG" or max(image.size) > 8192
                    or image.width * image.height > 16_777_216
                    or list(image.size) != candidate["verification"].get("png_dimensions")):
                return None
            canvas = candidate["canvas"]
            if image.width * canvas["height"] != image.height * canvas["width"]:
                return None
            image.verify()
        return data
    except (OSError, ValueError, TypeError, KeyError, SyntaxError, Image.DecompressionBombError):
        return None


def validated_style_recovery_snapshot(style: object, snapshot: Mapping) -> list[dict]:
    """Freeze catalogue bytes before the enclosing style snapshot is hashed."""
    identity = recovery_style_identity(snapshot)
    return [copy.deepcopy(candidate) for candidate in getattr(style, "validated_recovery_candidates", ())
            if recovery_candidate_valid(candidate) and candidate["style_id"] == snapshot.get("style_id")
            and candidate["style_identity_digest"] == identity]
