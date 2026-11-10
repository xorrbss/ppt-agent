"""Drop the authored tables that no kept code reads or writes any more.

Follow-up to d1a5c7e9b3f2 (owner decision 2026-11-11: the remaining unused
authored tables may go and their rows need no preservation).  Dropped:

* authored_vnext_export_jobs, authored_vnext_export_artifact_receipts and
  authored_vnext_render_receipts: the vNext export half was deleted with the
  release-gate retarget; account deletion only fenced leftover export jobs.
* authored_vnext_quality_decisions: no reader or writer.
* authored_vnext_generation_operations: only the deleted lease-fenced
  generation operations wrote it; account deletion only fenced leftover rows.
* authored_deck_emission_manifests and authored_deck_emission_active: only
  the deleted staging-retry import read the active pointer.
* authored_slide_editor_events: its write endpoint was deleted in B5.

Kept: authored_slide_checkpoints, authored_deck_plan_checkpoints (both svg)
and the master-pin storage (authored_vnext_master_revisions,
authored_vnext_slide_revisions, authored_vnext_deck_revisions,
authored_vnext_entity_heads, authored_vnext_candidates,
authored_vnext_publication_pointers) that the master router and the svg
master pin still use.

downgrade() recreates the exact shape the earlier revisions left behind
(columns in their physical order, defaults, constraints, indexes; on
PostgreSQL also the controller grant every table held and, for the editor
event ledger, the owner row-level-security policy of b5a0d3e7c9f1), so the
downgrades of a6b2c4d8e0f3 and b5a0d3e7c9f1 still find their tables.  The
recreated tables are always empty: upgrade() destroys the rows.

Revision ID: e7b2d4f6a8c1
Revises: d1a5c7e9b3f2
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e7b2d4f6a8c1"
down_revision: str | None = "d1a5c7e9b3f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONTROLLER_ROLE = "presenton_public_controller"
_REQUEST_POLICY = "presenton_owner_scope"
_CONTROLLER_POLICY = "presenton_owner_controller_authority"
_VISIBILITY_FUNCTION = "presenton_owner_row_visible"
_RLS_TABLE = "authored_slide_editor_events"

# Drop order: the active pointer references the manifests.
_DROP_ORDER = (
    "authored_vnext_export_artifact_receipts",
    "authored_vnext_render_receipts",
    "authored_vnext_export_jobs",
    "authored_vnext_quality_decisions",
    "authored_vnext_generation_operations",
    "authored_deck_emission_active",
    "authored_deck_emission_manifests",
    "authored_slide_editor_events",
)


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    for table in _DROP_ORDER:
        if _has_table(table):
            op.drop_table(table)


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def _updated_at() -> sa.Column:
    return sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def _owner_index(table: str) -> None:
    op.create_index(f"ix_{table}_owner_id", table, ["owner_id"], unique=False)


def _owner_foreign_key(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["owner_id"], ["user.id"], name=f"fk_{table}_owner_id_user", ondelete="CASCADE"
    )


def _create_authored_vnext_quality_decisions() -> None:
    table = "authored_vnext_quality_decisions"
    op.create_table(
        table,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_scope", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("deck_id", sa.String(length=256), nullable=False),
        sa.Column("deck_revision", sa.BigInteger(), nullable=False),
        sa.Column("deck_digest", sa.String(length=71), nullable=False),
        sa.Column("decision_key_hash", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("evidence_digest", sa.String(length=71), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        _created_at(),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_scope", "tenant_id", "decision_key_hash", name="uq_avnext_quality_decision_key"
        ),
        sa.CheckConstraint("decision IN ('approved', 'rejected')", name="ck_avnext_quality_decision"),
        _owner_foreign_key(table),
    )
    op.create_index(
        "ix_avnext_quality_deck_revision",
        table,
        ["owner_scope", "tenant_id", "deck_id", "deck_revision"],
        unique=False,
    )
    _owner_index(table)


def _create_authored_vnext_export_jobs() -> None:
    table = "authored_vnext_export_jobs"
    op.create_table(
        table,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("owner_scope", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
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
        sa.Column(
            "account_deletion_generation", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("request_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.PrimaryKeyConstraint("id"),
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
            "attempt_count >= 0 AND fence_token >= 0 AND operation_fence >= 1 "
            "AND account_deletion_generation >= 0",
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
        _owner_foreign_key(table),
    )
    op.create_index(
        "ix_avnext_export_claim", table, ["state", "lease_expires_at", "created_at"], unique=False
    )
    _owner_index(table)


def _create_authored_vnext_export_artifact_receipts() -> None:
    table = "authored_vnext_export_artifact_receipts"
    op.create_table(
        table,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_scope", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
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
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_avnext_artifact_job"),
        sa.UniqueConstraint(
            "owner_scope", "tenant_id", "storage_key", name="uq_avnext_artifact_storage"
        ),
        sa.CheckConstraint("size_bytes >= 0", name="ck_avnext_artifact_size"),
        sa.CheckConstraint("readback_verified = true", name="ck_avnext_artifact_readback"),
        _owner_foreign_key(table),
    )
    op.create_index(
        "ix_avnext_artifact_deck_created",
        table,
        ["owner_scope", "tenant_id", "deck_id", "created_at"],
        unique=False,
    )
    _owner_index(table)


def _create_authored_vnext_render_receipts() -> None:
    table = "authored_vnext_render_receipts"
    op.create_table(
        table,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_scope", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
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
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_scope", "tenant_id", "idempotency_key_hash", name="uq_avnext_render_receipt_key"
        ),
        sa.CheckConstraint(
            "deck_revision >= 1 AND slide_revision >= 1", name="ck_avnext_render_revisions"
        ),
        sa.CheckConstraint("width_px > 0 AND height_px > 0", name="ck_avnext_render_dimensions"),
        _owner_foreign_key(table),
    )
    op.create_index(
        "ix_avnext_render_deck_slide",
        table,
        ["owner_scope", "tenant_id", "deck_id", "slide_id", "created_at"],
        unique=False,
    )
    _owner_index(table)


def _create_authored_vnext_generation_operations() -> None:
    table = "authored_vnext_generation_operations"
    op.create_table(
        table,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("owner_scope", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("operation_id", sa.String(length=256), nullable=False),
        sa.Column("deck_id", sa.String(length=256), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("checkpoint_payload", sa.JSON(), nullable=True),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column("expected_revision", sa.BigInteger(), nullable=True),
        sa.Column("expected_digest", sa.String(length=71), nullable=True),
        sa.Column("result_revision", sa.BigInteger(), nullable=True),
        sa.Column("result_digest", sa.String(length=71), nullable=True),
        sa.Column(
            "account_deletion_generation", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("worker_id", sa.String(length=256), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_avnext_generation_operation_state",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["user.id"],
            name="fk_avnext_generation_operations_owner_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND fencing_token >= 0 AND version >= 0 "
            "AND account_deletion_generation >= 0",
            name="ck_avnext_generation_operation_counters",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_scope", "tenant_id", "operation_id", name="uq_avnext_generation_operation_id"
        ),
        sa.UniqueConstraint(
            "owner_scope",
            "tenant_id",
            "kind",
            "idempotency_key_hash",
            name="uq_avnext_generation_operation_idempotency",
        ),
    )
    op.create_index(
        "ix_avnext_generation_operation_dispatch",
        table,
        ["state", "lease_expires_at", "created_at"],
        unique=False,
    )


def _create_authored_deck_emission_manifests() -> None:
    table = "authored_deck_emission_manifests"
    op.create_table(
        table,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("presentation_id", sa.Uuid(), nullable=False),
        sa.Column("presentation_revision", sa.BigInteger(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("generation_attempt_id", sa.Uuid(), nullable=True),
        sa.Column("canonical_digest", sa.String(length=71), nullable=False),
        sa.Column("master_contract_digest", sa.String(length=71), nullable=False),
        sa.Column("composition_digest", sa.String(length=71), nullable=False),
        sa.Column("asset_manifest_digest", sa.String(length=71), nullable=False),
        sa.Column("font_manifest_digest", sa.String(length=71), nullable=False),
        sa.Column("quality_policy_version", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "tenant_id", "canonical_digest", name="uq_authored_deck_manifest_digest"
        ),
        sa.UniqueConstraint(
            "owner_id",
            "tenant_id",
            "presentation_id",
            "presentation_revision",
            "operation_id",
            name="uq_authored_deck_manifest_lineage",
        ),
        sa.CheckConstraint("presentation_revision >= 1", name="ck_authored_deck_manifest_revision"),
    )
    op.create_index(
        "ix_authored_deck_manifest_presentation",
        table,
        ["owner_id", "tenant_id", "presentation_id", "presentation_revision"],
        unique=False,
    )


def _create_authored_deck_emission_active() -> None:
    op.create_table(
        "authored_deck_emission_active",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("presentation_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_mode", sa.String(length=16), nullable=False),
        sa.Column("manifest_id", sa.Uuid(), nullable=True),
        sa.Column("presentation_revision", sa.BigInteger(), nullable=False),
        sa.Column("canonical_digest", sa.String(length=71), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        _updated_at(),
        sa.ForeignKeyConstraint(
            ["manifest_id"], ["authored_deck_emission_manifests.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "tenant_id", "presentation_id", name="uq_authored_deck_active_pointer"
        ),
        sa.CheckConstraint(
            "canonical_mode IN ('legacyHtml', 'bundleGraph')", name="ck_authored_deck_active_mode"
        ),
        sa.CheckConstraint(
            "presentation_revision >= 0 AND generation >= 1",
            name="ck_authored_deck_active_versions",
        ),
        sa.CheckConstraint(
            "(canonical_mode = 'legacyHtml' AND manifest_id IS NULL) OR "
            "(canonical_mode = 'bundleGraph' AND manifest_id IS NOT NULL)",
            name="ck_authored_deck_active_manifest_mode",
        ),
    )


def _create_authored_slide_editor_events() -> None:
    table = "authored_slide_editor_events"
    event_types = ", ".join(
        f"'{name}'"
        for name in (
            "slide_regenerated",
            "archetype_switched",
            "bulk_text_edit",
            "slide_deleted",
            "export_completed",
        )
    )
    op.create_table(
        table,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("presentation_id", sa.Uuid(), nullable=False),
        sa.Column("slide_index", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("archetype", sa.String(length=32), nullable=True),
        sa.Column("previous_archetype", sa.String(length=32), nullable=True),
        sa.Column("changed_characters", sa.Integer(), nullable=True),
        sa.Column("event_key_hash", sa.String(length=64), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            f"event_type IN ({event_types})", name="ck_authored_slide_editor_events_type"
        ),
        sa.CheckConstraint(
            "(event_type IN ('export_completed')) OR slide_index IS NOT NULL",
            name="ck_authored_slide_editor_events_slide_scope",
        ),
        sa.CheckConstraint(
            "slide_index IS NULL OR slide_index >= 0",
            name="ck_authored_slide_editor_events_slide_index",
        ),
        sa.CheckConstraint(
            "changed_characters IS NULL OR changed_characters >= 0",
            name="ck_authored_slide_editor_events_changed_characters",
        ),
        sa.CheckConstraint(
            "event_type != 'archetype_switched' OR "
            "(archetype IS NOT NULL AND previous_archetype IS NOT NULL)",
            name="ck_authored_slide_editor_events_switch_evidence",
        ),
        sa.ForeignKeyConstraint(
            ["presentation_id"],
            ["presentations.id"],
            name="fk_authored_slide_editor_events_presentation_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_key_hash", name="uq_authored_slide_editor_events_key_hash"),
    )
    op.create_index("ix_authored_slide_editor_events_owner_id", table, ["owner_id"], unique=False)
    op.create_index(
        "ix_authored_slide_editor_events_created_at", table, ["created_at"], unique=False
    )
    op.create_index(
        "ix_authored_slide_editor_events_archetype_window",
        table,
        ["archetype", "event_type", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_authored_slide_editor_events_presentation",
        table,
        ["presentation_id", "slide_index", "created_at"],
        unique=False,
    )


# Creation order: the manifests precede the active pointer.
_RECREATE = (
    ("authored_vnext_quality_decisions", _create_authored_vnext_quality_decisions),
    ("authored_vnext_export_jobs", _create_authored_vnext_export_jobs),
    ("authored_vnext_export_artifact_receipts", _create_authored_vnext_export_artifact_receipts),
    ("authored_vnext_render_receipts", _create_authored_vnext_render_receipts),
    ("authored_vnext_generation_operations", _create_authored_vnext_generation_operations),
    ("authored_deck_emission_manifests", _create_authored_deck_emission_manifests),
    ("authored_deck_emission_active", _create_authored_deck_emission_active),
    ("authored_slide_editor_events", _create_authored_slide_editor_events),
)


def _restore_postgresql_authority(table: str) -> None:
    """Re-apply what the earlier revisions granted to the controller role.

    a0d4f7c9e2b6 granted DML on every existing table, which covered all of
    these but the editor event ledger; b5a0d3e7c9f1 granted that one and gave
    it the owner row-level-security policy.
    """
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    role_exists = bind.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
        {"role": _CONTROLLER_ROLE},
    ).scalar()
    if role_exists:
        op.execute(
            sa.text(
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "{table}" '
                f"TO {_CONTROLLER_ROLE}"
            )
        )
    if table != _RLS_TABLE or not role_exists:
        return
    function_exists = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_proc WHERE proname = :name "
            "AND pronamespace = 'public'::regnamespace"
        ),
        {"name": _VISIBILITY_FUNCTION},
    ).scalar()
    if not function_exists:
        return
    predicate = f"public.{_VISIBILITY_FUNCTION}(owner_id, false)"
    op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
    op.execute(
        sa.text(
            f'CREATE POLICY {_REQUEST_POLICY} ON "{table}" FOR ALL '
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )
    )
    op.execute(
        sa.text(
            f'CREATE POLICY {_CONTROLLER_POLICY} ON "{table}" FOR ALL '
            f"TO {_CONTROLLER_ROLE} USING (true) WITH CHECK (true)"
        )
    )


def downgrade() -> None:
    for table, create in _RECREATE:
        if _has_table(table):
            continue
        create()
        _restore_postgresql_authority(table)
