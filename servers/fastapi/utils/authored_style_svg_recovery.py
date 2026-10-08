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
import uuid
import zipfile
from xml.etree import ElementTree as ET

from PIL import Image

from services.svg_contract import SVG_CONTRACT_VERSION, source_digest, validate_svg
from utils.hashing import sha256_prefixed

RECOVERY_CANDIDATE_VERSION = "svg-validated-recovery/v2"
RECOVERY_PNG_MAX_BYTES = 8 * 1024 * 1024
PRIVATE_RECOVERY_ASSET_DIRECTORY_NAME = "recovery-private"
PRIVATE_RECOVERY_PROVENANCE_VERSION = "svg-recovery-private-source/v1"
_IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_FIT_METRICS = frozenset({"content_chars", "nonempty_lines", "title_chars", "image_group_count", "image_member_count"})
_AXES = frozenset({"source_fidelity", "screen_integrity", "readability", "design", "native_editability"})
_PROVENANCE_KEYS = frozenset({"version", "owner_id", "tenant_id", "source_presentation_id", "source_revision",
    "source_operation_id", "source_operation_state", "source_revision_digest", "source_svg_digest",
    "source_png_digest", "source_pptx_digest", "source_reference_evidence_digest", "source_export_identity_id",
    "source_svg_artifact_id", "source_png_artifact_id", "source_pptx_artifact_id", "source_slide_index"})
_TESTED_SCOPE_KEYS = frozenset({"content_digest", "profile_digest", "native_requirements_digest", "image_groups_digest"})


def recovery_digest(value: object) -> str:
    return sha256_prefixed(json.dumps(value, ensure_ascii=False, sort_keys=True).encode())


def recovery_style_identity(snapshot: Mapping) -> str:
    return recovery_digest({"style_id": snapshot.get("style_id"), "tokens": snapshot.get("tokens")})


def is_private_recovery_asset(path: Path, assets_directory: Path) -> bool:
    """Keep declared aliases and resolved private material out of public assets."""
    root = assets_directory.resolve()
    private = root / PRIVATE_RECOVERY_ASSET_DIRECTORY_NAME
    return (path.is_relative_to(private) or path.resolve().is_relative_to(private.resolve()))


