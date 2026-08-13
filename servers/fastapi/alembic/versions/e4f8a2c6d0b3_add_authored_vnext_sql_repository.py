"""Add durable authored-vNext revision and export publication repository.

Revision ID: e4f8a2c6d0b3
Revises: d3e7a9c2b4f6

The schema is deliberately frozen here. Historical migrations must never import
live ORM metadata because a future model edit would otherwise rewrite history.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e4f8a2c6d0b3"
down_revision: str | None = "d3e7a9c2b4f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id() -> sa.Column:
    return sa.Column("id", sa.Uuid(), nullable=False, primary_key=True)


def _scope(*, owner: bool = False) -> list[sa.Column]:
    columns: list[sa.Column] = []
    if owner:
        columns.append(sa.Column("owner_id", sa.Uuid(), nullable=True))
    columns.extend(
        (
            sa.Column("owner_scope", sa.String(length=128), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
        )
    )
    return columns


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def _updated_at() -> sa.Column:
    return sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def _table_definitions(metadata: sa.MetaData) -> tuple[sa.Table, ...]:
    master = sa.Table(
        "authored_vnext_master_revisions",
        metadata,
        _id(),
        *_scope(owner=True),
        sa.Column("master_id", sa.String(length=256), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("digest", sa.String(length=71), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        _created_at(),
        sa.UniqueConstraint(
            "owner_scope", "tenant_id", "master_id", "revision", name="uq_avnext_master_revision"
        ),
        sa.CheckConstraint("revision >= 1", name="ck_avnext_master_revision_positive"),
        sa.CheckConstraint("status IN ('draft', 'published')", name="ck_avnext_master_status"),
        sa.Index(
            "ix_avnext_master_scope_id_created",
            "owner_scope",
            "tenant_id",
            "master_id",
            "created_at",
        ),
    )
    slide = sa.Table(
        "authored_vnext_slide_revisions",
        metadata,
        _id(),
        *_scope(owner=True),
        sa.Column("deck_id", sa.String(length=256), nullable=False),
        sa.Column("slide_id", sa.String(length=256), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("spec_digest", sa.String(length=71), nullable=False),
        sa.Column("master_digest", sa.String(length=71), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        _created_at(),
        sa.UniqueConstraint(
            "owner_scope",
            "tenant_id",
            "deck_id",
            "slide_id",
            "revision",
            name="uq_avnext_slide_revision",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_avnext_slide_revision_positive"),
        sa.Index(
            "ix_avnext_slide_scope_deck", "owner_scope", "tenant_id", "deck_id", "slide_id"
        ),
    )
    deck = sa.Table(
        "authored_vnext_deck_revisions",
        metadata,
        _id(),
        *_scope(owner=True),
        sa.Column("deck_id", sa.String(length=256), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("digest", sa.String(length=71), nullable=False),
        sa.Column("master_id", sa.String(length=256), nullable=False),
        sa.Column("master_revision", sa.BigInteger(), nullable=False),
        sa.Column("master_digest", sa.String(length=71), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        _created_at(),
        sa.UniqueConstraint(
            "owner_scope", "tenant_id", "deck_id", "revision", name="uq_avnext_deck_revision"
        ),
        sa.CheckConstraint("revision >= 1", name="ck_avnext_deck_revision_positive"),
        sa.Index(
            "ix_avnext_deck_scope_id_created",
            "owner_scope",
            "tenant_id",
            "deck_id",
            "created_at",
        ),
    )
    head = sa.Table(
        "authored_vnext_entity_heads",
        metadata,
        _id(),
        *_scope(),
        sa.Column("entity_kind", sa.String(length=16), nullable=False),
        sa.Column("entity_id", sa.String(length=256), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("digest", sa.String(length=71), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default="1"),
        _updated_at(),
        sa.UniqueConstraint(
            "owner_scope", "tenant_id", "entity_kind", "entity_id", name="uq_avnext_entity_head"
        ),
        sa.CheckConstraint("entity_kind IN ('master', 'deck')", name="ck_avnext_head_kind"),
        sa.CheckConstraint("revision >= 1 AND generation >= 1", name="ck_avnext_head_versions"),
    )
    candidate = sa.Table(
        "authored_vnext_candidates",
        metadata,
        _id(),
        *_scope(),
        sa.Column("candidate_digest", sa.String(length=71), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        _created_at(),
        sa.UniqueConstraint(
            "owner_scope", "tenant_id", "candidate_digest", name="uq_avnext_candidate_digest"
        ),
    )
    quality = sa.Table(
        "authored_vnext_quality_decisions",
        metadata,
        _id(),
        *_scope(),
        sa.Column("deck_id", sa.String(length=256), nullable=False),
        sa.Column("deck_revision", sa.BigInteger(), nullable=False),
        sa.Column("deck_digest", sa.String(length=71), nullable=False),
        sa.Column("decision_key_hash", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("evidence_digest", sa.String(length=71), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        _created_at(),
        sa.UniqueConstraint(
            "owner_scope", "tenant_id", "decision_key_hash", name="uq_avnext_quality_decision_key"
        ),
        sa.CheckConstraint("decision IN ('approved', 'rejected')", name="ck_avnext_quality_decision"),
        sa.Index(
            "ix_avnext_quality_deck_revision",
            "owner_scope",
            "tenant_id",
            "deck_id",
            "deck_revision",
        ),
    )
    export_job = sa.Table(
        "authored_vnext_export_jobs",
        metadata,
        _id(),
        *_scope(owner=True),
        sa.Column("deck_id", sa.String(length=256), nullable=False),
        sa.Column("deck_revision", sa.BigInteger(), nullable=False),
        sa.Column("deck_digest", sa.String(length=71), nullable=False),
        sa.Column("master_id", sa.String(length=256), nullable=False),
        sa.Column("master_revision", sa.BigInteger(), nullable=False),
        sa.Column("master_digest", sa.String(length=71), nullable=False),
        sa.Column("spec_digest", sa.String(length=71), nullable=False),
        sa.Column("asset_manifest_digest", sa.String(length=71), nullable=False),
        sa.Column("font_manifest_digest", sa.String(length=71), nullable=False),
        sa.Column("expected_package_digest", sa.String(length=71), nullable=True),
        sa.Column("expected_publication_revision", sa.BigInteger(), nullable=True),
        sa.Column("expected_publication_digest", sa.String(length=71), nullable=True),
        sa.Column("timeout_ms", sa.BigInteger(), nullable=False, server_default="120000"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("operation_fence", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("fence_token", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("worker_id", sa.String(length=256), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("failure_detail", sa.Text(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "owner_scope",
            "tenant_id",
            "deck_id",
            "idempotency_key_hash",
            name="uq_avnext_export_idempotency",
        ),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'cancel_requested', 'succeeded', 'failed', 'cancelled')",
            name="ck_avnext_export_state",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND fence_token >= 0 AND operation_fence >= 1",
            name="ck_avnext_export_counters",
        ),
        sa.CheckConstraint(
            "deck_revision >= 1 AND master_revision >= 1", name="ck_avnext_export_source_revisions"
        ),
        sa.CheckConstraint("timeout_ms > 0 AND max_attempts > 0", name="ck_avnext_export_limits"),
        sa.CheckConstraint(
            "(expected_publication_revision IS NULL AND expected_publication_digest IS NULL) OR "
            "(expected_publication_revision >= 1 AND expected_publication_digest IS NOT NULL)",
            name="ck_avnext_export_expected_publication",
        ),
        sa.Index("ix_avnext_export_claim", "state", "lease_expires_at", "created_at"),
    )
    artifact = sa.Table(
        "authored_vnext_export_artifact_receipts",
        metadata,
        _id(),
        *_scope(),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("deck_id", sa.String(length=256), nullable=False),
        sa.Column("deck_revision", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("artifact_digest", sa.String(length=71), nullable=False),
        sa.Column("readback_digest", sa.String(length=71), nullable=False),
        sa.Column("package_digest", sa.String(length=71), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("master_digest", sa.String(length=71), nullable=False),
        sa.Column("spec_digest", sa.String(length=71), nullable=False),
        sa.Column("asset_manifest_digest", sa.String(length=71), nullable=False),
        sa.Column("font_manifest_digest", sa.String(length=71), nullable=False),
        sa.Column("readback_verified", sa.Boolean(), nullable=False, server_default=sa.true()),
        _created_at(),
        sa.UniqueConstraint("job_id", name="uq_avnext_artifact_job"),
        sa.UniqueConstraint(
            "owner_scope", "tenant_id", "storage_key", name="uq_avnext_artifact_storage"
        ),
        sa.CheckConstraint("size_bytes >= 0", name="ck_avnext_artifact_size"),
        sa.CheckConstraint("readback_verified = true", name="ck_avnext_artifact_readback"),
        sa.Index(
            "ix_avnext_artifact_deck_created",
            "owner_scope",
            "tenant_id",
            "deck_id",
            "created_at",
        ),
    )
    render = sa.Table(
        "authored_vnext_render_receipts",
        metadata,
        _id(),
        *_scope(),
        sa.Column("deck_id", sa.String(length=256), nullable=False),
        sa.Column("deck_revision", sa.BigInteger(), nullable=False),
        sa.Column("slide_id", sa.String(length=256), nullable=False),
        sa.Column("slide_revision", sa.BigInteger(), nullable=False),
        sa.Column("renderer", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("input_digest", sa.String(length=71), nullable=False),
        sa.Column("output_digest", sa.String(length=71), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("width_px", sa.Integer(), nullable=False),
        sa.Column("height_px", sa.Integer(), nullable=False),
        _created_at(),
        sa.UniqueConstraint(
            "owner_scope", "tenant_id", "idempotency_key_hash", name="uq_avnext_render_receipt_key"
        ),
        sa.CheckConstraint(
            "deck_revision >= 1 AND slide_revision >= 1", name="ck_avnext_render_revisions"
        ),
        sa.CheckConstraint("width_px > 0 AND height_px > 0", name="ck_avnext_render_dimensions"),
        sa.Index(
            "ix_avnext_render_deck_slide",
            "owner_scope",
            "tenant_id",
            "deck_id",
            "slide_id",
            "created_at",
        ),
    )
    pointer = sa.Table(
        "authored_vnext_publication_pointers",
        metadata,
        _id(),
        *_scope(),
        sa.Column("publication_kind", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.String(length=256), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("digest", sa.String(length=71), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=True),
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        _updated_at(),
        sa.UniqueConstraint(
            "owner_scope",
            "tenant_id",
            "publication_kind",
            "entity_id",
            name="uq_avnext_publication_pointer",
        ),
        sa.CheckConstraint(
            "publication_kind IN ('master', 'deck_artifact')", name="ck_avnext_publication_kind"
        ),
        sa.CheckConstraint(
            "revision >= 1 AND generation >= 1", name="ck_avnext_publication_versions"
        ),
    )
    return master, slide, deck, head, candidate, quality, export_job, artifact, render, pointer


AUTHORED_VNEXT_SQL_TABLES = _table_definitions(sa.MetaData())


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    for table in AUTHORED_VNEXT_SQL_TABLES:
        if table.name not in existing:
            table.create(bind)


def downgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    for table in reversed(AUTHORED_VNEXT_SQL_TABLES):
        if table.name in existing:
            table.drop(bind)
