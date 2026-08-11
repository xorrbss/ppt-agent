from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from pydantic import TypeAdapter

from templates.v2.models.elements import ChartType, DataLabelPosition, SlideElement
from templates.v2.models.elements.chart import ChartType as DirectChartType
from templates.v2.models.layouts import SlideLayouts


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
GENERATOR_PATH = REPOSITORY_ROOT / "contracts" / "template_v2" / "generate.py"
SCHEMA_PATH = REPOSITORY_ROOT / "contracts" / "template_v2" / "v1" / "schema.json"
AST_SCHEMA_PATH = (
    REPOSITORY_ROOT / "contracts" / "template_v2" / "ast" / "v1" / "schema.json"
)
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
TYPESCRIPT_PATH = (
    REPOSITORY_ROOT
    / "servers"
    / "nextjs"
    / "lib"
    / "template-v2-html"
    / "template-v2-contract.generated.ts"
)
JAVASCRIPT_PATH = TYPESCRIPT_PATH.with_suffix(".mjs")


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "template_v2_contract_generator", GENERATOR_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stage_ast_wire_fixture(repository_root: Path) -> Path:
    fixture_path = repository_root / AST_FIXTURE_WIRE_PATH.relative_to(REPOSITORY_ROOT)
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_bytes(AST_FIXTURE_WIRE_PATH.read_bytes())
    return fixture_path


def _root_element_union(schema: dict[str, object]) -> dict[str, object]:
    component = schema["$defs"]["Component"]  # type: ignore[index]
    return component["properties"]["elements"]["items"]  # type: ignore[index]


def _discriminator_values(
    schema: dict[str, object], union: dict[str, object]
) -> list[str]:
    definitions = schema["$defs"]
    values = []
    for variant in union["oneOf"]:  # type: ignore[index]
        name = variant["$ref"].removeprefix("#/$defs/")
        values.append(definitions[name]["properties"]["type"]["const"])  # type: ignore[index]
    return values


def test_generated_schema_is_the_pydantic_contract() -> None:
    generated = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    expected = SlideLayouts.model_json_schema()

    assert generated == expected
    assert generated["$defs"]["ChartType"]["enum"] == [
        item.value for item in ChartType
    ]
    assert generated["$defs"]["DataLabelPosition"]["enum"] == [
        item.value for item in DataLabelPosition
    ]

    root_union = _root_element_union(generated)
    adapter_schema = TypeAdapter(SlideElement).json_schema()
    assert _discriminator_values(generated, root_union) == _discriminator_values(
        adapter_schema, adapter_schema
    )
    assert set(_discriminator_values(generated, root_union)) == set(
        root_union["discriminator"]["mapping"]
    )


def test_public_python_model_imports_remain_compatible() -> None:
    assert ChartType is DirectChartType
    assert ChartType("bar") is ChartType.BAR
    assert TypeAdapter(SlideElement).validate_python(
        {
            "type": "vector",
            "points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}],
        }
    ).type == "vector"


def test_rendering_is_deterministic_utf8_with_lf() -> None:
    generator = _load_generator()
    first = generator.render_outputs()
    second = generator.render_outputs()

    assert first == second
    assert set(first) == {
        SCHEMA_PATH,
        TYPESCRIPT_PATH,
        JAVASCRIPT_PATH,
        AST_SCHEMA_PATH,
        AST_TYPESCRIPT_PATH,
        AST_FIXTURE_PATH,
    }
    for content in first.values():
        assert content.endswith(b"\n")
        assert b"\r\n" not in content


def test_check_detects_missing_and_changed_outputs_without_writing(tmp_path: Path) -> None:
    generator = _load_generator()
    first = tmp_path / "first.generated"
    second = tmp_path / "second.generated"
    outputs = {first: b"first\n", second: b"second\n"}

    assert generator.stale_outputs(outputs) == [first, second]
    first.write_bytes(outputs[first])
    second.write_bytes(outputs[second])
    assert generator.stale_outputs(outputs) == []

    changed = b"locally edited\r\n"
    second.write_bytes(changed)
    assert generator.stale_outputs(outputs) == [second]
    assert second.read_bytes() == changed


def test_cli_check_is_cwd_independent_and_read_only(tmp_path: Path) -> None:
    before = {
        path: path.read_bytes()
        for path in _load_generator().render_outputs()
    }
    result = subprocess.run(
        [sys.executable, str(GENERATOR_PATH), "--check"],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "up to date" in result.stdout
    assert {path: path.read_bytes() for path in before} == before


def test_cli_check_fails_for_missing_outputs_without_creating_them(
    tmp_path: Path,
) -> None:
    staged_fixture = _stage_ast_wire_fixture(tmp_path)
    staged_fixture_bytes = staged_fixture.read_bytes()

    result = subprocess.run(
        [
            sys.executable,
            str(GENERATOR_PATH),
            "--check",
            "--repository-root",
            str(tmp_path),
        ],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 1
    assert "generated contracts are stale" in result.stderr
    assert staged_fixture.read_bytes() == staged_fixture_bytes
    assert {
        path.relative_to(tmp_path)
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == {AST_FIXTURE_WIRE_PATH.relative_to(REPOSITORY_ROOT)}
