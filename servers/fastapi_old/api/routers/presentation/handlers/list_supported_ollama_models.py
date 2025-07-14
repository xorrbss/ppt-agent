from api.models import LogMetadata, OllamaModelMetadata
from api.routers.presentation.models import OllamaSupportedModelsResponse
from api.services.logging import LoggingService
from api.utils.supported_ollama_models import SUPPORTED_OLLAMA_MODELS


class ListSupportedOllamaModelsHandler:
    async def get(self, logging_service: LoggingService, log_metadata: LogMetadata):
        logging_service.logger.info(
            logging_service.message("Listing supported Ollama models"),
            extra=log_metadata.model_dump(),
        )

        return OllamaSupportedModelsResponse(
            models=SUPPORTED_OLLAMA_MODELS.values(),
        )
