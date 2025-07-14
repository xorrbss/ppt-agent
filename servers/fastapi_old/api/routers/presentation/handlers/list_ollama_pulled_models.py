from api.models import LogMetadata
from api.services.logging import LoggingService
from api.utils.model_utils import list_pulled_ollama_models


class ListPulledOllamaModelsHandler:

    async def get(self, logging_service: LoggingService, log_metadata: LogMetadata):
        logging_service.logger.info(
            logging_service.message("Listing Ollama models"),
            extra=log_metadata.model_dump(),
        )
        pulled_models = await list_pulled_ollama_models()

        logging_service.logger.info(
            logging_service.message(pulled_models),
            extra=log_metadata.model_dump(),
        )
        return pulled_models
