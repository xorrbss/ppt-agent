"""Code-identity digests hash each source file once per process, with the same recipe."""
from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

import pytest

from utils import code_file_digest


@pytest.fixture
def counted_reads(monkeypatch):
    reads = Counter()
    read_bytes = Path.read_bytes

    def counting(path):
        reads[Path(path).resolve()] += 1
        return read_bytes(path)

    code_file_digest.clear_code_file_digests()
    monkeypatch.setattr(Path, "read_bytes", counting)
    yield reads
    code_file_digest.clear_code_file_digests()


def test_partial_recovery_code_digests_read_each_file_once(counted_reads):
    from services import svg_partial_recovery as recovery

    root = Path(recovery.__file__).resolve().parents[1]
    first = (recovery._code_digest(), recovery._page_render_code_digest(), recovery._base_code_digest())
    for _ in range(3):
        assert (recovery._code_digest(), recovery._page_render_code_digest(),
                recovery._base_code_digest()) == first

    code_paths = {(root / name).resolve() for name in recovery.CODE_FILES}
    assert code_paths <= set(counted_reads)
    assert all(counted_reads[path] == 1 for path in code_paths)
    assert max(counted_reads.values()) == 1

    # The cache keeps the published recipe: bare hex SHA-256 of the exact bytes.
    expected = recovery.digest([{"path": name, "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest()}
                                for name in recovery.CODE_FILES])
    assert first[2] == expected


def test_template_materializer_digest_reads_its_source_once(counted_reads):
    from services.svg_templates.frame_fonts import bytes_digest
    from services.svg_templates import frame_glyphs

    source = Path(frame_glyphs.__file__).resolve().parents[2] / "utils" / "svg_template_glyph_materialization.py"
    digests = {frame_glyphs.materializer_code_digest() for _ in range(5)}
    assert digests == {bytes_digest(source.read_bytes())}
    assert counted_reads[source.resolve()] == 2  # one cached read, one by the assertion itself
