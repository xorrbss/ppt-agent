"""Pillow-based rendering for the authored-run contact sheet: the composite
PNG grid image(s) and the companion, zoomable ``index.html`` page.

No metric arithmetic and no layout-size decisions here -- those live in
``authored_run_contact_sheet_grid.py`` (row heights, page splitting) and
``authored_run_contact_sheet_metrics.py`` (the numbers themselves). This
module only draws pixels and HTML strings from decisions already made.
"""

from __future__ import annotations

import html as html_lib
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from scripts.authored_run_contact_sheet_grid import scale_to_width
from scripts.authored_run_contact_sheet_metrics import RunSummary


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        # Older Pillow: load_default() takes no size argument.
        return ImageFont.load_default()


def render_contact_sheet_pages(
    *,
    out_dir: Path,
    columns: list[Any],
    row_heights: list[int],
    pages: list[tuple[int, int]],
    thumb_width: int,
    header_height: int,
    index_col_width: int,
    cell_padding: int,
) -> list[Path]:
    """Draw one composite grid PNG per page in ``pages`` and return the
    written file paths, in page order."""
    col_width = thumb_width + cell_padding
    total_width = index_col_width + col_width * len(columns)
    label_font = _font(16)
    multi_page = len(pages) > 1
    written: list[Path] = []

    for page_num, (start, end) in enumerate(pages, start=1):
        page_height = header_height + sum(row_heights[start:end])
        image = Image.new("RGB", (max(total_width, 1), max(page_height, 1)), "white")
        draw = ImageDraw.Draw(image)

        for c, col in enumerate(columns):
            x = index_col_width + c * col_width
            draw.rectangle([x, 0, x + col_width, header_height], outline="black")
            draw.text((x + 4, 4), col.label, fill="black", font=label_font)

        y = header_height
        for row in range(start, end):
            draw.text((4, y + 4), f"s{row:02d}", fill="black", font=label_font)
            row_height = row_heights[row]
            for c, col in enumerate(columns):
                x = index_col_width + c * col_width
                asset = col.slides.get(row)
                if asset is not None and asset.image_path is not None:
                    with Image.open(asset.image_path) as im:
                        w, h = im.size
                        scaled_h = scale_to_width(w, h, thumb_width)
                        thumb = im.convert("RGB").resize((thumb_width, scaled_h))
                    image.paste(thumb, (x, y))
                else:
                    box_h = row_height - cell_padding
                    draw.rectangle([x, y, x + thumb_width, y + box_h], outline="lightgray")
                    draw.text((x + 4, y + 4), "(missing)", fill="lightgray", font=label_font)
            y += row_height

        suffix = f"-{page_num}" if multi_page else ""
        path = out_dir / f"contact-sheet{suffix}.png"
        image.save(path)
        written.append(path)

    return written


def _summary_table_html(runs: list[RunSummary]) -> str:
    header = (
        "<tr><th>run</th><th>slides</th><th>owners/slide (median)</th>"
        "<th>chars/slide (median)</th><th>min font px</th><th>image count</th>"
        "<th>slides w/ clipped</th></tr>"
    )
    rows = []
    for r in runs:
        rows.append(
            "<tr>"
            f"<td>{html_lib.escape(r.label)}</td><td>{r.slide_count}</td>"
            f"<td>{r.median_text_owners if r.median_text_owners is not None else '-'}</td>"
            f"<td>{r.median_visible_chars if r.median_visible_chars is not None else '-'}</td>"
            f"<td>{r.min_font_px if r.min_font_px is not None else '-'}</td>"
            f"<td>{r.image_count}</td><td>{r.slides_with_clipped}</td>"
            "</tr>"
        )
    return f'<table class="summary">{header}{"".join(rows)}</table>'


def render_index_html(
    *,
    columns: list[Any],
    num_rows: int,
    out_dir: Path,
    run_summaries: list[RunSummary],
) -> str:
    """Build the zoomable HTML grid page: one <img> per present slide,
    referenced by a path relative to ``out_dir`` (the images were already
    copied under ``out_dir/images/...`` by the caller)."""
    header_cells = "".join(f"<th>{html_lib.escape(col.label)}</th>" for col in columns)

    rows_html = []
    for row in range(num_rows):
        cells = []
        for col in columns:
            asset = col.slides.get(row)
            if asset is not None and asset.image_path is not None:
                rel = asset.image_path.relative_to(out_dir).as_posix()
                m = asset.metrics
                caption = (
                    f"chars={m.visible_chars} owners={m.text_owners} clip={m.clipped_count}"
                    if not m.missing
                    else "(no metrics)"
                )
                cells.append(
                    f'<td><a href="{html_lib.escape(rel)}" target="_blank">'
                    f'<img src="{html_lib.escape(rel)}" loading="lazy"></a>'
                    f'<div class="cap">{html_lib.escape(caption)}</div></td>'
                )
            else:
                cells.append('<td class="gap">(missing)</td>')
        rows_html.append(f"<tr><th>s{row:02d}</th>{''.join(cells)}</tr>")

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Authored run contact sheet</title>
<style>
body {{ font-family: sans-serif; margin: 16px; }}
table.summary {{ border-collapse: collapse; margin-bottom: 20px; }}
table.summary td, table.summary th {{ border: 1px solid #ccc; padding: 4px 8px; }}
table.grid {{ border-collapse: collapse; }}
table.grid td, table.grid th {{ border: 1px solid #ccc; padding: 4px; vertical-align: top; }}
table.grid img {{ width: 320px; display: block; }}
td.gap {{ color: #999; text-align: center; }}
.cap {{ font-size: 11px; color: #555; }}
</style>
</head>
<body>
<h1>Authored run contact sheet</h1>
{_summary_table_html(run_summaries)}
<table class="grid">
<tr><th></th>{header_cells}</tr>
{''.join(rows_html)}
</table>
</body>
</html>
"""
