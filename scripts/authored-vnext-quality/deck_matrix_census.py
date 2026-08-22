"""Validate a fresh, bounded Fidelity/Hybrid Authored quality matrix.

The manifest points only at evidence created inside its own run directory.  A
deck is accepted when Fidelity and Hybrid exports share the same persisted
presentation revision and source identity, while each mode keeps its own
renderer digest and PPTX hash.  Hybrid logical coverage, OOXML readback,
backplates, and typography issues remain separate measurements in the report.

This tool never promotes a feature.  ``promotionReady`` is deliberately false
even when the bounded matrix is complete; promotion remains a product/release
decision based on the resulting fresh evidence bundle.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any
import uuid


SCHEMA_VERSION = "presenton.authored-deck-matrix/v1"
ATTEMPT_SCHEMA = "presenton.export-attempt-matrix-evidence/v1"
UI_SCHEMA = "presenton.ui-export-evidence/v1"
POWERPOINT_SCHEMA = "presenton.powerpoint-qa-evidence/v1"
VISUAL_SCHEMA = "presenton.full-slide-visual-inspection/v1"
REPORT_SCHEMA = "presenton.authored-deck-matrix-report/v1"
REQUIRED_ARCHETYPES = {
    "table",
    "chart",
    "image",
    "process",
    "architecture",
    "roadmap",
}
MODES = ("fidelity", "hybrid")
_SHA256_RE = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$")
_PNG_NAME_RE = re.compile(r"^(?:slide-|슬라이드)(\d+)\.png$", re.IGNORECASE)
_PATH_FIELDS = (
    "attemptReceipt",
    "pptx",
    "renderDirectory",
    "powerPointQa",
    "uiEvidence",
    "visualInspection",
)


class MatrixFailure(RuntimeError):
    """Fail-closed evidence or manifest error."""


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise MatrixFailure(f"{label}_missing:{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MatrixFailure(f"{label}_invalid:{path}") from exc
    if not isinstance(value, dict):
        raise MatrixFailure(f"{label}_object_required:{path}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise MatrixFailure(f"{label}_sha256_required")
    match = _SHA256_RE.fullmatch(value.lower())
    if match is None:
        raise MatrixFailure(f"{label}_sha256_invalid")
    return match.group(1)


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MatrixFailure(f"{label}_required")
    return value.strip()


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise MatrixFailure(f"{label}_invalid")
    return value


def _uuid(value: Any, label: str) -> str:
    raw = _nonempty(value, label)
    try:
        return str(uuid.UUID(raw))
    except ValueError as exc:
        raise MatrixFailure(f"{label}_uuid_invalid") from exc


def _resolve_inside(run_directory: Path, value: Any, label: str) -> Path:
    raw = _nonempty(value, label)
    candidate = Path(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise MatrixFailure(f"{label}_must_be_run_relative")
    resolved = (run_directory / candidate).resolve()
    try:
        resolved.relative_to(run_directory)
    except ValueError as exc:
        raise MatrixFailure(f"{label}_escapes_run_directory") from exc
    return resolved


def _binding(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MatrixFailure(f"{label}_binding_required")
    return {
        "presentationId": _uuid(value.get("presentationId"), f"{label}_presentation_id"),
        "exportAttemptId": _uuid(value.get("exportAttemptId"), f"{label}_attempt_id"),
        "presentationRevision": _integer(
            value.get("presentationRevision"), f"{label}_revision"
        ),
        "canonicalSourceDigest": _digest(
            value.get("canonicalSourceDigest"), f"{label}_canonical"
        ),
        "rendererSourceDigest": _digest(
            value.get("rendererSourceDigest"), f"{label}_renderer"
        ),
        "pptxSha256": _digest(value.get("pptxSha256"), f"{label}_pptx"),
    }


def _atomic_binding(identity: dict[str, Any]) -> dict[str, Any]:
    return {
        key: identity[key]
        for key in (
            "presentationId",
            "exportAttemptId",
            "presentationRevision",
            "canonicalSourceDigest",
            "rendererSourceDigest",
            "pptxSha256",
        )
    }


def _assert_same(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise MatrixFailure(f"{label}_mismatch")


def _attempt_identity(value: dict[str, Any], mode: str, label: str) -> dict[str, Any]:
    _assert_same(value.get("schemaVersion"), ATTEMPT_SCHEMA, f"{label}_schema")
    _assert_same(value.get("mode"), mode, f"{label}_mode")
    _assert_same(value.get("state"), "succeeded", f"{label}_state")
    identity = _binding(value, label)
    identity.update(
        {
            "htmlSourceDigest": _digest(
                value.get("htmlSourceDigest"), f"{label}_html_source"
            ),
            "compositionDigest": _digest(
                value.get("compositionDigest"), f"{label}_composition"
            ),
            "assetManifestDigest": _digest(
                value.get("assetManifestDigest"), f"{label}_asset_manifest"
            ),
            "slideCount": _integer(value.get("slideCount"), f"{label}_slide_count", minimum=1),
        }
    )
    return identity


def _render_hashes(render_directory: Path, slide_count: int, label: str) -> list[str]:
    if not render_directory.is_dir():
        raise MatrixFailure(f"{label}_render_directory_missing:{render_directory}")
    indexed: dict[int, Path] = {}
    for path in render_directory.iterdir():
        if not path.is_file():
            continue
        match = _PNG_NAME_RE.fullmatch(path.name)
        if match is None:
            continue
        index = int(match.group(1))
        if index in indexed:
            raise MatrixFailure(f"{label}_rendered_slide_duplicate_index:{index}")
        indexed[index] = path
    expected = list(range(1, slide_count + 1))
    if sorted(indexed) != expected:
        raise MatrixFailure(f"{label}_rendered_slide_set_mismatch")
    digests: list[str] = []
    for index in expected:
        path = indexed[index]
        if path.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
            raise MatrixFailure(f"{label}_rendered_slide_not_png:{index}")
        digests.append(_sha256_file(path))
    return digests


def _validate_powerpoint(
    value: dict[str, Any],
    binding: dict[str, Any],
    render_hashes: list[str],
    slide_count: int,
    label: str,
) -> dict[str, Any]:
    _assert_same(value.get("schemaVersion"), POWERPOINT_SCHEMA, f"{label}_schema")
    _assert_same(
        _binding(value.get("binding"), label),
        _atomic_binding(binding),
        f"{label}_binding",
    )
    qa = value.get("powerPointQa")
    if not isinstance(qa, dict):
        raise MatrixFailure(f"{label}_qa_required")
    required = {
        "status": "passed",
        "platform": "windows",
        "supported": True,
        "automation": "PowerPoint.COM",
        "reopened": True,
        "rendered": True,
        "readback": True,
        "repairDialogs": 0,
        "typographyIssuesGt5Pt": 0,
        "typographyIssuesGt10Pt": 0,
        "slideCount": slide_count,
    }
    for key, expected in required.items():
        _assert_same(qa.get(key), expected, f"{label}_{key}")
    observed_hashes = qa.get("renderedPngSha256")
    if not isinstance(observed_hashes, list):
        raise MatrixFailure(f"{label}_render_hashes_required")
    normalized = [_digest(item, f"{label}_render_hash") for item in observed_hashes]
    _assert_same(normalized, render_hashes, f"{label}_render_hashes")
    return {
        "status": qa["status"],
        "powerPointVersion": _nonempty(
            qa.get("powerPointVersion"), f"{label}_powerpoint_version"
        ),
        "repairDialogs": qa["repairDialogs"],
        "typographyIssuesGt5Pt": qa["typographyIssuesGt5Pt"],
        "typographyIssuesGt10Pt": qa["typographyIssuesGt10Pt"],
        "renderedPngSha256": normalized,
        "evidence": qa,
    }


def _validate_ui(
    value: dict[str, Any],
    binding: dict[str, Any],
    mode: str,
    run_directory: Path,
    label: str,
) -> dict[str, Any]:
    _assert_same(value.get("schemaVersion"), UI_SCHEMA, f"{label}_schema")
    _assert_same(value.get("mode"), mode, f"{label}_mode")
    _assert_same(
        _binding(value.get("binding"), label),
        _atomic_binding(binding),
        f"{label}_binding",
    )
    screenshot = value.get("screenshot")
    if not isinstance(screenshot, dict):
        raise MatrixFailure(f"{label}_screenshot_required")
    screenshot_path = _resolve_inside(
        run_directory, screenshot.get("path"), f"{label}_screenshot"
    )
    if not screenshot_path.is_file() or screenshot_path.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
        raise MatrixFailure(f"{label}_screenshot_png_required")
    screenshot_digest = _sha256_file(screenshot_path)
    _assert_same(
        _digest(screenshot.get("sha256"), f"{label}_screenshot_sha"),
        screenshot_digest,
        f"{label}_screenshot_sha",
    )
    return {"screenshot": str(screenshot_path), "sha256": screenshot_digest}


def _validate_visual(
    value: dict[str, Any], binding: dict[str, Any], slide_count: int, label: str
) -> dict[str, Any]:
    _assert_same(value.get("schemaVersion"), VISUAL_SCHEMA, f"{label}_schema")
    _assert_same(
        _binding(value.get("binding"), label),
        _atomic_binding(binding),
        f"{label}_binding",
    )
    _assert_same(value.get("status"), "passed", f"{label}_status")
    _assert_same(
        value.get("inspectedSlideNumbers"),
        list(range(1, slide_count + 1)),
        f"{label}_inspected_slides",
    )
    counts = value.get("issueCounts")
    if not isinstance(counts, dict):
        raise MatrixFailure(f"{label}_issue_counts_required")
    for issue in ("titleWrap", "clipping", "overlap", "ghosting"):
        _assert_same(counts.get(issue), 0, f"{label}_{issue}")
    return {"status": "passed", "inspectedSlideNumbers": value["inspectedSlideNumbers"], "issueCounts": counts}


def _validate_hybrid_sidecars(
    quality: dict[str, Any],
    coverage: dict[str, Any],
    binding: dict[str, Any],
    powerpoint: dict[str, Any],
    slide_count: int,
    label: str,
) -> dict[str, Any]:
    _assert_same(quality.get("exportAttemptId"), binding["exportAttemptId"], f"{label}_quality_attempt")
    _assert_same(
        _binding(
            {
                **quality.get("atomicBinding", {}),
                "exportAttemptId": binding["exportAttemptId"],
            },
            label,
        ),
        _atomic_binding(binding),
        f"{label}_quality_binding",
    )
    quality_qa = quality.get("powerPointQa")
    if not isinstance(quality_qa, dict) or quality_qa != powerpoint["evidence"]:
        raise MatrixFailure(f"{label}_quality_powerpoint_qa_not_passed")

    _assert_same(coverage.get("mode"), "hybrid", f"{label}_coverage_mode")
    _assert_same(coverage.get("totalSlides"), slide_count, f"{label}_coverage_slides")
    source = coverage.get("source")
    if not isinstance(source, dict):
        raise MatrixFailure(f"{label}_coverage_source_required")
    coverage_source = {
        "exportAttemptId": _uuid(source.get("exportAttemptId"), f"{label}_coverage_attempt"),
        "presentationRevision": _integer(source.get("presentationRevision"), f"{label}_coverage_revision"),
        "canonicalSourceDigest": _digest(source.get("canonicalSourceDigest"), f"{label}_coverage_canonical"),
        "rendererSourceDigest": _digest(source.get("sourceDigest"), f"{label}_coverage_renderer"),
    }
    for key in coverage_source:
        _assert_same(coverage_source[key], binding[key], f"{label}_coverage_{key}")
    _assert_same(coverage.get("visualParity"), "passed", f"{label}_coverage_visual_parity")
    logical = coverage.get("logicalCoverage")
    readback = coverage.get("ooxmlReadback")
    if not isinstance(logical, dict) or not isinstance(readback, dict):
        raise MatrixFailure(f"{label}_separate_coverage_units_required")
    _assert_same(readback.get("status"), "passed", f"{label}_ooxml_readback")
    qa_readback = powerpoint["evidence"]
    for field in ("nativeText", "nativeImage", "nativeShape", "nativeTable", "nativeChart"):
        _assert_same(
            _integer(qa_readback.get(field), f"{label}_powerpoint_{field}"),
            _integer(readback.get(field), f"{label}_ooxml_{field}"),
            f"{label}_native_readback_{field}",
        )
    backplate_count = _integer(coverage.get("backplateCount"), f"{label}_backplates")
    typography = coverage.get("typographyIssues")
    if not isinstance(typography, list):
        raise MatrixFailure(f"{label}_typography_issues_required")
    return {
        "logicalCoverage": logical,
        "ooxmlReadback": readback,
        "backplateCount": backplate_count,
        "typographyIssues": typography,
        "typographyIssueCount": len(typography),
        "sidecarSha256": {
            "quality": _canonical_sha256(quality),
            "coverage": _canonical_sha256(coverage),
        },
        "powerPointQa": powerpoint,
    }


def run_census(manifest_path: Path) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    run_directory = manifest_path.parent.resolve()
    manifest = _read_json(manifest_path, "matrix_manifest")
    _assert_same(manifest.get("schemaVersion"), SCHEMA_VERSION, "matrix_schema")
    run_id = _uuid(manifest.get("runId"), "matrix_run_id")
    try:
        datetime.fromisoformat(_nonempty(manifest.get("createdAt"), "matrix_created_at").replace("Z", "+00:00"))
    except ValueError as exc:
        raise MatrixFailure("matrix_created_at_invalid") from exc
    decks = manifest.get("decks")
    if not isinstance(decks, list) or not 3 <= len(decks) <= 8:
        raise MatrixFailure("matrix_requires_three_to_eight_decks")

    seen_paths: set[Path] = {manifest_path}
    seen_attempts: set[str] = set()
    seen_presentations: set[str] = set()
    observed_archetypes: set[str] = set()
    normalized_decks: list[dict[str, Any]] = []
    evidence_hashes: dict[str, str] = {
        manifest_path.name: _sha256_file(manifest_path)
    }

    for index, deck in enumerate(decks, start=1):
        if not isinstance(deck, dict):
            raise MatrixFailure(f"deck_{index}_object_required")
        matrix_id = _nonempty(deck.get("matrixId"), f"deck_{index}_matrix_id")
        archetypes = deck.get("archetypes")
        if not isinstance(archetypes, list) or not archetypes:
            raise MatrixFailure(f"deck_{matrix_id}_archetypes_required")
        normalized_archetypes = sorted(
            {_nonempty(item, f"deck_{matrix_id}_archetype") for item in archetypes}
        )
        observed_archetypes.update(normalized_archetypes)
        modes = deck.get("modes")
        if not isinstance(modes, dict) or set(modes) != set(MODES):
            raise MatrixFailure(f"deck_{matrix_id}_exact_modes_required")
        normalized_modes: dict[str, Any] = {}
        identities: dict[str, dict[str, Any]] = {}

        for mode in MODES:
            evidence = modes[mode]
            if not isinstance(evidence, dict):
                raise MatrixFailure(f"deck_{matrix_id}_{mode}_evidence_required")
            paths = {
                field: _resolve_inside(
                    run_directory,
                    evidence.get(field),
                    f"deck_{matrix_id}_{mode}_{field}",
                )
                for field in _PATH_FIELDS
            }
            optional_paths = {}
            if mode == "hybrid":
                for field in ("qualitySidecar", "coverageManifest"):
                    optional_paths[field] = _resolve_inside(
                        run_directory,
                        evidence.get(field),
                        f"deck_{matrix_id}_{mode}_{field}",
                    )
            for field, path in {**paths, **optional_paths}.items():
                if path in seen_paths:
                    raise MatrixFailure(f"evidence_path_reused:{field}:{path}")
                seen_paths.add(path)
                if field != "renderDirectory":
                    if not path.is_file():
                        raise MatrixFailure(f"evidence_file_missing:{field}:{path}")
                    evidence_hashes[str(path.relative_to(run_directory))] = _sha256_file(path)

            attempt = _read_json(paths["attemptReceipt"], f"deck_{matrix_id}_{mode}_attempt")
            identity = _attempt_identity(attempt, mode, f"deck_{matrix_id}_{mode}")
            identities[mode] = identity
            if identity["exportAttemptId"] in seen_attempts:
                raise MatrixFailure(f"export_attempt_reused:{identity['exportAttemptId']}")
            seen_attempts.add(identity["exportAttemptId"])
            if not paths["pptx"].is_file() or paths["pptx"].read_bytes()[:2] != b"PK":
                raise MatrixFailure(f"deck_{matrix_id}_{mode}_pptx_invalid")
            _assert_same(
                _sha256_file(paths["pptx"]),
                identity["pptxSha256"],
                f"deck_{matrix_id}_{mode}_pptx_sha",
            )
            render_hashes = _render_hashes(
                paths["renderDirectory"], identity["slideCount"], f"deck_{matrix_id}_{mode}"
            )
            powerpoint = _validate_powerpoint(
                _read_json(paths["powerPointQa"], f"deck_{matrix_id}_{mode}_powerpoint"),
                identity,
                render_hashes,
                identity["slideCount"],
                f"deck_{matrix_id}_{mode}_powerpoint",
            )
            ui = _validate_ui(
                _read_json(paths["uiEvidence"], f"deck_{matrix_id}_{mode}_ui"),
                identity,
                mode,
                run_directory,
                f"deck_{matrix_id}_{mode}_ui",
            )
            visual = _validate_visual(
                _read_json(paths["visualInspection"], f"deck_{matrix_id}_{mode}_visual"),
                identity,
                identity["slideCount"],
                f"deck_{matrix_id}_{mode}_visual",
            )
            normalized: dict[str, Any] = {
                "binding": identity,
                "pptx": str(paths["pptx"]),
                "renderDirectory": str(paths["renderDirectory"]),
                "powerPointQa": powerpoint,
                "uiEvidence": ui,
                "visualInspection": visual,
            }
            if mode == "hybrid":
                normalized["coverage"] = _validate_hybrid_sidecars(
                    _read_json(optional_paths["qualitySidecar"], f"deck_{matrix_id}_quality"),
                    _read_json(optional_paths["coverageManifest"], f"deck_{matrix_id}_coverage"),
                    identity,
                    powerpoint,
                    identity["slideCount"],
                    f"deck_{matrix_id}_hybrid",
                )
            normalized_modes[mode] = normalized

        shared_fields = (
            "presentationId",
            "presentationRevision",
            "canonicalSourceDigest",
            "htmlSourceDigest",
            "compositionDigest",
            "assetManifestDigest",
            "slideCount",
        )
        for field in shared_fields:
            _assert_same(
                identities["fidelity"][field],
                identities["hybrid"][field],
                f"deck_{matrix_id}_shared_{field}",
            )
        if identities["fidelity"]["exportAttemptId"] == identities["hybrid"]["exportAttemptId"]:
            raise MatrixFailure(f"deck_{matrix_id}_attempts_must_be_distinct")
        if identities["fidelity"]["pptxSha256"] == identities["hybrid"]["pptxSha256"]:
            raise MatrixFailure(f"deck_{matrix_id}_mode_pptx_must_be_distinct")
        presentation_id = identities["fidelity"]["presentationId"]
        if presentation_id in seen_presentations:
            raise MatrixFailure(f"presentation_reused_across_matrix:{presentation_id}")
        seen_presentations.add(presentation_id)
        normalized_decks.append(
            {
                "matrixId": matrix_id,
                "archetypes": normalized_archetypes,
                "sharedSourceBinding": {
                    field: identities["fidelity"][field] for field in shared_fields
                },
                "rendererSourceDigestByMode": {
                    mode: identities[mode]["rendererSourceDigest"] for mode in MODES
                },
                "modes": normalized_modes,
            }
        )

    missing_archetypes = sorted(REQUIRED_ARCHETYPES - observed_archetypes)
    if missing_archetypes:
        raise MatrixFailure("matrix_archetypes_missing:" + ",".join(missing_archetypes))
    bundle_identity = {
        "runId": run_id,
        "manifestSha256": evidence_hashes[manifest_path.name],
        "evidenceSha256": dict(sorted(evidence_hashes.items())),
        "deckBindings": [deck["sharedSourceBinding"] for deck in normalized_decks],
    }
    return {
        "schemaVersion": REPORT_SCHEMA,
        "status": "accepted",
        "matrixPassed": True,
        "promotionReady": False,
        "runId": run_id,
        "requiredArchetypes": sorted(REQUIRED_ARCHETYPES),
        "observedArchetypes": sorted(observed_archetypes),
        "bundleDigest": _canonical_sha256(bundle_identity),
        "evidenceSha256": bundle_identity["evidenceSha256"],
        "decks": normalized_decks,
        "notes": [
            "Fidelity and Hybrid share revision/canonical/HTML/composition/asset digests.",
            "Renderer source digests and PPTX hashes remain mode-specific atomic bindings.",
            "logicalCoverage, ooxmlReadback, backplateCount, and typographyIssues are not interchangeable units.",
            "A complete bounded matrix does not independently authorize promotion.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    output = Path(args.output).resolve()
    if output.exists():
        raise SystemExit(f"output_already_exists:{output}")
    try:
        report = run_census(Path(args.manifest))
    except MatrixFailure as exc:
        report = {
            "schemaVersion": REPORT_SCHEMA,
            "status": "blocked",
            "matrixPassed": False,
            "promotionReady": False,
            "issues": [str(exc)],
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(output)}, ensure_ascii=False))
    return 0 if report["status"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
