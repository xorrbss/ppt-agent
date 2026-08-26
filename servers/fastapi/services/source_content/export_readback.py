"""Candidate PPTX OOXML and rendered-geometry readback for source content."""

from __future__ import annotations

import hashlib
import io
import posixpath
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from services.authored_vnext.master.digests import canonical_digest
from services.authored_vnext.ooxml.native_scene import (
    NativeSceneError,
    readback_scene_pptx,
)
from services.source_content.coverage import SourceContentCoverageError


_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


@dataclass(frozen=True)
class ExportReadbackResult:
    candidate_artifact_digest: str
    export_readback_digest: str
    export_relationship_mismatches: int
    render_clipping_count: int
    render_overflow_count: int
    style_token_digest_matched: bool
    master_geometry_digest_matched: bool


def _mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True)  # type: ignore[union-attr]
    if not isinstance(value, Mapping):
        raise SourceContentCoverageError(
            "source_content_export_readback_failed", detail=f"{label} must be an object"
        )
    return value


def _value(value: Mapping[str, Any], camel: str, snake: str | None = None, default: Any = None) -> Any:
    if camel in value:
        return value[camel]
    if snake and snake in value:
        return value[snake]
    return default


def _candidate_bytes(candidate_pptx: bytes | bytearray | memoryview | str | Path) -> bytes:
    if isinstance(candidate_pptx, (bytes, bytearray, memoryview)):
        content = bytes(candidate_pptx)
    else:
        content = Path(candidate_pptx).read_bytes()
    if not content.startswith(b"PK"):
        raise SourceContentCoverageError(
            "source_content_export_readback_failed", detail="candidate artifact is not a PPTX package"
        )
    return content


def _source_part_for_rels(name: str) -> str:
    directory, filename = posixpath.split(name)
    if directory.endswith("/_rels") and filename.endswith(".rels"):
        return posixpath.join(directory.removesuffix("/_rels"), filename.removesuffix(".rels"))
    if directory == "_rels" and filename == ".rels":
        return ""
    return ""


def _relationship_target(source_part: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))


