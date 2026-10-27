"""Keep independent generation writers outside the caller's unit of work."""

from contextlib import asynccontextmanager
from copy import deepcopy

from sqlalchemy import event, inspect
from sqlalchemy.ext.asyncio import AsyncSession


def mirror_detached_checkpoint(owner, **values):
    """Mirror committed results only into a snapshot, never another session."""
    state = inspect(owner, raiseerr=False)
    if state is not None and state.session is not None:
        return
    for name, value in values.items():
        setattr(owner, name, deepcopy(value))


@asynccontextmanager
async def independent_generation(session, job):
    """Release preparation reads and prohibit outer transactions until publish.

    Retain the caller's identity map (including its status object), but give
    header/page writers a separate job snapshot. Never commit or discard an
    unacknowledged pending write as a side effect of starting page work.
    """
    if not isinstance(session, AsyncSession):
        # Non-database callers and page-loop harnesses have no transaction.
        yield job
        return
    if session.new or session.dirty or session.deleted:
        await session.rollback()
        raise RuntimeError("svg_generation_outer_session_has_pending_writes")
    state = inspect(job, raiseerr=False)
    if state is None:
        snapshot = deepcopy(job)
    else:
        # Copy values into a fresh mapped instance, never SQLModel's ORM state:
        # copying InstanceState also copies its weak reference to the old owner.
        snapshot = type(job)(**{column.key: deepcopy(getattr(job, column.key))
                                for column in state.mapper.column_attrs})
        if hasattr(job, "_workspace_fencing_token"):
            snapshot._workspace_fencing_token = job._workspace_fencing_token
    await session.rollback()
    previous = session.sync_session.autobegin
    session.sync_session.autobegin = False

    def reject_transaction(*_args):
        raise RuntimeError("svg_generation_outer_transaction_forbidden")

    event.listen(session.sync_session, "after_transaction_create", reject_transaction)
    try:
        yield snapshot
    finally:
        event.remove(session.sync_session, "after_transaction_create", reject_transaction)
        await session.rollback()
        session.sync_session.autobegin = previous