def _provenance_valid(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) != _PROVENANCE_KEYS:
        return False
    try:
        if value["version"] != PRIVATE_RECOVERY_PROVENANCE_VERSION:
            return False
        for key in ("owner_id", "source_presentation_id", "source_operation_id", "source_export_identity_id",
                    "source_svg_artifact_id", "source_png_artifact_id", "source_pptx_artifact_id"):
            if not isinstance(value[key], str) or str(uuid.UUID(value[key])) != value[key]:
                return False
        if (not isinstance(value["tenant_id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", value["tenant_id"])
                or type(value["source_revision"]) is not int or value["source_revision"] < 1
                or type(value["source_slide_index"]) is not int or value["source_slide_index"] < 0
                or value["source_operation_state"] not in {"succeeded", "completed"}):
            return False
        return all(isinstance(value[key], str) and _DIGEST.fullmatch(value[key]) for key in (
            "source_revision_digest", "source_svg_digest", "source_png_digest", "source_pptx_digest",
            "source_reference_evidence_digest"))
    except (ValueError, TypeError, KeyError):
        return False


def _private_namespace(provenance: Mapping) -> str:
    return recovery_digest({key: provenance[key] for key in ("owner_id", "tenant_id")}).removeprefix("sha256:")


def _private_asset(root: Path, reference: object, provenance: Mapping, suffix: str, limit: int) -> bytes:
    if not isinstance(reference, str):
        raise ValueError("svg_recovery_private_asset_required")
    prefix = Path(PRIVATE_RECOVERY_ASSET_DIRECTORY_NAME) / _private_namespace(provenance)
    if not Path(reference).is_relative_to(prefix) or not (root / reference).resolve().is_relative_to((root / prefix).resolve()):
        raise ValueError("svg_recovery_private_asset_required")
    return _asset(root, reference, suffix, limit)


def recovery_source_authorized(candidate: Mapping, allowed_source_evidence=None) -> bool:
    """Match separately supplied current server authority, never candidate claims.

    The caller obtains this evidence through the normal authenticated ownership
    and immutable revision/reference checks. Frozen candidate metadata cannot be
    used as that live permission witness. Missing evidence always excludes it.
    """
    from utils.get_env import is_disable_auth_enabled
    from utils.owner_context import get_current_owner_id, get_current_tenant_id

    if not isinstance(candidate, Mapping):
        return False
    provenance = candidate.get("private_provenance")
    if is_disable_auth_enabled() or not _provenance_valid(provenance):
        return False
    if (str(get_current_owner_id()) != provenance["owner_id"]
            or get_current_tenant_id() != provenance["tenant_id"]):
        return False
    if not isinstance(allowed_source_evidence, (list, tuple)):
        return False
    for evidence in allowed_source_evidence:
        if (not isinstance(evidence, Mapping) or evidence.get("provenance") != provenance
                or evidence.get("reference_kind") != "immutable_presentation_revision"
                or evidence.get("reference_allowed") is not True
                or evidence.get("authentication_method") != "verified_owner_context"
                or evidence.get("owner_id") != provenance["owner_id"]
                or evidence.get("tenant_id") != provenance["tenant_id"]):
            continue
        record = evidence.get("reference_evidence")
        if isinstance(record, Mapping) and recovery_digest(record) == provenance["source_reference_evidence_digest"]:
            return True
    return False


def _native_source_structure(data: bytes, slide_index: int) -> dict:
    """Observe existing OOXML native data shapes; no drawing or layout projection."""
    import posixpath

    ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main",
          "c": "http://schemas.openxmlformats.org/drawingml/2006/chart"}
    rid = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    relns = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
    with zipfile.ZipFile(BytesIO(data)) as package:
        part = f"ppt/slides/slide{slide_index + 1}.xml"
        slide = ET.fromstring(package.read(part))
        charts = slide.findall(".//c:chart", ns)
        shapes = []
        if charts:
            relations = ET.fromstring(package.read(f"ppt/slides/_rels/slide{slide_index + 1}.xml.rels"))
            by_id = {rel.get("Id"): rel for rel in relations.findall(relns)}
            for reference in charts:
                rel = by_id[reference.get(rid)]
                target = posixpath.normpath(posixpath.join("ppt/slides", rel.get("Target", "")))
                if rel.get("TargetMode") == "External" or not target.startswith("ppt/charts/"):
                    raise ValueError("svg_recovery_native_reference_invalid")
                chart = ET.fromstring(package.read(target))
                series = chart.findall(".//c:ser", ns)
                counts = [len(item.findall("c:val/c:numRef/c:numCache/c:pt", ns)) for item in series]
                if not counts or min(counts) < 1 or len(set(counts)) != 1 or chart.find(".//c:externalData", ns) is None:
                    raise ValueError("svg_recovery_native_data_incomplete")
                relations = ET.fromstring(package.read(posixpath.join(posixpath.dirname(target), "_rels", posixpath.basename(target) + ".rels")))
                workbook_id = chart.find(".//c:externalData", ns).get(rid)
                workbook_rel = next(rel for rel in relations.findall(relns) if rel.get("Id") == workbook_id)
                workbook = posixpath.normpath(posixpath.join(posixpath.dirname(target), workbook_rel.get("Target", "")))
                if (workbook_rel.get("TargetMode") == "External" or not workbook.startswith("ppt/embeddings/")
                        or not workbook.endswith(".xlsx") or workbook not in package.namelist()):
                    raise ValueError("svg_recovery_native_workbook_unavailable")
                shapes.append([counts[0], len(series)])
        return {"tables": [[len(table.findall("a:tr", ns)), len(table.findall("a:tblGrid/a:gridCol", ns))]
                for table in slide.findall(".//a:tbl", ns)], "charts": shapes}


