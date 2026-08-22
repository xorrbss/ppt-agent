# Authored Semantic Dual-output 아키텍처 설계

- 상태: Proposed
- 작성일: 2026-08-22
- 적용 대상: Authored 생성·페이지 다시 만들기, Workbench 편집, Fidelity/Hybrid PPTX export
- 결정 수준: 제품 아키텍처와 단계적 구현 경계
- 선행 설계: `authored-only-vnext-architecture-design-20260813.md`
- 핵심 결정: Authored가 하나의 생성 과정에서 시각 원본 HTML/CSS와 의미 기반 편집 projection을 원자적으로 발행한다.

## 1. 결정

Authored를 유일한 디자인·생성 엔진으로 유지한다. authored-vNext는 별도의 디자인 생성기가 아니라 Authored 결과를 보관하고, revision/CAS를 적용하고, Workbench에서 안전하게 편집하고, 검증된 객체를 OOXML/PPTX로 직렬화하는 플랫폼이다.

한 번의 Authored 생성 또는 페이지 다시 만들기는 `AuthoredEmissionBundle v1` 하나를 발행한다. 이 bundle에는 다음이 함께 묶인다.

- 브라우저가 그대로 렌더링할 `authoredHtml`
- Authored가 의도한 객체의 의미와 편집 가능성을 선언하는 `semanticProjection`
- asset/font/composition manifest
- revision, generation attempt, canonical/renderer digest를 연결하는 `sourceBinding`

전환기에는 HTML/CSS가 시각 원본이고 semantic projection은 편집·native export admission 원본이다. 둘은 독립적으로 수정하거나 재사용할 수 없다. Authored 재생성, 페이지 다시 만들기, Workbench 편집은 항상 새 bundle과 새 revision을 만든다.

Fidelity export는 Authored HTML/CSS의 정확한 raster 결과를 사용한다. Hybrid export는 semantic projection에 선언되었고 브라우저 관측과 일치하며 해당 serializer capability가 준비된 객체만 native OOXML로 승격한다. 그 밖의 CSS, 복합 도형, 차트, 표, 효과는 해당 element 또는 island 단위 raster fallback을 유지한다.

`promotionReady`라는 전역 boolean은 생성·Fidelity·Hybrid·독립 vNext generation을 한꺼번에 여는 스위치로 사용하지 않는다. 제품은 기능별 readiness와 deck별 실제 coverage를 별도로 보고한다. 독립 vNext 신규 generation은 계속 동결한다.

## 2. 이 결정을 선택한 이유

현재 Authored의 품질은 모델이 slide별 HTML/CSS를 자유롭게 구성하고 브라우저/Vision QA로 반복 보정하는 경로에서 나온다. 의미 schema를 디자인 제약으로 먼저 적용하면 현재 품질을 잃을 가능성이 크다.

반대로 현재 Hybrid의 post-hoc DOM 추론만으로는 원래 의도를 알 수 없다. 시각적으로 비슷한 DOM도 실제 의미가 표, 차트, 장식, 그룹 중 무엇인지 단정할 수 없고, chart SVG를 보고 원본 data series를 복원하는 것은 안전하지 않다.

따라서 다음 두 정보를 같은 Authored 결과에 결합한다.

1. HTML/CSS는 디자인 자유도와 Fidelity를 지킨다.
2. semantic projection은 Authored가 알고 있는 객체 의미, 원본 데이터, 편집 정책을 명시한다.
3. 브라우저 observation은 projection을 대신 생성하는 authority가 아니라 실제 paint와 declaration의 일치 여부를 검증하고 정확한 geometry/typography를 측정한다.
4. Template V2/Scene/OOXML 계층은 검증된 projection을 native 객체로 컴파일한다.

이 구조는 디자인 엔진을 둘로 만들지 않으면서도 editability를 점진적으로 넓힌다.

## 3. 제품 경계

