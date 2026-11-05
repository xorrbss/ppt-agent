"""Template material lives in the existing owner-bound artifact object store."""
from __future__ import annotations

from copy import deepcopy
import json

from services.artifact_store import ArtifactStoreError, configured_public_artifact_service
from utils.hashing import canonical_json_bytes

MAX_MATERIAL_BYTES = 128 * 1024 * 1024


async def persist_material(session, *, package, style_projection, operation_id):
    from services.svg_templates.catalog import principal
    owner, tenant = principal()
    material = {"schema_version": "svg-template-material/v1", "package": package,
                "style_projection": style_projection}
    payload = canonical_json_bytes(material)
    if len(payload) > MAX_MATERIAL_BYTES:
        raise ValueError("svg_template_material_size_limit")
    ref = await configured_public_artifact_service().persist_bytes(session,
        owner_id=owner, tenant_id=tenant, kind="source-manifest", media_type="application/json",
        payload=payload, created_by_operation_id=operation_id, retention_class="published")
    manifest = package.get("font_manifest", {})
    package_summary = {"schema_version": "svg-template-material-ref/v1",
        "artifact_ref": ref.model_dump(mode="json", by_alias=True),
        "source_canvas": package.get("source_canvas"), "issues": package.get("issues", []),
        "font_manifest": {"digest": manifest.get("digest"), "required_families": manifest.get("required_families", []),
            "assets": [{k: v for k, v in asset.items() if k != "data_base64"} for asset in manifest.get("assets", [])]}}
    profile_summary = {key: deepcopy(value) for key, value in style_projection.items()
                       if key not in {"references"}}
    return package_summary, profile_summary


async def load_material(session, version):
    from services.svg_templates.catalog import principal
    owner, tenant = principal()
    envelope = version.package
    if envelope.get("schema_version") != "svg-template-material-ref/v1":
        raise ValueError("svg_template_material_reference_invalid")
    from models.sql.public_artifact import ArtifactRef
    ref = ArtifactRef.model_validate(envelope.get("artifact_ref"))
    if ref.owner_id != owner or ref.tenant_id != tenant or ref.kind != "source-manifest":
        raise ValueError("svg_template_material_scope_mismatch")
    service = configured_public_artifact_service()
    try:
        current = await service.resolve_ref(session, artifact_id=ref.artifact_id,
            owner_id=owner, tenant_id=tenant, expected_digest=ref.sha256, check_object=False)
        payload, _ = await service.read_verified_ref_bytes(current, max_bytes=MAX_MATERIAL_BYTES)
    except ArtifactStoreError as error:
        raise ValueError("svg_template_material_unavailable") from error
    material = json.loads(payload)
    if (not isinstance(material, dict) or material.get("schema_version") != "svg-template-material/v1"
            or not isinstance(material.get("package"), dict) or not isinstance(material.get("style_projection"), dict)):
        raise ValueError("svg_template_material_invalid")
    return material


async def persist_adaptation_material(session, *, payload, operation_id):
    from services.svg_templates.catalog import principal
    owner, tenant = principal()
    raw = canonical_json_bytes({"schema_version": "svg-template-adaptation-material/v1", "adaptation": payload})
    if len(raw) > MAX_MATERIAL_BYTES:
        raise ValueError("svg_template_material_size_limit")
    ref = await configured_public_artifact_service().persist_bytes(session, owner_id=owner, tenant_id=tenant,
        kind="source-manifest", media_type="application/json", payload=raw,
        created_by_operation_id=operation_id, retention_class="published")
    return {"schema_version": "svg-template-adaptation-ref/v1", "artifact_ref": ref.model_dump(mode="json", by_alias=True)}


async def load_adaptation_material(session, envelope):
    from services.svg_templates.catalog import principal
    from models.sql.public_artifact import ArtifactRef
    owner, tenant = principal()
    if envelope.get("schema_version") != "svg-template-adaptation-ref/v1":
        raise ValueError("svg_template_adaptation_reference_invalid")
    ref = ArtifactRef.model_validate(envelope["artifact_ref"])
    if ref.owner_id != owner or ref.tenant_id != tenant or ref.kind != "source-manifest":
        raise ValueError("svg_template_material_scope_mismatch")
    service = configured_public_artifact_service()
    try:
        current = await service.resolve_ref(session, artifact_id=ref.artifact_id,
            owner_id=owner, tenant_id=tenant, expected_digest=ref.sha256, check_object=False)
        raw, _ = await service.read_verified_ref_bytes(current, max_bytes=MAX_MATERIAL_BYTES)
    except ArtifactStoreError as error:
        raise ValueError("svg_template_adaptation_material_unavailable") from error
    material = json.loads(raw)
    if (not isinstance(material, dict) or material.get("schema_version") != "svg-template-adaptation-material/v1"
            or not isinstance(material.get("adaptation"), dict)):
        raise ValueError("svg_template_adaptation_material_invalid")
    return material["adaptation"]


async def template_frame_preview(session, version, material) -> bytes | None:
    """The retained review render of the template's own cover frame (else its first frame).

    A template whose source deck could not be rendered for the design reference
    (for example a deck with embedded content) has no reference picture, but every
    published framed version keeps the frame renders it was approved on.
    """
    import uuid
    from services.svg_templates.catalog import principal
    owner, tenant = principal()
    entries = {entry.get("frame_id"): entry for entry in (version.review_evidence or {}).get("frames", [])
               if isinstance(entry, dict)}
    frames = material["package"].get("frames", [])
    ordered = sorted(frames, key=lambda frame: frame.get("role") != "cover")
    service = configured_public_artifact_service()
    for frame in ordered:
        ref = (entries.get(frame.get("frame_id")) or {}).get("frame_image_ref")
        if not isinstance(ref, dict) or not ref.get("artifact_id") or not ref.get("sha256"):
            continue
        try:
            current = await service.resolve_ref(session, artifact_id=uuid.UUID(str(ref["artifact_id"])),
                owner_id=owner, tenant_id=tenant, expected_digest=ref["sha256"], check_object=False)
            if current.kind != "image-derivative" or current.media_type != "image/png":
                continue
            payload, _ = await service.read_verified_ref_bytes(current, max_bytes=MAX_MATERIAL_BYTES)
        except (ArtifactStoreError, ValueError):
            continue
        return payload
    return None
