from __future__ import annotations
from copy import deepcopy
from typing import List, Optional


class SchemaProcessor:

    def resolve_refs(self, schema, defs):
        if isinstance(schema, dict):
            if "$ref" in schema:
                ref_path = schema["$ref"]
                if ref_path.startswith("#/$defs/"):
                    def_key = ref_path.replace("#/$defs/", "")
                    return self.resolve_refs(defs[def_key], defs)
                else:
                    raise ValueError(f"Unsupported $ref path: {ref_path}")
            else:
                return {k: self.resolve_refs(v, defs) for k, v in schema.items()}
        elif isinstance(schema, list):
            return [self.resolve_refs(item, defs) for item in schema]
        else:
            return schema

    def flatten_schema(self, schema):
        schema = deepcopy(schema)
        defs = schema.pop("$defs", {})
        return self.resolve_refs(schema, defs)

    def find_dict_with_key(
        self, schema: dict, target_key: str, current_path: Optional[List[str]] = None
    ) -> List[List[str]]:
        if current_path is None:
            current_path = []
        paths = []
        if target_key in schema:
            paths.append(current_path.copy())

        for key, value in schema.items():
            if isinstance(value, dict):
                new_path = current_path + [key]
                paths.extend(self.find_dict_with_key(value, target_key, new_path))
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, dict):
                        new_path = current_path + [key, str(i)]
                        paths.extend(
                            self.find_dict_with_key(item, target_key, new_path)
                        )
        return paths

    def get_dict_at_path(self, schema: dict, path: List[str]) -> dict:
        current = schema

        for part in path:
            if part.isdigit():
                current = current[int(part)]
            else:
                current = current[part]

        return current

    def remove_image_url_fields(self, schema: dict) -> dict:
        copied_schema = schema.copy()

        image_type_paths = self.find_dict_with_key(copied_schema, "_image_type")

        for path in image_type_paths:
            dict_at_path = self.get_dict_at_path(copied_schema, path)

            if dict_at_path.get("_image_type") == "image":
                if "properties" in dict_at_path and "url" in dict_at_path["properties"]:
                    del dict_at_path["properties"]["url"]

                if "required" in dict_at_path and "url" in dict_at_path["required"]:
                    dict_at_path["required"].remove("url")

        return copied_schema
