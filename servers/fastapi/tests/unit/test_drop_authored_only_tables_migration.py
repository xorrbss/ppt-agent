"""Round trip of d1a5c7e9b3f2, the authored-only table drop (authored removal P4)."""

from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

PREVIOUS_REVISION = "c5e8a1d3b7f9"
CURRENT_REVISION = "d1a5c7e9b3f2"

DROPPED_TABLES = frozenset(
    {
        "authored_canonical_revisions",
        "authored_capability_receipts_v2",
        "authored_editable_hybrid_mvp_completions_v1",
        "authored_exact_provenance",
        "authored_release_artifacts_v3",
        "authored_release_nonces_v3",
        "authored_ci_evidence_identities_v3",
        "authored_com_qualification_jobs_v3",
        "authored_evidence_graph_roots_v3",
        "authored_evidence_bundle_completions_v3",
        "authored_gate0_structural_traces_v3",
        "authored_gate0_structural_subjects_v3",
        "authored_gate0_structural_events_v3",
        "authored_gate0_structural_completions_v3",
        "authored_semantic_artifacts",
        "authored_emission_bundles",
        "authored_semantic_export_bindings",
        "slidespec_artifact_bundles",
        "slidespec_publication_attempts",
    }
)
KEPT_TABLES = frozenset(
    {
        "authored_slide_checkpoints",
        "authored_slide_editor_events",
        "authored_deck_emission_manifests",
        "authored_deck_emission_active",
        "authored_vnext_master_revisions",
        "authored_vnext_export_jobs",
        "authored_vnext_generation_operations",
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


def _shape(engine) -> dict:
    inspector = inspect(engine)
    shape = {}
    for table in sorted(DROPPED_TABLES):
        shape[table] = {
            "columns": [
                (c["name"], str(c["type"]), c["nullable"], str(c["default"]))
                for c in inspector.get_columns(table)
            ],
            "pk": inspector.get_pk_constraint(table)["constrained_columns"],
            "fks": sorted(
                (
                    tuple(f["constrained_columns"]),
                    f["referred_table"],
                    str(f.get("options")),
                )
                for f in inspector.get_foreign_keys(table)
            ),
            "uniques": sorted(
                (u["name"], tuple(u["column_names"]))
                for u in inspector.get_unique_constraints(table)
            ),
            "checks": sorted(
                (c["name"], str(c["sqltext"]))
                for c in inspector.get_check_constraints(table)
            ),
            "indexes": sorted(
                (i["name"], tuple(i["column_names"]), bool(i["unique"]))
                for i in inspector.get_indexes(table)
            ),
        }
    return shape


def test_authored_only_tables_are_dropped_and_recreated_exactly(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'drop-authored-only.db'}"
    config = _config(database_url)
    command.upgrade(config, PREVIOUS_REVISION)

    engine = create_engine(database_url)
    try:
        before = _shape(engine)
        tables = set(inspect(engine).get_table_names())
        assert DROPPED_TABLES <= tables
        assert KEPT_TABLES <= tables

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
                    "INSERT INTO authored_exact_provenance (presentation_id, "
                    "provenance) VALUES (:id, '{}')"
                ),
                {"id": presentation_id},
            )

        command.upgrade(config, CURRENT_REVISION)
        after = set(inspect(engine).get_table_names())
        assert after.isdisjoint(DROPPED_TABLES)
        assert KEPT_TABLES <= after
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT COUNT(*) FROM presentations")
            ).scalar_one() == 1

        command.downgrade(config, PREVIOUS_REVISION)
        assert _shape(engine) == before
        assert KEPT_TABLES <= set(inspect(engine).get_table_names())

        command.upgrade(config, CURRENT_REVISION)
        assert set(inspect(engine).get_table_names()).isdisjoint(DROPPED_TABLES)
    finally:
        engine.dispose()
