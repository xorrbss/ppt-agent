from typing import List

from models.json_path_guide import JsonPathGuide, DictGuide, ListGuide


def get_dict_paths_with_key(data: dict, key: str) -> List[JsonPathGuide]:
    result = []

    def _find_paths(obj, current_path: List[DictGuide | ListGuide]):
        if isinstance(obj, dict):
            if key in obj:
                result.append(JsonPathGuide(guides=current_path.copy()))
            for k, v in obj.items():
                new_path = current_path + [DictGuide(key=k)]
                _find_paths(v, new_path)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                new_path = current_path + [ListGuide(index=i)]
                _find_paths(item, new_path)

    _find_paths(data, [])
    return result


def get_dict_at_path(data: dict, path: JsonPathGuide) -> dict:
    current = data
    for guide in path.guides:
        if isinstance(guide, DictGuide):
            current = current[guide.key]
        elif isinstance(guide, ListGuide):
            current = current[guide.index]
    return current


def set_dict_at_path(data: dict, path: JsonPathGuide, value: dict):
    current = data
    for guide in path.guides[:-1]:
        if isinstance(guide, DictGuide):
            current = current[guide.key]
        elif isinstance(guide, ListGuide):
            current = current[guide.index]

    if path.guides:
        final_guide = path.guides[-1]
        if isinstance(final_guide, DictGuide):
            current[final_guide.key] = value
        elif isinstance(final_guide, ListGuide):
            current[final_guide.index] = value
