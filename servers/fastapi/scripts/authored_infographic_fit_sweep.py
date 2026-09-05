"""Offline fit sweep for the authored infographic component library.

Renders every archetype at 3..6 nodes, at every density and weight, inside a
fixed-size box on a 1280x720 canvas, and reports whether any library element
leaves the figure root's box or the canvas, and how much of the box it fills.

The document mirrors what generation ships: the skeleton in a fixed-size shell,
the library stylesheet injected as ``finalize_infographic_slide_html`` injects
it, and the same 15px font floor the pipeline clamps the document with before
the first Chromium render -- without that clamp the sweep would measure a
stylesheet no browser ever sees.

``boxes`` renders a sample of real generated slides and reports the box sizes
the authoring model actually gives figures; ``sweep`` is the fit matrix, which
also reports how much of its box each figure fills (see
``authored_infographic_fit_fullness``). Both need ``APP_DATA_DIRECTORY`` and
``USER_CONFIG_PATH``.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Mapping, NamedTuple, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models.authored_infographic_plan import (  # noqa: E402
    ARCHETYPES,
    AuthoredSlideInfographic,
    InfographicNode,
)
from utils.authored_font_policy import enforce_authored_font_floor  # noqa: E402
from utils.authored_infographic import library_css, render_skeleton  # noqa: E402
from utils.authored_infographic.skeleton_parts import figure_qid  # noqa: E402

from scripts import authored_infographic_fit_fullness as fullness  # noqa: E402

CANVAS_W, CANVAS_H = 1280, 720
FONT_FLOOR_PX = 15.0
BRAND = SimpleNamespace(primary="#08916E", fonts="Noto Sans KR")
ROOT_QID_RE = re.compile(r"^ig\.s\d+\.root$")
#: Where a figure container is told to end; every sweep box ends here.
BOTTOM_CONTENT_LINE = 688

NODE_COUNTS, DENSITIES, WEIGHTS = (3, 4, 5, 6), ("low", "medium", "high"), ("standard", "hero")

#: Text at the plan's per-field budget, as (label, desc, figure, before, after).
NODE_TEXT = (
    ("치명·중대 결함관리", "운영 진입 기준을 객관화한 절차", "8 단계", "4.6%", "2.2%"),
    ("전량 모의이관 검증", "최종 리허설로 전환을 검증한다", "99.9%", "12건", "0건"),
    ("사용자 인수시험", "이관 수행 결과를 전량 대조한다", "72시간 전", "72시간", "4시간"),
    ("사후대사 정합성", "시험 유형별 합격 기준을 고정", "4.6%", "38점", "71점"),
    ("운영 전환 리허설", "전환 판정 회의체가 승인한다", "2 회 이상", "2.1초", "0.8초"),
    ("단위·통합 시험", "잔여 결함은 회귀 시험으로 확인", "0 건", "9개", "3개"),
)
TAKEAWAY = "운영 전환을 객관화"


class Box(NamedTuple):
    """One figure shell the sweep places the figure in."""

    name: str
    width: int
    height: int

    @property
    def left(self) -> int:
        return (CANVAS_W - self.width) // 2

    @property
    def top(self) -> int:
        # Every box ends on the bottom content line the contract asks a figure
        # to reach; fit is a function of the box size alone, so the anchor does
        # not change any previously recorded result.
        return BOTTOM_CONTENT_LINE - self.height


#: The shells the sweep renders in. The three tall ones are what the figure
#: contract now produces (430..560px, ending on the bottom content line) and are
#: where fullness is judged; the two short ones are the regression floor.
BOXES = (
    Box("wide_short", 1138, 386),
    Box("wide_mid", 1138, 430),
    Box("wide_tall", 1138, 500),
    Box("wide_taller", 1152, 560),
    Box("narrow_short", 800, 400),
)


def parse_box(text: str) -> Box:
    """``name:WxH`` or ``WxH`` from the command line."""
    name, _, size = text.rpartition(":")
    width, _, height = size.lower().partition("x")
    return Box(name or f"box_{size}", int(width), int(height))


def build_slide(archetype: str, count: int, density: str, weight: str) -> AuthoredSlideInfographic:
    """One planned figure at the plan's maximum text budget."""
    fields = ("label", "desc", "figure", "before", "after")
    nodes = tuple(
        InfographicNode(**dict(zip(fields, NODE_TEXT[i % len(NODE_TEXT)], strict=True)))
        for i in range(count)
    )
    return AuthoredSlideInfographic(
        index=13,
        archetype=archetype,  # type: ignore[arg-type]
        weight=weight,  # type: ignore[arg-type]
        takeaway=TAKEAWAY,
        nodes=nodes,
        emphasis=min(count - 1, 2),
        density=density,  # type: ignore[arg-type]
    )


