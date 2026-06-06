# 종합 평결 — Adaptive-Layout 재설계 (4개 차원 통합)

## 1. 종합 점수: **71 / 100**

**가중치 선택과 근거** — build-ready 판정이 목적이므로, "지정된 대로 지을 수 있는가(correctness·implementability)"를 가장 무겁게, "토픽이 빠짐없이 다뤄졌는가(completeness)"를 그 다음, "잔여 불확실성이 경계지어졌는가(open-risk)"를 가장 가볍게 두었다. open-risk는 점수가 가장 높지만(80) 이는 "위험이 fallback으로 묶여 있다"는 별개 척도를 측정한 것이고, 다른 세 리뷰가 critical로 본 항목(data-block-id, Linux-only converter)을 major로 더 관대하게 평가했으며 일부 항목을 중복 계상하므로 의도적으로 down-weight했다.

| Dimension | Score | Weight | 기여 |
|---|---|---|---|
| Correctness | 70 | 30% | 21.0 |
| Implementability | 72 | 30% | 21.6 |
| Completeness | 67 | 25% | 16.75 |
| Open-risk | 80 | 15% | 12.0 |
| **종합** | **71** | 100% | **71.35** |

(단순 평균은 72.25 — 가중 결과가 약간 낮은 것은 가장 관대한 open-risk 렌즈를 낮추고, 점수가 낮은 correctness/implementability를 높인 결과로, 정직한 방향이다.)

## 2. 한 줄 평결

**Build-ready 아님 (Phase 3-6 기준). 단, 이미 출하·검증된 Phase 1-2는 조건부 ready.** 가장 큰 단일 이유: 재설계의 심장인 composer 적합화 파이프라인이 의존하는 두 개의 "무변경 재사용" 주장(`apply_capacity_fit`를 validator로 / capacity walker를 `blocks[]`에 그대로)이 **실제 코드와 모순되거나 degenerate**하여, Phase 3의 1차 입력(capacity menu)과 검증 레이어가 구현이 아니라 **설계가 덜 된** 상태다.

## 3. 강점 (Top 4)

1. **Phase 1-2가 주장이 아니라 실증으로 완료됨** — tsc=0, legacy 8-slide 덱 무변경 렌더, headless `/pdf-maker`가 export DOM contract 충족. 핵심 베팅(단일 `AdaptiveSlide` + group 공존 + DB migration 없음)이 buildable·low-regression임을 4개 리뷰 모두 인정한 가장 강한 증거.
2. **Export DOM-contract 전제가 기계적으로 타당하고 end-to-end 검증됨** — converter가 computed style로 leaf를 매핑하고 잉여 attribute를 무시하는 v0.2.9 동작을 역설계해 렌더러를 설계.
3. **실제 코드베이스에 강하게 정초됨** — `presentation.py`/`layout_capacity.py` file:line anchor가 정확, 기존 관습(마커 `__image_url__`/`__icon_url__`, capacity walker, free-JSON `SlideModel.content`, `getLayoutByLayoutId` group dispatch) 재사용, additive zero-migration 영속화.
4. **정직하고 규율 있는 §13 미해결 결정 목록** — 10개 항목 각각이 (불확실점 + 해소 테스트 + 경계지어진 fallback)을 짝지어, 나열된 잔여 리스크 중 접근법을 무효화하는 것은 없음.

## 4. 가장 메워야 할 갭 (중복 제거·severity 정렬)

**[CRITICAL] G1. capacity walker + `apply_capacity_fit` 재사용 주장이 코드와 불일치** — (completeness·correctness·implementability 3중 지적; implementability는 critical) 두 개의 결합된 문제:
- `apply_capacity_fit`는 markdown outline content + int structure index 위에서 동작하는데(`compute_content_volume` 정규식 기반), composer는 typed `blocks[]`(markdown 없음)를 방출 → drop-in 경로 없음.
- Phase-1 archetype 스키마가 `z.array(BlockSchema=union)`이고 outer array에 maxItems가 없어, `_walk`가 anyOf-max(단일 블록)×mult-1로 계산 → **모든 archetype이 거의 동일한 capacity**를 산출, composer가 고를 메뉴가 무의미.
- **Fix:** backend dynamic factory가 **closed per-archetype 스키마**(예: stat-hero → StatBlock array, maxItems 4)를 방출하게 하고, `compute_layout_capacity`를 legacy가 아닌 **adaptive archetype 스키마에 대해 golden-test**해 archetype별 distinct 값 확인. 그리고 `apply_capacity_fit`는 (a) adaptive 경로에서 제거하고 schema bound + 실제 시각적 720px fit 체크에 의존하거나, (b) 명시적 `blocks→ContentVolume` 어댑터를 작성. 어느 overflow 클래스(char volume vs pixel)를 어느 레이어가 막는지 명시.

