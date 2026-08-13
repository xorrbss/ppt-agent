"""Focused canonical fixture for authored vNext Master tests."""

from __future__ import annotations

from copy import deepcopy


ZERO_DIGEST = "sha256:" + ("0" * 64)


def _ownership(*, locked: bool, writable: bool) -> dict[str, bool]:
    return {"locked": locked, "modelWritable": writable}


def _bounds(x: float, y: float, width: float, height: float) -> dict[str, float]:
    return {"x": x, "y": y, "width": width, "height": height}


def _style(
    font_pt: float,
    min_font_pt: float,
    max_lines: int,
    color: str = "textPrimary",
) -> dict[str, object]:
    return {
        "fontToken": "eastAsian",
        "colorToken": color,
        "fontPt": font_pt,
        "minFontPt": min_font_pt,
        "lineHeight": 1.12,
        "fontWeight": 700,
        "maxLines": max_lines,
        "margins": {
            "leftPt": 7.2,
            "rightPt": 7.2,
            "topPt": 3.6,
            "bottomPt": 3.6,
        },
    }


def master_contract_payload() -> dict[str, object]:
    furniture_ownership = _ownership(locked=True, writable=False)
    slot_ownership = _ownership(locked=False, writable=True)
    fallbacks = [
        "Noto Sans CJK KR",
        "Noto Sans KR",
        "Arial Unicode MS",
        "Arial",
        "sans-serif",
    ]
    contract: dict[str, object] = {
        "contractVersion": "deckmaster-contract/v1",
        "masterId": "master:authored-vnext-test",
        "semanticVersion": "1.0.0",
        "name": "Authored vNext Master",
        "canvas": {
            "widthPx": 1280,
            "heightPx": 720,
            "locale": "ko-KR",
            "safeArea": _bounds(60.0, 80.0, 1160.0, 560.0),
        },
        "brandAssets": {"logo": None, "wordmark": None},
        "tokens": {
            "fonts": {
                "latin": "Malgun Gothic",
                "eastAsian": "Malgun Gothic",
                "complexScript": "Malgun Gothic",
                "fallbacks": fallbacks,
            },
            "colors": {
                "background": "#F7F9FC",
                "surface": "#FFFFFF",
                "textPrimary": "#0B1830",
                "textSecondary": "#486078",
                "accent": "#1463FF",
                "divider": "#C9D5E6",
                "warning": "#E85D75",
            },
        },
        "fontManifest": {
            "primary": "Malgun Gothic",
            "fallbacks": fallbacks,
            "windowsFallback": "Malgun Gothic",
            "themeScripts": {
                "latin": "Malgun Gothic",
                "hang": "Malgun Gothic",
                "jpan": "Malgun Gothic",
                "hans": "Malgun Gothic",
                "hant": "Malgun Gothic",
            },
            "files": [
                {
                    "style": "regular",
                    "path": "C:/Windows/Fonts/malgun.ttf",
                    "version": "6.68",
                    "sizeBytes": 13457164,
                    "sha256": "0" * 64,
                }
            ],
            "digest": ZERO_DIGEST,
        },
        "furniture": {
            "logo": {
                "id": "furniture:logo",
                "kind": "logo",
                "bounds": _bounds(76.0, 30.0, 32.0, 32.0),
                "ownership": furniture_ownership,
                "visibility": "always",
            },
            "header": {
                "id": "furniture:header",
                "kind": "text",
                "bounds": _bounds(120.0, 35.0, 440.0, 24.0),
                "ownership": furniture_ownership,
                "visibility": "always",
                "text": "PRESENTON · AUTHORED vNEXT",
                "fill": "#486078",
            },
            "footer": {
                "id": "furniture:footer",
                "kind": "text",
                "bounds": _bounds(76.0, 684.0, 650.0, 22.0),
                "ownership": furniture_ownership,
                "visibility": "always",
                "text": "DeckMasterContract · SlideSpec v2",
                "fill": "#486078",
            },
            "divider": {
                "id": "furniture:divider",
                "kind": "line",
                "bounds": _bounds(76.0, 672.0, 1128.0, 1.0),
                "ownership": furniture_ownership,
                "visibility": "always",
                "stroke": "#C9D5E6",
            },
            "pageNumber": {
                "id": "furniture:page-number",
                "kind": "pageNumber",
                "bounds": _bounds(1160.0, 684.0, 44.0, 22.0),
                "ownership": furniture_ownership,
                "visibility": "exceptCover",
                "text": "1",
                "fill": "#486078",
            },
        },
        "semanticStyles": {
            "eyebrow": _style(14.0, 12.0, 1, "accent"),
            "title": _style(38.0, 28.0, 3),
            "body": _style(20.0, 16.0, 8, "textSecondary"),
            "note": _style(14.0, 12.0, 4, "textSecondary"),
            "rightCallout": _style(18.0, 14.0, 5, "accent"),
            "warning": _style(18.0, 14.0, 5, "warning"),
            "caption": _style(12.0, 10.0, 3, "textSecondary"),
            "source": _style(10.0, 8.0, 3, "textSecondary"),
        },
        "layouts": {
            "cover": {
                "id": "layout:cover",
                "name": "Cover",
                "kind": "cover",
                "slots": [
                    {
                        "id": "slot:cover-title",
                        "role": "title",
                        "placeholderIndex": 0,
                        "bounds": _bounds(76.0, 180.0, 1128.0, 180.0),
                        "ownership": slot_ownership,
                        "style": "title",
                    },
                    {
                        "id": "slot:cover-body",
                        "role": "body",
                        "placeholderIndex": 1,
                        "bounds": _bounds(86.0, 390.0, 1090.0, 120.0),
                        "ownership": slot_ownership,
                        "style": "body",
                    },
                ],
            },
            "section": {
                "id": "layout:section",
                "name": "Section",
                "kind": "section",
                "slots": [
                    {
                        "id": "slot:section-title",
                        "role": "title",
                        "placeholderIndex": 0,
                        "bounds": _bounds(76.0, 160.0, 1128.0, 160.0),
                        "ownership": slot_ownership,
                        "style": "title",
                    },
                    {
                        "id": "slot:section-body",
                        "role": "body",
                        "placeholderIndex": 1,
                        "bounds": _bounds(86.0, 350.0, 1090.0, 160.0),
                        "ownership": slot_ownership,
                        "style": "body",
                    },
                ],
            },
            "standard": {
                "id": "layout:standard-title",
                "name": "Standard Title",
                "kind": "standard",
                "slots": [
                    {
                        "id": "slot:standard-title",
                        "role": "title",
                        "placeholderIndex": 0,
                        "bounds": _bounds(76.0, 104.0, 1128.0, 126.0),
                        "ownership": slot_ownership,
                        "style": "title",
                    },
                    {
                        "id": "slot:standard-body",
                        "role": "body",
                        "placeholderIndex": 1,
                        "bounds": _bounds(86.0, 270.0, 1090.0, 300.0),
                        "ownership": slot_ownership,
                        "style": "body",
                    },
                ],
            },
            "longTitle": {
                "id": "layout:long-title",
                "name": "Long Title",
                "kind": "longTitle",
                "slots": [
                    {
                        "id": "slot:long-title",
                        "role": "title",
                        "placeholderIndex": 0,
                        "bounds": _bounds(76.0, 104.0, 1140.0, 250.0),
                        "ownership": slot_ownership,
                        "style": "title",
                    },
                    {
                        "id": "slot:long-body",
                        "role": "body",
                        "placeholderIndex": 1,
                        "bounds": _bounds(86.0, 404.0, 1090.0, 200.0),
                        "ownership": slot_ownership,
                        "style": "body",
                    },
                ],
            },
        },
        "fitPolicy": {
            "preferredFontPt": 38.0,
            "minimumFontPt": 28.0,
            "standardMaxLines": 2,
            "longTitleMaxLines": 3,
            "promotionLayoutId": "layout:long-title",
            "explicitLineBreaks": True,
            "uax14Version": "17.0.0",
            "tailoring": "ko-space-first-with-id-fallback-v1",
            "kinsokuPolicy": "cjk-no-leading-closing-no-trailing-opening",
        },
        "titleStressCorpus": [
            {
                "id": "stress:ko",
                "category": "ko",
                "text": "품질 검증이 배포 속도를 높인다",
                "expectedLines": ["품질 검증이 배포 속도를 높인다"],
            },
            {
                "id": "stress:en",
                "category": "en",
                "text": "Canonical input drives HTML Preview geometry stays measurable",
                "expectedLines": [
                    "Canonical input drives HTML",
                    "Preview geometry stays measurable",
                ],
            },
            {
                "id": "stress:mixed",
                "category": "mixed",
                "text": (
                    "DeckMasterContract가 frame을 잠그고 "
                    "SlideSpec v2가 title을 채우며 "
                    "PowerPoint layout이 관계를 보존한다"
                ),
                "expectedLines": [
                    "DeckMasterContract가 frame을 잠그고",
                    "SlideSpec v2가 title을 채우며",
                    "PowerPoint layout이 관계를 보존한다",
                ],
            },
            {
                "id": "stress:numeric",
                "category": "numericParenthetical",
                "text": "3개 출력(HTML·PNG·PPTX)을 1개 digest로 추적하고 0건 repair 구조를 확인한다",
                "expectedLines": [
                    "3개 출력(HTML·PNG·PPTX)을",
                    "1개 digest로 추적하고",
                    "0건 repair 구조를 확인한다",
                ],
            },
        ],
        "digest": ZERO_DIGEST,
    }
    return deepcopy(contract)


