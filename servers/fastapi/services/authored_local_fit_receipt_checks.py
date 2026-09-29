"""Receipt and relationship checks for local-fit PPTX verification."""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping, Sequence
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from utils.coerce import finite_float
from services.authored_local_fit_evidence import (
    extended_shrink_basis_valid,
    receipt_is_preserved_original,
    receipt_quality_is_publishable,
)
from services.authored_local_fit_policy import acceptance_key, baseline_digest, font_policy, font_size_allowed


NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}


class LocalFitPageVerificationError(ValueError):
    """A hybrid-only page defect whose exact raster backplate is admissible."""

    def __init__(self, failed_pages: Sequence[Mapping[str, object]]):
        self.failed_pages = tuple(failed_pages)
        super().__init__("authored_local_fit_export:hybrid_page_verification_failed")


def _bounded_page_code(error: ValueError) -> str:
    code = str(error).removeprefix("authored_local_fit_export:").lower()
    code = re.sub(r"[^a-z0-9_-]+", "_", code).strip("_")
    return (code[:96] if code and code[0].isalpha() else "hybrid_page_verification_failed")


_HYBRID_NATIVE_TEXT_ADVISORY_CODES = frozenset({
    "native_run_owner_unavailable",
    "native_run_baseline_unavailable",
    "unverifiable_font_size",
    "font_floor_or_shrink_limit",
})
_HYBRID_CHART_ADVISORY_CODES = frozenset({
    "chart_tick_baseline_unavailable",
    "chart_label_baseline_unavailable",
    "chart_data_label_baseline_unavailable",
    "chart_font_evidence_unavailable",
    "unverifiable_font_size",
    "font_floor_or_shrink_limit",
    "chart_group_transform_unverifiable",
})
_HYBRID_IMAGE_EVIDENCE_ADVISORY_CODES = frozenset({
    "raster_evidence_mismatch",
    "raster_font_evidence_unavailable",
    "local_image_evidence_mismatch",
})


def _fail(reason: str) -> None:
    raise ValueError("authored_local_fit_export:" + reason)


def _relationships(archive: ZipFile, part: str) -> dict[str, str]:
    folder, name = posixpath.split(part)
    path = posixpath.join(folder, "_rels", name + ".rels")
    if path not in archive.namelist():
        return {}
    result = {}
    for item in ET.fromstring(archive.read(path)):
        if item.get("TargetMode") == "External":
            continue
        target = item.get("Target", "")
        resolved = (target.lstrip("/") if target.startswith("/") else
                    posixpath.normpath(posixpath.join(folder, target)))
        if not resolved.startswith("ppt/"):
            _fail("invalid_relationship")
        result[item.get("Id", "")] = resolved
    return result


def _number(node: ET.Element | None, attribute: str) -> float:
    result = finite_float(node.attrib.get(attribute) if node is not None else None)
    if result is None:
        _fail("unverifiable_font_size")
    return result


def _runs(
    receipt: Mapping[str, Any], *, required: bool = True
) -> list[Mapping[str, Any]]:
    values = receipt.get("runs") or receipt.get("baseline", {}).get("runs")
    if isinstance(values, Mapping):
        values = list(values.values())
    if not isinstance(values, (list, tuple)) or (required and not values):
        _fail("baseline_unavailable")
    if not all(isinstance(item, Mapping) for item in values):
        _fail("baseline_unavailable")
    return list(values)


def _check_sizes(
    runs: Sequence[Mapping[str, Any]], *, policy_revision: str
) -> None:
    for run in runs:
        if not font_size_allowed(
            float(run["original_pt"]),
            float(run["final_pt"]),
            policy_revision=policy_revision,
        ):
            _fail("font_floor_or_shrink_limit")


def _check_receipt(receipt: Mapping[str, Any]) -> str:
    policy_revision = receipt.get("policy_revision")
    if (
        not isinstance(policy_revision, str)
        or receipt.get("policyRevision") != policy_revision
    ):
        _fail("policy_identity_mismatch")
    try:
        font_policy(policy_revision)
    except ValueError:
        _fail("policy_identity_mismatch")
    baseline = receipt.get("baseline")
    preserved_original = receipt_is_preserved_original(receipt)
    if (not isinstance(baseline, Mapping)
            or (not preserved_original and baseline.get("complete") is not True)
            or not receipt_quality_is_publishable(receipt)
            or receipt.get("baselineDigest") != baseline_digest(baseline)
            or (receipt.get("changed") is True and (
                receipt.get("verifiedFitDisabled") is not True
                or receipt.get("verificationCount") != 1))):
        _fail("acceptance_identity_mismatch")
    policy = {
        "policy_revision": policy_revision,
        "export_scale_manifest": receipt.get("export_scale_manifest"),
    }
    if receipt.get("acceptanceKey") != acceptance_key(receipt.get("renderKey"), baseline, policy):
        _fail("acceptance_identity_mismatch")
    originals = {run.get("run_id"): run for run in baseline.get("runs", ())}
    finals = _runs(receipt, required=not preserved_original)
    if preserved_original:
        manifest = receipt.get("export_scale_manifest")
        png_size = receipt.get("pngSize")
        if (
            receipt.get("originalHtmlDigest") != receipt.get("finalHtmlDigest")
            or not isinstance(receipt.get("renderKey"), str)
            or not isinstance(manifest, Mapping)
            or not isinstance(png_size, Mapping)
            or png_size.get("width") != manifest.get("viewport_width")
            or png_size.get("height") != manifest.get("viewport_height")
        ):
            _fail("acceptance_identity_mismatch")
        return policy_revision
    if not extended_shrink_basis_valid(
        receipt, finals, policy_revision=policy_revision
    ):
        _fail("extended_comparison_incomplete")
    if {run.get("run_id") for run in finals} != set(originals):
        _fail("baseline_unavailable")
    for run in finals:
        original = originals[run.get("run_id")]
        if any(run.get(key) != original.get(key) for key in (
                "original_pt", "content_digest", "owner_qid", "start_offset", "end_offset")):
            _fail("baseline_identity_mismatch")
    return policy_revision