**[CRITICAL] G2. data-block-id가 readOnly Tiptap 경로에서 스트립됨 (FINDING b)** — **4개 리뷰 전원 지적**(최다 인용). `TiptapTextReplacer.tsx:90-96`이 직접 텍스트 leaf(`<h1>/<p>/<span>` = title/subtitle/eyebrow/text, 가장 흔한 블록)를 className+style만 복사한 새 `<div>`로 `replaceChild` → data-block-id와 시맨틱 태그 소실, `<li>`와 짧은 stat span에만 잔존. Phase 4(§5.2/§8.2)의 deterministic editor binding 전체가 "모든 leaf에 anchor 생존"을 전제하므로 정면 위배. §8.1은 TiptapText를 "무변경 재사용"으로 적어 내부 모순.
- **Fix:** group 'adaptive'에서는 node-replacing 경로를 우회하는 `BlockBindingWrapper`로 in-place 마운트(원소의 data-block-id/태그 보존), 또는 교체 컨테이너에 id 재각인. `h1/p/span`에 대해 readOnly 후 data-block-id 생존을 단언하는 regression test 추가.

**[CRITICAL] G3. content-first-design.md와 미조정 + prepare↔stream 경계 재작성 hand-wave** — (completeness critical; correctness가 persistence timing minor로 보강) adaptive 문서는 Stage A(ContentBrief)는 상속하나 Stage B/DeckPlan/`deck_plan` 컬럼/dual-write를 침묵 속에 폐기. 코드상 `/prepare`(`presentation.py:290-362`)는 outline/structure/layout만 영속화하고 **SlideModel row를 만들지 않으며**, slide는 `/stream` 루프에서 생성됨. "compose at /prepare, persist SlideSpec, /stream은 렌더+에셋만"은 저장 위치가 정의되지 않은 구조 재작성이고 SSE envelope 보존이 미증명.
- **Fix:** 두 설계 중 어느 것이 authoritative인지(또는 `compose_slides`가 DeckPlan을 어떻게 supersede하는지) 명시하는 절 추가. composed SlideSpec이 /prepare~/stream 사이 **정확히 어디에 영속화**되는지, 새 stream-loop 형태, 보존해야 할 byte-level SSE envelope를 규정.

