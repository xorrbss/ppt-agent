"""Run the public-v2 RC Alembic graph under a dedicated migration DSN."""

import os

from sqlalchemy.engine import make_url
from utils.db_utils import resolve_runtime_database_url


def _require_migration_database_url() -> str:
    if (os.environ.get("PUBLIC_DATABASE_PROCESS_ROLE") or "").strip().lower() != "migration":
        raise RuntimeError("public_migration_database_process_role_required")
    selected = (os.environ.get("PUBLIC_MIGRATION_DATABASE_URL") or "").strip()
    if not selected:
        raise RuntimeError("public_migration_database_url_required")
    if any((os.environ.get(name) or "").strip() for name in (
        "DATABASE_URL", "PUBLIC_API_DATABASE_URL", "PUBLIC_CONTROLLER_DATABASE_URL"
    )):
        raise RuntimeError("public_migration_opposite_role_database_url_forbidden")
    resolved = resolve_runtime_database_url()
    if resolved != selected:
        raise RuntimeError("public_migration_database_url_binding_invalid")
    try:
        parsed = make_url(resolved)
    except Exception:
        raise RuntimeError("public_migration_database_url_invalid") from None
    if parsed.get_backend_name() != "postgresql" or not parsed.username or not parsed.database:
        raise RuntimeError("public_migration_postgresql_owner_database_required")
    return resolved


def main() -> None:
    _require_migration_database_url()
    from migrations import _run_migrations

    _run_migrations()


if __name__ == "__main__":
    main()