```text
User brief / source material / Authored template
                       |
                       v
             Authored generation + VQA
                       |
                       v
           AuthoredEmissionBundle v1
             /                    \
    authoredHtml              semanticProjection
   (visual authority)       (edit/native admission)
             \                    /
              \-- sourceBinding -/
                       |
          +------------+-------------+
          |                          |
          v                          v
  Fidelity renderer        declaration/observation
  exact raster output          reconciliation
                                     |
                                     v
                           SlideVisualPlan / Scene IR
                                     |
                                     v
                        Template V2 native compilers
                                     |
                                     v
                         Workbench / OOXML / PPTX
```

### 3.1 Authored가 소유하는 것

- 콘텐츠 선정과 시각적 구성
- slide별 HTML/CSS
- stable `data-qid`와 semantic object identity
- text/image/table/chart의 원본 내용 또는 data reference
- 어떤 객체를 native candidate로 의도했는지에 대한 선언
- Vision QA 수정 후 갱신된 HTML과 projection

### 3.2 authored-vNext가 소유하는 것

- immutable bundle과 content-addressed artifact
- revision, CAS publish, rollback, attempt binding
- projection schema validation과 browser reconciliation
- Scene/SlideVisualPlan normalization
- Workbench capability projection
- native OOXML serializer, package validation, PowerPoint QA
- export evidence, coverage, readiness, observability

### 3.3 Template V2가 소유하지 않는 것

Template V2는 독립 디자인 생성기나 별도 canonical truth가 아니다. Authored가 선언한 안전한 객체를 Scene/OOXML로 컴파일하는 내부 infrastructure다. Template V2 schema의 표현 한계가 Authored HTML/CSS의 디자인 한계가 되어서는 안 된다.

## 4. `AuthoredEmissionBundle v1`

아래 예시는 wire contract의 방향을 고정하기 위한 것이다. 실제 JSON schema는 구현 단계 S0에서 추가한다.

```json
{
  "schemaVersion": "authored-emission-bundle/v1",
  "presentationId": "uuid",
  "slideId": "uuid",
  "slideRevision": 14,
  "generationAttemptId": "uuid",
  "authoredHtml": {
    "artifactRef": "artifact://sha256/...",
    "digest": "sha256:..."
  },
  "semanticProjection": {
    "artifactRef": "artifact://sha256/...",
    "digest": "sha256:...",
    "origin": "authored-declared-v1",
    "elements": []
  },
  "assetManifest": {
    "artifactRef": "artifact://sha256/...",
    "digest": "sha256:..."
  },
  "fontManifest": {
    "artifactRef": "artifact://sha256/...",
    "digest": "sha256:..."
  },
  "sourceBinding": {
    "canonicalDigest": "sha256:...",
    "compositionDigest": "sha256:...",
    "qualityPolicyVersion": "authored-quality/vN"
  }
}
```

### 4.1 원자성 규칙

1. HTML digest와 semantic digest 중 하나만 존재하는 bundle은 publish할 수 없다.
2. semantic projection은 다른 HTML digest와 재결합할 수 없다.
3. VQA가 HTML을 수정하면 semantic projection도 다시 발행하고 digest를 바꾼다.
4. Workbench patch는 기존 HTML만 바꾸지 않는다. semantic 객체를 수정하고 deterministic HTML patch를 함께 적용한 새 bundle을 CAS publish한다.
5. export attempt는 presentation revision, generation attempt, canonical digest, renderer digest, semantic digest, composition digest를 모두 고정한다.
6. active revision 변경은 artifacts를 먼저 검증한 뒤 pointer를 교체한다. 실패 시 기존 active revision을 유지한다.

현재 `authored-canonical/v1`의 `html`, `cssText`, `assetManifest`, `sourceDigest`, stable `data-qid`, CAS revision을 없애지 않는다. S0에서는 이 snapshot을 확장해 semantic artifact reference와 digest를 함께 seal하는 방식이 가장 작은 변경이다.

## 5. `AuthoredSemanticProjection v1`

semantic projection은 browser layout을 재구성하는 범용 CSS AST가 아니다. Authored가 알고 있는 의미와 원본 편집 데이터를 표현하고, native serializer admission을 보수적으로 결정하는 계약이다.

### 5.1 공통 element 필드