_PAGE_CSS = (
    "*{box-sizing:border-box}"
    "html,body{width:1280px;height:720px;margin:0;overflow:hidden}"
    'body{position:relative;background:#F9F9F9;color:#1D1D1B;font-family:"Noto Sans KR",'
    '"Malgun Gothic",sans-serif;word-break:keep-all}'
    ".hdr{position:absolute;left:48px;top:16px;width:1000px;font-size:30px;line-height:38px;"
    "font-weight:700}"
    ".claim{position:absolute;left:48px;top:62px;width:1000px;font-size:28px;line-height:36px;"
    "font-weight:800}"
    # The shell's border box IS the figure root's box: no border, no padding.
    ".shell{position:absolute;background:#FFFFFF;border-radius:14px;box-shadow:inset 0 0 0 1px #DDE3E8}"
)


def sweep_document(figure_html: str, box: Box, css: str, *, font_floor: float) -> str:
    """A minimal slide holding one figure in a fixed-size shell.

    A scene's picture is an unresolved illustration slot and renderer admission
    refuses it; dropping the marker keeps the slot's box.
    """
    figure_html = figure_html.replace(' data-illust-prompt="SCENE"', "")
    html = (
        '<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">'
        f"<style>{_PAGE_CSS}</style><style>{css}</style></head><body>"
        '<div class="hdr">Ⅲ. 사업수행 방안 — 이관·시험·전환 방안</div>'
        '<div class="claim">리허설로 전환 검증하고 운영 진입을 객관화</div>'
        f'<div class="shell" style="left:{box.left}px;top:{box.top}px;'
        f'width:{box.width}px;height:{box.height}px">{figure_html}</div>'
        "</body></html>"
    )
    return enforce_authored_font_floor(html, minimum_px=font_floor) if font_floor else html


class Overshoot(NamedTuple):
    """How far one element leaves a rectangle, per side (0 when inside)."""

    left: float = 0.0
    top: float = 0.0
    right: float = 0.0
    bottom: float = 0.0

    @property
    def worst(self) -> float:
        return max(self)

    def as_text(self) -> str:
        over = [(n, v) for n, v in zip(self._fields, self, strict=True) if v > 0]
        return ", ".join(f"{name}+{value:.0f}" for name, value in over) or "-"


def overshoot(bbox: Mapping[str, float], rect: Sequence[float], tolerance: float = 0.5) -> Overshoot:
    """Per-side overflow of ``bbox`` out of ``rect`` (x, y, width, height)."""
    rx, ry, rw, rh = rect
    x, y, w, h = (float(bbox[k]) for k in ("x", "y", "width", "height"))
    sides = (rx - x, ry - y, (x + w) - (rx + rw), (y + h) - (ry + rh))
    return Overshoot(*(side if side > tolerance else 0.0 for side in sides))


@dataclass
class Finding:
    """The worst overflow of one rendered combination."""

    box_overflow: float = 0.0
    box_qid: str = ""
    box_detail: str = ""
    canvas_overflow: float = 0.0
    canvas_qid: str = ""
    clipped: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return bool(self.box_overflow or self.canvas_overflow or self.clipped)


def inspect(elements: Iterable[Mapping[str, Any]], qid_prefix: str, box: Box) -> Finding:
    """Measure every library element of one figure against its box and the canvas."""
    finding = Finding()
    box_rect = (box.left, box.top, box.width, box.height)
    canvas_rect = (0.0, 0.0, float(CANVAS_W), float(CANVAS_H))
    for element in elements:
        qid = str(element.get("qid") or "")
        if not qid.startswith(qid_prefix):
            continue
        bbox = element.get("bbox") or {}
        if not bbox or (bbox.get("width", 0) <= 0 and bbox.get("height", 0) <= 0):
            continue
        if not ROOT_QID_RE.match(qid):
            out = overshoot(bbox, box_rect)
            if out.worst > finding.box_overflow:
                finding.box_overflow, finding.box_qid = out.worst, qid
                finding.box_detail = out.as_text()
        out = overshoot(bbox, canvas_rect)
        if out.worst > finding.canvas_overflow:
            finding.canvas_overflow, finding.canvas_qid = out.worst, qid
        if element.get("clipped"):
            finding.clipped.append(qid)
    return finding


def combinations(
    archetypes: Sequence[str],
    boxes: Sequence[Box] = BOXES,
    densities: Sequence[str] = DENSITIES,
    weights: Sequence[str] = WEIGHTS,
) -> list[tuple[str, int, str, str, Box]]:
    """Every archetype x node count x density x weight x box the sweep renders."""
    return list(itertools.product(archetypes, NODE_COUNTS, densities, weights, boxes))


def sweep_archetypes(skip: Sequence[str]) -> list[str]:
    return [a for a in ARCHETYPES if a != "none" and a not in skip]


