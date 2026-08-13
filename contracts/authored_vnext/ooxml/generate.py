"""Generate strict Python and TypeScript OOXML package API types."""

from __future__ import annotations

import argparse
import hashlib
import json
import keyword
import os
from pathlib import Path
import re
import tempfile
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_DIRECTORY = Path("contracts/authored_vnext/ooxml/v1")
SCHEMA_PATH = CONTRACT_DIRECTORY / "ooxml-package.schema.json"
PYTHON_PATH = CONTRACT_DIRECTORY / "generated" / "ooxml_package.py"
TYPESCRIPT_PATH = CONTRACT_DIRECTORY / "generated" / "ooxml-package.ts"
NEXT_TYPESCRIPT_PATH = Path(
    "servers/nextjs/lib/ooxml-vnext/generated/ooxml-package.ts"
)


def _schema(repository_root: Path) -> dict[str, Any]:
    return json.loads((repository_root / SCHEMA_PATH).read_text(encoding="utf-8"))


def _snake(value: str) -> str:
    result = re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()
    return f"{result}_" if keyword.iskeyword(result) else result


def _literal(values: list[object], *, language: str) -> str:
    encoded = [json.dumps(value, ensure_ascii=False) for value in values]
    if language == "python":
        encoded = [{"true": "True", "false": "False", "null": "None"}.get(item, item) for item in encoded]
        return f"Literal[{', '.join(encoded)}]"
    return " | ".join(encoded)


def _python_type(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if "const" in schema:
        return _literal([schema["const"]], language="python")
    if "enum" in schema:
        return _literal(schema["enum"], language="python")
    if "oneOf" in schema:
        return " | ".join(dict.fromkeys(_python_type(item) for item in schema["oneOf"]))
    schema_type = schema.get("type")
    if schema_type == "string":
        return "str"
    if schema_type == "integer":
        return "int"
    if schema_type == "number":
        return "float"
    if schema_type == "boolean":
        return "bool"
    if schema_type == "null":
        return "None"
    if schema_type == "array":
        return f"list[{_python_type(schema['items'])}]"
    if schema_type == "object":
        return "dict[str, Any]"
    raise ValueError(f"unsupported Python schema: {schema}")


def _python_field(name: str, schema: dict[str, Any], *, required: bool) -> str:
    field_type = _python_type(schema)
    if not required and "None" not in field_type.split(" | "):
        field_type += " | None"
    arguments = [f"alias={name!r}"]
    for source, target in (
        ("minimum", "ge"), ("maximum", "le"), ("exclusiveMinimum", "gt"),
        ("minLength", "min_length"), ("maxLength", "max_length"),
        ("minItems", "min_length"), ("maxItems", "max_length"), ("pattern", "pattern"),
    ):
        if source in schema:
            arguments.append(f"{target}={schema[source]!r}")
    if not required:
        arguments.insert(0, "default=None")
    return f"    {_snake(name)}: {field_type} = Field({', '.join(arguments)})"


def _render_python(schema: dict[str, Any], schema_digest: str) -> str:
    lines = [
        '"""Generated from ooxml-package.schema.json; do not edit."""',
        "",
        "from typing import Any, Literal",
        "",
        "from pydantic import BaseModel, ConfigDict, Field",
        "",
        f"CONTRACT_SCHEMA_SHA256 = {schema_digest!r}",
        "",
        "",
        "class OoxmlPackageContractModel(BaseModel):",
        "    model_config = ConfigDict(",
        "        allow_inf_nan=False,",
        "        extra=\"forbid\",",
        "        frozen=True,",
        "        populate_by_name=True,",
        "        strict=True,",
        "    )",
    ]
    model_names: list[str] = []
    for name, definition in schema["$defs"].items():
        if definition.get("type") != "object" or "properties" not in definition:
            continue
        model_names.append(name)
        lines.extend(["", "", f"class {name}(OoxmlPackageContractModel):"])
        required = set(definition.get("required", []))
        lines.extend(
            _python_field(field_name, field_schema, required=field_name in required)
            for field_name, field_schema in definition["properties"].items()
        )
    lines.extend(["", "", f"__all__ = {model_names!r}", ""])
    return "\n".join(lines)


def _typescript_type(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if "const" in schema:
        return _literal([schema["const"]], language="typescript")
    if "enum" in schema:
        return _literal(schema["enum"], language="typescript")
    if "oneOf" in schema:
        return " | ".join(dict.fromkeys(_typescript_type(item) for item in schema["oneOf"]))
    schema_type = schema.get("type")
    if schema_type == "string":
        return "string"
    if schema_type in {"integer", "number"}:
        return "number"
    if schema_type == "boolean":
        return "boolean"
    if schema_type == "null":
        return "null"
    if schema_type == "array":
        return f"readonly {_typescript_type(schema['items'])}[]"
    if schema_type == "object":
        return "Readonly<Record<string, JsonValue>>"
    raise ValueError(f"unsupported TypeScript schema: {schema}")


def _render_typescript(schema: dict[str, Any], schema_digest: str) -> str:
    lines = [
        "// Generated from ooxml-package.schema.json; do not edit.",
        f'export const CONTRACT_SCHEMA_SHA256 = "{schema_digest}" as const;',
        "",
        "export type JsonValue = string | number | boolean | null | "
        "readonly JsonValue[] | { readonly [key: string]: JsonValue };",
    ]
    for name, definition in schema["$defs"].items():
        if definition.get("type") != "object" or "properties" not in definition:
            continue
        required = set(definition.get("required", []))
        lines.extend(["", f"export interface {name} {{"])
        for field_name, field_schema in definition["properties"].items():
            optional = "" if field_name in required else "?"
            lines.append(f"  readonly {field_name}{optional}: {_typescript_type(field_schema)};")
        lines.append("}")
    lines.append("")
    return "\n".join(lines)


def render_outputs(repository_root: Path = REPOSITORY_ROOT) -> dict[Path, bytes]:
    schema = _schema(repository_root)
    canonical = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
    typescript = _render_typescript(schema, digest).encode()
    return {
        repository_root / PYTHON_PATH: _render_python(schema, digest).encode(),
        repository_root / TYPESCRIPT_PATH: typescript,
        repository_root / NEXT_TYPESCRIPT_PATH: typescript,
    }


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def generate(*, check: bool, repository_root: Path = REPOSITORY_ROOT) -> int:
    outputs = render_outputs(repository_root)
    stale = [path for path, content in outputs.items() if not path.is_file() or path.read_bytes() != content]
    if check:
        if stale:
            print("Authored vNext OOXML package generated contracts are stale:")
            for path in stale:
                print(f"  {path.relative_to(repository_root)}")
            return 1
        print("Authored vNext OOXML package generated contracts are up to date.")
        return 0
    for path, content in outputs.items():
        _atomic_write(path, content)
    print(f"Generated {len(outputs)} authored vNext OOXML package type files.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT, help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    return generate(check=arguments.check, repository_root=arguments.repository_root.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
