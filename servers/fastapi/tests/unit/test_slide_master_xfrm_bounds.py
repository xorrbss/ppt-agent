"""Axis-aligned shapes trace exactly the frame their xfrm declares (xfrm-bounds.1)."""

from __future__ import annotations

import random
import subprocess
import sys
from pathlib import Path

import pytest

from workers.public_svg_export_worker import VENDOR_ROOT


@pytest.fixture
def converter():
    sys.path.insert(0, str(VENDOR_ROOT))
    try:
        from svg_to_pptx.conversion_context import _ACTIVE_CONTEXT, ConversionContext
        from svg_to_pptx.drawingml.utils import rect_to_dml_xfrm

        # 1280x720 px on a 16:9 canvas: 9525 EMU per px, the deck canvas icons are placed on.
        token = _ACTIVE_CONTEXT.set(ConversionContext(1280, 720, 12192000, 6858000, legacy=False))
        try:
            yield rect_to_dml_xfrm
        finally:
            _ACTIVE_CONTEXT.reset(token)
    finally:
        sys.path.remove(str(VENDOR_ROOT))


def _translate(tx, ty):
    return (1.0, 0.0, 0.0, 1.0, tx, ty)


def test_circle_in_translate_group_traces_its_xfrm_frame(converter):
    # A circle box (cx - r, cy - r, 2r, 2r) inside a translate group, as a flattened icon draws
    # it. Before xfrm-bounds.1 the bottom edge traced as px_to_emu(y + h) = 3943622 while the
    # xfrm declared off_y + ext_cy = 3943623; the OOXML verifier rejects that 1 EMU difference
    # (svg_native_ooxml_trace_geometry_mismatch) and the whole export fails.
    attr, off_x, off_y, ext_cx, ext_cy, bounds = converter(
        8.624155449994365, 13.614305832081032, 0.9304517210534257, 10.17121704954157,
        _translate(865.2281620709953, 390.24307454440043),
    )
    assert attr == ""
    assert off_y + ext_cy == 3943623
    assert bounds == (off_x, off_y, off_x + ext_cx, off_y + ext_cy)


def test_every_axis_aligned_placement_traces_its_xfrm_frame(converter):
    generator = random.Random(20261102)
    for _ in range(4000):
        x, y = generator.uniform(0, 24), generator.uniform(0, 24)
        w, h = generator.uniform(0.1, 12), generator.uniform(0.1, 12)
        matrix = _translate(generator.uniform(0, 1200), generator.uniform(0, 650))
        attr, off_x, off_y, ext_cx, ext_cy, bounds = converter(x, y, w, h, matrix)
        assert attr == ""
        assert bounds == (off_x, off_y, off_x + ext_cx, off_y + ext_cy), (x, y, w, h, matrix)


def test_scaled_axis_aligned_placement_traces_its_xfrm_frame(converter):
    generator = random.Random(7)
    for _ in range(2000):
        scale = generator.uniform(0.4, 3.0)
        matrix = (scale, 0.0, 0.0, scale, generator.uniform(0, 900), generator.uniform(0, 500))
        _attr, off_x, off_y, ext_cx, ext_cy, bounds = converter(
            generator.uniform(0, 100), generator.uniform(0, 100),
            generator.uniform(1, 80), generator.uniform(1, 80), matrix,
        )
        assert bounds == (off_x, off_y, off_x + ext_cx, off_y + ext_cy)


def test_rotated_shape_keeps_its_visual_bounds(converter):
    # 90 degree rotation about the origin: the rotated visual box is not the xfrm frame, and
    # that unrotated-frame/visual-box distinction is unchanged by xfrm-bounds.1.
    attr, off_x, off_y, ext_cx, ext_cy, bounds = converter(10.0, 20.0, 30.0, 10.0, (0.0, 1.0, -1.0, 0.0, 100.0, 100.0))
    assert attr.strip().startswith("rot=")
    assert bounds != (off_x, off_y, off_x + ext_cx, off_y + ext_cy)
    assert bounds[2] - bounds[0] == ext_cy and bounds[3] - bounds[1] == ext_cx


def test_patch_reproduces_shipped_utils_bytes(tmp_path):
    root = Path(__file__).resolve().parents[4]
    patch = root / "compatibility/slide-master/patches/xfrm-bounds-1.patch"
    utils = VENDOR_ROOT / "svg_to_pptx/drawingml/utils.py"
    scratch = tmp_path / "svg_to_pptx/drawingml"
    scratch.mkdir(parents=True)
    target = scratch / "utils.py"
    target.write_bytes(utils.read_bytes())
    git = ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "apply", "--no-index", "-p5"]
    subprocess.run([*git, "--reverse", str(patch)], cwd=tmp_path, check=True)
    assert target.read_bytes() != utils.read_bytes()
    subprocess.run([*git, str(patch)], cwd=tmp_path, check=True)
    assert target.read_bytes() == utils.read_bytes()