- `semanticId`: stable `data-qid`와 1:1로 대응하는 immutable identity
- `kind`: `text | image | shape | connector | group | table | chart | complexIsland`
- `semanticRole`: title, body, caption, metric, process step 등 의미 역할
- `parentId`, `childIds`: group/ownership 관계
- `zOrder`, `bounds`, `rotation`, `opacity`: Authored의 의도 값
- `visualOwner`: `html | semantic`
- `editability`: `native | raster | readOnly`
- `nativePolicy`: serializer id/version, required feature set, declared fallback reasons
- `sourceRef`: asset/data/text source의 digest 또는 inline typed payload
- `qid`: DOM의 `data-qid`; semanticId와 다른 selector를 만들지 않는다.

`visualOwner=html`이 기본값이다. Hybrid가 reconciliation을 통과한 element만 export attempt 안에서 일시적으로 `semantic` paint를 소유한다. persist된 HTML에서 해당 paint를 제거하지 않는다.

### 5.2 text

text payload는 단순 문자열 외에 paragraphs와 runs를 보존한다.

- explicit line/paragraph breaks
- run별 text, language, script(`cjk | latin | common`), font role/family/face
- size, weight, italic, underline, color, spacing, alignment, direction
- no-wrap/pre 의미
- browser measurement와 비교할 line rect/bounds hints

CJK와 Latin/mono mixed run은 별도 run으로 유지한다. 한글을 `Consolas`로 직렬화하거나 `normAutofit`/overflow로 숨겨 성공 처리하지 않는다. native admission은 required embedding face, font substitution, bounds, line break, backplate collision을 모두 통과해야 한다.

### 5.3 image

- asset digest와 resolvable artifact reference
- alt text
- intrinsic size
- crop, object-fit, object-position
- optional clipping/decorations declaration

simple image만 native image로 승격한다. mask, complex clipping, unsupported filter, 불명확한 crop은 raster island로 남긴다.

### 5.4 table

table은 PNG나 DOM 모양이 아니라 실제 cell model을 가진다.

- rows/columns와 cell value
- rowSpan/columnSpan
- row height/column width
- text runs와 number format
- border/fill/alignment token
- header/accessibility metadata

실제 cell payload 없이 `dataDigest`만 있는 기존 plan은 native table readiness를 만족하지 않는다. 그런 표는 semantic HTML table이어도 serializer가 준비될 때까지 raster다.

### 5.5 chart

chart는 다음 typed data를 가져야 한다.

- chart type
- series names, category values, numeric values와 units
- number/data label format
- axis, legend, palette token
- accessibility summary

SVG/path/browser pixels은 chart render artifact일 뿐 원본 data가 아니다. declared series가 없으면 SVG를 보고 native chart로 승격하지 않는다.

### 5.6 shape, connector, group

지원 shape preset, fill/stroke token, radius, connector endpoint, arrow, grouping 관계를 명시한다. arbitrary SVG path, filter, mask, paint-order 효과는 `complexIsland`로 선언한다. group은 child paint의 중복 승격을 막을 수 있어야 한다.

### 5.7 complex island

native로 안전하게 표현할 수 없는 시각 영역의 명시적 contract다.

- island bounds와 z-order
- 포함 qid 목록
- raster 이유
- exact capture policy
- native child를 별도로 승격할 수 있는지 여부

복합 CSS를 편집 가능으로 위장하지 않는다. element-level capture가 안전하지 않으면 더 큰 island로 보수적으로 fallback한다.

## 6. 생성·검증 흐름

### 6.1 신규 Authored 생성

1. deck/slide visual story plan은 native candidate와 예상 fallback을 hint로 준다. native coverage 목표가 디자인을 제한하지 않는다.
2. Authored model은 한 응답 envelope에서 HTML과 semantic projection candidate를 함께 만든다.
3. server가 HTML을 canonicalize하고 deterministic `data-qid`를 확정한다.
4. semantic compiler가 qid namespace, element ownership, asset/data digest, group graph, payload schema를 검증한다.
5. isolated browser가 실제 DOM geometry, paint, text run/line rect, typography를 관측한다.
6. reconciler가 declaration과 observation을 element별로 대조한다.
7. mismatch element는 native admission을 잃고 raster reason을 받는다. slide 전체를 실패시키지는 않는다.
8. Vision QA 수정이 있으면 2~7을 새 candidate로 반복한다.
9. 품질 gate를 통과한 HTML, projection, manifests를 한 bundle로 seal하고 CAS publish한다.

