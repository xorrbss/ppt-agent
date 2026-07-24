"""harden Template V2 presentation deletion

Revision ID: 0a1b2c3d4e5f
Revises: f9a0b1c2d3e4
Create Date: 2026-07-24
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0a1b2c3d4e5f"
down_revision: str | None = "f9a0b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TEMPLATE_TABLE = "template_v2"
TEMPLATE_FK = "fk_template_v2_presentation_id_presentations"
LOCAL_STATE_TABLE = "template_v2_local_state"
LOCAL_STATE_PRESENTATION_FK = (
    "fk_template_v2_local_state_presentation_id_presentations"
)


def _delete_action(table_name: str, foreign_key_name: str) -> str | None:
    inspector = sa.inspect(op.get_bind())
    foreign_key = next(
        (
            candidate
            for candidate in inspector.get_foreign_keys(table_name)
            if candidate.get("name") == foreign_key_name
        ),
        None,
    )
    if foreign_key is None:
        return None
    return str(
        (foreign_key.get("options") or {}).get("ondelete", "")
    ).upper()


def _require_sidecar_ownership_ready() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    required = {
        "presentations",
        "template_v2",
        "template_v2_local_state",
    }
    missing = required - tables
    if missing:
        raise RuntimeError(
            "Template V2 delete hardening requires tables: "
            + ", ".join(sorted(missing))
        )
    if _delete_action(TEMPLATE_TABLE, TEMPLATE_FK) != "CASCADE":
        raise RuntimeError(
            "Template V2 delete hardening requires the f9 CASCADE baseline"
        )
    if (
        _delete_action(LOCAL_STATE_TABLE, LOCAL_STATE_PRESENTATION_FK)
        != "CASCADE"
    ):
        raise RuntimeError(
            "Template V2 delete hardening requires the f9 sidecar "
            "CASCADE baseline"
        )

    divergent = connection.execute(
        sa.text(
            """
            SELECT 1
            FROM template_v2 AS template
            LEFT JOIN template_v2_local_state AS local_state
              ON local_state.template_id = template.id
            WHERE local_state.template_id IS NULL
               OR local_state.presentation_id <> template.presentation_id
            UNION ALL
            SELECT 1
            FROM template_v2_local_state AS local_state
            LEFT JOIN template_v2 AS template
              ON template.id = local_state.template_id
            LEFT JOIN presentations AS presentation
              ON presentation.id = local_state.presentation_id
            WHERE template.id IS NULL
               OR presentation.id IS NULL
               OR template.presentation_id <> local_state.presentation_id
            LIMIT 1
            """
        )
    ).first()
    if divergent is not None:
        raise RuntimeError(
            "Template V2 delete hardening requires complete, matching "
            "local-state ownership"
        )


def _replace_delete_action(
    *,
    table_name: str,
    foreign_key_name: str,
    expected: str,
    replacement: str,
) -> None:
    if _delete_action(table_name, foreign_key_name) != expected:
        raise RuntimeError(
            f"{table_name}.{foreign_key_name} must use {expected} before "
            f"changing it to {replacement}"
        )

    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        with op.batch_alter_table(
            table_name,
            recreate="always",
        ) as batch_op:
            batch_op.drop_constraint(foreign_key_name, type_="foreignkey")
            batch_op.create_foreign_key(
                foreign_key_name,
                "presentations",
                ["presentation_id"],
                ["id"],
                ondelete=replacement,
            )
    elif connection.dialect.name == "mysql":
        # MySQL auto-commits DDL. Keep each FK replacement in one ALTER
        # statement so a failure cannot leave that table without the guard.
        op.execute(
            sa.text(
                f"ALTER TABLE {table_name} "
                f"DROP FOREIGN KEY {foreign_key_name}, "
                f"ADD CONSTRAINT {foreign_key_name} "
                "FOREIGN KEY (presentation_id) "
                "REFERENCES presentations (id) "
                f"ON DELETE {replacement}"
            )
        )
    else:
        op.drop_constraint(
            foreign_key_name,
            table_name,
            type_="foreignkey",
        )
        op.create_foreign_key(
            foreign_key_name,
            table_name,
            "presentations",
            ["presentation_id"],
            ["id"],
            ondelete=replacement,
        )

    if _delete_action(table_name, foreign_key_name) != replacement:
        raise RuntimeError(
            f"{table_name} presentation FK delete action verification failed"
        )


def upgrade() -> None:
    _require_sidecar_ownership_ready()
    _replace_delete_action(
        table_name=LOCAL_STATE_TABLE,
        foreign_key_name=LOCAL_STATE_PRESENTATION_FK,
        expected="CASCADE",
        replacement="RESTRICT",
    )
    _replace_delete_action(
        table_name=TEMPLATE_TABLE,
        foreign_key_name=TEMPLATE_FK,
        expected="CASCADE",
        replacement="RESTRICT",
    )


def downgrade() -> None:
    _replace_delete_action(
        table_name=TEMPLATE_TABLE,
        foreign_key_name=TEMPLATE_FK,
        expected="RESTRICT",
        replacement="CASCADE",
    )
    _replace_delete_action(
        table_name=LOCAL_STATE_TABLE,
        foreign_key_name=LOCAL_STATE_PRESENTATION_FK,
        expected="RESTRICT",
        replacement="CASCADE",
    )
