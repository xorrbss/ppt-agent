"""Build deck4: a synthetic Korean business-proposal PPTX (fictional company,
no real customer names) covering the coverage gap the coordinator flagged
(Korean + native tables). 10 slides:
  1 title, 2 agenda, 3 two-level-bullet, 4-6 native tables (4x4..6x5, %, 억원,
  시간/년, dates, negative %p), 7-8 screenshot-like PIL mockup + caption,
  9 KPI card (large numerals), 10 dense 3-column.

Font: Malgun Gothic throughout, sizes varied 10-32pt (dense text at 10-12pt).

Run with: uv run python build_deck4.py <out_dir>

Nothing under decks/deck4_korean_tables/ is checked into the repo: this
script fully regenerates deck4_source.pptx from code, then
build_image_only_deck.py turns that into ground_truth.json + source_pages/ +
image_only.pptx (see ../README.md "Rebuilding deck assets").
"""
from __future__ import annotations

import sys
from pathlib import Path

from pptx import Presentation
from pptx.util import Pt, Cm
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from PIL import Image, ImageDraw, ImageFont

FONT_NAME = "맑은 고딕"  # Malgun Gothic
MALGUN_REGULAR = r"C:\Windows\Fonts\malgun.ttf"
MALGUN_BOLD = r"C:\Windows\Fonts\malgunbd.ttf"

NAVY = RGBColor(0x1B, 0x2A, 0x4A)
BLUE = RGBColor(0x2D, 0x5B, 0xE0)
GRAY = RGBColor(0x5A, 0x5A, 0x5A)
LIGHT_BG = RGBColor(0xF2, 0xF4, 0xF8)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
RED = RGBColor(0xC0, 0x30, 0x30)
DARK_GRAY = RGBColor(0x30, 0x30, 0x30)

SLIDE_W = Cm(33.867)  # 16:9 widescreen (matches 12192000 EMU)
SLIDE_H = Cm(19.05)


def _font(size: int, bold: bool = False):
    path = MALGUN_BOLD if bold else MALGUN_REGULAR
    return ImageFont.truetype(path, size)


def build_mockup_png(path: Path, title: str, menu_items: list[str], rows: list[tuple[str, str, str]]) -> None:
    """A fake UI screenshot: header bar + left nav + a small data table, all
    with small Korean labels, meant to visually resemble a real app screenshot
    (like the ones in the large real-world reference deck this coverage gap
    was found against)."""
    W, H = 1600, 900
    img = Image.new("RGB", (W, H), (245, 247, 250))
    d = ImageDraw.Draw(img)
    # header
    d.rectangle([0, 0, W, 64], fill=(27, 42, 74))
    d.text((24, 18), title, font=_font(22, bold=True), fill=(255, 255, 255))
    d.text((W - 220, 22), "관리자 | 로그아웃", font=_font(14), fill=(210, 216, 230))
    # left nav
    d.rectangle([0, 64, 220, H], fill=(238, 240, 245))
    for i, item in enumerate(menu_items):
        y = 100 + i * 44
        if i == 0:
            d.rectangle([0, y - 8, 220, y + 28], fill=(45, 91, 224))
            d.text((24, y), item, font=_font(15), fill=(255, 255, 255))
        else:
            d.text((24, y), item, font=_font(15), fill=(60, 60, 60))
    # content: small "table"
    x0, y0 = 260, 110
    headers = ["항목", "값", "상태"]
    col_w = [220, 220, 160]
    d.rectangle([x0, y0, x0 + sum(col_w), y0 + 40], fill=(220, 226, 238))
    cx = x0
    for h, w in zip(headers, col_w, strict=False):
        d.text((cx + 12, y0 + 10), h, font=_font(14, bold=True), fill=(30, 30, 30))
        cx += w
    for r, row in enumerate(rows):
        ry = y0 + 40 + r * 38
        d.rectangle([x0, ry, x0 + sum(col_w), ry + 38],
                    outline=(210, 214, 222), fill=(255, 255, 255) if r % 2 == 0 else (248, 249, 252))
        cx = x0
        for val, w in zip(row, col_w, strict=False):
            d.text((cx + 12, ry + 9), val, font=_font(13), fill=(50, 50, 50))
            cx += w
    # a small chart-ish bar group
    bx0, by0 = 260, 420
    d.text((bx0, by0 - 30), "월별 처리 현황", font=_font(15, bold=True), fill=(30, 30, 30))
    import random
    random.seed(7)
    for i in range(8):
        bh = random.randint(40, 220)
        d.rectangle([bx0 + i * 60, by0 + 240 - bh, bx0 + i * 60 + 40, by0 + 240], fill=(45, 91, 224))
        d.text((bx0 + i * 60, by0 + 246), f"{i+1}월", font=_font(11), fill=(80, 80, 80))
    img.save(path)


