# Deck IR 기반 AI PPT 생성·품질 아키텍처

- 상태: **설계 확정 v1.3, 구현 전**
- 적용 범위: Public v2 Authored 신규 생성, 페이지 재생성의 후속 확장, Fidelity PPTX/PDF, 향후 Editable Hybrid
- 작성 기준일: 2026-08-31
- 상위 권위: FastAPI PPT Core, PostgreSQL operation/outbox/lease, `AuthoredEmissionBundle`, QID/Scene, Fidelity/Hybrid 분리
- 핵심 결정: 신규 생성에서 모델의 slide별 raw HTML/CSS 작성을 제거하고 `Deck Director → Deck IR → Constraint Compiler`를 기본 생성 구조로 채택한다.

v1.3 누적 보완 범위:

- `RequirementGraph`, `SourceBinding`, coverage receipt를 정식 계약으로 추가
- generation intent와 Workbench edit lineage 분리
- balanced-quality의 global plan + 병렬 Slide IR batch 구조 확정
- browser measurement 기반 deterministic recompile closure 추가
- 일반/복구 성능 SLO를 수학적으로 분리
- generation, artifact, service-promotion receipt와 API authority 분리
- IR/critic artifact 보존·삭제, grammar ABI, progress/cancel, D6 이후 rollback 계약 추가
- raw 좌표 없이 자유로운 시각 구성을 표현하는 `CompositionIntentGraph` 추가
- grammar capability preflight와 operation 전체 단위 strategy 결정을 정식 receipt로 고정
- 기존 경로 대비 블라인드 시각 비열등성 평가와 2개 연속 frozen RC 통과를 D6의 선행 조건으로 추가
- baseline 품질에 종속되지 않는 절대 시각 품질 하한과 통계적 평가 유효성 계약 추가
- 추가 provider 호출 없이 bounded deterministic candidate set과 deck-level Pareto selection 추가
- 여백·초점·비대칭·이미지 처리·타이포 리듬을 안전하게 표현하는 `ArtDirectionIntent` 추가
- D6 이후 grammar/compiler/model 변경의 shadow·canary·자동 중단·rollback 품질 드리프트 계약 추가

이 문서는 구현 계획이 아니라 구현 전에 고정해야 하는 제품·아키텍처·데이터·품질·보안 계약이다. 이 문서만으로 임의의 기능 플래그를 활성화하거나 `promotionReady`를 변경하지 않는다.

## 1. 목표 결과

현재 slide별 provider HTML 저작 방식은 디자인 자유도가 높지만 provider 호출 수, 출력 토큰, capacity 오류, DOM/QID 오류, slide 간 디자인 편차가 함께 증가한다. 새로운 구조는 모델이 **무엇을 전달하고 어떤 시각 의도를 가질지** 결정하고, 서버가 **어떻게 안전하게 배치하고 출력할지** 결정한다.

```text
User brief / source / style request
                 |
                 v
 RequirementGraph + capability preflight
                 |
                 v
 strategy_decided(deck-ir | legacy | reject)
                 |
                 v
        Deck Director provider call
                 |
                 v
 Deck IR + CompositionIntent + ArtDirectionIntent
                 v
       validation + Design Grammar registry
                 |
                 v
 bounded candidate enumeration(K) + deck Pareto selection
                 v
 selected Constraint Compiler transaction
          /                    \
         v                      v
 authored HTML            semantic source
          \                    /
                 v
 browser observation / deterministic QA
                 |
       optional bounded critic patch
                 |
                 v
     AuthoredEmissionBundle + manifest
          /                    \
         v                      v
 Fidelity PPTX/PDF       Hybrid admission/Scene
```

목표는 다음 여섯 가지다.

1. 5장 생성의 provider critical path를 줄여 interactive p95 180초 이하를 달성한다.
2. 첫 candidate에서 구조·용량·시각 gate를 통과하는 slide 비율을 95% 이상으로 만든다.
3. deck 전체의 내용 흐름과 디자인 언어를 하나의 계획에서 고정한다.
4. 동일한 semantic/QID 계보로 Fidelity와 Hybrid를 만들 수 있게 한다.
5. baseline보다 낮지 않은 수준을 넘어 absolute expert quality floor를 충족한다.
6. ArtDirection과 bounded candidate search로 template 반복을 줄이고 D6 이후에도 품질 drift를 자동 차단한다.

## 2. 확정 결정과 비범위

### 2.1 확정 결정

- 신규 기본 경로에서 모델은 raw HTML, CSS, JavaScript, 외부 URL을 생성하지 않는다.
- 모델 출력은 strict structured `Deck IR`이며 unknown field를 허용하지 않는다.
- 서버가 versioned Design Grammar와 Constraint Compiler로 HTML과 semantic source를 함께 만든다.
- `Deck IR`은 생성 의도와 provenance다. 게시 후 visual authority는 `authoredHtml`, semantic authority는 `semanticSource`, deck authority는 active immutable manifest다.
- QID는 검증된 `slideKey`와 `elementKey`에서 compiler가 결정론적으로 만든다. 생성 후 조용한 QID 교체는 금지한다.
- 전체 deck 디자인 언어는 한 번만 정하고 slide는 그 언어 안에서 변주한다.
- 모델의 구성 자유도는 `CompositionIntentGraph`의 zone·관계·비율·시각 가중치로 표현하고, raw 좌표/CSS는 허용하지 않는다.
- 구성의 미학적 방향은 `ArtDirectionIntent`의 bounded enum과 policy로 표현하며 콘텐츠·source ownership과 hard geometry를 변경하지 않는다.
- Director/critic provider 호출 전에 grammar capability preflight를 수행하고 operation 전체를 `deck-ir`, `legacy-authored`, `reject` 중 하나로 결정한다. slide별 혼합 라우팅은 금지한다.
- deterministic QA를 먼저 실행하고, 모델 기반 visual critic은 위험 slide 또는 품질 모드에서 최대 1회만 실행한다.
- critic은 raw HTML을 반환하지 않고 허용된 patch DSL만 반환한다.
- unresolved blocking defect는 gate를 완화하지 않고 fail-closed한다.
- Fidelity가 기본 공개 출력이며 Hybrid readiness와 promotion/deployment는 계속 별도 상태다.
- Deck IR를 public default로 전환하거나 legacy author를 retire하려면 블라인드 `VisualNonRegressionGate`를 통과해야 한다.
- 비열등성만으로 품질을 승인하지 않는다. 절대 품질 하한과 통계적 평가 유효성을 동시에 통과해야 한다.
- compiler는 추가 provider 호출 없이 bounded candidate set을 만들고 deck 전체 품질·다양성 Pareto 선택을 수행한다.
- D6 이후에도 grammar/compiler/model/policy 변경은 새 RC로 취급하며 shadow와 canary를 건너뛸 수 없다.

### 2.2 비범위

- FastAPI/Next.js 전체 재작성
- PostgreSQL durable queue를 Redis/Kafka/SQS로 즉시 교체
- arbitrary HTML/CSS를 native OOXML로 직접 번역
- 모델에게 serializer capability, readiness, fallback 성공 여부를 결정하게 하는 것
- source-bound text를 요약하거나 바꾸는 자동 repair
- blocking 오류를 raster whole-deck fallback으로 숨기는 것
- `promotionReady` 또는 `deploymentAuthorized`의 수동 override
- 기존 HTML-only presentation의 즉시 일괄 migration

## 3. 기존 계약과의 호환성

| 기존 계약 | 유지 방식 |
|---|---|
| `POST /api/v2/presentations/generations` | 초기에는 wire contract를 바꾸지 않고 서버 feature flag로 새 engine을 선택한다. |
| `qualityPolicy=interactive-v1/release-v1` | 새 compiler/QA required gate 선택에 그대로 사용한다. |
| `WorkspaceOperation` | public 상태, lease, attempt, fencing, cancellation authority를 유지한다. |
| `ProviderCallReservation` | Director, optional critic, optional correction마다 먼저 durable reservation을 만든다. |
| authored checkpoint | IR, compile, observation, QA 단계 digest를 추가한다. |
| `AuthoredEmissionBundle` | compiled HTML/semantic source와 intent provenance digest를 함께 봉인한다. |
| `AuthoredDeckEmissionManifest` | ordered immutable slide bundle의 유일한 deck publication authority다. |
| Fidelity | compiled HTML을 Chromium에서 정확히 rasterize한다. |
| Hybrid | semantic declaration과 browser observation이 일치한 객체만 별도 native admission한다. |
| ArtifactRef | IR/receipt/HTML/PNG/PPTX/PDF의 locator이며 identity는 digest다. |

새 구조는 기존 public operation phase를 깨지 않는다. 세부 상태는 generation timing/attempt receipt에 기록하고 public phase에는 다음처럼 투영한다.

| 내부 단계 | Public v2 phase |
|---|---|
| normalize, director, IR validation | `authoring` |
| compile, static validation | `validating` |
| browser observation, montage QA | `rendering` |
| quality decision | `validating` |
| bundle/package creation | `packaging` |
| manifest CAS | `publishing` |

## 4. 컴포넌트와 쓰기 권한

| 컴포넌트 | 책임 | 금지 |
|---|---|---|
| FastAPI PPT Core | admission, owner scope, policy, operation, receipt, publication | provider 응답을 신뢰해 gate 우회 |
| Director adapter | provider structured response 요청, reservation, retry 분류 | DB/publication 직접 변경 |
| Strategy Preflight | requirement와 grammar capability를 비교하고 operation 전체 strategy 결정 | slide별 경로 혼합, 품질 강등 성공 처리 |
| Deck IR validator | schema, source, identity, size, content budget 검증 | 자동 의미 수정 |
| Design Grammar Registry | tokens, layouts, components, compiler capability 제공 | 사용자 콘텐츠 저장 |
| Constraint Compiler | IR을 HTML/semantic source로 결정론적 컴파일 | network access, provider 호출 |
| Candidate Enumerator/Deck Selector | bounded local 후보 생성, feasible/Pareto filtering, deck-level 다양성 선택 | provider 호출, hard constraint 미통과 후보 선택 |
| Browser Observer | 실제 geometry/paint/font/image readiness 측정 | semantic authority 생성 |
| Quality Orchestrator | static/browser/deck-level gate 종합 | blocking issue 무시 |
| Visual Evaluation Harness | frozen corpus 블라인드 A/B, 층별·최악구간·다양성 비열등성 판정 | cohort 제외, 결과 확인 후 margin 변경 |
| Quality Governance Controller | absolute/non-regression evidence, post-D6 shadow/canary/drift와 rollback authority | mutable alias를 qualified revision처럼 재사용 |
| Critic adapter | bounded montage/slide patch 제안 | raw HTML/CSS 출력, source text 변경 |
| Publisher | immutable bundle/manifest CAS | mutable component overwrite |
| Render/Export worker | network-none raster/package 생성 | owner DB, provider credential 보유 |

Constraint Compiler는 같은 input digest, grammar digest, compiler version, font/asset manifest에서 byte-stable canonical HTML/semantic JSON을 만들어야 한다. 브라우저 PNG는 renderer version 영향을 받으므로 별도 observation/output digest로 관리한다.

## 5. 핵심 데이터 계약

실제 JSON Schema 파일은 구현 단계에 추가한다. 아래 구조와 불변식은 설계 권위다.

### 5.1 `deck-director-input/v1`

provider에 전달하는 내부 요청 envelope다. DB에는 원문 prompt/response를 보관하지 않고 reservation, policy/version, input/output digest와 usage만 보관한다.

```json
{
  "schemaVersion": "deck-director-input/v1",
  "operationId": "uuid",
  "requestDigest": "sha256:...",
  "brief": "ephemeral user content",
  "language": "ko-KR",
  "slideCount": 5,
  "sourceSnapshotDigests": ["sha256:..."],
  "styleIntent": "enterprise-modern",
  "qualityPolicy": "interactive-v1",
  "designGrammarDigest": "sha256:...",
  "capacityPolicyDigest": "sha256:..."
}
```

불변식:

- `operationId`, owner/tenant authority, source snapshot은 server가 결속한다.
- provider는 source locator, filesystem path, object-store key를 보지 않는다.
- source text는 명확한 data delimiter 안에 전달하며 system/compiler 지시로 해석하지 않는다.
- 요청에 slide별 hard capacity와 허용 component kind가 포함된다.

### 5.2 `requirement-graph/v1`