def _verify_internal_relationships(content: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
            for rels_name in sorted(name for name in names if name.endswith(".rels")):
                try:
                    root = ET.fromstring(archive.read(rels_name))
                except (KeyError, ET.ParseError) as error:
                    raise SourceContentCoverageError(
                        "source_content_export_readback_failed",
                        detail=f"candidate relationship part is malformed: {rels_name}",
                    ) from error
                source_part = _source_part_for_rels(rels_name)
                for relationship in root.findall(f"{{{_REL_NS}}}Relationship"):
                    if relationship.get("TargetMode") == "External":
                        raise SourceContentCoverageError(
                            "source_content_export_readback_failed",
                            detail="candidate PPTX contains an external relationship",
                        )
                    target = relationship.get("Target")
                    if not target:
                        raise SourceContentCoverageError(
                            "source_content_export_readback_failed",
                            detail="candidate PPTX contains a relationship without a target",
                        )
                    # Package-level relationships may point to metadata that is
                    # optional in small python-pptx fixtures. Every declared
                    # internal target must nevertheless exist.
                    resolved = _relationship_target(source_part, target)
                    if resolved not in names:
                        raise SourceContentCoverageError(
                            "source_content_export_readback_failed",
                            detail=f"candidate relationship target is missing: {resolved}",
                        )
    except zipfile.BadZipFile as error:
        raise SourceContentCoverageError(
            "source_content_export_readback_failed", detail="candidate PPTX package is corrupt"
        ) from error


def _verify_rendered_scene_geometry(
    expected_scene: Mapping[str, Any], readback_scene: Mapping[str, Any]
) -> None:
    """Bind actual OOXML shape geometry back to the staged master-layout Scene."""

    expected: dict[tuple[str, str], Mapping[str, Any]] = {}
    for raw_slide in _value(expected_scene, "slides", default=[]):
        slide = _mapping(raw_slide, label="staged Scene slide")
        slide_id = str(_value(slide, "slideId", "slide_id", ""))
        for raw_element in _value(slide, "elements", default=[]):
            element = _mapping(raw_element, label="staged Scene element")
            element_id = str(_value(element, "elementId", "element_id", ""))
            expected[(slide_id, element_id)] = _mapping(
                _value(element, "bounds", default={}),
                label="staged Scene element bounds",
            )
    actual: dict[tuple[str, str], Mapping[str, Any]] = {}
    for raw_slide in _value(readback_scene, "slides", default=[]):
        slide = _mapping(raw_slide, label="PPTX Scene readback slide")
        slide_id = str(_value(slide, "slideId", "slide_id", ""))
        for raw_element in _value(slide, "elements", default=[]):
            element = _mapping(raw_element, label="PPTX Scene readback element")
            element_id = str(_value(element, "elementId", "element_id", ""))
            actual[(slide_id, element_id)] = _mapping(
                _value(element, "renderedBounds", "rendered_bounds", {}),
                label="PPTX rendered element bounds",
            )
    if not expected or set(actual) != set(expected):
        raise SourceContentCoverageError(
            "source_content_export_readback_failed",
            detail="candidate PPTX geometry inventory differs from staged Scene",
        )
    for key, expected_bounds in expected.items():
        actual_bounds = actual[key]
        for name in ("x", "y", "width", "height"):
            expected_value = _value(expected_bounds, name)
            actual_value = _value(actual_bounds, name)
            if (
                isinstance(expected_value, bool)
                or not isinstance(expected_value, (int, float))
                or isinstance(actual_value, bool)
                or not isinstance(actual_value, (int, float))
                or abs(float(expected_value) - float(actual_value)) > 0.01
            ):
                raise SourceContentCoverageError(
                    "source_content_composition_not_applied",
                    detail=(
                        "candidate PPTX rendered geometry differs from the "
                        "staged master-layout Scene"
                    ),
                )


def verify_candidate_export(
    *,
    candidate_pptx: bytes | bytearray | memoryview | str | Path,
    materialized_scene: object,
    render_evidence: object,
) -> ExportReadbackResult:
    """Verify native OOXML identity plus artifact-bound render evidence.

    ``render_evidence`` must come from rendering this exact candidate package;
    merely asserting a pin was saved is intentionally insufficient.
    """

    content = _candidate_bytes(candidate_pptx)
    artifact_digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    envelope = _mapping(materialized_scene, label="materialized Scene")
    scene = _mapping(_value(envelope, "scene"), label="canonical Scene")
    expected_scene_digest = _value(scene, "sceneDigest", "scene_digest")
    expected_composition_digest = _value(scene, "compositionDigest", "composition_digest")
    _verify_internal_relationships(content)
    try:
        readback = readback_scene_pptx(content)
    except (NativeSceneError, ValueError, KeyError, TypeError) as error:
        raise SourceContentCoverageError(
            "source_content_export_readback_failed",
            detail=f"native PPTX OOXML readback failed: {error}",
        ) from error
    readback_scene = _mapping(readback.get("scene"), label="PPTX Scene readback")
    if (
        _value(readback_scene, "sceneDigest", "scene_digest") != expected_scene_digest
        or _value(readback_scene, "compositionDigest", "composition_digest")
        != expected_composition_digest
    ):
        raise SourceContentCoverageError(
            "source_content_export_readback_failed",
            detail="candidate PPTX is not bound to the staged Scene and composition",
        )
    _verify_rendered_scene_geometry(scene, readback_scene)

    evidence = _mapping(render_evidence, label="render readback evidence")
    clipping = _value(evidence, "renderClippingCount", "render_clipping_count", None)
    overflow = _value(evidence, "renderOverflowCount", "render_overflow_count", None)
    evidence_artifact = _value(evidence, "candidateArtifactDigest", "candidate_artifact_digest")
    evidence_scene = _value(evidence, "sceneDigest", "scene_digest")
    render_digest = _value(evidence, "renderDigest", "render_digest")
    if (
        evidence_artifact != artifact_digest
        or evidence_scene != expected_scene_digest
        or not isinstance(render_digest, str)
        or not render_digest.startswith("sha256:")
        or isinstance(clipping, bool)
        or not isinstance(clipping, int)
        or isinstance(overflow, bool)
        or not isinstance(overflow, int)
    ):
        raise SourceContentCoverageError(
            "source_content_export_readback_failed",
            detail="render evidence is incomplete or belongs to another candidate",
        )
    if clipping != 0 or overflow != 0:
        raise SourceContentCoverageError(
            "source_content_overflow",
            detail="candidate PPTX render contains clipping or overflow",
        )

    composition = _mapping(
        _value(envelope, "compositionApplication", "composition_application", {}),
        label="composition application",
    )
    style_applied = _value(
        composition, "styleTokenDigestApplied", "style_token_digest_applied"
    ) is True
    master_applied = _value(
        composition, "masterGeometryDigestApplied", "master_geometry_digest_applied"
    ) is True
    if not style_applied or not master_applied:
        raise SourceContentCoverageError(
            "source_content_composition_not_applied",
            detail="candidate export did not retain applied style/master evidence",
        )

    readback_payload = {
        "candidateArtifactDigest": artifact_digest,
        "sceneDigest": expected_scene_digest,
        "compositionDigest": expected_composition_digest,
        "nativeReadback": readback,
        "renderDigest": render_digest,
        "renderClippingCount": clipping,
        "renderOverflowCount": overflow,
        "styleTokenDigest": _value(composition, "styleTokenDigest", "style_token_digest"),
        "masterGeometryDigest": _value(
            composition, "masterGeometryDigest", "master_geometry_digest"
        ),
    }
    return ExportReadbackResult(
        candidate_artifact_digest=artifact_digest,
        export_readback_digest=canonical_digest(readback_payload),
        export_relationship_mismatches=0,
        render_clipping_count=0,
        render_overflow_count=0,
        style_token_digest_matched=True,
        master_geometry_digest_matched=True,
    )


async def render_candidate_powerpoint_evidence(
    *,
    candidate_pptx: str | Path,
    materialized_scene: object,
) -> dict[str, Any]:
    """Render and reopen the exact candidate through the owned PowerPoint QA path.

    Unsupported hosts and unavailable PowerPoint remain fail-closed.  The QA
    helper uses an isolated application instance, exports first-open and reopen
    PNGs, and measures native text bounds before reporting ``passed``.
    """

    from services.presentation_export_powerpoint_qa import trusted_powerpoint_qa

    path = Path(candidate_pptx).resolve()
    content = _candidate_bytes(path)
    artifact_digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    envelope = _mapping(materialized_scene, label="materialized Scene")
    scene = _mapping(_value(envelope, "scene"), label="canonical Scene")
    bindings = _value(envelope, "sourceBindings", "source_bindings", [])
    if not isinstance(bindings, list):
        raise SourceContentCoverageError(
            "source_content_export_readback_failed",
            detail="materialized Scene bindings are unavailable for render readback",
        )
    expected_qids = frozenset(
        str(_value(_mapping(item, label="source binding"), "elementId", "element_id"))
        for item in bindings
    )
    slides = _value(scene, "slides", default=[])
    report = await trusted_powerpoint_qa(
        path,
        len(slides),
        expected_native_qids=expected_qids,
    )
    if (
        report.get("status") != "passed"
        or report.get("rendered") is not True
        or report.get("readback") is not True
        or report.get("reopened") is not True
        or report.get("render_hashes_match") is not True
        or report.get("typography_issues_gt_5pt") != 0
        or report.get("typography_issues_gt_10pt") != 0
        or report.get("native_qid_missing") != 0
    ):
        raise SourceContentCoverageError(
            "source_content_export_readback_failed",
            detail="PowerPoint render/readback did not pass for the candidate artifact",
        )
    render_projection = {
        "artifactDigest": artifact_digest,
        "sceneDigest": _value(scene, "sceneDigest", "scene_digest"),
        "renderedPngSha256": report.get("rendered_png_sha256"),
        "firstOpenReadbackSha256": report.get("first_open_readback_sha256"),
        "reopenReadbackSha256": report.get("reopen_readback_sha256"),
        "powerPointVersion": report.get("powerpoint_version"),
    }
    return {
        "candidateArtifactDigest": artifact_digest,
        "sceneDigest": _value(scene, "sceneDigest", "scene_digest"),
        "renderDigest": canonical_digest(render_projection),
        "renderClippingCount": 0,
        "renderOverflowCount": 0,
        "report": report,
    }


__all__ = [
    "ExportReadbackResult",
    "render_candidate_powerpoint_evidence",
    "verify_candidate_export",
]