def add_text(slide, left, top, width, height, text, size=18, bold=False, color=NAVY, align=PP_ALIGN.LEFT,
             font_name=FONT_NAME, anchor=None, wrap=True):
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = wrap
    if anchor is not None:
        tf.vertical_anchor = anchor
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = font_name
    run.font.color.rgb = color
    return box


def add_bulleted(slide, left, top, width, height, items: list[tuple[int, str]], size=16, color=DARK_GRAY):
    """items: list of (level, text); level 0 = top bullet, 1 = sub-bullet."""
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    first = True
    for level, text in items:
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.level = level
        prefix = "• " if level == 0 else "‒ "
        run = p.add_run()
        run.text = prefix + text
        run.font.size = Pt(size if level == 0 else size - 2)
        run.font.name = FONT_NAME
        run.font.color.rgb = color if level == 0 else GRAY
    return box


def add_table(slide, left, top, width, height, headers: list[str], rows: list[list[str]], font_size=13):
    n_rows, n_cols = len(rows) + 1, len(headers)
    gframe = slide.shapes.add_table(n_rows, n_cols, left, top, width, height)
    table = gframe.table
    for c, h in enumerate(headers):
        cell = table.cell(0, c)
        cell.text = h
        for p in cell.text_frame.paragraphs:
            for r in p.runs:
                r.font.bold = True
                r.font.size = Pt(font_size)
                r.font.name = FONT_NAME
    for ri, row in enumerate(rows, start=1):
        for ci, val in enumerate(row):
            cell = table.cell(ri, ci)
            cell.text = val
            for p in cell.text_frame.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(font_size)
                    r.font.name = FONT_NAME
    return table