async def verified_recovery_source_evidence(candidates, session) -> list[dict]:
    """Read existing immutable owner-bound source refs without creating grants."""
    from utils.get_env import is_disable_auth_enabled
    from utils.owner_context import get_current_owner_id, get_current_tenant_id

    owner, tenant = get_current_owner_id(), get_current_tenant_id()
    if not candidates or is_disable_auth_enabled() or owner is None or not tenant:
        return []
    candidates = [candidate for candidate in candidates if isinstance(candidate, Mapping)
        and _provenance_valid(candidate.get("private_provenance"))
        and candidate["private_provenance"]["owner_id"] == str(owner)
        and candidate["private_provenance"]["tenant_id"] == tenant]
    if not candidates:
        return []
    from sqlalchemy import select
    from models.sql.public_artifact import PublicArtifact, PublicExportIdentity, PublicSlideCheckpoint
    from models.sql.public_admission import PublicOperationAdmission
    from models.sql.presentation import PresentationModel
    from models.sql.workspace import WorkspaceOperation
    from services.artifact_store import ArtifactStoreError, configured_public_artifact_service
    from services.generation_core.export.svg_evidence import SVG_SLIDE_CHECKPOINT_STAGE
    from services.public_export_identity import restore_canonical_export_identity, PublicExportIdentityBindingError
    from utils.datetime_utils import as_utc, get_current_utc_datetime

    service, allowed, seen = configured_public_artifact_service(), [], set()
    for candidate in candidates:
        provenance = candidate.get("private_provenance") if isinstance(candidate, Mapping) else None
        if (not _provenance_valid(provenance) or provenance["owner_id"] != str(owner)
                or provenance["tenant_id"] != tenant):
            continue
        key = recovery_digest(provenance)
        if key in seen:
            continue
        seen.add(key)
        try:
            presentation_id, operation_id = uuid.UUID(provenance["source_presentation_id"]), uuid.UUID(provenance["source_operation_id"])
            presentation = await session.scalar(select(PresentationModel).where(
                PresentationModel.id == presentation_id, PresentationModel.owner_id == owner))
            operation = await session.scalar(select(WorkspaceOperation).where(
                WorkspaceOperation.id == operation_id, WorkspaceOperation.owner_id == owner))
            admission = await session.scalar(select(PublicOperationAdmission).where(
                PublicOperationAdmission.operation_id == operation_id,
                PublicOperationAdmission.owner_id == owner, PublicOperationAdmission.tenant_id == tenant))
            identity = await session.scalar(select(PublicExportIdentity).where(
                PublicExportIdentity.id == uuid.UUID(provenance["source_export_identity_id"]),
                PublicExportIdentity.owner_id == owner, PublicExportIdentity.tenant_id == tenant,
                PublicExportIdentity.presentation_id == presentation_id,
                PublicExportIdentity.revision == provenance["source_revision"]))
            if (presentation is None or presentation.lifecycle_status != "published" or operation is None
                    or operation.state != "succeeded" or operation.presentation_id != presentation_id
                    or admission is None or admission.state != "succeeded"
                    or operation.expected_revision != provenance["source_revision"] or identity is None
                    or identity.state != "succeeded" or identity.claimed_by_operation_id != operation_id
                    or identity.export_mode != "pptx" or identity.output_mode != "svg-native"
                    or "sha256:" + identity.canonical_digest != provenance["source_revision_digest"]
                    or identity.result_artifact_id != uuid.UUID(provenance["source_pptx_artifact_id"])):
                continue
            pointer = await session.scalar(select(PublicSlideCheckpoint).where(
                PublicSlideCheckpoint.operation_id == operation_id,
                PublicSlideCheckpoint.revision == provenance["source_revision"],
                PublicSlideCheckpoint.slide_index == provenance["source_slide_index"],
                PublicSlideCheckpoint.artifact_id == uuid.UUID(provenance["source_svg_artifact_id"]),
                PublicSlideCheckpoint.stage_version == SVG_SLIDE_CHECKPOINT_STAGE))
            if pointer is None:
                continue
            checkpoint_row = await session.scalar(select(PublicArtifact).where(
                PublicArtifact.id == identity.source_artifact_id, PublicArtifact.owner_id == owner,
                PublicArtifact.tenant_id == tenant, PublicArtifact.kind == "checkpoint",
                PublicArtifact.lifecycle_state == "committed", PublicArtifact.tombstoned_at.is_(None)))
            if (checkpoint_row is None or checkpoint_row.created_by_operation_id != operation_id
                    or (checkpoint_row.expires_at is not None and as_utc(checkpoint_row.expires_at) <= get_current_utc_datetime())):
                continue
            checkpoint_ref = await service.resolve_ref(session, artifact_id=checkpoint_row.id, owner_id=owner,
                tenant_id=tenant, expected_digest=checkpoint_row.sha256)
            captured = restore_canonical_export_identity(checkpoint_ref, await service.read_ref_bytes(checkpoint_ref))
            capture_slide = next((slide for slide in captured.slides if slide.slide_index == pointer.slide_index), None)
            if (captured.canonical_digest != identity.canonical_digest or captured.owner_id != owner
                    or captured.tenant_id != tenant or captured.presentation_id != presentation_id
                    or captured.source_revision != provenance["source_revision"] or capture_slide is None
                    or capture_slide.artifact.artifact_id != pointer.artifact_id
                    or capture_slide.input_digest != pointer.input_digest):
                continue
            refs, bytes_by_kind = {}, {}
            for kind, ref_key, digest_key in (("slide-svg", "source_svg_artifact_id", "source_svg_digest"),
                    ("slide-png", "source_png_artifact_id", "source_png_digest"),
                    ("presentation-pptx", "source_pptx_artifact_id", "source_pptx_digest")):
                artifact_id = uuid.UUID(provenance[ref_key])
                row = await session.scalar(select(PublicArtifact).where(PublicArtifact.id == artifact_id,
                    PublicArtifact.owner_id == owner, PublicArtifact.tenant_id == tenant,
                    PublicArtifact.kind == kind, PublicArtifact.created_by_operation_id == operation_id,
                    PublicArtifact.lifecycle_state == "committed", PublicArtifact.tombstoned_at.is_(None)))
                if row is None or (row.expires_at is not None and as_utc(row.expires_at) <= get_current_utc_datetime()):
                    break
                ref = await service.resolve_ref(session, artifact_id=artifact_id, owner_id=owner,
                    tenant_id=tenant, expected_digest=provenance[digest_key])
                data = await service.read_ref_bytes(ref)
                if sha256_prefixed(data) != provenance[digest_key]:
                    break
                refs[kind] = {"id": str(ref.artifact_id), "digest": "sha256:" + ref.sha256, "bytes": len(data)}
                bytes_by_kind[kind] = data
            if len(refs) != 3:
                continue
            native = _native_source_structure(bytes_by_kind["presentation-pptx"], provenance["source_slide_index"])
            fit = candidate.get("content_fit") or {}
            if fit.get("table_shapes") != native["tables"] or fit.get("chart_shapes") != native["charts"]:
                continue
            record = {"owner_id": str(owner), "tenant_id": tenant, "presentation_id": str(presentation_id),
                "revision": identity.revision, "operation_id": str(operation_id), "operation_state": operation.state,
                "export_identity_id": str(identity.id), "export_identity_digest": "sha256:" + identity.canonical_digest,
                "checkpoint_input_digest": "sha256:" + pointer.input_digest,
                "checkpoint_validation_digest": "sha256:" + capture_slide.validation_digest,
                "slide_index": pointer.slide_index, "artifacts": refs, "native_structure": native}
            verified_provenance = {"version": PRIVATE_RECOVERY_PROVENANCE_VERSION,
                "owner_id": str(owner), "tenant_id": tenant, "source_presentation_id": str(presentation.id),
                "source_revision": identity.revision, "source_operation_id": str(operation.id),
                "source_operation_state": operation.state, "source_revision_digest": "sha256:" + captured.canonical_digest,
                "source_svg_digest": refs["slide-svg"]["digest"], "source_png_digest": refs["slide-png"]["digest"],
                "source_pptx_digest": refs["presentation-pptx"]["digest"],
                "source_reference_evidence_digest": recovery_digest(record), "source_export_identity_id": str(identity.id),
                "source_svg_artifact_id": refs["slide-svg"]["id"], "source_png_artifact_id": refs["slide-png"]["id"],
                "source_pptx_artifact_id": refs["presentation-pptx"]["id"], "source_slide_index": pointer.slide_index}
            if verified_provenance != provenance:
                continue
            allowed.append({"provenance": verified_provenance, "reference_evidence": record,
                "reference_kind": "immutable_presentation_revision", "reference_allowed": True,
                "authentication_method": "verified_owner_context", "owner_id": str(owner), "tenant_id": tenant})
        except (ArtifactStoreError, PublicExportIdentityBindingError, OSError,
                ValueError, TypeError, KeyError, StopIteration, zipfile.BadZipFile, ET.ParseError):
            continue
    return allowed


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
        provenance = candidate.get("private_provenance")
        if (not _provenance_valid(provenance) or provenance["source_svg_digest"] != candidate.get("svg_digest")
                or provenance["source_png_digest"] != candidate.get("png_digest")):
            return False
        scopes = candidate.get("tested_content_scopes")
        if not isinstance(scopes, list) or not scopes or any(
                not isinstance(scope, Mapping) or set(scope) != _TESTED_SCOPE_KEYS
                or any(not isinstance(value, str) or not _DIGEST.fullmatch(value) for value in scope.values())
                for scope in scopes):
            return False
        reference = candidate.get("png_asset_ref")
        if (not isinstance(reference, str) or not reference or ":" in reference or "\\" in reference
                or Path(reference).is_absolute() or ".." in Path(reference).parts
                or Path(reference).suffix.lower() != ".png"):
            return False
        if not Path(reference).is_relative_to(Path(PRIVATE_RECOVERY_ASSET_DIRECTORY_NAME) / _private_namespace(provenance)):
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
        if (evidence.get("private_provenance") != provenance
                or evidence.get("tested_content_scopes_digest") != recovery_digest(scopes)):
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
            provenance = entry.get("private_provenance")
            if not _provenance_valid(provenance):
                continue
            svg_bytes = _private_asset(root, entry.get("svg"), provenance, ".svg", 2 * 1024 * 1024)
            png_bytes = _private_asset(root, entry.get("png"), provenance, ".png", RECOVERY_PNG_MAX_BYTES)
            receipt_bytes = _private_asset(root, entry.get("receipt"), provenance, ".json", 256 * 1024)
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
                "private_provenance", "tested_content_scopes",
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


