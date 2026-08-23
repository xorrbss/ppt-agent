"""Fail-closed executable visual-parity gate for fresh Authored evidence.

The gate consumes a manifest produced by the real generation/export workflow.
It never renders, opens PowerPoint, or converts a missing artifact into a
pass. Every mode is bound to the same presentation revision and database
attempt, while each PPTX, first-open/reopen render, report, and PNG is hashed
at the point of verification.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import struct
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "authored-vnext-visual-parity-gate/v1"
REPORT_SCHEMA_VERSION = "authored-vnext-visual-parity-gate-report/v1"
DB_RESOLUTION_SCHEMA = "authored-db-attempt-resolution/v1"
MODES = ("authored", "fidelity", "hybrid")
FRESHNESS_TTL = timedelta(days=30)
FRESHNESS_MAX_FUTURE_SKEW = timedelta(minutes=5)
REQUIRED_LANGUAGES = frozenset({"ko", "zh", "ja"})
REQUIRED_ARCHETYPES = frozenset(
    {
        "card",
        "process",
        "timeline",
        "comparison",
        "metric",
        "infographic",
        "table",
        "chart",
        "image",
        "architecture",
        "roadmap",
    }
)
REQUIRED_RELEASE_ARCHETYPES = frozenset(
    {
        "boundary",
        "chart",
        "diagram",
        "image-crop",
        "long-text",
        "mixed-css",
        "multi-column",
        "table",
    }
)
DEFECT_FIELDS = ("titleLineBreakChanges", "clipping", "overlap", "ghosting", "nativeBackplateCollision")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
UUIDISH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


class GateFailure(RuntimeError):
    """A malformed or incomplete evidence bundle."""


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_digest(value: Any) -> str:
    return _digest_bytes(_canonical(value))


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise GateFailure(f"{label}_missing:{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GateFailure(f"{label}_invalid:{path}") from exc
    if not isinstance(value, dict):
        raise GateFailure(f"{label}_object_required:{path}")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GateFailure(f"{label}_required")
    return value


def _digest(value: Any, label: str) -> str:
    value = _text(value, label)
    if not DIGEST_RE.fullmatch(value):
        raise GateFailure(f"{label}_invalid")
    return value


def _int(value: Any, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise GateFailure(f"{label}_invalid")
    return value


def _path(root: Path, descriptor: Any, label: str, seen: set[Path]) -> tuple[Path, str]:
    if not isinstance(descriptor, dict):
        raise GateFailure(f"{label}_descriptor_required")
    relative = _text(descriptor.get("path"), f"{label}_path")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise GateFailure(f"{label}_path_escapes_root") from exc
    if candidate in seen:
        raise GateFailure(f"artifact_reused:{label}")
    seen.add(candidate)
    if not candidate.is_file():
        raise GateFailure(f"{label}_missing:{candidate}")
    expected = _digest(descriptor.get("sha256"), f"{label}_sha256")
    actual = _digest_bytes(candidate.read_bytes())
    if actual != expected:
        raise GateFailure(f"{label}_digest_mismatch")
    return candidate, actual


def _iso(value: Any, label: str) -> str:
    text = _text(value, label)
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GateFailure(f"{label}_invalid") from exc
    return text


def _freshness_timestamp(value: Any) -> datetime:
    text = _text(value, "freshness_generatedAt")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GateFailure("freshness_generatedAt_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GateFailure("freshness_generatedAt_timezone_required")
    return parsed.astimezone(timezone.utc)


def _verify_freshness(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("reusedFrom") is not None or value.get("previousEvidenceDigest") is not None:
        raise GateFailure("freshness_reuse_forbidden")
    run_id = _text(value.get("runId"), "freshness_runId")
    generated_at = _freshness_timestamp(value.get("generatedAt"))
    now = datetime.now(timezone.utc)
    if generated_at > now + FRESHNESS_MAX_FUTURE_SKEW:
        raise GateFailure("freshness_generatedAt_future")
    if now - generated_at > FRESHNESS_TTL:
        raise GateFailure("freshness_generatedAt_expired")
    return {"runId": run_id, "generatedAt": value["generatedAt"]}


def _render_hashes(root: Path, value: Any, slide_count: int, label: str, seen: set[Path]) -> list[str]:
    if not isinstance(value, list) or len(value) != slide_count:
        raise GateFailure(f"{label}_exact_slide_count_required")
    hashes: list[str] = []
    numbers: list[int] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise GateFailure(f"{label}_{index}_descriptor_required")
        number = _int(item.get("slideNumber"), f"{label}_{index}_slideNumber", 1)
        if number in numbers or number > slide_count:
            raise GateFailure(f"{label}_slide_number_invalid")
        numbers.append(number)
        _, digest = _path(root, item, f"{label}_{index}", seen)
        hashes.append(digest)
    if sorted(numbers) != list(range(1, slide_count + 1)):
        raise GateFailure(f"{label}_slide_numbers_incomplete")
    return hashes


def _render_paths(root: Path, value: Any, slide_count: int, label: str, seen: set[Path]) -> list[Path]:
    if not isinstance(value, list) or len(value) != slide_count:
        raise GateFailure(f"{label}_exact_slide_count_required")
    paths: list[Path] = []
    numbers: list[int] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise GateFailure(f"{label}_{index}_descriptor_required")
        number = _int(item.get("slideNumber"), f"{label}_{index}_slideNumber", 1)
        if number in numbers or number > slide_count:
            raise GateFailure(f"{label}_slide_number_invalid")
        numbers.append(number)
        path, _ = _path(root, item, f"{label}_{index}", seen)
        paths.append(path)
    if sorted(numbers) != list(range(1, slide_count + 1)):
        raise GateFailure(f"{label}_slide_numbers_incomplete")
    return paths


def _derived_archetypes(html: str, elements: list[dict[str, Any]]) -> set[str]:
    lowered = html.lower()
    text = " ".join(str(element.get("text", "")) for element in elements).lower() + " " + lowered
    kinds = {str(element.get("kind", "")).lower() for element in elements}
    roles = {str(element.get("semanticRole", "")).lower() for element in elements}
    result: set[str] = set()
    if "table" in kinds or "<table" in lowered:
        result.add("table")
    if "chart" in kinds or "chart" in lowered:
        result.add("chart")
    if "image" in kinds and "object-fit" in lowered and "cover" in lowered:
        result.add("image")
    if roles.intersection({"diagramnode", "diagramconnector"}) or any(token in text for token in ("process", "프로세스", "プロセス")):
        result.add("process")
    if any(token in text for token in ("architecture", "アーキテクチャ", "架构")) or "architecture" in roles:
        result.add("architecture")
    if any(token in text for token in ("timeline", "시간선", "时间线", "タイムライン")) or "timeline" in roles:
        result.add("timeline")
    if any(token in text for token in ("comparison", "비교", "比较", "比較")) or "comparison" in roles:
        result.add("comparison")
    if "metric" in roles or any(token in text for token in ("metric", "지표", "指标", "指標", "%")):
        result.add("metric")
    if any(token in text for token in ("infographic", "인포그래픽", "信息图", "インフォグラフィック")) or "infographic" in roles:
        result.add("infographic")
    if any(token in text for token in ("roadmap", "로드맵", "路线图", "ロードマップ")) or "roadmap" in roles:
        result.add("roadmap")
    if "card" in lowered or "카드" in text or "card" in roles:
        result.add("card")
    return result


def _derived_release_archetypes(
    html: str, elements: list[dict[str, Any]]
) -> set[str]:
    lowered = html.lower()
    kinds = {str(element.get("kind", "")).lower() for element in elements}
    roles = {
        str(element.get("semanticRole", "")).lower() for element in elements
    }
    text = " ".join(
        str(
            run.get("text", "")
            if isinstance(run, dict)
            else ""
        )
        for element in elements
        if isinstance(element, dict)
        for paragraph in (
            element.get("payload", {}).get("paragraphs", [])
            if isinstance(element.get("payload"), dict)
            else []
        )
        if isinstance(paragraph, dict)
        for run in paragraph.get("runs", [])
    )
    result: set[str] = set()
    if "table" in kinds or "<table" in lowered:
        result.add("table")
    if "chart" in kinds or "chart-native" in lowered:
        result.add("chart")
    if (
        roles.intersection({"diagramnode", "diagramconnector"})
        or "class='node" in lowered
        or 'class="node' in lowered
    ):
        result.add("diagram")
    if "image" in kinds and "object-fit:cover" in lowered.replace(" ", ""):
        result.add("image-crop")
    if len(text) >= 100:
        result.add("long-text")
    if any(
        token in lowered
        for token in (
            "linear-gradient(",
            "box-shadow:",
            "transform:",
            "clip-path:",
            "opacity:",
        )
    ):
        result.add("mixed-css")
    if any(
        token in lowered
        for token in ("display:grid", "display:flex", "grid-template-columns")
    ):
        result.add("multi-column")
    if "sentinel" in lowered or "boundarysentinel" in roles:
        result.add("boundary")
    return result


def _semantic_proof(
    root: Path,
    descriptor: Any,
    registry: list[dict[str, Any]],
    seen: set[Path],
) -> tuple[str, dict[str, Any], set[str]]:
    proof_path, proof_digest = _path(root, descriptor, "semanticProof", seen)
    proof = _read_json(proof_path, "semanticProof")
    if proof.get("schemaVersion") != "authored-semantic-proof/v1" or not isinstance(proof.get("slides"), list):
        raise GateFailure("semantic_proof_schema_invalid")
    if len(proof["slides"]) != len(registry):
        raise GateFailure("semantic_proof_slide_count_mismatch")
    registry_by_id = {slide["slideId"]: slide for slide in registry}
    proof_ids: set[str] = set()
    release_archetypes: set[str] = set()
    for index, slide in enumerate(proof["slides"], start=1):
        slide_id = _text(slide.get("slideId"), f"semanticProof:{index}:slideId")
        if slide_id in proof_ids or slide_id not in registry_by_id:
            raise GateFailure(f"semantic_proof_slide_identity_invalid:{slide_id}")
        proof_ids.add(slide_id)
        html = _text(slide.get("html"), f"semanticProof:{slide_id}:html")
        language = _text(slide.get("language"), f"semanticProof:{slide_id}:language").lower().split("-")[0]
        match = re.search(r"<html[^>]*\blang=['\"]([^'\"]+)", html, re.IGNORECASE)
        if not match or match.group(1).lower().split("-")[0] != language:
            raise GateFailure(f"semantic_proof_language_binding_invalid:{slide_id}")
        source = slide.get("semanticSource")
        elements = source.get("elements") if isinstance(source, dict) else None
        if not isinstance(elements, list) or not elements:
            raise GateFailure(f"semantic_proof_elements_required:{slide_id}")
        derived = _derived_archetypes(html, elements)
        release_archetypes.update(_derived_release_archetypes(html, elements))
        expected = set(registry_by_id[slide_id].get("archetypes", []))
        if derived != expected:
            raise GateFailure(f"semantic_proof_archetype_mismatch:{slide_id}")
        if registry_by_id[slide_id].get("language", "").lower().split("-")[0] != language:
            raise GateFailure(f"semantic_proof_registry_language_mismatch:{slide_id}")
    if proof_ids != set(registry_by_id):
        raise GateFailure("semantic_proof_slide_set_incomplete")
    return (
        proof_digest,
        {
            "schemaVersion": proof["schemaVersion"],
            "sha256": proof_digest,
            "slideCount": len(proof["slides"]),
        },
        release_archetypes,
    )


def _png_gray(path: Path) -> tuple[int, int, list[int]]:
    try:
        raw = path.read_bytes()
        if raw[:8] != b"\x89PNG\r\n\x1a\n":
            raise GateFailure("visual_diff_png_signature_missing")
        offset = 8
        width = height = bit_depth = color_type = interlace = None
        compressed = bytearray()
        while offset + 12 <= len(raw):
            length = struct.unpack(">I", raw[offset : offset + 4])[0]
            kind = raw[offset + 4 : offset + 8]
            payload = raw[offset + 8 : offset + 8 + length]
            offset += 12 + length
            if kind == b"IHDR":
                width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
                if bit_depth != 8 or compression != 0 or filtering != 0 or interlace != 0 or color_type not in {0, 2, 4, 6}:
                    raise GateFailure("visual_diff_png_format_unsupported")
            elif kind == b"IDAT":
                compressed.extend(payload)
            elif kind == b"IEND":
                break
        if width is None or height is None or not compressed:
            raise GateFailure("visual_diff_png_chunks_incomplete")
        channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
        row_bytes = width * channels
        decoded = zlib.decompress(bytes(compressed))
        if len(decoded) != height * (row_bytes + 1):
            raise GateFailure("visual_diff_png_scanline_length_invalid")
        rows: list[bytes] = []
        cursor = 0
        previous = bytearray(row_bytes)
        for _ in range(height):
            filter_type = decoded[cursor]
            cursor += 1
            source = decoded[cursor : cursor + row_bytes]
            cursor += row_bytes
            current = bytearray(row_bytes)
            for index, value in enumerate(source):
                left = current[index - channels] if index >= channels else 0
                up = previous[index]
                up_left = previous[index - channels] if index >= channels else 0
                if filter_type == 0:
                    predictor = 0
                elif filter_type == 1:
                    predictor = left
                elif filter_type == 2:
                    predictor = up
                elif filter_type == 3:
                    predictor = (left + up) // 2
                elif filter_type == 4:
                    estimate = left + up - up_left
                    distances = (abs(estimate - left), abs(estimate - up), abs(estimate - up_left))
                    predictor = (left, up, up_left)[distances.index(min(distances))]
                else:
                    raise GateFailure("visual_diff_png_filter_unsupported")
                current[index] = (value + predictor) & 0xFF
            rows.append(bytes(current))
            previous = current
        grayscale: list[int] = []
        for row in rows:
            for index in range(width):
                pixel = row[index * channels : (index + 1) * channels]
                if color_type in {0, 4}:
                    grayscale.append(pixel[0])
                else:
                    grayscale.append(round(0.2126 * pixel[0] + 0.7152 * pixel[1] + 0.0722 * pixel[2]))
        return width, height, grayscale
    except GateFailure:
        raise
    except Exception as exc:
        raise GateFailure("visual_diff_png_invalid") from exc
def _image_metrics(left_path: Path, right_path: Path) -> dict[str, float]:
    left_width, left_height, left_values = _png_gray(left_path)
    right_width, right_height, right_values = _png_gray(right_path)
    if (left_width, left_height) != (right_width, right_height):
        raise GateFailure("visual_diff_image_size_mismatch")
    if not left_values:
        raise GateFailure("visual_diff_png_empty")
    changed = sum(1 for a, b in zip(left_values, right_values) if a != b)
    n = len(left_values)
    mean_left = sum(left_values) / n
    mean_right = sum(right_values) / n
    variance_left = sum((value - mean_left) ** 2 for value in left_values) / n
    variance_right = sum((value - mean_right) ** 2 for value in right_values) / n
    covariance = sum((a - mean_left) * (b - mean_right) for a, b in zip(left_values, right_values)) / n
    c1, c2 = 6.5025, 58.5225
    denominator = (mean_left**2 + mean_right**2 + c1) * (variance_left + variance_right + c2)
    ssim = ((2 * mean_left * mean_right + c1) * (2 * covariance + c2) / denominator) if denominator else 1.0
    return {"changedPixelRatio": changed / n, "ssim": max(0.0, min(1.0, ssim))}


def _registry(manifest: dict[str, Any]) -> tuple[str, int, set[str], set[str]]:
    registry = manifest.get("slideRegistry")
    if not isinstance(registry, list) or not registry:
        raise GateFailure("slide_registry_required")
    ids: set[str] = set()
    languages: set[str] = set()
    archetypes: set[str] = set()
    for index, slide in enumerate(registry, start=1):
        if not isinstance(slide, dict):
            raise GateFailure(f"slide_registry_{index}_object_required")
        slide_id = _text(slide.get("slideId"), f"slide_registry_{index}_slideId")
        if slide_id in ids:
            raise GateFailure(f"slide_registry_duplicate:{slide_id}")
        ids.add(slide_id)
        language = _text(slide.get("language"), f"slide_registry_{index}_language").lower().split("-")[0]
        languages.add(language)
        values = slide.get("archetypes")
        if not isinstance(values, list) or not values:
            raise GateFailure(f"slide_registry_{slide_id}_archetypes_required")
        archetypes.update(_text(item, f"slide_registry_{slide_id}_archetype") for item in values)
    missing_languages = sorted(REQUIRED_LANGUAGES - languages)
    missing_archetypes = sorted(REQUIRED_ARCHETYPES - archetypes)
    if missing_languages:
        raise GateFailure("registry_languages_missing:" + ",".join(missing_languages))
    if missing_archetypes:
        raise GateFailure("registry_archetypes_missing:" + ",".join(missing_archetypes))
    supplied_digest = _digest(manifest.get("registryDigest"), "registryDigest")
    if supplied_digest != canonical_digest(registry):
        raise GateFailure("registry_digest_mismatch")
    return supplied_digest, len(registry), languages, archetypes


def _defects(value: Any, label: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise GateFailure(f"{label}_required")
    result: dict[str, int] = {}
    for field in DEFECT_FIELDS:
        result[field] = _int(value.get(field), f"{label}_{field}")
        if result[field] != 0:
            raise GateFailure(f"{label}_{field}_nonzero")
    return result


def _verify_mode(root: Path, mode: dict[str, Any], expected: dict[str, Any], seen: set[Path], slide_count: int) -> dict[str, Any]:
    name = _text(mode.get("mode"), "mode")
    if name not in MODES:
        raise GateFailure(f"mode_unsupported:{name}")
    for field in ("attemptId", "exportAttemptId", "presentationId", "operationId", "generationAttemptId"):
        _text(mode.get(field), f"{name}_{field}")
    for field in ("presentationId", "operationId", "generationAttemptId"):
        if mode[field] != expected[field]:
            raise GateFailure(f"{name}_{field}_binding_mismatch")
    if mode.get("presentationRevision") != expected["presentationRevision"]:
        raise GateFailure(f"{name}_revision_binding_mismatch")
    pptx_path, pptx_digest = _path(root, mode.get("pptx"), f"{name}_pptx", seen)
    if pptx_path.read_bytes()[:2] != b"PK":
        raise GateFailure(f"{name}_pptx_zip_signature_missing")
    report_path, report_digest = _path(root, mode.get("report"), f"{name}_report", seen)
    report = _read_json(report_path, f"{name}_report")
    for field, expected_value in {
        "presentationId": expected["presentationId"],
        "presentationRevision": expected["presentationRevision"],
        "operationId": expected["operationId"],
        "generationAttemptId": expected["generationAttemptId"],
        "exportAttemptId": mode["exportAttemptId"],
        "pptxSha256": pptx_digest,
    }.items():
        if report.get(field) != expected_value:
            raise GateFailure(f"{name}_report_{field}_binding_mismatch")
    _defects(report.get("defects"), f"{name}_report_defects")
    render = mode.get("powerPoint")
    if not isinstance(render, dict):
        raise GateFailure(f"{name}_powerpoint_evidence_required")
    if render.get("firstOpen") is not True or render.get("reopened") is not True:
        raise GateFailure(f"{name}_powerpoint_open_reopen_required")
    first = render.get("firstOpenRenderHashes")
    reopen = render.get("reopenRenderHashes")
    first_paths = _render_paths(root, first, slide_count, f"{name}_first_open", seen)
    reopen_paths = _render_paths(root, reopen, slide_count, f"{name}_reopen", seen)
    first_hashes = [_digest_bytes(path.read_bytes()) for path in first_paths]
    reopen_hashes = [_digest_bytes(path.read_bytes()) for path in reopen_paths]
    if first_hashes != reopen_hashes:
        raise GateFailure(f"{name}_powerpoint_reopen_hash_mismatch")
    if render.get("pptxSha256") != pptx_digest:
        raise GateFailure(f"{name}_powerpoint_pptx_binding_mismatch")
    defects = _defects(mode.get("defects"), f"{name}_defects")
    return {
        "mode": name,
        "attemptId": mode["attemptId"],
        "exportAttemptId": mode["exportAttemptId"],
        "pptxSha256": pptx_digest,
        "reportSha256": report_digest,
        "firstOpenRenderSha256": first_hashes,
        "reopenRenderSha256": reopen_hashes,
        "_firstOpenPaths": [str(path) for path in first_paths],
        "defects": defects,
    }


def _verify_diffs(manifest: dict[str, Any], mode_results: list[dict[str, Any]], slide_count: int) -> dict[str, Any]:
    policy = manifest.get("diffPolicy")
    if not isinstance(policy, dict):
        raise GateFailure("diff_policy_required")
    max_ratio = policy.get("maxChangedPixelRatio")
    min_ssim = policy.get("minSsim")
    if not isinstance(max_ratio, (int, float)) or not 0 <= max_ratio <= 1:
        raise GateFailure("diff_policy_max_ratio_invalid")
    if not isinstance(min_ssim, (int, float)) or not 0 <= min_ssim <= 1:
        raise GateFailure("diff_policy_min_ssim_invalid")
    entries = manifest.get("perSlideDiffs")
    if not isinstance(entries, list) or len(entries) != slide_count:
        raise GateFailure("per_slide_diff_exact_count_required")
    seen: set[int] = set()
    normalized: list[dict[str, Any]] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise GateFailure(f"per_slide_diff_{index}_required")
        number = _int(entry.get("slideNumber"), f"per_slide_diff_{index}_slideNumber", 1)
        if number in seen or number > slide_count:
            raise GateFailure("per_slide_diff_slide_number_invalid")
        seen.add(number)
        pairs = entry.get("pairs")
        if not isinstance(pairs, dict) or set(pairs) != {"authored-fidelity", "authored-hybrid", "fidelity-hybrid"}:
            raise GateFailure(f"per_slide_diff_{number}_pair_set_invalid")
        pair_result: dict[str, Any] = {}
        for pair, value in pairs.items():
            if not isinstance(value, dict) or set(value) - {"status"} or value.get("status") != "expected":
                raise GateFailure(f"per_slide_diff_{number}_{pair}_not_passed")
            left_name, right_name = pair.split("-")
            left = next(item for item in mode_results if item["mode"] == left_name)
            right = next(item for item in mode_results if item["mode"] == right_name)
            left_path = Path(left["_firstOpenPaths"][number - 1])
            right_path = Path(right["_firstOpenPaths"][number - 1])
            metrics = _image_metrics(left_path, right_path)
            if metrics["changedPixelRatio"] > max_ratio:
                raise GateFailure(f"per_slide_diff_{number}_{pair}_ratio_exceeded")
            if metrics["ssim"] < min_ssim:
                raise GateFailure(f"per_slide_diff_{number}_{pair}_ssim_below_policy")
            pair_result[pair] = {**metrics, "status": "pass"}
        normalized.append({"slideNumber": number, "pairs": pair_result})
    if seen != set(range(1, slide_count + 1)):
        raise GateFailure("per_slide_diff_slides_incomplete")
    return {"policy": {"maxChangedPixelRatio": max_ratio, "minSsim": min_ssim}, "slides": sorted(normalized, key=lambda item: item["slideNumber"])}


def _verify_ledger(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or not isinstance(value.get("entries"), list) or not value["entries"]:
        raise GateFailure("manual_review_ledger_required")
    entries: list[dict[str, Any]] = []
    reviewers: set[str] = set()
    for index, entry in enumerate(value["entries"], start=1):
        if not isinstance(entry, dict):
            raise GateFailure(f"manual_review_{index}_required")
        reviewer = _text(entry.get("reviewer"), f"manual_review_{index}_reviewer")
        if reviewer in reviewers:
            raise GateFailure("manual_review_reviewer_reused")
        reviewers.add(reviewer)
        _iso(entry.get("reviewedAt"), f"manual_review_{index}_reviewedAt")
        if entry.get("decision") != "accept":
            raise GateFailure(f"manual_review_{index}_not_accepted")
        scope = entry.get("scope")
        required_scope = {"all-slides", *DEFECT_FIELDS}
        if not isinstance(scope, list) or not required_scope.issubset(set(scope)):
            raise GateFailure(f"manual_review_{index}_scope_required")
        entries.append({"reviewer": reviewer, "reviewedAt": entry["reviewedAt"], "decision": "accept", "scope": scope})
    return entries


def _verify_release_authority(root: Path, value: Any, seen: set[Path]) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schemaVersion") != "authored-release-authority/v1":
        raise GateFailure("release_authority_receipt_required")
    authority_id = _text(value.get("authorityId"), "release_authority_id")
    receipt_path, receipt_digest = _path(root, value.get("receipt"), "release_authority_receipt", seen)
    if value.get("receiptSha256") != receipt_digest:
        raise GateFailure("release_authority_receipt_digest_mismatch")
    receipt = _read_json(receipt_path, "release_authority_receipt")
    if receipt.get("authorityId") != authority_id or receipt.get("status") != "authorized":
        raise GateFailure("release_authority_receipt_invalid")
    if not isinstance(receipt.get("scope"), list) or not {"all-slides", *DEFECT_FIELDS}.issubset(set(receipt["scope"])):
        raise GateFailure("release_authority_scope_incomplete")
    return {"authorityId": authority_id, "receiptSha256": receipt_digest}


def _verify_db_resolution(root: Path, value: Any, expected: dict[str, Any], seen: set[Path], export_attempt_ids: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schemaVersion") != DB_RESOLUTION_SCHEMA or value.get("authority") != "database" or value.get("status") != "resolved":
        raise GateFailure("db_attempt_resolution_required")
    for field in ("presentationId", "operationId", "generationAttemptId", "attemptId"):
        _text(value.get(field), f"db_resolution_{field}")
        if field != "attemptId" and value[field] != expected[field]:
            raise GateFailure(f"db_resolution_{field}_binding_mismatch")
    receipt_path, receipt_digest = _path(root, value.get("receipt"), "db_resolution_receipt", seen)
    receipt = _read_json(receipt_path, "db_resolution_receipt")
    for field, expected_value in {**expected, "attemptId": value["attemptId"]}.items():
        if receipt.get(field) != expected_value:
            raise GateFailure(f"db_receipt_{field}_binding_mismatch")
    if receipt.get("status") != "succeeded":
        raise GateFailure("db_receipt_attempt_not_succeeded")
    attempts = receipt.get("exportAttemptIds")
    if not isinstance(attempts, list) or set(attempts) != export_attempt_ids:
        raise GateFailure("db_receipt_export_attempts_mismatch")
    if value.get("receiptSha256") != receipt_digest:
        raise GateFailure("db_resolution_receipt_digest_mismatch")
    return {"attemptId": value["attemptId"], "receiptSha256": receipt_digest}


def verify_gate(manifest_path: Path) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    root = manifest_path.parent
    manifest = _read_json(manifest_path, "gate_manifest")
    if manifest.get("schemaVersion") != SCHEMA_VERSION:
        raise GateFailure("gate_schema_unsupported")
    for field in ("gateId", "presentationId", "operationId", "generationAttemptId"):
        _text(manifest.get(field), field)
    presentation_revision = _int(manifest.get("presentationRevision"), "presentationRevision", 1)
    registry_digest, slide_count, languages, archetypes = _registry(manifest)
    freshness = manifest.get("freshness")
    _verify_freshness(freshness)
    expected = {
        "presentationId": manifest["presentationId"],
        "presentationRevision": presentation_revision,
        "operationId": manifest["operationId"],
        "generationAttemptId": manifest["generationAttemptId"],
    }
    seen: set[Path] = {manifest_path}
    _semantic_proof_digest, semantic_proof, release_archetypes = _semantic_proof(
        root, manifest.get("semanticProof"), manifest["slideRegistry"], seen
    )
    release_profile_id = manifest.get("releaseProfileId")
    bounded_release = release_profile_id == "authored-windows-powerpoint-hybrid-v1"
    if release_profile_id is not None and not bounded_release:
        raise GateFailure("release_profile_unsupported")
    if bounded_release and release_archetypes != REQUIRED_RELEASE_ARCHETYPES:
        missing = sorted(REQUIRED_RELEASE_ARCHETYPES - release_archetypes)
        unexpected = sorted(release_archetypes - REQUIRED_RELEASE_ARCHETYPES)
        raise GateFailure(
            "semantic_proof_release_archetype_mismatch:"
            + ",".join(missing)
            + ":"
            + ",".join(unexpected)
        )
    modes = manifest.get("modes")
    if not isinstance(modes, list) or len(modes) != len(MODES):
        raise GateFailure("authored_fidelity_hybrid_modes_required")
    mode_names: set[str] = set()
    mode_results: list[dict[str, Any]] = []
    export_attempt_ids: set[str] = set()
    for mode in modes:
        result = _verify_mode(root, mode, expected, seen, slide_count)
        if result["mode"] in mode_names or result["exportAttemptId"] in export_attempt_ids:
            raise GateFailure("mode_or_export_attempt_reused")
        mode_names.add(result["mode"])
        export_attempt_ids.add(result["exportAttemptId"])
        mode_results.append(result)
    if mode_names != set(MODES):
        raise GateFailure("authored_fidelity_hybrid_mode_set_incomplete")
    diffs = _verify_diffs(manifest, mode_results, slide_count)
    ledger = _verify_ledger(manifest.get("manualReviewLedger"))
    release_authority = _verify_release_authority(root, manifest.get("releaseAuthority"), seen)
    db_resolution = _verify_db_resolution(root, manifest.get("dbAttemptResolution"), expected, seen, export_attempt_ids)
    atomic = manifest.get("atomicBinding")
    if not isinstance(atomic, dict):
        raise GateFailure("atomic_binding_required")
    for field, value in {**expected, "registryDigest": registry_digest, "exportAttemptIds": sorted(export_attempt_ids)}.items():
        if atomic.get(field) != value:
            raise GateFailure(f"atomic_binding_{field}_mismatch")
    atomic_digest = canonical_digest(atomic)
    if manifest.get("atomicBindingDigest") != atomic_digest:
        raise GateFailure("atomic_binding_digest_mismatch")
    public_mode_results = [{key: value for key, value in mode.items() if not key.startswith("_")} for mode in mode_results]
    identity = {
        "gateId": manifest["gateId"],
        "presentationId": manifest["presentationId"],
        "presentationRevision": presentation_revision,
        "operationId": manifest["operationId"],
        "generationAttemptId": manifest["generationAttemptId"],
        "registryDigest": registry_digest,
        "semanticProof": semantic_proof,
        "slideCount": slide_count,
        "modes": public_mode_results,
        "diffs": diffs,
        "ledger": ledger,
        "releaseAuthority": release_authority,
        "dbAttemptResolution": db_resolution,
        "freshness": freshness,
        "atomicBindingDigest": atomic_digest,
        **({"releaseProfileId": release_profile_id} if bounded_release else {}),
    }
    report = {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "status": "passed",
        "promotionReady": False,
        "visualParity": "passed",
        "languages": sorted(languages),
        "archetypes": sorted(release_archetypes if bounded_release else archetypes),
        "identity": identity,
        "evidenceDigest": canonical_digest(identity),
        "trustedIssuer": "authored-visual-parity-gate/v1",
    }
    signing_key = os.getenv("AUTHORED_RELEASE_VISUAL_GATE_HMAC_KEY", "").encode("utf-8")
    if len(signing_key) >= 32:
        report["authoritySignature"] = "hmac-sha256:" + hmac.new(
            signing_key,
            json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    output = Path(args.output).resolve()
    if output.exists():
        raise SystemExit(f"output_already_exists:{output}")
    try:
        report = verify_gate(Path(args.manifest))
        code = 0
    except GateFailure as exc:
        report = {"schemaVersion": REPORT_SCHEMA_VERSION, "status": "blocked", "promotionReady": False, "visualParity": "not-verified", "issues": [str(exc)]}
        code = 2
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(output)}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
