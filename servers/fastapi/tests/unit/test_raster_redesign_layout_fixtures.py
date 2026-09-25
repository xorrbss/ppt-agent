# ruff: noqa: E501  (verbatim Korean source text fixtures)
"""Hand-made redesign analyses modelled on the 28-page raster proposal deck.

Duck-typed (SimpleNamespace) so layout can be exercised before the redesign
fields land in ``raster_contracts``. Boxes are given in source pixels of a
1672x941 slide image and converted to EMU (8750 EMU per pixel).
"""

from __future__ import annotations

from types import SimpleNamespace

EMU = 8750
SOURCE_W, SOURCE_H = 1672 * EMU, 941 * EMU


def region(page, rid, role, box, text=None, *, cells=None, order=None, crop=None, group=None, heading=None):
    x, y, w, h = box
    return SimpleNamespace(
        region_id=f"p{page}-{rid}", page_number=page, role=role,
        bbox=SimpleNamespace(x=x * EMU, y=y * EMU, width=w * EMU, height=h * EMU),
        z_order=0, text=text, table_cells=cells, reading_order=order,
        crop_artifact_ref=crop if crop is not None else (
            "crop" if role in {"screenshot", "logo"} or (role == "table" and cells is None) else None
        ),
        source_pixel_bbox=(x, y, w, h), review_required=False, confidence=0.9, review_reason=None,
        group_id=group, heading_line_count=heading,
    )


def _steps(page, heads, captions, *, top=175, shot_h=420):
    regions = []
    for index, (head, caption) in enumerate(zip(heads, captions, strict=True)):
        x = 30 + index * 405
        regions.append(region(page, f"step{index}", "body", (x, top, 380, 90), head))
        regions.append(region(page, f"shot{index}", "screenshot", (x + 10, top + 95, 360, shot_h)))
        if caption:
            regions.append(region(page, f"cap{index}", "body", (x, top + shot_h + 110, 380, 90), caption))
    return regions


def page_manual(page=1):
    heads = [
        "01 신청자/반출 정보 입력\n신청자 정보와 반출 기본정보를 입력합니다.",
        "02 물품 정보 입력\n반출할 물품의 상세 정보를 입력하고\n실물사진을 첨부합니다.",
        "03 결재 작성\n결재선과 결재 정보를 입력하고\n본문 및 첨부파일을 준비합니다.",
        "04 신청현황 조회\n신청 내역과 진행 상태를 조회하고\n보완 요청 시 수정합니다.",
    ]
    captions = [
        "신청자 정보 및 반출 유형 입력\n신청자, 실반출자, 반출구분, 반입여부 등\n기본 정보를 입력합니다.",
        "물품 상세 정보 및 사진 등록\n"
        "분류, 모델명, 제조번호, 수량 등 물품 정보를\n"
        "입력하고 실물사진을 첨부합니다.",
        "결재선 구성 및 문안 작성\n필수 결재자를 지정하고, 결재 경로와\n본문 내용, 첨부파일을 준비합니다.",
        "신청 내역 및 진행상태 확인\n"
        "신청 내역을 조회하고, 승인/반려/보완 상태를\n"
        "확인하여 필요 시 수정 및 재신청합니다.",
    ]
    regions = [
        region(page, "label", "body", (40, 16, 300, 20), "SAMSUNG DISPLAY Security Portal"),
        region(page, "title", "title", (40, 40, 700, 70), "기존 방식 | 반출 신청 매뉴얼"),
        region(page, "subtitle", "body", (40, 110, 700, 40), "신청정보, 물품정보, 결제, 조회를 개별 화면에서 수행"),
        region(page, "logo", "logo", (1270, 30, 200, 60)),
        region(page, "motto", "body", (1480, 30, 160, 60), "더 안전한 오늘,\n더 스마트한 내일"),
        region(page, "arc", "decoration", (1640, 300, 30, 30)),
        *_steps(page, heads, captions),
        region(page, "pain", "body", (30, 850, 330, 70), "기존 반출 신청의 어려움"),
    ]
    pains = [
        "반출경로·반입여부 직접 판단\n상황에 따라 신청자가 직접 판단해야 함",
        "물품정보와 사진 첨부 반복\n여러 항목을 매번 수기로 입력",
        "결재 문안 수기 작성\n결재 경로와 문안을 직접 작성",
        "진행상태 확인과 보완 처리 분산\n여러 화면을 이동하며 상태 확인",
    ]
    for index, text in enumerate(pains):
        regions.append(region(page, f"pain{index}", "body", (420 + index * 310, 850, 300, 70), text))
    return _page(page, regions)


