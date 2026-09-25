# 레이아웃 수리 오픈소스 검토 — 채택·기각 판정

- 작성일: 2026-09-11
- 질문: authored 슬라이드의 HTML/CSS 깨짐을 **LLM 호출 없이** 고치는 데 쓸 오픈소스가 있는가.
- 결론: **완성품은 없다.** 있는 것은 부품이고, 이 포크는 그 대부분을 `authored-local-fit/v1.5`로
  이미 갖고 있다. **필수 신규 의존성 0개.**
- 실측 근거: [`artifacts/local-fit-source-reflow-replay-20260911/report.md`](../artifacts/local-fit-source-reflow-replay-20260911/report.md),
  [`artifacts/flagged-overreport-20260911/report.md`](../artifacts/flagged-overreport-20260911/report.md),
  [설계 문서 §10](authored-local-fit-overlap-space-design-20260909.md).
- 이 문서는 판정만 기록한다. 채택 2건은 **아직 착수하지 않았다** — 이유는 마지막 절에 있다.

## 1. 채택 (2건) — 둘 다 의존성 추가가 아니라 이식

| 항목 | 라이선스 | 무엇을 가져오는가 |
| --- | --- | --- |
| **microsoft/ReDeck** 의 기하 검출 함수 4종 | MIT | `app/modules/redeck/html_spatial_state.py`(~4,920줄)의 `_detect_overlaps`(컨테이너·장식 요소 제외, 완전 포함 90% / 부분 3%·8px), `_detect_occlusions`(z-index 가림, 조상-자손·인라인 형제는 의도된 중첩으로 제외), `canvas_truncation`, 그리고 `elementsFromPoint()` z-order 스택에 알파를 합성해 WCAG 2.1로 계산하는 대비 검사. Playwright/Chromium **1280×720** — 이 포크 `CANVAS_WIDTH/HEIGHT`와 같은 캔버스이고, 모두 `ContentBlock` 목록에 대한 독립 함수라 떼어낼 수 있다 |
| **`word-break: keep-all` + `overflow-wrap: anywhere`** 쌍 | 비용 0 | local-fit의 light-spacing 후보 티어에 추가. 이 포크의 인포그래픽 CSS(`utils/authored_infographic/css_base.py`)가 정확히 이 쌍을 쓴다 |

두 항목의 단서:

- ReDeck는 **의존성으로 걸지 않는다.** 별 7개·커밋 39개의 갓 나온 논문 코드이고, 자체 Playwright
  러너와 KaTeX CLI 선행이 붙어 있다. MIT 하에 함수만 기존 캡처 경로로 옮기고 500줄 정책대로 쪼갠다.
  임계값은 저들 코퍼스 기준이므로 **shadow로 기존 검출과 건수를 대조한 뒤** 승격한다
  (단편화 게이트를 켰을 때 발행 결함이 0→9로 늘어난 선례가 있다).
- `keep-all` **단독은 긴 무공백 토큰에서 넘침을 늘린다.** 반드시 `overflow-wrap`과 쌍으로 넣고,
  서버 CSS 주입이므로 은닉 오버플로 검사 이음새 3곳(라이브러리·플레이스홀더·slidespec 프로젝터)을
  함께 본다.

## 2. 조건부 (3건)

| 항목 | 라이선스 | 조건 |
| --- | --- | --- |
| **@lume/kiwi** | BSD-3 | v1.4 형제 재배치를 "유한 후보 열거" 대신 제약 솔버로 가기로 **결정되면**. 원조 `kiwi.js`는 유지보수 중단, 이쪽이 현행. 단 현재 설계의 "후보를 열거하고 비교해 증거를 설명한다"는 성격을 바꾼다 |
| **harfbuzzjs** | MIT | 렌더 **전** 한글 글리프 폭·줄 수 계산으로 capacity gate가 추정 대신 실측 예산을 잡게 한다. **하드 게이트로 쓰면 안 된다** — 폰트 파일이 필요하고 Chrome 줄바꿈과 1:1 일치를 보장하지 않는다 |
| **odiff** | MIT | pixelmatch 대비 6~8배. Playwright가 이미 pixelmatch를 쓰므로 얻는 것은 **속도뿐**. 시각 회귀가 CI 시간의 실측 병목으로 확인되면 교체 |

## 3. 기각 (10건)

