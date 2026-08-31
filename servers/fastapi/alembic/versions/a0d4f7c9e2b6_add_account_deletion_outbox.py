"""Add the durable account-deletion controller outbox.

Revision ID: a0d4f7c9e2b6
Revises: ff2a6c8e0b14
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a0d4f7c9e2b6"
down_revision: str | None = "ff2a6c8e0b14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "account_deletion_requests"
CONTROLLER_ROLE = "presenton_public_controller"
API_ROLE = "presenton_public_api"
CONTROLLER_LOGIN = "presenton_public_controller_login"
API_LOGIN = "presenton_public_api_login"
API_SELECT_POLICY = "presenton_account_deletion_api_select"
API_INSERT_POLICY = "presenton_account_deletion_api_insert"
CONTROLLER_POLICY = "presenton_account_deletion_controller_authority"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("claim_fence", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("claimed_by", sa.String(length=160), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "state IN ('pending', 'processing', 'retry')",
            name="ck_account_deletion_request_state",
        ),
        sa.CheckConstraint(
            "generation >= 1 AND claim_fence >= 0 AND attempt_count >= 0",
            name="ck_account_deletion_request_counters",
        ),
        sa.CheckConstraint(
            "(state = 'processing' AND claimed_by IS NOT NULL AND claim_expires_at IS NOT NULL) "
            "OR (state <> 'processing' AND claimed_by IS NULL AND claim_expires_at IS NULL)",
            name="ck_account_deletion_request_claim",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("owner_id"),
    )
    op.create_index(
        "ix_account_deletion_request_claim",
        TABLE,
        ["state", "available_at", "claim_expires_at", "requested_at"],
        unique=False,
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                "DO $$ BEGIN "
                "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'presenton_public_api') THEN "
                "CREATE ROLE presenton_public_api NOLOGIN; END IF; END $$"
            )
        )
        for group_role in (API_ROLE, CONTROLLER_ROLE):
            op.execute(
                sa.text(
                    f"ALTER ROLE {group_role} WITH NOLOGIN INHERIT NOSUPERUSER "
                    "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                )
            )
        op.execute(sa.text(f"GRANT USAGE ON SCHEMA public TO {CONTROLLER_ROLE}"))
        op.execute(sa.text(f"REVOKE CREATE ON SCHEMA public FROM {CONTROLLER_ROLE}"))
        op.execute(
            sa.text(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                f"IN SCHEMA public TO {CONTROLLER_ROLE}"
            )
        )
        op.execute(
            sa.text(
                "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES "
                f"IN SCHEMA public TO {CONTROLLER_ROLE}"
            )
        )
        op.execute(
            sa.text(
                f'REVOKE ALL PRIVILEGES ON TABLE "{TABLE}" FROM {API_ROLE}'
            )
        )
        op.execute(sa.text(f'GRANT SELECT, INSERT ON TABLE "{TABLE}" TO {API_ROLE}'))
        op.execute(
            sa.text(
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "{TABLE}" '
                f"TO {CONTROLLER_ROLE}"
            )
        )
        op.execute(sa.text(f'ALTER TABLE "{TABLE}" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'ALTER TABLE "{TABLE}" FORCE ROW LEVEL SECURITY'))
        # The authenticated API actor is deliberately different from the
        # account being deleted.  Allow only an active superuser request whose
        # target marker was already atomically changed to ``deleting`` in the
        # same transaction.  The controller retains the cross-owner authority
        # required to claim and finalize the durable outbox.
        api_predicate = (
            "NULLIF(current_setting('presenton.owner_id', true), '') IS NOT NULL "
            "AND EXISTS (SELECT 1 FROM \"user\" AS actor "
            "WHERE actor.id = "
            "(NULLIF(current_setting('presenton.owner_id', true), ''))::uuid "
            "AND actor.is_superuser = true AND actor.is_active = true "
            "AND actor.deletion_state = 'active') "
            "AND EXISTS (SELECT 1 FROM \"user\" AS target "
            f"WHERE target.id = \"{TABLE}\".owner_id "
            "AND target.is_superuser = false "
            "AND target.deletion_state = 'deleting' "
            "AND target.is_active = false "
            f"AND target.deletion_generation = \"{TABLE}\".generation)"
        )
        op.execute(
            sa.text(
                f'CREATE POLICY {API_SELECT_POLICY} ON "{TABLE}" '
                f"FOR SELECT TO {API_ROLE} USING ({api_predicate})"
            )
        )
        op.execute(
            sa.text(
                f'CREATE POLICY {API_INSERT_POLICY} ON "{TABLE}" '
                f"FOR INSERT TO {API_ROLE} WITH CHECK ({api_predicate})"
            )
        )
        op.execute(
            sa.text(
                f'CREATE POLICY {CONTROLLER_POLICY} ON "{TABLE}" '
                f"TO {CONTROLLER_ROLE} USING (true) WITH CHECK (true)"
            )
        )
        # Normalize known runtime logins last so an operator that temporarily
        # used one as the migration authority cannot lose CREATEROLE midway
        # through this transaction. Arbitrarily named external logins are
        # independently rejected by the startup authority probe when unsafe.
        for login_role in (API_LOGIN, CONTROLLER_LOGIN):
            op.execute(
                sa.text(
                    "DO $$ BEGIN "
                    "IF EXISTS (SELECT 1 FROM pg_roles "
                    f"WHERE rolname = '{login_role}') THEN "
                    f"ALTER ROLE {login_role} WITH LOGIN INHERIT NOSUPERUSER "
                    "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS; "
                    "END IF; END $$"
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(sa.text(f'REVOKE ALL ON TABLE "{TABLE}" FROM {API_ROLE}'))
        op.execute(sa.text(f'REVOKE ALL ON TABLE "{TABLE}" FROM {CONTROLLER_ROLE}'))
    op.drop_index("ix_account_deletion_request_claim", table_name=TABLE)
    op.drop_table(TABLE)
