# 재심사 종합 평가 (RE-SCORE)

## 1. 새 종합 점수: **85 / 100** (이전 71 → +14)

| Dimension | Weight | Prior | New | Δ | New 가중치 기여 |
|---|---|---|---|---|---|
| Correctness | 30% | 71 | 86 | **+15** | 25.80 |
| Implementability | 30% | 72 | 84 | **+12** | 25.20 |
| Completeness | 25% | 67 | 85 | **+18** | 21.25 |
| Open-risk | 15% | 80 | 86 | **+6** | 12.90 |
| **종합** | **100%** | **71** | **85.15 ≈ 85** | **+14** | **85.15** |

가장 크게 오른 축은 completeness(+18)와 correctness(+15) — 즉 "핵심 seam이 설계됐다"가 점수 상승의 본질이다. open-risk가 +6에 그친 것은 정직한 신호다: 설계 개정일 뿐 **새로운 경험적 검증이 0건** 추가되지 않았기 때문.

## 2. 개정 성공 여부 — G1~G4 봉합 판정 (리뷰어 across 중복 제거)

| Gap | 대응 | 종합 status | 근거 / skepticism 플래그 |
|---|---|---|---|
| **G1** capacity walker/validator | R1 | **CLOSED** (4/4 closed) | `ARCHETYPE_PROFILES` + closed discriminated-union 스키마 + `specToBlocks` + `validate_composition`, `apply_capacity_fit`는 legacy 격리. distinctness golden test 강제. 코드 검증됨(`layout_capacity.py:145` degenerate 실재). ⚠ 잔여: `validate_composition`의 split 경로가 `_split_content`(markdown-line) 재사용을 주장하나 **block-level split 함수가 없어 미정의** — closed 안의 hand-wave. |
| **G2** data-block-id 소실 | R2 | **CLOSED** (caveat) | `TiptapTextReplacer.tsx:90-96` strip 검증, 2-point surgical fix + `updateAdaptiveBlock` reducer + survival regression test 강제. ⚠ 잔여: literal `getAttribute('data-block-id')`가 **BULLETS 미처리**(id가 skip된 `<li>`에 있고 교체 대상은 inner `<span>`) → `.closest()` 필요; `onContentChange(path→binding)` 시그니처 변경의 caller 파급 미열거. |
| **G3** content-first 정합 + persist 경계 | R3 | **PARTIAL** (2 closed / 2 partial) | 권위 해소(compose_slides가 DeckPlan planner supersede), `/prepare`·`/stream` 경계 + SSE envelope byte 단위 보존(`presentation.py:290-528` 검증), len-invariant 명시 — 설계 hand-wave는 해소. ⚠ **그러나 PARTIAL 유지**: `deck_plan` 컬럼이 `models/sql/presentation.py`에 **부재**(검증), R3은 이를 "보존/no new column"이라 표기하나 실제로는 **신설 필요**(컬럼+accessor+alembic). content-first의 `get_deck_plan()→DeckPlanModel` vs 어댑티브가 필요한 `PresentationComposition(SlideSpec[])` **타입 불일치 미해소**. |
| **G4** byte 라운드트립 환경 차단 | R4 | **CLOSED** (caveat) | Linux/Docker/CI 레인 이전, local Windows `/pdf-maker`를 proxy로 재명명, Phase-6 blocking gate화. 인프라 실재 검증(ubuntu CI + chromium/libreoffice + `convert-linux-x64`). ⚠ 잔여: 라운드트립 test와 CI job이 **아직 존재하지 않고 어떤 archetype에도 1회도 실행된 적 없음**(runnable-but-unrun). |

부수 갭: **G5 CLOSED**(speaker_note 1급 필드화), **G7 CLOSED**(`dict_utils.py` dict+list 재귀 검증, 코드 변경 없이 test만), **G6 OPEN**, **G8 OPEN**, **G9 OPEN**, **G10 PARTIAL**.

**판정**: 4개 핵심 중 **G1·G2·G4는 실질 봉합**, **G3는 설계 권위는 봉합됐으나 빌드가 미구현 cross-doc 컬럼에 의존하여 PARTIAL**. R1~R6은 prior review가 지목한 "load-bearing seam" 3종(G1/G2/G3)을 약속이 아닌 코드 기반 사양으로 전환하는 데 성공했다.

**"약속일 뿐 사양 아님" skepticism 플래그**:
- **G6** — review MAJOR를 개정이 명시적으로 "minor"로 **격하·연기**("Phase 4·5 진입 시 상세화"). block-CRUD landing region / archetype-constrained type-picker / reorder / schema-driven property-panel 모두 미정. 명백한 promise-not-spec.
- **G8** — schema-valid rate / variety-adjacency / n_slides contract가 **test로만 §12에 존재, flag-on acceptance gate(임계값)로 승격 안 됨**. Phase 진입으로 연기.
- **G3** persist — 위 표의 컬럼 "보존" 표기.

## 3. 남은 갭 (~90 미달 원인, 순위)