사용자 요청과 source에서 반드시 충족해야 하는 항목을 Deck IR보다 먼저 server-owned identity로 고정한다. 이 graph가 없으면 "요청 coverage 100%"를 계산하지 않는다.

```json
{
  "schemaVersion": "requirement-graph/v1",
  "requestDigest": "sha256:...",
  "requirements": [
    {
      "requirementId": "req-001",
      "kind": "claim",
      "priority": "required",
      "normalizedIntent": "통합 플랫폼의 리드타임 개선 근거를 제시",
      "sourceBindingIds": ["src-001"],
      "minimumOccurrences": 1,
      "maximumOccurrences": 2,
      "allowedSlideRoles": ["evidence", "solution"]
    }
  ],
  "graphDigest": "sha256:..."
}
```

- `requirementId`는 server가 요청 정규화 단계에서 만든다.
- `required` 항목의 누락은 blocking이고 `optional` 누락은 receipt에 기록하되 blocking이 아니다.
- 의도적 반복은 `maximumOccurrences`와 slide의 `repetitionPurpose`로 명시한다.
- model이 requirement를 추가할 수는 있지만 요청/source에 없는 사실은 `derivedSuggestion`으로 격리하며 required coverage에 포함하지 않는다.
- requirement graph 자체의 model 보조 추출이 필요하면 별도 reservation/receipt를 사용하고 server가 source exactness를 검증한다.

### 5.3 `source-binding/v1`

```json
{
  "schemaVersion": "source-binding/v1",
  "sourceBindingId": "src-001",
  "snapshotDigest": "sha256:...",
  "blockId": "page-3.paragraph-2",
  "contentDigest": "sha256:...",
  "useMode": "verbatim",
  "allowedTransform": "none",
  "requiredOccurrences": 1,
  "normalizationPolicy": "unicode-nfc-whitespace-v1"
}
```

`useMode`:

- `verbatim`: 문자·숫자·단위·순서 변경 금지
- `structured-data`: typed table/chart payload로만 변환
- `paraphrase`: 의미 보존 요약 허용, 숫자·고유명사 exact
- `reference-only`: 사실 검증 근거이지만 화면에 직접 노출할 의무 없음

규칙:

- `snapshotDigest + blockId + contentDigest`가 source identity다.
- page/paragraph/table/cell/region identifier는 snapshot 내부 opaque ID이며 filesystem path가 아니다.
- `allowedTransform` 밖의 변환은 `deck_ir_source_binding_invalid`다.
- 하나의 binding을 여러 component가 사용할 수 있지만 각 occurrence와 visible/hidden use를 coverage receipt에 기록한다.
- source가 수정되면 새 snapshot/binding digest를 만들며 기존 Deck IR를 조용히 재해석하지 않는다.

### 5.4 `requirement-coverage-receipt/v1`

```json
{
  "schemaVersion": "requirement-coverage-receipt/v1",
  "requirementGraphDigest": "sha256:...",
  "deckIrDigest": "sha256:...",
  "requiredTotal": 12,
  "requiredCovered": 12,
  "optionalTotal": 4,
  "optionalCovered": 3,
  "duplicateViolations": [],
  "uncoveredRequirementIds": [],
  "occurrences": [
    {"requirementId": "req-001", "slideKey": "s03", "elementKey": "metric.primary"}
  ],
  "status": "passed",
  "receiptDigest": "sha256:..."
}
```

Coverage 100%는 `requiredCovered == requiredTotal`일 때만 의미한다. recap/summary의 의도적 반복은 duplicate violation이 아니며 requirement의 occurrence 범위 안에 있어야 한다.

### 5.5 `deck-ir/v1`

```json
{
  "schemaVersion": "deck-ir/v1",
  "deckKey": "deck",
  "title": "서비스 제안",
  "language": "ko-KR",
  "story": {
    "audience": "enterprise decision makers",
    "objective": "decision",
    "thesis": "통합 플랫폼이 개발 리드타임과 품질을 함께 개선한다",
    "arc": ["problem", "evidence", "solution", "execution", "decision"]
  },
  "designLanguage": {
    "grammarId": "enterprise-modular",
    "mood": ["credible", "progressive"],
    "paletteId": "blue-neutral-accent",
    "typeScaleId": "business-korean-v1",
    "gridId": "wide-12",
    "motifId": "connected-modules",
    "density": "medium",
    "variationBudget": 2
  },
  "slides": [
    {
      "slideKey": "s01",
      "role": "problem",
      "claim": "분절된 도구가 개발 리드타임을 늘린다",
      "evidence": [],
      "requirementIds": ["req-001"],
      "sourceBindingIds": ["src-001"],
      "compositionIntentGraphDigest": "sha256:...",
      "artDirectionIntentDigest": "sha256:...",
      "repetitionPurpose": "none",
      "layoutIntent": {
        "composition": "asymmetric-split",
        "emphasis": "right",
        "readingOrder": "title-summary-evidence",
        "density": "medium"
      },
      "components": []
    }
  ]
}
```

Deck-level validation:

- `slideKey`는 deck 안에서 unique하며 ordered slide identity다.
- slide 수는 admitted request와 정확히 일치한다.
- 모든 slide는 정확히 하나의 `role`과 하나의 non-empty `claim`을 가진다.
- `arc`의 필수 항목이 하나 이상의 slide에 매핑된다.
- 동일 claim/evidence의 비의도적 정규화 중복은 금지한다. recap/continuity/comparison 반복은 requirement occurrence 범위와 `repetitionPurpose`가 필요하다.
- design language는 registry의 허용 ID만 참조한다.
- 각 slide는 같은 `slideKey`의 validated CompositionIntentGraph digest를 정확히 하나 참조한다.
- 각 slide는 같은 `slideKey`의 validated ArtDirectionIntent digest를 정확히 하나 참조한다.
- source-bound 사실은 정확한 source binding을 가져야 한다.
- 전체 JSON depth, string length, component count, byte size는 policy cap을 넘을 수 없다.

### 5.6 `slide-ir/v1` component

각 component는 다음 공통 필드를 가진다.

```json
{
  "elementKey": "metric.primary",
  "kind": "metric",
  "semanticRole": "evidence",
  "contentPriority": "must-keep",
  "requirementIds": ["req-001"],
  "sourceBindingId": "src-001",
  "layoutSlot": "primary",
  "variant": "emphasis",
  "payload": {
    "value": "40%",
    "label": "리드타임 단축"
  }
}
```

초기 component kind:

- `text`, `metric`, `card`, `comparison`, `process`, `timeline`
- `image`, `quote`, `table`, `chart`, `shape`, `connector`, `group`, `decoration`

규칙:

- `elementKey`는 slide-local unique이고 `^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$`를 따른다.
- source-bound payload는 원문/데이터 digest와 정확히 일치해야 한다.
- model은 asset URL 대신 server-issued opaque asset slot만 참조한다.
- `table`과 `chart`는 typed payload만 허용한다. SVG/HTML 문자열은 payload가 아니다.
- `decoration`은 text/source/data를 소유할 수 없다.
- compiler capability 밖의 component/variant는 fail-closed하며 임의 유사 변환하지 않는다.

### 5.7 `composition-intent-graph/v1`

고정 template 선택만으로 slide를 만들지 않도록, 모델이 의미 영역과 상대 관계를 선언하는 안전한 구성 계약이다. 물리 좌표는 compiler가 결정한다.

```json
{
  "schemaVersion": "composition-intent-graph/v1",
  "slideKey": "s01",
  "zones": [
    {
      "zoneKey": "claim-zone",
      "semanticRole": "claim",
      "contentRefs": ["title", "summary"],
      "preferredColumnSpan": 5,
      "minimumColumnSpan": 4,
      "maximumColumnSpan": 7,
      "visualWeight": "dominant",
      "flow": "vertical"
    },
    {
      "zoneKey": "evidence-zone",
      "semanticRole": "evidence",
      "contentRefs": ["metric.primary"],
      "preferredColumnSpan": 7,
      "minimumColumnSpan": 5,
      "maximumColumnSpan": 8,
      "visualWeight": "supporting",
      "flow": "cluster"
    }
  ],
  "relations": [
    {
      "from": "claim-zone",
      "relation": "leftOf",
      "to": "evidence-zone",
      "strength": "hard"
    },
    {
      "from": "claim-zone",
      "relation": "alignTop",
      "to": "evidence-zone",
      "strength": "soft"
    }
  ],
  "readingOrder": ["claim-zone", "evidence-zone"],
  "graphDigest": "sha256:..."
}
```

허용 관계는 `leftOf`, `rightOf`, `above`, `below`, `alignTop`, `alignCenter`, `equalWidth`, `emphasis`, `flow`의 versioned enum이다.

불변식:

- `x`, `y`, `width`, `height`, `px`, raw CSS, selector, transform 문자열을 입력 계약에 넣지 않는다.
- span은 grammar가 선언한 bounded grid 단위이며 `minimum <= preferred <= maximum`이어야 한다.
- hard 방향 관계와 reading order는 cycle이 없어야 한다. 모순된 관계는 자동 삭제하지 않고 `composition_intent_graph_invalid`로 거절한다.
- 각 non-decoration `contentRef`는 정확히 하나의 owning zone에 속한다. relation은 콘텐츠나 source ownership을 바꾸지 않는다.
- graph는 구성 의도이지 최종 geometry authority가 아니다. compiler는 같은 의도를 만족하는 registered primitive와 layout variant를 탐색하고 실제 좌표·간격·폰트는 grammar token으로만 결정한다.
- graph digest는 Slide IR, compile receipt, browser measurement closure에 결속한다. critic은 허용 enum·zone weight·soft relation만 바꿀 수 있고 hard relation이나 content ownership은 바꿀 수 없다.

### 5.8 `art-direction-intent/v1`

CompositionIntentGraph가 공간 관계를 표현한다면 ArtDirectionIntent는 그 공간을 어떤 미학적 리듬으로 사용할지 선언한다. raw 스타일 문자열이 아니라 grammar가 해석하는 bounded vocabulary다.

```json
{
  "schemaVersion": "art-direction-intent/v1",
  "slideKey": "s01",
  "compositionIntentGraphDigest": "sha256:...",
  "focalAnchor": {"zoneKey": "evidence-zone", "strength": "strong"},
  "negativeSpace": {"bias": "right", "amount": "generous"},
  "asymmetry": "moderate",
  "overlapPolicy": {
    "mode": "decorative-layering",
    "allowedZonePairs": [["evidence-zone", "decoration-zone"]],
    "textOcclusion": "forbidden",
    "sourceOcclusion": "forbidden"
  },
  "imageTreatment": "edge-crop",
  "cropIntent": "subject-emphasis",
  "typographyRhythm": "editorial-contrast",
  "depthIntent": "layered",
  "visualEnergy": "progressive",
  "intentDigest": "sha256:..."
}
```

규칙:

- focal anchor, negative-space bias/amount, asymmetry, overlap, crop, image treatment, typography rhythm, depth, visual energy는 grammar manifest의 versioned enum만 사용한다.
- hex color, font name/size, shadow/filter, opacity, z-index, pixel coordinate, CSS/HTML/SVG 문자열은 허용하지 않는다.
- controlled overlap은 decoration/background와 명시된 safe zone pair에만 허용한다. visible text, source-bound content, chart/table label 가림은 항상 hard failure다.
- `negativeSpace.amount`는 콘텐츠 삭제 명령이 아니다. must-keep content가 공간 안에 들어가지 않으면 다른 candidate를 선택하거나 fail-closed한다.
- slide별 override는 deck design language와 충돌할 수 없으며, opening/body/evidence/closing 역할별 허용 범위는 grammar가 정한다.
- intent digest는 Deck IR, candidate set, compile/quality receipt에 결속한다. critic은 bounded enum만 바꿀 수 있고 content/source/QID를 바꿀 수 없다.

### 5.9 `design-grammar-manifest/v1`

```json
{
  "schemaVersion": "design-grammar-manifest/v1",
  "grammarId": "enterprise-modular",
  "version": 1,
  "tokensDigest": "sha256:...",
  "layoutRegistryDigest": "sha256:...",
  "componentRegistryDigest": "sha256:...",
  "artDirectionRegistryDigest": "sha256:...",
  "candidatePolicyDigest": "sha256:...",
  "fontPolicyDigest": "sha256:...",
  "capacityPolicyDigest": "sha256:...",
  "rendererAbiVersion": "design-component-renderer/v1",
  "solverAbiVersion": "constraint-solver/v1",
  "canonicalizationPolicy": "canonical-json-unicode-nfc-f64-v1",
  "compilerCompatibility": ["constraint-compiler/v1"],
  "deprecatedCapabilities": [],
  "manifestDigest": "sha256:..."
}
```

