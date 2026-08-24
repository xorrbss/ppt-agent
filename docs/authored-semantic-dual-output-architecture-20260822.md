# Authored Semantic Dual-output 아키텍처 설계

- 상태: Proposed, architecture review 반영본
- 작성일: 2026-08-22
- 적용 대상: Authored 생성·페이지 다시 만들기, Workbench 편집, Fidelity/Hybrid PPTX export
- 결정 수준: 제품 아키텍처와 단계적 구현 경계
- 선행 설계: `authored-only-vnext-architecture-design-20260813.md`
- 핵심 결정: Authored가 하나의 생성 과정에서 시각 원본 HTML/CSS와 의미 기반 source를 발행하고, 둘을 하나의 immutable canonical aggregate로 원자적으로 봉인한다.

## 1. 결정

Authored를 유일한 디자인·생성 엔진으로 유지한다. authored-vNext는 별도의 디자인 생성기가 아니라 Authored 결과를 보관하고, revision/CAS를 적용하고, Workbench에서 안전하게 편집하고, 검증된 객체를 OOXML/PPTX로 직렬화하는 플랫폼이다.

`AuthoredEmissionBundle v1`은 slide 단위다. 페이지 다시 만들기나 single-slide edit는 affected slide bundle 하나를 만들고, deck generation/batch edit는 affected slide 수만큼 bundle set을 만든다. command 하나는 최종적으로 deck publication manifest 하나만 CAS publish한다. 각 slide bundle에는 다음이 함께 묶인다.

- 브라우저가 그대로 렌더링할 `authoredHtml`
- Authored가 의도한 객체의 의미와 원본 데이터를 선언하는 `semanticSource`
- asset/font/composition manifest
- revision/operation lineage와 component digest를 연결하는 slide bundle 및 deck publication manifest

전환기에는 HTML/CSS가 시각 원본이고 semantic source는 편집·native export admission의 입력이다. 둘은 독립적으로 수정하거나 재사용할 수 없다. Authored 재생성, 페이지 다시 만들기, Workbench 편집은 항상 새 bundle과 새 revision을 만든다.

Fidelity export는 Authored HTML/CSS의 정확한 raster 결과를 사용한다. Hybrid export는 semantic source에 선언되었고 브라우저 관측과 일치하며 server-issued admission receipt가 준비된 객체만 native OOXML로 승격한다. 그 밖의 CSS, 복합 도형, 차트, 표, 효과는 해당 element 또는 island 단위 raster fallback을 유지한다.

`promotionReady`라는 전역 boolean은 생성·Fidelity·Hybrid·독립 vNext generation을 한꺼번에 여는 스위치로 사용하지 않는다. 제품은 기능별 readiness와 deck별 실제 coverage를 별도로 보고한다. 독립 vNext 신규 generation은 계속 동결한다.

### 1.1 선행 설계와 canonical authority

이 문서는 선행 `authored-only-vNext` 설계의 T0과 T1 사이에 `T0.5`를 추가한다. 선행 설계의 sandbox/CSP, tenant isolation, immutable revision, CAS, staged publish, rollback, evidence, imported-source 보존 규칙은 그대로 적용한다.

T0.5의 canonical state는 active `AuthoredDeckEmissionManifest`와 그것이 참조하는 `AuthoredEmissionBundle` graph 하나다. publish된 manifest와 bundle/component는 모두 immutable이다. 변경 가능한 상태는 presentation이 가리키는 active manifest pointer뿐이며 CAS로만 전진한다. HTML, semantic source, assets, fonts, composition 중 어느 component도 독립 write API를 갖지 않는다.

mutation authority는 다음 둘뿐이다.

- Authored generation/page remake가 제출하고 검증된 `AuthoredGenerationCommand`
- Workbench가 server-issued editable field에 대해 제출하는 `AuthoredMutationCommand`

명령은 detached candidate를 만들고, compiler가 모든 component를 다시 봉인한 뒤 한 bundle revision만 CAS publish한다. 두 component에 대한 임의 dual write는 금지한다.

선행 설계의 최종 `SlideSpec v2 canonical` 결정은 이 문서가 자동으로 폐기하지 않는다. T1/T2 전환은 semantic source만으로 Authored 표현력을 재현하는 여러 archetype evidence와 별도 superseding ADR이 있을 때만 수행한다. 그 전에는 T0.5 manifest/bundle graph가 canonical이고 HTML visual authority가 유지된다.

## 2. 이 결정을 선택한 이유

현재 Authored의 품질은 모델이 slide별 HTML/CSS를 자유롭게 구성하고 브라우저/Vision QA로 반복 보정하는 경로에서 나온다. 의미 schema를 디자인 제약으로 먼저 적용하면 현재 품질을 잃을 가능성이 크다.

반대로 현재 Hybrid의 post-hoc DOM 추론만으로는 원래 의도를 알 수 없다. 시각적으로 비슷한 DOM도 실제 의미가 표, 차트, 장식, 그룹 중 무엇인지 단정할 수 없고, chart SVG를 보고 원본 data series를 복원하는 것은 안전하지 않다.

따라서 다음 두 정보를 같은 Authored 결과에 결합한다.

1. HTML/CSS는 디자인 자유도와 Fidelity를 지킨다.
2. semantic source는 Authored가 알고 있는 객체 의미, 원본 데이터, 필요한 시각 feature를 명시한다.
3. 브라우저 observation은 source를 대신 생성하는 authority가 아니라 실제 paint와 declaration의 일치 여부를 검증하고 정확한 geometry/typography를 측정한다.
4. server admission과 Template V2/Scene/OOXML 계층은 검증된 source의 supported subset만 native 객체로 컴파일한다.

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
    authoredHtml              semanticSource
   (visual authority)       (meaning/source data)
             \                    /
              \-- bundleDigest --/
                       |
          +------------+-------------+
          |                          |
          v                          v
  Fidelity renderer        declaration/observation
  exact raster output          reconciliation
                                     |
                                     v
                         NativeAdmissionReceipt
                                     |
                                     v
                            HybridExportPlan
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
- 객체 kind와 필요한 시각 feature에 대한 선언
- Vision QA 수정 후 갱신된 HTML과 semantic source

### 3.2 authored-vNext가 소유하는 것

