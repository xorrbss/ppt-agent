"""Fail-closed company-standard A/B Scene and export census.

The body Scene is intentionally compared as bytes.  This command does not
normalize colors, drop canvas/background elements, or otherwise make a
company-standard difference disappear.  If a background is still present in
the canonical body Scene, a changed fill is therefore reported as a contract
failure; the compiler must place that furniture outside the body Scene.

The inputs may be a raw ``deck-scene-ir/v1`` JSON document, a deck snapshot
with ``scene``, or a wrapper with ``deck.scene``.  Optional PPTX/render inputs
bind the Scene comparison to the actual native export and PowerPoint/COM
render evidence.  This command never starts or stops a server and never
claims lifecycle promotion readiness.

Example::

    uv run --directory servers/fastapi python \
      scripts/authored-vnext-quality/company_standard_ab_census.py \
      --scene-a artifacts/quality/deck-a.json \
      --scene-b artifacts/quality/deck-b.json \
      --pptx-a artifacts/quality/a.pptx \
      --pptx-b artifacts/quality/b.pptx \
      --render-dir-a artifacts/quality/render-a \
      --render-dir-b artifacts/quality/render-b \
      --contact-sheet artifacts/quality/contact-sheet-comparison.png \
      --evidence-a artifacts/quality/atomic-a.json \
      --evidence-b artifacts/quality/atomic-b.json \
      --output artifacts/quality/company-standard-ab-report.json

Exit status is non-zero for missing/invalid evidence or any identity,
byte-level, native-object, or render-count mismatch.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


SCHEMA_VERSION = "authored-vnext-company-standard-ab/v2"
SCENE_SCHEMA_VERSION = "deck-scene-ir/v1"
ATOMIC_BUNDLE_SCHEMA_VERSION = "authored-vnext-quality-atomic-bundle/v1"


class CensusFailure(RuntimeError):
    """Raised for missing or malformed evidence, never for a quality result."""


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise CensusFailure(f"{label}_missing:{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CensusFailure(f"{label}_invalid:{path}") from exc
    if not isinstance(value, dict):
        raise CensusFailure(f"{label}_object_required:{path}")
    return value


def _json_bytes(value: Any) -> bytes:
    """Serialize without sorting: source object and array order is evidence."""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _unwrap_scene(value: dict[str, Any], label: str) -> dict[str, Any]:
    candidate: Any = value
    if isinstance(candidate.get("deck"), dict):
        candidate = candidate["deck"]
    if isinstance(candidate, dict) and isinstance(candidate.get("scene"), dict):
        candidate = candidate["scene"]
    if not isinstance(candidate, dict) or candidate.get("schemaVersion") != SCENE_SCHEMA_VERSION:
        raise CensusFailure(f"{label}_scene_schema_unsupported")
    slides = candidate.get("slides")
    if not isinstance(slides, list) or not slides or not all(isinstance(item, dict) for item in slides):
        raise CensusFailure(f"{label}_scene_slides_required")
    for index, slide in enumerate(slides, start=1):
        if not isinstance(slide.get("slideId"), str) or not isinstance(slide.get("elements"), list):
            raise CensusFailure(f"{label}_scene_slide_invalid:{index}")
    return candidate


def _body_scene(scene: dict[str, Any]) -> dict[str, Any]:
    """The exact ordered body payload used for byte-level A/B comparison."""

    return {
        "schemaVersion": scene["schemaVersion"],
        "slides": [
            {"slideId": slide["slideId"], "elements": slide["elements"]}
            for slide in scene["slides"]
        ],
    }


def _stable_identity(scene: dict[str, Any]) -> list[dict[str, Any]]:
    identity: list[dict[str, Any]] = []
    for slide in scene["slides"]:
        elements: list[dict[str, Any]] = []
        for element in slide["elements"]:
            if not isinstance(element, dict):
                elements.append({"elementId": None, "kind": None})
                continue
            elements.append(
                {"elementId": element.get("elementId"), "kind": element.get("kind")}
            )
        identity.append({"slideId": slide["slideId"], "elements": elements})
    return identity


def _pptx_identity(scene: dict[str, Any]) -> list[dict[str, Any]]:
    """Match ``visual_acceptance_census.inspect_pptx``'s XML identity shape."""

    return [
        {
            "slide": index,
            "elements": slide["elements"],
        }
        for index, slide in enumerate(_stable_identity(scene), start=1)
    ]


