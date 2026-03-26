import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from utils.db_utils import get_database_url_and_connect_args
from utils.get_env import get_migrate_database_on_startup_env

LEGACY_BASELINE_REVISION = "00b3c27a13bc"


def _to_sync_database_url(database_url: str) -> str:
    # Preserve slash counts for sqlite URLs so Windows paths stay valid.
    if database_url.startswith("sqlite+aiosqlite:///"):
        return "sqlite:///" + database_url[len("sqlite+aiosqlite:///") :]
    if database_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + database_url[len("postgresql+asyncpg://") :]
    if database_url.startswith("mysql+aiomysql://"):
        return "mysql://" + database_url[len("mysql+aiomysql://") :]
    return database_url


async def migrate_database_on_startup() -> None:
    if get_migrate_database_on_startup_env() not in ["true", "True"]:
        return

    try:
        await asyncio.to_thread(_run_migrations)
        print("Migrations run successfully", flush=True)
    except Exception as exc:
        print(f"Error running migrations: {exc}", flush=True)
        raise


def run_migrations_sync() -> None:
    """Apply Alembic migrations to head (for CLI/scripts; no env gate)."""
    _run_migrations()
        raise


def run_migrations_sync() -> None:
    """Apply Alembic migrations to head (for CLI/scripts; no env gate)."""
    _run_migrations()


def _run_migrations() -> None:
    # migrations.py lives at servers/fastapi/migrations.py
    # so parents[0] = servers/fastapi/, where alembic/ lives alongside it.
    base_dir = Path(__file__).resolve().parents[0]
    config = Config()
    config.set_main_option("script_location", str(base_dir / "alembic"))

    database_url, _ = get_database_url_and_connect_args()

    # Alembic uses synchronous engines; strip async driver prefixes.
    database_url = _to_sync_database_url(database_url)

    config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(config, "head")
    except Exception as exc:
        # Recovery path for historical DBs that were created via create_all()
        # without an alembic_version table.
        if _is_unversioned_populated_database(database_url):
            script = ScriptDirectory.from_config(config)
            known_revisions = {rev.revision for rev in script.walk_revisions()}
            baseline_revision = (
                LEGACY_BASELINE_REVISION
                if LEGACY_BASELINE_REVISION in known_revisions
                else script.get_base()
            )
            print(
                "Detected existing unversioned database schema. "
                f"Stamping revision to {baseline_revision} before upgrading.",
                flush=True,
            )
            command.stamp(config, baseline_revision)
            command.upgrade(config, "head")
            return
        raise


def _is_unversioned_populated_database(database_url: str) -> bool:
    known_app_tables = {
        "presentations",
        "slides",
        "templates",
        "keyvaluesqlmodel",
        "imageasset",
        "presentation_layout_codes",
        "async_presentation_generation_tasks",
        "webhook_subscriptions",
    }
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            table_names = set(inspector.get_table_names())
            has_alembic_version_table = "alembic_version" in table_names
            has_applied_revision = False
            if has_alembic_version_table:
                revision_count = connection.execute(
                    text("SELECT COUNT(*) FROM alembic_version")
                ).scalar_one()
                has_applied_revision = revision_count > 0
            has_known_app_tables = len(table_names.intersection(known_app_tables)) > 0
            return has_known_app_tables and not has_applied_revision
    finally:
        engine.dispose()
