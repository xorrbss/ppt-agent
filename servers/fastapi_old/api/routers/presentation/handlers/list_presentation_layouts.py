from sqlmodel import select
from api.models import LogMetadata
from api.services.database import get_sql_session
from api.services.logging import LoggingService
from api.sql_models import PresentationLayoutSqlModel


class ListPresentationLayoutsHandler:

    def __init__(self):
        pass

    async def get(self, logging_service: LoggingService, log_metadata: LogMetadata):
        with get_sql_session() as sql_session:
            layouts = sql_session.exec(select(PresentationLayoutSqlModel)).all()
        return layouts
