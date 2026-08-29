"""replace persisted COM qualification bearer tokens with hash verifiers

Revision ID: e8d2b5f7a9c1
Revises: e7c1a4f6b8d2
"""

from __future__ import annotations

from hashlib import sha256

from alembic import op
import sqlalchemy as sa


revision: str = "e8d2b5f7a9c1"
down_revision: str | None = "e7c1a4f6b8d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    jobs = sa.table(
        "authored_com_qualification_jobs_v3",
        sa.column("id", sa.Uuid()),
        sa.column("lease_token", sa.String(length=128)),
    )
    rows = bind.execute(
        sa.select(jobs.c.id, jobs.c.lease_token).where(jobs.c.lease_token.is_not(None))
    )
    for job_id, token in rows:
        value = str(token)
        if value.startswith("sha256:") and len(value) == 71:
            continue
        verifier = "sha256:" + sha256(value.encode("utf-8")).hexdigest()
        bind.execute(
            jobs.update().where(jobs.c.id == job_id).values(lease_token=verifier)
        )


def downgrade() -> None:
    # Bearer tokens are intentionally unrecoverable. A downgraded worker must
    # wait for the bounded lease to expire and claim a new token.
    pass
