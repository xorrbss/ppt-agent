"""Round trip of e7b2d4f6a8c1, the drop of the remaining unused authored tables."""

from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from migration_revision_contract import HEAD_REVISION

PREVIOUS_REVISION = "d1a5c7e9b3f2"
CURRENT_REVISION = "e7b2d4f6a8c1"

DROPPED_TABLES = frozenset(
    {
        "authored_vnext_quality_decisions",
        "authored_vnext_export_jobs",
        "authored_vnext_export_artifact_receipts",
        "authored_vnext_render_receipts",
        "authored_vnext_generation_operations",
        "authored_deck_emission_manifests",
        "authored_deck_emission_active",
        "authored_slide_editor_events",
    }
)
KEPT_TABLES = frozenset(
    {
        "authored_slide_checkpoints",
        "authored_deck_plan_checkpoints",
        "authored_vnext_master_revisions",
        "authored_vnext_slide_revisions",
        "authored_vnext_deck_revisions",
        "authored_vnext_entity_heads",
        "authored_vnext_candidates",
        "authored_vnext_publication_pointers",
    }
)


def _config(database_url: str) -> Config:
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parents[2] / "alembic"),
    )
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _shape(engine, tables) -> dict:
    inspector = inspect(engine)
    shape = {}
    for table in sorted(tables):
        shape[table] = {
            "columns": [
                (c["name"], str(c["type"]), c["nullable"], str(c["default"]))
                for c in inspector.get_columns(table)
            ],
            "pk": inspector.get_pk_constraint(table)["constrained_columns"],
            "fks": sorted(
                (
                    str(f.get("name")),
                    tuple(f["constrained_columns"]),
                    f["referred_table"],
                    tuple(f["referred_columns"]),
                    str(f.get("options")),
                )
                for f in inspector.get_foreign_keys(table)
            ),
            "uniques": sorted(
                (str(u["name"]), tuple(u["column_names"]))
                for u in inspector.get_unique_constraints(table)
            ),
            "checks": sorted(
                (str(c["name"]), str(c["sqltext"]))
                for c in inspector.get_check_constraints(table)
            ),
            "indexes": sorted(
                (str(i["name"]), tuple(i["column_names"]), bool(i["unique"]))
                for i in inspector.get_indexes(table)
            ),
        }
    return shape


def test_the_drop_is_the_head_revision():
    assert HEAD_REVISION == CURRENT_REVISION


def test_unused_authored_tables_are_dropped_and_recreated_exactly(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'drop-unused-authored.db'}"
    config = _config(database_url)
    command.upgrade(config, PREVIOUS_REVISION)

    engine = create_engine(database_url)
    try:
        before = _shape(engine, DROPPED_TABLES)
        tables = set(inspect(engine).get_table_names())
        assert DROPPED_TABLES <= tables
        assert KEPT_TABLES <= tables
        others_before = _shape(engine, tables - DROPPED_TABLES - {"alembic_version"})

        presentation_id = uuid.uuid4().hex
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO presentations (id, content, n_slides, language, "
                    "created_at, updated_at, lifecycle_status, generation_mode) "
                    "VALUES (:id, 'x', 1, 'ko', CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP, 'active', 'svg')"
                ),
                {"id": presentation_id},
            )
            connection.execute(
                text(
                    "INSERT INTO authored_slide_editor_events (id, presentation_id, "
                    "slide_index, event_type, event_key_hash, created_at) VALUES "
                    "(:id, :presentation, 0, 'slide_deleted', :key, CURRENT_TIMESTAMP)"
                ),
                {"id": uuid.uuid4().hex, "presentation": presentation_id, "key": "k" * 64},
            )

        command.upgrade(config, CURRENT_REVISION)
        after = set(inspect(engine).get_table_names())
        assert after.isdisjoint(DROPPED_TABLES)
        assert after == tables - DROPPED_TABLES
        assert _shape(engine, after - {"alembic_version"}) == others_before
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT COUNT(*) FROM presentations")
            ).scalar_one() == 1

        command.downgrade(config, PREVIOUS_REVISION)
        assert _shape(engine, DROPPED_TABLES) == before
        assert set(inspect(engine).get_table_names()) == tables

        command.upgrade(config, CURRENT_REVISION)
        assert set(inspect(engine).get_table_names()).isdisjoint(DROPPED_TABLES)
    finally:
        engine.dispose()


def test_the_downgrade_chain_through_the_hardening_revisions_still_runs(tmp_path):
    # a6b2c4d8e0f3 and b5a0d3e7c9f1 alter or drop dropped tables on their way
    # down, so the recreated shape must satisfy them.
    database_url = f"sqlite:///{tmp_path / 'downgrade-chain.db'}"
    config = _config(database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "f5a1c3e7b9d2")
    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "authored_vnext_export_jobs" in tables
        assert "authored_slide_editor_events" not in tables
    finally:
        engine.dispose()
