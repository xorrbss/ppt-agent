#!/usr/bin/env python3
"""Seed one real Authored eight-slide semantic vertical slice into product SQLite.

This is an additive local fixture command.  It uses the product SQL models and
the atomic semantic publication service; it never calls the LLM, mocks a
browser/exporter, or rewrites an existing presentation.  The resulting row is
therefore suitable for the official UI and export routes to consume.

Run from the repository root, for example:

    uv run python scripts/seed-authored-semantic-vertical-slice.py \
      --database-url sqlite:///app_data/fastapi.db

The command prints a JSON seed receipt containing the presentation id,
revision, operation lineage, active manifest digest, semantic HTML/source
proof, and the exact QID set. ``--variant`` makes release-matrix decks
content-distinct while preserving the same bounded archetype coverage.
The ``--font-path`` and CJK face paths must point at actual font bytes.  On
Windows, Malgun Gothic and Batang are used as defaults when present.  The
command fails closed when any required face is unavailable; it never labels
placeholder bytes as a real font.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
import uuid


REPO_ROOT = Path(__file__).resolve().parents[1]
FASTAPI_ROOT = REPO_ROOT / "servers" / "fastapi"
if str(FASTAPI_ROOT) not in sys.path:
    sys.path.insert(0, str(FASTAPI_ROOT))


# Imports intentionally happen after DATABASE_URL is configured in main().
_IMAGE_PATH = REPO_ROOT / "templates" / "general" / "static" / "image3-82be19a88965.png"
if not _IMAGE_PATH.is_file():
    raise RuntimeError(f"bundled_image_missing:{_IMAGE_PATH}")
_PNG_BYTES = _IMAGE_PATH.read_bytes()
_IMAGE_DATA_URI = "data:image/png;base64," + base64.b64encode(_PNG_BYTES).decode("ascii")


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _locator_digest(value: str) -> str:
    return _sha256(value.encode("utf-8"))


def _artifact_ref(digest: str) -> str:
    return "artifact://sha256/" + digest.removeprefix("sha256:")


def _app_data_directory(database_url: str | None) -> Path:
    configured = os.getenv("APP_DATA_DIRECTORY")
    if configured:
        return Path(configured).expanduser().resolve()
    if database_url and database_url.startswith("sqlite"):
        raw_path = urlsplit(database_url).path
        if os.name == "nt" and raw_path.startswith("/") and len(raw_path) > 2 and raw_path[2] == ":":
            raw_path = raw_path[1:]
        return Path(raw_path).expanduser().resolve().parent
    raise RuntimeError("app_data_directory_required_for_font_allowlist")


def _materialize_font_allowlist(font_bytes: bytes, digest: str, database_url: str | None) -> Path:
    target = _app_data_directory(database_url) / "font-embedding-allowlist" / "IBMPlexMono-Regular.ttf"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not target.is_file() or _sha256(target.read_bytes()) != digest:
            raise RuntimeError("font_allowlist_digest_conflict")
        return target
    try:
        with target.open("xb") as handle:
            handle.write(font_bytes)
    except FileExistsError:
        if _sha256(target.read_bytes()) != digest:
            raise RuntimeError("font_allowlist_digest_conflict") from None
    return target


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLAlchemy URL; defaults to the product APP_DATA_DIRECTORY database.",
    )
    parser.add_argument(
        "--owner-id",
        default=None,
        help="Existing product user UUID. If omitted, the active admin_slot=primary user is used.",
    )
    parser.add_argument(
        "--presentation-id",
        default=None,
        help="Optional UUID; defaults to a fresh UUID4.",
    )
    parser.add_argument(
        "--font-path",
        required=True,
        help="Actual IBM Plex Mono font bytes to persist.",
    )
    parser.add_argument(
        "--cjk-sans-font-path",
        default="C:/Windows/Fonts/malgun.ttf" if os.name == "nt" else None,
        help="Actual CJK sans face bytes; Windows Malgun Gothic is the default on Windows.",
    )
    parser.add_argument(
        "--cjk-serif-font-path",
        default="C:/Windows/Fonts/batang.ttc" if os.name == "nt" else None,
        help="Actual CJK serif face bytes; Windows Batang is the default on Windows.",
    )
    parser.add_argument(
        "--tenant-id",
        default="default",
        help="Semantic artifact tenant scope.",
    )
    parser.add_argument(
        "--slide-count",
        type=int,
        choices=(2, 8),
        default=8,
        help="Seed the fresh eight-slide quality slice (default) or the legacy two-slide slice.",
    )
    parser.add_argument(
        "--receipt-path",
        default=None,
        help="Optional path for the atomic JSON seed receipt.",
    )
    parser.add_argument(
        "--variant",
        default="default",
        help="Content variant label; release matrix decks must use distinct labels.",
    )
    return parser.parse_args()


def _html(slide: str) -> str:
    if slide == "a":
        return """<!doctype html><html data-qid='a-document' lang='ko'><head><meta charset='utf-8'><style>
