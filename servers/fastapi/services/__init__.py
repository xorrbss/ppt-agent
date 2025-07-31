from services.redis_service import RedisService
from services.schema_to_model_service import SchemaToModelService
from services.temp_file_service import TempFileService


TEMP_FILE_SERVICE = TempFileService()
REDIS_SERVICE = RedisService()
SCHEMA_TO_MODEL_SERVICE = SchemaToModelService(TEMP_FILE_SERVICE)