Design Grammar는 고정 slide template 목록이 아니다. 다음을 조합하는 versioned 문법이다.

- spatial primitives: safe area, grid, rail, split, stage, stack, flow
- information components: claim, metric, comparison, process, chart, quote
- visual tokens: palette, typography, spacing, radius, line, elevation
- art-direction vocabulary: focal, negative space, asymmetry, safe overlap, image/crop treatment, type rhythm, depth
- deck motif: furniture, numbering, connectors, recurring visual rhythm
- hard constraints, candidate policy와 Pareto objective floors/weights

ABI 규칙:

- manifest는 component/art-direction renderer, solver, candidate enumeration/Pareto selector, canonicalization, Unicode normalization, float formatting, locale, random-seed policy를 결속한다.
- compiler는 compatibility range 밖 grammar를 거절하고 최신 버전으로 조용히 변환하지 않는다.
- token/layout/component 폐기는 deprecated revision을 먼저 발행하고 기존 published bundle 재현 package를 retention 기간 동안 유지한다.
- grammar migration은 old/new digest, 변환 규칙, loss report를 가진 explicit receipt이며 published bundle을 in-place 변경하지 않는다.
- unsupported component는 stable error 또는 명시적 raster-capable Hybrid plan으로만 처리하며 유사 component로 바꾸지 않는다.

### 5.10 `generation-strategy-decision-receipt/v1`

Director/critic provider 비용이 발생하기 전에 요구사항과 선택 가능한 grammar/compiler 능력을 비교해 operation 전체 경로를 확정한다. RequirementGraph 보조 추출이 필요하면 그 독립 reservation/receipt는 이 결정의 입력으로 먼저 완료한다.

```json
{
  "schemaVersion": "generation-strategy-decision-receipt/v1",
  "operationId": "uuid",
  "requirementGraphDigest": "sha256:...",
  "requestedProfile": "balanced-quality-v1",
  "grammarCapabilityDigest": "sha256:...",
  "compilerCapabilityDigest": "sha256:...",
  "rolloutPhase": "D4",
  "selectedStrategy": "deck-ir",
  "missingCapabilities": [],
  "decisionReason": "all-required-capabilities-supported",
  "routingPolicyDigest": "sha256:...",
  "receiptDigest": "sha256:..."
}
```

규칙:

- preflight는 required component kind/variant, composition/art-direction enum, candidate policy, language·font, source use mode, asset/data type, density, accessibility와 output capability를 모두 평가한다.
- `selectedStrategy`는 `deck-ir`, `legacy-authored`, `reject` 중 하나이며 operation의 모든 slide와 모든 candidate에 동일하게 적용한다.
- D0–D5에서는 required capability가 하나라도 없으면 policy가 허용한 operation 전체를 `legacy-authored`로 보내거나 명시적으로 `reject`한다. generic template, raster-only, slide별 legacy 혼합으로 성공 처리하지 않는다.
- D6 이후에는 unsupported 요청을 `reject`한다. time-bounded dormant legacy package 사용은 별도 operator authorization receipt가 있을 때만 operation 전체에 허용한다.
- 시작된 operation의 strategy, grammar, compiler digest는 terminal까지 고정한다. 재시도·critic·correction이 strategy를 바꿀 수 없다.

### 5.11 `constraint-compile-receipt/v1`

```json
{
  "schemaVersion": "constraint-compile-receipt/v1",
  "operationId": "uuid",
  "slideKey": "s01",
  "strategyDecisionDigest": "sha256:...",
  "deckIrDigest": "sha256:...",
  "slideIrDigest": "sha256:...",
  "compositionIntentGraphDigest": "sha256:...",
  "artDirectionIntentDigest": "sha256:...",
  "candidateSelectionReceiptDigest": "sha256:...",
  "grammarDigest": "sha256:...",
  "compilerVersion": "constraint-compiler/v1",
  "chosenLayoutId": "asymmetric-split/2",
  "qidRegistryDigest": "sha256:...",
  "htmlDigest": "sha256:...",
  "semanticDigest": "sha256:...",
  "hardConstraintStatus": "passed",
  "softScore": 0,
  "measurementClosureRevision": 2,
  "browserMeasurementDigest": "sha256:...",
  "recoverySteps": [],
  "receiptDigest": "sha256:..."
}
```

`softScore`는 다른 RC/grammar 사이의 품질 성능 주장이 아니다. 같은 compiler input 안에서 feasible candidate를 결정론적으로 선택하기 위한 내부 정렬 값이다.

### 5.12 `candidate-set-selection-receipt/v1`

추가 provider 호출 없이 한 slide의 여러 feasible 구성을 탐색하고 deck 전체 조합을 선택한 증거다.

```json
{
  "schemaVersion": "candidate-set-selection-receipt/v1",
  "operationId": "uuid",
  "deckIrDigest": "sha256:...",
  "grammarDigest": "sha256:...",
  "compilerVersion": "constraint-compiler/v1",
  "candidatePolicyDigest": "sha256:...",
  "slides": [
    {
      "slideKey": "s01",
      "requestedK": 6,
      "enumeratedCount": 6,
      "feasibleCount": 4,
      "candidates": [
        {
          "candidateId": "c01",
          "layoutId": "asymmetric-split/2",
          "hardConstraintStatus": "passed",
          "qualityVector": {"hierarchy": 0.9, "whitespace": 0.8, "roleFitness": 0.9},
          "diversitySignature": "sha256:...",
          "candidateDigest": "sha256:..."
        }
      ],
      "selectedCandidateId": "c01"
    }
  ],
  "deckSelection": {
    "algorithm": "pareto-beam-v1",
    "beamWidth": 8,
    "objectivePolicyDigest": "sha256:...",
    "selectedCombinationDigest": "sha256:..."
  },
  "providerCallsAdded": 0,
  "receiptDigest": "sha256:..."
}
```

규칙:

- `balanced-quality-v1`은 slide별 `K=6`을 기본으로 하고 policy 허용 범위는 `4..8`이다. `fast-preview-v1`은 `K=2`이며 release default authority가 아니다.
- 후보 열거 순서는 같은 IR/grammar/compiler/art-direction/candidate-policy digest에서 동일하다. wall clock이나 비고정 random seed를 사용하지 않는다.
- static hard constraint를 통과한 후보만 Pareto set에 들어간다. quality vector의 높은 평균으로 hard failure를 상쇄할 수 없다.
- deck selector는 hierarchy, whitespace, role fitness, ArtDirection 일치, composition diversity, consecutive repetition을 함께 최적화한다. beam width는 최대 8이고 동일 입력에서 동일 조합을 선택한다.
- interactive 경로는 선택 후보만 browser measurement한다. 실패 시 같은 ranked set의 다음 feasible 후보를 기존 measurement closure 최대 2회 안에서 사용한다.
- shadow/release evaluation은 policy가 지정한 위험 slide의 상위 2개 후보를 render할 수 있지만 모든 시간과 비용을 evidence에 포함한다.
- candidate set은 publication authority가 아니다. 선택된 candidate의 compile receipt와 bundle만 publish될 수 있다.

### 5.13 `deck-critic-patch/v1`

```json
{
  "schemaVersion": "deck-critic-patch/v1",
  "deckIrDigest": "sha256:...",
  "observationDigest": "sha256:...",
  "targets": [
    {
      "slideKey": "s04",
      "allowedOperations": [
        {"op": "replaceLayoutVariant", "value": "asymmetric-split/3"},
        {"op": "setDensity", "value": "low"}
      ]
    }
  ]
}
```

허용 patch:

- `replaceLayoutVariant`
- `setDensity`
- `setEmphasis`
- `setComponentVariant`
- `setAssetCropIntent`
- `setReadingOrderVariant`
- `setFocalStrength`
- `setNegativeSpaceIntent`
- `setAsymmetryIntent`
- `setImageTreatment`
- `setTypographyRhythm`

금지 patch:

- raw HTML/CSS/JavaScript
- 임의 좌표·폰트 크기·색상 문자열 직접 주입
- title/source-bound text 변경
- QID/elementKey 변경
- component 추가·삭제를 통한 사실 누락
- content priority, claim, evidence, requirement/source binding 변경
- hard composition relation, overlap safety, text/source occlusion policy 변경
- capability/readiness/fallback 결과 지정

Critic은 시각 구조만 제안한다. 콘텐츠 우선순위나 문구를 바꿔야 하는 결함은 `deck_visual_quality_failed`로 숨기지 않고 별도의 Director correction으로 보내며, correction 결과는 requirement/source coverage를 처음부터 다시 검증한다. Critic과 Director correction은 하나의 shared recovery budget을 사용하므로 한 attempt에서 둘 다 실행하지 않는다.

### 5.14 `generation-intent-lineage/v1`

Deck IR은 생성 시점 의도 provenance이며 Workbench 이후 current visual/semantic authority가 아니다.

생성 bundle:

```json
{
  "intentConsistencyState": "exact",
  "generationIntentDigest": "sha256:deck-ir",
  "compileReceiptDigest": "sha256:compile-receipt",
  "parentBundleDigest": null,
  "editCommandReceiptDigest": null
}
```

편집 bundle:

```json
{
  "intentConsistencyState": "derived-by-command",
  "originGenerationIntentDigest": "sha256:deck-ir",
  "generationIntentDigest": null,
  "compileReceiptDigest": "sha256:current-compile-receipt",
  "parentBundleDigest": "sha256:previous-bundle",
  "editCommandReceiptDigest": "sha256:edit-command"
}
```

상태:

- `exact`: current HTML/semantic source가 해당 Deck IR compile 결과와 exact
- `derived-by-command`: typed Workbench command와 deterministic reducer로 파생
- `origin-only`: legacy/import/비결정적 편집으로 최초 생성 의도만 provenance로 남음
- `invalidated`: lineage 또는 receipt 검증 실패, publish/export 불가

Workbench는 Deck IR을 current truth처럼 다시 쓰지 않는다. current bundle은 parent, typed command, compile receipt로 설명하고 원래 intent는 `originGenerationIntentDigest`로만 유지한다.

### 5.15 `visual-quality-evaluation-policy/v1`

결과를 보기 전에 absolute quality, non-inferiority, 표본·평가자·통계 판정을 고정하는 정책이다.

```json
{
  "schemaVersion": "visual-quality-evaluation-policy/v1",
  "corpusDigest": "sha256:...",
  "sealedHoldoutDigest": "sha256:...",
  "studyDesign": "paired-stratified-blind",
  "powerAnalysis": {
    "oneSidedAlpha": 0.025,
    "targetPower": 0.8,
    "nonInferiorityMargin": 0.25,
    "minimumOverallPairs": 120,
    "minimumPairsPerRequiredMarginalStratum": 30
  },
  "reviewProtocol": {
    "reviewersPerPair": 3,
    "ratingScale": "1..5",
    "calibrationRequired": true,
    "pathRcAndFilenameBlinded": true,
    "tieHandling": "explicit-tie"
  },
  "absoluteFloors": {
    "overallExpertMeanLowerBound": 4.0,
    "requiredDimensionMeanLowerBound": 3.75,
    "worstDecileMeanLowerBound": 3.5,
    "p0Maximum": 0,
    "p1Maximum": 0
  },
  "pairwisePreference": {
    "candidatePreferredOrTiedPointMinimum": 0.9,
    "oneSided95LowerBoundMinimum": 0.85
  },
  "reliability": {
    "metric": "krippendorff-alpha-ordinal",
    "minimum": 0.67,
    "belowMinimumAction": "evaluation-invalid-recalibrate"
  },
  "confidenceMethod": "stratified-bootstrap-one-sided-95-v1",
  "multiplicityCorrection": "holm-v1",
  "policyDigest": "sha256:..."
}
```

- `4.0/5`는 전문가가 수정 없이 공개 가능한 수준, `3.75/5`는 필수 품질 차원의 하한, `3.5/5`는 최악 10%가 명백한 재작업 상태로 내려가지 않는 하한으로 정의한다.
- 표본 수는 power analysis 결과가 위 최소치보다 크면 큰 값을 사용한다. required stratum 교차는 frozen covering array로 구성하고 각 marginal stratum의 최소 표본을 보장한다.
- non-inferiority는 candidate-baseline 차이의 one-sided 95% lower bound가 `-0.25` 이상이어야 한다. required stratum/dimension 다중 비교는 Holm 보정한다.
- pairwise preferred-or-tied는 point estimate `>= 0.90`과 one-sided 95% lower bound `>= 0.85`를 동시에 만족한다.
- 평가자 일치도가 `0.67` 미만이면 candidate pass/fail이 아니라 평가 자체가 `invalid`다. 결과를 공개하지 않은 채 reviewer 재교정과 새 blind assignment를 수행한다.
- holdout 일부는 grammar/compiler/model 튜닝에 사용할 수 없고 Quality Governance Controller만 해제한다. corpus 교체도 새 policy digest와 bridge study가 필요하다.

