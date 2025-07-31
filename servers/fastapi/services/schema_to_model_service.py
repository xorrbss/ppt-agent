import json
import tempfile
from pydantic import BaseModel

from services import TEMP_FILE_SERVICE
from utils.randomizers import get_random_uuid


class SchemaToModelService:
    def __init__(self):
        self.temp_dir = TEMP_FILE_SERVICE.create_temp_dir()
        self._records = {}

    def convert(self, schema: dict, identifier: str) -> BaseModel:
        return BaseModel.model_validate(schema)

    def schema_to_pydantic_model(self, schema: dict, class_name: str):
        schema_path = TEMP_FILE_SERVICE.create_temp_file_path(
            get_random_uuid() + ".json", self.temp_dir
        )
        with open(schema_path, "w") as f:
            json.dump(schema, f)

        generated_model_path = TEMP_FILE_SERVICE.create_temp_file_path(
            get_random_uuid() + ".py", self.temp_dir
        )
        # generate(
        #     input_=Path(schema_path),
        #     input_file_type=InputFileType.JsonSchema,
        #     output=Path(output_file),
        #     output_model_type=DataModelType.PydanticV2BaseModel,
        #     class_name=class_name,
        #     use_annotated=False,
        #     field_constraints=True,
        # )

        # Path(schema_file).unlink(missing_ok=True)

        return generated_model_path
