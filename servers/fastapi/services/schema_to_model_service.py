import asyncio
import json
import os
from pathlib import Path
from typing import Dict
from fastapi import HTTPException
from datamodel_code_generator import generate, InputFileType, DataModelType

from services.temp_file_service import TempFileService
from utils.randomizers import get_random_uuid


class SchemaToModelService:
    def __init__(self, temp_file_service: TempFileService):
        self.temp_file_service = temp_file_service
        self.temp_dir = self.temp_file_service.create_temp_dir()

        self.generated_models_dir = "generated_models"
        if os.path.exists(self.generated_models_dir):
            for file in os.listdir(self.generated_models_dir):
                if file.endswith(".py"):
                    os.remove(os.path.join(self.generated_models_dir, file))
        os.makedirs(self.generated_models_dir, exist_ok=True)

        self._records: Dict[str, str] = {}
        self._fetch_locks: Dict[str, asyncio.Lock] = {}

    def convert_path_to_module_path(self, path: str):
        return path.replace("/", ".").replace("\\", ".").replace(".py", "")

    async def get_pydantic_model_path_from_schema(
        self, identifier: str, schema: dict
    ) -> str:
        if identifier in self._fetch_locks:
            async with self._fetch_locks[identifier]:
                return self._records[identifier]
        else:
            async_lock = asyncio.Lock()
            await async_lock.acquire()
            self._fetch_locks[identifier] = async_lock
            model_path = await self.generate_pydantic_model_from_schema_async(schema)
            model_path = self.convert_path_to_module_path(model_path)
            self._records[identifier] = model_path
            async_lock.release()
            return model_path

    async def generate_pydantic_model_from_schema_async(self, schema: dict):
        return await asyncio.to_thread(self.generate_pydantic_model_from_schema, schema)

    def generate_pydantic_model_from_schema(self, schema: dict):
        generated_model_path = os.path.join(
            self.generated_models_dir, get_random_uuid() + ".py"
        )
        try:
            schema_path = self.temp_file_service.create_temp_file_path(
                get_random_uuid() + ".json", self.temp_dir
            )
            with open(schema_path, "w") as f:
                json.dump(schema, f)

            generate(
                input_=Path(schema_path),
                input_file_type=InputFileType.JsonSchema,
                output=Path(generated_model_path),
                output_model_type=DataModelType.PydanticV2BaseModel,
                class_name="GeneratedModel",
                use_annotated=False,
                field_constraints=True,
                extra_fields="ignore",
            )
        except Exception as e:
            raise HTTPException(
                status_code=500, detail="Failed to generate Pydantic model from schema"
            )
        finally:
            self.temp_file_service.cleanup_temp_file(schema_path)

        return generated_model_path