### 5.16 `visual-non-regression-report/v1`

Deck IR 경로가 기존 authored 경로보다 시각 품질·표현 다양성을 떨어뜨리지 않았음을 frozen corpus와 blinded review로 증명하는 RC 단위 계약이다.

```json
{
  "schemaVersion": "visual-non-regression-report/v1",
  "baselineRcDigest": "sha256:...",
  "candidateRcDigest": "sha256:...",
  "corpusDigest": "sha256:...",
  "evaluationPolicyDigest": "sha256:...",
  "strata": ["archetype:proposal", "language:ko-KR", "density:medium", "source:bound"],
  "blindAssignmentDigest": "sha256:...",
  "reviewerReceiptDigests": ["sha256:...", "sha256:...", "sha256:..."],
  "evaluationValidity": "valid",
  "overallPairCount": 120,
  "minimumMarginalStratumPairCount": 30,
  "reviewerAgreement": 0.74,
  "requiredCoverageRatio": 1.0,
  "p0Count": 0,
  "p1Count": 0,
  "absoluteOverallMeanLowerBound": 4.08,
  "absoluteRequiredDimensionMinimumLowerBound": 3.82,
  "absoluteWorstDecileMeanLowerBound": 3.57,
  "candidatePreferredOrTiedRatio": 0.92,
  "nonInferiorityLowerBound": -0.12,
  "worstDecileRatingDelta": -0.1,
  "compositionEntropyDelta": -0.01,
  "consecutiveRepeatRateDelta": 0.0,
  "absoluteQualityStatus": "passed",
  "nonInferiorityStatus": "passed",
  "failedStrata": [],
  "status": "passed",
  "reportDigest": "sha256:..."
}
```

- 평가 corpus, 층, sample size, reviewer pool, 질문, 비열등 margin은 결과를 보기 전에 `evaluationPolicyDigest`로 고정한다.
- 각 pair는 경로·RC·파일명을 숨긴 상태에서 독립 reviewer 최소 3명이 평가한다. 동일인이 같은 pair를 중복 평가할 수 없다.
- report에는 pairwise 선호/동률뿐 아니라 typography hierarchy, whitespace, density, image/chart fit, opening/closing, composition diversity, exact layout 반복, 최악 10% 결과를 포함한다.
- 평균이 높은 층으로 낮은 층을 상쇄하지 않는다. required stratum 하나라도 실패하면 전체 report는 `failed`다.
- review 원자료와 assignment는 owner/PII를 제거한 immutable digest로 결속하고, reviewer identity는 접근 통제된 별도 저장소에 둔다.

### 5.17 `quality-drift-receipt/v1`

D6 이후에도 grammar/compiler/model/evaluation policy 변경이 qualified 품질을 유지하는지 추적하는 change-set 단위 receipt다.

```json
{
  "schemaVersion": "quality-drift-receipt/v1",
  "candidateRcDigest": "sha256:...",
  "previousQualifiedRcDigest": "sha256:...",
  "changedDigests": {"grammar": "sha256:...", "compiler": "sha256:...", "modelFingerprint": "sha256:..."},
  "shadowVisualReportDigest": "sha256:...",
  "canaryWindows": [
    {"cohortPercent": 1, "minimumOperations": 100, "minimumHours": 24, "status": "passed"}
  ],
  "observedDrift": {"p0": 0, "p1": 0, "repeatRateDelta": 0.0, "p95LatencyDeltaMs": 0},
  "triggeredRules": [],
  "action": "advance-canary",
  "rollbackTargetRcDigest": "sha256:...",
  "receiptDigest": "sha256:..."
}
```

- mutable provider model alias나 같은 이름의 grammar/compiler 배포를 qualified revision으로 간주하지 않는다. observed model fingerprint 또는 digest가 바뀌면 새 RC다.
- 단계는 `shadow → 1%/100 operations/24h → 5%/500/24h → 25%/1000/48h → 100%`이며 각 단계는 최소 operation과 최소 시간을 모두 채운다.
- confirmed P0/source corruption/cross-render artifact corruption 1건, confirmed P1 1건, absolute/non-inferiority floor 실패는 즉시 admission pause와 이전 qualified RC rollback을 요구한다.
- generation failure rate가 baseline보다 2 percentage points 초과하거나 p95 180초 초과가 최소 100-operation rolling window 두 번 연속 발생하면 canary를 중단한다.
- composition entropy `-0.05` 미만 또는 consecutive repeat rate `+0.02` 초과가 두 window 연속이면 quality drift로 중단한다.
- 운영 sample은 owner-safe opt-in/redaction policy를 따르며 사용자 원문을 drift metric label이나 일반 로그에 넣지 않는다.

## 6. Identity, digest, QID 계약

QID는 model 자유 문자열이 아니라 다음 입력의 결정론적 함수다.

```text
qid = normalize(slideKey + "." + elementKey)
```

- 같은 slide revision에서 QID는 unique다.
- compiler는 validation 전에 QID를 만들고 HTML과 semantic source에 동시에 쓴다.
- 이후 단계는 QID를 변경하지 않는다.
- page remake는 새 origin generation lineage를 만들 수 있다.
- Workbench edit는 기존 element identity registry를 유지하고 명시적 add/remove command만 허용한다.

Digest graph:

1. `strategyDecisionDigest = SHA256(canonical GenerationStrategyDecisionReceipt)`
2. `compositionIntentGraphDigest = SHA256(canonical CompositionIntentGraph JSON)`
3. `artDirectionIntentDigest = SHA256(canonical ArtDirectionIntent JSON)`
4. `deckIrDigest = SHA256(canonical Deck IR JSON)`이며 각 slide의 composition/art-direction digest를 포함한다.
5. `grammarDigest = SHA256(canonical grammar manifest)`
6. `candidateSetSelectionDigest = SHA256(canonical CandidateSetSelectionReceipt)`
7. `compileInputDigest = SHA256(strategyDecisionDigest + deckIrDigest + grammarDigest + candidateSetSelectionDigest + compilerVersion + assetManifestDigest + fontManifestDigest + policyDigest)`
8. `htmlDigest = SHA256(canonical UTF-8 HTML)`
9. `semanticDigest = SHA256(canonical semantic JSON)`
10. `compileReceiptDigest = SHA256(receipt without self digest and locators)`
11. 생성 bundle의 `bundleDigest`에는 exact `generationIntentDigest`, strategy/candidate decision과 compile receipt를 추가한다.
12. 편집 bundle의 `bundleDigest`에는 `originGenerationIntentDigest`, parent bundle, edit command, current compile receipt, `intentConsistencyState`를 추가하며 Deck IR exactness를 주장하지 않는다.
13. 기존 deck `canonicalDigest`는 ordered bundle digest를 통해 간접 결속한다.

Artifact location, signed URL, local path, timestamp는 content identity 입력이 아니다.

## 7. 생성 파이프라인과 상태 전이

```text
accepted
  → input_normalized
  → requirement_graph_ready
  → grammar_capability_preflight
  → strategy_decided(deck-ir | legacy-authored | reject)
  → [legacy-authored는 별도 기존 state machine으로 위임]
  → director_reserved
  → director_running
  → ir_received
  → ir_composition_art_direction_validating
  → ir_accepted
  → candidate_enumerating
  → candidate_feasibility_filtering
  → deck_pareto_selecting
  → selected_candidate_compiling
  → compiled
  → browser_measuring
  → [measurement_feedback → deterministic_recompile → browser_remeasure] × 최대 2
  → deterministic_quality
  → [critic_reserved → critic_running → patch_validating → candidate_reenumerating → recompiling]
  → quality_accepted
  → bundle_staging
  → manifest_publishing
  → published
```

Terminal/대기 상태:

- `capacity_waiting`: owner/tenant/provider/compiler/browser slot 대기
- `retry_waiting`: response가 시작되기 전 transient provider 실패
- `failed`: schema/source/capacity/compile/quality 결정적 실패 또는 budget 소진
- `cancelled`: 사용자 취소
- `dead_letter`: 자동 복구 한도 초과

중간 IR, HTML, observation은 detached artifact다. active presentation API와 export는 published manifest만 읽는다.

`strategy_decided` 이후 이 state machine에 들어오는 것은 `deck-ir` operation뿐이다. `legacy-authored`는 기존 state machine에서 끝까지 처리하고, 두 경로의 slide·candidate·bundle·quality receipt를 합치지 않는다. `reject`는 provider reservation 전에 stable capability error로 종료한다.

### 7.1 Provider 호출 계획

`balanced-quality-v1`은 하나의 큰 응답보다 global consistency와 slide-level failure isolation을 함께 확보한다.

| slide 수 | initial Director 계획 | shared recovery budget | attempt 최대 logical calls |
|---:|---|---:|---:|
| 1–8 | global DeckPlan 1회 + Slide IR batch 2회 병렬 | critic 또는 Director correction 1회 | 4 |
| 9–20 | global DeckPlan 1회 + Slide IR batch 최대 3회 병렬 | critic 또는 correction 1회 | 5 |
| 21–30 | global DeckPlan 1회 + Slide IR batch 최대 4회 병렬 | critic 또는 correction 1회 | 6 |

`fast-preview-v1`은 1–8장 전체 Deck IR 단일 호출과 correction 최대 1회를 허용하지만, 별도 evidence 전에는 내부 preview/shadow 전용이며 Fidelity release default가 아니다.

- public production policy의 기본 최대는 30장이다. wire contract의 더 큰 상한은 내부/호환 범위이며 별도 admission policy가 필요하다.
- balanced batch는 2–8장 범위에서 나누며 5장은 `3 + 2` 두 batch를 기본으로 한다.
- batch는 slide 순서, requirement graph, global DeckPlan, design language digest를 공유한다.
- batch는 provider weighted semaphore 안에서 병렬 실행하고 한 batch 실패가 이미 검증된 다른 batch를 무효화하지 않는다. 다만 전체 Deck IR가 완성되기 전 publication은 없다.
- provider response streaming 중 완성된 batch는 detached checkpoint로 검증할 수 있지만 전체 Deck IR가 통과하기 전 compile/publish authority를 얻지 않는다.
- response event가 시작된 뒤 transport retry하지 않는다. 부분 결과 중복과 과금을 방지한다.
- validation, source mismatch, deterministic capacity failure는 같은 prompt로 재호출하지 않는다. schema/content correction은 failed batch만 대상으로 하며 shared recovery budget을 소비한다.
- model 교체는 별도 A/B 품질 증거가 있을 때만 한다.
- Director/critic provider 예약 전 `GenerationStrategyDecisionReceipt`가 sealed되어야 하며, Director와 critic은 선택된 strategy를 변경할 권한이 없다.
- candidate enumeration과 deck selection은 local deterministic 단계이며 provider logical call/token budget을 증가시키지 않는다.

## 8. Constraint Compiler 설계

### 8.1 Hard constraints

- 1280×720 viewport와 safe-area containment
- title/body/visible text capacity
- minimum font 15px
- required component/source coverage
- QID uniqueness와 semantic role
- asset/font readiness
- no overflow, clipping, unintended overlap
- connector endpoint clearance
- reading order와 accessibility label
- source-bound exactness
- 허용 component/layout/variant만 사용
- CompositionIntentGraph hard relation의 무순환성, zone span 범위, content ownership
- ArtDirectionIntent enum/role policy와 text/source occlusion 금지

Hard constraint를 하나라도 만족하지 못한 candidate는 선택될 수 없다.

Static compiler는 실제 glyph/line/paint bounds를 추측해 최종 승인하지 않는다. typography와 geometry closure는 다음 bounded deterministic loop가 소유한다.

```text
compile(candidate N)
  → isolated browser measurement
  → BrowserConstraintFeedbackReceipt
  → same IR + same grammar에서 deterministic recompile(candidate N+1)
  → isolated browser remeasure
  → 최대 2회 후 passed 또는 blocking failure
```