def page_hero(page=2):
    regions = [
        region(page, "label", "body", (40, 30, 300, 20), "SAMSUNG DISPLAY Security Portal"),
        region(page, "title", "title", (40, 90, 1250, 70), "실제 화면을 그대로 살리면서, 업무는 더 쉽고 빨라집니다."),
        region(page, "subtitle", "body", (40, 175, 1400, 40),
               "보안성검토 시스템 AX 개선은 새로운 화면이 아닌, 기존 화면에서 더 편하게 일하기 위한 제안입니다."),
        region(page, "logo", "logo", (1240, 30, 200, 60)),
        region(page, "card0", "body", (40, 285, 560, 170),
               "업무 흐름 유지\n"
               "익숙한 사용 환경과 화면 구성을 그대로 유지하여\n"
               "추가 학습 없이 자연스럽게 사용할 수 있습니다."),
        region(page, "card1", "body", (40, 460, 560, 170),
               "AX 확장 가능\n"
               "검토 요약, 자동 분류, 유사사례 추천 등\n"
               "필요한 기능만 단계적으로 확장할 수 있어\n"
               "지속적인 업무 혁신이 가능합니다."),
        region(page, "card2", "body", (40, 640, 560, 170),
               "즉시 적용\n"
               "기존 시스템과 연동하여 빠르게 적용 가능하며,\n"
               "담당자의 부담을 최소화하고\n"
               "바로 업무 효율을 높일 수 있습니다."),
        region(page, "shot", "screenshot", (620, 285, 1020, 470)),
        region(page, "banner", "body", (40, 810, 1590, 70),
               "기존 화면은 그대로, 필요한 AI 도우미만 추가하여 더 쉽고 빠른 보안성검토 업무 환경을 제공합니다."),
        region(page, "footer", "body", (40, 905, 900, 25),
               "SAMSUNG DISPLAY | 사람과 기술로 더 안전한 디스플레이의 미래를 만듭니다. 07"),
    ]
    return _page(page, regions)


def page_report(page=3):
    heads = [
        "01 조회 결과 선택\n활용할 수치와 지표 선택",
        "02 AI 보고서 초안\n요약 문안 자동 생성",
        "03 차트·표 정리\n회의용 시각화 자동 구성",
        "04 공유·활용\n보고·브리핑·후속 질문 연결",
    ]
    regions = [
        region(page, "title", "title", (40, 40, 800, 70), "개선 방식 | 보고서·브리핑 자동 생성"),
        region(page, "subtitle", "body", (40, 110, 800, 40), "조회 결과를 회의용 보고서와 브리핑 자료로 바로 연결"),
        region(page, "logo", "logo", (1240, 30, 200, 60)),
        *_steps(page, heads, [None] * 4, top=170, shot_h=500),
    ]
    points = [
        "보고 준비 자동화 포인트",
        "요약 자동 생성\n핵심 내용 자동 요약",
        "차트 자동 정리\n회의용 시각화 구성",
        "반복 보고 단축\n정기 보고서 빠른 생성",
        "문안 일관성 확보\n표준 문장 기반 자동 작성",
    ]
    for index, text in enumerate(points):
        regions.append(region(page, f"point{index}", "body", (30 + index * 330, 790, 310, 60), text))
    regions.append(region(page, "closing", "body", (30, 870, 1100, 50),
                          "통계 확인 이후의 정리·보고 업무를 줄여, 의사결정용 자료 준비 시간을 단축"))
    return _page(page, regions)


def page_table(page=4):
    cells = (
        ("구분", "기존 방식", "개선 방식", "개선 효과"),
        ("신청서 작성 시간", "평균 25분", "평균 8분", "68%"),
        ("결재 반려율", "18.5%", "6.2%", "-12.3%p"),
        ("월간 처리 건수", "1,240건", "1,980건", "1.6배"),
        ("보완 요청 응답", "2.4일", "0.8일", "67%"),
    )
    regions = [
        region(page, "title", "title", (40, 40, 800, 70), "도입 효과 | 정량 지표 비교"),
        region(page, "intro", "body", (40, 130, 1500, 60),
               "2026년 1분기 시범 운영(3개 부서, 412건) 결과를 기준으로 산출한 수치입니다."),
        region(page, "table", "table", (40, 220, 1580, 420), cells=cells),
        region(page, "note", "body", (40, 680, 1580, 80),
               "※ 개선 효과는 시범 운영 기간 평균값이며, 전사 확대 시 부서별 편차가 발생할 수 있습니다.\n"
               "※ 처리 건수는 반출·반입 신청을 합산한 값입니다."),
        region(page, "raw", "table", (40, 780, 700, 140)),
    ]
    return _page(page, regions)


