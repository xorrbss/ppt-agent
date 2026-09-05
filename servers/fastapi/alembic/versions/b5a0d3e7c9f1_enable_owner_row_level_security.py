"""Enable forced row-level security on every owner-scoped core table.

Revision ID: b5a0d3e7c9f1
Revises: a4f9c2e7b1d3

Contract (mirrors ff2a6c8e0b14 / a0d4f7c9e2b6 for the core tables):

* ``presenton.owner_id`` set -> rows whose ``owner_id`` matches, for every
  command (SELECT/INSERT/UPDATE/DELETE via one ``FOR ALL`` policy).
* Admin principal -> cross-owner authority, resolved the same way the account
  deletion outbox does it: the acting user row must be an active, non-deleting
  superuser.
* ``presenton.owner_id`` empty -> allowed while ``presenton.request_scope`` is
  not ``'on'``, which is how the in-process dispatchers (outbox, export
  retention, generation) and the external workers keep their cross-owner
  reads.  Inside an enforced request the same state **raises**
  ``owner_context_required`` (SQLSTATE 42501) rather than quietly matching no
  rows.  That is the same answer ``utils.owner_context.require_owner_context``
  gives in the ORM, and it is deliberate: an identity-less authenticated
  request is a bug, and an empty result set hides it.  A statement that opts
  out of ORM scoping (``skip_owner_scope``) or bypasses the ORM entirely still
  meets this refusal at the database.
* ``presenton_public_controller`` -> ``USING (true)`` like the earlier
  policies.
* Superuser / BYPASSRLS logins are unaffected by definition.

Predicate evaluation runs with the *querying* role's privileges, so the admin
test cannot read ``"user"`` inline: a deployment whose role lacks SELECT on
that table would fail every query with "permission denied for table user".
The whole decision therefore lives in one SECURITY DEFINER function owned by
the migration authority, and only EXECUTE on it is granted.  No role gains
read access to the user table.

Coverage: the 24 tables below plus ``template_v2``, which the ORM scopes
through catalog-aware criteria and which therefore also needs a policy.  Its
predicate additionally admits ``owner_id IS NULL`` rows, because the shared
catalog templates are ownerless by design.  The three public control-plane
tables keep their stricter tenant+owner predicate from ff2a6c8e0b14 and are
deliberately excluded: a second permissive policy would widen them.
``tests/unit/test_owner_rls_migration.py`` asserts that this set is exactly
``services.database_owner_scope.owner_scoped_table_names()``, so a model added
to the ORM tuple later fails the suite until a follow-up migration covers it.

Grants: a0d4f7c9e2b6 already granted the controller role SELECT/INSERT/UPDATE/
DELETE ``ON ALL TABLES IN SCHEMA public``.  Granting those again here and
revoking them on downgrade would leave the controller *worse* than before the
upgrade and break account deletion and the controller-role workers.  This
migration therefore grants only the tables created after that blanket grant --
``_CONTROLLER_GRANT_TABLES`` -- and revokes exactly the same set, so upgrade
and downgrade are symmetric.

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
_API_ROLE = "presenton_public_api"
_REQUEST_POLICY = "presenton_owner_scope"
_CONTROLLER_POLICY = "presenton_owner_controller_authority"
_VISIBILITY_FUNCTION = "presenton_owner_row_visible"

# Frozen at authoring time from services.database_owner_scope's owner-scoped
# model set minus the tenant-scoped tables below.
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
# Owner-scoped, but they also hold shared ownerless catalog rows that every
# request legitimately reads.
_CATALOG_SHARED_TABLES = ("template_v2",)
# Already ENABLE/FORCE'd by ff2a6c8e0b14 with the tenant+owner predicate.
_TENANT_SCOPED_TABLES = (
    "public_artifacts",
    "public_export_identities",
    "public_operation_admissions",
)
# Created after a0d4f7c9e2b6's ``ON ALL TABLES IN SCHEMA public`` grant, so the
# controller does not already hold DML on them.  Everything else in
# ``_OWNER_TABLES`` predates that grant and must not be revoked here.
_CONTROLLER_GRANT_TABLES = ("authored_slide_editor_events",)

_VISIBILITY_CALL = f"public.{_VISIBILITY_FUNCTION}(owner_id, {{shared}})"

_VISIBILITY_FUNCTION_BODY = f"""
CREATE OR REPLACE FUNCTION public.{_VISIBILITY_FUNCTION}(
    row_owner_id uuid, allow_shared boolean
) RETURNS boolean
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $presenton_owner_row_visible$
DECLARE
    actor_id text := nullif(current_setting('presenton.owner_id', true), '');