`BrowserConstraintFeedbackReceipt`는 slide/QID별 measured line rect, painted bounds, overflow/clip/overlap, resolved font, image decode, connector clearance와 renderer/font/policy digest를 가진다. compiler가 변경할 수 있는 것은 grammar가 선언한 layout variant와 discrete token뿐이다. browser가 semantic source나 콘텐츠를 수정하지 않는다. 동일 measurement receipt와 compiler input은 동일한 다음 candidate를 만들어야 한다.

### 8.2 Composition intent solver

Composition solver는 `CompositionIntentGraph`와 `ArtDirectionIntent`를 Design Grammar의 safe primitive로 내린다.

```text
semantic zones + bounded spans + relation graph + art direction
  → relation/cycle/content-ownership validation
  → compatible primitive family enumeration
  → bounded grid placement
  → hard-constraint filtering
  → soft-score ordering
  → discrete layout variant
```

- primitive는 `grid`, `rail`, `split`, `stage`, `stack`, `flow`, `cluster`, `inset`, `band`처럼 registry가 구현·검증한 종류만 사용한다.
- zone 관계와 시각 가중치는 배치를 열어 두지만 pixel-perfect 명령은 아니다. 동일 intent에서도 slide role, density, motif와 이웃 slide repetition penalty에 따라 다른 registered variant가 선택될 수 있다.
- 그림·차트·metric의 visual weight, zone proportion과 alignment 관계를 조합해 template 목록보다 넓은 구성을 만들되, 모든 결과는 safe area·typography·source·reading-order hard constraint 안에 있어야 한다.
- negative space, focal anchor, bounded overlap, crop/image treatment와 typography rhythm은 grammar enum을 통해 후보를 분화하며 raw style로 변환되지 않는다.
- feasible variant가 없으면 generic template로 강등하지 않고 `deck_ir_capacity_unsatisfiable` 또는 `composition_intent_graph_invalid`로 끝낸다.

### 8.3 Bounded candidate enumeration과 deck selection

한 개의 greedy candidate를 즉시 확정하지 않는다. balanced-quality는 slide별 6개 candidate를 결정론적으로 열거하고 static hard constraint를 통과한 Pareto set을 만든다.

```text
per-slide K=6 deterministic candidates
  → static hard filter
  → local quality vector + diversity signature
  → deck beam search B<=8
  → global Pareto frontier
  → deterministic selected combination
  → selected candidate browser closure
```

- local quality vector는 scalar 하나가 아니라 hierarchy, whitespace, density, role fitness, art-direction alignment를 유지한다.
- global objective는 story role별 적합성과 deck cohesion을 유지하면서 composition family, focal pattern, image treatment, typography rhythm의 반복을 제한한다.
- 어느 한 quality dimension의 극단적 저하를 다른 dimension 평균으로 숨기지 않도록 policy별 minimum vector floor를 먼저 적용한다.
- 선택 후보의 browser hard failure는 ranked feasible set의 다음 후보로 이동하되 기존 2회 closure budget과 absolute deadline을 늘리지 않는다.
- candidate enumeration/selection receipt가 없거나 입력 digest와 맞지 않으면 compile/publish하지 않는다.

### 8.4 Soft scoring

feasible candidate에 한해서 다음을 평가한다.

- visual hierarchy
- balance와 whitespace
- density consistency
- claim/evidence prominence
- deck motif consistency
- consecutive layout repetition penalty
- slide role에 맞는 component fitness
- 이전 slide와의 시각적 변주
- focal/negative-space/overlap/image/type-rhythm ArtDirection 일치

동점은 `(layoutId, variantId, canonical candidate digest)` 순서로 결정해 재현성을 보장한다.

### 8.5 Recovery ladder

compiler는 아래 순서만 사용한다.

1. 동일 layout 안의 허용 slot 재배치
2. 같은 composition family의 다른 variant 선택
3. discrete spacing/type-scale token 한 단계 조정
4. optional/low-priority supporting text의 결정론적 축약
5. source-free ordinary slide에 한해 최대 3개 owner의 bounded shortening
6. browser measurement feedback 기반 deterministic recompile 최대 2회
7. content가 아닌 전체 구성 결함에 한해 optional critic patch 1회
8. `deck_ir_capacity_unsatisfiable` 또는 `deck_visual_quality_failed`로 fail-closed

금지:

- 15px 미만 축소
- source/title 보호 토큰 변경
- component나 근거를 조용히 제거
- 전체 slide를 임의 raster로 강등해 generation 성공 처리
- 무한 repair loop

### 8.6 HTML과 semantic source 생성

- HTML은 server-owned component renderer만 만든다.
- inline script, event handler, iframe, remote URL은 생성할 수 없다.
- CSS는 grammar token과 compiler-generated scoped class만 사용한다.
- semantic source는 같은 IR traversal에서 함께 생성한다.
- HTML과 semantic source의 QID, text/data/asset digest는 compile 직후 exact 비교한다.
- browser observation은 실제 geometry/paint evidence를 추가하지만 semantic source를 재작성하지 않는다.

## 9. 품질 시스템

### 9.1 Content/semantic gate

- `RequirementCoverageReceipt` 기준 required coverage 100%
- slide마다 하나의 명확한 claim
- title과 claim의 의미 일치
- 인접/전체 deck의 비의도적 중복 claim·evidence 0
- recap/continuity/comparison 반복은 `repetitionPurpose`와 requirement occurrence 범위 필요
- source-bound 수치·날짜·이름 exact
- 결론에서 검증되지 않은 새 사실 추가 금지
- 빈 component, placeholder, lorem ipsum 0
- story arc의 필수 role 누락 0

### 9.2 Compile/static gate

- schema/QID/role/source/asset/font binding
- hard capacity와 component limits
- semantic order와 accessibility label
- CSS/DOM allowlist
- deterministic recompile digest 일치

### 9.3 Browser gate

- painted bound viewport containment
- overflow/clip/overlap/ghosting 0
- font load와 image decode 완료
- minimum font/contrast
- line-break/connector clearance
- animation/network/mutation quiescence
- captured PNG와 observation receipt exact binding

### 9.4 Deck-level gate

- montage 기준 slide 순서와 story progression
- 동일 composition 3장 연속은 soft repetition penalty로 처리하며 역할상 필요한 반복은 허용
- furniture, palette, type scale 일관성
- density outlier와 과도한 whitespace 탐지
- 반복 이미지/아이콘/문구 탐지
- opening/closing 시각적 완결성

Deck-level deterministic signal이 충분하면 critic을 호출하지 않는다. critic이 필요한 경우에도 montage 1회만 전달하고 blocking candidate slide만 patch 대상이 된다.

Visual severity:

- **P0:** source/숫자/identity 손상, slide 누락, artifact 불능, 보안 경계 위반
- **P1:** visible overflow/clip/overlap, 읽을 수 없는 글자, 핵심 claim 가림, 잘못된 reading order
- **P2:** 비blocking 정렬·리듬·반복·장식 품질 저하

interactive publication은 P0/P1이 0이어야 한다. P2는 policy threshold와 receipt에 따라 허용할 수 있지만 조용히 숨기지 않는다. 사람/독립 review도 같은 severity vocabulary를 사용한다.

### 9.5 Absolute visual quality gate

baseline이 낮아도 candidate가 통과하는 문제를 막는다. public default와 release RC는 비교 결과와 무관하게 다음 absolute floor를 먼저 통과해야 한다.

- 전체 expert rating의 one-sided 95% lower bound `>= 4.0/5`
- typography hierarchy, whitespace, density, information clarity, image/chart fit, composition, opening/closing 각 required dimension의 lower bound `>= 3.75/5`
- slide/deck rating worst 10% mean lower bound `>= 3.5/5`
- required coverage `1.0`, P0/P1 `0`, placeholder/empty/missing artifact `0`
- 각 required archetype/language/density/source/asset stratum이 같은 absolute floor를 독립적으로 통과

absolute floor는 RC 결과를 본 뒤 낮출 수 없다. 변경하려면 새 evaluation policy, 독립 승인과 old/new bridge study가 필요하며 낮은 baseline 또는 빠른 latency로 시각 품질 부족을 상쇄하지 않는다.

### 9.6 Statistical blind evaluation validity

`VisualQualityEvaluationPolicy`가 study 시작 전에 sealed되어야 한다.

- overall pair 최소 120, required marginal stratum별 최소 30이며 더 큰 power-analysis 표본이 계산되면 그 값을 사용한다.
- candidate-baseline paired difference는 one-sided alpha `0.025`, target power `0.8`, non-inferiority margin `0.25`로 판정한다.
- pair당 독립 reviewer 최소 3명이며 경로, RC, 파일명과 생성 메타데이터를 숨긴다.
- ordinal Krippendorff alpha가 `0.67` 미만이면 결과는 fail/pass가 아니라 `evaluation-invalid`다.
- one-sided stratified bootstrap 95% CI와 Holm 다중비교 보정을 사용하고 point estimate만으로 통과시키지 않는다.
- holdout, reviewer calibration, tie/disagreement, missing artifact 처리와 평가 질문은 policy digest에 포함한다.

### 9.7 Visual non-regression gate

이 gate는 개별 operation 성공 판정이 아니라 public default와 D6 retirement를 위한 RC 비교 권위다.

Frozen corpus는 최소한 다음 축의 required stratum을 교차 포함한다.

- archetype: proposal, report, roadmap, comparison, data-story, training
- language/font: ko-KR, en-US, CJK supported subset
- density: low, medium, high
- source mode: source-free, verbatim-bound, structured-data, paraphrase
- asset/data: text-only, image, table, chart, mixed
- slide role: opening, body, evidence, transition, closing

수용 기준:

1. required requirement/source coverage는 candidate에서 `1.0`, P0/P1은 `0`이다.
2. blind pairwise review에서 candidate preferred 또는 tied 비율이 전체와 각 required stratum에서 모두 `>= 0.90`이고 one-sided 95% lower bound가 evaluation policy의 floor 이상이다.
3. 5점 척도 평균과 최악 10% 평균의 보정된 one-sided 95% lower bound는 baseline 대비 `-0.25`보다 낮아질 수 없다. `0.25`는 한 평가 단계의 절반인 practical non-inferiority margin이며 결과 확인 뒤 변경할 수 없다.
4. normalized composition-family Shannon entropy delta는 `>= -0.05`, exact-layout consecutive repeat rate delta는 `<= +0.02`다. 의도적 recap/series 반복은 사전 표기된 corpus case만 분리 집계한다.
5. exact layout 3장 연속은 의도적 반복 외 `0`이고, typography hierarchy·whitespace·density·image/chart fit·opening/closing 중 required dimension 하나라도 층별 margin을 넘으면 실패다.
6. `AbsoluteVisualQualityGate`와 `StatisticalBlindEvaluationValidity`가 모두 passed/valid여야 한다.
7. 독립 reviewer는 pair당 최소 3명이고, disagreement와 tie를 포함한 원자료를 report digest에 결속한다.
8. 동일한 frozen corpus/evaluation policy로 **2개 연속 frozen RC**가 모두 통과해야 D6를 허용한다. 한 RC 실패 시 연속 통과 count는 0으로 재설정한다.

전체 평균, 좋은 archetype, 쉬운 언어가 worst stratum 또는 worst decile 실패를 상쇄할 수 없다. candidate artifact 누락·생성 실패도 평가에서 제외하지 않고 candidate loss와 해당 severity로 계산한다.

### 9.8 Generation, artifact, promotion receipt 분리

`GenerationQualityReceipt`:

- requirement/source coverage
- generation strategy decision, CompositionIntentGraph, ArtDirectionIntent와 candidate selection digest
- Deck IR/grammar/compiler binding
- static/browser/deck quality
- published manifest revision/digest
- generation stage timing/provider usage

`ArtifactQualityReceipt`는 별도 export operation이 소유한다.

- package structure/readback와 artifact digest
- Fidelity raster/geometry/visual gate
- authorized 303/206 download binding
- `release-v1`이면 font preflight, PowerPoint first-open/save-copy/reopen/render/readback, same-file LibreOffice, immutable source/package/sidecar binding
- Hybrid를 주장할 경우 capability별 native coverage/fallback/QID survival

`ServicePromotionReceipt`는 같은 RC의 별도 release authority가 소유한다.

- performance matrix, fault, security, soak, provenance, detached signature
- 연속 frozen RC의 absolute/non-regression report, evaluation policy와 reviewer reliability binding
- artifact-level `releaseReadiness=ready`를 service promotion으로 재해석하지 않음
- `promotionReady`와 operator `deploymentAuthorized` 분리

