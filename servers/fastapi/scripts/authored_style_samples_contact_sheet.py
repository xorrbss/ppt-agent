"""Contact-sheet HTML for reviewing a sample-generation run (plan §2).

One row per style, 12 thumbnails each, failed styles listed first so a
reviewer sees problems without scrolling.
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence


@dataclass(frozen=True)
class ContactSheetRow:
    style_id: str
    ok: bool
    presentation_id: Optional[str] = None
    #: Relative (or absolute, for local review) paths/URLs to the 12 thumbnails.
    thumbnails: Sequence[str] = field(default_factory=tuple)
    problems: Sequence[str] = field(default_factory=tuple)


def render_contact_sheet(rows: Sequence[ContactSheetRow]) -> str:
    failed = [row for row in rows if not row.ok]
    passed = [row for row in rows if row.ok]
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        "<title>Authored style samples - contact sheet</title>",
        "<style>",
        "body{font-family:sans-serif;margin:16px}",
        "table{border-collapse:collapse;width:100%}",
        "td,th{border:1px solid #ccc;padding:6px;vertical-align:top;text-align:left}",
        "img{width:120px;height:auto;margin:2px;border:1px solid #ddd}",
        ".fail{background:#fee}",
        "</style></head><body>",
        "<h1>Authored style samples - contact sheet</h1>",
        f"<p>{len(passed)} passed, {len(failed)} failed.</p>",
        "<table>",
        "<tr><th>Style</th><th>Presentation</th><th>Slides / problems</th></tr>",
    ]
    for row in [*failed, *passed]:
        css_class = "" if row.ok else ' class="fail"'
        parts.append(f"<tr{css_class}>")
        parts.append(f"<td>{html.escape(row.style_id)}</td>")
        parts.append(f"<td>{html.escape(row.presentation_id or '')}</td>")
        if row.ok:
            thumbs = "".join(
                f'<img src="{html.escape(src)}" alt="{i + 1}">' for i, src in enumerate(row.thumbnails)
            )
            parts.append(f"<td>{thumbs}</td>")
        else:
            problems = "<br>".join(html.escape(problem) for problem in row.problems)
            parts.append(f"<td>{problems}</td>")
        parts.append("</tr>")
    parts.append("</table></body></html>")
    return "\n".join(parts)


def write_contact_sheet(path: Path, rows: Sequence[ContactSheetRow]) -> None:
    path.write_text(render_contact_sheet(rows), encoding="utf-8", newline="\n")
