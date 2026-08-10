"""Generate the versioned Template V2 contract and TypeScript bindings."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from importlib import import_module
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FASTAPI_ROOT = REPOSITORY_ROOT / "servers" / "fastapi"
SCHEMA_PATH = REPOSITORY_ROOT / "contracts" / "template_v2" / "v1" / "schema.json"
AST_SCHEMA_PATH = (
    REPOSITORY_ROOT / "contracts" / "template_v2" / "ast" / "v1" / "schema.json"
)
TYPESCRIPT_PATH = (
    REPOSITORY_ROOT
    / "servers"
    / "nextjs"
    / "lib"
    / "template-v2-html"
    / "template-v2-contract.generated.ts"
)
JAVASCRIPT_PATH = TYPESCRIPT_PATH.with_suffix(".mjs")
AST_TYPESCRIPT_PATH = (
    REPOSITORY_ROOT
    / "servers"
    / "nextjs"
    / "lib"
    / "template-v2-ast"
    / "template-v2-ast.generated.ts"
)
AST_FIXTURE_WIRE_PATH = (
    REPOSITORY_ROOT
    / "contracts"
    / "template_v2"
    / "fixtures"
    / "repeatable-grouped-content.a3352f3c.json"
)
AST_FIXTURE_PATH = AST_FIXTURE_WIRE_PATH.with_suffix(".ast.json")

_REFERENCE_PREFIX = "#/$defs/"
_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def _slide_layouts_model() -> type[Any]:
    backend_path = str(FASTAPI_ROOT)
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)
    return import_module("templates.v2.models.layouts").SlideLayouts


def _canonical_ast_model() -> type[Any]:
    backend_path = str(FASTAPI_ROOT)
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)
    return import_module("templates.v2.ast.models").TemplateCompilerContract


def build_schema() -> dict[str, Any]:
    """Return the strict contract emitted by the authoritative Pydantic root."""

    return _slide_layouts_model().model_json_schema()


def build_ast_schema() -> dict[str, Any]:
    """Return the canonical AST schema from the same Pydantic contract root."""

    return _canonical_ast_model().model_json_schema()


def build_ast_fixture(repository_root: Path) -> dict[str, Any]:
    """Compile the shared a3352f3c wire fixture through the Python decoder."""

    backend_path = str(FASTAPI_ROOT)
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)
    wire_path = repository_root / AST_FIXTURE_WIRE_PATH.relative_to(REPOSITORY_ROOT)
    wire = json.loads(wire_path.read_text(encoding="utf-8"))
    ast = import_module("templates.v2.ast").decode_template_ast(
        wire, source_version="a3352f3c"
    )
    return ast.model_dump(mode="json")


def _json_literal(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _reference_name(reference: str) -> str:
    if not reference.startswith(_REFERENCE_PREFIX):
        raise ValueError(f"unsupported JSON Schema reference: {reference}")
    return reference.removeprefix(_REFERENCE_PREFIX)


def _property_name(name: str) -> str:
    return name if _IDENTIFIER.fullmatch(name) else _json_literal(name)


def _typescript_type(schema: Mapping[str, Any]) -> str:
    reference = schema.get("$ref")
    if isinstance(reference, str):
        return _reference_name(reference)
    if "const" in schema:
        return _json_literal(schema["const"])
    enum_values = schema.get("enum")
    if isinstance(enum_values, list):
        return " | ".join(_json_literal(value) for value in enum_values)
    for union_keyword in ("anyOf", "oneOf"):
        variants = schema.get(union_keyword)
        if isinstance(variants, list):
            rendered = dict.fromkeys(
                _typescript_type(variant)
                for variant in variants
                if isinstance(variant, Mapping)
            )
            return " | ".join(rendered) or "never"
    schema_type = schema.get("type")
    if schema_type == "array":
        items = schema.get("items", {})
        item_type = _typescript_type(items) if isinstance(items, Mapping) else "unknown"
        return f"Array<{item_type}>"
    if schema_type == "object" or "properties" in schema:
        return _typescript_object(schema)
    return {
        "boolean": "boolean",
        "integer": "number",
        "null": "null",
        "number": "number",
        "string": "string",
    }.get(schema_type, "unknown")


def _typescript_object(schema: Mapping[str, Any]) -> str:
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        return "Record<string, unknown>"
    required_value = schema.get("required", [])
    required = set(required_value) if isinstance(required_value, list) else set()
    lines = ["{"]
    for name in sorted(properties):
        property_schema = properties[name]
        if not isinstance(property_schema, Mapping):
            continue
        optional = "" if name in required else "?"
        lines.append(
            f"  {_property_name(name)}{optional}: {_typescript_type(property_schema)};"
        )
    if schema.get("additionalProperties") not in (False, None):
        lines.append("  [key: string]: unknown;")
    lines.append("}")
    return "\n".join(lines)


def _walk_mappings(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_mappings(child)


def _discriminated_union(schema: Mapping[str, Any]) -> Mapping[str, Any]:
    candidates = []
    for node in _walk_mappings(schema):
        discriminator = node.get("discriminator")
        variants = node.get("oneOf")
        if (
            isinstance(discriminator, Mapping)
            and discriminator.get("propertyName") == "type"
            and isinstance(discriminator.get("mapping"), Mapping)
            and isinstance(variants, list)
        ):
            candidates.append(node)
    if not candidates:
        raise ValueError("Template V2 schema has no element discriminator")
    return max(candidates, key=lambda item: len(item["discriminator"]["mapping"]))


def _union_values(
    schema: Mapping[str, Any], union_schema: Mapping[str, Any]
) -> tuple[list[str], list[str]]:
    definitions = schema.get("$defs", {})
    variants = union_schema.get("oneOf", [])
    if not isinstance(definitions, Mapping) or not isinstance(variants, list):
        raise ValueError("invalid discriminated union schema")
    values: list[str] = []
    references: list[str] = []
    for variant in variants:
        if not isinstance(variant, Mapping) or not isinstance(variant.get("$ref"), str):
            raise ValueError("element variants must be local JSON Schema references")
        name = _reference_name(variant["$ref"])
        definition = definitions.get(name)
        if not isinstance(definition, Mapping):
            raise ValueError(f"missing JSON Schema definition: {name}")
        properties = definition.get("properties", {})
        type_property = properties.get("type") if isinstance(properties, Mapping) else None
        if not isinstance(type_property, Mapping) or not isinstance(
            type_property.get("const"), str
        ):
            raise ValueError(f"element definition has no string discriminator: {name}")
        values.append(type_property["const"])
        references.append(name)
    mapping = union_schema["discriminator"]["mapping"]
    if set(values) != set(mapping):
        raise ValueError("element discriminator mapping and union variants disagree")
    return values, references


def _enum_values(schema: Mapping[str, Any], definition_name: str) -> list[str]:
    definitions = schema.get("$defs", {})
    definition = definitions.get(definition_name) if isinstance(definitions, Mapping) else None
    values = definition.get("enum") if isinstance(definition, Mapping) else None
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError(f"{definition_name} must be a string enum")
    return values


def _infographic_values(schema: Mapping[str, Any]) -> list[str]:
    definitions = schema.get("$defs", {})
    infographic = definitions.get("Infographic") if isinstance(definitions, Mapping) else None
    properties = infographic.get("properties", {}) if isinstance(infographic, Mapping) else {}
    data = properties.get("data") if isinstance(properties, Mapping) else None
    if not isinstance(data, Mapping):
        raise ValueError("Infographic.data schema is missing")
    values, _ = _union_values(schema, data)
    return values


def _render_string_array(name: str, values: Sequence[str]) -> list[str]:
    lines = [f"export const {name} = Object.freeze(["]
    lines.extend(f"  {_json_literal(value)}," for value in values)
    lines.append("] as const);")
    return lines


def render_typescript(schema: Mapping[str, Any]) -> str:
    """Render strict TS types plus runtime discriminator constants from schema."""

    element_union = _discriminated_union(schema)
    element_values, element_references = _union_values(schema, element_union)
    chart_values = _enum_values(schema, "ChartType")
    data_label_values = _enum_values(schema, "DataLabelPosition")
    infographic_values = _infographic_values(schema)
    definitions = schema.get("$defs", {})
    if not isinstance(definitions, Mapping):
        raise ValueError("Template V2 schema definitions are missing")

    lines = [
        "// This file is generated by contracts/template_v2/generate.py.",
        "// Run `npm run generate:template-v2-contracts`; do not edit by hand.",
        "",
    ]
    lines.extend(_render_string_array("TEMPLATE_V2_CHART_TYPES", chart_values))
    lines.extend(
        [
            "export type TemplateV2ChartType =",
            "  (typeof TEMPLATE_V2_CHART_TYPES)[number];",
            "",
        ]
    )
    lines.extend(_render_string_array("TEMPLATE_V2_DATA_LABEL_POSITIONS", data_label_values))
    lines.extend(
        [
            "export type TemplateV2DataLabelPosition =",
            "  (typeof TEMPLATE_V2_DATA_LABEL_POSITIONS)[number];",
            "",
        ]
    )
    lines.extend(_render_string_array("TEMPLATE_V2_INFOGRAPHIC_TYPES", infographic_values))
    lines.extend(
        [
            "export type TemplateV2InfographicType =",
            "  (typeof TEMPLATE_V2_INFOGRAPHIC_TYPES)[number];",
            "",
        ]
    )
    lines.extend(_render_string_array("TEMPLATE_V2_ELEMENT_TYPES", element_values))
    lines.extend(
        [
            "export type TemplateV2ElementType =",
            "  (typeof TEMPLATE_V2_ELEMENT_TYPES)[number];",
            "",
            "const TEMPLATE_V2_CHART_TYPE_SET: ReadonlySet<string> =",
            "  new Set(TEMPLATE_V2_CHART_TYPES);",
            "",
            "export function isTemplateV2ChartType(",
            "  value: string,",
            "): value is TemplateV2ChartType {",
            "  return TEMPLATE_V2_CHART_TYPE_SET.has(value);",
            "}",
            "",
        ]
    )
    for name in sorted(definitions):
        definition = definitions[name]
        if not isinstance(definition, Mapping):
            continue
        lines.extend([f"export type {name} = {_typescript_type(definition)};", ""])
    lines.extend(
        [
            "export type SlideElement =",
            *[f"  | {reference}" for reference in element_references],
            ";",
            "",
            f"export type TemplateV2Contract = {_typescript_type(schema)};",
            "",
        ]
    )
    return "\n".join(lines)


def render_javascript(schema: Mapping[str, Any]) -> str:
    """Render runtime constants for JavaScript renderer boundaries."""

    element_values, _ = _union_values(schema, _discriminated_union(schema))
    value_groups = (
        ("TEMPLATE_V2_CHART_TYPES", _enum_values(schema, "ChartType")),
        (
            "TEMPLATE_V2_DATA_LABEL_POSITIONS",
            _enum_values(schema, "DataLabelPosition"),
        ),
        ("TEMPLATE_V2_INFOGRAPHIC_TYPES", _infographic_values(schema)),
        ("TEMPLATE_V2_ELEMENT_TYPES", element_values),
    )
    lines = [
        "// This file is generated by contracts/template_v2/generate.py.",
        "// Run `npm run generate:template-v2-contracts`; do not edit by hand.",
        "",
    ]
    for name, values in value_groups:
        lines.append(f"export const {name} = Object.freeze([")
        lines.extend(f"  {_json_literal(value)}," for value in values)
        lines.extend(["]);", ""])
    return "\n".join(lines)


def render_ast_typescript(schema: Mapping[str, Any]) -> str:
    """Render canonical AST types without a second hand-written contract."""

    definitions = schema.get("$defs", {})
    if not isinstance(definitions, Mapping):
        raise ValueError("Template V2 AST schema definitions are missing")
    lines = [
        "// This file is generated by contracts/template_v2/generate.py.",
        "// Run `npm run generate:template-v2-contracts`; do not edit by hand.",
        "",
    ]
    for name in sorted(definitions):
        definition = definitions[name]
        if isinstance(definition, Mapping):
            lines.extend([f"export type {name} = {_typescript_type(definition)};", ""])
    lines.extend(
        [
            f"export type TemplateCompilerContract = {_typescript_type(schema)};",
            "",
        ]
    )
    return "\n".join(lines)


def render_outputs(repository_root: Path = REPOSITORY_ROOT) -> dict[Path, bytes]:
    schema = build_schema()
    ast_schema = build_ast_schema()
    ast_fixture = build_ast_fixture(repository_root)
    schema_bytes = (
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    typescript_bytes = render_typescript(schema).encode("utf-8")
    return {
        repository_root / SCHEMA_PATH.relative_to(REPOSITORY_ROOT): schema_bytes,
        repository_root / TYPESCRIPT_PATH.relative_to(REPOSITORY_ROOT): typescript_bytes,
        repository_root / JAVASCRIPT_PATH.relative_to(REPOSITORY_ROOT): render_javascript(
            schema
        ).encode("utf-8"),
        repository_root
        / AST_SCHEMA_PATH.relative_to(REPOSITORY_ROOT): (
            json.dumps(ast_schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8"),
        repository_root
        / AST_TYPESCRIPT_PATH.relative_to(REPOSITORY_ROOT): render_ast_typescript(
            ast_schema
        ).encode("utf-8"),
        repository_root / AST_FIXTURE_PATH.relative_to(REPOSITORY_ROOT): (
            json.dumps(ast_fixture, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8"),
    }


def stale_outputs(outputs: Mapping[Path, bytes]) -> list[Path]:
    return [
        path
        for path, expected in outputs.items()
        if not path.is_file() or path.read_bytes() != expected
    ]


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def generate(*, check: bool, repository_root: Path = REPOSITORY_ROOT) -> int:
    outputs = render_outputs(repository_root)
    stale = stale_outputs(outputs)
    if check:
        if stale:
            print("Template V2 generated contracts are stale:", file=sys.stderr)
            for path in stale:
                print(f"  {path.relative_to(repository_root)}", file=sys.stderr)
            print("Run `npm run generate:template-v2-contracts`.", file=sys.stderr)
            return 1
        print("Template V2 generated contracts are up to date.")
        return 0
    for path, content in outputs.items():
        _atomic_write(path, content)
    print(f"Generated {len(outputs)} Template V2 contract files.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail without writing when checked-in generated files are stale",
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=REPOSITORY_ROOT,
        help=argparse.SUPPRESS,
    )
    arguments = parser.parse_args(argv)
    return generate(
        check=arguments.check,
        repository_root=arguments.repository_root.resolve(),
    )


if __name__ == "__main__":
    raise SystemExit(main())