Generation operation은 아직 export되지 않았다는 이유로 실패하지 않는다. `qualityPolicy=release-v1` generation은 release-grade generation evidence를 만들고, export/readback과 service promotion은 후속 operation/receipt에서 완성한다.

### 9.9 Post-D6 quality drift governance

D6는 품질 검증의 종료가 아니다. grammar, compiler, model fingerprint, font/asset/candidate/evaluation policy digest 중 하나라도 바뀌면 새 RC로 처리한다.

1. sealed holdout shadow에서 absolute, statistical-validity, non-regression gate를 다시 통과한다.
2. canary는 `1% → 5% → 25% → 100%`로만 진행하며 `QualityDriftReceipt`의 operation/time minimum을 모두 충족한다.
3. confirmed P0/P1, source/cross-render corruption, absolute floor 실패는 즉시 신규 admission을 pause하고 이전 qualified RC로 rollback한다.
4. latency/error/diversity drift는 두 개의 독립 rolling window에서 반복될 때 canary를 중단한다.
5. rollback은 이미 published bundle을 덮어쓰지 않고 신규 operation admission revision만 이전 qualified digest로 전환한다.
6. 반복 튜닝으로 holdout이 노출되면 corpus를 교체하고 old/new bridge study를 수행한다. 공개 benchmark와 sealed holdout을 같은 tuning set으로 사용하지 않는다.

operator는 drift failure를 수동 `passed`로 바꿀 수 없다. 조사 후 고친 immutable RC가 동일 단계를 새로 통과해야 한다.

## 10. 성능 설계와 목표

현재 실측 5장 경로는 total 약 290초, outline 누적 176.6초, author 누적 627.5초, provider logical call 11회, actual token 64,148이었다. render 약 9.4초와 export 약 0.1초는 1차 최적화 대상이 아니다.

### 10.1 5장 target budget

일반 경로는 shared provider recovery가 실행되지 않은 operation이다.

| 단계 | normal target | general-path p95 budget |
|---|---:|---:|
| admission/normalize/queue | 5s | 30s(C5 queue 포함) |
| global plan + Slide IR batches | 70s | 90s |
| candidate enumeration/selection + compile + measurement closure | 15s | 20s |
| deck QA | 15s | 20s |
| publication/Fidelity package | 10s | 20s |
| 합계 | **115s** | **180s** |

복구 경로:

- critic 또는 Director correction 중 하나만 실행한다.
- shared recovery 추가 budget은 최대 60초, operation absolute recovery deadline은 240초다.
- critic/correction invocation ratio 목표는 5% 미만이다.
- 복구 operation도 전체 성공률·latency distribution·비용에서 제외하지 않는다.
- promotion matrix의 generation→PPTX p95 180초 기준은 실제 전체 operation 집합에 적용한다. 복구 빈도나 시간이 p95를 넘기면 성능 gate는 실패한다.

모든 수치는 목표이며 현재 성능 증거가 아니다.

### 10.2 Provider budgets

- 5장 balanced-quality initial logical calls 3회, shared recovery 포함 최대 4회
- actual tokens 30,000 이하 목표
- response 시작 전 transient retry 최대 1회
- author/critic recovery 합계 최대 1회
- provider 429/5xx는 bounded exponential backoff+jitter
- quota/auth/validation/deterministic quality 실패는 retry 금지
- 모든 실행에 actual/estimated token, cost, latency, retry class를 기록

### 10.3 Local execution

- IR validation/compile은 CPU-bound bounded pool로 병렬화한다.
- slide compile은 독립 가능하지만 ordered result를 유지한다.
- 5장 balanced 후보 열거/feasibility/Pareto selection의 local target은 합계 3초 이하이고 provider logical call은 0이다.
- CPU pressure 때문에 K를 조용히 낮추지 않는다. admitted policy의 K를 실행하거나 `capacity_waiting`/stable failure로 처리한다.
- browser render는 기존 presentation 내부 최대 동시성 2를 유지한다.
- grammar/font/static asset만 process cache할 수 있다.
- provider content response의 cross-owner cache는 금지한다.
- 같은 owner, 같은 immutable request fingerprint의 in-flight single-flight는 operation idempotency 안에서만 허용한다.
- cache는 authority가 아니며 digest와 ArtifactRef 존재를 다시 확인한다.

## 11. 보안과 개인정보

| 위협 | 방어 |
|---|---|
| prompt injection in source | source를 data delimiter로 전달하고 compiler capability/system instruction과 분리 |
| XSS/remote fetch | model raw HTML/CSS/URL 금지, server renderer allowlist, render network-none |
| filesystem/object-store 노출 | provider에는 opaque source/asset content만 전달하고 locator/path 금지 |
| oversized IR/algorithmic DoS | depth/string/component/byte/layout-candidate hard cap |
| cross-owner artifact | owner/tenant-bound ArtifactRef와 signed download grant |
| critic의 내용 변조 | patch allowlist, source/title/QID immutable, 재검증 |
| stale worker publication | attempt token, fencing token, manifest CAS |
| secret/PII log | prompt/response body 미기록, digest/usage/reason code만 기록 |
| malicious image/document | source snapshot quarantine, media decode limits, network-none renderer |
| benchmark/holdout leakage | sealed holdout 접근 분리, tuning worker 비가시성, access audit와 bridge-study rotation |
| reviewer identity/assignment 노출 | pseudonymous reviewer receipt, identity 별도 저장, blind assignment immutable seal |
| mutable model alias drift | observed provider model fingerprint를 RC digest에 결속하고 변경 시 새 shadow/canary |

Critic에는 owner identifier, source locator, prompt, hidden metadata를 전달하지 않는다. 필요한 slide PNG, montage, content-free defect code, 허용 patch schema만 전달한다.

### 11.1 데이터 보존과 삭제

기존 Artifact Store의 `ephemeral | checkpoint | published` retention class를 재사용한다.

| 데이터 | retention class | 초기 정책 |
|---|---|---|
| D2 shadow Deck IR/critic montage | `ephemeral` | 최대 24시간, publication/release evidence 사용 금지 |
| 미게시 attempt IR/compile/measurement receipt | `checkpoint` | 기존 attempt TTL policy, 초기 기본 7일 |
| active manifest가 참조하는 Deck IR provenance/HTML/semantic/receipt | `published` | presentation lifecycle과 결속 |
| release evidence로 봉인된 montage/readback | `published` + evidence purpose | release retention/legal-hold policy와 결속 |
| sealed evaluation holdout/assignment | `published` + evaluation purpose | tuning 접근 금지, policy rotation/감사 lifecycle과 결속 |
| reviewer identity/calibration record | 별도 access-controlled store | 최소 권한, evaluation/legal policy 기간 후 삭제 |

- production DB/object storage의 IR, semantic source, montage, receipt는 at-rest encryption을 요구한다.
- authorization은 owner/tenant와 ArtifactRef를 결속하며 digest만 알아서는 읽을 수 없다.
- account/presentation/source snapshot 삭제는 active manifest, provenance graph, detached artifact index에 deletion tombstone을 만들고 retention controller가 cascade 처리한다.
- legal hold가 있으면 raw 사용자 식별정보 대신 opaque `legalHoldRef`와 expiry/policy를 기록하고 UI/API 삭제 상태에 보존 사유를 명시한다.
- backup 삭제는 backup lifecycle policy에 포함하며 primary 삭제만으로 즉시 완료를 선언하지 않는다.
- prompt와 raw provider response는 저장하지 않는다. published Deck IR는 사용자 제품 콘텐츠이므로 owner export/delete 대상이다.
- TTL, encryption key policy, legal hold, deletion receipt는 versioned retention policy digest로 operation/bundle에 결속한다.

## 12. API와 UI 투영

### 12.1 초기 API

기존 strict Public v2 request를 유지한다.

```text
POST /api/v2/presentations/generations
GET  /api/v2/operations/{operationId}
GET  /api/v2/operations/{operationId}/events
POST /api/v2/operations/{operationId}/cancel
POST /api/v2/presentations/{presentationId}/exports
GET  /api/v2/artifacts/{artifactId}/download
```

`qualityPolicy` 매핑:

- `interactive-v1`: 새 compiler의 interactive gate
- `release-v1`: release required evidence가 없으면 fail-closed

초기 rollout 동안 public client는 engine을 선택하지 않는다. 서버가 owner-safe feature flag, cohort policy와 grammar capability preflight로 기존 authored path 또는 Deck IR path를 operation 전체에 선택한다. `GenerationStrategyDecisionReceipt`가 그 결정을 봉인하며 두 경로의 slide, 중간 candidate, bundle, receipt를 섞지 않는다. Deck IR capability가 부족할 때 generic template나 raster-only artifact로 조용히 강등하지 않는다.

현재 `operation-status/v2`의 `nativeEditabilityReady`, `promotionReady`, `deploymentAuthorized`는 의도적으로 literal false다. v2 operation status는 artifact-scoped compatibility projection이며 service promotion authority가 아니다. Deck IR 구현이 이 literal을 바꾸지 않는다.

### 12.2 향후 API revision

성능/품질 evidence가 확보된 뒤에만 새 request revision에서 다음을 검토한다.

```json
{
  "generationProfile": "balanced-quality-v1"
}
```

초기 허용 후보는 `fast-preview-v1`, `balanced-quality-v1`이지만, default 변경과 public 노출은 별도 제품 결정이다. `release-v1`은 생성 속도 옵션이 아니라 evidence policy다.

readiness API revision은 operation artifact 상태와 service release 상태를 분리한다.

```text
GET /api/v3/operations/{operationId}      # generation/artifact receipts
GET /api/v3/release-candidates/{rcId}     # service promotion receipt
```

- operation response는 `GenerationQualityReceipt`/`ArtifactQualityReceipt`를 투영한다.
- release-candidate response만 `absoluteQualityReady`, `statisticalEvaluationValid`, `visualNonRegressionReady`, `qualityDriftGovernanceReady`와 최종 `promotionReady`를 계산할 수 있다.
- `deploymentAuthorized`는 별도 operator authorization receipt가 있을 때만 release-candidate response에 나타난다.
- v3가 준비되지 않은 동안 UI는 v2 literal false를 service failure로 해석하지 않는다.

### 12.3 UI

UI는 다음만 표시한다.

- 현재 phase와 progress
- slide count와 완료/검증 수
- stable failure reason과 retry 가능 여부
- `fidelityArtifactReady`
- `nativeEditabilityReady`
- `promotionReady`
- `deploymentAuthorized`

IR, provider prompt, internal score, object-store key, filesystem path는 노출하지 않는다.

### 12.4 Progress와 cancellation

Progress는 시간 추정치가 아니라 완료된 durable checkpoint의 가중 projection이다.

| checkpoint | progress 범위 | 취소 처리 |
|---|---:|---|
| accepted/queued | 0–5 | 즉시 cancel |
| global plan | 5–25 | provider cancel 요청, reservation usage settlement 후 cancel |
| Slide IR batches | 25–50 | 미시작 batch 취소, 완료 batch detached 유지, publication 없음 |
| candidate enumeration/selection + compile/measurement closure | 50–75 | 현재 bounded local 작업 종료 후 cancel |
| deck QA/shared recovery | 75–88 | 미시작 critic 금지, 시작된 provider usage settlement |
| bundle staging | 88–95 | staging 취소 후 unpublished artifact retention |
| manifest CAS publication | 95–100 | CAS 시작 후 cancel 거절, terminal publication 결과 반환 |

- percentage는 slide count, batch count, quality policy에 따라 normalize하며 단순 wall-clock 비율이 아니다.
- cancel 요청은 idempotent하고 `cancellationRequestedAt`, provider settlement, detached artifact disposition을 receipt에 남긴다.
- 취소 후 stale worker의 compile/checkpoint/publication은 attempt/fencing token으로 거절한다.
- 사용자가 취소해도 이미 발생한 provider usage를 0으로 기록하지 않는다.

## 13. 오류 계약

