import os
import aiohttp
from api.models import LogMetadata
from api.routers.presentation.models import OllamaModelStatusResponse
from api.services.logging import LoggingService
from api.utils.model_utils import get_llm_api_key_or, get_llm_provider_url_or


class ListPulledOllamaModelsHandler:

    async def get(self, logging_service: LoggingService, log_metadata: LogMetadata):
        logging_service.logger.info(
            logging_service.message("Listing Ollama models"),
            extra=log_metadata.model_dump(),
        )

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{get_llm_provider_url_or()}/api/tags",
                headers={"Authorization": f"Bearer {get_llm_api_key_or()}"},
            ) as response:
                response_data = await response.json()

        logging_service.logger.info(
            logging_service.message(response_data),
            extra=log_metadata.model_dump(),
        )

        return [
            OllamaModelStatusResponse(
                name=model["model"],
                size=model["size"],
                status="pulled",
                downloaded=model["size"],
                done=True,
            )
            for model in response_data["models"]
        ]
