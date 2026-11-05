# Template page composition grammar and body exemplars (2026-11-05)

Code: `services/svg_templates/body_composition.py`, `SvgTemplatePackage.body_composition`
(`models/svg_template_frame.py`), `body_author_rules` / `template_exemplar_facts` in
`services/svg_templates/generation.py`. Internal knob `SVG_TEMPLATE_BODY_EXEMPLARS` (`off` / `grammar` / `on`),
**off by default**. Tests: `tests/unit/test_svg_template_body_composition.py`.

## Problem

Template decks scored about 2.7 with judge v2 against about 4.4 for the original pages, lowest on density
and visuals. Component style (`docs/template-component-style-20261104.md`) passes how components look,
not how the template composes a page: a key-message bar under the header, numbered sections holding
different components, many small text items, a body filled to the bottom.

## Design

**Measured at registration, stored on the package**, for the same reason as component style: the
measurement needs the open PPTX (theme colours, inherited run styles, font substitutions, chart parts)
and every body page, not only the representative ones, and the result then travels with the
immutable, digest-checked package. The field is omitted when absent, so earlier package digests are
unchanged; a template registered before this change has no composition and the knob does nothing for it.
About 2 s for a 101-page deck; no model call.

* **Grammar** over the template's content pages (pages with body text items at least 30 % of the median):
  sections per page (numbered heads: a chip or bare number with heading words beside it, or a bold
  heading starting with a number; heads in a row of three or more, or closer than 12 % of the body
  height in one column, number a sequence or list), shares of pages with a key-message bar and with
  numbered sub-headings, content kinds per page, text items (table cells count), ink coverage (body grid
  cells under text, tables, charts, figures, steps; container interiors are not ink), lowest element
  reach, character-weighted text sizes. Rendered as one paragraph in the deck-invariant rules (about
  440 tokens), sizes raised to the page's body text minimum.
* **Exemplars**: one page per recurring combination of main content kinds (table, chart, flow,
  hierarchy, KPI; cards or list when none), most frequent first, up to ten; the representative fits the
  token budget uncut and has the text amount nearest the upper quartile. Each is a structural outline:
  element kind, box relative to the body region (clipped to it), fill/stroke, and for text only
  size/weight/colour with paragraph and character counts (`number` for a bare item number, `number+` for a
  heading starting with one); tables give rows, columns, header fill and cell text; charts their plot type.
* **Per page** (knob `on`, body frames only, not for source-preserving pages): `content_kinds` classifies
  the outline page's Markdown (table rows; a series of four figures of one unit or periods with figures;
  figures with units next to target/result words; stage words, arrows or numbered lines; organisation,
  role and layer words; list items or sub-headings), `choose_exemplars` picks the best match (shared kinds
  weighted, kinds the page does not need penalised, section count) and a second only when it shows a
  kind the first lacks. Rendered at the body text minimum (characters scaled to what fits the same box)
  within 2,500 estimated tokens each, sent as `template_exemplars` in the page prompt (not a digest input).
  The rules say they show how the template divides and fills a page and must not be copied.

**No source text can reach a prompt.** The stored models have no free-text field (kinds and chart types
are literals, colours validated hex, the rest numbers); a unit test serialises a synthetic package and
checks that every stored string is a kind, a literal or a colour and that none of the deck's words appear.

## Smoke (template A, knob on, component style off, one run per input)

Template A registered again with this code; the same 4D eval tool and judge v2 against the stage-1
baseline cells (`agent5A`, same code otherwise, the older registration of the same deck).

| Cell | Judge overall | density | visuals | First try | Fallback | Texts | Rects | Tables |
|---|---|---|---|---|---|---|---|---|
| dense_proposal_ko, baseline | 2.62 | 2.3 | 2.2 | 9 | 0 | 410 | 102 | 0 |
| dense_proposal_ko, exemplars | 2.62 | 2.2 | 2.0 | 10 | 0 | 391 | 141 | 0 |
| data_report_ko, baseline | 2.50 | 2.3 | 2.1 | 8 | 1 | 361 | 91 | 3 |
| data_report_ko, exemplars | 2.66 | 2.5 | 2.2 | 6 | 1 | 418 | 133 | 7 |

Author input tokens per page: 18.1k -> 20.8k (dense) and 17.5k -> 20.1k (data); the cover page (rules only)
+0.4k. About $2 per deck.

Seen on the pages: every body page now opens with the template's key-message bar, cards carry the
template's label strips, and data pages fill the lower half (a chart page that was half empty became a
table, a doughnut and two KPI figures). But the dense baseline was already multi-section and dense, and
drawn bar or line charts were replaced by tables or tile rows on two pages, so visuals did not rise. One
run per cell is noise-level; the paired measurement (N >= 3 over the four inputs and both templates)
decides the default.

## 짝지은 측정과 기본값 (2026-11-05)

같은 코드, 같은 세션, 템플릿 A·B 모두 본문 구성을 담아 다시 등록, 입력 3종(`dense_proposal_ko`, `data_report_ko`,
`unstructured_report_ko`) x 템플릿 2개 = 6칸, 칸마다 1회, 심사 버전 2. 조건: a 꺼짐, b `SVG_TEMPLATE_BODY_EXEMPLARS=on`,
c b에 `SVG_TEMPLATE_COMPONENT_STYLE=on`을 더함.

| 지표 (6칸 평균) | a 꺼짐 | b 예시 | c 예시+부품 스타일 |
|---|---|---|---|
| 첫 시도 성공 (10장 중) | 7.0 | 8.33 (좋아짐, 4/6칸) | 7.17 |
| 덱당 폴백 | 0.50 | 0.33 | 1.00 |
| 심사 종합 | 2.70 | 2.88 (6칸 중 5칸 상승) | 2.84 |
| 심사 구성 | 2.82 | 3.03 (좋아짐, 5/6칸) | 3.05 (좋아짐) |
| 심사 밀도 | 2.53 | 2.75 (좋아짐, 4/6칸) | 2.63 |
| 심사 위계 | 3.03 | 3.18 | 3.23 (좋아짐) |
| 심사 시각 요소 | 2.18 | 2.35 | 2.28 |

렌더에서 b는 본문마다 핵심 메시지 띠, 수치 카드 줄, 서로 다른 부품의 구역 두세 개로 구성되고, a에서 아래 절반이 비던
데이터 페이지가 지표 카드와 표와 설명 상자로 채워졌다. c는 번호 칩과 소제목 막대가 더 템플릿답지만 b보다 첫 시도가
나빠졌다(4/6칸).

결정: `SVG_TEMPLATE_BODY_EXEMPLARS`의 기본값을 `on`으로 바꾼다(값이 비면 `on`, `off`/`grammar`는 그대로 쓸 수 있음).
부품 스타일은 꺼짐 유지. 본문 구성 없이 등록된 템플릿에는 아무 변화가 없으므로, 효과를 보려면 템플릿을 다시 등록해야 한다.
원본 페이지는 같은 심사에서 약 4.4이므로 격차(약 1.5점)는 여전히 크다.
