"""G8 composer-stability harness (manual, real Codex).

Runs the adaptive composer (create -> prepare) over several varied golden
outline sets, REPS times each, against the running backend (:8000), then
aggregates variety / n_slides / schema-valid metrics via utils.composer_metrics.

Run (from servers/fastapi):
  APP_DATA_DIRECTORY=../../app_data uv run python ../../scripts/measure_composer_stability.py

Not a CI test — it needs a live Codex-configured backend. The deterministic
metric logic is unit-tested in tests/unit/test_composer_metrics.py.
"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "servers", "fastapi"))

import aiohttp  # noqa: E402
from templates.get_layout_by_name import get_layout_by_name  # noqa: E402
from utils.composer_metrics import (  # noqa: E402
    DEFAULT_THRESHOLDS,
    evaluate_archetypes,
    passes,
    summarize,
)

BASE = "http://localhost:8000/api/v1/ppt/presentation"
REPS = 3

GOLDEN = [
    {
        "title": "클라우드 전략",
        "outlines": [
            {"content": "# 표지\n2026 클라우드 사업 전략 발표"},
            {"content": "# 목차\n시장, 전략, 지표, 로드맵, 비교, 역량, 매출추이, 마무리"},
            {"content": "# 핵심 지표\n- 시장 4.2조\n- 성장률 37%\n- 점유율 1위\n- 고객 240"},
            {"content": "# 로드맵\n1단계 설계(Q1). 2단계 구축(Q2). 3단계 확산(Q3). 4단계 고도화(Q4)."},
            {"content": "# 경쟁 비교\n기존: 수작업·고장애·느린배포. 신규: 자동화·장애60%감소·일배포."},
            {"content": "# 핵심 역량\n네 가지 동등 역량: 빠른배포, 보안, 무중단확장, 비용효율."},
            {"content": "# 매출 추이\n2022 100, 2023 124, 2024 170. 3년 연속 성장, 신규사업 견인."},
            {"content": "# 핵심 메시지\n고객 경험의 혁신이 곧 시장 지배력이다."},
            {"content": "# 마무리\n감사합니다. 연락처와 다음 단계 안내."},
        ],
    },
    {
        "title": "신제품 출시",
        "outlines": [
            {"content": "# 표지\n차세대 AI 분석 플랫폼 출시"},
            {"content": "# 문제 정의\n기업의 데이터 활용률은 20% 미만. 분석 인력 부족과 도구 파편화."},
            {"content": "# 솔루션 개요\n원클릭 분석, 자연어 질의, 자동 리포트 — 세 가지 핵심 기능."},
            {"content": "# 성과 지표\n- 분석시간 70% 단축\n- 도입사 80개\n- NPS 62"},
            {"content": "# 기존 도구 대비\n기존: 코딩필요·느림·고가. 신규: 노코드·실시간·구독형."},
            {"content": "# 출시 일정\n베타(3월), 정식출시(6월), 엔터프라이즈(9월)."},
            {"content": "# 마무리\n지금 무료 체험을 시작하세요. demo@company.com"},
        ],
    },
]


async def compose_once(session, layout, golden):
    async with session.post(
        f"{BASE}/create",
        json={"content": golden["title"], "n_slides": len(golden["outlines"]), "language": "Korean"},
    ) as r:
        pid = (await r.json())["id"]
    async with session.post(
        f"{BASE}/prepare",
        json={"presentation_id": pid, "outlines": golden["outlines"], "layout": layout, "title": golden["title"]},
    ) as r:
        if r.status != 200:
            return None  # schema-invalid / compose failed
        prep = json.loads(await r.text())
    slides = (prep.get("deck_plan") or {}).get("slides") or []
    return [s.get("archetype") for s in slides]


async def main():
    layout = (await get_layout_by_name("adaptive")).model_dump()
    results, schema_valid, total = [], 0, 0
    async with aiohttp.ClientSession() as session:
        for golden in GOLDEN:
            req_n = len(golden["outlines"])
            for rep in range(REPS):
                total += 1
                archetypes = await compose_once(session, layout, golden)
                if archetypes is None:
                    print(f"  [{golden['title']} #{rep+1}] SCHEMA-INVALID")
                    continue
                schema_valid += 1
                m = evaluate_archetypes(archetypes, req_n)
                results.append(m)
                print(
                    f"  [{golden['title']} #{rep+1}] n={m['n_slides']} match={m['n_match']} "
                    f"distinct={m['distinct_archetypes']} dups={m['adjacent_dups']} :: {archetypes}"
                )

    summary = summarize(results, schema_valid, total)
    print("\n=== G8 composer-stability summary ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("\nproposed thresholds:", json.dumps(DEFAULT_THRESHOLDS, ensure_ascii=False))
    print("passes (proposed):", passes(summary))


asyncio.run(main())