def read_validated_recovery_png(candidate: Mapping, *, assets_directory: Path | None = None,
                                allowed_source_evidence=None) -> bytes | None:
    """Read pinned managed material only after the caller's current authority check.

    Store the reference in checkpoints; avoid duplicating a page PNG in each
    candidate record. Never rebind edited or missing material to another asset.
    """
    if not recovery_source_authorized(candidate, allowed_source_evidence) or not recovery_candidate_valid(candidate):
        return None
    if assets_directory is None:
        from utils.authored_styles import AUTHORED_STYLE_ASSETS_DIRECTORY

        assets_directory = AUTHORED_STYLE_ASSETS_DIRECTORY
    try:
        data = _private_asset(assets_directory.resolve(), candidate["png_asset_ref"], candidate["private_provenance"],
                              ".png", RECOVERY_PNG_MAX_BYTES)
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


def validated_style_recovery_snapshot(style: object, snapshot: Mapping, *, allowed_source_evidence=None) -> list[dict]:
    """Freeze catalogue bytes before the enclosing style snapshot is hashed."""
    identity = recovery_style_identity(snapshot)
    return [copy.deepcopy(candidate) for candidate in getattr(style, "validated_recovery_candidates", ())
            if recovery_source_authorized(candidate, allowed_source_evidence)
            and recovery_candidate_valid(candidate) and candidate["style_id"] == snapshot.get("style_id")
            and candidate["style_identity_digest"] == identity]