| reason code | 의미 | 자동 복구 |
|---|---|---|
| `deck_director_transient_failure` | event 전 provider transient 실패 | bounded retry 1회 |
| `deck_director_output_invalid` | JSON/schema 위반 | correction budget이 있을 때 1회 |
| `requirement_graph_invalid` | 요청 requirement identity/occurrence 계약 위반 | 없음 |
| `generation_strategy_capability_missing` | required grammar/compiler capability가 없음 | D0–D5 operation 전체 legacy 또는 reject, D6 이후 reject |
| `deck_ir_request_mismatch` | slide count/language/request binding 불일치 | 없음 |
| `deck_ir_source_binding_invalid` | source exactness/owner mismatch | 없음 |
| `requirement_coverage_incomplete` | required coverage 미달 | Director correction 1회 가능 |
| `deck_ir_duplicate_claim` | 허용 occurrence 밖 비의도적 content 중복 | Director correction 1회 가능 |
| `deck_ir_capacity_unsatisfiable` | 허용 recovery로 배치 불가 | 없음 |
| `design_grammar_unknown` | registry 밖 grammar/token/layout | 없음 |
| `composition_intent_graph_invalid` | zone/span/relation cycle/content ownership 위반 | 없음 |
| `art_direction_intent_invalid` | enum/role/overlap/occlusion policy 위반 | 없음 |
| `candidate_set_exhausted` | K 후보 모두 hard constraint 미통과 | 없음 |
| `candidate_selection_receipt_invalid` | candidate/selection/input digest 불일치 | 없음 |
| `constraint_compile_failed` | compiler 결정적 오류 | 없음, 조사 대상 |
| `compiled_semantic_mismatch` | HTML/semantic/QID mismatch | 없음 |
| `browser_observation_failed` | font/image/readiness/measurement 실패 | 환경 오류만 bounded retry |
| `browser_measurement_closure_failed` | deterministic recompile 2회 후 hard constraint 잔존 | 없음 |
| `deck_visual_quality_failed` | blocking visual defect 잔존 | critic budget이 있을 때 1회 |
| `absolute_visual_quality_failed` | baseline과 무관한 expert quality floor 미달 | 자동 승격 없음 |
| `visual_evaluation_invalid` | 표본/reliability/blinding/statistical policy 불충족 | reviewer 재교정 후 새 blind study |
| `visual_non_regression_failed` | frozen RC의 시각·다양성 비열등성 기준 미달 | 자동 승격/retirement 없음 |
| `quality_drift_detected` | post-D6 canary/rolling window 품질 저하 | admission pause + previous qualified RC rollback |
| `deck_critic_patch_invalid` | patch scope/schema/immutable 위반 | 없음 |
| `generation_intent_lineage_invalid` | Deck IR/parent/edit/compile receipt 계보 불일치 | 없음 |
| `deck_generation_budget_exhausted` | call/token/time budget 소진 | 없음 |

Public error는 content-free stable code만 제공한다. 내부 receipt는 어느 단계, slideKey, digest, policy/version에서 실패했는지 기록하되 사용자 콘텐츠를 로그에 복제하지 않는다.

## 14. 관측성

operation마다 다음을 보존한다.

- request/profile/grammar/compiler/model/policy digest
- requirement graph, capability preflight와 generation strategy decision receipt digest
- queue depth/position/wait와 limiting resource
- Director/batch/critic logical call, latency, token, cost, retry class
- Deck IR bytes, slide/component count, validation duration
- requirement/source coverage와 intentional repetition count
- requested/enumerated/feasible candidate count, Pareto frontier/beam selection digest, browser measurement closure revision, recovery step, compile duration
- first-pass accepted slide 수와 defect 분포
- composition family/variant, ArtDirection focal/space/image/type-rhythm 분포, entropy, exact-layout consecutive repeat
- browser launch/reuse, render duration, font/image readiness
- critic 호출 여부, target slide 수, patch status
- publication/fence/revision/artifact digest
- cancellation checkpoint와 provider usage settlement
- Fidelity/Hybrid/promotion/deployment readiness 분리 상태
- RC별 blind review assignment/report digest, required stratum과 worst-decile 결과
- evaluation sample/power/reliability/confidence/absolute-floor status
- post-D6 change-set, shadow/canary cohort/window, drift trigger와 rollback target receipt

필수 지표:

- `deck_director_wall_ms`
- `deck_ir_validation_ms`
- `slide_compile_ms`
- `candidate_enumeration_ms`
- `candidate_feasible_count`
- `deck_pareto_selection_ms`
- `first_pass_acceptance_ratio`
- `provider_logical_calls_total`
- `provider_retry_calls_total`
- `provider_actual_tokens_total`
- `critic_invocation_ratio`
- `capacity_recovery_count`
- `blocking_visual_issue_count`
- `generation_to_fidelity_pptx_ms`
- `duplicate_claim_count`
- `duplicate_publication_count`
- `measurement_recompile_count`
- `required_coverage_ratio`
- `cancel_settlement_ms`
- `generation_strategy_selected_total{strategy}`
- `generation_strategy_capability_missing_total{capability}`
- `composition_family_entropy`
- `consecutive_layout_repeat_ratio`
- `visual_pairwise_preferred_or_tied_ratio`
- `visual_absolute_overall_lower_bound`
- `visual_absolute_dimension_min_lower_bound`
- `visual_reviewer_agreement_alpha`
- `visual_evaluation_valid_total`
- `visual_worst_decile_rating_delta`
- `visual_non_regression_failed_strata_total`
- `quality_drift_detected_total{rule}`
- `quality_canary_stage{percent}`
- `quality_rollback_total{target_rc}`

실행된 required stage timing이 null이면 release evidence로 사용할 수 없다.

## 15. Migration과 rollback

### 15.1 단계

1. **D0 — 계약:** schema, grammar manifest, CompositionIntentGraph, ArtDirectionIntent, candidate/evaluation/non-regression/drift receipt, digest/QID fixture를 추가한다. 제품 경로 변경 없음.
2. **D1 — Compiler shadow:** 고정 IR fixture에서 bounded candidate set과 Pareto deck selection을 compile하고 기존 path와 독립적으로 검증한다. publication 없음.
3. **D2 — Director shadow:** 실제 request에서 IR/composition/art-direction intent를 만들되 사용자 결과에는 사용하지 않는다. IR/montage는 owner-bound `ephemeral`, 최대 24시간이며 product/release evidence로 사용할 수 없다. provider 비용과 사용자/운영자 cohort 승인을 별도 policy로 결속한다.
4. **D3 — Internal canary:** 허용된 owner/cohort에서 capability preflight가 통과한 신규 presentation만 Deck IR path로 publish한다. Fidelity만 지원하고 strategy는 operation 전체에 고정한다.
5. **D4 — Dual evidence:** 같은 frozen brief/source/style corpus를 기존 path와 새 path로 각각 생성해 속도·내용과 absolute/non-inferiority 품질을 statistically valid blind study로 비교한다. 실패 artifact를 제외하거나 결과 artifact를 혼합하지 않는다.
6. **D5 — Default candidate:** Gate 2와 Gate 3의 absolute/statistical/non-regression gate를 통과한 frozen RC만 신규 public generation default 후보가 된다. capability 미지원 operation은 전체 legacy routing 또는 reject다.
7. **D6 — Legacy author retirement:** 동일 evaluation policy에서 2개 연속 frozen RC가 Gate 3의 모든 품질 gate를 통과한 뒤에만 신규 raw HTML generation을 중지한다. 기존 HTML-only presentation read/export는 유지한다. 이 단계부터 primary rollback target은 이전 qualified Deck IR compiler/grammar/model/candidate-policy revision이다.
8. **D7 — Hybrid extension:** 별도 native capability evidence가 있는 component부터 Scene/OOXML compiler를 활성화한다.
9. **D8 — Continuous quality governance:** D6 이후 모든 change-set을 새 frozen RC로 만들고 shadow와 1%/5%/25% canary, drift receipt, automatic rollback을 상시 적용한다.

### 15.2 Rollback

- D0–D5에서 `deck-ir-generation` kill switch는 신규 operation admission만 기존 path로 되돌린다.
- D6 이후 kill switch는 이전 qualified Deck IR compiler/grammar/model/candidate-policy revision으로 되돌린다. legacy author fallback은 time-bounded dormant recovery package가 남아 있고 보안/호환 preflight를 통과한 경우에만 operator가 별도 승인한다.
- D6 전 capability 미지원은 operation 전체 legacy routing 또는 reject이며, D6 후에는 기본 reject다. generic template·raster-only·slide별 혼합 fallback은 어느 단계에서도 rollback이 아니다.
- 이미 시작한 operation은 `GenerationStrategyDecisionReceipt`의 strategy/grammar/compiler digest를 끝까지 유지한다.
- 이미 published된 Deck IR bundle을 다른 compiler 결과로 덮어쓰지 않는다.
- rollback 후에도 existing Deck IR presentation의 Fidelity read/export는 유지한다.
- active manifest를 과거 row로 되돌리지 않고 검증된 bundle refs를 가리키는 새 revision을 CAS publish한다.
- QualityDriftReceipt의 immediate-stop rule은 신규 admission을 이전 qualified grammar/compiler/model/candidate-policy digest로 원자적으로 전환한다. 진행 중 operation은 기존 sealed digest를 유지한다.
- drift rollback 후 동일 mutable alias를 재활성화하지 않는다. 수정한 immutable RC가 shadow부터 다시 통과해야 한다.
- legacy recovery package의 보존 만료일, 지원 runtime, source digest와 최종 제거 조건을 D6 activation receipt에 기록한다. 제거 후에는 legacy generation rollback을 주장하지 않는다.

## 16. 검증과 수용 기준

각 변경 경계마다 가장 좁은 검증 하나만 실행하고, 다음 gate로 넘어갈 때만 범위를 넓힌다.

### Gate 0 — 계약/결정론

- strict schema positive/negative fixture
- canonical JSON/digest cross-runtime exact
- 동일 compile input의 HTML/semantic/receipt digest exact
- QID uniqueness와 immutable source/title negative cases
- requirement/source coverage exact와 intentional repetition cases
- generated/edited bundle intent lineage와 invalidated negative case
- grammar/compiler/solver ABI compatibility와 migration receipt
- CompositionIntentGraph cycle/span/content-owner positive/negative case
- ArtDirectionIntent enum/overlap/occlusion/role policy positive/negative case
- K candidate enumeration, diversity signature, Pareto beam selection과 receipt digest exact
- strategy preflight의 whole-operation `deck-ir|legacy-authored|reject` 결정과 slide-level mixed routing 거절
- VisualQualityEvaluationPolicy, VisualNonRegressionReport, QualityDriftReceipt canonical digest와 결과 확인 후 policy/margin 변경 거절

### Gate 1 — Compiler 한 장

- text/metric/card/process/image 한 장
- hard constraints, browser observation, Fidelity raster
- browser feedback deterministic recompile 최대 2회와 closure receipt
- HTML↔semantic QID/text/asset exact
- 같은 composition intent에서 safe primitive 변주와 raw 좌표/CSS 입력 거절
- ArtDirectionIntent가 다른 K=6 deterministic 후보, hard-failure 제외와 동일 Pareto selection
- selected candidate만 interactive browser closure하고 다음 ranked candidate 전환도 최대 2회 budget 안에서 종료

### Gate 2 — 실제 5장 개발 경로

- 실제 provider → Deck IR → compile → browser QA → publication → Fidelity PPTX/PDF → 303/206
- sealed strategy receipt → operation 전체 Deck IR 경로와 capability mismatch provider-before-reservation 거절
- 5장 K=6 candidate enumeration/selection 추가 provider call 0, local target 3초 이하와 실제 timing observed
- logical call/token/stage timing 모두 observed
- 5장 balanced initial 3 calls, shared recovery 포함 최대 4 calls
- blocking issue 0, first-pass acceptance 측정
- GenerationQualityReceipt와 후속 ArtifactQualityReceipt authority 분리

### Gate 3 — 기존 경로 대비 A/B

같은 frozen brief/source/style corpus를 두 경로에서 별도 operation으로 생성한다.

필수 absolute quality, statistical validity, non-inferiority와 평가 절차:

