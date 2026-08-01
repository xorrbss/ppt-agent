import hashlib
from pathlib import Path
import uuid

import pytest

from services import authored_review_asset_store as asset_store


def _asset_path(app_data: Path, reference: str) -> Path:
    return app_data / "images" / Path(reference)


def test_publish_uses_content_addressed_immutable_names(monkeypatch, tmp_path):
    app_data = tmp_path / "app-data"
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    presentation_id = uuid.uuid4()

    first = asset_store.publish_reviewed_png(presentation_id, 3, b"first-png")
    second = asset_store.publish_reviewed_png(presentation_id, 3, b"second-png")

    assert first != second
    assert hashlib.sha256(b"first-png").hexdigest() in first
    assert hashlib.sha256(b"second-png").hexdigest() in second
    assert _asset_path(app_data, first).read_bytes() == b"first-png"
    assert _asset_path(app_data, second).read_bytes() == b"second-png"


def test_publish_reuses_verified_content_without_replacing(monkeypatch, tmp_path):
    app_data = tmp_path / "app-data"
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    presentation_id = uuid.uuid4()
    reference = asset_store.publish_reviewed_png(presentation_id, 0, b"same-png")

    def unexpected_replace(*_args):
        raise AssertionError("verified immutable asset must be reused")

    monkeypatch.setattr(asset_store.os, "replace", unexpected_replace)
    assert (
        asset_store.publish_reviewed_png(presentation_id, 0, b"same-png")
        == reference
    )


def test_atomic_publish_failure_preserves_existing_asset(monkeypatch, tmp_path):
    app_data = tmp_path / "app-data"
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    presentation_id = uuid.uuid4()
    old_reference = asset_store.publish_reviewed_png(
        presentation_id, 1, b"accepted-old-png"
    )
    old_path = _asset_path(app_data, old_reference)

    def fail_replace(*_args):
        raise OSError("simulated atomic publish failure")

    monkeypatch.setattr(asset_store.os, "replace", fail_replace)
    with pytest.raises(RuntimeError, match="could not be published atomically"):
        asset_store.publish_reviewed_png(presentation_id, 1, b"candidate-new-png")

    assert old_path.read_bytes() == b"accepted-old-png"
    asset_directory = old_path.parent
    assert list(asset_directory.glob("*.tmp")) == []
    assert list(asset_directory.glob("slide_1_*.png")) == [old_path]


def test_rejects_corrupt_existing_content_address(monkeypatch, tmp_path):
    app_data = tmp_path / "app-data"
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    presentation_id = uuid.uuid4()
    png = b"expected-png"
    reference = asset_store.publish_reviewed_png(presentation_id, 2, png)
    _asset_path(app_data, reference).write_bytes(b"corrupt")

    with pytest.raises(RuntimeError, match="violates its content hash"):
        asset_store.publish_reviewed_png(presentation_id, 2, png)
