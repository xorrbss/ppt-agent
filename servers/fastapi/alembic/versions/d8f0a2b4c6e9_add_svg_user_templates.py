"""Owner-scoped immutable SVG template versions and canonical retention refs."""
from alembic import op
import sqlalchemy as sa

revision = "d8f0a2b4c6e9"
down_revision = "c3f5a7b9d1e2"
branch_labels = None
depends_on = None

TABLES = ("svg_templates", "svg_template_versions", "svg_template_frame_adaptations", "svg_template_asset_bindings")


def owner_columns():
    return (sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
            sa.Column("tenant_id", sa.String(128), nullable=False))


def upgrade():
    op.create_table("svg_templates", sa.Column("id", sa.Uuid(), primary_key=True), *owner_columns(),
        sa.Column("name", sa.String(160), nullable=False), sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("registration_state", sa.String(24), nullable=False), sa.Column("head_revision", sa.Integer(), nullable=False),
        sa.Column("draft_operation_id", sa.Uuid()), sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("head_revision >= 0", name="ck_svg_template_head_revision"),
        sa.CheckConstraint("kind IN ('style','framed')", name="ck_svg_template_kind"),
        sa.CheckConstraint("registration_state IN ('analyzing','review-required','ready','failed','archived')", name="ck_svg_template_state"))
    op.create_index("ix_svg_templates_owner_tenant", "svg_templates", ["owner_id", "tenant_id"])
    op.create_table("svg_template_versions", sa.Column("id", sa.Uuid(), primary_key=True), *owner_columns(),
        sa.Column("template_id", sa.Uuid(), sa.ForeignKey("svg_templates.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("digest", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False), sa.Column("source_snapshot_id", sa.Uuid(), sa.ForeignKey("reference_snapshots.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED"), nullable=False),
        sa.Column("package", sa.JSON(), nullable=False), sa.Column("style_projection", sa.JSON(), nullable=False),
        sa.Column("review_evidence", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("template_id", "revision", name="uq_svg_template_version"),
        sa.CheckConstraint("revision >= 1", name="ck_svg_template_revision"))
    op.create_table("svg_template_frame_adaptations", sa.Column("id", sa.Uuid(), primary_key=True), *owner_columns(),
        sa.Column("version_id", sa.Uuid(), sa.ForeignKey("svg_template_versions.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED"), nullable=False),
        sa.Column("frame_id", sa.String(128), nullable=False), sa.Column("canvas_digest", sa.String(80), nullable=False),
        sa.Column("digest", sa.String(80), nullable=False), sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("version_id", "frame_id", "canvas_digest", name="uq_svg_template_frame_canvas"))
    op.create_table("svg_template_asset_bindings", sa.Column("id", sa.Uuid(), primary_key=True), *owner_columns(),
        sa.Column("template_id", sa.Uuid(), sa.ForeignKey("svg_templates.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED"), nullable=False),
        sa.Column("version_id", sa.Uuid(), sa.ForeignKey("svg_template_versions.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED")),
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("workspace_assets.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED")),
        sa.Column("snapshot_id", sa.Uuid(), sa.ForeignKey("reference_snapshots.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED")),
        sa.Column("artifact_id", sa.Uuid(), sa.ForeignKey("public_artifacts.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED")),
        sa.Column("binding", sa.JSON(), nullable=False),
        sa.UniqueConstraint("template_id", "asset_id", "version_id", name="uq_svg_template_retained_asset"),
        sa.UniqueConstraint("template_id", "artifact_id", "version_id", name="uq_svg_template_retained_artifact"),
        sa.CheckConstraint("(asset_id IS NOT NULL AND snapshot_id IS NOT NULL AND artifact_id IS NULL) OR (asset_id IS NULL AND snapshot_id IS NULL AND artifact_id IS NOT NULL)", name="ck_svg_template_binding_target"))
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in TABLES:
            op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
            op.execute(sa.text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
            predicate = "owner_id::text = current_setting('presenton.owner_id', true) AND tenant_id = current_setting('presenton.tenant_id', true)"
            op.execute(sa.text(f'CREATE POLICY svg_template_principal ON "{table}" FOR ALL USING ({predicate}) WITH CHECK ({predicate})'))
            op.execute(sa.text(f'CREATE POLICY svg_template_controller ON "{table}" FOR ALL TO presenton_public_controller USING (true) WITH CHECK (true)'))
            op.execute(sa.text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "{table}" TO presenton_public_api, presenton_public_controller'))
        op.execute(sa.text("CREATE FUNCTION svg_template_immutable_version() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'svg_template_immutable_version'; END; $$"))
        for table in ("svg_template_versions", "svg_template_frame_adaptations"):
            op.execute(sa.text(f'CREATE TRIGGER svg_template_immutable BEFORE UPDATE ON "{table}" FOR EACH ROW EXECUTE FUNCTION svg_template_immutable_version()'))


def downgrade():
    for table in reversed(TABLES):
        op.drop_table(table)
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("DROP FUNCTION IF EXISTS svg_template_immutable_version()"))
