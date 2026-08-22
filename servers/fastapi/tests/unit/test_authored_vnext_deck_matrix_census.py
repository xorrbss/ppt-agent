from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import uuid
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[4]
MODULE_PATH = ROOT / "scripts" / "authored-vnext-quality" / "deck_matrix_census.py"
SPEC = importlib.util.spec_from_file_location("deck_matrix_census", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
matrix = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(matrix)

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _sha(value: str | bytes) -> str:
    payload = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_pptx(path: Path, marker: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", f"<Types marker=\"{marker}\"/>")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mode_evidence(
    run_dir: Path,
    deck_index: int,
    mode: str,
    shared: dict[str, object],
) -> dict[str, str]:
    prefix = Path(f"deck-{deck_index}") / mode
    attempt_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"matrix:{deck_index}:{mode}"))
    renderer_digest = _sha(f"renderer:{deck_index}:{mode}")
    pptx_path = run_dir / prefix / "export.pptx"
    pptx_sha = _write_pptx(pptx_path, f"{deck_index}:{mode}")
    binding = {
        "presentationId": shared["presentationId"],
        "exportAttemptId": attempt_id,
        "presentationRevision": shared["presentationRevision"],
        "canonicalSourceDigest": shared["canonicalSourceDigest"],
        "rendererSourceDigest": renderer_digest,
        "pptxSha256": pptx_sha,
    }
    attempt = {
        "schemaVersion": matrix.ATTEMPT_SCHEMA,
        "mode": mode,
        "state": "succeeded",
        **binding,
        "htmlSourceDigest": shared["htmlSourceDigest"],
        "compositionDigest": shared["compositionDigest"],
        "assetManifestDigest": shared["assetManifestDigest"],
        "slideCount": 1,
    }
    attempt_path = run_dir / prefix / "attempt.json"
    _write_json(attempt_path, attempt)

    render_dir = run_dir / prefix / "powerpoint-render"
    render_dir.mkdir(parents=True)
    render_path = render_dir / "slide-1.png"
    render_path.write_bytes(PNG + f"render:{deck_index}:{mode}".encode())
    render_sha = hashlib.sha256(render_path.read_bytes()).hexdigest()
    qa = {
        "status": "passed",
        "platform": "windows",
        "supported": True,
        "automation": "PowerPoint.COM",
        "reopened": True,
        "rendered": True,
        "readback": True,
        "repairDialogs": 0,
        "renderedPngSha256": [render_sha],
        "powerPointVersion": "16.0",
        "slideCount": 1,
        "typographyIssuesGt5Pt": 0,
        "typographyIssuesGt10Pt": 0,
        "nativeText": deck_index if mode == "hybrid" else 0,
        "nativeImage": 0,
        "nativeShape": deck_index + 2 if mode == "hybrid" else 0,
        "nativeTable": 0,
        "nativeChart": 0,
    }
    qa_path = run_dir / prefix / "powerpoint-qa.json"
    _write_json(
        qa_path,
        {
            "schemaVersion": matrix.POWERPOINT_SCHEMA,
            "binding": binding,
            "powerPointQa": qa,
        },
    )

    screenshot_path = run_dir / prefix / "ui-export.png"
    screenshot_path.write_bytes(PNG + f"ui:{deck_index}:{mode}".encode())
    ui_path = run_dir / prefix / "ui-export.json"
    _write_json(
        ui_path,
        {
            "schemaVersion": matrix.UI_SCHEMA,
            "mode": mode,
            "binding": binding,
            "screenshot": {
                "path": str(screenshot_path.relative_to(run_dir)),
                "sha256": hashlib.sha256(screenshot_path.read_bytes()).hexdigest(),
            },
        },
    )
    visual_path = run_dir / prefix / "visual-inspection.json"
    _write_json(
        visual_path,
        {
            "schemaVersion": matrix.VISUAL_SCHEMA,
            "binding": binding,
            "status": "passed",
            "inspectedSlideNumbers": [1],
            "issueCounts": {
                "titleWrap": 0,
                "clipping": 0,
                "overlap": 0,
                "ghosting": 0,
            },
        },
    )
    evidence = {
        "attemptReceipt": str(attempt_path.relative_to(run_dir)),
        "pptx": str(pptx_path.relative_to(run_dir)),
        "renderDirectory": str(render_dir.relative_to(run_dir)),
        "powerPointQa": str(qa_path.relative_to(run_dir)),
        "uiEvidence": str(ui_path.relative_to(run_dir)),
        "visualInspection": str(visual_path.relative_to(run_dir)),
    }
    if mode == "hybrid":
        atomic = {key: value for key, value in binding.items() if key != "exportAttemptId"}
        quality_path = run_dir / prefix / "export.pptx.quality.json"
        quality = {
            "exportAttemptId": attempt_id,
            "atomicBinding": atomic,
            "powerPointQa": qa,
        }
        _write_json(quality_path, quality)
        coverage_path = run_dir / prefix / "export.pptx.coverage.json"
        coverage = {
            "schemaVersion": "presenton.authored-hybrid-coverage/v2",
            "mode": "hybrid",
            "source": {
                "exportAttemptId": attempt_id,
                "presentationRevision": shared["presentationRevision"],
                "canonicalSourceDigest": shared["canonicalSourceDigest"],
                "sourceDigest": renderer_digest,
            },
            "totalSlides": 1,
            "logicalCoverage": {
                "nativeText": deck_index,
                "nativeImage": 0,
                "nativeShape": deck_index + 1,
                "nativeTable": 0,
                "nativeChart": 0,
                "rasterRemaining": 2,
            },
            "ooxmlReadback": {
                "status": "passed",
                "nativeText": deck_index,
                "nativeImage": 0,
                "nativeShape": deck_index + 2,
                "nativeTable": 0,
                "nativeChart": 0,
                "rasterRemaining": 1,
            },
            "backplateCount": deck_index,
            "typographyIssues": [{"code": "font-substitution"}] * deck_index,
            "visualParity": "passed",
        }
        _write_json(coverage_path, coverage)
        evidence["qualitySidecar"] = str(quality_path.relative_to(run_dir))
        evidence["coverageManifest"] = str(coverage_path.relative_to(run_dir))
    return evidence


