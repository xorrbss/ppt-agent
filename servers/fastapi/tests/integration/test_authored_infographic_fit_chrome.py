"""Real-Chrome regression: a six-node figure stays inside the box it was given.

The reproduction is the slide that terminated a deck: a six-node ``roadmap``
in the 1138x386 shell its own document reserved. Before the fit work the rows
kept their intrinsic height, needed 548px, and row six landed at y=667..744
with its phrase clipped by the bottom of the canvas.

The unit suite pins the CSS rules; this pins the rendered outcome, which is
the only thing the gate actually reads.
"""

import asyncio
import re
from types import SimpleNamespace

import pytest

from models.authored_infographic_plan import AuthoredSlideInfographic, InfographicNode
from services.authored_composition_policy import blocking_safety_issues
from services.authored_local_fit_policy import html_digest, local_fit_policy
from utils.authored_dom_quality import inspect_authored_dom
from utils.authored_font_policy import enforce_authored_font_floor
from utils.authored_infographic import library_css, render_skeleton
from utils.slide_capture import find_chrome, render_html_to_png_with_metrics

#: The figure shell the failing slide gave its figure, on the 1280x720 canvas.
BOX_LEFT, BOX_TOP, BOX_WIDTH, BOX_HEIGHT = 71, 203, 1138, 386
CANVAS_WIDTH, CANVAS_HEIGHT = 1280, 720
#: Labels and phrases at the plan's own per-field budget.
NODES = (
    ("치명·중대 결함관리", "운영 진입 기준을 객관화한 절차", "8 단계"),
    ("전량 모의이관 검증", "최종 리허설로 전환을 검증한다", "99.9%"),
    ("사용자 인수시험", "이관 수행 결과를 전량 대조한다", "72시간 전"),
    ("사후대사 정합성", "시험 유형별 합격 기준을 고정", "4.6%"),
    ("운영 전환 리허설", "전환 판정 회의체가 승인한다", "2 회 이상"),
    ("단위·통합 시험", "잔여 결함은 회귀 시험으로 확인", "0 건"),
)


def _document(archetype: str, count: int) -> str:
    slide = AuthoredSlideInfographic(
        index=13,
        archetype=archetype,
        weight="standard",
        takeaway="운영 전환을 객관화",
        nodes=tuple(
            InfographicNode(label=label, desc=desc, figure=figure)
            for label, desc, figure in NODES[:count]
        ),
        emphasis=count - 2,
        density="high",
    )
    brand = SimpleNamespace(primary="#08916E", fonts="Noto Sans KR")
    html = (
        '<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><style>'
        "*{box-sizing:border-box}html,body{width:1280px;height:720px;margin:0;overflow:hidden}"
        'body{position:relative;background:#F9F9F9;font-family:"Noto Sans KR",sans-serif}'
        f".shell{{position:absolute;left:{BOX_LEFT}px;top:{BOX_TOP}px;"
        f"width:{BOX_WIDTH}px;height:{BOX_HEIGHT}px;background:#FFFFFF;border-radius:14px}}"
        f"</style><style>{library_css(brand)}</style></head><body>"
        f'<div class="shell">{render_skeleton(slide)}</div></body></html>'
    )
    # The generation pipeline clamps the whole document, this stylesheet
    # included, to the frozen slide capacity's 15px floor before the first
    # render. Rendering without it would measure a stylesheet that never ships.
    return enforce_authored_font_floor(html, minimum_px=15)


@pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")
@pytest.mark.parametrize("archetype", ["roadmap", "layers", "checklist"])
def test_a_six_node_figure_stays_inside_a_1138x386_box(archetype: str) -> None:
    capture = asyncio.run(render_html_to_png_with_metrics(_document(archetype, 6), timeout=60))
    elements = [
        element
        for element in (capture.browser_metrics or {})["elements"]
        if str(element.get("qid") or "").startswith("ig.s14.")
    ]
    assert len(elements) > 6, "the figure did not render its nodes"

    for element in elements:
        qid, box = element["qid"], element["bbox"]
        if box["width"] <= 0 and box["height"] <= 0:
            continue
        assert not element.get("clipped"), f"{qid} is clipped"
        assert box["y"] >= 0 and box["y"] + box["height"] <= CANVAS_HEIGHT, (
            f"{qid} leaves the canvas: y={box['y']:.0f}..{box['y'] + box['height']:.0f}"
        )
        assert box["x"] >= 0 and box["x"] + box["width"] <= CANVAS_WIDTH, f"{qid} leaves the canvas"
        if qid.endswith(".root"):
            continue
        assert box["y"] >= BOX_TOP - 0.5, f"{qid} leaves its box at the top"
        assert box["y"] + box["height"] <= BOX_TOP + BOX_HEIGHT + 0.5, (
            f"{qid} leaves its box at the bottom by "
            f"{box['y'] + box['height'] - BOX_TOP - BOX_HEIGHT:.0f}px"
        )
        assert box["x"] >= BOX_LEFT - 0.5, f"{qid} leaves its box on the left"
        assert box["x"] + box["width"] <= BOX_LEFT + BOX_WIDTH + 0.5, (
            f"{qid} leaves its box on the right"
        )


@pytest.mark.xfail(
    strict=True,
    reason="UNIMPLEMENTED, awaiting a design decision. The scope root here is the "
    "grid itself and its only ancestor, the auto-height <main>, is excluded as "
    "fixed_boundary_unavailable, so the space that growing the map needs (84px "
    "against 8px available) is never reachable: it would come from pushing the "
    "band down, and only <main> holds both. Allowing an auto-height flow "
    "container as a scope root means letting that root grow, which changes v1.4 "
    "design 5.2 ('the document height and viewport may not grow') and the "
    "_sameBox(baseline.rootBox, ...) contract in _scopeHard. Red since the "
    "fragments and this test landed together in bd72851e4.",
)
@pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")
def test_local_fit_compacts_intrinsic_grid_rows_before_they_cover_a_following_band() -> None:
    panels = "".join(
        '<article class="panel" data-qid="panel-{panel}" data-semantic-role="decoration">{rows}</article>'.format(
            panel=panel,
            rows="".join(
                f'<div class="row" data-qid="panel-{panel}-row-{index}" data-semantic-role="body">Row {index}</div>'
                for index in range(5)
            ) + '<span class="badge" data-qid="panel-{panel}-badge" data-semantic-role="supporting">Badge</span>'
            '<span class="diamond" data-qid="panel-{panel}-diamond" data-semantic-role="decoration"></span>'.format(panel=panel),
        )
        for panel in range(3)
    )
    html = f"""<!doctype html><html><head><style>
    *{{box-sizing:border-box}}html,body{{margin:0;width:1280px;height:720px;overflow:hidden;font-family:Arial,sans-serif}}
    main{{position:absolute;left:56px;top:100px;width:1168px}}
    .map{{position:relative;height:180px;display:grid;grid-template-columns:repeat(3,1fr);gap:20px}}
    .panel{{position:relative;display:grid;gap:10px;padding:16px;background:white;border:1px solid #ddd;border-radius:12px}}
    .row{{padding:8px;font-size:16px;line-height:22px;background:#f3f4f6}}
    .badge{{position:absolute;left:12px;top:-9px;height:18px;padding:0 8px;background:#123;color:white;font-size:13px;line-height:18px}}
    .diamond{{position:absolute;right:4px;top:4px;width:10px;height:10px;background:#345;transform:rotate(45deg)}}
    .band{{height:40px;margin-top:10px;padding:8px 16px;background:#123;color:white;font-size:16px;line-height:24px}}
    </style></head><body><main data-qid="main" data-semantic-role="decoration">
    <section class="map" data-qid="map" data-semantic-role="decoration">{panels}</section>
    <section class="band" data-qid="band" data-semantic-role="body">Takeaway</section>
    </main></body></html>"""
    revision = html_digest(html)
    policy = local_fit_policy(
        author_revision=revision,
        original_html_digest=revision,
        slide_index=0,
    )

    fitted = asyncio.run(
        render_html_to_png_with_metrics(html, timeout=60, fit=True, fit_policy=policy, fresh=True)
    )
    report = fitted.fit_report or {}
    assert report.get("unresolved_reasons") == []
    assert any(
        operation.get("kind") == "spacing_adjusted" and operation.get("qid") == "map"
        for operation in report.get("operations", [])
    )
    assert fitted.fitted_html

    verified = asyncio.run(
        render_html_to_png_with_metrics(
            fitted.fitted_html,
            timeout=60,
            fit=False,
            fit_policy={**policy, "baseline": report["baseline"]},
            fresh=True,
        )
    )
    metrics = {item["qid"]: item for item in verified.browser_metrics["elements"]}
    band_top = metrics["band"]["bbox"]["y"]
    assert all(
        metrics[f"panel-{panel}"]["bbox"]["y"] + metrics[f"panel-{panel}"]["bbox"]["height"]
        <= band_top + 0.5
        for panel in range(3)
    )
    quality = inspect_authored_dom(fitted.fitted_html, verified.browser_metrics)
    assert blocking_safety_issues(quality) == ()