def build(out_dir: Path) -> Path:
    assets = out_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    mock1 = assets / "mockup1.png"
    mock2 = assets / "mockup2.png"
    build_mockup_png(
        mock1, "스마트테크 업무포털",
        ["대시보드", "신청 관리", "통계", "설정"],
        [("전체 신청 건수", "1,284건", "정상"), ("처리 대기", "37건", "주의"), ("평균 처리 시간", "4.2분", "정상")],
    )
    build_mockup_png(
        mock2, "스마트테크 보고서 뷰어",
        ["보고서 목록", "즐겨찾기", "공유함", "휴지통"],
        [("2026년 3분기 실적", "완료", "승인"), ("부서별 비교 리포트", "검토중", "대기"), ("연간 예산 요약", "완료", "승인")],
    )

    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H
    blank = prs.slide_layouts[6]

    # 1. Title
    s = prs.slides.add_slide(blank)
    s.shapes.add_shape(1, 0, 0, SLIDE_W, Cm(2.2)).fill.solid()
    s.shapes[-1].fill.fore_color.rgb = NAVY
    s.shapes[-1].line.fill.background()
    add_text(s, Cm(2), Cm(6.5), Cm(28), Cm(2.5), "스마트테크 주식회사", size=20, bold=True, color=BLUE)
    add_text(s, Cm(2), Cm(8.5), Cm(28), Cm(3), "AI 기반 업무자동화 도입 제안서", size=32, bold=True, color=NAVY)
    add_text(s, Cm(2), Cm(11.2), Cm(28), Cm(1.5), "가상 시나리오 · 테스트용 합성 자료 · 2026. 09. 25", size=14, color=GRAY)

    # 2. Agenda
    s = prs.slides.add_slide(blank)
    add_text(s, Cm(1.5), Cm(0.8), Cm(20), Cm(1.5), "목차", size=26, bold=True, color=NAVY)
    agenda_items = [
        "1. 추진 배경 및 현황",
        "2. 부서별 처리 시간 비교",
        "3. 도입 시나리오별 효과",
        "4. 연도별 매출 및 성장률",
        "5. 시스템 화면 소개",
        "6. 보고서 뷰어 소개",
        "7. 핵심 성과 지표(KPI)",
        "8. 세부 실행 계획 및 유의사항",
    ]
    add_bulleted(s, Cm(2), Cm(2.8), Cm(26), Cm(14), [(0, it) for it in agenda_items], size=18)

    # 3. Two-level bullets
    s = prs.slides.add_slide(blank)
    add_text(s, Cm(1.5), Cm(0.8), Cm(24), Cm(1.5), "추진 배경 및 현황", size=26, bold=True, color=NAVY)
    items = [
        (0, "반복적인 수기 입력 업무로 처리 지연이 발생하고 있습니다"),
        (1, "부서별 평균 처리 시간이 표준 대비 최대 3.5배 증가"),
        (1, "동일 데이터를 여러 화면에 중복 입력하는 사례 다수"),
        (0, "보고서 작성에 소요되는 시간이 과도합니다"),
        (1, "월간 보고서 작성에 평균 4시간 소요"),
        (1, "취합·검토 과정에서 재작업 비율 18.7%"),
        (0, "AI 기반 자동화로 처리 시간과 오류율을 동시에 낮출 수 있습니다"),
        (1, "파일럿 부서 기준 처리 시간 -62.4% 단축"),
        (1, "입력 오류 건수 연간 1,150건 → 210건으로 감소"),
    ]
    add_bulleted(s, Cm(2), Cm(2.8), Cm(28), Cm(14), items, size=16)

    # 4. Table 1: 4x4 - 연도별 매출 현황 (억원, %, 음수 포함)
    s = prs.slides.add_slide(blank)
    add_text(s, Cm(1.5), Cm(0.8), Cm(24), Cm(1.5), "연도별 매출 및 성장률", size=26, bold=True, color=NAVY)
    add_table(
        s, Cm(2), Cm(2.8), Cm(29), Cm(6),
        headers=["연도", "매출(억원)", "성장률(%)", "비고"],
        rows=[
            ["2023", "482억원", "+6.2%", "안정적 성장"],
            ["2024", "515억원", "+6.8%", "신규 사업 개시"],
            ["2025", "451억원", "-12.3%p", "시장 위축 영향"],
            ["2026(E)", "530억원", "+17.5%", "AI 전환 효과 반영"],
        ],
    )
    add_text(s, Cm(2), Cm(9.5), Cm(28), Cm(1.5), "※ 2025년 매출은 전년 대비 -12.3%p 감소, 2026년은 AI 도입 효과를 반영한 예상치입니다.",
              size=13, color=GRAY)

    # 5. Table 2: 6x5 - 부서별 처리 시간 비교
    s = prs.slides.add_slide(blank)
    add_text(s, Cm(1.5), Cm(0.8), Cm(24), Cm(1.5), "부서별 처리 시간 비교", size=26, bold=True, color=NAVY)
    add_table(
        s, Cm(1.5), Cm(2.8), Cm(30.5), Cm(9),
        headers=["부서", "기존(분)", "개선(분)", "단축률(%)", "연간 처리량", "연간 절감(시간/년)"],
        rows=[
            ["총무팀", "45", "12", "73.3%", "8,200건", "4,510시간/년"],
            ["영업지원팀", "30", "6", "80.0%", "12,400건", "4,960시간/년"],
            ["재무팀", "60", "20", "66.7%", "3,100건", "2,067시간/년"],
            ["인사팀", "25", "9", "64.0%", "5,600건", "1,493시간/년"],
            ["IT운영팀", "40", "11", "72.5%", "2,900건", "1,401시간/년"],
        ],
        font_size=13,
    )

    # 6. Table 3: 6x5 - 도입 시나리오별 효과 (날짜 포함)
    s = prs.slides.add_slide(blank)
    add_text(s, Cm(1.5), Cm(0.8), Cm(24), Cm(1.5), "도입 시나리오별 효과", size=26, bold=True, color=NAVY)
    add_table(
        s, Cm(1.5), Cm(2.8), Cm(30.5), Cm(8),
        headers=["시나리오", "적용 시작일", "활용률", "절감 시간", "비용 절감"],
        rows=[
            ["보수적", "2026-10-01", "50%", "9,860시간", "약 3.9억원"],
            ["기준", "2026-11-15", "75%", "14,790시간", "약 5.9억원"],
            ["확대", "2027-01-01", "100%", "19,720시간", "약 7.9억원"],
            ["지연", "2027-03-01", "60%", "11,832시간", "약 4.7억원"],
            ["최소", "2026-10-01", "25%", "4,930시간", "약 2.0억원"],
        ],
        font_size=13,
    )

    # 7. Screenshot 1
    s = prs.slides.add_slide(blank)
    add_text(s, Cm(1.5), Cm(0.8), Cm(24), Cm(1.5), "시스템 화면 소개 · 업무포털", size=24, bold=True, color=NAVY)
    s.shapes.add_picture(str(mock1), Cm(4.5), Cm(2.6), width=Cm(24), height=Cm(13.5))
    add_text(s, Cm(2), Cm(16.4), Cm(30), Cm(2),
              "신청 현황을 한눈에 확인할 수 있는 통합 대시보드입니다. 전체 1,284건 중 처리 대기 37건, 평균 처리 시간 4.2분입니다.",
              size=14, color=GRAY)

    # 8. Screenshot 2
    s = prs.slides.add_slide(blank)
    add_text(s, Cm(1.5), Cm(0.8), Cm(24), Cm(1.5), "시스템 화면 소개 · 보고서 뷰어", size=24, bold=True, color=NAVY)
    s.shapes.add_picture(str(mock2), Cm(4.5), Cm(2.6), width=Cm(24), height=Cm(13.5))
    add_text(s, Cm(2), Cm(16.4), Cm(30), Cm(2),
              "보고서 승인 현황을 관리하는 화면입니다. 분기 실적 보고서는 승인 완료, 부서별 비교 리포트는 검토 중입니다.",
              size=14, color=GRAY)

    # 9. KPI card
    s = prs.slides.add_slide(blank)
    add_text(s, Cm(1.5), Cm(0.8), Cm(24), Cm(1.5), "핵심 성과 지표(KPI)", size=26, bold=True, color=NAVY)
    kpis = [("연간 절감 시간", "10,300시간"), ("업무 자동화율", "90%"), ("오류 감소율", "81.7%"), ("연간 비용 절감", "7.9억원")]
    card_w, card_h, gap = Cm(7), Cm(6), Cm(0.6)
    x = Cm(1.7)
    for label, value in kpis:
        box = s.shapes.add_shape(1, x, Cm(3), card_w, card_h)
        box.fill.solid()
        box.fill.fore_color.rgb = LIGHT_BG
        box.line.color.rgb = BLUE
        add_text(s, x + Cm(0.3), Cm(3.4), card_w - Cm(0.6), Cm(1.2), label, size=14, color=GRAY)
        add_text(s, x + Cm(0.3), Cm(4.6), card_w - Cm(0.6), Cm(3), value, size=30, bold=True, color=BLUE)
        x += card_w + gap

    # 10. Dense 3-column
    s = prs.slides.add_slide(blank)
    add_text(s, Cm(1.5), Cm(0.8), Cm(24), Cm(1.5), "세부 실행 계획 및 유의사항", size=24, bold=True, color=NAVY)
    columns = [
        ("실행 단계", [
            "1단계(2026.10): 파일럿 부서 선정 및 초기 구축",
            "2단계(2026.11~12): 검증 완료 부서 순차 확대",
            "3단계(2027.01~): 전사 확대 및 고도화",
            "각 단계별 종료 시 정량 지표 재검토를 수행합니다.",
            "단계 전환은 담당 조직의 승인을 거쳐 진행됩니다.",
        ]),
        ("리스크 관리", [
            "데이터 이관 중 정합성 오류 발생 가능성 존재",
            "기존 시스템과의 연동 구간에서 지연 발생 가능",
            "사용자 교육 미이수 시 초기 적응 기간 연장 우려",
            "보안 정책 변경 시 재검토 절차가 필요합니다",
            "외부 감사 대응을 위한 로그 보존 정책을 적용합니다",
        ]),
        ("주의사항", [
            "본 자료의 수치는 예시 산정치이며 실제 운영 데이터에 따라 조정될 수 있습니다",
            "부서명 및 수치는 가상 시나리오로 실제 조직과 무관합니다",
            "정책·법규 변경 시 계획이 변경될 수 있습니다",
            "세부 일정은 협의를 통해 최종 확정됩니다",
            "본 문서는 테스트 목적의 합성 데이터입니다",
        ]),
    ]
    col_w = Cm(10.2)
    x = Cm(1.5)
    for title, lines in columns:
        add_text(s, x, Cm(2.6), col_w, Cm(0.9), title, size=15, bold=True, color=BLUE)
        box = s.shapes.add_textbox(x, Cm(3.5), col_w, Cm(13))
        tf = box.text_frame
        tf.word_wrap = True
        first = True
        for line in lines:
            p = tf.paragraphs[0] if first else tf.add_paragraph()
            first = False
            run = p.add_run()
            run.text = "· " + line
            run.font.size = Pt(11)
            run.font.name = FONT_NAME
            run.font.color.rgb = RGBColor(0x40, 0x40, 0x40)
        x += col_w + Cm(0.2)

    out_path = out_dir / "deck4_source.pptx"
    prs.save(str(out_path))
    return out_path


if __name__ == "__main__":
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    p = build(out_dir)
    print(f"wrote {p} ({p.stat().st_size} bytes), slides={len(Presentation(str(p)).slides)}")
