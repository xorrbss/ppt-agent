# ruff: noqa: E402
"""A/B: per-line text boxes (A) vs paragraph merging with relative line positions (C)."""
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image
from pptx import Presentation

SVG_DIR, OUT = Path(sys.argv[1]), Path(sys.argv[2])
W, H = int(sys.argv[3]), int(sys.argv[4])
VARIANTS = sys.argv[5].split(",") if len(sys.argv) > 5 else ["A", "B", "C"]
OUT.mkdir(parents=True, exist_ok=True)

from services.document_canvas import document_canvas_for_orientation
from models.document_canvas import DocumentCanvasMetadata, LogicalSize, PhysicalSize, SafeArea
sys.path.insert(0, str(Path(__file__).resolve().parent))
from relativize import relativize_line_positions
from workers.public_svg_pptx_convert import VENDOR_ROOT, convert_pptx

canvas = DocumentCanvasMetadata(
    contract_version=document_canvas_for_orientation("landscape").contract_version,
    logical_size=LogicalSize(width=W, height=H), physical_size=PhysicalSize(width_emu=W * 9000, height_emu=H * 9000),
    safe_area=SafeArea(x=60, y=60, width=W - 120, height=H - 120))

sys.path.insert(0, str(VENDOR_ROOT))
import svg_to_pptx  # noqa: E402

ORIGINAL = svg_to_pptx.create_pptx_with_native_svg
ET.register_namespace("", "http://www.w3.org/2000/svg")
svgs = sorted(SVG_DIR.glob("*.svg"))


def convert(variant):
    work = OUT / f"work-{variant}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    paths = []
    for svg in svgs:
        target = work / svg.name
        root = ET.fromstring(svg.read_text(encoding="utf-8"))
        if variant == "C":
            relativize_line_positions(root)
        target.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")
        paths.append(target)
    merge = variant in {"B", "C"}

    def patched(*args, **kwargs):
        kwargs["merge_paragraphs"] = merge
        return ORIGINAL(*args, **kwargs)

    svg_to_pptx.create_pptx_with_native_svg = patched
    try:
        pptx = OUT / f"{variant}.pptx"
        convert_pptx(paths, pptx, [""] * len(paths), canvas)
    finally:
        svg_to_pptx.create_pptx_with_native_svg = ORIGINAL
    return pptx


def render_pptx(pptx, variant):
    d = OUT / f"render-{variant}"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir()
    subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(d), str(pptx)],
                   check=True, capture_output=True, timeout=300)
    subprocess.run(["pdftoppm", "-png", "-scale-to-x", str(W), "-scale-to-y", str(H), str(d / f"{variant}.pdf"),
                    str(d / "p")], check=True)
    return sorted(d.glob("p-*.png"))


def render_ref():
    d = OUT / "ref"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir()
    out = []
    for svg in svgs:
        png = d / f"{svg.stem}.png"
        html = d / f"{svg.stem}.html"
        html.write_text(f'<html><body style="margin:0">{svg.read_text(encoding="utf-8")}</body></html>')
        subprocess.run(["google-chrome", "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                        f"--window-size={W},{H}", f"--screenshot={png}", str(html)], check=True,
                       capture_output=True, timeout=120)
        out.append(png)
    return out


def gray(path):
    im = Image.open(path).convert("L")
    if im.size != (W, H):
        im = im.resize((W, H))
    return np.asarray(im, dtype=np.int16)


def blocks(svg):
    ns = "{http://www.w3.org/2000/svg}"
    root = ET.fromstring(svg.read_text(encoding="utf-8"))
    result = []
    for text in root.iter(ns + "text"):
        spans = list(text.iter(ns + "tspan"))
        size = float(text.get("font-size", 16))
        ys = [float(s.get("y")) for s in spans if s.get("y")] or [float(text.get("y"))]
        x = float(text.get("x", spans[0].get("x") if spans else 0))
        anchor = text.get("text-anchor", "start")
        content = "".join(text.itertext())[:18]
        x0, x1 = (x - 4, x + 700) if anchor == "start" else (x - 350, x + 350) if anchor == "middle" else (x - 700, x + 4)
        result.append(dict(text=content, lines=max(1, len(spans)), box=(max(0, int(x0)), int(min(ys) - size * 1.15),
                           min(W, int(x1)), int(max(ys) + size * 0.45))))
    return result


def ink_bbox(img, box):
    x0, y0, x1, y1 = box
    sub = img[max(0, y0):min(H, y1), x0:x1]
    mask = sub < 150
    if not mask.any():
        return None
    ys, xs = np.where(mask)
    return (x0 + int(xs.min()), max(0, y0) + int(ys.min()), x0 + int(xs.max()), max(0, y0) + int(ys.max()))


def frames(pptx):
    deck = Presentation(str(pptx))
    return [sum(1 for sh in slide.shapes if sh.has_text_frame and sh.text_frame.text.strip()) for slide in deck.slides]


ref = render_ref()
report = {}
for variant in VARIANTS:
    pptx = convert(variant)
    pages = render_pptx(pptx, variant)
    assert len(pages) == len(svgs), (len(pages), len(svgs))
    per_slide = []
    for svg, rpng, ppng in zip(svgs, ref, pages, strict=True):
        a, b = gray(rpng), gray(ppng)
        diff = np.abs(a - b)
        deltas = []
        for block in blocks(svg):
            ra, rb = ink_bbox(a, block["box"]), ink_bbox(b, block["box"])
            if ra is None or rb is None:
                deltas.append(dict(text=block["text"], lines=block["lines"], missing=ra is None or rb is None))
                continue
            deltas.append(dict(text=block["text"], lines=block["lines"],
                               dleft=rb[0] - ra[0], dtop=rb[1] - ra[1], dright=rb[2] - ra[2], dbottom=rb[3] - ra[3]))
        per_slide.append(dict(slide=svg.name, mad=float(diff.mean()), mismatch=float((diff > 64).mean()),
                              frames=frames(pptx)[len(per_slide)], blocks=deltas))
    report[variant] = per_slide
(OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
for variant, slides in report.items():
    print(variant, "frames", [s["frames"] for s in slides], "MAD", [round(s["mad"], 3) for s in slides],
          "mismatch%", [round(s["mismatch"] * 100, 3) for s in slides])