**[MAJOR] G4. export round-trip(#1 검증 게이트)가 Windows dev box에서 실행 불가 (FINDING a)** — **4개 리뷰 전원 지적**. v0.2.9는 `convert-linux-x64`만 출하(convert-win32.exe 없음). §12의 최우선 검증(SlideSpec→/pdf-maker→PPTX→python-pptx shape-count/text assert)이 개발이 일어나는 환경에서 돌지 않아, "editable PPTX survives" 키스톤이 byte-검증이 아닌 plausibility로 남음. open-risk는 export-fidelity 항목 5개(2-6)가 **모두 이 하나의 환경 의존에 게이트**됨을 추가 지적.
- **Fix:** round-trip suite를 Docker/Linux/CI 레인에서 돌리고 CI를 게이트로 고정. 로컬 Windows의 DOM-contract 렌더 assert는 shape-count 증명이 아닌 **proxy**로 라벨링. `ADAPTIVE_COMPOSER` 기본 on 전에 green round-trip을 Phase 6 전제로 못박기.

**[MAJOR] G5. adaptive 경로의 speaker_note provenance 미정의** — (completeness) `generate_slide_content`(`__speaker_note__` 주입, minLength 100) 제거되는데 composer 출력 모델(§6.2)에 speaker_note 필드 없음. §2.2는 `SlideModel.speaker_note` 컬럼에 산다고 하나 아무것도 채우지 않음.
- **Fix:** composer 출력 스키마에 `speaker_note`(또는 hint) 필드 추가하고 `SlideModel.speaker_note` 매핑 명시, 기존 `data-speaker-note` export contract 보존.

**[MAJOR] G6. block-CRUD 에디터 UX 미명세** — (completeness) add/delete/reorder + drag, schema-driven generic property panel, archetype별 min/max, 신규 블록이 고정 archetype region에 어떻게 들어가는지가 ~3 bullet로 hand-wave.
- **Fix:** Phase 4 전에 block-ops UX(추가 블록의 archetype별 착지 위치, archetype 스키마로 제약된 type-picker, region 내 reorder 의미)와 schema-driven panel 필드 매핑 규정.

**[MINOR] G7. asset 파이프라인 nested-block deep-walk — 리뷰어 간 불일치** — completeness는 §13.10 미해결로 major 우려; correctness·implementability는 `get_dict_paths_with_key`/`process_slides`가 이미 depth-agnostic이라 **over-flag**라고 판정. 실제론 이미 동작할 가능성이 높음.
- **Fix:** nested `ColumnsBlock` 안 image/icon 마커로 1개 테스트만 추가해 확정(저비용). 추가 코드는 불요할 공산.

**[MINOR] G8. composer LLM schema/structured-output 안정성 과소가중** — (open-risk·completeness §13.9) 콘텐츠 품질 가치 제안의 본체인데 10개 중 1개 동급 bullet로 취급.
- **Fix:** schema-valid rate, variety/adjacency 준수, n_slides 계약을 acceptance metric으로 일급 승격, flag-on 전 충족 의무화 + Phase 3 sign-off 전 schema-stability probe.

**[MINOR] G9. brand-slot 메커니즘이 구현과 괴리** — (correctness) §3.3은 magic key를 `data.brand`로 교체한다 하나, built `AdaptiveSlide`는 `spec.__logo_url__`/`spec.__companyName__`을 읽고 `V1ContentRender`가 그 magic key를 주입(구현이 더 KISS). source of truth 하나로 정리.

**[MINOR] G10. 기타** — chart/table 편집 fidelity 미정의(§13.4), `slides_markdown` bypass × composer 및 `n_slides='auto'` 경로 미정의, theme §3.4 style-preset 테이블 값 미열거, Phase 1/2 file list가 실제 출하분과 불일치(backend nested-theme rewrite는 client-side `deriveThemeTokens`로 대체됨 — re-baseline 필요), 리스크 등급이 Phase 3-6 일괄 '중'으로 평탄화(composer 통합·JS fit 엔진은 실제 '상').

## 5. 점수를 올리려면 (대략적 가점)

- **G1 해소** (closed per-archetype 스키마 + maxItems + adaptive 스키마 golden-test + validator 어댑터/대체 결정): **+4~5점** — Phase 3 fitting 입력·검증을 실제로 buildable하게 만듦, 최대 레버리지.
- **G2 해소** (adaptive용 binding이 node-replacement 우회 + h1/p/span regression): **+3~4점** — Phase 4 에디터 가치 제안 잠금 해제.
- **G3 해소** (content-first 조정 + SlideSpec 영속 위치 + 새 stream-loop + SSE envelope 명시): **+3점** — 가장 큰 구조적 hand-wave 제거.
- **G4 해소** (Linux/Docker CI 레인 바인딩 + Phase 6 게이트, 로컬은 proxy 라벨): **+2점** — 키스톤 검증을 실행 가능하게.
- **G5 + G6 + G8** (speaker_note 필드·매핑, block-ops UX 명세, composer 안정성 acceptance metric): 각 **+1~2점**.

합계로 ~71 → **mid-80s** 이동 가능. 핵심은 "무변경 재사용" 프레이밍을 걷어내고 load-bearing seam 3곳(G1·G2·G3)을 실제 설계로 닫는 것.

## 6. "전체 설계 다 되어 있나?"에 대한 직답

**Partly (부분적으로 — 아니오에 가까움).**

- **폭(coverage)은 ~90%** — 출하에 필요한 모든 영역이 구체 절로 존재(SlideSpec 모델 §2, 단일 렌더러 §5, 14-archetype 매트릭스 §4, block→PPTX 매핑 §7, 영속화·edge·테스트·6단계 rollout).
- **그러나 ship-readiness 기준 설계 완성도는 ~70~75%.** 나머지 **~25~30%는 추가 설계 필요**, 그것도 흩어진 게 아니라 **3-4개 load-bearing seam에 집중**: (1) capacity menu + validator 경로(G1), (2) 에디터 data-block-id binding(G2), (3) prepare↔stream SlideSpec 영속 경계(G3), (4) export 검증 환경(G4).
- **단계별로 나누면:** Phase 1-2는 **설계·구현·검증 완료(100%)**. Phase 3는 G1으로 인해 설계 미완(핵심). Phase 4는 G2로 설계 미완. Phase 5-6은 G4 환경 게이트 미해결. 즉 **이미 지어진 1/3은 build-ready, 나머지 2/3은 설계 종결(design-close) 후에야 build-ready.**

**리뷰어 간 불일치 노트:** (a) asset 재귀(G7) — completeness만 major 우려, 나머지 둘은 이미 처리됨으로 봄(over-flag 가능성 높음). (b) `apply_capacity_fit` severity — implementability는 critical, completeness·correctness는 major. (c) open-risk는 잔여 위험이 fallback으로 묶여 "approach-invalidating 없음"이라 80을 줬으나, 동일 critical 2건(G2·G4)을 다른 리뷰는 더 무겁게 봄 — 종합 점수에서 open-risk를 down-weight한 이유.