*{box-sizing:border-box}html,body{margin:0;width:1280px;height:720px;overflow:hidden;background:#f8fafc}
.panel{position:absolute;left:54px;top:46px;width:1172px;height:620px;border:2px solid #0f172a;border-radius:22px;background:#fff;padding:34px;font-family:'Malgun Gothic',sans-serif}
.simple-shape{position:absolute;border-radius:12px}.chart{position:absolute;left:780px;top:270px;width:370px;height:250px;background:linear-gradient(135deg,#e0f2fe,#bfdbfe);filter:drop-shadow(2px 4px 4px rgba(15,23,42,.18))}
.boundary-sentinel{position:absolute;background:transparent;border:1px dashed transparent}.image-card{position:absolute;left:82px;top:300px;width:260px;height:170px;object-fit:cover}
</style></head><body data-qid='a-body-root'><section data-qid='a-panel' class='panel' data-semantic-role='panel'>
<h1 data-qid='a-title' data-semantic-role='title' style='position:absolute;left:34px;top:28px;font-size:34px;color:#0f172a'>Authored 품질 Slice A</h1>
<p data-qid='a-body' data-semantic-role='body' style='position:absolute;left:38px;top:106px;width:590px;height:100px;font-size:25px;line-height:1.35;color:#1e293b'>혼합 텍스트 Korean / Latin / 2026 숫자</p>
<img data-qid='a-image' class='image-card' alt='simple image fixture' src='__IMAGE_DATA_URI__'>
<div data-qid='a-shape-primary' class='simple-shape' role='presentation' style='left:390px;top:310px;width:250px;height:72px;background:#2563eb'></div>
<div data-qid='a-shape-secondary' class='simple-shape' role='presentation' style='left:820px;top:430px;width:190px;height:56px;background:#f59e0b'></div>
<div data-qid='a-chart' class='chart complex-island' data-semantic-role='chart' aria-label='complex chart raster island'></div>
</section>
<div data-qid='a-right-sentinel' class='boundary-sentinel' style='left:1248px;top:310px;width:2px;height:10px;background:#ef4444;border-color:#ef4444'></div>
<div data-qid='a-bottom-sentinel' class='boundary-sentinel' style='left:640px;top:708px;width:10px;height:2px;background:#ef4444;border-color:#ef4444'></div>
<p data-qid='a-body-mono' data-semantic-role='body' style='position:absolute;left:704px;top:160px;margin:0;font-family:"IBM Plex Mono",monospace;font-size:25px;color:#1e293b'>MONORUN</p>
</body></html>""".replace("__IMAGE_DATA_URI__", _IMAGE_DATA_URI)
    if slide == "b":
        return """<!doctype html><html data-qid='b-document' lang='ko'><head><meta charset='utf-8'><style>
*{box-sizing:border-box}html,body{margin:0;width:1280px;height:720px;overflow:hidden;background:#fff7ed}
.panel{position:absolute;left:72px;top:62px;width:1136px;height:596px;border:3px solid #7c2d12;border-radius:6px;background:#fffbeb;padding:28px;font-family:'Batang',serif}
.simple-shape{position:absolute;border-radius:2px}.chart{position:absolute;left:760px;top:250px;width:390px;height:272px;background:linear-gradient(45deg,#fed7aa,#fdba74);filter:drop-shadow(0 3px 5px rgba(124,45,18,.2))}
.boundary-sentinel{position:absolute;background:transparent;border:1px dashed transparent}.image-card{position:absolute;left:92px;top:290px;width:230px;height:190px;object-fit:contain}
</style></head><body data-qid='b-body-root'><section data-qid='b-panel' class='panel' data-semantic-role='panel'>
<h1 data-qid='b-title' data-semantic-role='title' style='position:absolute;left:28px;top:24px;font-size:32px;color:#7c2d12'>동일 class, 다른 내용 B</h1>
<p data-qid='b-body' data-semantic-role='body' style='position:absolute;left:32px;top:100px;width:600px;height:108px;font-size:23px;line-height:1.45;color:#431407'>다른 content / Latin-KR / 3.14159</p>
<img data-qid='b-image' class='image-card' alt='simple image fixture' src='__IMAGE_DATA_URI__'>
<div data-qid='b-shape-primary' class='simple-shape' role='presentation' style='left:370px;top:310px;width:270px;height:80px;background:#ea580c'></div>
<div data-qid='b-shape-secondary' class='simple-shape' role='presentation' style='left:840px;top:430px;width:160px;height:44px;background:#16a34a'></div>
<div data-qid='b-chart' class='chart complex-island' data-semantic-role='chart' aria-label='complex chart raster island'></div>
</section>
<div data-qid='b-right-sentinel' class='boundary-sentinel' style='left:1278px;top:300px;width:2px;height:12px;background:#ef4444;border-color:#ef4444'></div>
<div data-qid='b-bottom-sentinel' class='boundary-sentinel' style='left:620px;top:718px;width:12px;height:2px;background:#ef4444;border-color:#ef4444'></div>
<p data-qid='b-body-mono' data-semantic-role='body' style='position:absolute;left:722px;top:170px;margin:0;font-family:"IBM Plex Mono",monospace;font-size:23px;color:#431407'>FIXEDWIDTH</p>
</body></html>""".replace("__IMAGE_DATA_URI__", _IMAGE_DATA_URI)
    raise ValueError(f"unknown slide: {slide}")


def _eight_slide_htmls(variant: str = "default") -> list[str]:
    image = _IMAGE_DATA_URI
    bodies = [
        """<h1 data-qid='s01-title'>분기별 운영 지표 카드</h1><table data-qid='s01-table'><tr data-qid-ignore><th data-qid-ignore>분기</th><th data-qid-ignore>정확도</th><th data-qid-ignore>처리량</th></tr><tr data-qid-ignore><td data-qid-ignore>1분기</td><td data-qid-ignore>92%</td><td data-qid-ignore>1,240</td></tr><tr data-qid-ignore><td data-qid-ignore>2분기</td><td data-qid-ignore>96%</td><td data-qid-ignore>1,680</td></tr></table><p data-qid='s01-caption'>카드 지표 표 데이터와 합계를 네이티브 편집 대상으로 보존합니다.</p>""",
        """<h1 data-qid='s02-title'>月度增长趋势 时间线</h1><div data-qid='s02-chart' class='chart-native'></div><p data-qid='s02-chart-label'>图表 · 时间线 · 营收 · 2026</p>""",
        """<h1 data-qid='s03-title'>検討プロセス アーキテクチャ</h1><div data-qid='s03-node-a' class='node n1'>収集</div><div data-qid='s03-node-b' class='node n2'>検証</div><div data-qid='s03-node-c' class='node n3'>配布</div><div data-qid='s03-arrow-a' class='arrow a1'>→</div><div data-qid='s03-arrow-b' class='arrow a2'>→</div>""",
        f"""<h1 data-qid='s04-title'>이미지 크롭 비교</h1><img data-qid='s04-image' src='{image}' alt='cropped evidence image'><p data-qid='s04-caption'>object-fit: cover · 50% 25% · 比較 / 比较</p>""",
        """<h1 data-qid='s05-title'>긴 한국어 제목과 中文説明 및 日本語データ가 줄바꿈과 여백을 보존하는지 검증합니다</h1><p data-qid='s05-body'>이 문장은 실제 저작물에서 발생하는 긴 한국어 문장입니다. 中文 설명과 日本語 データ, Korean typography / 2026 숫자와 행간, 자간, 단어 단위 줄바꿈이 PowerPoint 저장 후에도 유지되어야 합니다. インフォグラフィック 품질을 함께 확인합니다.</p><code data-qid='s05-mono'>MONO-2026-検証-검증</code>""",
        """<h1 data-qid='s06-title'>세 가지 실행 축 로드맵</h1><div data-qid='s06-grid' class='columns'><section data-qid-ignore><h2 data-qid='s06-col-a'>수집</h2><p data-qid='s06-copy-a'>원본 보존</p></section><section data-qid-ignore><h2 data-qid='s06-col-b'>분석</h2><p data-qid='s06-copy-b'>계약 검증</p></section><section data-qid-ignore><h2 data-qid='s06-col-c'>공유</h2><p data-qid='s06-copy-c'>로드맵 증거 결속</p></section></div>""",
        """<h1 data-qid='s07-title'>混合 CSS 比較装飾</h1><div data-qid='s07-gradient' class='gradient'></div><div data-qid='s07-badge' class='badge'>Hybrid</div><p data-qid='s07-copy'>gradient · shadow · transform · radius · opacity · clip은 안정 reason code로 강등됩니다. 比較 가능한 인포그래픽 장식입니다.</p>""",
        """<h1 data-qid='s08-title'>경계 보존 센티넬</h1><p data-qid='s08-copy'>오른쪽과 아래쪽 경계, 내부 요소의 clipping과 ghosting을 확인합니다.</p><span data-qid='s08-right' class='right-sentinel'>R</span><span data-qid='s08-bottom' class='bottom-sentinel'>B</span><span data-qid='s08-internal' class='internal-sentinel'>INTERNAL</span>""",
    ]
    scoped_css = [
        "table{border-collapse:collapse;width:760px;font-size:24px}th,td{border:1px solid #94a3b8;padding:14px;text-align:left}th{background:#2563eb;color:#fff;font-weight:700}td{background:transparent;color:#202020;font-weight:400}",
        ".chart-native{position:absolute;left:120px;top:185px;width:880px;height:350px}",
        ".node{position:absolute;top:280px;width:210px;padding:28px;border-radius:18px;background:#dbeafe;text-align:center;font-size:30px}.n1{left:110px}.n2{left:500px}.n3{left:890px}.arrow{position:absolute;top:292px;font-size:54px}.a1{left:385px}.a2{left:775px}",
        "img{position:absolute;left:100px;top:160px;width:720px;height:400px;object-fit:cover;object-position:50% 25%}p{position:absolute;left:860px;top:260px;width:280px;font-size:24px}",
        "h1{font-family:'Batang',serif;max-width:1060px;line-height:1.18}p{font-family:'Batang',serif;width:980px;font-size:25px;line-height:1.62}code{display:block;margin-top:34px;font-family:'IBM Plex Mono',monospace;font-size:24px}",
        ".columns{display:grid;grid-template-columns:repeat(3,1fr);gap:28px}.columns section{padding:28px;background:#eff6ff;border-top:6px solid #2563eb;min-height:280px}.columns h2{font-size:30px}.columns p{font-size:23px}",
        ".gradient{height:220px;border-radius:26px;background:linear-gradient(135deg,#0ea5e9,#1d4ed8);box-shadow:0 18px 40px #0f172a55;opacity:.88;clip-path:polygon(0 0,100% 0,88% 100%,0 100%)}.badge{position:absolute;right:100px;top:250px;padding:24px 48px;background:#facc15;border-radius:18px;transform:rotate(-5deg);font-size:30px}.fixture-panel p{font-size:24px}",
        ".right-sentinel{position:absolute;right:0;top:320px}.bottom-sentinel{position:absolute;bottom:0;left:620px}.internal-sentinel{position:absolute;right:90px;bottom:80px}.right-sentinel,.bottom-sentinel,.internal-sentinel{padding:6px;background:#ef4444;color:white;font-weight:700}",
    ]
    if variant != "default":
        bodies[0] = bodies[0].replace("</h1>", f" · {variant}</h1>")
        bodies[7] = bodies[7].replace("</h1>", f" · {variant}</h1>")
    languages = ["ko", "zh", "ja", "ko", "ko", "ko", "zh", "ko"]
    htmls: list[str] = []
    for index, (body, css) in enumerate(zip(bodies, scoped_css, strict=True), start=1):
        scope = f"scope-{index:02d}"
        htmls.append(
            f"<!doctype html><html lang='{languages[index - 1]}'><head><meta charset='utf-8'><style>"
            "*{box-sizing:border-box}html,body{margin:0;width:1280px;height:720px;overflow:hidden;background:#f8fafc}"
            ".fixture-panel{position:relative;width:100%;height:100%;padding:64px 72px;font-family:'Malgun Gothic',sans-serif;color:#0f172a}"
            ".fixture-panel h1{margin:0 0 42px;font-size:42px;line-height:1.2}"
            f"[data-scope='{scope}'].fixture-panel{{border-top:{6 + index}px solid hsl({205 + index * 7} 78% 45%)}}"
            f"[data-scope='{scope}'].fixture-panel {css}"
            "</style></head><body>"
            f"<main data-qid='s{index:02d}-root' data-scope='{scope}' class='fixture-panel'>{body}</main>"
            "</body></html>"
        )
    return htmls


def _eight_slide_semantic_drafts(
    htmls: list[str], image_digest: str
) -> list[dict]:
    import re

    drafts: list[dict] = []
    for slide_index, html in enumerate(htmls, start=1):
        qids = re.findall(r"data-qid=['\"]([^'\"]+)['\"]", html)
        root_qid = f"s{slide_index:02d}-root"
        children_by_qid = {qid: [] for qid in qids}
        parent_by_qid: dict[str, str | None] = {}
        for qid in qids:
            if qid == root_qid:
                parent_by_qid[qid] = None
            elif slide_index == 6 and (
                qid.startswith("s06-col-") or qid.startswith("s06-copy-")
            ):
                parent_by_qid[qid] = "s06-grid"
            else:
                parent_by_qid[qid] = root_qid
            if parent_by_qid[qid]:
                children_by_qid[parent_by_qid[qid]].append(qid)
        elements: list[dict] = []
        for ordinal, qid in enumerate(qids):
            base = {
                "elementId": qid,
                "semanticRole": (
                    "title" if qid.endswith("title") else "body"
                ),
                "parentId": parent_by_qid[qid],
                "childIds": children_by_qid[qid],
                "intendedOrder": ordinal,
                "requiredFeatures": [],
            }
            if qid == "s02-chart":
                element = base | {
                    "kind": "chart",
                    "semanticRole": "nativeChart",
                    "requiredFeatures": ["native-chart", "chart-data", "chart.axes", "chart.legend", "chart.fonts"],
                    "payload": {
                        "chartType": "column",
                        "categories": ["1월", "2월", "3월", "4월"],
                        "series": [
                            {"name": "매출", "values": [42, 58, 71, 88], "color": "#2563EB"},
                            {"name": "영업이익", "values": [12, 17, 24, 31], "color": "#0EA5E9"},
                        ],
                        "title": "2026 월별 성장",
                        "legend": {"visible": True, "position": "bottom", "fontFamily": "Malgun Gothic", "fontSizePt": 11, "color": "#0F172A"},
                        "categoryAxis": {"title": "월", "fontFamily": "Malgun Gothic", "fontSizePt": 10, "color": "#334155"},
                        "valueAxis": {"title": "억원", "fontFamily": "Malgun Gothic", "fontSizePt": 10, "color": "#334155"},
                        "fontFamily": "Malgun Gothic",
                        "fontSizePt": 11,
                        "textColor": "#0F172A",
                        "palette": ["#2563EB", "#0EA5E9"],
                    },
                }
            elif qid == "s04-image":
                element = base | {
                    "kind": "image",
                    "semanticRole": "croppedImage",
                    "requiredFeatures": ["crop", "object-fit", "object-position"],
                    "payload": {
                        "assetId": "asset:vertical-slice-image",
                        "assetDigest": image_digest,
                        "altText": "cropped evidence image",
                        "intrinsicWidthPx": 192,
                        "intrinsicHeightPx": 128,
                        "objectFit": "cover",
                        "objectPosition": "50% 25%",
                    },
                }
            elif qid == "s01-table":
                element = base | {
                    "kind": "table",
                    "semanticRole": "nativeTable",
                    "requiredFeatures": ["table", "table-v1", "cell-text", "cell-fill"],
                    "payload": {
                        "schemaVersion": "authored-native-table/v1",
                        "headerRows": 1,
                        "fontFamily": "Malgun Gothic",
                        "fontSizePt": 18,
                        "columnWidths": [1.0, 1.0, 1.0],
                        "rows": [
                            [
                                {"text": "분기", "fill": "#2563EB", "textColor": "#FFFFFF", "bold": True},
                                {"text": "정확도", "fill": "#2563EB", "textColor": "#FFFFFF", "bold": True},
                                {"text": "처리량", "fill": "#2563EB", "textColor": "#FFFFFF", "bold": True},
                            ],
                            [{"text": "1분기"}, {"text": "92%"}, {"text": "1,240"}],
                            [{"text": "2분기"}, {"text": "96%"}, {"text": "1,680"}],
                        ],
                    },
                }
            elif any(token in qid for token in ("grid", "gradient", "badge", "root")):
                features = {
                    "s01-table": ["table", "table-grid"],
                    "s06-grid": ["grid", "multi-column"],
                    "s07-gradient": ["gradient", "shadow", "opacity", "clip-path"],
                    "s07-badge": ["transform", "border-radius"],
                }.get(qid, [])
                element = base | {
                    "kind": "complexIsland" if not qid.endswith("root") else "group",
                    "semanticRole": "complexExpression" if features else "slideRoot",
                    "requiredFeatures": features,
                    "sourceRef": _sha256(f"{qid}:{html}".encode()),
                    "rasterReason": "complex_css_requires_raster" if features else "group_serializer_unavailable",
                }
            elif any(token in qid for token in ("node", "arrow", "right", "bottom", "internal")):
                element = base | {
                    "kind": "shape",
                    "semanticRole": "boundarySentinel" if slide_index == 8 else "diagramNode",
                    "payload": {
                        "preset": "rectangle",
                        "fill": "#2563EB" if slide_index == 3 else "#EF4444",
                        "stroke": None,
                        "radiusPt": 10 if slide_index == 3 else 0,
                    },
                }
            else:
                match = re.search(
                    rf"data-qid=['\"]{re.escape(qid)}['\"][^>]*>(.*?)</[^>]+>",
                    html,
                    re.DOTALL,
                )
                text = (
                    re.sub(r"<[^>]+>", " ", match.group(1)).strip()
                    if match
                    else qid.replace("-", " ")
                )
                family = "Batang" if slide_index in (3, 5) and qid != "s05-mono" else "Malgun Gothic"
                language_by_slide = ["ko", "zh", "ja", "ko", "ko", "ko", "zh", "ko"]
                language = language_by_slide[slide_index - 1]
                script = "cjk"
                runs = (
                    [
                        {
                            "text": "MONO-2026-",
                            "language": "en",
                            "script": "latin",
                            "fontFamily": "IBM Plex Mono",
                            "fontFace": "Regular",
                            "fontSizePt": 18,
                            "weight": 400,
                            "color": "#0F172A",
                        },
                        {
                            "text": "검증",
                            "language": language,
                            "script": "cjk",
                            "fontFamily": "BatangChe",
                            "fontFace": "Regular",
                            "fontSizePt": 18,
                            "weight": 400,
                            "color": "#0F172A",
                        },
                    ]
                    if qid == "s05-mono"
                    else [{
                        "text": text,
                        "language": "ko",
                        "script": script,
                        "fontFamily": family,
                        "fontFace": "Regular",
                        "fontSizePt": 30 if qid.endswith("title") else 18,
                        "weight": 700 if qid.endswith("title") else 400,
                        "color": "#0F172A",
                    }]
                )
                element = base | {
                    "kind": "text",
                    "payload": {
                        "paragraphs": [{"runs": runs}],
                        "whitespace": "normal",
                    },
                }
            elements.append(element)
        drafts.append({
            "semanticSource": {
                "schemaVersion": "authored-semantic-source/v1",
                "origin": "authored-declared-v1",
                "elements": elements,
                "elementIdentityRegistry": {
                    "schemaVersion": "authored-element-identity-registry/v1",
                    "activeQids": sorted(qids),
                    "retiredQids": [],
                },
            }
        })
    return drafts


def _bounds(x: float, y: float, width: float, height: float) -> dict[str, float]:
    # Authored DOM is captured in CSS px; semantic intent is persisted in
    # PowerPoint points using the fixed 96-DPI conversion used by the product.
    px_to_pt = 0.75
    return {
        "xPt": x * px_to_pt,
        "yPt": y * px_to_pt,
        "widthPt": width * px_to_pt,
        "heightPt": height * px_to_pt,
        "rotationDeg": 0,
    }


def _text_payload(text: str, *, title: bool = False, body: bool = False, font_family: str = "Malgun Gothic", color: str | None = None) -> dict:
    runs = []
    if body:
        runs.extend(
            [
                {
                    "text": text,
                    "language": "ko",
                    "script": "cjk",
                    "fontFamily": font_family,
                    "fontFace": "Regular",
                    "fontSizePt": 18,
                    "weight": 400,
                    "color": color or "#1e293b",
                },
            ]
        )
    else:
        runs.append(
            {
                "text": text,
                "language": "ko" if title else "en",
                "script": "cjk" if title else "latin",
                "fontFamily": font_family if title else "IBM Plex Mono",
                "fontFace": "Regular",
                "fontSizePt": 24 if title else 16,
                "weight": 700 if title else 400,
                "color": color or ("#0f172a" if title else "#1e293b"),
            }
        )
    return {"paragraphs": [{"runs": runs}], "whitespace": "normal"}


def _semantic_elements(slide: str, asset_digest: str) -> list[dict]:
    prefix = f"{slide}-"
    if slide == "a":
        title = "Authored 품질 Slice A"
        body = "혼합 텍스트 Korean / Latin / 2026 숫자"
    else:
        title = "동일 class, 다른 내용 B"
        body = "다른 content / Latin-KR / 3.14159"
    font_family = "Malgun Gothic" if slide == "a" else "Batang"
    text_color = "#0f172a" if slide == "a" else "#7c2d12"
    body_color = "#1e293b" if slide == "a" else "#431407"
    mono_text = "MONORUN" if slide == "a" else "FIXEDWIDTH"
    root_children = [f"{prefix}body-root"]
    body_children = [
        f"{prefix}panel",
        f"{prefix}right-sentinel",
        f"{prefix}bottom-sentinel",
        f"{prefix}body-mono",
    ]
    children = [
        f"{prefix}title",
        f"{prefix}body",
        f"{prefix}image",
        f"{prefix}shape-primary",
        f"{prefix}shape-secondary",
        f"{prefix}chart",
    ]
    elements: list[dict] = [
        {
            "elementId": f"{prefix}document",
            "kind": "shape",
            "semanticRole": "documentRoot",
            "parentId": None,
            "childIds": root_children,
            "intendedOrder": 0,
            "intendedBounds": _bounds(0, 0, 1280, 720),
            "requiredFeatures": [],
            "payload": {"preset": "rectangle", "fill": "#ffffff", "stroke": None, "radiusPt": None},
        },
        {
            "elementId": f"{prefix}body-root",
            "kind": "shape",
            "semanticRole": "bodyRoot",
            "parentId": f"{prefix}document",
            "childIds": body_children,
            "intendedOrder": 1,
            "intendedBounds": _bounds(0, 0, 1280, 720),
            "requiredFeatures": [],
            "payload": {"preset": "rectangle", "fill": None, "stroke": None, "radiusPt": None},
        },
        {
            "elementId": f"{prefix}panel",
            "kind": "shape",
            "semanticRole": "panel",
            "parentId": f"{prefix}body-root",
            "childIds": children,
            "intendedOrder": 2,
            "intendedBounds": _bounds(54 if slide == "a" else 72, 46 if slide == "a" else 62, 1172 if slide == "a" else 1136, 620 if slide == "a" else 596),
            "requiredFeatures": [],
            "payload": {"preset": "roundedRectangle" if slide == "a" else "rectangle", "fill": "#ffffff", "stroke": "#0f172a" if slide == "a" else "#7c2d12", "radiusPt": 12 if slide == "a" else 2},
        },
        {
            "elementId": f"{prefix}title",
            "kind": "text",
            "semanticRole": "title",
            "parentId": f"{prefix}panel",
            "childIds": [],
            "intendedOrder": 3,
            "intendedBounds": _bounds(84, 78, 530, 52),
            "requiredFeatures": [],
            "payload": _text_payload(title, title=True, font_family=font_family, color=text_color),
        },
        {
            "elementId": f"{prefix}body",
            "kind": "text",
            "semanticRole": "body",
            "parentId": f"{prefix}panel",
            "childIds": [],
            "intendedOrder": 4,
            "intendedBounds": _bounds(86, 152, 620, 80),
            "requiredFeatures": [],
            "payload": _text_payload(body, body=True, font_family=font_family, color=body_color),
        },
        {
            "elementId": f"{prefix}body-mono",
            "kind": "text",
            "semanticRole": "bodyMonoRun",
            "parentId": f"{prefix}body-root",
            "childIds": [],
            "intendedOrder": 5,
            "intendedBounds": _bounds(530, 164, 180, 34),
            "requiredFeatures": [],
            "payload": _text_payload(mono_text, font_family="IBM Plex Mono", color=body_color),
        },
        {
            "elementId": f"{prefix}image",
            "kind": "image",
            "semanticRole": "image",
            "parentId": f"{prefix}panel",
            "childIds": [],
            "intendedOrder": 6,
            "intendedBounds": _bounds(136, 346, 220, 145),
            "requiredFeatures": [],
            "payload": {"assetId": "asset:vertical-slice-image", "assetDigest": asset_digest, "altText": "simple image fixture", "intrinsicWidthPx": 192, "intrinsicHeightPx": 128, "objectFit": "contain", "objectPosition": "50% 50%"},
        },
    ]
    for index, (qid, fill, x, y, width, height) in enumerate(
        [
            (f"{prefix}shape-primary", "#2563eb" if slide == "a" else "#ea580c", 390, 310, 250, 72),
            (f"{prefix}shape-secondary", "#f59e0b" if slide == "a" else "#16a34a", 820 if slide == "a" else 840, 430, 190 if slide == "a" else 160, 56 if slide == "a" else 44),
        ],
        start=7,
    ):
        elements.append(
            {
                "elementId": qid,
                "kind": "shape",
                "semanticRole": "simpleShape",
                "parentId": f"{prefix}panel",
                "childIds": [],
                "intendedOrder": index,
                "intendedBounds": _bounds(x, y, width, height),
                "requiredFeatures": [],
                "payload": {"preset": "roundedRectangle", "fill": fill, "stroke": None, "radiusPt": 8},
            }
        )
    elements.append(
        {
            "elementId": f"{prefix}chart",
            "kind": "complexIsland",
            "semanticRole": "chart",
            "parentId": f"{prefix}panel",
            "childIds": [],
            "intendedOrder": 9,
            "intendedBounds": _bounds(760 if slide == "b" else 780, 250 if slide == "b" else 270, 390 if slide == "b" else 370, 272 if slide == "b" else 250),
            "requiredFeatures": ["chart", "gradient", "filter"],
            "sourceRef": _sha256(f"{slide}:complex-chart-raster-island".encode()),
            "rasterReason": "complex_chart_requires_raster_island",
        }
    )
    for index, suffix in enumerate(("right-sentinel", "bottom-sentinel"), start=10):
        if slide == "a":
            sentinel_bounds = (1248, 310, 2, 10) if suffix.startswith("right") else (640, 708, 10, 2)
        else:
            sentinel_bounds = (1278, 300, 2, 12) if suffix.startswith("right") else (620, 718, 12, 2)
        elements.append(
            {
                "elementId": f"{prefix}{suffix}",
                "kind": "shape",
                "semanticRole": "boundarySentinel",
                "parentId": f"{prefix}body-root",
                "childIds": [],
                "intendedOrder": index,
                "intendedBounds": _bounds(*sentinel_bounds),
                "requiredFeatures": [],
                "payload": {"preset": "rectangle", "fill": None, "stroke": None, "radiusPt": None},
            }
        )
    return elements


def _semantic_draft(slide: str, asset_digest: str) -> dict:
    elements = _semantic_elements(slide, asset_digest)
    return {
        "semanticSource": {
            "schemaVersion": "authored-semantic-source/v1",
            "origin": "authored-declared-v1",
            "elements": elements,
            "elementIdentityRegistry": {
                "schemaVersion": "authored-element-identity-registry/v1",
                "activeQids": sorted(item["elementId"] for item in elements),
                "retiredQids": [],
            },
        }
    }


async def _seed(args: argparse.Namespace) -> dict:
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url
    owner_id = uuid.UUID(args.owner_id) if args.owner_id else None
    presentation_id = uuid.UUID(args.presentation_id) if args.presentation_id else uuid.uuid4()
    operation_id = uuid.uuid4()
    generation_attempt_id = uuid.uuid4()
    font_path = Path(args.font_path).expanduser().resolve()
    cjk_sans_path = Path(args.cjk_sans_font_path).expanduser().resolve() if args.cjk_sans_font_path else None
    cjk_serif_path = Path(args.cjk_serif_font_path).expanduser().resolve() if args.cjk_serif_font_path else None
    for label, candidate in (("font_path", font_path), ("cjk_sans_font_path", cjk_sans_path), ("cjk_serif_font_path", cjk_serif_path)):
        if candidate is None or not candidate.is_file() or candidate.stat().st_size == 0:
            raise SystemExit(f"{label}_actual_bytes_required:{candidate}")
    font_bytes = font_path.read_bytes()
    sans_font_bytes = cjk_sans_path.read_bytes()
    serif_font_bytes = cjk_serif_path.read_bytes()
    font_locator = str(font_path)
    image_locator = "bundled://templates/general/static/" + _IMAGE_PATH.name
    image_digest = _sha256(_PNG_BYTES)
    font_digest = _sha256(font_bytes)
    sans_font_digest = _sha256(sans_font_bytes)
    serif_font_digest = _sha256(serif_font_bytes)

    from sqlalchemy import select
    from services.database import async_session_maker, dispose_engines, sql_engine
    from models.sql.presentation import PresentationModel
    from models.sql.slide import SlideModel
    from models.sql.user import User
    from models.sql.workspace import WorkspaceConversation, WorkspaceOperation
    from services.authored_vnext.semantic.canonical import canonical_digest
    from services.authored_vnext.semantic.publication import publish_authored_semantic_bundle_graph
    from services.authored_vnext.semantic.typography import NativeFontByteRegistry
    from models.authored_vnext.semantic.contracts import (
        ArtifactPointerV1,
        AssetManifestV1,
        FontManifestV1,
        ResolvedAssetV1,
        ResolvedFontV1,
    )
    from utils.owner_context import reset_current_owner_id, set_current_owner_id

    required_tables = {
        "authored_semantic_artifacts",
        "authored_emission_bundles",
        "authored_deck_emission_manifests",
        "authored_deck_emission_active",
    }
    from sqlalchemy import inspect
    async with sql_engine.connect() as connection:
        actual_tables = await connection.run_sync(lambda sync_connection: set(inspect(sync_connection).get_table_names()))
    missing_tables = sorted(required_tables - actual_tables)
    if missing_tables:
        raise RuntimeError("authored_semantic_schema_missing:" + ",".join(missing_tables))
    font_registry = NativeFontByteRegistry()
    mono_receipt = font_registry.register(font_bytes)
    sans_receipt = font_registry.register(sans_font_bytes)
    serif_receipt = font_registry.register(serif_font_bytes, collection_index=0)
    cjk_mono_receipt = font_registry.register(serif_font_bytes, collection_index=1)
    expected_families = {
        mono_receipt.family.casefold(): "ibm plex mono",
        sans_receipt.family.casefold(): "malgun gothic",
        serif_receipt.family.casefold(): "batang",
        cjk_mono_receipt.family.casefold(): "batangche",
    }
    if any(actual != expected for actual, expected in expected_families.items()):
        raise RuntimeError("authored_semantic_seed_font_family_mismatch")
    if not all(
        receipt.embedding_eligible
        for receipt in (mono_receipt, sans_receipt, serif_receipt, cjk_mono_receipt)
    ):
        raise RuntimeError("authored_semantic_seed_font_embedding_not_eligible")
    font_allowlist_path = _materialize_font_allowlist(
        font_bytes,
        font_digest,
        args.database_url or os.getenv("DATABASE_URL"),
    )
    if owner_id is None:
        async with async_session_maker() as session:
            owner = await session.scalar(
                select(User).where(
                    User.admin_slot == "primary",
                    User.is_active.is_(True),
                    User.deletion_state == "active",
                )
            )
            if owner is None:
                raise RuntimeError("primary_admin_owner_required")
            owner_id = owner.id
    else:
        async with async_session_maker() as session:
            if await session.get(User, owner_id) is None:
                raise RuntimeError("owner_user_not_found:" + str(owner_id))
    assert owner_id is not None
    owner_token = set_current_owner_id(owner_id)
    try:
        async with async_session_maker() as session:
            async with session.begin():
                existing = await session.scalar(select(PresentationModel.id).where(PresentationModel.id == presentation_id))
                if existing is not None:
                    raise RuntimeError("seed_presentation_already_exists")
                user = await session.get(User, owner_id)
                if user is None:
                    raise RuntimeError("owner_user_not_found:" + str(owner_id))
                presentation = PresentationModel(
                    id=presentation_id,
                    owner_id=owner_id,
                    content=f"Authored semantic dual-output deterministic vertical slice ({args.variant})",
                    n_slides=args.slide_count,
                    language="Korean",
                    title="Authored Semantic Vertical Slice",
                    mode="authored",
                    version="v1-standard",
                    lifecycle_status="staging",
                    revision=0,
                    theme={"mode": "authored", "seed": "semantic-vertical-slice-v2", "variant": args.variant},
                )
                session.add(presentation)
                await session.flush()
                conversation = WorkspaceConversation(owner_id=owner_id, presentation_id=presentation_id, next_position=args.slide_count)
                session.add(conversation)
                await session.flush()
                request_payload = {"seed": "authored-semantic-vertical-slice-v2", "slideCount": args.slide_count, "template": "authored", "variant": args.variant}
                operation = WorkspaceOperation(
                    id=operation_id,
                    owner_id=owner_id,
                    owner_scope=str(owner_id),
                    conversation_id=conversation.id,
                    presentation_id=presentation_id,
                    anchor_position=1,
                    kind="presentation.generate",
                    state="running",
                    idempotency_key_hash=hashlib.sha256((str(presentation_id) + ":seed").encode()).hexdigest(),
                    request_hash=hashlib.sha256(json.dumps(request_payload, sort_keys=True).encode()).hexdigest(),
                    request_payload=request_payload,
                    expected_revision=0,
                    attempt_number=1,
                    attempt_token=str(generation_attempt_id),
                    fencing_token=1,
                )
                session.add(operation)
                htmls = (
                    _eight_slide_htmls(args.variant)
                    if args.slide_count == 8
                    else [_html("a"), _html("b")]
                )
                slides = []
                for index, html in enumerate(htmls):
                    slide = SlideModel(
                        id=uuid.uuid4(), owner_id=owner_id, presentation=presentation_id,
                        layout_group="authored", layout="authored:semantic-vertical-slice-v2",
                        index=index, content={"__authored__": True, "seed": "authored-semantic-vertical-slice-v2", "slide": index + 1, "variant": args.variant},
                        html_content=html, properties={"seed": "authored-semantic-vertical-slice-v2", "scope": f"scope-{index + 1:02d}", "variant": args.variant},
                    )
                    session.add(slide)
                    slides.append(slide)
                await session.flush()
                asset_pointer = ArtifactPointerV1(artifactRef=_artifact_ref(image_digest), digest=image_digest, byteLength=len(_PNG_BYTES), mediaType="image/png")
                font_pointer = ArtifactPointerV1(artifactRef=_artifact_ref(font_digest), digest=font_digest, byteLength=len(font_bytes), mediaType="font/ttf")
                sans_font_pointer = ArtifactPointerV1(artifactRef=_artifact_ref(sans_font_digest), digest=sans_font_digest, byteLength=len(sans_font_bytes), mediaType="font/ttf")
                serif_font_pointer = ArtifactPointerV1(artifactRef=_artifact_ref(serif_font_digest), digest=serif_font_digest, byteLength=len(serif_font_bytes), mediaType="font/collection")
                asset_manifest = AssetManifestV1(assets=[ResolvedAssetV1(
                    assetId="asset:vertical-slice-image", artifact=asset_pointer,
                    locatorDigest=_locator_digest(image_locator), resolverVersion="seed-resolver/v1",
                    widthPx=192, heightPx=128, provenance="authored", retentionClass="active",
                )])
                font_manifest = FontManifestV1(fonts=[
                    ResolvedFontV1(
                        faceId="font:ibm-plex-mono-regular", family=mono_receipt.family, face=mono_receipt.face,
                        artifact=font_pointer, locatorDigest=_locator_digest(font_locator), resolverVersion="seed-resolver/v1",
                        embeddingPermitted=mono_receipt.embedding_eligible, subsettingPermitted=mono_receipt.subsetting_permitted, retentionClass="active",
                    ),
                    ResolvedFontV1(
                        faceId="font:malgun-gothic-regular", family=sans_receipt.family, face=sans_receipt.face,
                        artifact=sans_font_pointer, locatorDigest=_locator_digest(str(cjk_sans_path)), resolverVersion="seed-resolver/v1",
                        embeddingPermitted=sans_receipt.embedding_eligible, subsettingPermitted=sans_receipt.subsetting_permitted, retentionClass="active",
                    ),
                    ResolvedFontV1(
                        faceId="font:batang-regular", family=serif_receipt.family, face=serif_receipt.face,
                        artifact=serif_font_pointer, locatorDigest=_locator_digest(str(cjk_serif_path)), resolverVersion="seed-resolver/v1",
                        embeddingPermitted=serif_receipt.embedding_eligible, subsettingPermitted=serif_receipt.subsetting_permitted, retentionClass="active",
                    ),
                    ResolvedFontV1(
                        faceId="font:batangche-regular", family=cjk_mono_receipt.family, face=cjk_mono_receipt.face,
                        artifact=serif_font_pointer, locatorDigest=_locator_digest(str(cjk_serif_path) + "#1"), resolverVersion="seed-resolver/v1",
                        embeddingPermitted=cjk_mono_receipt.embedding_eligible, subsettingPermitted=cjk_mono_receipt.subsetting_permitted, retentionClass="active",
                    ),
                ])
                master_digest = canonical_digest({"schemaVersion": "authored-master-contract/v1", "seed": "semantic-vertical-slice-v2", "variant": args.variant})
                composition_digest = canonical_digest({"schemaVersion": "authored-composition/v1", "seed": "semantic-vertical-slice-v2", "cssIsolation": "slide-root-v1", "variant": args.variant})
                manifest = await publish_authored_semantic_bundle_graph(
                    session,
                    presentation_id=presentation_id,
                    owner_id=owner_id,
                    expected_presentation_revision=0,
                    operation_id=operation_id,
                    generation_attempt_id=generation_attempt_id,
                    slides=slides,
                    htmls=htmls,
                    semantic_drafts=(
                        _eight_slide_semantic_drafts(htmls, image_digest)
                        if args.slide_count == 8
                        else [_semantic_draft("a", image_digest), _semantic_draft("b", image_digest)]
                    ),
                    master_contract_digest=master_digest,
                    composition_digest=composition_digest,
                    slide_asset_manifests=[asset_manifest for _ in slides],
                    slide_font_manifests=[font_manifest for _ in slides],
                    resolved_artifact_bytes={image_digest: _PNG_BYTES, font_digest: font_bytes, sans_font_digest: sans_font_bytes, serif_font_digest: serif_font_bytes},
                    tenant_id=args.tenant_id,
                )
                presentation.revision = 1
                presentation.lifecycle_status = "published"
                operation.state = "succeeded"
                operation.result_revision = 1
                operation.attempt_token = None
                operation.result = {"manifestDigest": manifest.canonical_digest, "presentationRevision": 1, "slideCount": args.slide_count}
                operation.completed_at = datetime.now(timezone.utc)
                await session.flush()
                qids = (
                    [
                        qid
                        for html in htmls
                        for qid in __import__("re").findall(r"data-qid=['\"]([^'\"]+)['\"]", html)
                    ]
                    if args.slide_count == 8
                    else [item["elementId"] for slide in ("a", "b") for item in _semantic_elements(slide, image_digest)]
                )
                semantic_drafts_for_receipt = (
                    _eight_slide_semantic_drafts(htmls, image_digest)
                    if args.slide_count == 8
                    else [_semantic_draft("a", image_digest), _semantic_draft("b", image_digest)]
                )
                semantic_manifest = {
                    "schemaVersion": "authored-semantic-proof/v1",
                    "variant": args.variant,
                    "slides": [
                        {
                            "slideId": f"s{index + 1:02d}-root",
                            "slideIndex": index + 1,
                            "language": __import__("re").search(r"<html[^>]*lang=['\"]([^'\"]+)", html, __import__("re").IGNORECASE).group(1),
                            "html": html,
                            "semanticSource": draft["semanticSource"],
                        }
                        for index, (html, draft) in enumerate(zip(htmls, semantic_drafts_for_receipt, strict=True))
                    ],
                }
                receipt = {
                    "schemaVersion": "authored-semantic-vertical-slice-seed/v3",
                    "variant": args.variant,
                    "presentationId": str(presentation_id),
                    "ownerId": str(owner_id),
                    "presentationRevision": 1,
                    "manifestDigest": manifest.canonical_digest,
                    "operationId": str(operation_id),
                    "generationAttemptId": str(generation_attempt_id),
                    "slideIds": [str(slide.id) for slide in slides],
                    "slideCount": args.slide_count,
                    "semanticManifest": semantic_manifest,
                    "semanticManifestDigest": canonical_digest(semantic_manifest),
                    "qids": qids,
                    "genericCssClass": "fixture-panel" if args.slide_count == 8 else "panel",
                    "assetDigest": image_digest,
                    "fontDigest": font_digest,
                    "fontDigests": [font_digest, sans_font_digest, serif_font_digest],
                    "fontSource": font_locator,
                    "fontAllowlistPath": str(font_allowlist_path),
                    "fontAllowlistDigest": font_digest,
                    "fontRegistryDigest": font_registry.digest,
                    "fontFaceReceipts": [
                        receipt.model_dump(mode="json", by_alias=True)
                        for receipt in (mono_receipt, sans_receipt, serif_receipt, cjk_mono_receipt)
                    ],
                    "expectedRasterDemotionQids": (
                        ["s06-grid", "s07-gradient", "s07-badge"]
                        if args.slide_count == 8
                        else ["a-chart", "a-shape-secondary", "b-chart", "b-shape-secondary"]
                    ),
                    "promotionReady": False,
                    "fullNativeParityReady": False,
                    "independentVnextGenerationReady": False,
                }
            receipt_json = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2)
            if args.receipt_path:
                receipt_path = Path(args.receipt_path).expanduser().resolve()
                receipt_path.parent.mkdir(parents=True, exist_ok=True)
                receipt_path.write_text(receipt_json + "\n", encoding="utf-8")
            print(receipt_json)
            return receipt
    finally:
        reset_current_owner_id(owner_token)
        await dispose_engines()


def main() -> None:
    args = _parse_args()
    try:
        asyncio.run(_seed(args))
    except Exception as error:
        raise SystemExit(f"authored_semantic_vertical_slice_seed_failed:{error}") from error


if __name__ == "__main__":
    main()
