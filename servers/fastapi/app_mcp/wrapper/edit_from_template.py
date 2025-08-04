from typing import Dict, Any
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from models.presentation_from_template import GetPresentationUsingTemplateRequest
from utils.dict_utils import deep_update
from utils.export_utils import export_presentation
from sqlmodel import select
from fastapi import HTTPException

class EditFromTemplateTools:
    def __init__(self):
        pass

    def register(self, mcp):
        @mcp.tool("edit_from_template")
        async def edit_from_template(
            data: GetPresentationUsingTemplateRequest,
            sql_session
        ) -> Dict[str, Any]:
            """
            Create a new presentation from a template and updated slide data, then export.
            """
            presentation = await sql_session.get(PresentationModel, data.presentation_id)
            if not presentation:
                raise HTTPException(status_code=404, detail="Presentation not found")
            slides = await sql_session.scalars(
                select(SlideModel).where(SlideModel.presentation == data.presentation_id)
            )

            new_presentation = presentation.get_new_presentation()
            new_slides = []
            for each_slide in slides:
                updated_content = None
                new_slide_data = list(filter(lambda x: x.index == each_slide.index, data.data))
                if new_slide_data:
                    updated_content = deep_update(each_slide.content, new_slide_data[0].content)
                new_slides.append(
                    each_slide.get_new_slide(new_presentation.id, updated_content)
                )

            sql_session.add(new_presentation)
            sql_session.add_all(new_slides)
            await sql_session.commit()

            presentation_and_path = await export_presentation(
                new_presentation.id, new_presentation.title, data.export_as
            )

            return {
                **presentation_and_path.model_dump(),
                "edit_path": f"/presentation?id={new_presentation.id}",
            }
