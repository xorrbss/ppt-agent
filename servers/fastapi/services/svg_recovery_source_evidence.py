"""Verify immutable source authority for private SVG recovery candidates."""

from __future__ import annotations

from collections.abc import Mapping
import uuid
import zipfile
from xml.etree import ElementTree as ET

from utils.hashing import sha256_prefixed
from utils.authored_style_svg_recovery import (
    PRIVATE_RECOVERY_PROVENANCE_VERSION,
    _native_source_structure,
    _provenance_valid,
    recovery_digest,
)


async def verified_recovery_source_evidence(candidates, session) -> list[dict]:
    """Read existing immutable owner-bound source refs without creating grants."""
    from utils.get_env import is_disable_auth_enabled
    from utils.owner_context import get_current_owner_id, get_current_tenant_id

    owner, tenant = get_current_owner_id(), get_current_tenant_id()
    if not candidates or is_disable_auth_enabled() or owner is None or not tenant:
        return []
    candidates = [
        candidate
        for candidate in candidates
        if isinstance(candidate, Mapping)
        and _provenance_valid(candidate.get("private_provenance"))
        and candidate["private_provenance"]["owner_id"] == str(owner)
        and candidate["private_provenance"]["tenant_id"] == tenant
    ]
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
        if (
            not _provenance_valid(provenance)
            or provenance["owner_id"] != str(owner)
            or provenance["tenant_id"] != tenant
        ):
            continue
        key = recovery_digest(provenance)
        if key in seen:
            continue
        seen.add(key)
        try:
            presentation_id, operation_id = (
                uuid.UUID(provenance["source_presentation_id"]),
                uuid.UUID(provenance["source_operation_id"]),
            )
            presentation = await session.scalar(
                select(PresentationModel).where(
                    PresentationModel.id == presentation_id, PresentationModel.owner_id == owner
                )
            )
            operation = await session.scalar(
                select(WorkspaceOperation).where(
                    WorkspaceOperation.id == operation_id, WorkspaceOperation.owner_id == owner
                )
            )
            admission = await session.scalar(
                select(PublicOperationAdmission).where(
                    PublicOperationAdmission.operation_id == operation_id,
                    PublicOperationAdmission.owner_id == owner,
                    PublicOperationAdmission.tenant_id == tenant,
                )
            )
            identity = await session.scalar(
                select(PublicExportIdentity).where(
                    PublicExportIdentity.id == uuid.UUID(provenance["source_export_identity_id"]),
                    PublicExportIdentity.owner_id == owner,
                    PublicExportIdentity.tenant_id == tenant,
                    PublicExportIdentity.presentation_id == presentation_id,
                    PublicExportIdentity.revision == provenance["source_revision"],
                )
            )
            if (
                presentation is None
                or presentation.lifecycle_status != "published"
                or operation is None
                or operation.state != "succeeded"
                or operation.presentation_id != presentation_id
                or admission is None
                or admission.state != "succeeded"
                or operation.expected_revision != provenance["source_revision"]
                or identity is None
                or identity.state != "succeeded"
                or identity.claimed_by_operation_id != operation_id
                or identity.export_mode != "pptx"
                or identity.output_mode != "svg-native"
                or "sha256:" + identity.canonical_digest != provenance["source_revision_digest"]
                or identity.result_artifact_id != uuid.UUID(provenance["source_pptx_artifact_id"])
            ):
                continue
            pointer = await session.scalar(
                select(PublicSlideCheckpoint).where(
                    PublicSlideCheckpoint.operation_id == operation_id,
                    PublicSlideCheckpoint.revision == provenance["source_revision"],
                    PublicSlideCheckpoint.slide_index == provenance["source_slide_index"],
                    PublicSlideCheckpoint.artifact_id == uuid.UUID(provenance["source_svg_artifact_id"]),
                    PublicSlideCheckpoint.stage_version == SVG_SLIDE_CHECKPOINT_STAGE,
                )
            )
            if pointer is None:
                continue
            checkpoint_row = await session.scalar(
                select(PublicArtifact).where(
                    PublicArtifact.id == identity.source_artifact_id,
                    PublicArtifact.owner_id == owner,
                    PublicArtifact.tenant_id == tenant,
                    PublicArtifact.kind == "checkpoint",
                    PublicArtifact.lifecycle_state == "committed",
                    PublicArtifact.tombstoned_at.is_(None),
                )
            )
            if (
                checkpoint_row is None
                or checkpoint_row.created_by_operation_id != operation_id
                or (
                    checkpoint_row.expires_at is not None
                    and as_utc(checkpoint_row.expires_at) <= get_current_utc_datetime()
                )
            ):
                continue
            checkpoint_ref = await service.resolve_ref(
                session,
                artifact_id=checkpoint_row.id,
                owner_id=owner,
                tenant_id=tenant,
                expected_digest=checkpoint_row.sha256,
            )
            captured = restore_canonical_export_identity(checkpoint_ref, await service.read_ref_bytes(checkpoint_ref))
            capture_slide = next((slide for slide in captured.slides if slide.slide_index == pointer.slide_index), None)
            if (
                captured.canonical_digest != identity.canonical_digest
                or captured.owner_id != owner
                or captured.tenant_id != tenant
                or captured.presentation_id != presentation_id
                or captured.source_revision != provenance["source_revision"]
                or capture_slide is None
                or capture_slide.artifact.artifact_id != pointer.artifact_id
                or capture_slide.input_digest != pointer.input_digest
            ):
                continue
            refs, bytes_by_kind = {}, {}
            for kind, ref_key, digest_key in (
                ("slide-svg", "source_svg_artifact_id", "source_svg_digest"),
                ("slide-png", "source_png_artifact_id", "source_png_digest"),
                ("presentation-pptx", "source_pptx_artifact_id", "source_pptx_digest"),
            ):
                artifact_id = uuid.UUID(provenance[ref_key])
                row = await session.scalar(
                    select(PublicArtifact).where(
                        PublicArtifact.id == artifact_id,
                        PublicArtifact.owner_id == owner,
                        PublicArtifact.tenant_id == tenant,
                        PublicArtifact.kind == kind,
                        PublicArtifact.created_by_operation_id == operation_id,
                        PublicArtifact.lifecycle_state == "committed",
                        PublicArtifact.tombstoned_at.is_(None),
                    )
                )
                if row is None or (row.expires_at is not None and as_utc(row.expires_at) <= get_current_utc_datetime()):
                    break
                ref = await service.resolve_ref(
                    session,
                    artifact_id=artifact_id,
                    owner_id=owner,
                    tenant_id=tenant,
                    expected_digest=provenance[digest_key],
                )
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
            record = {
                "owner_id": str(owner),
                "tenant_id": tenant,
                "presentation_id": str(presentation_id),
                "revision": identity.revision,
                "operation_id": str(operation_id),
                "operation_state": operation.state,
                "export_identity_id": str(identity.id),
                "export_identity_digest": "sha256:" + identity.canonical_digest,
                "checkpoint_input_digest": "sha256:" + pointer.input_digest,
                "checkpoint_validation_digest": "sha256:" + capture_slide.validation_digest,
                "slide_index": pointer.slide_index,
                "artifacts": refs,
                "native_structure": native,
            }
            verified_provenance = {
                "version": PRIVATE_RECOVERY_PROVENANCE_VERSION,
                "owner_id": str(owner),
                "tenant_id": tenant,
                "source_presentation_id": str(presentation.id),
                "source_revision": identity.revision,
                "source_operation_id": str(operation.id),
                "source_operation_state": operation.state,
                "source_revision_digest": "sha256:" + captured.canonical_digest,
                "source_svg_digest": refs["slide-svg"]["digest"],
                "source_png_digest": refs["slide-png"]["digest"],
                "source_pptx_digest": refs["presentation-pptx"]["digest"],
                "source_reference_evidence_digest": recovery_digest(record),
                "source_export_identity_id": str(identity.id),
                "source_svg_artifact_id": refs["slide-svg"]["id"],
                "source_png_artifact_id": refs["slide-png"]["id"],
                "source_pptx_artifact_id": refs["presentation-pptx"]["id"],
                "source_slide_index": pointer.slide_index,
            }
            if verified_provenance != provenance:
                continue
            allowed.append(
                {
                    "provenance": verified_provenance,
                    "reference_evidence": record,
                    "reference_kind": "immutable_presentation_revision",
                    "reference_allowed": True,
                    "authentication_method": "verified_owner_context",
                    "owner_id": str(owner),
                    "tenant_id": tenant,
                }
            )
        except (
            ArtifactStoreError,
            PublicExportIdentityBindingError,
            OSError,
            ValueError,
            TypeError,
            KeyError,
            StopIteration,
            zipfile.BadZipFile,
            ET.ParseError,
        ):
            continue
    return allowed
