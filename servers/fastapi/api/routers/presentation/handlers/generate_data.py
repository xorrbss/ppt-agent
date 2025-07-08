import os
import random
import uuid

from fastapi import HTTPException
from api.models import LogMetadata, SessionModel
from api.routers.presentation.handlers.list_supported_ollama_models import (
    SUPPORTED_OLLAMA_MODELS,
)
from api.routers.presentation.models import PresentationGenerateRequest
from api.services.logging import LoggingService
from api.sql_models import KeyValueSqlModel, PresentationSqlModel
from api.services.database import get_sql_session
from api.utils.model_utils import is_custom_llm_selected, is_ollama_selected
from ppt_config_generator.models import PresentationMarkdownModel, SlideStructureModel
from ppt_config_generator.structure_generator import generate_presentation_structure

SLIDES_WITHOUT_GRAPH = [2, 4, 6, 7, 8]


class PresentationGenerateDataHandler:

    def __init__(self, data: PresentationGenerateRequest):
        self.data = data
        self.session = str(uuid.uuid4())

    async def post(self, logging_service: LoggingService, log_metadata: LogMetadata):
        logging_service.logger.info(
            logging_service.message(self.data.model_dump()),
            extra=log_metadata.model_dump(),
        )

        if not self.data.outlines:
            raise HTTPException(400, "Outlines can not be empty")

        key_value_model = KeyValueSqlModel(
            id=self.session,
            key=self.session,
            value=self.data.model_dump(mode="json"),
        )

        if is_ollama_selected() or is_custom_llm_selected():
            with get_sql_session() as sql_session:
                presentation = sql_session.get(
                    PresentationSqlModel, self.data.presentation_id
                )
                presentation_structure = await generate_presentation_structure(
                    PresentationMarkdownModel(
                        **{
                            "title": presentation.title,
                            "slides": presentation.outlines,
                            "notes": presentation.notes,
                        }
                    )
                )
                supports_graph = not is_custom_llm_selected()
                if is_ollama_selected():
                    model = SUPPORTED_OLLAMA_MODELS[os.getenv("OLLAMA_MODEL")]
                    supports_graph = model.supports_graph

                for each in presentation_structure.slides:
                    if each.type > 9:
                        each.type = random.choice(SLIDES_WITHOUT_GRAPH)
                    if each.type == 3:
                        each.type = 6
                    if not supports_graph:
                        if each.type == 5:
                            each.type = 1
                        elif each.type == 9:
                            each.type = 6

                presentation_outlines_len = len(presentation.outlines)
                missing_slides_len = presentation_outlines_len - len(
                    presentation_structure.slides
                )
                if missing_slides_len > 0:
                    for index in range(missing_slides_len):
                        selected_type = (
                            random.choice(SLIDES_WITHOUT_GRAPH)
                            if index != missing_slides_len - 1
                            else 1
                        )
                        presentation_structure.slides.append(
                            SlideStructureModel(type=selected_type)
                        )
                elif missing_slides_len < 0:
                    presentation_structure.slides = presentation_structure.slides[
                        :presentation_outlines_len
                    ]

                presentation.structure = presentation_structure.model_dump(mode="json")
                sql_session.commit()
                sql_session.refresh(presentation)

        with get_sql_session() as sql_session:
            sql_session.add(key_value_model)
            sql_session.commit()
            sql_session.refresh(key_value_model)

        response = SessionModel(session=self.session)
        logging_service.logger.info(
            logging_service.message(response),
            extra=log_metadata.model_dump(),
        )

        return response