def page_text_only(page=5):
    blocks = [
        "추진 배경\n보안성검토 요청 증가로 담당자 1인당 월 처리 건수가 2년간 42% 늘었습니다.",
        "현황 문제\n동일한 정보를 여러 화면에 반복 입력하고, 결재 문안을 매번 수기로 작성합니다.",
        "개선 방향\n기존 화면은 유지하고 AI 도우미를 단계적으로 추가합니다.",
        "기대 효과\n작성 시간 단축, 반려율 감소, 보고 자료 준비 자동화",
        "추진 일정\n"
        "1단계 파일럿(2026.10) → 2단계 확산(2027.01) → 3단계 고도화(2027.06)",
        "협조 요청\n현업 부서 검토 인력 2명과 테스트 데이터 제공을 요청드립니다.",
    ]
    regions = [region(page, "title", "title", (40, 40, 800, 70), "추진 개요 및 요청 사항")]
    for index, text in enumerate(blocks):
        box = (40 + (index % 2) * 800, 150 + (index // 2) * 250, 760, 220)
        regions.append(region(page, f"b{index}", "body", box, text))
    return _page(page, regions)


def _page(number, regions):
    reference = region(number, "reference", "screenshot", (0, 0, 1672, 941))
    return SimpleNamespace(
        page_number=number, regions=tuple(regions), ocr_inventory=(), render_mode="redesign",
        approval_method="automatic_redesign", full_page_region=None, reference_region=reference,
        full_page_preserve_acknowledgements=(),
    )


def analysis(*pages):
    return SimpleNamespace(
        source=SimpleNamespace(canvas_width_emu=SOURCE_W, canvas_height_emu=SOURCE_H, page_count=len(pages)),
        target_canvas=SimpleNamespace(format="A4", orientation="landscape", width_units=1188,
                                      height_units=840, emu_per_unit=9000),
        pages=tuple(pages), unresolved=(),
    )


def deck():
    return analysis(page_manual(1), page_hero(2), page_report(3), page_table(4), page_text_only(5))


# --- Semantic structure (vision-LLM grouping): group_id + heading_line_count ---


def semantic_steps(page=6):
    """Four step cards (icon, 2-line heading+body, capture) plus a pain-point row."""
    regions = [
        region(page, "kicker", "body", (40, 16, 300, 20), "SAMSUNG DISPLAY Security Portal"),
        region(page, "title", "title", (40, 40, 700, 70), "기존 방식 | 반출 신청 매뉴얼"),
        region(page, "subtitle", "body", (40, 112, 700, 30), "신청정보, 물품정보, 결재, 조회를 개별 화면에서 수행"),
        region(page, "brand", "logo", (1270, 30, 200, 60)),
    ]
    steps = [
        ("01 신청자/반출 정보 입력", "신청자 정보와 반출 기본정보를 입력합니다.\n신청자, 실반출자, 반출구분, 반입여부 등 기본 정보를 입력합니다."),
        ("02 물품 정보 입력", "반출할 물품의 상세 정보를 입력하고 실물사진을 첨부합니다.\n분류, 모델명, 제조번호, 수량 등을 입력합니다."),
        ("03 결재 작성", "결재선과 결재 정보를 입력하고 본문 및 첨부파일을 준비합니다."),
        ("04 신청현황 조회", "신청 내역과 진행 상태를 조회하고 보완 요청 시 수정합니다.\n승인/반려/보완 상태를 확인합니다."),
    ]
    for index, (head, body) in enumerate(steps):
        x = 30 + index * 405
        group = f"step{index}"
        regions.append(region(page, f"icon{index}", "logo", (x + 10, 175, 40, 40), group=group))
        regions.append(region(page, f"text{index}", "body", (x + 60, 175, 320, 120), f"{head}\n{body}",
                              group=group, heading=1))
        regions.append(region(page, f"shot{index}", "screenshot", (x + 10, 300, 360, 420), group=group))
    pains = ["반출경로·반입여부 직접 판단\n상황에 따라 신청자가 직접 판단해야 함",
             "물품정보와 사진 첨부 반복\n여러 항목을 매번 수기로 입력",
             "결재 문안 수기 작성\n결재 경로와 문안을 직접 작성"]
    for index, text in enumerate(pains):
        group = f"pain{index}"
        regions.append(region(page, f"pain-icon{index}", "logo", (40 + index * 540, 790, 36, 36), group=group))
        regions.append(region(page, f"pain{index}", "body", (90 + index * 540, 790, 440, 70), text,
                              group=group, heading=1))
    return _page(page, _ordered(regions))


def semantic_table(page=7):
    """Borderless KPI table as table_cells plus three benefit cards."""
    cells = (
        ("업무 항목", "AS-IS 처리시간", "TO-BE 처리시간", "단축률", "연간 처리량", "연간 절감시간"),
        ("내방/차량 신청", "10분", "1분", "90%", "24,000건", "3,600시간/년"),
        ("반출 신청", "15분", "4분", "73%", "12,000건", "2,200시간/년"),
        ("신청현황 조회", "5분", "30초", "90%", "60,000건", "4,500시간/년"),
    )
    regions = [
        region(page, "title", "title", (55, 65, 630, 70), "통합보안포털 도입 효과"),
        region(page, "subtitle", "body", (55, 150, 1030, 50),
               "신청·조회 업무를 AX 방식으로 전환했을 때의 시간 단축과 운영 효율 개선"),
        region(page, "kpi", "table", (45, 215, 1580, 420), cells=cells),
        region(page, "total", "body", (550, 655, 560, 70), "연간 절감시간 | 10,300시간"),
    ]
    benefits = [("신청 자동화", "AI가 상황에 맞는 신청을 자동으로 생성하고 후속 절차까지 안내"),
                ("문의 감소", "AI 보안비서가 24시간 응답하여 반복 문의를 줄입니다."),
                ("반복 입력 최소화", "기존 정보 재활용으로 불필요한 중복 입력을 없앱니다.")]
    for index, (head, body) in enumerate(benefits):
        group = f"benefit{index}"
        regions.append(region(page, f"b-icon{index}", "logo", (95 + index * 550, 745, 50, 50), group=group))
        regions.append(region(page, f"b{index}", "body", (200 + index * 550, 750, 300, 80), f"{head}\n{body}",
                              group=group, heading=1))
    regions.append(region(page, "note", "body", (40, 850, 360, 25), "※ 예시 수치이며 실제 운영량 기준에 따라 조정 가능합니다."))
    return _page(page, _ordered(regions))


def semantic_hero(page=8):
    """Three feature cards beside one large standalone capture, then a banner."""
    regions = [
        region(page, "title", "title", (40, 90, 1250, 70), "실제 화면을 그대로 살리면서, 업무는 더 쉽고 빨라집니다."),
        region(page, "subtitle", "body", (40, 175, 1400, 40),
               "보안성검토 시스템 AX 개선은 새로운 화면이 아닌, 기존 화면에서 더 편하게 일하기 위한 제안입니다."),
    ]
    cards = [("업무 흐름 유지", "익숙한 사용 환경과 화면 구성을 그대로 유지하여 추가 학습 없이 자연스럽게 사용할 수 있습니다."),
             ("AX 확장 가능", "검토 요약, 자동 분류, 유사사례 추천 등 필요한 기능만 단계적으로 확장할 수 있습니다."),
             ("즉시 적용", "기존 시스템과 연동하여 빠르게 적용 가능하며, 담당자의 부담을 최소화합니다.")]
    for index, (head, body) in enumerate(cards):
        group = f"card{index}"
        regions.append(region(page, f"c-icon{index}", "logo", (50, 290 + index * 175, 56, 56), group=group))
        regions.append(region(page, f"c{index}", "body", (120, 285 + index * 175, 470, 150), f"{head}\n{body}",
                              group=group, heading=1))
    regions.append(region(page, "shot", "screenshot", (620, 285, 1020, 470)))
    regions.append(region(page, "banner", "body", (40, 810, 1590, 70),
                          "기존 화면은 그대로, 필요한 AI 도우미만 추가하여 더 쉽고 빠른 보안성검토 업무 환경을 제공합니다."))
    return _page(page, _ordered(regions))


def _ordered(regions):
    for order, item in enumerate(regions):
        item.reading_order = order
    return regions


def semantic_deck():
    return analysis(semantic_steps(1), semantic_table(2), semantic_hero(3))