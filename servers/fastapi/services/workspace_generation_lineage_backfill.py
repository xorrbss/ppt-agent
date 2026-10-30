"""Future owner-scoped maintenance for the generation-attempt projection.

This module is never invoked by startup or the Alembic schema revision. An
operator must first drain old writers, obtain an authorized complete owner
inventory, and run each scope with its own API-role principal context.
"""

from __future__ import annotations

import uuid

from sqlalchemy import String, cast, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.sql.workspace import WorkspaceOperation, get_workspace_owner_scope
from models.workspace_generation_lineage import previous_attempt_from_raw_payload
from utils.owner_context import get_current_owner_id, get_current_tenant_id


async def backfill_owned_generation_attempts(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID | None,
    owner_scope: str,
    tenant_id: str,
    expected_generation_rows: int,
    batch_size: int = 100,
) -> int:
    """Project one explicitly bound scope; caller owns transaction and commit.

    The original JSON text is read before any update. Only the derived column
    changes; payload bytes, hash, lifecycle, and version remain untouched.
    """
    if (
        session.bind.dialect.name != "postgresql"
        or owner_id is None
        or owner_id != get_current_owner_id()
        or owner_scope != get_workspace_owner_scope()
        or tenant_id != get_current_tenant_id()
        or expected_generation_rows < 0
        or batch_size < 1
        or batch_size > 1000
    ):
        raise ValueError("generation_lineage_backfill_scope_invalid")
    role = (await session.execute(text(
        "SELECT current_user, current_setting('presenton.owner_id', true), "
        "current_setting('presenton.tenant_id', true), "
        "current_setting('row_security'), current_setting('transaction_read_only'), "
        "r.rolsuper, r.rolbypassrls, "
        "pg_has_role(current_user, 'presenton_public_controller', 'MEMBER') "
        "FROM pg_roles AS r WHERE r.rolname = current_user"
    ))).one()
    if (
        role[0] != "presenton_public_api"
        or role[1] != (str(owner_id) if owner_id is not None else "")
        or role[2] != tenant_id
        or role[3] != "on"
        or role[4] != "off"
        or role[5] is not False
        or role[6] is not False
        or role[7] is not False
    ):
        raise ValueError("generation_lineage_backfill_role_invalid")
    owner_filter = (
        WorkspaceOperation.owner_id.is_(None)
        if owner_id is None else WorkspaceOperation.owner_id == owner_id
    )
    last_id: uuid.UUID | None = None
    observed = 0
    while True:
        query = select(
            WorkspaceOperation.id,
            cast(WorkspaceOperation.request_payload, String),
            WorkspaceOperation.previous_presentation_id,
        ).where(
            WorkspaceOperation.owner_scope == owner_scope,
            owner_filter,
            WorkspaceOperation.kind == "presentation.generate",
        )
        if last_id is not None:
            query = query.where(WorkspaceOperation.id > last_id)
        rows = (await session.execute(query.order_by(WorkspaceOperation.id).limit(batch_size))).all()
        if not rows:
            break
        for operation_id, raw_payload, old_parent in rows:
            parent = previous_attempt_from_raw_payload("presentation.generate", raw_payload)
            if old_parent != parent:
                changed = await session.execute(
                    update(WorkspaceOperation)
                    .where(
                        WorkspaceOperation.id == operation_id,
                        WorkspaceOperation.owner_scope == owner_scope,
                        owner_filter,
                        WorkspaceOperation.kind == "presentation.generate",
                        cast(WorkspaceOperation.request_payload, String) == raw_payload,
                    )
                    .values(previous_presentation_id=parent)
                )
                if changed.rowcount != 1:
                    raise RuntimeError("generation_lineage_backfill_payload_changed")
            observed += 1
            last_id = operation_id
    if observed != expected_generation_rows:
        raise RuntimeError("generation_lineage_backfill_count_mismatch")
    return observed
