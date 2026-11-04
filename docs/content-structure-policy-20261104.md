# 내용 구조 표현 정책: 표와 연결선 (2026-11-04)

`utils/content_structure_policy.py`. 표와 연결선(화살표)을 쓰는 조건을 한곳에서 정의한다.
기본값은 **꺼짐**이다. 켜는 방법과 켜면 안 되는 이유는 아래 "측정"에 있다.

## 배경: 흩어져 있던 세 지시

| 지시 | 위치 | 이력 |
|---|---|---|
| 아웃라인의 표 요청 줄(`Must have content either in bullet points or table or both`)이 주석 처리됨 | `utils/llm_calls/generate_presentation_outlines.py` | 2025-07 업스트림 시절부터 있었고, 2026-04-15 업스트림의 215개 파일 일괄 커밋 `c7860127f`에서 주석 처리됨. 이유는 커밋 메시지에도 문서에도 없다. 이 포크가 끈 것이 아니다 |
| 작성 정책(`ppt-writing-policy/v1`)이 "파이프 구분자를 피하라"고 지시함 | `utils/ppt_writing_prompt.py` | 2026-10-13 `4dec21eb6`. 기호 제한이 목적이고 표 지시문은 건드리지 않았다. 다만 마크다운 표도 파이프로 쓰므로 표 생성을 막는다 |
| 작성 규칙 "독립 항목 사이에 화살표를 넣지 말라" | `services/generation_core/authoring/author_slide_svg.py` (`SVG_AUTHOR_RULES`) | 2026-09-17 `5f2e837ea`. 설계 근거는 `docs/svg-generation-mode-design-20260916.md` §7.1(근거 없는 순서·인과·계층을 만들지 않는다)과 §12.1(실험에서 근거 없는 화살표 관찰) |

세 곳이 따로 정해져 있어서, 근거가 있는 흐름과 표도 그리지 못했다.

## 구조

`StructurePolicy(tables, connectors)` 하나를 아웃라인과 작성 규칙이 같이 쓴다.
정책이 없으면(`None`) 모든 호출부가 기존 문장을 그대로 쓴다(바이트 동일).

- 아웃라인: 표 요청 줄을 복원하고, 작성 정책 뒤에 "표의 파이프는 필수 문법"이라는 문장을 붙인다.
- 작성 규칙: 연결선 규칙을 "소스가 순서·흐름·계층·의존·인과를 명시한 항목만 연결하고, 독립 항목이나 근거 없는 관계는 만들지 않는다"로 바꾼다. 표가 켜져 있으면 셀 글자 속성 문장을 붙이고, 템플릿 덱이면 네이티브 표 그룹 ID를 `body-` 접두어로 쓰게 한다.
- 범위: 환경 변수 `CONTENT_STRUCTURE_POLICY_SCOPE` = `off`(기본) / `template`(template-redesign 덱만) / `svg`(모든 svg 덱).
- `SVG_AUTHOR_RULES` 상수는 고치지 않는다(헤더 설계 호출의 digest 입력이다). 범위 안의 덱에서만 호출마다 치환한다. 문장이 사라졌으면 조용히 건너뛰지 않고 규칙을 덧붙인다.

## 측정 (실제 템플릿 덱, 원본 10페이지 텍스트 입력, `verbosity=text-heavy`, 모델 `claude-sonnet-5-5`)

**아웃라인만 (2회씩, 가설 검증)**

| 조건 | 표 개수 |
|---|---|
| 현재 상태 (작성 정책 켜짐, 표 요청 줄 꺼짐) | 0 |
| 표 요청 줄만 켬 | 0, 0 |
| 표 요청 줄 + 파이프 예외 | 3, 2 |
| 파이프 예외만 | 2, 2 |

작성 정책의 파이프 금지가 표를 막고 있었고, 표 요청 줄의 주석 처리는 원인이 아니었다. 파이프 예외만으로 표가 나온다.

**전체 생성 (템플릿 덱, 10페이지)**

| 실행 | 품질 게이트 | 표가 있는 페이지의 결과 |
|---|---|---|
| 정책 꺼짐 (`text-heavy`) | 6 통과 / 4 검토 | 해당 없음 (네이티브 표 0개) |
| 정책 켜짐 (1차) | 2 통과 / 8 검토 | 5 / 5 폴백 |
| 정책 켜짐 + 표 문장 보강 (2차) | 3 통과 / 7 검토 | 5 / 5 폴백 |

- 모델은 `data-native-table` 선언까지 쓰지만 검증에서 거절된다. 1차의 원인은 셀 글자 속성 누락(`svg_contract_explicit_text_attributes_required`)과 ID 충돌(`svg_template_body_namespace_required`: 템플릿은 `body-` 접두어를, 네이티브 표 규칙은 `native-table-SLUG`를 요구)이었다. 2차에서 ID 충돌은 사라졌고 `native_requested_table_missing`, `text_overlap`, `svg_template_font_unavailable`이 남았다.
- 표 없는 페이지에서도 속성 누락이 나와서 폴백 증가가 전부 정책 때문이라고 단정할 수 없다. 정책을 끈 대조 실행이 같은 조건으로 한 번 더 필요하다. 실행 간 변동이 크다(같은 입력에서 폴백 3건 → 7건).
- 연결선의 효과는 측정하지 못했다. 통과한 페이지가 2~3개뿐이고, 그 페이지의 `polyline`이 아이콘인지 연결선인지 구분하지 않았다.

## 결론과 다음 작업

1. 정책은 기본 꺼짐으로 둔다. 켜려면 `CONTENT_STRUCTURE_POLICY_SCOPE=template`.
2. 켜기 전에 템플릿 모드의 네이티브 표 작성을 고쳐야 한다. 표 페이지가 10 / 10 폴백이다. 우선 `native_requested_table_missing`의 원인(선언은 있는데 요구와 맞지 않는지)부터 본다.
3. 속성 누락(`svg_contract_explicit_text_attributes_required`)은 표와 무관하게 많이 나온다. 모델이 텍스트마다 폰트 속성을 반복하지 않으려는 경향이므로, 루트에 공통 스타일을 선언하는 기존 방식(`services/svg_text_defaults.py`)이 이 경로에서 쓰이는지 확인한다.
4. 정책을 끈 대조 실행으로 폴백 변동의 몫을 분리한다.
5. 연결선은 근거 없는 화살표가 늘지 않는지(설계 문서 §12.1) 페이지별로 확인한 뒤에 켠다.

## 환경 메모

- 로컬 `public-artifacts` 저장소에서 등록한 템플릿의 저장 객체 파일이 시간이 지나면 사라져(`artifact.object_missing`, DB에는 행이 남음) 생성이 `svg_template_adaptation_material_unavailable`로 실패했다. 등록 직후 생성하면 재현되지 않는다. 원인은 조사하지 않았다.
