"""Lossless provider view; source authority remains in the durable raw input."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy

from utils.hashing import canonical_json_digest

VERSION = "svg-header-source-handles/v3"
_PREVIOUS_VERSION = "svg-header-source-handles/v2"
_LEGACY_VERSION = "svg-header-source-handles/v1"
TRANSPORT_RULES = """Resolve *_index through shared_value_dictionary, zero based. row_index is
zero based. Available-value rows use the declared page_binding; otherwise their page_id
is explicit. Source handles are opaque server provenance, never display values or
provider authority. user_fixed_01 is 0=false,1=true. Preserve display strings
verbatim; return original value_ref names.
An object {"$header_context_alias":"/style/design_pack_template"} at
header_requirements.reference_content means the complete identical value at
style.design_pack_template; resolve it there without omitting any reference evidence.
"""
_SOURCE_KINDS = {"page_id": "p", "representative_page_id": "p", "source_ref": "r", "source_digest": "s"}
_PRIVATE_KEYS = {"shared_value_dictionary", "transport_source_mapping"}
_REFERENCE_ALIAS_KEY = "$header_context_alias"
_REFERENCE_ALIAS = {_REFERENCE_ALIAS_KEY: "/style/design_pack_template"}


def _invalid():
    raise ValueError("svg_common_header_transport_input_invalid")


def _reference_alias_paths(node, path=()):
    paths = []
    if isinstance(node, dict):
        if _REFERENCE_ALIAS_KEY in node:
            paths.append(path)
        for key, item in node.items():
            paths.extend(_reference_alias_paths(item, path + (key,)))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            paths.extend(_reference_alias_paths(item, path + (index,)))
    return paths


def _opaque_sources(payload):
    originals = defaultdict(set)

    def collect(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in _SOURCE_KINDS and isinstance(value, str):
                    originals[_SOURCE_KINDS[key]].add(value)
                else:
                    collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(payload)
    # Sorted full strings give the same handles for the same frozen context.
    mapping = {kind: {f"{kind}{index}": text for index, text in enumerate(sorted(texts))}
               for kind, texts in sorted(originals.items())}
    lookup = {kind: {text: handle for handle, text in entries.items()} for kind, entries in mapping.items()}

    def replace(node):
        if isinstance(node, dict):
            return {key: lookup[_SOURCE_KINDS[key]][value]
                    if key in _SOURCE_KINDS and isinstance(value, str) else replace(value)
                    for key, value in node.items()}
        if isinstance(node, list):
            return [replace(value) for value in node]
        return node

    return replace(payload), mapping


def pack_header_transport(payload):
    """Return model input plus a full server-only source mapping and proof."""
    if (not isinstance(payload, dict) or _PRIVATE_KEYS & set(payload)
            or _reference_alias_paths(payload)):
        _invalid()
    source_digest = canonical_json_digest(payload)
    value, mapping = _opaque_sources(payload)
    dictionary, indices = [], {}

    def intern(text):
        if not isinstance(text, str):
            _invalid()
        if text not in indices:
            indices[text] = len(dictionary)
            dictionary.append(text)
        return indices[text]

    available = value.get("available_values")
    if not isinstance(available, list) or not available:
        _invalid()
    field_rows = []
    for page in available:
        if (not isinstance(page, dict) or set(page) != {"page_id", "values"}
                or not isinstance(page["page_id"], str) or not isinstance(page["values"], dict)):
            _invalid()
        fields = []
        for ref, bound in page["values"].items():
            if (not isinstance(bound, dict) or set(bound) != {"value", "user_fixed", "source_ref"}
                    or type(bound["user_fixed"]) is not bool or not isinstance(bound["source_ref"], str)):
                _invalid()
            fields.append([intern(ref), intern(bound["value"]), int(bound["user_fixed"]), bound["source_ref"]])
        field_rows.append(fields)
    encoded = {"field_columns": ["value_ref_index", "value_index", "user_fixed_01", "source_ref_handle"]}
    if "pages" in value:
        pages = value["pages"]
        if (not isinstance(pages, list) or len(pages) != len(available)
                or any(not isinstance(page, dict) or set(page) != {"page_id", "page_index", "title", "source_digest"}
                       or type(page["page_index"]) is not int or page["page_index"] != index
                       or page["page_id"] != available[index]["page_id"]
                       for index, page in enumerate(pages))):
            _invalid()
        value["pages"] = {"page_index": "row_index",
            "columns": ["page_id", "title_index", "source_digest_handle"],
            "rows": [[page["page_id"], intern(page["title"]), page["source_digest"]] for page in pages]}
        encoded.update(page_binding="pages[row_index].page_id", rows=field_rows)
    else:
        encoded.update(page_columns=["page_id", "fields"],
            rows=[[page["page_id"], fields] for page, fields in zip(available, field_rows, strict=True)])
    value["available_values"] = encoded
    value["shared_value_dictionary"] = dictionary
    style, requirements = payload.get("style"), payload.get("header_requirements")
    if isinstance(style, dict) and isinstance(requirements, dict):
        reference = style.get("design_pack_template")
        repeated = requirements.get("reference_content")
        if (isinstance(reference, dict) and reference and repeated == reference
                and canonical_json_digest(repeated) == canonical_json_digest(reference)):
            value["header_requirements"]["reference_content"] = dict(_REFERENCE_ALIAS)
    value["transport_source_mapping"] = {"version": VERSION,
        "digest": canonical_json_digest(mapping), "unpacked_context_digest": source_digest}
    restored = restore_header_transport(value, mapping, expected_context_digest=source_digest)
    if restored != payload:
        _invalid()
    return value, {"version": VERSION, "source_mapping": mapping,
        "source_mapping_digest": canonical_json_digest(mapping),
        "unpacked_context_digest": source_digest, "projected_context_digest": canonical_json_digest(value)}


def restore_header_transport(payload, mapping, *, expected_context_digest):
    """Validate server evidence, then restore exact strings; never resolve LLM output."""
    try:
        value = deepcopy(payload)
        descriptor = value.pop("transport_source_mapping")
        version = descriptor.get("version")
        if (version not in {VERSION, _PREVIOUS_VERSION, _LEGACY_VERSION}
                or descriptor != {"version": version, "digest": canonical_json_digest(mapping),
                           "unpacked_context_digest": expected_context_digest}
                or not isinstance(mapping, dict)):
            _invalid()
        aliases = _reference_alias_paths(value)
        if aliases:
            if (version != VERSION or aliases != [("header_requirements", "reference_content")]
                    or value["header_requirements"]["reference_content"] != _REFERENCE_ALIAS):
                _invalid()
            reference = value["style"]["design_pack_template"]
            if not isinstance(reference, dict) or not reference:
                _invalid()
            value["header_requirements"]["reference_content"] = deepcopy(reference)
        dictionary = value.pop("shared_value_dictionary")
        if (not isinstance(dictionary, list) or any(not isinstance(text, str) for text in dictionary)
                or len(set(dictionary)) != len(dictionary)):
            _invalid()
        used = set()

        def text(index):
            if type(index) is not int or not 0 <= index < len(dictionary):
                _invalid()
            used.add(index)
            return dictionary[index]

        available = value["available_values"]
        fixed_column = "user_fixed" if version == _LEGACY_VERSION else "user_fixed_01"
        if available["field_columns"] != ["value_ref_index", "value_index", fixed_column, "source_ref_handle"]:
            _invalid()
        if "pages" in value:
            pages = value["pages"]
            if (set(pages) != {"page_index", "columns", "rows"} or pages["page_index"] != "row_index"
                    or pages["columns"] != ["page_id", "title_index", "source_digest_handle"]
                    or set(available) != {"field_columns", "page_binding", "rows"}
                    or available["page_binding"] != "pages[row_index].page_id"):
                _invalid()
            value["pages"] = [{"page_id": page_id, "page_index": index, "title": text(title),
                               "source_digest": source} for index, (page_id, title, source) in enumerate(pages["rows"])]
            bound_rows = [(page["page_id"], fields) for page, fields in zip(value["pages"], available["rows"], strict=True)]
        else:
            if (set(available) != {"field_columns", "page_columns", "rows"}
                    or available["page_columns"] != ["page_id", "fields"]):
                _invalid()
            bound_rows = available["rows"]
        decoded = []
        for page_id, fields in bound_rows:
            bindings = {}
            for ref, item, fixed, source in fields:
                name = text(ref)
                if version != _LEGACY_VERSION:
                    if type(fixed) is not int or fixed not in (0, 1):
                        _invalid()
                    fixed = bool(fixed)
                if name in bindings or type(fixed) is not bool:
                    _invalid()
                bindings[name] = {"value": text(item), "user_fixed": fixed, "source_ref": source}
            decoded.append({"page_id": page_id, "values": bindings})
        if used != set(range(len(dictionary))):
            _invalid()
        value["available_values"] = decoded

        def restore(node):
            if isinstance(node, dict):
                return {key: mapping[_SOURCE_KINDS[key]][item]
                        if key in _SOURCE_KINDS and isinstance(item, str) else restore(item)
                        for key, item in node.items()}
            if isinstance(node, list):
                return [restore(item) for item in node]
            return node

        restored = restore(value)
        if canonical_json_digest(restored) != expected_context_digest or _opaque_sources(restored)[1] != mapping:
            _invalid()
        return restored
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise ValueError("svg_common_header_transport_input_invalid") from error