def _matrix_manifest(run_dir: Path) -> tuple[Path, list[dict[str, object]]]:
    archetypes = [
        ["table", "chart"],
        ["image", "process"],
        ["architecture", "roadmap"],
    ]
    decks = []
    for index in range(1, 4):
        presentation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"matrix-deck:{index}"))
        shared = {
            "presentationId": presentation_id,
            "presentationRevision": index,
            "canonicalSourceDigest": _sha(f"canonical:{index}"),
            "htmlSourceDigest": _sha(f"html:{index}"),
            "compositionDigest": _sha(f"composition:{index}"),
            "assetManifestDigest": _sha(f"assets:{index}"),
        }
        decks.append(
            {
                "matrixId": f"deck-{index}",
                "archetypes": archetypes[index - 1],
                "modes": {
                    mode: _mode_evidence(run_dir, index, mode, shared)
                    for mode in matrix.MODES
                },
            }
        )
    manifest_path = run_dir / "matrix-manifest.json"
    _write_json(
        manifest_path,
        {
            "schemaVersion": matrix.SCHEMA_VERSION,
            "runId": str(uuid.uuid4()),
            "createdAt": "2026-08-22T12:00:00+09:00",
            "decks": decks,
        },
    )
    return manifest_path, decks


def test_matrix_binds_fresh_mode_evidence_and_keeps_coverage_units_separate(
    tmp_path: Path,
) -> None:
    manifest_path, decks = _matrix_manifest(tmp_path)

    report = matrix.run_census(manifest_path)

    assert report["status"] == "accepted"
    assert report["matrixPassed"] is True
    assert report["promotionReady"] is False
    assert report["observedArchetypes"] == sorted(matrix.REQUIRED_ARCHETYPES)
    hybrid = report["decks"][1]["modes"]["hybrid"]["coverage"]
    assert hybrid["logicalCoverage"]["nativeShape"] == 3
    assert hybrid["ooxmlReadback"]["nativeShape"] == 4
    assert hybrid["backplateCount"] == 2
    assert hybrid["typographyIssueCount"] == 2

    hybrid_attempt_path = tmp_path / decks[0]["modes"]["hybrid"]["attemptReceipt"]
    attempt = json.loads(hybrid_attempt_path.read_text(encoding="utf-8"))
    attempt["presentationRevision"] += 1
    _write_json(hybrid_attempt_path, attempt)
    with pytest.raises(matrix.MatrixFailure, match="binding_mismatch"):
        matrix.run_census(manifest_path)
