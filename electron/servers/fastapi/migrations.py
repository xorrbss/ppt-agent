import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

from utils.db_utils import get_database_url_and_connect_args
from utils.get_env import get_migrate_database_on_startup_env


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
    command.upgrade(config, "head")
