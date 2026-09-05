"""Enable forced row-level security on every owner-scoped core table.

Revision ID: b5a0d3e7c9f1
Revises: a4f9c2e7b1d3

Contract (mirrors ff2a6c8e0b14 / a0d4f7c9e2b6 for the core tables):

* ``presenton.owner_id`` set -> rows whose ``owner_id`` matches, for every
  command (SELECT/INSERT/UPDATE/DELETE via one ``FOR ALL`` policy).
* Admin principal -> cross-owner authority, resolved the same way the account
  deletion outbox does it: the acting user row must be an active, non-deleting
  superuser.
* ``presenton.owner_id`` empty -> allowed only when ``presenton.request_scope``
  is not ``'on'``.  ``services.database`` publishes ``'on'`` for authenticated
  requests with auth enabled, so a request that lost its identity is denied at
  the database while the in-process dispatchers (outbox, export retention,
  generation) and the external workers, which never set it, keep their
  cross-owner reads.  The public control-plane tables keep their stricter
  tenant+owner predicate from ff2a6c8e0b14 and are deliberately excluded: a
  second permissive policy would widen them.
* ``presenton_public_controller`` -> ``USING (true)`` like the earlier policies.
* Superuser / BYPASSRLS logins are unaffected by definition.

PostgreSQL only; SQLite has no row-level security and is a no-op.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b5a0d3e7c9f1"
down_revision: str | None = "a4f9c2e7b1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONTROLLER_ROLE = "presenton_public_controller"
_REQUEST_POLICY = "presenton_owner_scope"
_CONTROLLER_POLICY = "presenton_owner_controller_authority"

# Frozen at authoring time from services.database._STRICT_OWNER_MODELS minus
# the tenant-scoped tables below.  tests/unit/test_owner_rls_migration.py
# asserts parity with the live model tuple, so a model added later fails the
# suite until a follow-up migration covers it instead of silently drifting.
_OWNER_TABLES = (
    "asset_gateway_idempotency_receipts",
    "async_presentation_generation_tasks",
    "async_tasks",
    "authored_canonical_revisions",
    "authored_slide_editor_events",
    "chat_history_messages",
    "imageasset",
    "presentation_export_attempts",
    "presentation_layout_codes",
    "presentations",
    "reference_snapshots",
    "slides",
    "template_create_infos",
    "templates",
    "webhook_subscriptions",
    "workspace_action_receipts",
    "workspace_artifacts",
    "workspace_assets",
    "workspace_conversations",
    "workspace_message_attachments",
    "workspace_messages",
    "workspace_operation_events",
    "workspace_operations",
    "workspace_outbox",
)
# Already ENABLE/FORCE'd by ff2a6c8e0b14 with the tenant+owner predicate.
_TENANT_SCOPED_TABLES = (
    "public_artifacts",
    "public_export_identities",
    "public_operation_admissions",
)

_OWNER_SETTING = "NULLIF(current_setting('presenton.owner_id', true), '')"
_REQUEST_SETTING = "COALESCE(current_setting('presenton.request_scope', true), '')"
_ACTOR_IS_ADMIN = (
    'EXISTS (SELECT 1 FROM "user" AS actor '
    f"WHERE actor.id = ({_OWNER_SETTING})::uuid "
    "AND actor.is_superuser = true AND actor.is_active = true "
    "AND actor.deletion_state = 'active')"
)
_PREDICATE = (
    f"({_OWNER_SETTING} IS NULL AND {_REQUEST_SETTING} <> 'on') "
    f"OR owner_id = ({_OWNER_SETTING})::uuid "
    f"OR ({_OWNER_SETTING} IS NOT NULL AND {_ACTOR_IS_ADMIN})"
)


def _ensure_controller_role() -> None:
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM pg_roles "
            f"WHERE rolname = '{_CONTROLLER_ROLE}') THEN "
            f"CREATE ROLE {_CONTROLLER_ROLE} NOLOGIN; "
            "END IF; END $$"
        )
    )


def _enable_policy(table: str) -> None:
    op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
    op.execute(
        sa.text(
            f'CREATE POLICY {_REQUEST_POLICY} ON "{table}" FOR ALL '
            f"USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})"
        )
    )
    op.execute(
        sa.text(
            f'CREATE POLICY {_CONTROLLER_POLICY} ON "{table}" FOR ALL '
            f"TO {_CONTROLLER_ROLE} USING (true) WITH CHECK (true)"
        )
    )
    op.execute(
        sa.text(
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "{table}" '
            f"TO {_CONTROLLER_ROLE}"
        )
    )


def _disable_policy(table: str) -> None:
    op.execute(
        sa.text(
            f'REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLE "{table}" '
            f"FROM {_CONTROLLER_ROLE}"
        )
    )
    op.execute(sa.text(f'DROP POLICY IF EXISTS {_CONTROLLER_POLICY} ON "{table}"'))
    op.execute(sa.text(f'DROP POLICY IF EXISTS {_REQUEST_POLICY} ON "{table}"'))
    op.execute(sa.text(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY'))
    op.execute(sa.text(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY'))


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    _ensure_controller_role()
    for table in _OWNER_TABLES:
        _enable_policy(table)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table in reversed(_OWNER_TABLES):
        _disable_policy(table)
