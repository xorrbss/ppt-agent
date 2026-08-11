"""Fail-closed provenance and browser-capture evidence validation."""

from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image

from .models import PackageAssetInventory, TemplatePackageManifest


class TemplatePackageValidationError(ValueError):
    pass


def validate_rights_evidence(
    source_root: Path,
    manifest: TemplatePackageManifest,
    inventory: PackageAssetInventory,
    evidence: dict[str, Any],
) -> None:
    claims = evidence.get("claims")
    upstream = evidence.get("upstream")
    sources = evidence.get("evidenceSources")
    if (
        evidence.get("schemaVersion") != "presenton.template-package-provenance/v1"
        or evidence.get("templateId") != manifest.template_id
        or not isinstance(upstream, dict)
        or upstream.get("repository") != manifest.provenance.repository
        or upstream.get("commitSha") != manifest.provenance.commit_sha
        or upstream.get("sourcePath") != manifest.provenance.source_path
        or not isinstance(sources, list)
        or not isinstance(claims, list)
    ):
        raise TemplatePackageValidationError("template_package_provenance_invalid")

    declared_extensions = {
        extension.path: extension
        for extension in manifest.provenance.extensions
    }
    if len(declared_extensions) != len(manifest.provenance.extensions):
        raise TemplatePackageValidationError("template_package_provenance_extension_invalid")
    evidence_extensions = evidence.get("extensions", [])
    if evidence_extensions != [
        extension.model_dump(mode="json", by_alias=True)
        for extension in manifest.provenance.extensions
    ]:
        raise TemplatePackageValidationError("template_package_provenance_extension_invalid")

    source_by_id: dict[str, dict[str, Any]] = {}
    for source in sources:
        if not isinstance(source, dict):
            raise TemplatePackageValidationError("template_package_evidence_source_invalid")
        source_id = source.get("id")
        if not isinstance(source_id, str) or not source_id or source_id in source_by_id:
            raise TemplatePackageValidationError("template_package_evidence_source_invalid")
        source_by_id[source_id] = source
        source_type = source.get("type")
        url = source.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise TemplatePackageValidationError("template_package_evidence_source_invalid")
        if source_type == "git-commit":
            commit_sha = source.get("commitSha")
            if (
                not isinstance(commit_sha, str)
                or not re.fullmatch(r"[0-9a-f]{40}", commit_sha)
                or commit_sha not in url
                or (
                    commit_sha == manifest.provenance.commit_sha
                    and not url.startswith(f"{manifest.provenance.repository.rstrip('/')}/")
                )
            ):
                raise TemplatePackageValidationError("template_package_evidence_source_not_immutable")
        elif source_type in {"repository-license", "font-license"}:
            match = re.search(r"/blob/([0-9a-f]{40})/", url)
            if (
                match is None
                or not isinstance(source.get("licenseExpression"), str)
                or not source.get("licenseExpression")
                or not re.fullmatch(r"[0-9a-f]{64}", str(source.get("sha256", "")))
                or (
                    source_type == "font-license"
                    and source.get("commitSha") != match.group(1)
                )
            ):
                raise TemplatePackageValidationError(
                    "template_package_evidence_source_not_immutable"
                )
        elif source_type != "pull-request":
            raise TemplatePackageValidationError("template_package_evidence_source_invalid")

    license_record = upstream.get("license")
    repository_license_sources = [
        source
        for source in source_by_id.values()
        if source.get("type") == "repository-license"
    ]
    if (
        not isinstance(license_record, dict)
        or not str(license_record.get("url", "")).startswith(
            f"{manifest.provenance.repository.rstrip('/')}/"
        )
        or not re.fullmatch(
            r"[0-9a-f]{40}", str(license_record.get("upstreamGitBlobSha1", ""))
        )
        or not any(
            source.get("sha256") == license_record.get("sha256")
            and source.get("upstreamGitBlobSha1")
            == license_record.get("upstreamGitBlobSha1")
            and source.get("url") == license_record.get("url")
            and source.get("licenseExpression") == license_record.get("expression")
            for source in repository_license_sources
        )
    ):
        raise TemplatePackageValidationError("template_package_upstream_license_identity_invalid")

    claim_by_id: dict[str, dict[str, Any]] = {}
    for item in claims:
        if not isinstance(item, dict):
            raise TemplatePackageValidationError("template_package_rights_evidence_invalid")
        claim_id = item.get("id")
        if not isinstance(claim_id, str) or not claim_id or claim_id in claim_by_id:
            raise TemplatePackageValidationError("template_package_rights_evidence_invalid")
        claim_by_id[claim_id] = item
        refs = item.get("evidenceSourceIds")
        if (
            not isinstance(refs, list)
            or not refs
            or len(refs) != len(set(refs))
            or any(not isinstance(ref, str) or ref not in source_by_id for ref in refs)
        ):
            raise TemplatePackageValidationError("template_package_evidence_source_reference_invalid")

    claim = claim_by_id.get(manifest.license.rights_evidence_id or "")
    if (
        not isinstance(claim, dict)
        or claim.get("status") != "verified"
        or claim.get("licenseExpression") != license_record.get("expression")
    ):
        raise TemplatePackageValidationError("template_package_rights_evidence_invalid")

    expected = {
        item.path: (
            item.sha256,
            _git_blob_sha1(_confined_below(source_root, item.path).read_bytes()),
            item.license_ref,
        )
        for item in inventory.files
    }
    actual: dict[str, tuple[str, str, str]] = {}
    for evidence_claim in claim_by_id.values():
        covered = evidence_claim.get("coveredAssets")
        license_expression = evidence_claim.get("licenseExpression")
        claim_refs = evidence_claim.get("evidenceSourceIds")
        if (
            evidence_claim.get("status") != "verified"
            or not isinstance(license_expression, str)
            or not license_expression
            or not isinstance(covered, list)
            or not isinstance(claim_refs, list)
        ):
            raise TemplatePackageValidationError("template_package_rights_evidence_invalid")
        for item in covered:
            if not isinstance(item, dict):
                raise TemplatePackageValidationError("template_package_rights_evidence_invalid")
            path = item.get("path")
            digest = item.get("sha256")
            blob = item.get("upstreamGitBlobSha1")
            item_refs = item.get("evidenceSourceIds", claim_refs)
            extension = declared_extensions.get(str(path))
            source_commit_sha = item.get(
                "sourceCommitSha",
                extension.source_commit_sha if extension is not None else manifest.provenance.commit_sha,
            )
            source_path = item.get(
                "sourcePath",
                extension.source_path if extension is not None else path,
            )
            if (
                not isinstance(path, str)
                or not isinstance(digest, str)
                or not isinstance(blob, str)
                or path in actual
                or not isinstance(item_refs, list)
                or not item_refs
                or len(item_refs) != len(set(item_refs))
                or any(not isinstance(ref, str) or ref not in source_by_id for ref in item_refs)
                or not set(item_refs).issubset(set(claim_refs))
                or not isinstance(source_path, str)
                or not source_path
                or (extension is not None and source_path != extension.source_path)
                or (extension is not None and source_commit_sha != extension.source_commit_sha)
                or (
                    extension is not None
                    and extension.kind == "added"
                    and (
                        digest != extension.source_sha256
                        or blob != extension.source_git_blob_sha1
                    )
                )
                or not any(
                    source_by_id[ref].get("type") == "git-commit"
                    and source_by_id[ref].get("commitSha")
                    == source_commit_sha
                    for ref in item_refs
                )
                or not any(
                    source_by_id[ref].get("type") in {"repository-license", "font-license"}
                    and source_by_id[ref].get("licenseExpression") == license_expression
                    for ref in item_refs
                )
            ):
                raise TemplatePackageValidationError("template_package_rights_evidence_invalid")
            actual[path] = (digest, blob, license_expression)
    if actual != expected:
        raise TemplatePackageValidationError("template_package_rights_evidence_not_exact")
    if any(
        re.search(
            rf"(?<![A-Za-z0-9.+-]){re.escape(item.license_ref)}(?![A-Za-z0-9.+-])",
            manifest.license.expression,
        )
        is None
        for item in inventory.files
    ):
        raise TemplatePackageValidationError("template_package_license_expression_incomplete")


