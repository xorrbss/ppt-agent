from services.redis_service import RedisService
from services.temp_file_service import TempFileService
from services.database import sql_engine


TEMP_FILE_SERVICE = TempFileService()
SQL_ENGINE = sql_engine
REDIS_SERVICE = RedisService()
