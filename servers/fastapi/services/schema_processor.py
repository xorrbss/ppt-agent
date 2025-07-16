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
        self, data: dict, target_key: str, current_path: Optional[List[str]] = None
    ) -> List[List[str]]:
        if current_path is None:
            current_path = []
        paths = []
        if target_key in data:
            paths.append(current_path.copy())

        for key, value in data.items():
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

    def get_dict_at_path(self, data: dict, path: List[str]) -> dict:
        current = data

        for part in path:
            if part.isdigit():
                current = current[int(part)]
            else:
                current = current[part]

        return current

    def set_dict_at_path(self, data: dict, path: List[str], value) -> None:
        if not path:
            raise ValueError("Path cannot be empty")

        current = data

        # Navigate to the parent of the target location
        for part in path[:-1]:
            if part.isdigit():
                index = int(part)
                if index >= len(current):
                    # Extend list if needed
                    current.extend([{}] * (index - len(current) + 1))
                current = current[index]
            else:
                if part not in current:
                    current[part] = {}
                current = current[part]

        # Set the value at the final path component
        final_part = path[-1]
        if final_part.isdigit():
            index = int(final_part)
            if index >= len(current):
                # Extend list if needed
                current.extend([None] * (index - len(current) + 1))
            current[index] = value
        else:
            current[final_part] = value

    def remove_image_url_fields(self, data: dict) -> dict:
        copied_data = data.copy()

        image_type_paths = self.find_dict_with_key(copied_data, "__image_type__")

        for path in image_type_paths:
            dict_at_path = self.get_dict_at_path(copied_data, path)
            if "properties" in dict_at_path:
                del dict_at_path["properties"]["url"]
            dict_at_parent_path = self.get_dict_at_path(copied_data, path[:-1])
            if "required" in dict_at_parent_path:
                dict_at_parent_path["required"].remove("url")

        return copied_data
