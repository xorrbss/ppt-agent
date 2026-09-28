"""Canvas admission must reproduce the checked-in baseline exactly.

See docs/canvas-expansion-admission-spec-20260929.md §4. Regenerate the
fixture only for an intended admission change:
``uv run python scripts/canvas_admission_table.py --write``.
"""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path

import pytest


FASTAPI_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = FASTAPI_ROOT / "scripts" / "canvas_admission_table.py"


def _table_module():
    spec = spec_from_file_location("canvas_admission_table", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def table(monkeypatch: pytest.MonkeyPatch):
    module = _table_module()
    for name in [name for name in os.environ if name.startswith(module.EXACT_ENV_PREFIX)]:
        monkeypatch.delenv(name)
    # Packaged catalogue only: every lookup must read the shipped directory.
    import utils.authored_styles as catalogue
    import utils.svg_template_rules as svg_rules

    assert catalogue.AUTHORED_STYLES_DIRECTORY == FASTAPI_ROOT / "authored_styles"
    assert svg_rules.SUPPORTED_TEMPLATE_IDS == frozenset({"default", "osb-smart-banking", "screenshot-blue"})
    return module


def test_current_admission_reproduces_checked_in_baseline(table) -> None:
    expected = json.loads(table.FIXTURE_PATH.read_text(encoding="utf-8"))
    actual = table.build_document()

    differences = table.diff_documents(expected, actual)
    assert not differences, "canvas admission drifted from the baseline:\n" + "\n".join(differences[:50])
    assert table.render(actual) == table.FIXTURE_PATH.read_text(encoding="utf-8")


def test_baseline_covers_every_caller_and_combination(table) -> None:
    document = json.loads(table.FIXTURE_PATH.read_text(encoding="utf-8"))
    assert document["header"]["schemaVersion"] == "canvas-admission-baseline/v1"
    keys = [tuple(row[field] for field in table.KEY_FIELDS) for row in document["rows"]]
    combos = list(table.combinations())
    assert len(set(keys)) == len(keys) == len(table.CALLERS) * len(combos)
    assert keys == sorted(keys)
