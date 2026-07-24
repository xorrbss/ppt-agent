import asyncio
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select

from models.sql.presentation import PresentationModel
from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_revision import TemplateV2Revision
from services.template_v2_revision_service import (
    MAX_REVISIONS_PER_TEMPLATE,
    append_revision,
)
from templates.v2.constants import TEMPLATE_V2_VERSION


def test_revision_journal_keeps_only_the_most_recent_entries(tmp_path):
    async def scenario() -> None:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'revision-retention.db'}"
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        presentation = PresentationModel(
            id=uuid.uuid4(),
            content="Revision retention",
            n_slides=1,
            language="en",
            version=TEMPLATE_V2_VERSION,
            mode="template",
        )
        template = TemplateV2(
            id="retained-template",
            presentation_id=presentation.id,
            name="Revision 1",
            layouts={"layouts": []},
        )

        async with engine.begin() as connection:
            await connection.run_sync(PresentationModel.__table__.create)
            await connection.run_sync(TemplateV2.__table__.create)
            await connection.run_sync(TemplateV2Revision.__table__.create)

        async with session_factory() as session:
            session.add_all([presentation, template])
            await session.commit()
            for revision in range(1, MAX_REVISIONS_PER_TEMPLATE + 6):
                await append_revision(
                    session,
                    template=template,
                    revision=revision,
                    reason="autosave",
                    changes={"name": f"Revision {revision}"},
                )
                await session.flush()
            await session.commit()

            entries = list(
                await session.scalars(
                    select(TemplateV2Revision)
                    .where(
                        TemplateV2Revision.template_id == template.id
                    )
                    .order_by(TemplateV2Revision.revision)
                )
            )
            assert len(entries) == MAX_REVISIONS_PER_TEMPLATE
            assert entries[0].revision == 6
            assert entries[-1].revision == 55
            assert entries[0].name == "Revision 6"

        await engine.dispose()

    asyncio.run(scenario())
