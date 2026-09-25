"""3.1 — is emphasis expressible by whole-block tokens, or are character ranges needed?

The design's question is narrow: not whether a source block is split across
elements (run tokens already express that), but whether a **styled span sits
inside one projected run**, which no run-level token can address.

A DOM element bound to source text carries ``data-source-block-id``, whose value
is either a plain block id (whole block) or ``<block>:run:<start>[-<end>]``
(``services/source_content/source_runs.py``). Within such an element, an
emphasis element that covers only *part* of the element's text is the case that
would need ``{{q:<id>@<start>-<end>}}``; one that covers all of it is already
expressible by wrapping the whole token (design 2.1).

Read-only: published ``slides.html_content`` only. No generation, no provider
call, no database write.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

DB = Path(__file__).resolve().parent / "dburl.txt"
RUNS = ("run-11", "run-15", "run-16", "run-20", "run-21")

#: Tags whose whole purpose is emphasis.
EMPHASIS_TAGS = frozenset({"strong", "b", "em", "i", "mark", "u", "ins", "del", "s"})
#: Inline declarations that make a span read as emphasis against its surroundings.
_WEIGHT = re.compile(r"font-weight\s*:\s*(bold|[6-9]\d\d)", re.I)
_ITALIC = re.compile(r"font-style\s*:\s*italic", re.I)
_COLOR = re.compile(r"(?<!-)\bcolor\s*:", re.I)
_DECOR = re.compile(r"text-decoration[^;]*:\s*(?!none)", re.I)
_RUN_ID = re.compile(r"^(?P<block>.+):run:(?P<start>\d+)(?:-(?P<end>\d+))?$")


def parse_binding(value: str) -> tuple[str, int | None, int | None]:
    """Mirror ``parse_source_binding_id`` without importing the service."""

    raw = (value or "").strip()
    match = _RUN_ID.match(raw)
    if match is None:
        return raw, None, None
    start = int(match.group("start"))
    end = int(match.group("end")) if match.group("end") is not None else start
    return match.group("block"), start, end


def _is_emphasis(tag: str, attrs: dict[str, str]) -> list[str]:
    """Why this element reads as emphasis, if it does."""

    why: list[str] = []
    if tag in EMPHASIS_TAGS:
        why.append(f"tag:{tag}")
    style = attrs.get("style") or ""
    if _WEIGHT.search(style):
        why.append("font-weight")
    if _ITALIC.search(style):
        why.append("font-style")
    if _COLOR.search(style):
        why.append("color")
    if _DECOR.search(style):
        why.append("text-decoration")
    return why


class BoundElementScanner(HTMLParser):
    """Collect, for each source-bound element, its text and its emphasis spans."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[dict[str, Any]] = []
        self.bound: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: (value or "") for name, value in attrs}
        frame: dict[str, Any] = {"tag": tag, "text": [], "emphasis": []}
        binding = attributes.get("data-source-block-id")
        if binding:
            frame["binding"] = binding
        why = _is_emphasis(tag, attributes)
        if why:
            frame["emphasis_self"] = why
        self.stack.append(frame)
        if tag in {"br", "img", "hr", "input", "meta", "link", "source", "area", "col"}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for position in range(len(self.stack) - 1, -1, -1):
            if self.stack[position]["tag"] == tag:
                break
        else:
            return
        closed = self.stack[position]
        del self.stack[position:]
        text = "".join(closed["text"])
        if closed.get("emphasis_self") and text.strip():
            closed_span = {"why": closed["emphasis_self"], "text": text}
            if self.stack:
                self.stack[-1]["emphasis"].append(closed_span)
        for span in closed.get("emphasis", []):
            if self.stack:
                self.stack[-1]["emphasis"].append(span)
        if self.stack:
            self.stack[-1]["text"].append(text)
        if closed.get("binding"):
            self.bound.append(
                {
                    "binding": closed["binding"],
                    "tag": closed["tag"],
                    "text": text,
                    "emphasis": closed.get("emphasis", []),
                    "self_emphasis": closed.get("emphasis_self") or [],
                }
            )

    def handle_data(self, data: str) -> None:
        if self.stack:
            self.stack[-1]["text"].append(data)

    def close(self) -> None:  # flush any unclosed elements
        super().close()
        while self.stack:
            self.handle_endtag(self.stack[-1]["tag"])


