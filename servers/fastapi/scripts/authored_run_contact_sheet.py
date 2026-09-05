"""Turn a set of authored-run artifact folders into one side-by-side visual
comparison the coordinator can judge at a glance, plus a metrics table.

Each ``--run`` points at a directory of ``sNN.png`` slide renders and
(optionally) matching ``sNN.metrics.json`` browser-metrics dumps, e.g.
``artifacts/authored-fixture-run7-20260905/slides``. Runs may have different
slide counts or missing metrics files -- both are handled by leaving a gap
rather than failing. An optional ``--reference`` directory of PNGs (in name
order; no ``sNN`` naming assumed) is added as a final column, e.g. an August
baseline render set.

Outputs, written under ``--out``:

* ``contact-sheet.png`` (or ``contact-sheet-1.png``, ``-2.png``, ... if the
  full grid would be taller than ``--max-height`` pixels): one column per
  run, one row per slide index, thumbnails ~``--thumb-width`` px wide, with
  row/column labels drawn on the image.
* ``metrics.md``: a per-run summary table (slides, median text owners/slide,
  median visible chars/slide, min font px seen, image count, slides with any
  clipped element) plus a per-slide detail table per run.
* ``index.html``: the same grid, but as real ``<img>`` tags referencing
  copies of the full-resolution PNGs under ``out/images/<label>/`` (so the
  coordinator can click through and zoom), plus the same metrics table.

See ``authored_run_contact_sheet_discovery.py`` (filesystem -> slide index
mapping), ``authored_run_contact_sheet_metrics.py`` (metrics extraction and
markdown tables), ``authored_run_contact_sheet_grid.py`` (pure row-height /
page-splitting arithmetic) and ``authored_run_contact_sheet_render.py`` (the
Pillow drawing and the HTML page) for the implementation. This module is
orchestration only.

Example:

    cd servers/fastapi
    uv run python scripts/authored_run_contact_sheet.py \\
      --run ../../artifacts/authored-fixture-run3-20260905/slides \\
      --run ../../artifacts/authored-fixture-run7-20260905/slides \\
      --reference ../../artifacts/august-reference/slides \\
      --out ../../artifacts/contact-sheet-demo
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

FASTAPI_ROOT = Path(__file__).resolve().parents[1]
if str(FASTAPI_ROOT) not in sys.path:
    sys.path.insert(0, str(FASTAPI_ROOT))

from PIL import Image  # noqa: E402

from scripts.authored_run_contact_sheet_discovery import (  # noqa: E402
    derive_run_label,
    discover_reference_dir,
    discover_run_dir,
    load_metrics_json,
)
from scripts.authored_run_contact_sheet_grid import (  # noqa: E402
    compute_row_height,
    plan_pages,
    scale_to_width,
)
from scripts.authored_run_contact_sheet_metrics import (  # noqa: E402
    SlideMetricsSummary,
    extract_slide_metrics,
    render_metrics_markdown,
    summarize_run,
)
from scripts.authored_run_contact_sheet_render import (  # noqa: E402
    render_contact_sheet_pages,
    render_index_html,
)

THUMB_WIDTH = 320
MAX_SHEET_HEIGHT = 16000
LABEL_ROW_HEIGHT = 24
HEADER_PADDING = 8
INDEX_COL_WIDTH = 48
CELL_PADDING = 8


@dataclass
class SlideAsset:
    image_path: Path | None
    size: tuple[int, int] | None
    metrics: SlideMetricsSummary = field(default_factory=SlideMetricsSummary)


@dataclass
class RunColumn:
    label: str
    slides: dict[int, SlideAsset]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--run", action="append", default=[], dest="runs", required=True,
        help="Directory of sNN.png (+ optional sNN.metrics.json); repeatable.",
    )
    parser.add_argument("--reference", default=None, help="Directory of reference PNGs.")
    parser.add_argument("--out", required=True)
    parser.add_argument("--thumb-width", type=int, default=THUMB_WIDTH)
    parser.add_argument("--max-height", type=int, default=MAX_SHEET_HEIGHT)
    return parser.parse_args(argv)


def _copy_and_measure(src: Path, dest: Path) -> tuple[Path, tuple[int, int]]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    with Image.open(dest) as im:
        size = im.size
    return dest, size


def build_run_column(run_dir: Path, images_root: Path) -> RunColumn:
    label = derive_run_label(run_dir)
    entries = discover_run_dir(run_dir)
    slides: dict[int, SlideAsset] = {}
    for idx, (png_path, metrics_path) in entries.items():
        summary = extract_slide_metrics(load_metrics_json(metrics_path))
        image_path: Path | None = None
        size: tuple[int, int] | None = None
        if png_path is not None:
            image_path, size = _copy_and_measure(png_path, images_root / label / png_path.name)
        slides[idx] = SlideAsset(image_path=image_path, size=size, metrics=summary)
    return RunColumn(label=label, slides=slides)


def build_reference_column(reference_dir: Path, images_root: Path) -> RunColumn:
    label = "reference"
    slides: dict[int, SlideAsset] = {}
    for idx, png_path in discover_reference_dir(reference_dir).items():
        image_path, size = _copy_and_measure(png_path, images_root / label / png_path.name)
        slides[idx] = SlideAsset(image_path=image_path, size=size)
    return RunColumn(label=label, slides=slides)


def _row_heights(columns: list[RunColumn], num_rows: int, thumb_width: int, cell_padding: int) -> list[int]:
    placeholder_h = scale_to_width(16, 9, thumb_width)
    heights = []
    for row in range(num_rows):
        cell_heights = []
        for col in columns:
            asset = col.slides.get(row)
            if asset is not None and asset.size is not None:
                w, h = asset.size
                cell_heights.append(scale_to_width(w, h, thumb_width))
            else:
                cell_heights.append(None)
        heights.append(
            compute_row_height(cell_heights, placeholder_height=placeholder_h, label_padding=cell_padding)
        )
    return heights


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out_dir = Path(args.out)
    images_root = out_dir / "images"
    out_dir.mkdir(parents=True, exist_ok=True)

    columns = [build_run_column(Path(run), images_root) for run in args.runs]
    if args.reference:
        columns.append(build_reference_column(Path(args.reference), images_root))
    if not columns:
        raise SystemExit("at least one --run is required")

    num_rows = 0
    for col in columns:
        if col.slides:
            num_rows = max(num_rows, max(col.slides.keys()) + 1)

    row_heights = _row_heights(columns, num_rows, args.thumb_width, CELL_PADDING)
    header_height = LABEL_ROW_HEIGHT + HEADER_PADDING
    pages = plan_pages(row_heights, header_height=header_height, max_height=args.max_height)

    sheet_paths = render_contact_sheet_pages(
        out_dir=out_dir,
        columns=columns,
        row_heights=row_heights,
        pages=pages,
        thumb_width=args.thumb_width,
        header_height=header_height,
        index_col_width=INDEX_COL_WIDTH,
        cell_padding=CELL_PADDING,
    )

    run_summaries = [
        summarize_run(col.label, {i: a.metrics for i, a in col.slides.items()}) for col in columns
    ]
    per_slide_by_label = {
        col.label: {i: a.metrics for i, a in col.slides.items()} for col in columns
    }
    metrics_md = render_metrics_markdown(run_summaries, per_slide_by_label, num_rows)
    (out_dir / "metrics.md").write_text(metrics_md, encoding="utf-8")

    html = render_index_html(
        columns=columns, num_rows=num_rows, out_dir=out_dir, run_summaries=run_summaries
    )
    (out_dir / "index.html").write_text(html, encoding="utf-8")

    print(f"contact sheet(s): {', '.join(str(p) for p in sheet_paths)}")
    print(f"metrics: {out_dir / 'metrics.md'}")
    print(f"index: {out_dir / 'index.html'}")
    print()
    print(metrics_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
