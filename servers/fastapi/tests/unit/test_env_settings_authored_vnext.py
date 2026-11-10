import ast
from pathlib import Path

import pytest

from utils.env_settings import authored_vnext
from utils.env_settings.authored_vnext import (
    get_authored_retained_reference_root,
    get_authored_vnext_default_company_standard_id,
    get_authored_vnext_repository_mode,
    is_authored_vnext_master_enabled,
    is_deck_ir_dev_fixture_allowed,
    is_deck_ir_generation_enabled,
)


OPT_IN_FLAGS = {
    "DECK_IR_ALLOW_DEV_FIXTURE": is_deck_ir_dev_fixture_allowed,
    "DECK_IR_GENERATION_ENABLED": is_deck_ir_generation_enabled,
}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in authored_vnext.ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("name", sorted(OPT_IN_FLAGS))
def test_opt_in_flags_default_off_and_only_truthy_set_enables(name, monkeypatch):
    accessor = OPT_IN_FLAGS[name]
    assert accessor() is False
    for raw in ("", "  ", "0", "false", "off", "no", "enabled", "TRUE ", "y"):
        monkeypatch.setenv(name, raw)
        assert accessor() is (raw.strip().lower() in {"1", "true", "yes", "on"}), raw
    for raw in ("1", "true", " True ", "YES", "on"):
        monkeypatch.setenv(name, raw)
        assert accessor() is True, raw


def test_master_flag_defaults_on_and_falsy_set_disables(monkeypatch):
    assert is_authored_vnext_master_enabled() is True
    for raw in ("0", "false", "FALSE", " off ", "no"):
        monkeypatch.setenv("AUTHORED_VNEXT_MASTER_ENABLED", raw)
        assert is_authored_vnext_master_enabled() is False, raw
    # Behaviour change vs. the old `== "true"` comparison: 1/yes/on and
    # unrecognised text keep the feature enabled instead of disabling it.
    for raw in ("1", "yes", "on", "true", "", "typo"):
        monkeypatch.setenv("AUTHORED_VNEXT_MASTER_ENABLED", raw)
        assert is_authored_vnext_master_enabled() is True, raw


def test_repository_mode(monkeypatch):
    assert get_authored_vnext_repository_mode() == "sql"
    monkeypatch.setenv("AUTHORED_VNEXT_REPOSITORY_MODE", "")
    assert get_authored_vnext_repository_mode() == "sql"
    monkeypatch.setenv("AUTHORED_VNEXT_REPOSITORY_MODE", " FILE ")
    assert get_authored_vnext_repository_mode() == "file"


def test_default_company_standard_id(monkeypatch):
    assert get_authored_vnext_default_company_standard_id() == "master:ibiz-proposal-blue"
    monkeypatch.setenv("AUTHORED_VNEXT_DEFAULT_COMPANY_STANDARD_ID", "  ")
    assert get_authored_vnext_default_company_standard_id() == ""
    monkeypatch.setenv("AUTHORED_VNEXT_DEFAULT_COMPANY_STANDARD_ID", " master:x ")
    assert get_authored_vnext_default_company_standard_id() == "master:x"


def test_retained_reference_root(monkeypatch):
    assert get_authored_retained_reference_root() is None
    monkeypatch.setenv("AUTHORED_RETAINED_REFERENCE_ROOT", "C:/refs")
    assert get_authored_retained_reference_root() == "C:/refs"


def test_module_layering_and_env_names_cover_every_read():
    source = Path(authored_vnext.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    assert not any(m == "services" or m.startswith("services.") for m in modules)
    assert not any(m == "api" or m.startswith("api.") for m in modules)
    assert "os" not in modules
    read_names = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        and node.args[0].value.isupper()
    }
    assert read_names == set(authored_vnext.ENV_NAMES)