BEGIN
    IF actor_id IS NULL THEN
        IF coalesce(current_setting('presenton.request_scope', true), '')
            = 'on' THEN
            RAISE EXCEPTION 'owner_context_required'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN true;
    END IF;
    IF allow_shared AND row_owner_id IS NULL THEN
        RETURN true;
    END IF;
    IF row_owner_id = actor_id::uuid THEN
        RETURN true;
    END IF;
    RETURN EXISTS (
        SELECT 1 FROM public."user" AS actor
        WHERE actor.id = actor_id::uuid
          AND actor.is_superuser = true
          AND actor.is_active = true
          AND actor.deletion_state = 'active'
    );
END;
$presenton_owner_row_visible$
"""


def policy_predicate(table: str) -> str:
    """The USING/WITH CHECK expression this migration installs on ``table``."""
    shared = "true" if table in _CATALOG_SHARED_TABLES else "false"
    return _VISIBILITY_CALL.format(shared=shared)


def policy_tables() -> tuple[str, ...]:
    return _OWNER_TABLES + _CATALOG_SHARED_TABLES


def _ensure_role(role: str) -> None:
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "
            f"THEN CREATE ROLE {role} NOLOGIN; "
            "END IF; END $$"
        )
    )


def _create_visibility_function() -> None:
    op.execute(sa.text(_VISIBILITY_FUNCTION_BODY))
    for role in (_API_ROLE, _CONTROLLER_ROLE):
        op.execute(
            sa.text(
                f"GRANT EXECUTE ON FUNCTION public.{_VISIBILITY_FUNCTION}"
                f"(uuid, boolean) TO {role}"
            )
        )


def _drop_visibility_function() -> None:
    for role in (_CONTROLLER_ROLE, _API_ROLE):
        op.execute(
            sa.text(
                f"REVOKE EXECUTE ON FUNCTION public.{_VISIBILITY_FUNCTION}"
                f"(uuid, boolean) FROM {role}"
            )
        )
    op.execute(
        sa.text(
            f"DROP FUNCTION IF EXISTS public.{_VISIBILITY_FUNCTION}"
            "(uuid, boolean)"
        )
    )


def _enable_policy(table: str) -> None:
    predicate = policy_predicate(table)
    op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
    op.execute(
        sa.text(
            f'CREATE POLICY {_REQUEST_POLICY} ON "{table}" FOR ALL '
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )
    )
    op.execute(
        sa.text(
            f'CREATE POLICY {_CONTROLLER_POLICY} ON "{table}" FOR ALL '
            f"TO {_CONTROLLER_ROLE} USING (true) WITH CHECK (true)"
        )
    )


def _disable_policy(table: str) -> None:
    op.execute(sa.text(f'DROP POLICY IF EXISTS {_CONTROLLER_POLICY} ON "{table}"'))
    op.execute(sa.text(f'DROP POLICY IF EXISTS {_REQUEST_POLICY} ON "{table}"'))
    op.execute(sa.text(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY'))
    op.execute(sa.text(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY'))


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for role in (_API_ROLE, _CONTROLLER_ROLE):
        _ensure_role(role)
    _create_visibility_function()
    for table in policy_tables():
        _enable_policy(table)
    for table in _CONTROLLER_GRANT_TABLES:
        op.execute(
            sa.text(
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "{table}" '
                f"TO {_CONTROLLER_ROLE}"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table in reversed(_CONTROLLER_GRANT_TABLES):
        op.execute(
            sa.text(
                f'REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLE "{table}" '
                f"FROM {_CONTROLLER_ROLE}"
            )
        )
    for table in reversed(policy_tables()):
        _disable_policy(table)
    _drop_visibility_function()
