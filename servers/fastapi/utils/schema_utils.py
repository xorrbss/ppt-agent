from copy import deepcopy
from typing import List

from utils.dict_utils import get_dict_paths_with_key, get_dict_at_path, set_dict_at_path


def resolve_refs(schema, defs):
    if isinstance(schema, dict):
        if "$ref" in schema:
            ref_path = schema["$ref"]
            if ref_path.startswith("#/$defs/"):
                def_key = ref_path.replace("#/$defs/", "")
                return resolve_refs(defs[def_key], defs)
            else:
                raise ValueError(f"Unsupported $ref path: {ref_path}")
        else:
            return {k: resolve_refs(v, defs) for k, v in schema.items()}
    elif isinstance(schema, list):
        return [resolve_refs(item, defs) for item in schema]
    else:
        return schema


def flatten_schema(schema):
    schema = deepcopy(schema)
    defs = schema.pop("$defs", {})
    return resolve_refs(schema, defs)


def remove_fields_from_schema(schema: dict, fields_to_remove: List[str]):
    schema = deepcopy(schema)
    properties_paths = get_dict_paths_with_key(schema, "properties")
    for path in properties_paths:
        parent_obj = get_dict_at_path(schema, path)
        if "properties" in parent_obj and isinstance(parent_obj["properties"], dict):
            for field in fields_to_remove:
                if field in parent_obj["properties"]:
                    del parent_obj["properties"][field]

    required_paths = get_dict_paths_with_key(schema, "required")
    for path in required_paths:
        parent_obj = get_dict_at_path(schema, path)
        if "required" in parent_obj and isinstance(parent_obj["required"], list):
            parent_obj["required"] = [
                field
                for field in parent_obj["required"]
                if field not in fields_to_remove
            ]

    return schema