1. **G8 composer 출력 안정성** — 가장 큰 잔여 **경험적** 리스크. content-quality 가치 명제 전체가 LLM이 discriminated-union을 distinct/varied/in-bound로 재현하느냐에 달렸는데, R4의 export gate에 대응하는 **정량 acceptance gate가 전무**. flag-default-off로 blast radius만 제한될 뿐 리스크는 미해소.
2. **G6 block-CRUD 에디터 UX** — 핵심 Phase-4 표면이 ~3 bullet로 명시 연기. 가치 명제의 일부.
3. **G3 cross-doc/forward 의존** — `deck_plan` 컬럼 + `set/get_deck_plan` accessor + alembic migration **미구현**, 별도 미구현 `content-first-design.md §2.3`에만 존재 → 두 문서를 합쳐 읽어야 완결. accessor 타입(`DeckPlanModel` vs `PresentationComposition`) 정합 glossed.
4. **Export keystone runnable-but-unrun** — G4 CI job/test 미작성, archetype 대상 무실행. G10 chart/table/bullets fidelity(§13.3/§13.4)는 fallback("editable을 vector edit로 재정의")으로만 bounded — 첫 실행이 제품 양보를 강제할 수 있음.
5. **G10 잔여 minors** — `slides_markdown` bypass × adaptive composer + `n_slides='auto'` 경로 미정의, theme §3.4 preset 값 미기재, Phase 1/2 file-list drift(shipped client `deriveThemeTokens` vs design backend rewrite) 미재기준화.
6. **G9 brand-slot** — built `AdaptiveSlide.tsx`가 `_logo_url__`/`__companyName__` 주입 키를 읽음(design §3.3 `data.brand`와 모순), `_logo_url__` single-underscore typo는 코드 전반 `__logo_url__` 컨벤션과 불일치 → 잠재 버그. R1~R6 미언급.
7. **사양 범위** — `ARCHETYPE_PROFILES` 14개 중 **3개만 구체 작성**, 11개는 패턴만(Phase 5 연기).
8. **국소 hand-wave** — `validate_composition` block-split 미구현, R2 bullets 바인딩·시그니처 파급 under-spec.

## 4. 평결 — Phase 3 빌드 착수 가능한가?

**조건부 GREEN — Phase 3 빌드 착수 가능(설계 준비 완료, 미구현 상태).**

- Phase 3 생성 코어(G1 composer 스키마/profiles, G3 persist 경계)는 코드 기반으로 **구성 가능(constructible)** 수준까지 설계됨. prior 71(코어 seam 미설계) → 85(코어 seam 설계, 주변부 연기)의 차이가 곧 빌드 착수 가능 여부의 차이다.
- **착수 전/병행 필수 조건**: (a) `deck_plan` 컬럼 + accessor + alembic을 **"보존"이 아닌 신설로 정직히 재기재**하고 `DeckPlanModel`/`PresentationComposition` 타입 정합 결정, (b) 3개 shipped archetype에 대해 `ARCHETYPE_PROFILES`/`validate_composition`/`specToBlocks` 실제 구현 + `_split_content` block-level 대응 결정(또는 YAGNI 명시 보류), (c) **flag default-on 이전** 게이트 2종 — G4 CI export 라운드트립, G8 composer-stability 임계값 — 수립.
- G2는 Phase-4(에디터), G6/G10 fidelity는 Phase-4/5 사안으로 **Phase-3 blocker 아님** → 연기 타당.

요약: **Phase 3 진입은 승인. 단 flag-on(ADAPTIVE_COMPOSER 기본 활성)은 G8·G4 게이트 구축 및 G3 컬럼/타입 정합 완결 이후**로 미뤄야 한다.

## 5. 정직성 체크 (hand-wavy / self-contradictory vs code)

- **G3 — self-contradictory vs code (가장 중대)**: R3가 `deck_plan` 컬럼을 "보존 / no new column"으로 기술하나, `models/sql/presentation.py:20-44`에 **컬럼 부재**가 검증됨. implementability 리뷰어가 "buildable but **mislabeled**, +real step"으로 명시. 또한 `get_deck_plan()→DeckPlanModel` vs 어댑티브 요구 `PresentationComposition(SlideSpec[])`의 타입 불일치를 glossed. SSE 봉인(`SlideOutlineModel.content`가 `str`인데 JSON SlideSpec를 투영 → markdown 기대 readers에 JSON 공급)도 mild repurpose로 인정됨.
- **G6 — self-serving 격하**: 개정이 review-MAJOR를 "minor"로 재라벨하고 연기. completeness 리뷰어가 "a promise, not a spec"으로 직격.
- **G9 — design/impl 모순 방치**: design §3.3 `data.brand` vs built `AdaptiveSlide.tsx:16,216`의 `_logo_url__`/`__companyName__`. `_logo_url__` typo(코드 116파일의 `__logo_url__` 컨벤션 위배)까지 미해결.
- **G1 — closed 내부 hand-wave**: `validate_composition` overflow split이 "apply_capacity_fit policy 재사용"을 주장하나 `_split_content`(L234-270)는 markdown-line 기반, block-level split 부재 → 해당 branch 미정의. composer-native split이 primary라 mitigated이지만 사양 공백은 사실.
- **G2 — 코드 부정확**: 제시된 literal `getAttribute('data-block-id')`가 BULLETS를 놓침(id가 skip된 `<li>`, 교체된 span엔 id 없음). `.closest('[data-block-id]')`로 trivially fixable이나 사양대로면 오작동.
- **정직성 가점**: 개정이 composer/JS-fit 리스크 등급을 medium→high로 **상향**(§11)한 것은 올바른 open-risk 신호다(이전 flattening 불만 해소). 또한 G4를 "dev box 검증 불가"에서 "CI 검증 가능, but 미실행"으로 정직히 runnable-but-unrun 표기.

**총평**: 점수 상승(+14)은 개정이 작성됐다는 이유가 아니라, 세 load-bearing seam이 코드 대조로 검증 가능한 사양으로 전환됐기 때문이다. 그러나 (1) 경험적 검증 0건, (2) G3의 "보존" 오표기, (3) G6/G8의 약속-격하라는 세 정직성 흠결이 85에서 더 올라가지 못하게 막는 핵심 요인이다.