| 항목 | 라이선스 | 기각 사유 |
| --- | --- | --- |
| **ReDeck 프레임워크 전체** | MIT | 요구사항과 정면으로 어긋난다. LLM 에이전트가 원자적 편집 액션(structural/content/attribute + rollback/submit)을 고르고 렌더 관측을 돌려받는 루프이며, 비교 실험이 **태스크당 LLM 호출 150회 상한**으로 맞춰져 있다. 부속 `repair_utils.py`는 LLM 수리 결과 검증기이고 그 임계(70~75%)는 이 포크의 누적 10% 상한보다 느슨해 값이 없다. `geometry_fixer.py`는 python-pptx 클램프 3개 |
| **Paged.js** | MIT | 흐름(flow) 문서를 페이지 박스로 분절하는 엔진. 이 덱은 role 계약·QID·header 계약으로 묶인 고정 캔버스라 absolutely-positioned 인포그래픽은 분절되지 않고 QID·role 모델만 깨진다. 장 분할은 "어느 카드를 어느 장으로"라는 의미 문제 — **코드가 이걸 provider에 넘기는 것은 기능 누락이 아니라 옳은 판단이다.** 긴 불릿·표처럼 실제로 흐름 모양인 넘침에만 좁게 유효 |
| **Vivliostyle.js** | **AGPL-3.0** | 조판 품질은 Paged.js보다 위지만 자체 호스팅 상용 제품에 링크하면 소스 공개 의무가 걸린다. 검토 중단 |
| **rbush** | MIT | 겹침 검출·수리가 이미 있다 — `repair_source_decoration_paint_order`, `repair_authored_owned_text_paint_order`, `_repair_authored_intrinsic_grid_overlap`(`services/authored_generation_repair.py:134–155`). R-tree는 성능 최적화일 뿐 없는 기능이 아니다 |
| **gridstack.js `GridStackEngine`** | MIT | 겹친 사각형을 결정론적으로 밀어내는 엔진. 전면 재배치는 Composer로 시도하고 **2026-09-08에 삭제된 방향**이다 |
| **@lume/kiwi** (솔버 단독 채택) | BSD-3 | 제약 솔버는 현재 병목이 아니다. 실패 원인 분해에서 솔버로 풀리지 않는 항목이 다수(§5) |
| **Satori** | MIT | Yoga 기반 결정론적 레이아웃이라 "브라우저 없이 측정"에 매력적으로 보이지만 **flexbox 전용 서브셋**이다 — grid 없음, 가상요소 없음, z-index 없음, `<style>`·외부 리소스 없음. authored HTML은 grid/absolute를 자유롭게 쓰므로 측정값이 실제 Chrome과 갈라져 거짓 판정을 만든다 |
| **axe-core** | MPL-2.0 | `color-contrast` 규칙이 **배경 이미지가 있으면 검사를 건너뛴다.** pass가 아니라 미검사이므로 깨끗한 스캔을 통과로 오독하게 된다. 이 케이스는 ReDeck 대비 함수가 실제로 계산한다 |
| **textFit / fitty** | MIT | local-fit v1.5가 상위 호환이다: 유한 후보, 9.0pt 바닥, 누적 10% 상한, `parent_reflow`, 검증 렌더, 바이트 보존 증거 재구성. 이진탐색 이식은 퇴행 |
| **detect-element-overflow** | MIT | `scrollHeight > clientHeight` 한 줄. 의존성 추가 가치 없음 |
| **slidev-overflow-checker** | — | Slidev 전용. ReDeck 검출기가 같은 일을 훨씬 넓게 한다. 리포트 스키마 참고 외에는 값이 없다 |

## 4. 이미 있던 것 (검토 중 확인)

검토는 이 포크가 무엇을 이미 갖고 있는지 모르고 시작했다. 확인된 것:

- `services/authored_local_fit_policy.py` revision `authored-local-fit/v1.5`, 폰트 바닥 9.0pt,
  최초 저작 대비 누적 축소 ≤10%.
- `utils/slide_capture_fit_script.py`가 Chromium **안에서** 유한 후보(light spacing → combined →
  font-only, `parent_reflow` 계수 1.0→0.40)를 평가해 QID 인라인 선언만 덧붙이고,
  `authored_presentation_generation.py`가 `fit=True` 렌더 뒤 `fit=False` 검증 렌더를 돈다.
  **기하 LLM 호출 0회.**
- 따라서 "in-page 맞춤 루프"와 "중복 후보 제거"는 새로 들여올 기능이 아니었다.

## 5. 그 뒤 실측이 기대치를 낮췄다

검토 직후 라이브 영수증 집계, 짝지은 오프라인 리플레이, 후보 거절 347건 전량 분해를 돌렸다.
세 번 모두 같은 결론에 도달했고, 마지막에 원인이 바뀌었다.

- **검출은 병목이 아니었다.** fitter는 결함을 찾고 **못 고친다**. 발행된 검토 표시 35장에 fitter를
  걸면 1장 해소·1장 개선·0장 악화이고, `overlap` 9건은 그대로 남는다
  → **ReDeck 검출 포팅의 수정률 기대를 1점에서 0~1점으로 내렸다.**
- **후보 소진은 예산이 아니라 시행 목록의 끝이다.** 확장 래더 0.875~0.50이 9pt 바닥 계수 아래라
  전부 같은 px로 clamp되므로, 바닥·10% 상한 아래에서는 **구별되는 추가 후보가 존재하지 않는다.**
  중복 후보 제거는 측정을 아낄 뿐(한 픽스처에서 26→12) 수정률을 올리지 않는다.
- **최대 병목은 `fixed_horizontal` 42.7%** — `_scopeHard`가 `left`/`width` 이동을 허용오차 초과로
  거절한다. 실패 8장 중 5장에서 이 규칙이 지배적이고, 밀리는 요소는 전부
  **폭이 내용으로 정해지는 텍스트 박스**(`<span white-space:nowrap>`, 맨 `<strong>`, 폭 무지정
  `display:flex`)다. fitter가 바꿀 수 있는 활자 크기가 곧 그 폭이므로 폰트 축소 후보는 세로
  의도였는데 가로 드리프트로 통째 거절된다. **오픈소스 15건 어느 것도 이것을 건드리지 못한다.**
- **harfbuzzjs는 1점에서 0점으로** 내렸다 — 폰트 바닥 도달 버킷이 0건이었다.

## 6. 그래서 남은 판단

채택 2건은 여전히 유효하고 비용도 낮지만, **실제 레버는 오픈소스 바깥에 있다.** 설계 결정
3건(§10의 (a) auto-height scope root, (b) grid 부모 밖 slack, (c) 내용 폭 텍스트 박스 vs 세로 전용
보존 계약)이 비중 순으로 (c) > (a)+(b)이며, 그 결정 전에는 어느 라이브러리도 수정률을 올리지 못한다.

재현은 `artifacts/local-fit-source-reflow-replay-20260911/`의 스크립트 3개로 가능하다
(생성·프로바이더 호출·DB 쓰기 없음).
