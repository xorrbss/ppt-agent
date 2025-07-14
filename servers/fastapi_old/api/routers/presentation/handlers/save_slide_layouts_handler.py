from api.models import LogMetadata
from api.routers.presentation.models import SaveSlideLayoutsRequest
from api.services.database import get_sql_session
from api.services.logging import LoggingService


class SaveSlideLayoutsHandler:

    def __init__(self, data: SaveSlideLayoutsRequest):
        self.data = data

    async def post(self, logging_service: LoggingService, log_metadata: LogMetadata):

        with get_sql_session() as sql_session:
            for layout in self.data.layouts:
                sql_session.merge(layout)
            sql_session.commit()

        return self.data