def master_contract_with_logo_payload() -> dict[str, object]:
    contract = master_contract_payload()
    contract["brandAssets"]["logo"] = {
        "assetId": "asset:presenton-logo",
        "digest": (
            "sha256:689976b88fc161e4cfa9733b4bf9be5f"
            "4a8443569586256360581e584f9cb76e"
        ),
        "altText": "Presenton logo",
    }
    contract["furniture"]["logo"]["assetId"] = "asset:presenton-logo"
    return contract


def long_title_slide_payload() -> dict[str, object]:
    return {
        "slideId": "slide:long-title",
        "layoutId": "layout:long-title",
        "title": {
            "text": (
                "생성형 AI 저작물의 출처·권리·품질을 "
                "audit-ready provenance로 연결하는 실행 계약 "
                "(DeckMasterContract + SlideSpec v2, 2026)"
            ),
            "lines": [
                "생성형 AI 저작물의 출처·권리·품질을",
                "audit-ready provenance로 연결하는 실행 계약",
                "(DeckMasterContract + SlideSpec v2, 2026)",
            ],
            "lineBreakPolicy": "canonical-explicit-uax14-v1",
            "uax14Version": "17.0.0",
            "tailoring": "ko-space-first-with-id-fallback-v1",
            "kinsokuPolicy": "cjk-no-leading-closing-no-trailing-opening",
        },
        "body": "공통 frame은 master에만 존재하고 모델은 body slot만 수정합니다.",
    }