def validate_browser_golden(
    root: Path,
    manifest: TemplatePackageManifest,
    template: dict[str, Any],
    golden: dict[str, Any],
) -> None:
    layouts = template.get("layouts")
    expected_ids = [layout.get("id") for layout in layouts] if isinstance(layouts, list) else []
    screenshots = golden.get("screenshots")
    checks = golden.get("checks")
    if (
        golden.get("schemaVersion") != "presenton.template-browser-golden/v1"
        or golden.get("templateId") != manifest.template_id
        or golden.get("engineId") != manifest.engine.id
        or golden.get("engineContractSha256") != manifest.engine.contract_sha256
        or golden.get("entrypointSha256") != manifest.entrypoint.sha256
        or golden.get("networkPolicy") != "deny"
        or golden.get("viewport") != {
            "width": manifest.presentation.width,
            "height": manifest.presentation.height,
        }
        or golden.get("layoutIds") != expected_ids
        or not isinstance(screenshots, list)
        or len(screenshots) != len(expected_ids)
        or not isinstance(checks, dict)
        or checks.get("renderedLayoutCount") != len(expected_ids)
        or checks.get("nonBlankLayoutCount") != len(expected_ids)
        or checks.get("missingImages") != 0
        or checks.get("externalRequests") != []
        or checks.get("localFontsLoaded") is not True
        or checks.get("consoleErrors") != []
        or checks.get("paintReadinessContract") != "passed"
        or golden.get("status") != "passed-browser-golden"
    ):
        raise TemplatePackageValidationError("template_package_browser_golden_invalid")

    renderer = golden.get("renderer")
    if not isinstance(renderer, dict):
        raise TemplatePackageValidationError("template_package_browser_golden_invalid")
    renderer_path = renderer.get("modulePath")
    renderer_sha256 = renderer.get("moduleSha256")
    if not isinstance(renderer_path, str) or not isinstance(renderer_sha256, str):
        raise TemplatePackageValidationError("template_package_browser_golden_invalid")
    _verify_digest(
        _confined_file(root, Path(renderer_path)).read_bytes(),
        renderer_sha256,
        "browser_golden_renderer",
    )

    capture_reference = golden.get("captureEvidence")
    if not isinstance(capture_reference, dict):
        raise TemplatePackageValidationError("template_package_browser_capture_missing")
    capture_path = capture_reference.get("path")
    capture_sha256 = capture_reference.get("sha256")
    if (
        not isinstance(capture_path, str)
        or not isinstance(capture_sha256, str)
        or capture_reference.get("adapter") != "chromium-cdp/Page.captureScreenshot"
    ):
        raise TemplatePackageValidationError("template_package_browser_capture_invalid")
    capture_bytes = _confined_file(root, Path(capture_path)).read_bytes()
    _verify_digest(capture_bytes, capture_sha256, "browser_capture")
    capture = _json_object(capture_bytes, "browser_capture")
    captured_screenshots = capture.get("screenshots")
    if (
        capture.get("schemaVersion")
        != "presenton.template-browser-capture-evidence/v1"
        or capture.get("templateId") != manifest.template_id
        or capture.get("rendererModuleSha256") != renderer_sha256
        or capture.get("entrypointSha256") != manifest.entrypoint.sha256
        or capture.get("status") != "passed-browser-capture"
        or capture.get("checks") != checks
        or not isinstance(captured_screenshots, list)
        or len(captured_screenshots) != len(screenshots)
    ):
        raise TemplatePackageValidationError("template_package_browser_capture_invalid")

    for expected_id, screenshot in zip(expected_ids, screenshots, strict=True):
        if not isinstance(screenshot, dict) or screenshot.get("layoutId") != expected_id:
            raise TemplatePackageValidationError("template_package_browser_golden_invalid")
        screenshot_path = screenshot.get("path")
        screenshot_sha256 = screenshot.get("sha256")
        if not isinstance(screenshot_path, str) or not isinstance(screenshot_sha256, str):
            raise TemplatePackageValidationError("template_package_browser_golden_invalid")
        screenshot_bytes = _confined_file(root, Path(screenshot_path)).read_bytes()
        _verify_digest(screenshot_bytes, screenshot_sha256, "browser_golden_screenshot")
        try:
            with Image.open(io.BytesIO(screenshot_bytes)) as image:
                if image.format != "PNG" or image.size != (
                    manifest.presentation.width,
                    manifest.presentation.height,
                ):
                    raise TemplatePackageValidationError(
                        "template_package_browser_golden_geometry_mismatch"
                    )
                extrema = image.convert("RGB").getextrema()
                if all(low == high for low, high in extrema):
                    raise TemplatePackageValidationError("template_package_browser_golden_blank")
        except TemplatePackageValidationError:
            raise
        except Exception as error:
            raise TemplatePackageValidationError(
                "template_package_browser_golden_invalid"
            ) from error

    for screenshot, captured in zip(screenshots, captured_screenshots, strict=True):
        if (
            not isinstance(captured, dict)
            or captured.get("layoutId") != screenshot.get("layoutId")
            or captured.get("goldenSha256") != screenshot.get("sha256")
            or captured.get("exact") is not True
            or captured.get("badPixelRatio") != 0
            or captured.get("meanAbsoluteError") != 0
        ):
            raise TemplatePackageValidationError("template_package_browser_capture_invalid")


def _git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode()
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def _confined_file(root: Path, path_value: Path) -> Path:
    value = _confined_below(root, path_value.as_posix())
    if not value.is_file() or value.is_symlink():
        raise TemplatePackageValidationError("template_package_file_missing")
    return value


def _confined_below(root: Path, relative: str) -> Path:
    if "\\" in relative:
        raise TemplatePackageValidationError("template_package_path_invalid")
    logical = PurePosixPath(relative)
    if logical.is_absolute() or not logical.parts or ".." in logical.parts:
        raise TemplatePackageValidationError("template_package_path_invalid")
    candidate = (root / Path(*logical.parts)).resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise TemplatePackageValidationError("template_package_path_escape") from error
    return candidate


def _verify_digest(data: bytes, expected: str, label: str) -> None:
    if hashlib.sha256(data).hexdigest() != expected:
        raise TemplatePackageValidationError(f"template_package_{label}_hash_mismatch")


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TemplatePackageValidationError(f"template_package_{label}_invalid_json") from error
    if not isinstance(value, dict):
        raise TemplatePackageValidationError(f"template_package_{label}_invalid")
    return value


__all__ = [
    "TemplatePackageValidationError",
    "validate_browser_golden",
    "validate_rights_evidence",
]
