"""Harden public tenant RLS and hash stored API bearer tokens.

Revision ID: ff2a6c8e0b14
Revises: fb5e9c3a7d10, fe8b1c4d7a20
"""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
import re

from alembic import op
import sqlalchemy as sa


revision: str = "ff2a6c8e0b14"
down_revision: tuple[str, str] = ("fb5e9c3a7d10", "fe8b1c4d7a20")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CONTROLLER_ROLE = "presenton_public_controller"
_REQUEST_POLICY = "presenton_tenant_owner_scope"
_CONTROLLER_POLICY = "presenton_controller_authority"

_DIRECT_TABLES = (
    "public_operation_admissions",
    "public_artifacts",
    "public_export_identities",
)
_ADMISSION_CHILDREN = (
    "public_resource_leases",
    "public_provider_logical_calls",
)
_ARTIFACT_CHILDREN = (
    "public_artifact_publications",
    "public_slide_checkpoints",
)
_RLS_TABLES = (
    *_DIRECT_TABLES,
    *_ADMISSION_CHILDREN,
    *_ARTIFACT_CHILDREN,
    "public_quota_counters",
)

_TENANT_SETTING = "NULLIF(current_setting('presenton.tenant_id', true), '')"
_OWNER_SETTING = "NULLIF(current_setting('presenton.owner_id', true), '')"
_DIRECT_PREDICATE = (
    f"{_TENANT_SETTING} IS NOT NULL "
    f"AND {_OWNER_SETTING} IS NOT NULL "
    f"AND tenant_id = {_TENANT_SETTING} "
    f"AND owner_id = ({_OWNER_SETTING})::uuid"
)


def _hash_existing_tokens(bind: sa.Connection) -> None:
    inspector = sa.inspect(bind)
    if "access_tokens" not in inspector.get_table_names():
        return
    values = list(bind.execute(sa.text("SELECT token FROM access_tokens")).scalars())
    for value in values:
        if not isinstance(value, str) or _DIGEST.fullmatch(value):
            continue
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        bind.execute(
            sa.text("UPDATE access_tokens SET token = :digest WHERE token = :token"),
            {"digest": digest, "token": value},
        )


def _enable_policy(table: str, predicate: str) -> None:
    op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
    op.execute(
        sa.text(
            f'CREATE POLICY {_REQUEST_POLICY} ON "{table}" '
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )
    )
    op.execute(
        sa.text(
            f'CREATE POLICY {_CONTROLLER_POLICY} ON "{table}" '
            f'TO {_CONTROLLER_ROLE} USING (true) WITH CHECK (true)'
        )
    )
    op.execute(
        sa.text(
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "{table}" '
            f"TO {_CONTROLLER_ROLE}"
        )
    )


def _create_external_principal_bindings() -> None:
    op.create_table(
        "public_principal_bindings",
        sa.Column("binding_digest", sa.String(length=64), nullable=False),
        sa.Column("issuer_digest", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("subject_digest", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("binding_digest"),
        sa.UniqueConstraint(
            "issuer_digest",
            "tenant_id",
            "subject_digest",
            name="uq_public_principal_binding_identity",
        ),
    )
    op.create_index(
        "ix_public_principal_binding_owner",
        "public_principal_bindings",
        ["tenant_id", "user_id"],
        unique=False,
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


def upgrade() -> None:
    bind = op.get_bind()
    _create_external_principal_bindings()
    _hash_existing_tokens(bind)
    if bind.dialect.name != "postgresql":
        return

    # Membership is provisioned to a dedicated controller login outside this
    # migration.  The public API login must never be a member.  Startup probes
    # enforce both sides before either process accepts traffic or queue work.
    _ensure_controller_role()

    for table in _DIRECT_TABLES:
        _enable_policy(table, _DIRECT_PREDICATE)

    for table in _ADMISSION_CHILDREN:
        predicate = (
            "EXISTS (SELECT 1 FROM public_operation_admissions AS admission "
            f'WHERE admission.id = "{table}".admission_id '
            f"AND admission.tenant_id = {_TENANT_SETTING} "
            f"AND admission.owner_id = ({_OWNER_SETTING})::uuid)"
        )
        _enable_policy(table, predicate)

    for table in _ARTIFACT_CHILDREN:
        predicate = (
            "EXISTS (SELECT 1 FROM public_artifacts AS artifact "
            f'WHERE artifact.id = "{table}".artifact_id '
            f"AND artifact.tenant_id = {_TENANT_SETTING} "
            f"AND artifact.owner_id = ({_OWNER_SETTING})::uuid)"
        )
        _enable_policy(table, predicate)

    quota_predicate = (
        f"{_TENANT_SETTING} IS NOT NULL AND {_OWNER_SETTING} IS NOT NULL AND ("
        "scope_type = 'global' OR "
        f"(scope_type = 'tenant' AND scope_key = {_TENANT_SETTING}) OR "
        f"(scope_type = 'owner' AND scope_key = {_OWNER_SETTING}))"
    )
    _enable_policy("public_quota_counters", quota_predicate)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in reversed(_RLS_TABLES):
            op.execute(
                sa.text(
                    f'REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLE "{table}" '
                    f"FROM {_CONTROLLER_ROLE}"
                )
            )
            op.execute(
                sa.text(
                    f'DROP POLICY IF EXISTS {_CONTROLLER_POLICY} ON "{table}"'
                )
            )
            op.execute(
                sa.text(f'DROP POLICY IF EXISTS {_REQUEST_POLICY} ON "{table}"')
            )
            op.execute(sa.text(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY'))
            op.execute(sa.text(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY'))
    op.drop_index(
        "ix_public_principal_binding_owner",
        table_name="public_principal_bindings",
    )
    op.drop_table("public_principal_bindings")
    # Token hashes are intentionally one-way and cannot be downgraded to secrets.