@pytest.mark.xfail(
    strict=True,
    reason="UNIMPLEMENTED, awaiting a design decision. The zone needs 24.5px "
    "against 1px available and the slack sits in the empty 100px 'top' section: "
    "materializeGrowth only scans the siblings that follow the grid and only "
    "those that carry ink, and raising the scope root does not help because the "
    "grid's parent is the zone rather than the root. Reclaiming slack from "
    "outside the grid's immediate parent, from preceding siblings, or from "
    "ink-less sections is outside the v1.4 design 5.3 table of initially "
    "supported structures. Red since the fragments and this test landed "
    "together in bd72851e4.",
)
@pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")
def test_local_fit_parent_reflows_a_55px_three_line_grid_card_without_font_or_outer_geometry_drift() -> None:
    cards = "".join(
        f'''<div class="item" data-qid="card-{index}" data-semantic-role="decoration">
        <div class="name" data-qid="name-{index}" data-semantic-role="supporting">ETL · BI</div>
        <div class="tech" data-qid="tech-{index}" data-semantic-role="body">{text}</div></div>'''
        for index, text in enumerate((
            "WebLogic · OHS", "WebLogic · Oracle", "TIBCO BW · EMS",
            "DataStage<br>MicroStrategy<br>Exadata", "IPRON-IVR 대응개발",
        ))
    )
    html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
    *{{box-sizing:border-box}}html,body{{margin:0;width:1280px;height:720px;overflow:hidden;font-family:Arial,sans-serif}}
    .outside{{position:absolute;left:1000px;top:120px;width:180px;height:80px;padding:12px;background:#123;color:white}}
    .frame{{position:absolute;left:56px;top:120px;width:900px;height:330px}}.root{{width:900px;height:330px;padding:16px;display:flex;flex-direction:column;gap:12px;border:1px solid #ddd;background:white}}
    .top{{height:100px;background:#f3f4f6}}.zone{{height:111px;padding:11px 13px;border:1px solid #cfe8e1;background:#f2faf8}}
    .head{{height:22px;margin-bottom:9px;font-size:15px;line-height:22px}}.grid{{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}}
    .item{{height:55px;padding:7px 8px;border:1px solid #ddd;background:white;text-align:center}}
    .name{{font-size:14px;line-height:19px;font-weight:700}}.tech{{font-size:13px;line-height:18px}}
    .bottom{{position:relative;height:60px;background:#eef}}.arrow{{position:absolute;right:10px;top:10px;width:8px;height:8px;transform:rotate(45deg);background:#08916e}}
    </style></head><body>
    <aside class="outside" data-qid="outside" data-semantic-role="body">Outside geometry</aside>
    <div class="frame"><main class="root" data-qid="root" data-semantic-role="decoration">
      <section class="top" data-qid="top" data-semantic-role="decoration"></section>
      <section class="zone" data-qid="zone" data-semantic-role="decoration">
        <div class="head" data-qid="head" data-semantic-role="supporting">Common integration layer</div>
        <div class="grid" data-qid="grid" data-semantic-role="decoration">{cards}</div>
      </section>
      <section class="bottom" data-qid="bottom" data-semantic-role="decoration"><i class="arrow"></i></section>
    </main></div></body></html>"""
    revision = html_digest(html)
    policy = local_fit_policy(author_revision=revision, original_html_digest=revision, slide_index=0)
    original = asyncio.run(render_html_to_png_with_metrics(html, timeout=60, fit=False, fit_policy=policy, fresh=True))
    fitted = asyncio.run(render_html_to_png_with_metrics(html, timeout=60, fit=True, fit_policy=policy, fresh=True))
    report = fitted.fit_report or {}

    assert report.get("unresolved_reasons") == []
    assert any(operation.get("kind") == "parent_reflow" and operation.get("qid") == "root"
               for operation in report.get("operations", []))
    assert fitted.fitted_html
    assert all("font-size:" not in edit["declarations"] and "line-height:" not in edit["declarations"]
               for edit in report.get("edits", []))

    verified = asyncio.run(render_html_to_png_with_metrics(
        fitted.fitted_html, timeout=60, fit=False,
        fit_policy={**policy, "baseline": report["baseline"]}, fresh=True,
    ))
    before = {item["qid"]: item for item in original.browser_metrics["elements"]}
    after = {item["qid"]: item for item in verified.browser_metrics["elements"]}
    for qid in ("root", "outside"):
        assert after[qid]["bbox"] == pytest.approx(before[qid]["bbox"], abs=0.5)
    for qid, item in before.items():
        if qid not in after or not (qid == "root" or qid.startswith(("top", "zone", "head", "grid", "card-", "name-", "tech-", "bottom"))):
            continue
        assert after[qid]["bbox"]["x"] == pytest.approx(item["bbox"]["x"], abs=0.5)
        assert after[qid]["bbox"]["width"] == pytest.approx(item["bbox"]["width"], abs=0.5)
    assert after["card-3"]["bbox"]["height"] > before["card-3"]["bbox"]["height"]
    assert len({round(after[f"card-{index}"]["bbox"]["height"], 1) for index in range(5)}) == 1
    quality = inspect_authored_dom(fitted.fitted_html, verified.browser_metrics)
    assert blocking_safety_issues(quality) == ()


def _fit_and_verify(html: str, *, slide_index: int) -> tuple[object, object, object]:
    """Return the pre-fit capture, fitted capture, and fresh final capture."""

    revision = html_digest(html)
    policy = local_fit_policy(
        author_revision=revision,
        original_html_digest=revision,
        slide_index=slide_index,
    )
    original = asyncio.run(
        render_html_to_png_with_metrics(
            html, timeout=60, fit=False, fit_policy=policy, fresh=True
        )
    )
    fitted = asyncio.run(
        render_html_to_png_with_metrics(
            html, timeout=60, fit=True, fit_policy=policy, fresh=True
        )
    )
    report = fitted.fit_report or {}
    assert report.get("unresolved_reasons") == []
    assert fitted.fitted_html
    verified = asyncio.run(
        render_html_to_png_with_metrics(
            fitted.fitted_html,
            timeout=60,
            fit=False,
            fit_policy={**policy, "baseline": report["baseline"]},
            fresh=True,
        )
    )
    return original, fitted, verified


@pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")
def test_local_fit_expands_only_a_fixed_frame_into_its_footer_safe_zone() -> None:
    html = """<!doctype html><html><head><style>
    *{box-sizing:border-box}html,body{margin:0;width:1280px;height:720px;overflow:hidden;font-family:Arial,sans-serif}
    .frame{position:absolute;left:72px;top:116px;width:850px;height:260px;padding:18px;background:#f6fbf9;border:1px solid #89cfc0}
    .summary{height:264px;padding:18px;background:white;border:1px solid #b8d8d0;font-size:18px;line-height:26px}
    .outside{position:absolute;left:1000px;top:150px;width:180px;height:70px;padding:12px;background:#123;color:white}
    .footer{position:absolute;left:0;right:0;bottom:16px;height:20px;text-align:center;font-size:13px;line-height:20px}
    </style></head><body>
    <main class="frame" data-qid="frame" data-semantic-role="decoration"><section class="summary" data-qid="summary" data-semantic-role="body">A fixed-height summary needs about thirty additional pixels, but the footer safe zone is intentionally available for the parent frame.</section></main>
    <aside class="outside" data-qid="outside" data-semantic-role="body">Outside stays fixed</aside><footer class="footer" data-qid="footer" data-semantic-role="supporting">20</footer>
    </body></html>"""
    original, fitted, verified = _fit_and_verify(html, slide_index=19)
    report = fitted.fit_report or {}
    assert any(item.get("kind") == "parent_reflow" for item in report.get("operations", []))
    assert all("font-size:" not in item["declarations"] for item in report.get("edits", []))
    before = {item["qid"]: item for item in original.browser_metrics["elements"]}
    after = {item["qid"]: item for item in verified.browser_metrics["elements"]}
    assert after["frame"]["bbox"]["height"] >= before["frame"]["bbox"]["height"] + 19
    assert after["frame"]["bbox"]["x"] == pytest.approx(before["frame"]["bbox"]["x"], abs=0.5)
    assert after["frame"]["bbox"]["width"] == pytest.approx(before["frame"]["bbox"]["width"], abs=0.5)
    assert after["summary"]["bbox"]["y"] + after["summary"]["bbox"]["height"] == pytest.approx(
        after["frame"]["bbox"]["y"] + after["frame"]["bbox"]["height"] - 1, abs=0.5
    )
    assert after["frame"]["bbox"]["y"] + after["frame"]["bbox"]["height"] <= after["footer"]["bbox"]["y"] - 0.5
    assert after["outside"]["bbox"] == pytest.approx(before["outside"]["bbox"], abs=0.5)
    assert re.findall(r'data-qid="([^"]+)"', html) == re.findall(r'data-qid="([^"]+)"', fitted.fitted_html)
    assert blocking_safety_issues(inspect_authored_dom(fitted.fitted_html, verified.browser_metrics)) == ()


@pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")
def test_local_fit_expands_only_a_terminal_card_when_bottom_slack_is_available() -> None:
    html = """<!doctype html><html><head><style>
    *{box-sizing:border-box}html,body{margin:0;width:1280px;height:720px;overflow:hidden;font-family:Arial,sans-serif}
    .frame{position:absolute;left:80px;top:120px;width:820px;height:280px;padding:16px;background:#f6fbf9;border:1px solid #89cfc0}
    .stack{height:246px;display:flex;flex-direction:column;gap:12px}.sibling{height:70px;padding:16px;background:#dff3ed;font-size:17px;line-height:22px}.terminal{height:52px;padding:10px 14px;overflow:visible;background:white;border:1px solid #b8d8d0;font-size:17px;line-height:23px}
    .outside{position:absolute;left:1000px;top:150px;width:180px;height:70px;padding:12px;background:#123;color:white}.footer{position:absolute;left:0;right:0;bottom:16px;height:20px;text-align:center;font-size:13px;line-height:20px}
    </style></head><body><main class="frame" data-qid="frame" data-semantic-role="decoration"><div class="stack" data-qid="stack" data-semantic-role="decoration"><section class="sibling" data-qid="sibling" data-semantic-role="body">Stable sibling</section><section class="terminal" data-qid="terminal" data-semantic-role="body">Terminal card uses two fixed lines.<br>Its second 23px line escapes the 44px content box by 2px.</section></div></main><aside class="outside" data-qid="outside" data-semantic-role="body">Outside stays fixed</aside><footer class="footer" data-qid="footer" data-semantic-role="supporting">19</footer></body></html>"""
    original, fitted, verified = _fit_and_verify(html, slide_index=18)
    report = fitted.fit_report or {}
    assert all("font-size:" not in item["declarations"] for item in report.get("edits", []))
    before = {item["qid"]: item for item in original.browser_metrics["elements"]}
    after = {item["qid"]: item for item in verified.browser_metrics["elements"]}
    assert after["terminal"]["bbox"]["height"] > before["terminal"]["bbox"]["height"]
    assert after["sibling"]["bbox"] == pytest.approx(before["sibling"]["bbox"], abs=0.5)
    assert after["frame"]["bbox"] == pytest.approx(before["frame"]["bbox"], abs=0.5)
    assert after["outside"]["bbox"] == pytest.approx(before["outside"]["bbox"], abs=0.5)
    assert after["terminal"]["bbox"]["y"] + after["terminal"]["bbox"]["height"] <= after["footer"]["bbox"]["y"] - 0.5
    assert re.findall(r'data-qid="([^"]+)"', html) == re.findall(r'data-qid="([^"]+)"', fitted.fitted_html)
    assert blocking_safety_issues(inspect_authored_dom(fitted.fitted_html, verified.browser_metrics)) == ()


@pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")
@pytest.mark.xfail(strict=True, reason="authored frozen: v1.5 extended shrink lacks a measured nonextended comparator; see handoff x2-tests")
def test_v15_rejects_extended_shrink_without_a_measured_nonextended_comparator() -> None:
    html = """<!doctype html><html><head><style>
    *{box-sizing:border-box}html,body{margin:0;width:1280px;height:720px;overflow:hidden;font-family:Arial,sans-serif}
    .root{position:absolute;left:100px;top:120px;width:304px;height:104px;padding:2px;background:#f4f8f7}
    .target{height:46px;padding:2px;width:300px;overflow:hidden;background:white;border:1px solid #bddbd4}
    .label{font-size:20px;line-height:24px}
    .footer{position:absolute;left:2px;top:50px;width:300px;height:50px;background:#123;color:white;font-size:16px;line-height:20px}
    .stable{position:absolute;left:500px;top:120px;width:220px;height:60px;padding:8px;background:#eef;font-size:16px;line-height:20px}
    </style></head><body><main class="root" data-qid="root" data-semantic-role="decoration">
    <div class="target" data-qid="target" data-semantic-role="decoration"><div class="label" data-qid="label" data-semantic-role="body">Extended shrink candidate<br>must fit only this label<br>without moving the footer</div></div>
    <footer class="footer" data-qid="footer" data-semantic-role="supporting">Protected footer</footer>
    </main><aside class="stable" data-qid="stable" data-semantic-role="body">Stable sibling</aside></body></html>"""
    revision = html_digest(html)
    legacy_policy = local_fit_policy(
        author_revision=revision,
        original_html_digest=revision,
        policy_revision="authored-local-fit/v1.4",
    )
    legacy = asyncio.run(render_html_to_png_with_metrics(
        html, timeout=60, fit=True, fit_policy=legacy_policy, fresh=True,
    ))
    legacy_reasons = set((legacy.fit_report or {}).get("unresolved_reasons", []))
    assert {"relative_shrink_limit", "candidates_exhausted"} <= legacy_reasons, legacy.fit_report

    policy = local_fit_policy(
        author_revision=revision,
        original_html_digest=revision,
        policy_revision="authored-local-fit/v1.5",
    )
    original = asyncio.run(render_html_to_png_with_metrics(
        html, timeout=60, fit=False, fit_policy=policy, fresh=True,
    ))
    fitted = asyncio.run(render_html_to_png_with_metrics(
        html, timeout=60, fit=True, fit_policy=policy, fresh=True,
    ))
    report = fitted.fit_report or {}
    assert "candidates_exhausted" in report.get("unresolved_reasons", [])
    rejected = [
        item for item in report["candidates"]
        if item.get("outcome") == "rejected:font_extended_comparison_incomplete"
    ]
    assert rejected
    assert all(item.get("rejection", {}).get("code") == "reject.font_extended_comparison_incomplete"
               for item in rejected)
    assert all(item.get("extended_shrink_basis", {}).get("comparison_complete") is False
               for item in rejected)
    assert all(item.get("extended_shrink_basis", {}).get("compared_candidate_ids") == []
               for item in rejected)
    assert any(item.get("uses_parent_space") for item in report["candidates"])
    assert report["edits"] == []
    before = {item["qid"]: item for item in original.browser_metrics["elements"]}
    after = {item["qid"]: item for item in fitted.browser_metrics["elements"]}
    for qid in ("root", "target", "footer", "stable"):
        assert after[qid]["bbox"] == pytest.approx(before[qid]["bbox"], abs=0.5)