초기 provider가 JSON structured output과 자유 HTML을 안정적으로 동시에 반환하지 못하면 두 모델 결과를 독립 truth로 저장하지 않는다. 같은 generation attempt 안에서 HTML 작성 후 projection을 생성하고, server가 둘을 검증한 뒤 하나의 bundle만 publish한다. 중간 산출물은 detached candidate다.

### 6.2 기존 Authored deck

- 기존 HTML은 변경하거나 가짜 semantic data로 backfill하지 않는다.
- deterministic browser extraction으로 안전한 text/image/shape candidate만 `origin=observed-v1` shadow projection에 기록한다.
- table/chart/복합 diagram은 원본 data가 없으면 raster다.
- 사용자가 페이지 다시 만들기 또는 Authored 재작성한 slide부터 `origin=authored-declared-v1` bundle을 발행한다.
- observed-v1과 authored-declared-v1 coverage는 지표에서 구분한다.

### 6.3 페이지 다시 만들기와 Workbench edit

페이지 다시 만들기는 기존 semantic projection을 붙여 쓰지 않고 Authored를 통해 전체 bundle을 다시 발행한다.

Workbench는 해당 revision의 capability projection이 `native`로 선언한 필드만 편집한다. read-only/raster 객체는 선택 정보만 보여주고 지원하지 않는 편집 명령을 숨기거나 명확히 거절한다. 편집은 typed semantic patch와 deterministic HTML patch를 같은 transaction에 적용하고 새 revision을 만든다.

## 7. Fidelity와 Hybrid

### 7.1 Fidelity

- 기본 export mode다.
- exact Authored HTML/CSS render를 slide raster로 사용한다.
- semantic projection이나 native serializer failure가 Fidelity를 막지 않는다.
- source binding, renderer digest, PNG/PPTX SHA, PowerPoint QA receipt를 남긴다.

### 7.2 Hybrid

1. 동일 revision/bundle의 exact backplate를 렌더한다.
2. declaration과 observation이 일치한 element만 native plan에 넣는다.
3. backplate에서 identity가 다시 일치한 native leaf의 paint만 숨긴다.
4. Scene/Template V2 serializer가 OOXML object를 생성한다.
5. PPTX readback에서 element kind/count, bounds, text runs, asset relationship을 검사한다.
6. visual diff, typography, collision, repair gate가 실패하면 해당 capability 또는 element를 raster fallback한다.

현재 Hybrid DOM extractor의 역할은 유지하되 authority를 바꾼다. 신규 bundle에서는 extractor가 element 의미를 추측하는 주체가 아니라 Authored declaration을 검증하고 실제 measurement를 제공한다. `observed-v1` legacy slide에서만 보수적 추론을 허용한다.

### 7.3 Scene과 SlideVisualPlan 매핑

`AuthoredSemanticProjection`은 Authored 경계의 admission contract다. 검증된 projection을 기존 body-only `SlideVisualPlan v1`과 Scene IR로 deterministic normalize한다.

- text/image/shape/connector/group은 현재 plan/Scene 표현으로 매핑한다.
- table/chart는 실제 typed payload serializer가 생기기 전에는 `complexIsland` raster로 compile한다.
- company furniture는 계속 master/composition owner이며 body projection에 넣지 않는다.
- CSS-specific decoration이나 browser DOM detail을 SlideVisualPlan canonical 필드로 확장하지 않는다.
- plan digest, scene digest, composition digest는 bundle binding에 포함한다.

## 8. readiness와 `promotionReady`

### 8.1 기능별 readiness

제품은 최소한 다음을 독립적으로 계산하고 노출한다.

