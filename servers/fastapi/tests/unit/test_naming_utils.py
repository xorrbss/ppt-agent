"""Gate for ``utils.naming.snake_to_camel`` and the alias generators that use it.

Two of the former copies preserved the tail's case and four flattened it
with ``str.capitalize``.  One helper now serves all six, so this test proves
that no declared field name of any model behind those generators produces a
different alias under the two rules: a persisted camelCase key can therefore
not have changed.
"""

from __future__ import annotations

import importlib
import inspect

import pytest
from pydantic import BaseModel

from utils.naming import snake_to_camel

_ALIAS_GENERATOR_MODULES = (
    "models.authored_vnext.master.contract",
    "services.authored_vnext.deck_ir.contracts",
    "services.authored_vnext.deck_ir.canonical",
    "models.authored_vnext.narrative_ir_contracts",
    "services.authored_vnext.generation.ai_text_zones",
    "services.authored_vnext.semantic.canonical",
    "models.authored_vnext.semantic.contracts",
    "services.authored_vnext.design_pack.models",
    "services.authored_vnext.design_pack.manifest",
    "services.source_content.contracts",
)


def _capitalize_form(name: str) -> str:
    first, *rest = name.split("_")
    return first + "".join(part.capitalize() for part in rest)


def test_tail_case_is_preserved() -> None:
    assert snake_to_camel("foo_bar_baz") == "fooBarBaz"
    assert snake_to_camel("foo_BAR") == "fooBAR"
    assert snake_to_camel("gate0_structural_v3") == "gate0StructuralV3"
    assert snake_to_camel("single") == "single"
    assert snake_to_camel("") == ""


def _model_field_names(module_name: str) -> set[str]:
    module = importlib.import_module(module_name)
    names: set[str] = set()
    for _, member in inspect.getmembers(module, inspect.isclass):
        if issubclass(member, BaseModel) and member is not BaseModel:
            names.update(member.model_fields)
    return names


@pytest.mark.parametrize("module_name", _ALIAS_GENERATOR_MODULES)
def test_no_declared_field_alias_changes_under_the_shared_rule(module_name: str) -> None:
    names = _model_field_names(module_name)
    assert names, f"{module_name} declares no pydantic models"
    differing = sorted(name for name in names if snake_to_camel(name) != _capitalize_form(name))
    assert differing == []