- immutable bundle과 content-addressed artifact
- revision, CAS publish, rollback, attempt binding
- semantic source validation과 browser reconciliation
- server-issued Workbench capability와 native admission receipt
- attempt별 Hybrid paint ownership/export plan
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
  "operationId": "uuid",
  "originGenerationAttemptId": "uuid-or-null",
  "authoredHtml": {
    "artifactRef": "artifact://sha256/...",
    "digest": "sha256:..."
  },
  "semanticSource": {
    "artifactRef": "artifact://sha256/...",
    "digest": "sha256:...",
    "origin": "authored-declared-v1"
  },
  "assetManifest": {
    "artifactRef": "artifact://sha256/...",
    "digest": "sha256:..."
  },
  "fontManifest": {
    "artifactRef": "artifact://sha256/...",
    "digest": "sha256:..."
  },
  "masterContractDigest": "sha256:...",
  "compositionDigest": "sha256:...",
  "elementIdentityRegistryDigest": "sha256:...",
  "qualityPolicyVersion": "authored-quality/vN",
  "bundleDigest": "sha256:..."
}
```

`artifactRef`는 retrieval locator일 뿐 identity가 아니다. 모든 identity/CAS/evidence 비교는 digest로 수행한다. `originGenerationAttemptId`는 해당 slide bundle lineage를 만든 최초 deck generation 또는 page-remake attempt를 가리키며 Workbench edit마다 바뀌지 않는다. legacy compatibility bundle은 null이다. 모든 생성·편집·undo·redo publication은 고유한 `operationId`를 가진다.

### 4.1 deck-level publication manifest

slide bundle만으로는 여러 slide와 master/composition이 한 deck revision에 함께 속한다는 사실을 증명할 수 없다. active presentation revision은 다음 `AuthoredDeckEmissionManifest v1` 하나를 가리킨다.

```json
{
  "schemaVersion": "authored-deck-emission-manifest/v1",
  "presentationId": "uuid",
  "presentationRevision": 22,
  "operationId": "uuid",
  "generationAttemptId": "uuid-or-null",
  "orderedSlides": [
    {"slideId": "uuid", "slideRevision": 14, "bundleKind": "authored", "bundleDigest": "sha256:..."}
  ],
  "masterContractDigest": "sha256:...",
  "compositionDigest": "sha256:...",
  "assetManifestDigest": "sha256:...",
  "fontManifestDigest": "sha256:...",
  "qualityPolicyVersion": "authored-quality/vN",
  "canonicalDigest": "sha256:..."
}
```

deck generation, reorder, page remake, Workbench patch, undo/redo는 새 manifest revision을 CAS publish한다. `generationAttemptId`는 해당 publication operation 자체가 generation/page remake일 때 그 attempt이며 edit/reorder/undo/redo이면 null이다. unchanged slide는 기존 immutable bundle digest와 각자의 origin lineage를 명시적으로 재사용할 수 있다. export binding의 generation lineage는 단일 deck 값으로 추측하지 않고 manifest operation과 ordered slide bundle의 origin lineage를 함께 검증한다.

### 4.2 digest graph

hash 계산은 self-reference와 mutable locator를 제외한 canonical JSON/bytes로 고정한다.

1. `htmlDigest = SHA256(canonical UTF-8 HTML bytes)`
2. `semanticDigest = SHA256(canonical JSON semantic source)`
3. 개별 asset/font digest는 resolve된 실제 bytes의 SHA-256이다.
4. manifest digest는 ordered entry의 content digest, media type, byte length, provenance를 canonical JSON으로 hash한다.
5. `bundleDigest`는 schema version, presentation/slide/revision/operation/origin lineage와 component digests, element identity registry, master/composition/policy version을 hash한다. `artifactRef`와 `bundleDigest` 자신은 입력에서 제외한다.
6. `canonicalDigest`는 manifest schema version, presentation ID/revision, operation/generation lineage, ordered slide ID/revision/bundle digests와 deck-level master/composition/asset/font/policy를 hash한다. `canonicalDigest` 자신과 artifact locator는 입력에서 제외한다.
7. `rendererSourceDigest`는 canonical digest, export mode, renderer/compiler/serializer versions, capability receipt digests, reconciliation receipt digests를 hash한다.
8. `pptxSha256`와 slide PNG SHA 목록은 renderer output digest이며 canonical digest와 혼동하지 않는다.

hash canonicalization algorithm과 schema version은 contract에 포함한다. 같은 의미의 JSON이 runtime별 key order, float, Unicode normalization 차이로 다른 digest가 되지 않도록 Python/TypeScript cross-runtime fixture를 둔다.

slide asset/font manifests는 deck manifest를 만들 때 stable asset/face ID 순서로 union한다. 동일 ID가 다른 content digest를 가리키면 publication을 거절하고, 동일 content digest의 중복 reference만 deduplicate한다. master/composition asset도 같은 aggregate에 포함한다. component content digest는 dedup identity이고 bundle/canonical digest는 publication identity라는 역할을 섞지 않는다.

### 4.3 asset/font binding

HTML의 `src` 문자열 hash는 locator integrity일 뿐 visual-content integrity가 아니다. publish 전에 asset을 허용된 resolver로 fetch하고 다음을 봉인한다.

- resolved content SHA-256, byte length, media type, width/height
- original locator digest와 resolver/version
- tenant/owner scope와 retention class
- font family/face, actual font bytes digest, embedding/subsetting permission

외부 mutable URL을 export 시점에 다시 fetch하지 않는다. resolve되지 않은 asset은 candidate를 fail-closed하거나 해당 element를 명시적 missing-asset fallback으로 처리한다. content-addressed artifact의 cross-tenant existence를 추론할 수 없도록 authorization을 tenant/owner scope에서 검사한다.

publish-time asset resolver는 renderer와 분리된 control-plane service다. HTTPS와 reviewed internal artifact scheme만 허용하고 credentials/cookies를 전달하지 않는다. DNS resolution 전후 loopback, link-local, private, metadata, reserved IP를 차단하고 redirect마다 protocol/host/IP policy를 다시 검사해 DNS rebinding과 redirect pivot을 막는다. TLS verification, streaming byte/time limit, declared/decoded media type 일치, image/font parser sandbox와 malware/decompression-bomb 검사를 통과한 bytes만 artifact store에 넣는다. 실패는 stable reason으로 candidate 또는 해당 asset element를 fail-closed하며 원격 응답 body를 log에 복제하지 않는다.

### 4.4 원자성 규칙

publication slide bundle은 discriminated union이다.

- `AuthoredEmissionBundle v1`: `bundleKind=authored`; semantic component는 `AuthoredSemanticSource v1 | SemanticUnavailableReceipt v1` 중 정확히 하나다.
- `LegacyHtmlBundle v1`: `bundleKind=legacyHtml`; compatibility component는 `CompatibilitySemanticObservation v1 | SemanticUnavailableReceipt v1` 중 정확히 하나다. `AuthoredSemanticSource`를 가질 수 없고 Workbench semantic edit authority가 없다.

deck manifest의 `orderedSlides[].bundleKind`와 resolved bundle discriminator/digest가 일치해야 한다.

1. 각 bundle variant의 component가 둘 다 없거나 둘 다 있으면 publish할 수 없다.
2. semantic source는 다른 HTML digest와 재결합할 수 없다.
3. VQA가 HTML을 수정하면 semantic source도 다시 발행하고 bundle digest를 바꾼다.
4. Workbench command는 기존 HTML 또는 semantic source만 바꾸지 않는다. compiler가 두 component를 함께 재생성·검증한 새 bundle을 만든다.
5. export attempt는 presentation revision, operation/generation lineage, canonical, renderer, semantic, composition, master, font, asset, policy, mode, serializer, PPTX digest를 고정한다.
6. active revision 변경은 모든 artifacts와 manifest를 먼저 검증한 뒤 하나의 pointer를 교체한다. 실패 시 기존 active revision을 유지한다.
7. capability switch나 serializer failure로 plan이 바뀌면 같은 attempt의 evidence를 덮어쓰지 않는다. 새 renderer digest와 새 export attempt를 만든다.
8. legacy extraction은 semantic source로 위장하지 않고 `LegacyHtmlBundle`의 `CompatibilitySemanticObservation v1` artifact로 동일 HTML digest에 결합한다. authored-declared source와 같은 trust/editability를 부여하지 않는다.

`SemanticUnavailableReceipt v1`은 semantic 생성/validation이 bounded retry 후 실패했지만 Authored HTML 자체의 Fidelity gate는 통과한 경우에만 허용한다. receipt는 presentation/slide/revision/operation lineage, HTML digest, stable failure code, attempt/compiler/policy version을 봉인하고 그 receipt digest가 bundle digest 입력이 된다. bundle digest를 receipt 입력에 다시 넣는 self-reference는 금지한다. 이 bundle은 Fidelity export와 page remake만 허용하며 Workbench element edit, Hybrid native admission, readiness evidence에는 사용할 수 없다. 누락된 semantic data를 빈 성공 projection으로 위장하지 않는다.

현재 `authored-canonical/v1`의 `html`, `cssText`, `assetManifest`, `sourceDigest`, stable `data-qid`, CAS revision을 없애지 않는다. S0에서는 이 snapshot을 versioned compatibility reader로 유지하고, 새 deck manifest/bundle tables 또는 동등한 immutable artifact repository를 추가한다. 기존 JSON snapshot을 무제한 확대하거나 semantic payload를 slide row마다 중복 저장하지 않는다.

## 5. semantic source와 derived receipt

semantic source는 browser layout을 재구성하는 범용 CSS AST가 아니다. Authored가 알고 있는 의미, 원본 편집 데이터, 필요한 시각 feature를 표현한다. 관측값, 편집 허용, serializer 지원, paint ownership은 semantic source에 저장하지 않는다.

### 5.1 계약 분리

| 계약 | authority | 포함하는 것 | 포함하지 않는 것 |
|---|---|---|---|
| `AuthoredSemanticSource v1` | Authored candidate, server validation | 의미 kind, source content/data, stable identity, required visual features | browser measurement, serializer version, readiness |
| `CompatibilitySemanticObservation v1` | legacy browser extractor | 기존 HTML의 보수적 text/image/shape candidate | Authored source authority, table/chart 원본 data, 자동 Workbench 권한 |
| `BrowserObservationReceipt v1` | isolated browser | DOM identity, measured bounds/lines/paint/font, renderer digest | 편집 허용, native 지원 선언 |
| `WorkbenchCapabilityProjection v1` | server policy | element별 editable fields와 read-only reason | paint owner, visual parity |
| `NativeAdmissionReceipt v1` | server reconciler/serializer registry | admitted kind/feature set, serializer version, fallback reasons | model의 희망값 |
| `HybridExportPlan v1` | export attempt planner | native objects, raster islands, paint owner, backplate suppression graph | canonical source mutation |
| `CapabilityReceipt v1` | release evaluator | scope/version/platform/evidence에 묶인 readiness | runtime enable input |

각 derived receipt는 input bundle digest, renderer/policy version과 자기 digest를 가진다. receipt는 canonical source를 수정하지 않으며 다른 bundle에 재사용할 수 없다.

### 5.2 semantic element 공통 필드

- `elementId`: HTML의 stable `data-qid` 값과 byte-for-byte 동일한 유일 identity
- `kind`: `text | image | shape | connector | group | table | chart | complexIsland`
- `semanticRole`: title, body, caption, metric, process step 등 의미 역할
- `parentId`, `childIds`: group/ownership 관계
- `intendedOrder`, optional intended bounds/rotation: Authored의 의미적 의도이며 measured geometry가 아님
- `requiredFeatures`: mask, filter, crop, gradient, merged-cell, axis 등 실제 표현에 필요한 feature
- `sourceRef`: asset/data/text source의 digest 또는 inline typed payload

`editableFields`, `native/raster/readOnly`, serializer id/version은 server-issued capability/admission receipt에만 존재한다. `visualOwner`는 attempt별 Hybrid export plan에만 존재한다. browser가 측정한 bounds, line rect, computed style은 observation receipt에만 존재한다.

`parentId`와 `childIds`는 동일 graph를 표현하므로 compiler가 양방향 일치, unique child, cycle 없음, 한 paint owner만 존재함을 검증한다. inline payload는 schema별 size limit를 넘을 수 없고 큰 table/chart data는 content-addressed `sourceRef`를 사용한다.

### 5.3 identity/QID 규칙

- 신규 `authored-declared-v1` candidate는 HTML `data-qid`와 semantic `elementId`에 유효하고 unique한 동일 문자열을 제출해야 한다. 별도 semantic ID나 selector를 만들지 않는다.
- declared candidate에서 누락·중복·invalid QID를 server가 조용히 교체하지 않는다. `authored_semantic_qid_invalid` 또는 `authored_semantic_qid_mismatch`로 candidate를 거절하고 bounded retry한다.
- declared candidate에는 remap을 허용하지 않는다. provider compatibility는 retry로 해결하고, silent repair 결과를 publish하지 않는다.
- deterministic QID 자동 생성과 ordered remap receipt는 `CompatibilitySemanticObservation v1` 생성에만 허용한다.
- QID uniqueness scope는 slide-local이고 deck-wide identity는 `(slideId, elementId)`다. bundle은 active/retired QID와 lineage를 가진 element identity registry digest를 포함한다. descendant slide revision에서 삭제된 QID는 다른 객체에 재사용하지 않는다. undo가 과거 bundle을 참조하거나 page remake가 새 `originGenerationAttemptId` lineage를 만드는 경우만 이전 QID set을 다시 사용할 수 있다.
- reconciliation은 element ID, cardinality, parent/child ownership을 exact match한다. DOM path나 capture ordinal은 보조 진단값이며 identity authority가 아니다.

### 5.4 text

text payload는 단순 문자열 외에 paragraphs와 runs를 보존한다.

- explicit line/paragraph breaks
- run별 text, language, script(`cjk | latin | common`), font role/family/face
- size, weight, italic, underline, color, spacing, alignment, direction
- no-wrap/pre 의미
- explicit authored layout intent가 있을 때의 optional wrap/no-wrap constraint

CJK와 Latin/mono mixed run은 별도 run으로 유지한다. 한글을 `Consolas`로 직렬화하거나 CJK serif 의도를 sans로 바꾸거나 `normAutofit`/overflow로 숨겨 성공 처리하지 않는다. native admission은 required embedding face, font substitution, bounds, line break, backplate collision을 모두 통과해야 한다. line rect와 실제 typeface는 browser observation/PowerPoint readback receipt에 기록한다.

### 5.5 image

- asset digest와 resolvable artifact reference
- alt text
- intrinsic size
- crop, object-fit, object-position
- optional clipping/decorations declaration

simple image만 native image로 승격한다. mask, complex clipping, unsupported filter, 불명확한 crop은 raster island로 남긴다.

### 5.6 table

table은 PNG나 DOM 모양이 아니라 실제 cell model을 가진다.

- rows/columns와 cell value
- rowSpan/columnSpan
- row height/column width
- text runs와 number format
- border/fill/alignment token
- header/accessibility metadata

실제 cell payload 없이 `dataDigest`만 있는 기존 plan은 native table readiness를 만족하지 않는다. 그런 표는 semantic HTML table이어도 serializer가 준비될 때까지 raster다.

### 5.7 chart

chart는 다음 typed data를 가져야 한다.

- chart type
- series names, category values, numeric values와 units
- number/data label format
- axis, legend, palette token
- accessibility summary

SVG/path/browser pixels은 chart render artifact일 뿐 원본 data가 아니다. declared series가 없으면 SVG를 보고 native chart로 승격하지 않는다.

### 5.8 shape, connector, group

지원 shape preset, fill/stroke token, radius, connector endpoint, arrow, grouping 관계를 명시한다. arbitrary SVG path, filter, mask, paint-order 효과는 `complexIsland`로 선언한다. group은 child paint의 중복 승격을 막을 수 있어야 한다.

### 5.9 complex island

native로 안전하게 표현할 수 없는 시각 영역의 명시적 contract다.

- island bounds와 z-order
- 포함 qid 목록
- raster 이유
- exact capture policy
- native child를 별도로 승격할 수 있는지 여부

복합 CSS를 편집 가능으로 위장하지 않는다. element-level capture가 안전하지 않으면 더 큰 island로 보수적으로 fallback한다.

island와 native child가 겹치는 경우 `HybridExportPlan`이 paint ownership graph를 계산한다. 한 DOM paint는 backplate 또는 하나의 native object 중 정확히 하나만 소유해야 한다. 중첩, clipping, opacity, blend, 불명확한 z-order 때문에 exclusive ownership을 증명할 수 없으면 상위 island 전체를 raster로 강등한다.

## 6. 생성·검증 흐름

### 6.1 신규 Authored 생성

1. deck/slide visual story plan은 native candidate와 예상 fallback을 hint로 준다. native coverage 목표가 디자인을 제한하지 않는다.
2. Authored model은 한 candidate transaction에서 HTML과 semantic source를 함께 만든다. 한 structured response가 안정적인 provider는 하나의 envelope를 사용한다.
3. server가 declared candidate의 QID namespace와 HTML↔semantic exact mapping을 검증한다. declared QID를 조용히 다시 쓰지 않는다.
4. semantic compiler가 element ownership, asset/data digest, group graph, payload schema와 size limit를 검증한다.
5. isolated browser가 실제 DOM geometry, paint, text run/line rect, typography를 관측한다.
6. reconciler가 declaration과 observation을 element별로 대조한다.
7. server serializer registry가 `NativeAdmissionReceipt`와 `WorkbenchCapabilityProjection`을 만든다. 모델은 serializer id/version이나 readiness를 결정하지 않는다.
8. mismatch element는 native admission을 잃고 raster reason을 받는다. identity/ownership/asset integrity mismatch는 candidate publication 자체를 막고, 표현 feature mismatch만 element/island raster fallback을 허용한다.
9. Vision QA 수정이 있으면 2~8을 새 candidate로 반복한다.
10. 품질 gate를 통과한 HTML, semantic source, manifests를 한 bundle로 seal하고 deck manifest를 CAS publish한다.

초기 provider가 JSON structured output과 자유 HTML을 안정적으로 동시에 반환하지 못하면 두 모델 결과를 독립 truth로 저장하지 않는다. 같은 candidate transaction 안에서 HTML 작성 후 semantic source를 생성하고 server가 둘을 검증한 뒤 하나의 bundle만 publish한다.

candidate state는 `draft → qidValidated → semanticValidated → observed → admitted → qualityAccepted → published`로 단방향 전이한다. 중간 artifact는 detached이며 active read/export API에서 보이지 않는다. bounded retry 횟수, timeout, token/latency budget을 넘기면 page remake/edit는 기존 active revision을 유지하고 실패 처리한다. 신규 deck generation은 product policy가 허용할 때만 `SemanticUnavailableReceipt`를 가진 `degradedFidelityOnly` bundle로 publish할 수 있으며 UI에 편집/Hybrid 미지원 상태를 표시한다.

### 6.2 기존 Authored deck

- 기존 HTML은 변경하거나 가짜 semantic data로 backfill하지 않는다.
- deterministic browser extraction으로 안전한 text/image/shape candidate만 `CompatibilitySemanticObservation v1` shadow artifact에 기록한다.
- table/chart/복합 diagram은 원본 data가 없으면 raster다.
- 사용자가 페이지 다시 만들기 또는 Authored 재작성한 slide부터 `origin=authored-declared-v1` bundle을 발행한다.
- compatibility observation은 native export candidate가 될 수 있어도 Workbench semantic-data edit 권한은 자동 부여하지 않는다.
- compatibility-observed와 authored-declared coverage/trust level은 지표와 UI에서 구분한다.

기존 HTML-only active revision은 곧바로 새 canonical로 가장하지 않는다. migration candidate는 모든 legacy slide를 `LegacyHtmlBundle v1`로 봉인하고, missing semantic slot에는 compatibility observation 또는 `SemanticUnavailableReceipt v1`을 넣은 완전한 deck manifest를 만든다. 전 slide/asset digest와 legacy Fidelity가 검증된 뒤 새 revision으로 pointer를 CAS 전환한다. 중간에 하나라도 실패하면 old HTML-only pointer를 유지한다.

API는 active pointer의 `canonicalMode=legacyHtml | bundleGraph`를 명시한다. compatibility reader는 두 mode를 읽되 신규 bundle write API는 `bundleGraph`만 쓴다. rollback은 old row를 다시 활성화하지 않고 검증된 legacy bundle refs를 가리키는 새 manifest revision을 publish한다.

### 6.3 페이지 다시 만들기와 Workbench edit

페이지 다시 만들기는 기존 semantic source를 붙여 쓰지 않고 Authored를 통해 전체 bundle을 다시 발행한다.

Workbench는 해당 bundle digest에 묶인 server-issued `editableFields`만 편집한다. read-only/raster 객체는 선택 정보만 보여주고 지원하지 않는 편집 명령을 숨기거나 명확히 거절한다.

Workbench command는 HTML과 semantic JSON에 각각 patch를 쓰는 dual-write API가 아니다. command reducer가 semantic source와 HTML component를 함께 재계산하고, browser reconciliation을 다시 실행하고, 새 bundle/manifest revision을 봉인한다. deterministic closure가 정의되지 않은 style/geometry 변경은 page remake로 fail-closed한다. undo/redo도 과거 component를 직접 활성화하지 않고 과거 bundle refs를 가리키는 새 manifest revision을 publish한다.

모든 mutation command는 `operationId`, idempotency key, expected presentation revision/canonical digest, target slide/QID, typed operation, before-value digest를 포함한다. server는 active manifest와 precondition을 확인하고 unknown field/action을 거절한다. 같은 idempotency key의 재시도는 동일 receipt를 반환하며 다른 payload를 결합하지 않는다.

## 7. Fidelity와 Hybrid

### 7.1 Fidelity

- 기본 export mode다.
- exact Authored HTML/CSS render를 slide raster로 사용한다.
- Fidelity renderer는 font size, font family, DOM hierarchy, CSS property, asset URL을 수정하지 않는다. diagnostics marker는 paint/layout에 영향을 주지 않는 격리 채널을 사용한다.
- minimum font size와 native-safe font policy는 native admission 규칙이며 Fidelity DOM mutation 규칙이 아니다.
- semantic source, derived receipt, native serializer failure가 Fidelity를 막지 않는다.
- source binding, renderer digest, PNG/PPTX SHA, PowerPoint QA receipt를 남긴다.

### 7.2 Hybrid

1. 동일 revision/bundle을 mutation 없이 source capture하고 observation/Fidelity 기준 PNG digest를 만든다. 이것은 아직 Hybrid backplate가 아니다.
2. declaration과 observation이 일치하고 admission receipt가 `admitted`인 element만 `HybridExportPlan`의 native candidate가 된다.
3. planner가 전체 DOM paint ownership/z-order graph를 만든다. unsupported element는 Scene에 전달하기 전에 raster island가 된다.
4. raster/native가 교차하는 paint graph에서 exclusive layer를 증명할 수 없는 native candidate를 반복 강등해 fixed point를 만든다.
5. fixed point plan을 기준으로 native paint를 숨기고 투명 RGBA `rasterLayers[]`를 z-band별 capture한다. 하나의 full-slide backplate 뒤에 모든 native object를 놓는 방식으로 교차 z-order를 위조하지 않는다.
6. OOXML insertion order는 ordered raster layer와 native object의 z-band를 그대로 따른다. z-band capture가 불가능하면 영향을 받는 closure 또는 slide 전체를 단일 raster layer로 강등한다.
7. Scene/Template V2 serializer가 admission을 통과한 native node만 생성한다.
8. PPTX readback에서 element kind/count, bounds, z-order, text paragraphs/runs/line breaks, actual typeface/face, asset relationship/data를 검사한다.
9. visual diff, typography, collision, repair gate가 실패하면 그 attempt를 실패 처리한다. fallback policy가 허용되면 raster 범위를 넓힌 새 export plan/digest/attempt를 만든다.

현재 Hybrid DOM extractor의 역할은 유지하되 authority를 바꾼다. 신규 bundle에서는 extractor가 element 의미를 추측하는 주체가 아니라 Authored declaration을 검증하고 실제 measurement를 제공한다. `CompatibilitySemanticObservation v1` legacy slide에서만 보수적 추론을 허용한다.

### 7.3 reconciliation contract

reconciliation은 `reconciliation-policy/vN`으로 versioning한다.

- identity, QID, source text/data digest, asset digest, ownership graph는 exact match다.
- geometry, line rect, crop, opacity, rotation은 property별 tolerance registry를 사용한다. 실제 evidence 없이 임의 숫자를 상수로 넣지 않는다.
- transform ancestor, clipping, occlusion, pseudo paint, overlapping island, ambiguous z-order는 stable reason code로 fail-closed한다.
- admission feature set은 declared와 observed feature의 합집합이다. 모델이 mask/filter/decoration을 누락해도 browser observation이 발견하면 native blocker가 된다.
- element마다 declared, observed, admitted, fallback 상태와 input/output digest를 기록한다.
- 같은 generic CSS class를 가진 서로 다른 slide를 별도 isolated document에서 capture하고 cross-slide style leakage가 없음을 확인한다.
- 우측/하단 경계 sentinel과 internal probe를 두어 viewport clipping, pixel-ratio, off-by-one capture를 검증한다.
- browser renderer version, viewport, CSS DPI, device scale factor, font set digest가 달라지면 receipt를 재사용하지 않는다.
- observation/capture 전에 required font face load와 image decode가 모두 성공해야 한다. timeout이나 rejected decode를 ready로 간주하지 않는다.
- animation/transition clock, request/network activity, DOM/style mutation을 `render-readiness-policy/vN`에 따라 봉인한다. policy가 요구하는 mutation-free window와 연속 paint epoch를 충족하지 못하면 observation/Fidelity capture를 실패 처리한다.
- receipt는 font/image readiness 결과, unresolved resource 목록, mutation-free window, capture epoch, exact captured source PNG SHA를 포함한다.

`BrowserObservationReceipt`는 measurement evidence이지 visual parity receipt가 아니다. observation match만으로 native PPTX 성공이나 PowerPoint 시각 일치를 선언하지 않는다.

### 7.4 Scene과 SlideVisualPlan 매핑

`AuthoredSemanticSource`는 Authored 경계의 source contract다. semantic source와 observation을 reconcile한 admission receipt로 `HybridExportPlan`을 만들고, 그중 native node만 기존 body-only `SlideVisualPlan v1`과 Scene IR로 deterministic normalize한다.

- text/image/shape/connector/group은 현재 plan/Scene 표현으로 매핑한다.
- table/chart는 실제 typed payload serializer가 생기기 전에는 `complexIsland` raster로 compile한다.
- company furniture는 계속 master/composition owner이며 body semantic source에 넣지 않는다.
- CSS-specific decoration이나 browser DOM detail을 SlideVisualPlan canonical 필드로 확장하지 않는다.
- plan digest, scene digest, composition digest는 bundle binding에 포함한다.
- Scene serializer가 지원하지 않는 connector/group/feature는 Scene 전체 오류가 되기 전에 raster island로 분리한다.
- 예상하지 못한 serializer 오류는 성공 fallback으로 숨기지 않는다. attempt를 실패시키고 capability receipt를 invalidation한 뒤 새 plan으로 명시적 retry한다.

## 8. readiness와 `promotionReady`

### 8.1 기능별 readiness

readiness는 boolean 원본이 아니라 다음과 같은 versioned receipt다.

```json
{
  "schemaVersion": "authored-capability-receipt/v1",
  "capabilityId": "hybrid.text",
  "state": "shadow",
  "scope": {
    "platform": "windows-powerpoint-16",
    "renderer": "chromium/version",
    "archetypes": ["process", "architecture"]
  },
  "serializerId": "authored-hybrid-text",
  "serializerVersion": "version",
  "featureSetDigest": "sha256:...",
  "policyDigest": "sha256:...",
  "evidenceBundleId": "uuid",
  "evidenceDigest": "sha256:...",
  "issuer": "release-authority-id",
  "environment": {
    "osBuild": "version",
    "officeBuild": "version",
    "rendererImageDigest": "sha256:...",
    "fontInventoryDigest": "sha256:..."
  },
  "evaluatedAt": "RFC3339",
  "expiresAt": "RFC3339",
  "stateReason": "bounded stable code"
}
```

`state`는 `disabled | shadow | ready | blocked` 중 하나다. receipt는 serializer version, supported feature set, platform, bounded deck matrix evidence manifest, PowerPoint readback/repair/visual/typography gate와 결합한다. renderer/policy/feature set이 바뀌거나 evidence retention window가 만료되면 새 receipt가 필요하다.

receipt는 release-authority RBAC를 통과한 issuer만 append-only store에 발급할 수 있다. 기존 receipt를 수정하지 않고 `supersedesReceiptDigest` 또는 `revokesReceiptDigest`를 가진 새 receipt로만 supersede/revoke한다. issuer, reason, approval identity/time은 audit event로 보존한다. environment scope는 OS/Office build, renderer image, browser, font inventory digest까지 exact match해야 하며 범위를 알 수 없는 ready receipt는 invalid다.

UI가 필요한 경우 `authoredCreationReady`, `fidelityExportReady`, `hybridTextReady` 같은 boolean projection을 receipt에서 계산할 수 있다. 이 projection은 receipt digest와 scope를 함께 제공해야 하며 저장된 authority나 runtime enable input이 아니다. `independentVnextGenerationReady`와 `fullNativeParityReady`는 별도 capability이며 하나의 Hybrid kind가 ready가 되어도 자동 승격되지 않는다.

export attempt는 시작 시 사용한 capability receipt digests를 고정한다. export 도중 global switch가 바뀌어도 같은 attempt에서 UI 표시와 compiler behavior가 달라지지 않는다. kill switch는 다음 attempt부터 해당 capability를 `disabled`로 평가하고 raster plan을 만든다.

kill switch authority는 emergency global, platform, serializer/version, element kind 순의 명시적 우선순위를 가진다. 최소 권한 운영자만 변경할 수 있고 activation/expiry/TTL/reason/approver를 immutable audit log에 남긴다. global emergency switch도 Fidelity renderer를 끄지 않으며, 시작 전 attempt는 disabled receipt로 다시 plan하고 이미 시작한 attempt는 취소 또는 기존 pinned receipt로 완료한다는 정책을 상태별로 명시한다.

### 8.2 전역 `promotionReady`

기존 API 호환을 위해 필드를 당장 제거하지는 않는다. 의미는 다음과 같이 축소한다.

- runtime 기능을 여는 입력 flag가 아니다.
- capability별 receipt의 집계 결과인 read-only release summary다.
- `promotionReady=true`여도 독립 vNext generation flag를 열지 않는다.
- Authored creation 또는 Fidelity export admission을 막지 않는다.
- 여러 archetype에서 실제 시각·편집 parity를 증명하기 전에는 `fullNativeParityReady=false`이고 aggregate `promotionReady=false`다.

개별 deck/export는 전역 readiness와 별도로 실제 `logicalCoverage`, `ooxmlReadback`, `backplateCount`, `typographyIssues`, fallback reasons를 보고한다. 이 값들은 서로 다른 단위이므로 합치거나 같은 백분율로 표현하지 않는다.

aggregate 계산식, required capability set, platform scope가 명시되지 않은 `promotionReady=true`는 invalid다. backward-compatible 필드는 새 receipt schema가 배포될 때까지 항상 `false`를 유지할 수 있으며, 임의 수동 override를 제공하지 않는다.

## 9. atomic evidence와 quality gate

`visualParity=passed`는 다음이 같은 binding일 때만 가능하다.

- presentation revision, operation ID와 generation lineage
- canonical HTML digest와 semantic digest
- deck manifest, master/composition/font/asset/policy digest
- renderer digest, Fidelity/Hybrid mode와 capability/serializer versions
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

### 9.1 executable visual-parity 판정

`visualParity=passed`는 PowerPoint COM process가 열리고 PNG가 생성되었다는 뜻이 아니다. 다음 predicate를 모두 만족할 때만 계산한다.

```text
passed =
  atomicBinding.valid
  AND ooxmlReadback.status == passed
  AND powerPointQa.openedSavedReopenedRendered == true
  AND powerPointQa.repairObservation == observed-none
  AND everySlide(renderDigestBound == true)
  AND everySlide(visualDiffPolicy == passed)
  AND criticalTypographyIssues == 0
  AND clippingOverlapGhostingIssues == 0
  AND requiredManualLedger == completed