```json
{
  "authoredCreationReady": true,
  "fidelityExportReady": true,
  "hybridShapeReady": false,
  "hybridTextReady": false,
  "hybridImageReady": false,
  "hybridTableReady": false,
  "hybridChartReady": false,
  "independentVnextGenerationReady": false,
  "fullNativeParityReady": false
}
```

위 값은 예시이며 실제 검증 없이 `true`로 설정하지 않는다. 각 Hybrid readiness는 serializer version, supported feature set, bounded deck matrix evidence manifest, PowerPoint readback/repair/visual/typography gate와 결합한다.

### 8.2 전역 `promotionReady`

기존 API 호환을 위해 필드를 당장 제거하지는 않는다. 의미는 다음과 같이 축소한다.

- runtime 기능을 여는 입력 flag가 아니다.
- capability별 receipt의 집계 결과인 read-only release summary다.
- `promotionReady=true`여도 독립 vNext generation flag를 열지 않는다.
- Authored creation 또는 Fidelity export admission을 막지 않는다.
- 여러 archetype에서 실제 시각·편집 parity를 증명하기 전에는 `fullNativeParityReady=false`이고 aggregate `promotionReady=false`다.

개별 deck/export는 전역 readiness와 별도로 실제 `logicalCoverage`, `ooxmlReadback`, `backplateCount`, `typographyIssues`, fallback reasons를 보고한다. 이 값들은 서로 다른 단위이므로 합치거나 같은 백분율로 표현하지 않는다.

## 9. atomic evidence와 quality gate

`visualParity=passed`는 다음이 같은 binding일 때만 가능하다.

- presentation revision와 generation attempt
- canonical HTML digest와 semantic digest
- renderer/composition/font/asset manifest digest
- Fidelity/Hybrid mode와 serializer versions
- PPTX SHA
- PowerPoint QA DB receipt
- quality/coverage sidecar와 UI projection
- render/report/manifest/hash가 포함된 한 evidence bundle

필수 gate는 다음과 같다.

- 실제 UI export 경로
- PowerPoint 16 COM 저장, 재오픈, 전 슬라이드 render/readback
- repair prompt 없음
- >5pt, >10pt overflow 분리 보고
- title line break, clipping, overlap, ghosting
- native/backplate collision
- CJK/mono mixed run과 embedding face
- 동일 qid의 declared/observed/native OOXML identity
- 표, 차트, 이미지, 프로세스, 아키텍처, 로드맵을 포함한 bounded deck matrix

font part/relationship/declaration의 존재와 실제 native text가 해당 face를 사용한 사실은 별도 검증한다.

## 10. 구현 단계

### S0. contract와 shadow artifact

- `AuthoredEmissionBundle v1`과 `AuthoredSemanticProjection v1` schema 추가
- current canonical slide snapshot에 semantic artifact ref/digest를 atomic seal
- existing Authored generation/remake에서 shadow projection 생성
- 제품 UI/export behavior는 변경하지 않음

완료 조건: HTML-only publish, stale projection reuse, digest mismatch가 모두 fail-closed이며 기존 Fidelity가 byte/visual regression 없이 동작한다.

### S1. declared text/image/shape admission

- model prompt/response에 stable qid 기반 declaration 추가
- browser extractor를 declaration reconciliation에 연결
- 기존 post-hoc classifier와 결과를 shadow 비교
- mismatch는 element raster fallback

완료 조건: 첫 vertical slice와 bounded deck matrix에서 declared/observed identity, bounds, paint ownership이 일치하고 ghosting이 없다.

### S2. Workbench typed edit

- capability projection을 UI에 노출
- text/image/simple shape typed patch
- semantic patch와 deterministic HTML patch의 atomic CAS publish
- undo/redo가 bundle revision을 복원

완료 조건: edit 후 Fidelity와 Hybrid가 같은 새 revision을 사용하고 stale export가 거절된다.

### S3. table/chart typed payload와 serializer

- full cell/data-series contract
- table/chart Scene compiler와 OOXML serializer
- PowerPoint readback과 visual differential gate