def _squash(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def scan_slide(html: str) -> list[dict[str, Any]]:
    scanner = BoundElementScanner()
    scanner.feed(html)
    scanner.close()
    result = []
    for element in scanner.bound:
        block, start, end = parse_binding(element["binding"])
        whole_text = _squash(element["text"])
        partial, covering = [], []
        for span in element["emphasis"]:
            span_text = _squash(span["text"])
            if not span_text:
                continue
            (covering if span_text == whole_text else partial).append(
                {"why": span["why"], "chars": len(span_text)}
            )
        result.append(
            {
                "block": block,
                "run_start": start,
                "run_end": end,
                "is_run_binding": start is not None,
                "tag": element["tag"],
                "chars": len(whole_text),
                "partial_emphasis": partial,
                "covering_emphasis": covering,
            }
        )
    return result


def main(out_path: str) -> int:
    import psycopg

    url = re.sub(r"^postgresql\+\w+://", "postgresql://", DB.read_text().strip())
    base = Path("C:/project/PPT-agent/ppt-agent/artifacts/authored-full-product-validation-20260910")

    def walk(obj: Any, key: str, acc: list | None = None) -> list:
        acc = [] if acc is None else acc
        if isinstance(obj, dict):
            for name, value in obj.items():
                if name == key:
                    acc.append(value)
                walk(value, key, acc)
        elif isinstance(obj, list):
            for value in obj:
                walk(value, key, acc)
        return acc

    rows: list[dict[str, Any]] = []
    with psycopg.connect(url, connect_timeout=15, options="-c default_transaction_read_only=on") as c:
        c.execute("set statement_timeout='60s'")
        for run in RUNS:
            status = json.loads((base / run / "06-final-status.json").read_text(encoding="utf-8-sig"))
            pid = (walk(status, "presentation_id") or walk(status, "presentationId") or [None])[0]
            if not pid:
                print(f"{run}: no presentation id", flush=True)
                continue
            slides = c.execute(
                "select index, html_content from slides where presentation = %s order by index",
                (pid,),
            ).fetchall()
            for index, html in slides:
                if not html:
                    continue
                for element in scan_slide(html):
                    rows.append({"run": run, "slide": int(index), **element})
            print(f"{run}: {len(slides)} slides scanned ({pid})", flush=True)

    Path(out_path).write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")

    blocks = {(row["run"], row["block"]) for row in rows}
    run_bound = {(row["run"], row["block"]) for row in rows if row["is_run_binding"]}
    elements_per_block: Counter[tuple[str, str]] = Counter(
        (row["run"], row["block"]) for row in rows
    )
    split = {key for key, count in elements_per_block.items() if count > 1}
    partial = [row for row in rows if row["partial_emphasis"]]
    partial_in_run = [row for row in partial if row["is_run_binding"]]
    covering = [row for row in rows if row["covering_emphasis"] and not row["partial_emphasis"]]
    why = Counter(
        reason
        for row in partial
        for span in row["partial_emphasis"]
        for reason in span["why"]
    )

    print(f"\n=== 3.1 over {len(RUNS)} published decks ===")
    print(f"source-bound elements: {len(rows)}")
    print(f"distinct bound blocks: {len(blocks)}")
    print(f"blocks bound as runs (split across elements by the binder): {len(run_bound)}")
    print(f"blocks carried by more than one bound element: {len(split)}")
    print(f"\nelements with emphasis covering ALL of their text: {len(covering)}")
    print(f"  -> already expressible by wrapping a whole-block/run token")
    print(f"elements with emphasis covering only PART of their text: {len(partial)}")
    print(f"  of those, inside a run binding: {len(partial_in_run)}")
    print(f"  reasons: {dict(why)}")
    if partial:
        print("  examples:")
        for row in partial[:6]:
            span = "" if row["run_start"] is None else f":run:{row['run_start']}"
            print(
                f"    {row['run']} s{row['slide']} {row['block']}{span}"
                f" tag={row['tag']} chars={row['chars']} spans={row['partial_emphasis']}"
            )
    verdict = (
        "character-range tokens ARE needed"
        if partial
        else "character-range tokens are NOT needed"
    )
    print(f"\nVERDICT: {verdict}")
    print("wrote", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "measure-3-1.json"))
