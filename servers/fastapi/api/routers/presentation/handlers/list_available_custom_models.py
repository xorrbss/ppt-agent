from typing import Optional
from api.services.logging import LoggingService
from api.models import LogMetadata
from api.utils.model_utils import list_available_custom_models


class ListAvailableCustomModelsHandler:

    def __init__(self, url: Optional[str] = None, api_key: Optional[str] = None):
        self.url = url
        self.api_key = api_key

    async def get(self, logging_service: LoggingService, log_metadata: LogMetadata):
        return await list_available_custom_models(self.url, self.api_key)
