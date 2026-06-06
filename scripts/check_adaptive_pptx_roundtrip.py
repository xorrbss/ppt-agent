"""G4 — editable-PPTX byte round-trip for adaptive decks (Docker/Linux/CI only).

Seeds an adaptive deck, exports it to PPTX via the real export runtime, reopens
the .pptx with python-pptx, and asserts that each archetype produced the expected
EDITABLE shapes (text frames with real text, a real table, a chart). This is the
design's gate G4 (revision R4): it can only run where the converter binary exists
(Linux/Docker — `convert-linux-x64`); on Windows the converter is absent
(`convert-win32.exe` is not shipped), so the script skips cleanly.

Run (inside the Docker dev stack, with Next.js + FastAPI + converter up):
  docker compose up development            # serves :5000, FastAPI :8000, converter
  # then, in the FastAPI container:
  RUN_PPTX_ROUNDTRIP=1 NEXT_PUBLIC_FAST_API=http://127.0.0.1:8000 \
    APP_DATA_DIRECTORY=/app_data uv run python scripts/check_adaptive_pptx_roundtrip.py

Exit code 0 = pass or skip; 1 = a round-trip assertion failed.
"""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "servers", "fastapi"))

from services.export_task_service import EXPORT_TASK_SERVICE  # noqa: E402


def _skip(reason: str) -> None:
    print(f"SKIP (G4 byte-PPTX round-trip): {reason}")
    sys.exit(0)


if not os.path.isfile(EXPORT_TASK_SERVICE.converter_path):
    _skip(
        f"export converter not found at {EXPORT_TASK_SERVICE.converter_path} "
        "- byte-level PPTX export needs the Linux/Docker runtime (Windows lacks "
        "convert-win32.exe). The DOM contract is verified separately via /pdf-maker."
    )

from pptx import Presentation  # noqa: E402
from pptx.enum.shapes import MSO_SHAPE_TYPE  # noqa: E402

from models.slide_spec_model import (  # noqa: E402
    BulletItem, ChartInsightSpec, ChartPoint, ComparisonColumn, ComparisonSpec,
    CoverSpec, OneColumnBulletsSpec, StatHeroSpec, StatItem, TableSpec,
    archetype_to_layout_id, spec_to_blocks,
)
from models.sql.presentation import PresentationModel  # noqa: E402
from models.sql.slide import SlideModel  # noqa: E402
from services.database import async_session_maker  # noqa: E402
from templates.get_layout_by_name import get_layout_by_name  # noqa: E402
from utils.export_utils import export_presentation  # noqa: E402

PID = uuid.UUID("ada000g4-0000-4000-8000-0000000000g4".replace("g4", "04"))

SPECS = [
    CoverSpec(archetype="cover", eyebrow="2026", title="클라우드 전략 G4", subtitle="byte round-trip"),
    StatHeroSpec(archetype="stat-hero", title="핵심 지표", stats=[
        StatItem(value="5천억", label="연 매출", delta="+37%"),
        StatItem(value="1위", label="점유율"),
        StatItem(value="240", label="고객사"),
    ]),
    OneColumnBulletsSpec(archetype="one-column-bullets", title="추진 전략", lead="세 가지 축",
        bullets=[BulletItem(text="표준화"), BulletItem(text="자동화"), BulletItem(text="보안 강화")]),
    ComparisonSpec(archetype="comparison", title="도입 전후", columns=[
        ComparisonColumn(heading="기존", items=["수작업", "고장애"]),
        ComparisonColumn(heading="신규", items=["자동화", "안정화"]),
    ]),
    TableSpec(archetype="table", title="요금제", headers=["구분", "베이직", "프로"],
        rows=[["가격", "1만", "3만"], ["지원", "이메일", "24/7"]]),
    ChartInsightSpec(archetype="chart-insight", title="매출 추이", chart_type="bar",
        data=[ChartPoint(name="2022", value=100), ChartPoint(name="2023", value=137)],
        takeaways=[BulletItem(text="성장 지속")]),
]


async def seed():
    layout = await get_layout_by_name("adaptive")
    async with async_session_maker() as s:
        old = await s.get(PresentationModel, PID)
        if old:
            await s.delete(old)
            await s.commit()
        s.add(PresentationModel(
            id=PID, content="G4 roundtrip", n_slides=len(SPECS), language="Korean",
            title="G4 round-trip", outlines={"slides": [{"content": "x"} for _ in SPECS]},
            layout=layout.model_dump(),
            structure={"slides": [layout.get_slide_layout_index(archetype_to_layout_id(sp.archetype)) for sp in SPECS]},
            tone="default", verbosity="standard",
        ))
        for i, sp in enumerate(SPECS):
            s.add(SlideModel(presentation=PID, layout_group="adaptive",
                layout=archetype_to_layout_id(sp.archetype), index=i, speaker_note="",
                content=spec_to_blocks(sp)))
        await s.commit()


def slide_text(slide) -> str:
    return " ".join(sh.text_frame.text for sh in slide.shapes if sh.has_text_frame)


def check(name, cond, failures):
    print(f"  {'ok ' if cond else 'FAIL'} - {name}")
    if not cond:
        failures.append(name)


async def main():
    await seed()
    res = await export_presentation(PID, "G4 round-trip", "pptx")
    prs = Presentation(res.path)
    print(f"exported {res.path} -> {len(prs.slides)} slides")
    failures: list = []
    check("slide count == n", len(prs.slides) == len(SPECS), failures)

    for idx, (sp, slide) in enumerate(zip(SPECS, prs.slides)):
        text = slide_text(slide)
        n_text = sum(1 for sh in slide.shapes if sh.has_text_frame and sh.text_frame.text.strip())
        has_table = any(sh.has_table for sh in slide.shapes)
        has_chart = any(getattr(sh, "has_chart", False) for sh in slide.shapes)
        has_pic = any(sh.shape_type == MSO_SHAPE_TYPE.PICTURE for sh in slide.shapes)
        print(f"slide[{idx}] {sp.archetype}: text_shapes={n_text} table={has_table} "
              f"chart={has_chart} pic={has_pic} | text='{text[:60]}'")
        a = sp.archetype
        if a == "cover":
            check(f"[{idx}] cover title text editable", sp.title in text, failures)
        elif a == "stat-hero":
            check(f"[{idx}] stat-hero has multiple text shapes", n_text >= 3, failures)
        elif a == "one-column-bullets":
            check(f"[{idx}] bullets text present", "표준화" in text, failures)
        elif a == "comparison":
            check(f"[{idx}] comparison headings present", "기존" in text and "신규" in text, failures)
        elif a == "table":
            check(f"[{idx}] table is a real PPTX table", has_table, failures)
        elif a == "chart-insight":
            # Recharts SVG -> native chart or grouped vector (converter-dependent);
            # assert at least the title + a non-text shape (chart) is present.
            non_text = any(not sh.has_text_frame for sh in slide.shapes)
            check(f"[{idx}] chart-insight title + a chart/graphic shape",
                  sp.title in text and (has_chart or non_text), failures)

    if failures:
        print(f"\nG4 FAIL: {len(failures)} assertion(s) failed: {failures}")
        sys.exit(1)
    print("\nG4 PASS: adaptive deck round-trips to editable PPTX shapes.")


asyncio.run(main())
