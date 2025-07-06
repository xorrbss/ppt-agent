import aiohttp
from fastapi import HTTPException
from api.models import LogMetadata
from api.routers.presentation.models import OllamaModelStatusResponse
from api.services.logging import LoggingService
from api.utils.model_utils import (
    get_llm_provider_url_or,
    get_ollama_request_headers,
)


class ListPulledOllamaModelsHandler:

    async def get(self, logging_service: LoggingService, log_metadata: LogMetadata):
        logging_service.logger.info(
            logging_service.message("Listing Ollama models"),
            extra=log_metadata.model_dump(),
        )
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{get_llm_provider_url_or()}/api/tags",
                headers=get_ollama_request_headers(),
            ) as response:
                if response.status == 200:
                    response_data = await response.json()
                elif response.status == 403:
                    raise HTTPException(
                        status_code=403,
                        detail="Forbidden: Please check your Ollama Configuration",
                    )
                else:
                    raise HTTPException(
                        status_code=response.status,
                        detail=f"Failed to list Ollama models: {response.status}",
                    )

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
