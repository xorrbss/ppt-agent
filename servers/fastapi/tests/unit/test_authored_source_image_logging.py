"""What the source-image path is allowed to write to a log.

A source image is the user's own document content sitting at a server-local
path. Neither may reach an operator log: the path names where a tenant's bytes
live on disk, and the bytes themselves are the document. Every degradation here
therefore logs a reason and nothing else -- which is exactly the property that
regresses silently, because a debug-time "%s" costs nothing to add.
"""

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from services.source_images.models import SourceImageCandidate
from utils import authored_illustrations
from utils.authored_illustrations import (
    _data_uri_from_source_image,
    apply_authored_illustration,
)


def _run(coro):
    return asyncio.run(coro)


def _candidate(tmp_path: Path) -> SourceImageCandidate:
    path = tmp_path / "quarterly-revenue.png"
    Image.new("RGB", (400, 300), (12, 34, 56)).save(path)
    return SourceImageCandidate(
        asset_id="asset-1",
        path=path,
        mime_type="image/png",
        width=400,
        height=300,
        digest="d" * 64,
    )


def _assert_no_path(caplog, path: Path) -> None:
    emitted = "\n".join(
        record.getMessage() + " " + repr(record.args) for record in caplog.records
    )
    assert str(path) not in emitted
    assert path.name not in emitted
    assert str(path.parent) not in emitted


def test_an_oversized_source_image_logs_its_size_and_not_its_location(
    tmp_path, monkeypatch, caplog
):
    candidate = _candidate(tmp_path)
    monkeypatch.setattr(authored_illustrations, "MAX_INLINE_SOURCE_IMAGE_BYTES", 1)

    with caplog.at_level(logging.DEBUG, logger=authored_illustrations.__name__):
        assert _data_uri_from_source_image(candidate) is None

    assert caplog.records, "the dropped slot must still be explained"
    _assert_no_path(caplog, candidate.path)


def test_an_unreadable_source_image_logs_the_reason_and_not_the_filename(
    tmp_path, monkeypatch, caplog
):
    candidate = _candidate(tmp_path)

    real_stat = Path.stat
    seen: list[int] = []

    def _refuse(self, **kwargs):
        # The admission stat still succeeds; the size read that follows it is
        # the one that loses the race with retention, and an OSError quotes the
        # filename in its own text.
        if self == candidate.path:
            seen.append(1)
            if len(seen) > 1:
                raise OSError(13, "Permission denied", str(candidate.path))
        return real_stat(self, **kwargs)

    monkeypatch.setattr(Path, "stat", _refuse)

    with caplog.at_level(logging.DEBUG, logger=authored_illustrations.__name__):
        assert _data_uri_from_source_image(candidate) is None

    assert "Permission denied" in caplog.text
    _assert_no_path(caplog, candidate.path)


@pytest.mark.parametrize("cap", [1])
def test_the_generated_illustration_branch_keeps_the_same_silence(
    tmp_path, monkeypatch, caplog, cap
):
    """The generated image lands in shared app storage; its path is no more
    loggable than an uploaded one, and both branches share the encoder."""
    generated = tmp_path / "generated-illustration.png"
    Image.new("RGB", (64, 64), (200, 200, 200)).save(generated)
    monkeypatch.setattr(authored_illustrations, "MAX_ILLUSTRATION_BYTES", cap)

    style = SimpleNamespace(
        id="editorial",
        illustration_prompt="flat vector",
        reference_mode="style_reference",
    )

    class _Service:
        async def generate_image(self, _prompt):
            return SimpleNamespace(path=str(generated))

    html = '<div><img data-illust-prompt="a warehouse" style="width:10px"></div>'
    with caplog.at_level(logging.DEBUG, logger=authored_illustrations.__name__):
        out = _run(apply_authored_illustration(html, style, _Service(), 0))

    assert "<img" not in out
    _assert_no_path(caplog, generated)