- archetype/language/density/source mode/asset·data/slide role required stratum을 가진 frozen corpus, sealed holdout와 policy digest
- overall pair 최소 120, marginal required stratum 최소 30 또는 power analysis가 요구한 더 큰 표본
- 각 pair blind independent reviewer 최소 3명, 경로·RC·파일명 비공개, ordinal Krippendorff alpha `>= 0.67`
- one-sided alpha `0.025`, power `0.8`, stratified bootstrap 95% CI, Holm 다중비교 보정
- candidate 요청/source coverage 100%, P0/P1 0
- absolute overall expert mean lower bound `>= 4.0/5`, required dimension lower bound `>= 3.75/5`, worst decile lower bound `>= 3.5/5`
- duplicate claim/slide가 기존보다 많지 않음
- provider logical calls와 token이 기존보다 감소
- 5장 generation→PPTX p95 180초 이하
- critic/correction invocation ratio 5% 미만, 복구 operation을 latency에서 제외하지 않음
- candidate preferred 또는 tied 비율이 전체와 각 required stratum에서 모두 90% 이상이며 policy의 one-sided CI floor 통과
- 보정된 5점 평균과 worst decile difference lower bound `>= -0.25`
- normalized composition entropy delta `>= -0.05`, consecutive exact-layout repeat-rate delta `<= +0.02`
- exact layout 3장 연속 0(사전 표기된 의도적 반복 제외)
- artifact 생성 실패·누락을 candidate loss로 포함하고, 좋은 strata 평균으로 실패 strata를 상쇄하지 않음
- 동일 frozen policy에서 2개 연속 frozen RC 통과 전 D6 금지

### Gate 4 — Cross-renderer

- Gate 3의 정확한 같은 PPTX bytes
- PowerPoint first-open/save-copy/reopen/render/readback
- LibreOffice same-file readback
- source/QID/font/slide count/repair dialog evidence

### Gate 5 — 운영 승격

- same-RC 32-cell matrix
- worker/DB/object-store/provider fault matrix
- malicious corpus
- 86,400초 soak와 1,440 samples
- detached trusted collector signatures
- 연속 2개 frozen RC의 immutable absolute/non-regression report와 reviewer/evaluation policy binding
- `promotionReady` derivation
- 별도 operator `deploymentAuthorized`

### Gate 6 — Post-D6 change-set

- grammar/compiler/model fingerprint/font/asset/candidate/evaluation policy 변경마다 새 immutable RC
- sealed holdout shadow에서 Gate 3 absolute/statistical/non-regression 재통과
- `1%/100/24h → 5%/500/24h → 25%/1000/48h → 100%` canary progression
- confirmed P0/P1/source/cross-render corruption 또는 quality floor 실패의 immediate admission pause
- 두 rolling window의 latency/error/diversity drift 중단 규칙
- `QualityDriftReceipt`와 이전 qualified RC admission rollback proof
- 수동 pass override 금지, 수정 RC는 shadow부터 재시작

Fixture/unit/shadow/canary는 실제 제품 경로 또는 promotion evidence를 대신하지 않는다.

## 17. 개발 작업 패키지

개발은 이 문서 승인 이후 별도 요청에서 시작한다.

| 패키지 | 범위 | 완료 산출물 |
|---|---|---|
| WP1 Contracts | RequirementGraph, SourceBinding, Deck IR, CompositionIntentGraph, ArtDirectionIntent, candidate/evaluation/non-regression/drift/strategy/lineage/compile receipt와 digest fixture | versioned schemas + narrow contract tests |
| WP2 Grammar/Compiler | token/layout/component registry, safe composition/art-direction primitive, K candidate/Pareto selector, ABI, deterministic compiler와 browser closure | K=6 구성 변주와 selection receipt를 포함한 한 장 Fidelity candidate |
| WP3 Director | capability preflight, operation strategy decision, global plan + bounded parallel batch adapter, reservation, shared recovery budget | sealed strategy receipt + detached Deck IR artifact |
| WP4 Quality | static/browser/deck-level QA, bounded critic, absolute/statistical/non-regression frozen corpus blind harness | evaluation policy + quality/non-regression receipts |
| WP5 Core integration | operation phase/checkpoint/publication, generation/artifact receipt와 API projection | 5장 development product path |
| WP6 Migration | ephemeral shadow retention, whole-operation canary, blind dual evidence, 2-RC D6 guard, version rollback/kill switch | rollback 가능한 cohort rollout |
| WP7 Hybrid | admitted Scene/OOXML subset | 별도 native readiness evidence |
| WP8 Release | matrix/fault/security/soak/signature와 연속 frozen RC absolute/non-regression binding | promotion evidence bundle |
| WP9 Quality Governance | immutable change-set RC, sealed holdout, 1/5/25% canary, drift stop와 previous-qualified rollback | QualityDriftReceipt + automatic admission rollback proof |

WP1–WP6은 Fidelity 기본 생성 완성 범위다. WP7은 editable output을 제품 요구로 확정할 때만 promotion 범위에 포함한다. WP8은 개발 완료가 아니라 운영 승격 증거다. WP9는 D6 전에 구현되어야 하며 D6 이후 모든 변경에 계속 적용한다.

## 18. 최종 제품 계약

이 설계가 구현되면 신규 AI PPT 생성의 제품 계약은 다음과 같다.

1. 모델은 deck의 내용·스토리·디자인 의도를 구조화해 결정한다.
2. 구성 의도는 raw 좌표/CSS가 아니라 semantic zone, 상대 관계, bounded proportion과 visual weight로 표현한다.
3. 미학적 방향은 안전한 ArtDirectionIntent로 표현해 여백·초점·비대칭·이미지·타이포 리듬의 품질 상한을 확보한다.
4. 서버는 provider 호출 전에 required capability를 판정하고 operation 전체를 Deck IR, legacy 또는 reject로 고정한다.
5. 서버는 추가 provider 호출 없이 bounded deterministic 후보를 만들고 deck-level Pareto selection으로 품질과 다양성을 선택한다.
6. 서버는 versioned 디자인 문법과 제약조건으로 안전하고 재현 가능하게 배치하고 browser measurement closure로 실제 typography를 닫는다.
7. HTML과 semantic source는 한 compiler transaction에서 동일 QID로 생성된다.
8. deterministic QA가 구조·용량·시각 결함을 먼저 차단한다.
9. model critic은 필요할 때만 콘텐츠를 변경하지 않는 bounded visual patch를 제안한다.
10. 게시 결과는 기존 immutable bundle/manifest와 CAS 규칙을 따른다.
11. Fidelity는 기본 정확도 경로이고 Hybrid는 검증된 subset만 점진적으로 native 승격한다.
12. public default와 legacy retirement는 절대 품질, statistically valid blind non-regression과 다양성 gate를 모두 통과해야 한다.
13. D6 이후 모든 품질 영향 변경은 shadow/canary/drift gate와 automatic rollback을 거친다.
14. 성능·artifact quality·native editability·service promotion·deployment authority는 각각 별도 evidence로 판단한다.

## 19. 설계 완료 판정

다음 항목이 이 문서에서 모두 고정되었으므로 구현을 시작할 수 있다.

- 아키텍처와 canonical authority
- component 책임과 trust boundary
- Deck IR/Grammar/Compiler/Critic 핵심 계약
- CompositionIntentGraph와 safe primitive solver
- ArtDirectionIntent와 bounded aesthetic vocabulary
- deterministic K candidate set과 deck Pareto selection
- RequirementGraph/SourceBinding/coverage receipt
- generation intent와 Workbench edit lineage
- QID와 digest graph
- 생성 상태 전이와 provider call budget
- layout constraint와 recovery ladder
- browser measurement deterministic closure
- content/static/browser/deck/export와 absolute visual quality gate
- 성능 budget과 observability
- API/UI 호환 및 오류 계약
- grammar capability preflight와 operation 전체 strategy receipt
- generation/artifact/service-promotion receipt 분리와 cancellation
- security/privacy
- retention/deletion과 grammar ABI
- statistically valid blind VisualNonRegressionGate, reviewer reliability와 worst-stratum/diversity 기준
- sealed holdout와 benchmark-overfitting 방지
- migration/2-RC D6 guard/post-D6 drift/automatic rollback
- 단계별 검증과 release truth boundary
- 개발 작업 패키지와 순서

남은 하드웨어 inventory, provider quota, 운영 font inventory는 구현/운영 설정 입력이며 아키텍처 설계 미완료 항목이 아니다. 실제 수치가 들어오면 versioned runtime/grammar/font policy로 결속한다.

## 20. Provider 의도와 compiler 안전 권한 분리 (2026-09-01)

Provider ArtDirection은 배치 제안이며 overlap 허가가 아니다. 원본 IR을 수정하거나 콘텐츠 역할을 decoration으로 바꾸지 않고 별도의 resolved intent를 만든다. 공급자 의도, resolved intent, 후보 및 최종 브라우저 선택은 digest로 연결한다.

- capability 종류는 exclusive-content, overlay-host/backplate, annotation, ambient이다. 공급자의 역할명만으로 capability를 얻을 수 없다.
- exclusive content끼리의 겹침은 금지한다. overlay/annotation은 compiler가 소유한 host와 safe-region/대비/가림 검증이 있을 때만 허용한다.
- 현재 source-free renderer는 cross-zone overlay를 허가하지 않는다. 동일 콘텐츠와 의도를 가진 분리 배치 후보를 평가하며, 이미지 safe-region이나 annotation ownership이 구현되었다고 주장하지 않는다.
- K=6을 서로 다른 배치 계열로 열거한다. 각 후보를 최대 한 번 측정하며 후보별 재컴파일 한도 2와 별도 계수한다. 새 후보나 provider 호출을 추가하지 않는다.
- 브라우저는 내용 readback, QID 소유 영역, 영역 겹침, hard 관계, span, 최소 글자 크기·대비·가림을 확인한다. 전 후보 실패는 terminal이며 schema/reference/security/digest 오류는 즉시 terminal이다.
- 초기 선택과 실제 최종 선택을 모두 보존한다. 원본 checkpoint와 별도로 성공/소진 execution evidence를 남기고, 성공 시 기존 publication/quality evidence에 결속한다.
- 표, bar chart, process/timeline은 구조화 payload 전체를 렌더링한다. connector는 실제 endpoint 참조를 검증하고, 서로 다른 영역 또는 같은 영역 내부의 방향과 순서를 계산한 뒤 브라우저 좌표로 확인한다. 이미지, nested group과 미지원 chart 종류는 빈 요소로 대체하지 않고 명시적으로 차단한다. 공급자 schema도 실제 source-free 지원 집합과 bar chart로 제한한다.
- 12-column zone grid가 hard 상대 관계와 span 범위를 보존한다. 한글/CJK 제목은 font 42px를 유지하고 line-height 1.4를 grammar token에 결속해 두 줄 glyph 충돌을 방지한다. 빈 막대·화살표 paint node만 decoration으로 명시하며 실제 텍스트는 대비·가림 검사를 계속 적용한다.
- 목록·표·차트의 내부 컨테이너에도 HTML과 semantic이 같은 QID를 부여한다. compiler 단계에서 기존 publication의 strict QID validator를 적용하여 늦은 게시 실패를 막는다. publication은 slideKey 문자열 정렬 대신 검증된 Deck IR의 원래 슬라이드 순서를 유지한다.
- occurrence coverage는 존재 여부가 아니라 minimum/maximum 횟수를 정확히 계산한다. 상한 초과를 감추거나 requirement binding을 삭제해 통과시키지 않는다.

이 변경은 일반적인 문서 품질 보장이나 원본 PPTX 재현 완료가 아니다. 기존 10장 파일의 추출 내용으로 구성한 5장 제품 경로 요청과 원본 10장 그대로의 재생성은 구분한다. 2026-09-01 최종 실제 재시험은 API → worker → provider 3회 → Deck IR → browser QA → manifest publication → Fidelity PPTX/PDF → 303/206/전체 SHA256 다운로드 및 같은 PPTX 재사용까지 통과했다 (`1 passed, 3 deselected`, 전체 테스트 193.39초). API-only 18010과 소유한 별도 PostgreSQL을 사용했으며 UI/proxy 검증을 뜻하지 않는다.

증거는 `output/deck-ir-rich-authored-success-20260901/product-evidence.json`에 보관한다. generation operation은 `7dbe2304-d6dc-44db-bd42-843244e45353`, 전달 PPTX는 `rich-authored-new-deck-ir.pptx`이며 SHA256은 `6b0c0483491c56bbd295d38f5ffaa97c4da1f96383367fdda7a0758e34c8f063`이다. 전달본으로부터 생성된 5장 PNG를 모두 확인했다. 단, 보조 artifact-tool 렌더러는 PNG 생성 후 비정상 종료했으므로 해당 명령의 정상 종료를 주장하지 않는다. 본문의 82→97 수치·네이티브 편집성 문구는 테스트 시나리오이지 새 측정 결과가 아니다. 실제 산출물은 이미지 기반 Fidelity이며 독립 PowerPoint 확인, 절대/통계 품질 gate, promotion과 deployment 판정은 여전히 별도다 (`promotionReady=false`, `deploymentAuthorized=false`).
