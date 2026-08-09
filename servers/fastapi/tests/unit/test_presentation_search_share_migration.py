from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

import migrations


def _config(database_url: str) -> Config:
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "alembic")
    )
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_search_share_metrics_migration_preserves_legacy_links_and_rolls_back(
    tmp_path,
):
    database_url = f"sqlite:///{(tmp_path / 'share-metrics.db').as_posix()}"
    config = _config(database_url)
    command.upgrade(config, migrations.REVISION_CLEANUP_JOURNAL_REDRIVE)

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO presentations (
                        id, content, n_slides, language, title,
                        created_at, updated_at,
                        include_table_of_contents, include_title_slide,
                        web_search, version, lifecycle_status, revision,
                        share_token
                    ) VALUES (
                        :id, 'legacy deck', 1, 'ko', 'Legacy shared deck',
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                        0, 1, 0, 'v1-standard', 'published', 0,
                        :share_token
                    )
                    """
                ),
                {"id": "00000000-0000-0000-0000-000000000001", "share_token": "legacy-permanent-token"},
            )

        command.upgrade(config, "head")
        with engine.connect() as connection:
            inspector = inspect(connection)
            presentation_columns = {
                column["name"]
                for column in inspector.get_columns("presentations")
            }
            presentation_indexes = {
                index["name"]: tuple(index["column_names"])
                for index in inspector.get_indexes("presentations")
            }
            slide_indexes = {
                index["name"]: tuple(index["column_names"])
                for index in inspector.get_indexes("slides")
            }
            row = connection.execute(
                text(
                    "SELECT share_token, expires_at, view_count "
                    "FROM presentations WHERE id = :id"
                ),
                {"id": "00000000-0000-0000-0000-000000000001"},
            ).mappings().one()
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))

        assert revision == migrations.HEAD_REVISION
        assert {"expires_at", "view_count"}.issubset(presentation_columns)
        assert row == {
            "share_token": "legacy-permanent-token",
            "expires_at": None,
            "view_count": 0,
        }
        assert presentation_indexes[
            "ix_presentations_owner_lifecycle_updated_id"
        ] == ("owner_id", "lifecycle_status", "updated_at", "id")
        assert slide_indexes["ix_slides_presentation_index"] == (
            "presentation",
            "index",
        )

        command.downgrade(config, migrations.REVISION_CLEANUP_JOURNAL_REDRIVE)
        with engine.connect() as connection:
            inspector = inspect(connection)
            downgraded_columns = {
                column["name"]
                for column in inspector.get_columns("presentations")
            }
            preserved_token = connection.scalar(
                text("SELECT share_token FROM presentations WHERE id = :id"),
                {"id": "00000000-0000-0000-0000-000000000001"},
            )
        assert "expires_at" not in downgraded_columns
        assert "view_count" not in downgraded_columns
        assert preserved_token == "legacy-permanent-token"

        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT expires_at, view_count FROM presentations "
                    "WHERE id = :id"
                ),
                {"id": "00000000-0000-0000-0000-000000000001"},
            ).one() == (None, 0)
    finally:
        engine.dispose()