def _content_identity(scene: dict[str, Any]) -> dict[str, Any]:
    """Extract user content/data seams without including furniture styles."""

    slides: list[dict[str, Any]] = []
    for slide in scene["slides"]:
        text: list[str] = []
        data: list[dict[str, Any]] = []
        for element in slide["elements"]:
            if not isinstance(element, dict):
                continue
            if isinstance(element.get("text"), str):
                text.append(element["text"])
            digest = element.get("dataDigest") or element.get("nativeDataDigest")
            if isinstance(digest, str):
                data.append({"elementId": element.get("elementId"), "digest": digest})
        slides.append({"slideId": slide["slideId"], "text": text, "data": data})
    return {"sourceDigest": scene.get("sourceDigest"), "slides": slides}


def _deck_document(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Return deck-snapshot metadata when the input is a full snapshot."""

    if isinstance(raw.get("deck"), dict):
        return raw["deck"]
    if isinstance(raw.get("scene"), dict):
        return raw
    return None


def _same_digest(left: Any, right: Any) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    return left.removeprefix("sha256:").lower() == right.removeprefix("sha256:").lower()


def _first_diff(left: Any, right: Any, path: str = "$") -> dict[str, Any] | None:
    if type(left) is not type(right):
        return {"path": path, "a": left, "b": right}
    if isinstance(left, Mapping):
        if list(left.keys()) != list(right.keys()):
            return {"path": path, "aKeys": list(left.keys()), "bKeys": list(right.keys())}
        for key in left:
            result = _first_diff(left[key], right[key], f"{path}.{key}")
            if result:
                return result
        return None
    if isinstance(left, list):
        if len(left) != len(right):
            return {"path": path, "aLength": len(left), "bLength": len(right)}
        for index, (a_item, b_item) in enumerate(zip(left, right)):
            result = _first_diff(a_item, b_item, f"{path}[{index}]")
            if result:
                return result
        return None
    return None if left == right else {"path": path, "a": left, "b": right}


def _optional_export_evidence(args: argparse.Namespace, scene_a: dict[str, Any], scene_b: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """Inspect native exports and actual raster evidence when supplied."""

    issues: list[str] = []
    evidence: dict[str, Any] = {}
    pptx_a = Path(args.pptx_a) if args.pptx_a else None
    pptx_b = Path(args.pptx_b) if args.pptx_b else None
    if bool(pptx_a) != bool(pptx_b):
        issues.append("pptx_pair_required")
    if pptx_a and pptx_b:
        script_dir = Path(__file__).resolve().parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        try:
            from visual_acceptance_census import inspect_pptx

            export_a = inspect_pptx(pptx_a)
            export_b = inspect_pptx(pptx_b)
        except Exception as exc:  # translate setup/parse failures to report evidence
            raise CensusFailure(f"pptx_inspection_failed:{exc}") from exc
        evidence["pptx"] = {
            "a": {"path": export_a["path"], "sha256": export_a["sha256"], "slideCount": export_a["slideCount"], "nativeObjectCounts": export_a["nativeObjectCounts"], "sceneIdentityDigest": export_a["sceneIdentityDigest"]},
            "b": {"path": export_b["path"], "sha256": export_b["sha256"], "slideCount": export_b["slideCount"], "nativeObjectCounts": export_b["nativeObjectCounts"], "sceneIdentityDigest": export_b["sceneIdentityDigest"]},
        }
        if export_a["slideCount"] != len(scene_a["slides"]) or export_b["slideCount"] != len(scene_b["slides"]):
            issues.append("pptx_scene_slide_count_mismatch")
        if export_a["sceneIdentity"] != export_b["sceneIdentity"]:
            issues.append("pptx_xml_stable_id_order_mismatch")
        if export_a["nativeObjectCounts"] != export_b["nativeObjectCounts"]:
            issues.append("pptx_native_counts_mismatch")
        if export_a["sceneIdentity"] != _pptx_identity(scene_a):
            issues.append("pptx_a_scene_identity_readback_mismatch")
        if export_b["sceneIdentity"] != _pptx_identity(scene_b):
            issues.append("pptx_b_scene_identity_readback_mismatch")

    render_a = Path(args.render_dir_a) if args.render_dir_a else None
    render_b = Path(args.render_dir_b) if args.render_dir_b else None
    if bool(render_a) != bool(render_b):
        issues.append("render_pair_required")
    if render_a and render_b:
        script_dir = Path(__file__).resolve().parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        try:
            from visual_acceptance_census import inspect_rendered_slides

            rendered_a = inspect_rendered_slides(render_a, len(scene_a["slides"]))
            rendered_b = inspect_rendered_slides(render_b, len(scene_b["slides"]))
        except Exception as exc:
            raise CensusFailure(f"render_inspection_failed:{exc}") from exc
        evidence["render"] = {"a": rendered_a, "b": rendered_b}
        # inspect_rendered_slides computes each digest from the PNG bytes.  The
        # digest list is retained as immutable evidence even though furniture
        # is expected to make A/B pixels different.
        if any(not str(item.get("sha256", "")).startswith("sha256:") for item in rendered_a["slides"] + rendered_b["slides"]):
            issues.append("render_png_sha256_missing")

    contact_sheet = Path(args.contact_sheet) if args.contact_sheet else None
    if contact_sheet:
        if not contact_sheet.is_file():
            issues.append(f"contact_sheet_missing:{contact_sheet}")
        else:
            evidence["contactSheet"] = {"path": str(contact_sheet), "sha256": _sha256(contact_sheet.read_bytes()), "bytes": contact_sheet.stat().st_size}
    return issues, evidence


def _atomic_bundle_checks(
    args: argparse.Namespace,
    raw_a: dict[str, Any],
    raw_b: dict[str, Any],
    scene_a: dict[str, Any],
    scene_b: dict[str, Any],
    export_evidence: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    """Cross-pin one generation's deck, Scene, artifact, and render evidence."""

    has_bound_evidence = bool(
        getattr(args, "pptx_a", None)
        or getattr(args, "pptx_b", None)
        or getattr(args, "render_dir_a", None)
        or getattr(args, "render_dir_b", None)
    )
    if not has_bound_evidence:
        return [], {}
    issues: list[str] = []
    evidence: dict[str, Any] = {}
    if (getattr(args, "pptx_a", None) or getattr(args, "pptx_b", None)) and not (
        getattr(args, "render_dir_a", None) and getattr(args, "render_dir_b", None)
    ):
        issues.append("atomic_render_pair_required_with_pptx")
    evidence_a = getattr(args, "evidence_a", None)
    evidence_b = getattr(args, "evidence_b", None)
    paths = (Path(evidence_a) if evidence_a else None, Path(evidence_b) if evidence_b else None)
    if bool(paths[0]) != bool(paths[1]):
        return ["atomic_evidence_manifest_pair_required"], evidence
    if not paths[0] or not paths[1]:
        return ["atomic_evidence_manifest_pair_required"], evidence
    manifests = (_read_json(paths[0], "atomic_evidence_a"), _read_json(paths[1], "atomic_evidence_b"))
    scenes = (scene_a, scene_b)
    raws = (raw_a, raw_b)
    for label, manifest, raw, scene in zip(("a", "b"), manifests, raws, scenes):
        if manifest.get("schemaVersion") != ATOMIC_BUNDLE_SCHEMA_VERSION:
            issues.append(f"atomic_evidence_schema_unsupported:{label}")
        required = ("deckId", "revision", "deckDigest", "sceneDigest", "publicationRevision", "artifactDigest", "pptxSha256", "pngSha256")
        missing = [key for key in required if manifest.get(key) in (None, "")]
        if missing:
            issues.append(f"atomic_evidence_required_missing:{label}:{','.join(missing)}")
        if not isinstance(manifest.get("pngSha256"), list) or not manifest["pngSha256"]:
            issues.append(f"atomic_evidence_png_sha256_required:{label}")
        if not _same_digest(manifest.get("sceneDigest"), scene.get("sceneDigest")):
            issues.append(f"atomic_evidence_scene_digest_mismatch:{label}")
        deck = _deck_document(raw)
        if deck is None:
            issues.append(f"atomic_evidence_deck_snapshot_missing:{label}")
        else:
            if manifest.get("deckId") != deck.get("deckId") or manifest.get("revision") != deck.get("revision"):
                issues.append(f"atomic_evidence_deck_identity_mismatch:{label}")
            if not _same_digest(manifest.get("deckDigest"), deck.get("digest")):
                issues.append(f"atomic_evidence_deck_digest_mismatch:{label}")
        evidence[label] = {
            "path": str(paths[0] if label == "a" else paths[1]),
            "deckId": manifest.get("deckId"),
            "revision": manifest.get("revision"),
            "publicationRevision": manifest.get("publicationRevision"),
            "deckDigest": manifest.get("deckDigest"),
            "sceneDigest": manifest.get("sceneDigest"),
            "artifactDigest": manifest.get("artifactDigest"),
            "pptxSha256": manifest.get("pptxSha256"),
        }
    pptx_evidence = export_evidence.get("pptx")
    if isinstance(pptx_evidence, dict):
        for label in ("a", "b"):
            manifest = manifests[0 if label == "a" else 1]
            actual = pptx_evidence.get(label, {}).get("sha256")
            if not _same_digest(manifest.get("pptxSha256"), actual):
                issues.append(f"atomic_evidence_pptx_digest_mismatch:{label}")
            if not _same_digest(manifest.get("artifactDigest"), actual):
                issues.append(f"atomic_evidence_artifact_digest_mismatch:{label}")
    render_evidence = export_evidence.get("render")
    if isinstance(render_evidence, dict):
        for label in ("a", "b"):
            manifest = manifests[0 if label == "a" else 1]
            expected = manifest.get("pngSha256")
            actual = [item.get("sha256") for item in render_evidence.get(label, {}).get("slides", [])]
            if not isinstance(expected, list) or expected != actual:
                issues.append(f"atomic_evidence_png_digest_mismatch:{label}")
    return issues, evidence


def run_census(args: argparse.Namespace) -> dict[str, Any]:
    raw_a = _read_json(Path(args.scene_a), "scene_a")
    raw_b = _read_json(Path(args.scene_b), "scene_b")
    scene_a = _unwrap_scene(raw_a, "scene_a")
    scene_b = _unwrap_scene(raw_b, "scene_b")
    body_a = _body_scene(scene_a)
    body_b = _body_scene(scene_b)
    body_bytes_a = _json_bytes(body_a)
    body_bytes_b = _json_bytes(body_b)
    identity_a = _stable_identity(scene_a)
    identity_b = _stable_identity(scene_b)
    content_a = _content_identity(scene_a)
    content_b = _content_identity(scene_b)
    issues: list[str] = []
    if body_bytes_a != body_bytes_b:
        issues.append("raw_body_scene_byte_identity_mismatch")
    if identity_a != identity_b:
        issues.append("stable_element_id_or_order_mismatch")
    if content_a != content_b:
        issues.append("content_identity_mismatch")
    if len(scene_a["slides"]) != len(scene_b["slides"]):
        issues.append("scene_slide_count_mismatch")
    export_issues, export_evidence = _optional_export_evidence(args, scene_a, scene_b)
    issues.extend(export_issues)
    assets_equal = scene_a.get("assetManifest") == scene_b.get("assetManifest")
    data_equal = scene_a.get("dataManifest") == scene_b.get("dataManifest")
    if not assets_equal:
        issues.append("asset_manifest_mismatch")
    if not data_equal:
        issues.append("data_manifest_mismatch")
    atomic_issues, atomic_evidence = _atomic_bundle_checks(
        args, raw_a, raw_b, scene_a, scene_b, export_evidence
    )
    issues.extend(atomic_issues)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "accepted" if not issues else "rejected",
        "promotionReady": False,
        "promotionNote": "A/B identity evidence does not assert lifecycle promotion readiness.",
        "rawBodyScene": {"byteEqual": body_bytes_a == body_bytes_b, "aSha256": _sha256(body_bytes_a), "bSha256": _sha256(body_bytes_b), "firstDiff": _first_diff(body_a, body_b)},
        "stableElementIdsAndOrder": {"equal": identity_a == identity_b, "a": identity_a, "b": identity_b},
        "contentIdentity": {"equal": content_a == content_b, "a": content_a, "b": content_b},
        "assetDataDigests": {"assetManifestEqual": assets_equal, "dataManifestEqual": data_equal},
        "atomicEvidence": atomic_evidence,
        "exportEvidence": export_evidence,
        "scene": {"a": {"path": str(Path(args.scene_a)), "sceneDigest": scene_a.get("sceneDigest")}, "b": {"path": str(Path(args.scene_b)), "sceneDigest": scene_b.get("sceneDigest")}},
        "issues": issues,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene-a", required=True)
    parser.add_argument("--scene-b", required=True)
    parser.add_argument("--pptx-a")
    parser.add_argument("--pptx-b")
    parser.add_argument("--render-dir-a")
    parser.add_argument("--render-dir-b")
    parser.add_argument("--contact-sheet")
    parser.add_argument("--evidence-a", help="Atomic generation bundle receipt for Scene/PPTX/render pins.")
    parser.add_argument("--evidence-b", help="Atomic generation bundle receipt for Scene/PPTX/render pins.")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        report = run_census(args)
    except CensusFailure as exc:
        report = {"schemaVersion": SCHEMA_VERSION, "status": "blocked", "promotionReady": False, "issues": [str(exc)]}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(output), "issueCount": len(report.get("issues", []))}, ensure_ascii=False))
    return 0 if report["status"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
