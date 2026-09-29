import os
import tempfile
from utils.env_parse import is_truthy
from utils.get_env import env_bool, env_int, get_app_data_directory_env
from urllib.parse import urlsplit, urlunsplit, parse_qsl
import ssl
from collections.abc import Mapping


_LOCAL_ENVIRONMENTS = {"local", "development", "dev", "test"}
_PUBLIC_DATABASE_ROLES = {"api", "controller", "migration"}


_truthy = is_truthy


def resolve_runtime_database_url(
    source: Mapping[str, str] | None = None,
) -> str | None:
    """Select one least-privilege DSN without copying the other role's secret.

    Local/self-hosted compatibility continues to use ``DATABASE_URL``.  A
    public non-local process must declare its environment and role, receive
    exactly one role-specific DSN, and leave schema migration to a separate
    deployment authority.  Error messages deliberately never contain URLs.
    """

    environment = os.environ if source is None else source
    tier_raw = (environment.get("ENVIRONMENT") or "").strip().lower()
    process_role = (
        environment.get("PUBLIC_DATABASE_PROCESS_ROLE") or ""
    ).strip().lower()
    public_configured = bool(
        process_role
        or (environment.get("PUBLIC_API_DATABASE_URL") or "").strip()
        or (environment.get("PUBLIC_CONTROLLER_DATABASE_URL") or "").strip()
        or (environment.get("PPT_PUBLIC_OIDC_ISSUER") or "").strip()
        or (environment.get("PUBLIC_V2_INTEGRATION_PROFILE") or "").strip()
        == "postgresql-strict"
    )
    if public_configured and not tier_raw:
        raise RuntimeError("public_environment_required")
    if not public_configured or tier_raw in _LOCAL_ENVIRONMENTS:
        return (environment.get("DATABASE_URL") or "").strip() or None
    if process_role not in _PUBLIC_DATABASE_ROLES:
        raise RuntimeError("public_database_process_role_required")
    if (environment.get("DATABASE_URL") or "").strip():
        raise RuntimeError("public_generic_database_url_forbidden")
    if _truthy(environment.get("MIGRATE_DATABASE_ON_STARTUP")):
        raise RuntimeError("public_runtime_database_migrations_forbidden")

    selected_name = {
        "api": "PUBLIC_API_DATABASE_URL",
        "controller": "PUBLIC_CONTROLLER_DATABASE_URL",
        "migration": "PUBLIC_MIGRATION_DATABASE_URL",
    }[process_role]
    opposite_names = {
        "api": ("PUBLIC_CONTROLLER_DATABASE_URL", "PUBLIC_MIGRATION_DATABASE_URL"),
        "controller": ("PUBLIC_API_DATABASE_URL", "PUBLIC_MIGRATION_DATABASE_URL"),
        "migration": ("PUBLIC_API_DATABASE_URL", "PUBLIC_CONTROLLER_DATABASE_URL"),
    }[process_role]
    selected = (environment.get(selected_name) or "").strip()
    if not selected:
        raise RuntimeError("public_role_database_url_required")
    if any((environment.get(name) or "").strip() for name in opposite_names):
        raise RuntimeError("public_opposite_role_database_url_forbidden")
    return selected


def _ensure_sqlite_parent_dir(database_url: str) -> None:
    if not database_url.startswith("sqlite://"):
        return

    split_result = urlsplit(database_url)
    db_path = split_result.path
    if not db_path:
        return

    # sqlite URLs on Windows can start with /C:/..., normalize that for os.path.
    if os.name == "nt" and len(db_path) >= 3 and db_path[0] == "/" and db_path[2] == ":":
        db_path = db_path[1:]

    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
def _int_env(name: str, default: int) -> int:
    """Integer pool knob, falling back to *default* on unset or malformed text.

    Deliberately unbounded: SQLAlchemy gives ``pool_size=0`` and
    ``max_overflow=-1`` their own meanings (no pooling / unlimited overflow),
    so a positivity check here would silently discard a valid operator choice.
    """
    return env_int(name, default)


