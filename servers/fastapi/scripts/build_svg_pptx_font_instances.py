"""Build the static font instances SVG PPTX font embedding loads read-only.

PPTX font embedding instantiates the bundled variable Noto Sans KR at the
slot's weight (400 or 700) before subsetting it. That takes 8-24 s per export,
because every export runs in a fresh isolated worker with no writable cache
root. This script does the instancing once, at image build time (``Dockerfile``,
``fastapi-builder`` stage), and writes the result to
``services.svg_pptx_font_subset.PREBUILT_INSTANCE_DIRECTORY``.

It runs the same ``instantiate_static_face`` the export would run, on the
digest-checked source, and writes an instance only when its digest equals the
one pinned in ``PREBUILT_INSTANCE_DIGESTS``; anything else is an error, so the
exported bytes cannot drift with a dependency change. An instance that already
exists with the pinned digest is kept. A local checkout may run it too:

    cd servers/fastapi && uv run python scripts/build_svg_pptx_font_instances.py
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tempfile

FASTAPI_ROOT = Path(__file__).resolve().parents[1]
if str(FASTAPI_ROOT) not in sys.path:
    sys.path.insert(0, str(FASTAPI_ROOT))

from services import svg_pptx_font_subset  # noqa: E402
from utils.hashing import sha256_prefixed  # noqa: E402
from utils.svg_template_fonts import FONT_DIGEST, FONT_PATH  # noqa: E402


class FontInstanceBuildError(RuntimeError):
    pass


def _variable_sources() -> dict[str, bytes]:
    try:
        payload = FONT_PATH.read_bytes()
    except OSError as error:
        raise FontInstanceBuildError(f"variable font source unavailable: {FONT_PATH}") from error
    if sha256_prefixed(payload) != FONT_DIGEST:
        raise FontInstanceBuildError(f"variable font source digest mismatch: {FONT_PATH}")
    return {FONT_DIGEST: payload}


def _write_atomically(target: Path, payload: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def build(directory: Path | None = None) -> list[Path]:
    """Write every pinned instance into ``directory`` (default: the load directory)."""

    sources = _variable_sources()
    written = []
    for (source_digest, weight), expected in sorted(svg_pptx_font_subset.PREBUILT_INSTANCE_DIGESTS.items()):
        payload = sources.get(source_digest)
        if payload is None:
            raise FontInstanceBuildError(f"no bundled variable source for pinned {source_digest}")
        target = svg_pptx_font_subset.prebuilt_instance_path(source_digest, weight, directory)
        try:
            if sha256_prefixed(target.read_bytes()) == expected:
                written.append(target)
                continue
        except FileNotFoundError:
            pass
        instance = svg_pptx_font_subset.instantiate_static_face(payload, weight)
        actual = sha256_prefixed(instance)
        if actual != expected:
            raise FontInstanceBuildError(
                f"instance {source_digest}@{weight} is {actual}, pinned {expected}; "
                "a fontTools change alters exported PPTX bytes and needs a reviewed pin update"
            )
        _write_atomically(target, instance)
        written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="directory to write into (default: the directory exports load from)")
    arguments = parser.parse_args(argv)
    for path in build(arguments.output_dir):
        print(f"{sha256_prefixed(path.read_bytes())}  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
