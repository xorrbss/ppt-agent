"""Canonical retention references used by existing asset maintenance paths."""
from sqlalchemy import exists, inspect, select, true

from models.sql.svg_template import SvgTemplateAssetBinding


async def retention_schema_available(session) -> bool:
    """Old-schema legacy paths remain usable before the rollout migration.

    A disabled feature can still have retained documents after deployment, so
    the flag alone never turns off retention in a migrated database.
    """
    from services.svg_templates.availability import user_templates_enabled
    if user_templates_enabled():
        return True
    key = "svg_template_retention_schema_available"
    if key not in session.info:
        session.info[key] = await session.run_sync(
            lambda sync: inspect(sync.connection()).has_table(SvgTemplateAssetBinding.__tablename__))
    return session.info[key]


def asset_unretained(asset_id, *, schema_available=True):
    if not schema_available:
        return true()
    return ~exists(select(SvgTemplateAssetBinding.id).where(SvgTemplateAssetBinding.asset_id == asset_id))


def snapshot_unretained(snapshot_id, *, schema_available=True):
    if not schema_available:
        return true()
    return ~exists(select(SvgTemplateAssetBinding.id).where(SvgTemplateAssetBinding.snapshot_id == snapshot_id))


async def asset_is_retained(session, asset_id) -> bool:
    if not await retention_schema_available(session):
        return False
    return bool(await session.scalar(select(exists(select(SvgTemplateAssetBinding.id).where(
        SvgTemplateAssetBinding.asset_id == asset_id)))))


async def retain_artifacts(session, *, template_id, artifact_ids, version_id=None):
    import uuid
    from models.sql.public_artifact import PublicArtifact
    from services.svg_templates.catalog import principal, scoped
    owner, tenant = principal()
    from services.artifact_store import require_artifact_key_guard
    for artifact_id in sorted({uuid.UUID(str(value)) for value in artifact_ids}, key=str):
        existing = await session.scalar(select(SvgTemplateAssetBinding.id).where(
            SvgTemplateAssetBinding.template_id == template_id,
            SvgTemplateAssetBinding.artifact_id == artifact_id,
            SvgTemplateAssetBinding.version_id == version_id, *scoped(SvgTemplateAssetBinding)))
        if existing:
            continue
        artifact = await session.scalar(select(PublicArtifact).where(PublicArtifact.id == artifact_id,
            PublicArtifact.owner_id == owner, PublicArtifact.tenant_id == tenant,
            PublicArtifact.lifecycle_state == "committed"))
        if artifact is None:
            raise ValueError("svg_template_review_artifact_unavailable")
        await require_artifact_key_guard(session, artifact.to_ref())
        artifact = await session.scalar(select(PublicArtifact).where(PublicArtifact.id == artifact_id,
            PublicArtifact.owner_id == owner, PublicArtifact.tenant_id == tenant,
            PublicArtifact.lifecycle_state == "committed").execution_options(populate_existing=True))
        if artifact is None:
            raise ValueError("svg_template_review_artifact_unavailable")
        session.add(SvgTemplateAssetBinding(template_id=template_id, version_id=version_id,
            owner_id=owner, tenant_id=tenant, artifact_id=artifact_id,
            binding={"artifactId": str(artifact_id), "sha256": artifact.sha256, "kind": artifact.kind}))
    await session.flush()
