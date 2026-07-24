from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_revision import TemplateV2Revision


MAX_REVISIONS_PER_TEMPLATE = 50
SNAPSHOT_FIELDS = (
    "name",
    "description",
    "merged_components",
    "layouts",
    "assets",
    "is_default",
)


def snapshot_values(
    template: TemplateV2,
    changes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    changes = changes or {}
    return {
        field: deepcopy(
            changes[field] if field in changes else getattr(template, field)
        )
        for field in SNAPSHOT_FIELDS
    }


async def append_revision(
    session: AsyncSession,
    *,
    template: TemplateV2,
    revision: int,
    reason: str,
    changes: dict[str, Any] | None = None,
) -> TemplateV2Revision:
    if revision > MAX_REVISIONS_PER_TEMPLATE:
        stale_revisions = list(
            await session.scalars(
                select(TemplateV2Revision.revision)
                .where(TemplateV2Revision.template_id == template.id)
                .order_by(TemplateV2Revision.revision.desc())
                .offset(MAX_REVISIONS_PER_TEMPLATE - 1)
            )
        )
        if stale_revisions:
            await session.execute(
                delete(TemplateV2Revision).where(
                    TemplateV2Revision.template_id == template.id,
                    TemplateV2Revision.revision.in_(stale_revisions),
                )
            )

    entry = TemplateV2Revision(
        template_id=template.id,
        revision=revision,
        reason=reason,
        **snapshot_values(template, changes),
    )
    session.add(entry)
    return entry


async def list_revisions(
    session: AsyncSession,
    template_id: str,
) -> list[TemplateV2Revision]:
    return list(
        await session.scalars(
            select(TemplateV2Revision)
            .where(TemplateV2Revision.template_id == template_id)
            .order_by(TemplateV2Revision.revision.desc())
        )
    )


async def get_revision(
    session: AsyncSession,
    template_id: str,
    revision: int,
) -> TemplateV2Revision | None:
    return (
        await session.execute(
            select(TemplateV2Revision).where(
                TemplateV2Revision.template_id == template_id,
                TemplateV2Revision.revision == revision,
            )
        )
    ).scalar_one_or_none()


def restore_changes(entry: TemplateV2Revision) -> dict[str, Any]:
    return {
        field: deepcopy(getattr(entry, field))
        for field in SNAPSHOT_FIELDS
    }