async def run_sweep(
    out_dir: Path,
    archetypes: Sequence[str],
    font_floor: float,
    boxes: Sequence[Box] = BOXES,
    densities: Sequence[str] = DENSITIES,
    weights: Sequence[str] = WEIGHTS,
    *,
    keep_png: bool = False,
) -> int:
    """Render the whole matrix; save a PNG and the HTML of every failure."""
    from utils import slide_capture as sc

    css = library_css(BRAND)
    rows: list[dict[str, Any]] = []
    failures = 0
    for archetype, count, density, weight, box in combinations(
        archetypes, boxes, densities, weights
    ):
        slide = build_slide(archetype, count, density, weight)
        html = sweep_document(render_skeleton(slide), box, css, font_floor=font_floor)
        capture = await sc.render_html_to_png_with_metrics(html, timeout=60)
        elements = (capture.browser_metrics or {}).get("elements") or []
        prefix = figure_qid(slide) + "."
        finding = inspect(elements, prefix, box)
        full = fullness.measure(elements, prefix, box.height, box.width * box.height)
        key = f"{archetype}-n{count}-{density}-{weight}-{box.name}"
        rows.append(
            {
                "archetype": archetype,
                "nodes": count,
                "density": density,
                "weight": weight,
                "box": f"{box.width}x{box.height}",
                "box_overflow_px": round(finding.box_overflow, 1),
                "box_qid": finding.box_qid,
                "box_sides": finding.box_detail,
                "canvas_overflow_px": round(finding.canvas_overflow, 1),
                "canvas_qid": finding.canvas_qid,
                "clipped": " ".join(finding.clipped[:4]),
                "ok": not finding.failed,
                **fullness.columns(full),
            }
        )
        if finding.failed or keep_png:
            (out_dir / f"{'fail' if finding.failed else 'seen'}-{key}.png").write_bytes(capture.png)
        if finding.failed:
            failures += 1
            (out_dir / f"fail-{key}.html").write_text(html, encoding="utf-8")
        print(
            f"{'FAIL' if finding.failed else 'ok  '} {key:<52} "
            f"box+{finding.box_overflow:6.1f} canvas+{finding.canvas_overflow:6.1f} "
            f"slack {full.card_slack:5.2f} use {full.box_use:5.2f} ink {full.ink_fill:5.2f} "
            f"{' '.join(full.flags)}",
            flush=True,
        )
    _write_table(out_dir, "sweep", rows)
    _write_table(out_dir, "fullness", fullness.worst_per_archetype(rows))
    flagged = sum(1 for row in rows if not row["full"])
    print(f"{failures} overflowing, {flagged} not full, of {len(rows)} -> {out_dir}")
    return failures + flagged


def _write_table(out_dir: Path, stem: str, rows: Sequence[Mapping[str, Any]]) -> None:
    (out_dir / f"{stem}.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    if not rows:
        return
    head = list(rows[0])
    body = [head, *[[str(row[column]) for column in head] for row in rows]]
    text = "\n".join("\t".join(cells) for cells in body) + "\n"
    (out_dir / f"{stem}.tsv").write_text(text, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="authored infographic fit sweep")
    sub = parser.add_subparsers(dest="mode", required=True)
    sweep = sub.add_parser("sweep")
    sweep.add_argument("--out", type=Path, required=True)
    sweep.add_argument("--skip", nargs="*", default=[])
    sweep.add_argument("--only", nargs="*", default=[])
    sweep.add_argument("--font-floor", type=float, default=FONT_FLOOR_PX)
    sweep.add_argument("--box", nargs="*", default=[], metavar="[NAME:]WxH")
    sweep.add_argument("--density", nargs="*", default=[], choices=DENSITIES)
    sweep.add_argument("--weight", nargs="*", default=[], choices=WEIGHTS)
    sweep.add_argument("--keep-png", action="store_true")
    boxes = sub.add_parser("boxes")
    boxes.add_argument("--db", type=Path, required=True)
    boxes.add_argument("--out", type=Path, required=True)
    boxes.add_argument("--per-job", type=int, default=5)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    if args.mode == "boxes":
        from scripts import authored_infographic_fit_boxes as survey

        records = asyncio.run(
            survey.run_boxes(survey.sample_slides(args.db, args.per_job))
        )
        _write_table(args.out, "real-root-boxes", records)
        survey.report_distribution(records)
        return 0
    archetypes = list(args.only) or sweep_archetypes(args.skip)
    boxes = [parse_box(item) for item in args.box] or list(BOXES)
    misses = asyncio.run(
        run_sweep(args.out, archetypes, args.font_floor, boxes,
                  list(args.density) or DENSITIES, list(args.weight) or WEIGHTS,
                  keep_png=args.keep_png)
    )
    return 1 if misses else 0


if __name__ == "__main__":
    raise SystemExit(main())
