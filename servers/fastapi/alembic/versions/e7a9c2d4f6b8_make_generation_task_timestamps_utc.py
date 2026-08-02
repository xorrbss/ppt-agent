"""Store async presentation task timestamps as timezone-aware UTC.

Revision ID: e7a9c2d4f6b8
Revises: d6f8a1c3e5b7
"""

import os
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "e7a9c2d4f6b8"
down_revision: Union[str, None] = "d6f8a1c3e5b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "async_presentation_generation_tasks"
TIMESTAMP_COLUMNS = ("created_at", "updated_at")
LEGACY_TIMEZONE_ENV = "PPT_AGENT_ASYNC_TASK_LEGACY_TIMEZONE"
LEGACY_TIMEZONE_X_ARG = "async_task_legacy_timezone"


def _table_exists() -> bool:
    return TABLE in sa.inspect(op.get_bind()).get_table_names()


def _has_rows() -> bool:
    return bool(
        op.get_bind()
        .execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {TABLE})"))
        .scalar_one()
    )


def _lock_table() -> None:
    """Freeze legacy writers before inspecting rows or timestamp types."""

    op.execute(
        sa.text(
            f"LOCK TABLE {TABLE} IN ACCESS EXCLUSIVE MODE"
        )
    )


def _timestamp_state() -> str:
    """Return the uniform PostgreSQL timestamp state, rejecting partial DDL."""

    columns = {
        column["name"]: column
        for column in sa.inspect(op.get_bind()).get_columns(TABLE)
        if column["name"] in TIMESTAMP_COLUMNS
    }
    if set(columns) != set(TIMESTAMP_COLUMNS):
        raise RuntimeError(
            f"{TABLE} is missing required timestamp columns"
        )

    awareness: list[bool] = []
    for name in TIMESTAMP_COLUMNS:
        column_type = columns[name].get("type")
        if not isinstance(column_type, sa.DateTime):
            raise RuntimeError(
                f"{TABLE}.{name} must be a PostgreSQL timestamp column"
            )
        awareness.append(bool(column_type.timezone))

    if all(awareness):
        return "aware"
    if not any(awareness):
        return "naive"
    raise RuntimeError(
        f"{TABLE} timestamp timezone migration is only partially applied"
    )


def _configured_legacy_timezone() -> str | None:
    """Return one unambiguous operator-supplied legacy wall-clock timezone."""

    configured: list[tuple[str, str]] = []
    x_value = context.get_x_argument(as_dictionary=True).get(
        LEGACY_TIMEZONE_X_ARG
    )
    main_value = context.config.get_main_option(LEGACY_TIMEZONE_X_ARG)
    env_value = os.getenv(LEGACY_TIMEZONE_ENV)
    for source, value in (
        (f"-x {LEGACY_TIMEZONE_X_ARG}", x_value),
        (f"Alembic option {LEGACY_TIMEZONE_X_ARG}", main_value),
        (LEGACY_TIMEZONE_ENV, env_value),
    ):
        if value and value.strip():
            configured.append((source, value.strip()))

    distinct_values = {value for _, value in configured}
    if len(distinct_values) > 1:
        sources = ", ".join(f"{source}={value!r}" for source, value in configured)
        raise RuntimeError(
            "Conflicting async task legacy timezone settings: " + sources
        )
    return next(iter(distinct_values), None)


def _legacy_timezone_literal(*, direction: str) -> str:
    timezone_name = _configured_legacy_timezone()
    if timezone_name is None:
        if _has_rows():
            raise RuntimeError(
                f"{LEGACY_TIMEZONE_ENV} is required to {direction} populated "
                f"{TABLE}. Set it to the timezone used by the legacy app "
                "process (for example UTC or Asia/Seoul), or pass "
                f"-x {LEGACY_TIMEZONE_X_ARG}=<timezone>."
            )
        timezone_name = "UTC"

    bind = op.get_bind()
    is_valid = bind.execute(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM pg_timezone_names WHERE name = :timezone_name"
            ")"
        ),
        {"timezone_name": timezone_name},
    ).scalar_one()
    if not is_valid:
        raise RuntimeError(
            f"{LEGACY_TIMEZONE_ENV} must name a PostgreSQL timezone; "
            f"received {timezone_name!r}"
        )
    return str(
        sa.literal(timezone_name).compile(
            dialect=bind.dialect,
            compile_kwargs={"literal_binds": True},
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists() or bind.dialect.name == "sqlite":
        return
    if bind.dialect.name != "postgresql":
        raise RuntimeError(
            "Async presentation task timezone migration supports only SQLite and PostgreSQL"
        )

    _lock_table()
    timestamp_state = _timestamp_state()
    if timestamp_state == "aware":
        return
    legacy_timezone = _legacy_timezone_literal(direction="upgrade")
    for column in TIMESTAMP_COLUMNS:
        op.alter_column(
            TABLE,
            column,
            existing_type=sa.DateTime(timezone=False),
            type_=sa.DateTime(timezone=True),
            existing_nullable=False,
            postgresql_using=f"{column} AT TIME ZONE {legacy_timezone}",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists() or bind.dialect.name == "sqlite":
        return
    if bind.dialect.name != "postgresql":
        raise RuntimeError(
            "Async presentation task timezone migration supports only SQLite and PostgreSQL"
        )

    _lock_table()
    timestamp_state = _timestamp_state()
    if timestamp_state == "naive":
        return
    legacy_timezone = _legacy_timezone_literal(direction="downgrade")
    for column in TIMESTAMP_COLUMNS:
        op.alter_column(
            TABLE,
            column,
            existing_type=sa.DateTime(timezone=True),
            type_=sa.DateTime(timezone=False),
            existing_nullable=False,
            postgresql_using=f"{column} AT TIME ZONE {legacy_timezone}",
        )
