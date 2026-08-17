"""Exact schema guard for retired authored-vNext Alembic revisions."""

from __future__ import annotations

from collections.abc import Callable, Collection

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text


_RETIRED_REVISION = "d1f4a6b8c0e2"
RETIRED_AUTHORED_VNEXT_TABLES = frozenset(
    {
        "authored_vnext_master_revisions",
        "authored_vnext_slide_revisions",
        "authored_vnext_deck_revisions",
        "authored_vnext_entity_heads",
        "authored_vnext_candidates",
        "authored_vnext_quality_decisions",
        "authored_vnext_export_jobs",
        "authored_vnext_export_artifact_receipts",
        "authored_vnext_render_receipts",
        "authored_vnext_publication_pointers",
    }
)
RETIRED_PROVIDER_RUNTIME_TABLES = frozenset(
    {
        "provider_runtime_budgets",
        "provider_runtime_reservations",
        "provider_runtime_circuits",
    }
)


def retired_revision_recovery(
    version: str,
    tables: Collection[str],
    *,
    interrupted_revision: str,
    rebase_target: str,
) -> tuple[str, str] | None:
    """Return a safe re-stamp only for the exact retired physical schema."""

    retired_schema = (
        RETIRED_AUTHORED_VNEXT_TABLES.issubset(tables)
        and RETIRED_PROVIDER_RUNTIME_TABLES.issubset(tables)
        and "provider_usage_receipts" in tables
        and "provider_credential_references" not in tables
        and "authored_vnext_generation_operations" not in tables
    )
    if not retired_schema:
        return None
    if version == _RETIRED_REVISION:
        return rebase_target, "retired authored-vNext branch"
    # SQLite cannot roll back its Alembic version-table update around DDL. The
    # old recovery could therefore leave this schema at the share-token
    # revision when interrupted.
    if version == interrupted_revision:
        return rebase_target, "interrupted retired-branch recovery"
    return None


def repair_orphan_alembic_revision(
    config: Config,
    database_url: str,
    *,
    interrupted_revision: str,
    rebase_target: str,
    infer_revision: Callable[[object, set[str], str], str],
) -> None:
    """Repair either an exact retired schema or a conservatively inferred orphan."""

    script = ScriptDirectory.from_config(config)
    known = {revision.revision for revision in script.walk_revisions()}
    heads = script.get_heads()
    if len(heads) != 1:
        return
    head = heads[0]
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            if "alembic_version" not in tables:
                return
            version = connection.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            ).scalar_one_or_none()
            if not version:
                return
            recovery = retired_revision_recovery(
                version,
                tables,
                interrupted_revision=interrupted_revision,
                rebase_target=rebase_target,
            )
            if recovery is None and version in known:
                return
            if recovery is None:
                print(
                    f"Alembic revision {version!r} is missing from the codebase; "
                    "inferring applied migrations from schema and re-stamping.",
                    flush=True,
                )
                target = infer_revision(inspector, tables, head)
            else:
                target, reason = recovery
                print(
                    f"Alembic revision {version!r} belongs to {reason}; "
                    f"re-stamping at current ancestor {target!r}.",
                    flush=True,
                )
            repaired = connection.execute(
                text(
                    "UPDATE alembic_version SET version_num = :target "
                    "WHERE version_num = :current"
                ),
                {"target": target, "current": version},
            )
            if repaired.rowcount != 1:
                raise RuntimeError(
                    "Alembic orphan revision changed during schema repair"
                )
    finally:
        engine.dispose()