def get_pool_kwargs() -> dict:
    """Build SQLAlchemy engine pool keyword arguments from environment variables.

    Supported variables (all optional):
        DB_POOL_SIZE          – max persistent connections (default 5)
        DB_MAX_OVERFLOW       – extra connections above pool_size (default 10)
        DB_POOL_TIMEOUT       – seconds to wait for a connection (default 30)
        DB_POOL_RECYCLE       – seconds before a connection is recycled (default 1800)
        DB_POOL_PRE_PING      – enable connection liveness check (default true)

    For SQLite the pool settings are not applicable and an empty dict is
    returned, since SQLite uses ``StaticPool`` / ``NullPool`` by default.
    """
    return {
        "pool_size": _int_env("DB_POOL_SIZE", 5),
        "max_overflow": _int_env("DB_MAX_OVERFLOW", 10),
        "pool_timeout": _int_env("DB_POOL_TIMEOUT", 30),
        "pool_recycle": _int_env("DB_POOL_RECYCLE", 1800),
        "pool_pre_ping": env_bool("DB_POOL_PRE_PING", True),
    }


def get_database_url_and_connect_args() -> tuple[str, dict]:
    database_url = resolve_runtime_database_url() or "sqlite:///" + os.path.join(
        get_app_data_directory_env()
        or os.path.join(tempfile.gettempdir(), "presenton"),
        "fastapi.db",
    )

    _ensure_sqlite_parent_dir(database_url)

    if database_url.startswith("sqlite://"):
        database_url = database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif database_url.startswith("mysql://"):
        database_url = database_url.replace("mysql://", "mysql+aiomysql://", 1)
    else:
        database_url = database_url

    connect_args = {}
    if "sqlite" in database_url:
        connect_args["check_same_thread"] = False
        # External workspace workers and the API can briefly contend for the
        # local database.  Let SQLite wait for the current writer instead of
        # surfacing transient ``database is locked`` responses immediately.
        connect_args["timeout"] = 30.0

    try:
        split_result = urlsplit(database_url)
        if split_result.query:
            query_params = parse_qsl(split_result.query, keep_blank_values=True)
            driver_scheme = split_result.scheme
            for k, v in query_params:
                key_lower = k.lower()
                if key_lower == "sslmode" and "postgresql+asyncpg" in driver_scheme:
                    if v.lower() != "disable" and "sqlite" not in database_url:
                        connect_args["ssl"] = ssl.create_default_context()

            database_url = urlunsplit(
                (
                    split_result.scheme,
                    split_result.netloc,
                    split_result.path,
                    "",
                    split_result.fragment,
                )
            )
    except Exception:
        pass

    return database_url, connect_args


def to_sync_sqlalchemy_url(database_url: str) -> str:
    """Strip async driver prefixes for Alembic and other sync SQLAlchemy engines.

    PostgreSQL URLs use ``postgresql+psycopg://`` (psycopg3) so migrations do not
    depend on psycopg2, which is not installed when using asyncpg at runtime.

    MySQL URLs use ``mysql+pymysql://`` so Alembic does not require ``mysqlclient``
    (the default for plain ``mysql://``); PyMySQL is already pulled in by aiomysql.
    """
    if database_url.startswith("sqlite+aiosqlite:///"):
        return "sqlite:///" + database_url[len("sqlite+aiosqlite:///") :]
    if database_url.startswith("postgresql+asyncpg://"):
        rest = database_url[len("postgresql+asyncpg://") :]
        return f"postgresql+psycopg://{rest}"
    if database_url.startswith("mysql+aiomysql://"):
        rest = database_url[len("mysql+aiomysql://") :]
        return f"mysql+pymysql://{rest}"
    if database_url.startswith("postgresql://"):
        rest = database_url[len("postgresql://") :]
        return f"postgresql+psycopg://{rest}"
    if database_url.startswith("mysql://"):
        rest = database_url[len("mysql://") :]
        return f"mysql+pymysql://{rest}"
    return database_url
