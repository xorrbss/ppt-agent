"""Persist compact dashboard metadata and keep rolling writers coherent."""

from alembic import op
import sqlalchemy as sa


revision = "c9e3a5f7b1d4"
down_revision = "b8d2f4a6c9e1"
branch_labels = None
depends_on = None


_POSTGRES_METADATA = """
SELECT json_build_object('schema_version', 1, 'theme',
    CASE
        WHEN source IS NULL OR json_typeof(source) = 'null' THEN '{}'::json
        WHEN json_typeof(source) <> 'object' THEN '[]'::json
        ELSE COALESCE((
            SELECT json_object_agg(key,
                CASE WHEN key = 'authored_vnext'
                    THEN json_build_object('canvas', json_extract_path(value, 'canvas'))
                    ELSE value END)
            FROM json_each(source)
            WHERE key IN ('mode', 'documentCanvas')
                OR (key = 'authored_vnext' AND json_extract_path(value, 'canvas') IS NOT NULL)
        ), '{}'::json)
    END)
"""


def _sqlite_metadata(source: str) -> str:
    # json() keeps object/array values nested while json_quote() preserves
    # scalar strings. Explicit JSON null remains present for canvas validation.
    return f"""
    json_object('schema_version', 1, 'theme', json(CASE
        WHEN {source} IS NULL OR json_type({source}) = 'null' THEN '{{}}'
        WHEN json_type({source}) <> 'object' THEN '[]'
        ELSE COALESCE((
            SELECT json_group_object(key, json(CASE
                WHEN key = 'authored_vnext'
                    THEN json_object('canvas', json_extract(value, '$.canvas'))
                WHEN type = 'text' THEN json_quote(value)
                WHEN type = 'null' THEN 'null'
                ELSE value END))
            FROM json_each({source})
            WHERE key IN ('mode', 'documentCanvas')
                OR (key = 'authored_vnext' AND
                    CASE WHEN type = 'object' THEN json_type(value, '$.canvas') IS NOT NULL ELSE 0 END)
        ), '{{}}')
    END))
    """


def upgrade() -> None:
    bind = op.get_bind()
    if "presentations" not in sa.inspect(bind).get_table_names():
        return
    columns = {column["name"] for column in sa.inspect(bind).get_columns("presentations")}
    if "dashboard_metadata" not in columns:
        op.add_column("presentations", sa.Column("dashboard_metadata", sa.JSON(), nullable=True))
    if bind.dialect.name == "postgresql":
        op.execute(f"""
            CREATE OR REPLACE FUNCTION presentation_dashboard_metadata(source json)
            RETURNS json LANGUAGE sql IMMUTABLE AS $metadata$
            {_POSTGRES_METADATA}
            $metadata$
        """)
        op.execute("""
            CREATE OR REPLACE FUNCTION refresh_presentation_dashboard_metadata()
            RETURNS trigger LANGUAGE plpgsql AS $refresh$
            BEGIN
                IF TG_OP = 'INSERT' OR NEW.theme::text IS DISTINCT FROM OLD.theme::text THEN
                    NEW.dashboard_metadata := presentation_dashboard_metadata(NEW.theme);
                END IF;
                RETURN NEW;
            END;
            $refresh$
        """)
        op.execute("DROP TRIGGER IF EXISTS presentations_dashboard_metadata ON presentations")
        op.execute("""
            CREATE TRIGGER presentations_dashboard_metadata
            BEFORE INSERT OR UPDATE OF theme ON presentations
            FOR EACH ROW EXECUTE FUNCTION refresh_presentation_dashboard_metadata()
        """)
        op.execute("""
            UPDATE presentations
            SET dashboard_metadata = presentation_dashboard_metadata(theme)
            WHERE dashboard_metadata IS NULL OR json_typeof(dashboard_metadata) = 'null'
        """)
    elif bind.dialect.name == "sqlite":
        for name in ("presentations_dashboard_metadata_insert", "presentations_dashboard_metadata_update"):
            op.execute(f"DROP TRIGGER IF EXISTS {name}")
        metadata = _sqlite_metadata("NEW.theme")
        op.execute(f"""
            CREATE TRIGGER presentations_dashboard_metadata_insert
            AFTER INSERT ON presentations FOR EACH ROW BEGIN
                UPDATE presentations SET dashboard_metadata = {metadata} WHERE id = NEW.id;
            END
        """)
        op.execute(f"""
            CREATE TRIGGER presentations_dashboard_metadata_update
            AFTER UPDATE OF theme ON presentations FOR EACH ROW
            WHEN NEW.theme IS NOT OLD.theme BEGIN
                UPDATE presentations SET dashboard_metadata = {metadata} WHERE id = NEW.id;
            END
        """)
        op.execute(f"""
            UPDATE presentations SET dashboard_metadata = {_sqlite_metadata('presentations.theme')}
            WHERE dashboard_metadata IS NULL OR json_type(dashboard_metadata) = 'null'
        """)
    else:
        raise RuntimeError("Unsupported dashboard metadata database dialect")


def downgrade() -> None:
    bind = op.get_bind()
    if "presentations" not in sa.inspect(bind).get_table_names():
        return
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS presentations_dashboard_metadata ON presentations")
        op.execute("DROP FUNCTION IF EXISTS refresh_presentation_dashboard_metadata()")
        op.execute("DROP FUNCTION IF EXISTS presentation_dashboard_metadata(json)")
    elif bind.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS presentations_dashboard_metadata_insert")
        op.execute("DROP TRIGGER IF EXISTS presentations_dashboard_metadata_update")
    columns = {column["name"] for column in sa.inspect(bind).get_columns("presentations")}
    if "dashboard_metadata" in columns:
        with op.batch_alter_table("presentations") as batch:
            batch.drop_column("dashboard_metadata")