완료 조건: 선언 데이터가 있는 supported subset만 native이고, 나머지는 정확히 raster fallback한다.

### S4. capability별 promotion

- archetype별 evidence manifest
- serializer별 kill switch
- production-readiness/rollback/observability 판정

완료 조건: capability 하나를 꺼도 persisted bundle을 바꾸지 않고 Fidelity/raster fallback으로 복구된다.

### S5. optional canonical migration

semantic model이 Authored 표현력을 충분히 담는다는 장기 evidence가 확보된 뒤에만 supported node의 canonical ownership 전환을 검토한다. 이 단계 전에는 HTML visual authority를 제거하지 않는다. 독립 vNext 신규 generation은 별도 대형 범위이며 이 설계의 완료 조건이 아니다.

## 11. 첫 구현 vertical slice

한 장의 실제 Authored slide에서 다음을 함께 검증한다.

- 한글 본문과 Latin/number가 섞인 native text
- `IBM Plex Mono` 같은 mono run이 포함된 별도 text
- simple image 1개
- simple shape/connector 3개 이상
- 복합 chart island 1개는 의도적으로 raster

같은 bundle/revision으로 Fidelity와 Hybrid를 실제 UI에서 export한다. Hybrid는 text/image/shape만 native이고 chart는 raster여야 한다. PowerPoint 16에서 저장·재오픈·render/readback 후 repair, overflow, line-break mismatch, ghosting, backplate collision이 없어야 한다. 이 한 장은 contract를 검증하는 시작점이며 전체 promotion 증거는 아니다.

## 12. rollback과 관측성

각 capability는 독립 kill switch를 가진다. kill switch는 새 generation을 막거나 persisted semantic data를 삭제하지 않고 해당 kind를 raster로 강등한다. Fidelity는 항상 사용 가능해야 한다.

필수 metric/log는 다음과 같다.

- declared element 수와 kind
- declared-to-observed match/mismatch 수
- fallback reason별 element/island 수
- serializer id/version별 native output과 readback 수
- stale binding/CAS reject 수
- typography issue code별 수
- backplate collision과 ghosting failure
- archetype별 visual diff 결과
- observed-v1과 authored-declared-v1 coverage

로그에는 원문 slide text나 asset bytes를 복제하지 않고 stable identity와 digest를 사용한다.

## 13. 비범위와 금지 사항

- semantic IR로 Authored의 자유로운 HTML/CSS 디자인을 제한하지 않는다.
- 임의 HTML/CSS가 완전히 native와 동일하다고 선언하지 않는다.
- raster fallback 객체를 fully editable로 표시하지 않는다.
- table DOM이나 chart SVG에서 존재하지 않는 원본 data를 추측하지 않는다.
- HTML과 semantic projection을 서로 독립된 mutable truth로 만들지 않는다.
- `promotionReady`를 수동으로 `true`로 바꿔 evidence gate를 우회하지 않는다.
- `AUTHORED_VNEXT_NEW_GENERATION_ENABLED`를 이 설계 때문에 활성화하지 않는다.
- Template V2를 독립 생성 제품으로 부활시키지 않는다.
- Scene 문법 전체 확장, arbitrary CSS native parity, full Template V2 generation은 별도 대형 범위로 관리한다.

## 14. 최종 제품 판단

이 설계에서 “최선”은 모든 객체를 즉시 native로 만드는 것이 아니다. Authored가 이미 만드는 시각 품질을 기본 경로에서 손상시키지 않으면서, Authored가 명시적으로 알고 있고 browser/PowerPoint에서 재검증된 객체만 편집 가능하게 넓히는 것이다.

따라서 제품의 truthful 상태는 다음과 같다.

- Authored 생성: 주 생성 경로
- Fidelity: 기본 품질 보존 경로
- Hybrid: capability별 점진적 native 경로
- Workbench: 선언·검증된 field만 편집
- Template V2/Scene/OOXML: 내부 compiler infrastructure
- 독립 vNext generation: 동결
- full native parity와 aggregate promotion: 여러 archetype evidence 전까지 미승격
