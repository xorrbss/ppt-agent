"""Defects found by rehearsing the runtime analyzer rather than unit-testing it.

Each one produced a plausible-looking result instead of an error, which is why the
green suite did not catch any of them.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import template_v2_pptx_ingestion_service as ingestion
from services.template_v2_pptx_storage import (
    private_asset_reference,
    relocate_runtime_assets,
    resolve_private_asset,
)


def _runtime_job(*, deleted: bool) -> SimpleNamespace:
    return SimpleNamespace(
        analysis_result={"analyzer": ingestion.RUNTIME_ANALYSIS_MARKER},
        source_deleted_at=datetime.now(timezone.utc) if deleted else None,
    )


def test_confirm_is_refused_once_retention_reclaimed_the_media():
    """Otherwise the template persists references to files retention already deleted."""
    assert ingestion._runtime_assets_reclaimed(_runtime_job(deleted=True)) is True


def test_confirm_stays_open_while_the_media_is_intact():
    assert ingestion._runtime_assets_reclaimed(_runtime_job(deleted=False)) is False


def test_a_deterministic_import_still_confirms_after_its_source_is_reclaimed():
    """Its analysis is self-contained, so losing the source costs only the audit copy."""
    job = SimpleNamespace(
        analysis_result={"candidates": {}},
        source_deleted_at=datetime.now(timezone.utc),
    )

    assert ingestion._runtime_assets_reclaimed(job) is False


@pytest.mark.parametrize("elements", [None, "not-a-list", 7])
def test_a_layout_without_an_elements_array_is_left_for_the_validator(elements):
    """Defaulting to [] here turned a broken slide into a silently blank one."""
    rewritten = ingestion._with_private_asset_references(
        [{"id": "slide_1", "elements": elements}], SimpleNamespace(reference_for=lambda _u: None)
    )

    assert rewritten[0].get("elements") == elements


def test_a_layout_missing_elements_entirely_is_left_for_the_validator():
    rewritten = ingestion._with_private_asset_references(
        [{"id": "slide_1"}], SimpleNamespace(reference_for=lambda _u: None)
    )

    assert "elements" not in rewritten[0]


def _runtime_output(root: Path, *names: str) -> Path:
    run_directory = root / "pptx-to-json" / uuid.uuid4().hex
    media = run_directory / "images"
    media.mkdir(parents=True)
    for name in names:
        (media / name).write_bytes(b"\x89PNG\r\n\x1a\n" + name.encode())
    (run_directory / "presentation.json").write_text('{"layouts": []}', encoding="utf-8")
    return run_directory


def test_relocation_discards_the_converter_run_directory(tmp_path, monkeypatch):
    """It holds presentation.json -- the deck's full text -- outside retention's tree."""
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
    import_id = uuid.uuid4()
    run_directory = _runtime_output(tmp_path / "app-data", "image1.png")

    relocate_runtime_assets(run_directory, import_id=import_id)

    assert not run_directory.exists(), "the run directory must not survive the import"
    asset = resolve_private_asset(private_asset_reference(import_id, "image1.png"))
    assert asset.is_file(), "the media itself must have been taken over"


def test_relocation_leaves_no_temporary_files_behind(tmp_path, monkeypatch):
    """The temp name is per-call, so a leftover would mean an unfinished publish."""
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
    import_id = uuid.uuid4()
    run_directory = _runtime_output(tmp_path / "app-data", "a.png", "b.png")

    relocate_runtime_assets(run_directory, import_id=import_id)

    assets = resolve_private_asset(private_asset_reference(import_id, "a.png")).parent
    assert sorted(p.name for p in assets.iterdir()) == ["a.png", "b.png"]