```

- `repairObservation=assumed-none` 또는 상수 0은 pass evidence가 아니다.
- PNG hash는 freshness/identity 증거일 뿐 서로 다른 renderer 사이의 시각 parity 증거가 아니다.
- `visual-diff-policy/vN`은 비교 대상, masking 금지 영역, metrics, threshold, renderer/version을 고정한다.
- source Authored, Fidelity, Hybrid browser render, PowerPoint first open, save/reopen render를 slide별로 구분한다.
- auto gate가 측정하지 못하는 제목 줄바꿈, clipping, overlap, ghosting은 slide별 issue ledger와 reviewer identity/time을 가진다. promotion/readiness evidence에는 전 slide ledger가 필수이며 `not-required` 면제를 허용하지 않는다.
- 어떤 receipt도 다른 attempt/revision/PPTX에 복사하거나 자기선언 JSON만으로 대체할 수 없다. DB-issued attempt receipt와 artifact hashes를 verifier가 직접 resolve한다.

현재 QA가 위 predicate를 모두 구현하기 전에는 상태를 `not-verified` 또는 `failed`로만 기록하고 `passed`로 승격하지 않는다.

### 9.2 bounded deck matrix manifest

matrix는 archetype 문자열 목록이 아니라 versioned fixture/evidence manifest다.

- deck/presentation ID, revision, operation/generation attempt와 canonical digest
- slide별 archetype과 expected semantic/native/raster objects
- baseline Authored/Fidelity render digest
- actual UI request/response receipt와 downloaded PPTX SHA
- PowerPoint version/platform과 per-slide render digests
- 자동 issue 결과와 육안 ledger
- generic CSS class isolation slide pair와 right/bottom sentinel probes
- evidence 생성 시간, runner identity, tool/version, freshness window

대표 범위는 table, chart, image, process, architecture, roadmap을 포함한다. 동일 deck의 Fidelity와 Hybrid는 같은 canonical digest에 묶되 각각의 renderer digest와 PPTX SHA를 가진다. matrix verifier는 sidecar 형식만 검사하지 않고 DB attempt와 실제 artifact를 resolve해야 한다.

`capability-evaluation-policy/vN`은 capability/archetype별 fixture registry, 필수 deck/slide 수, mandatory edge case, baseline version과 freshness window를 고정한다. archetype은 presentation이 자기선언한 문자열이 아니라 registry의 fixture ID로 판정한다. 필수 fixture 한 건의 실패도 해당 scope receipt를 `ready`로 만들 수 없고, 이미 ready라면 invalidation한다. 재실행 또는 baseline 교체는 새 evidence bundle과 reviewer approval을 요구한다.

## 10. 구현 단계

### S0. contract와 shadow artifact

- 선행 ADR supersession/T0.5 canonical contract 고정
- `AuthoredEmissionBundle`, `AuthoredDeckEmissionManifest`, `AuthoredSemanticSource` schema 추가
- observation/admission/export-plan/capability receipt schema 분리
- digest graph, QID reject/remap, asset/font byte binding fixture 추가
- degraded Fidelity-only receipt, legacy bundle cutover, receipt issuer/revoke/kill control plane 계약 추가
- existing canonical snapshot을 compatibility reader로 유지하고 immutable artifact/manifest persistence 추가
- existing Authored generation/remake에서 detached shadow semantic source 생성
- 제품 UI/export behavior는 변경하지 않음

완료 조건: semantic source/unavailable receipt가 모두 없는 publish, stale semantic reuse, partial deck manifest, locator-only asset binding, digest mismatch가 모두 fail-closed다. DB/API migration의 upgrade/downgrade, old-reader compatibility, tenant authorization, retention/GC rehearsal이 통과한다. Fidelity는 DOM/CSS mutation 없이 기존 Authored visual baseline을 유지한다.

### S1. declared text/image/shape admission

- model prompt/response에 stable QID 기반 semantic source 추가
- declared QID invalid/duplicate/mismatch candidate fail-closed
- browser extractor를 source-bound observation receipt로 연결
- 기존 post-hoc classifier와 결과를 shadow 비교
- reconciliation tolerance policy와 identity/ownership failure class 고정
- 이 단계에서는 새 admission을 product export에 사용하지 않음

완료 조건: 첫 vertical slice와 bounded deck matrix에서 declared/observed identity, bounds, paint ownership이 일치하고 generic-class/sentinel isolation 회귀가 통과한다. token/latency/retry budget도 운영 SLO 안에 있다.

### S2. Hybrid admission과 export plan

- server serializer registry와 versioned `NativeAdmissionReceipt`
- text/image/simple shape에 한정된 `HybridExportPlan`
- unsupported/nested/overlap 객체의 pre-Scene raster island partition
- exact Fidelity renderer에서 minimum-font DOM mutation 제거
- attempt-scoped capability receipt/kill switch와 retry 규칙

완료 조건: serializer에 unsupported kind가 전달되지 않고, identity-verified paint만 backplate에서 제거된다. 예상하지 못한 serializer 오류는 attempt 실패이며 새 raster plan attempt로만 복구한다.

### S3. Workbench typed edit

- capability projection을 UI에 노출
- text/image/simple shape typed patch
- command reducer가 semantic source와 HTML을 함께 재계산하는 atomic CAS publish
- undo/redo가 bundle revision을 복원

완료 조건: edit 후 Fidelity와 Hybrid가 같은 새 deck manifest revision을 사용하고 stale export가 거절된다. deterministic closure가 없는 편집은 page remake로 fail-closed한다.

### S4. table/chart typed payload와 serializer

- full cell/data-series contract
- table/chart Scene compiler와 OOXML serializer
- PowerPoint readback과 visual differential gate

완료 조건: 선언 데이터가 있는 supported subset만 native이고, 나머지는 정확히 raster fallback한다.

### S5. capability별 promotion

- archetype별 evidence manifest
- serializer별 kill switch
- production-readiness/rollback/observability 판정

완료 조건: capability 하나를 꺼도 persisted bundle을 바꾸지 않고 다음 attempt가 Fidelity/raster fallback으로 복구된다. CI required check, staging migration/rollback rehearsal, alert/runbook, platform-specific matrix receipt가 존재한다.

### S6. optional canonical migration

semantic model이 Authored 표현력을 충분히 담는다는 장기 evidence가 확보된 뒤에만 supported node의 canonical ownership 전환을 검토한다. 이 단계 전에는 HTML visual authority를 제거하지 않는다. 독립 vNext 신규 generation은 별도 대형 범위이며 이 설계의 완료 조건이 아니다.

## 11. 첫 구현 vertical slice

최소 두 장의 실제 Authored deck에서 다음을 함께 검증한다.

- slide A: 한글 본문과 Latin/number가 섞인 text, `IBM Plex Mono` run, simple image 1개, simple shape 3개 이상, 의도적 raster chart island 1개
- slide B: slide A와 같은 generic CSS class 이름을 쓰되 완전히 다른 style/content를 가진 text/shape
- slide B 우측과 하단 경계에 clipping/off-by-one을 검출하는 sentinel과 internal probe
- raster foreground/native object/raster background가 교차하는 z-order fixture; 다중 z-band 또는 native 강등 결과를 명시
- 두 slide 모두 stable declared QID, semantic source, observation/admission receipt

같은 deck manifest/revision으로 Fidelity와 Hybrid를 실제 UI에서 export한다. Hybrid는 admitted text/image/shape만 native이고 chart는 raster여야 한다. PowerPoint 16에서 저장·재오픈·render/readback 후 repair, overflow, line-break mismatch, cross-slide CSS leakage, boundary clipping, ghosting, backplate collision이 없어야 한다. 이 두 장은 contract를 검증하는 시작점이며 전체 promotion 증거는 아니다.

## 12. rollback과 관측성

각 capability는 독립 kill switch를 가진다. kill switch는 새 generation을 막거나 persisted semantic data를 삭제하지 않고 다음 export attempt에서 해당 kind를 raster로 강등한다. 이미 시작한 attempt는 고정된 capability receipt를 사용한다. Fidelity는 항상 사용 가능해야 한다.

필수 metric/log는 다음과 같다.

- declared element 수와 kind
- declared-to-observed match/mismatch 수
- fallback reason별 element/island 수
- serializer id/version별 native output과 readback 수
- stale binding/CAS reject 수
- typography issue code별 수
- backplate collision과 ghosting failure
- archetype별 visual diff 결과
- compatibility-observed와 authored-declared coverage
- semantic generation token/cost/latency와 bounded retry 수
- artifact resolve 실패, tenant authorization reject, GC/retention 상태
- capability receipt state change와 kill-switch activation

로그에는 원문 slide text나 asset bytes를 복제하지 않고 stable identity와 digest를 사용한다.

## 13. 보안, 저장, API와 migration

- LLM HTML과 semantic JSON은 모두 untrusted input이다. 선행 ADR의 sanitizer, CSP, isolated renderer, network/filesystem/process deny policy를 그대로 적용한다.
- schema는 `extra=forbid`, element/data/asset/font count와 byte size limit, graph depth, string length 제한을 가진다.
- artifact resolver는 tenant/owner authorization을 매 read마다 검사하고 signed/opaque ref를 사용한다.
- content-addressed artifact retention은 active revision, undo/redo window, audit/evidence hold를 고려한 reference census 후 GC한다. active/evidence artifact는 자동 삭제하지 않는다.
- font embedding/subsetting은 기술적 가능 여부와 라이선스 허용 여부를 함께 통과해야 한다.
- 신규 API는 explicit versioned request/response model과 stable error code를 가진다. unknown field를 조용히 수용하지 않는다.
- migration은 old HTML-only revision의 read/export를 유지하고 new bundle write만 feature gate한다. upgrade, downgrade, backup/restore, partial failure, rollback rehearsal을 정의한다.
- CI required check는 schema cross-runtime digest fixture, migration contract, QID/atomic negative cases, browser isolation, native admission/fallback을 좁은 필수 gate로 실행한다.
- staging/production enablement는 capability receipt와 별개 승인이다. receipt가 ready여도 deploy/merge/feature enable을 자동 수행하지 않는다.

## 14. 비범위와 금지 사항

- semantic IR로 Authored의 자유로운 HTML/CSS 디자인을 제한하지 않는다.
- 임의 HTML/CSS가 완전히 native와 동일하다고 선언하지 않는다.
- raster fallback 객체를 fully editable로 표시하지 않는다.
- table DOM이나 chart SVG에서 존재하지 않는 원본 data를 추측하지 않는다.
- HTML과 semantic source를 서로 독립된 mutable truth로 만들지 않는다.
- `promotionReady`를 수동으로 `true`로 바꿔 evidence gate를 우회하지 않는다.
- `AUTHORED_VNEXT_NEW_GENERATION_ENABLED`를 이 설계 때문에 활성화하지 않는다.
- Template V2를 독립 생성 제품으로 부활시키지 않는다.
- Scene 문법 전체 확장, arbitrary CSS native parity, full Template V2 generation은 별도 대형 범위로 관리한다.

## 15. 최종 제품 판단

이 설계에서 “최선”은 모든 객체를 즉시 native로 만드는 것이 아니다. Authored가 이미 만드는 시각 품질을 기본 경로에서 손상시키지 않으면서, Authored가 명시적으로 알고 있고 browser/PowerPoint에서 재검증된 객체만 편집 가능하게 넓히는 것이다.

따라서 제품의 truthful 상태는 다음과 같다.

- Authored 생성: 주 생성 경로
- Fidelity: 기본 품질 보존 경로
- Hybrid: capability별 점진적 native 경로
- Workbench: 선언·검증된 field만 편집
- Template V2/Scene/OOXML: 내부 compiler infrastructure
- 독립 vNext generation: 동결
- full native parity와 aggregate promotion: 여러 archetype evidence 전까지 미승격
