# 원본 콘텐츠 기반 템플릿 재디자인 설계

- 상태: 설계 초안, 제품 코드 구현 전
- 작성일: 2026-08-26
- 대상: Upload → Authored 생성 → Workbench → PPTX export
- 사용자 확정 계약: 원본 콘텐츠는 그대로 유지하고, 선택한 AI 저작 템플릿과 회사 표준으로 다시 디자인한다.

## 1. 문제 정의

현재 `원본 문서 그대로 생성`은 사용자가 기대한 기능과 반대로 동작한다.

- 체크 시 AI 저작 스타일과 회사 표준 선택 UI를 숨긴다.
- 생성 시 `authoredStyle`과 `masterPin`을 해석하기 전에 `source_locked` 경로로 빠진다.
- PPTX는 원본 패키지 바이트 복원, PDF는 페이지 이미지 복원을 성공 기준으로 사용한다.
- 결과가 편집 가능한 템플릿 재디자인 프레젠테이션이 아니라 별도 다운로드 아티팩트다.

이 기능의 올바른 의미는 다음 한 문장으로 고정한다.

> 원본은 내용의 권위자이고, 선택 템플릿은 디자인의 권위자다.

## 2. 목표와 비목표

### 2.1 목표

1. 원본의 모든 텍스트, 수치, 표 셀, 이미지 occurrence, 출처와 순서를 보존한다.
2. 선택한 AI 저작 스타일을 색상, 타이포그래피, 패널, 차트, 이미지 방향에 적용한다.
3. 선택한 회사 표준이 있으면 geometry, 안전영역, writable slot, 로고, 헤더와 푸터에 적용한다.
4. 공간이 부족하면 슬라이드를 추가하고, 콘텐츠 삭제·요약·임의 축소로 맞추지 않는다.
5. 생성형 HTML authoring을 거치지 않고 봉인된 원본 payload를 결정적으로 Authored Scene/Workbench/PPTX에 materialize해 편집 가능하게 한다.
6. 원본 package inventory부터 최종 PPTX readback까지 콘텐츠 포함률을 기계적으로 검증하고 100%가 아니면 성공으로 게시하지 않는다.

### 2.2 비목표

- 원본 슬라이드의 색상, 폰트, 배경, 좌표를 그대로 복제하지 않는다.
- 원본 PPTX 패키지와 결과 PPTX의 바이트 동일성을 요구하지 않는다.
- LLM이 원문을 더 자연스럽게 고치거나 요약하도록 허용하지 않는다.
- 원본 페이지 수를 맞추기 위해 내용을 삭제하거나 가독성을 훼손하지 않는다.
- 기존 `Presentation.generation_mode="standard"`의 의미를 재사용하지 않는다.

## 3. 사용자 경험 계약

### 3.1 명칭

기존 체크박스 명칭을 다음과 같이 바꾼다.

- 기존: `원본 문서 그대로 생성`
- 변경: `원본 콘텐츠 유지`
- 도움말: `문구·수치·표·이미지는 유지하고 선택한 템플릿으로 다시 디자인합니다.`

생성 버튼은 이 모드에서 `선택한 템플릿으로 재디자인`으로 표시한다.

`source_locked`라는 내부 이름은 사용자 UI에 노출하지 않는다.

### 3.2 화면 순서

파일 첨부, 원본 콘텐츠 모드, AI 저작 스타일과 회사 표준은 생성 전까지 어떤 순서로든 선택할 수 있다. 파일을 먼저 첨부했다는 이유로 모드를 잠그지 않는다. 실제 생성 요청이 서버 admission을 통과한 시점에만 선택 snapshot을 고정한다.

```text
파일·모드·AI 저작 스타일·회사 표준 선택
  → 원본 분석 요약 확인
  → 선택한 템플릿으로 재디자인
  → 서버 admission 시 선택 snapshot 고정
```

원본 콘텐츠 모드를 켜도 다음 UI를 절대 숨기지 않는다.

- `TemplateSelection`의 AI 저작 스타일 카탈로그
- 회사 표준 선택 카드와 변경 버튼
- 현재 선택 요약

### 3.3 생성 전 선택 요약

생성 버튼 위에 다음 값을 항상 표시한다.

```text
콘텐츠 · 원본 유지
AI 저작 스타일 · OSB 스마트뱅킹
회사 표준 · IBIZ 제안서 v2 또는 사용 안 함
overflow · 내용 삭제 없이 슬라이드 추가
```

AI 저작 스타일을 해석할 수 없거나 선택 revision이 유효하지 않으면 생성 버튼을 비활성화하고 카탈로그를 연다. 회사 표준은 선택 사항이지만, 선택된 경우 published head 검증에 실패하면 조용히 제거하지 않고 다시 선택하도록 한다.

### 3.4 생성 옵션

원본 콘텐츠 모드에서는 일반 생성 옵션을 다음처럼 바꾼다.

- 프롬프트: `추가 디자인 지시`로 명칭을 바꾸고 레이아웃·강조·밀도 지시만 허용
- 슬라이드 수: `원본 기준 + 필요 시 자동 추가`로 고정
- 언어: 원본에서 자동 감지하며 번역하지 않음
- tone/verbosity: 원문을 바꿀 수 있으므로 비활성화
- web search: 새 사실을 추가할 수 있으므로 비활성화
- 이미지 생성: 원본 이미지를 대체하는 용도로 사용 금지, 템플릿 장식 자산만 허용

요약, 문장 수정, 번역, 내용 추가를 요구하는 디자인 지시는 admission에서 `source_content_instruction_conflict`로 거절한다.

### 3.5 원본 분석 요약

파일을 수락한 뒤 다음 정보만 보여준다.

- 원본 종류와 페이지 또는 슬라이드 수
- 텍스트 블록 수
- 표와 차트 수
- 고유 이미지 수와 이미지 occurrence 수
- 추출 신뢰도가 낮아 raster fallback이 필요한 블록 수

원본 SHA-256, 내부 asset id, 전체 경로와 같은 구현 세부정보는 기본 UI에 노출하지 않는다.

## 4. 콘텐츠 보존 계약

### 4.1 원본 개체 inventory

manifest 생성 전에 원본 package 또는 문서 모델의 모든 개체와 relationship을 inventory한다. 추출기가 인식한 개체만 커버리지 분모로 삼아서는 안 된다.

```json
{
  "discoveredObjectCount": 81,
  "classifiedObjectCount": 81,
  "contentBearingObjectCount": 67,
  "unsupportedContentObjectIds": [],
  "unclassifiedObjectIds": [],
  "externalRelationshipCount": 0
}
```

admission 성공 조건은 다음과 같다.

- `discoveredObjectCount == classifiedObjectCount`
- `unclassifiedObjectIds`가 비어 있음
- content-bearing unsupported object는 지원되는 block-level raster fallback으로 봉인됐거나 명시적으로 실패함
- decorative master/layout object와 실제 원본 콘텐츠 object가 분리됨
- extractor가 이해하지 못한 개체를 누락한 채 `100%`로 계산하지 않음

### 4.2 보존 단위

모든 원본 요소에 안정적인 `sourceBlockId`와 콘텐츠 digest를 부여한다.

| 원본 종류 | 보존 단위 | 보존 규칙 |
|---|---|---|
| 제목·본문·각주 | 텍스트 블록과 run | 원본 payload 변경 금지, Unicode 정규화는 비교용 projection에만 허용 |
| 수치·날짜·단위 | 텍스트 run 또는 데이터 값 | 문자와 단위 그대로 유지 |
| 표 | 셀, 병합, 행·열 순서 | 모든 셀 값과 병합 의미 유지 |
| 차트 | 시리즈, category, 값, label | 데이터 추출 가능 시 네이티브 차트로 재표현 |
| 이미지 | occurrence | 같은 이미지가 여러 번 나오면 매 occurrence 배치 |
| 도형·다이어그램 | 의미 블록 또는 raster fallback | 텍스트와 관계를 보존하고 누락 금지 |
| 출처·주석 | source note | 본문과 분리하되 결과에 반드시 포함 |

### 4.3 텍스트 정책

- 기본값은 `verbatim`이다.
- 줄바꿈, 글머리표 모양과 서식은 템플릿에 맞게 바꿀 수 있다.
- 단어, 숫자, 문장 순서, 표 셀 값은 바꾸지 않는다.
- LLM은 텍스트를 생성하지 않고 semantic role 분류와 layout 후보 선택에만 사용한다.
- 출력 payload는 원본 문자열과 run 경계를 사용한다. 줄바꿈 또는 Unicode 비교 정규화 값은 별도 필드로 저장하고 출력 문자열을 덮어쓰지 않는다.
- 문구 수정은 생성 후 Workbench의 명시적 사용자 편집으로만 허용한다.

### 4.4 페이지와 슬라이드 정책

- PPTX의 원본 슬라이드 grouping은 우선 보존한다.
- PDF의 페이지 grouping, DOCX의 heading/section grouping을 우선 보존한다.
- 하나의 원본 단위가 선택 layout의 안전 용량을 넘으면 `part 1`, `part 2` 결과 슬라이드로 분할한다.
- 분할 시 `sourceBlockId`와 source range 순서를 유지한다. 각 fragment의 range 합집합은 원본 block 전체와 같고 gap과 overlap이 없어야 한다.
- 표 반복 헤더처럼 결과 가독성을 위해 생성한 복제 요소는 `derivedDuplicate=true`로 표시하고 원본 커버리지 분모·분자에서 제외한다.
- 빈 공간을 줄이기 위한 병합은 가능하지만 서로 다른 원본 단위의 상대 순서를 바꾸지 않는다.
- 기본 정책은 `preserve_content`, `allow_slide_expansion=true`다.

## 5. 포맷별 처리

### 5.1 PPTX

- OOXML에서 슬라이드별 text run, table, chart data, media relationship, group 관계를 추출한다.
- master/layout의 장식 이미지는 원본 콘텐츠 occurrence와 구분한다.
- 원본 master furniture는 결과로 복사하지 않는다. 선택 회사 표준의 furniture가 소유한다.
- 편집 가능한 text/table/chart/simple shape는 native candidate로 유지한다.
- SmartArt, OLE, 복잡한 group은 bounding crop과 접근성 텍스트를 함께 보존하는 raster fallback을 허용한다.

### 5.2 PDF

- 페이지 단위 text span, reading order, image region과 vector region을 추출한다.
- 신뢰도가 높은 텍스트는 native text로 재배치한다.
- 표·도식의 구조 추출 신뢰도가 낮으면 해당 region만 raster fallback한다.
- 전체 페이지를 한 장의 배경 이미지로 사용하는 것은 템플릿 적용이 아니므로 금지한다.

### 5.3 DOCX

- heading, paragraph, list, table, image, caption과 section order를 추출한다.
- pagination은 권위값으로 보지 않고 heading/section을 슬라이드 grouping 기준으로 사용한다.
- 표와 이미지가 연결된 문단 순서를 유지한다.

### 5.4 legacy PPT

- 안전한 중간 PPTX 변환과 content readback이 모두 성공할 때만 수락한다.
- 변환 전후 텍스트·표·이미지 occurrence 비교가 불가능하면 `SOURCE_CONVERSION_REQUIRED`로 실패한다.

### 5.5 지원 단계와 보안 admission

1차 제품 경로는 PPTX만 연다. PDF와 DOCX는 각각 extractor, inventory, readback 경계가 통과된 뒤 capability flag로 별도 활성화한다. legacy PPT는 격리 변환과 변환 전후 readback까지 검증된 뒤 마지막으로 활성화한다. UI는 활성화된 포맷만 `원본 콘텐츠 유지` 지원 대상으로 표시한다.

모든 업로드는 신뢰하지 않는 입력으로 취급한다.

- PPTX/DOCX external relationship과 linked media를 네트워크로 가져오지 않는다.
- 매크로, 실행 가능 attachment, PDF JavaScript는 실행하지 않는다.
- OLE/SmartArt 등 active 또는 unsupported object는 inert block-level raster fallback이 가능할 때만 수락하고 그렇지 않으면 실패한다.
- 압축 해제 크기와 비율, 파일·페이지·슬라이드·개체 수, 이미지 pixel 수, OCR 시간과 메모리 상한을 admission에서 제한한다.
- legacy 변환, PDF render와 OCR은 네트워크가 없는 격리 작업 공간에서 실행한다.
- 추출 텍스트와 추가 디자인 지시는 LLM에게 명령이 아닌 untrusted data로 구획하고, 모델 출력은 허용된 ID와 enum만 받는다.

## 6. 아키텍처

### 6.1 전체 흐름

```text
Asset Gateway / reference snapshot
  → SourceSecurityAdmission + SourceObjectInventory
  → SourceContentExtractor
  → immutable SourceContentManifest/v1
  → ContentAllocator
       + selected authored style pin
       + optional company-standard master pin
  → SourceContentPlacementPlan/v1
  → deterministic SourceContentSceneMaterializer
  → staged Workbench canonical document
  → SceneContentCoverageVerifier
  → isolated candidate PPTX export
  → ExportOOXMLAndRenderReadbackVerifier
  → atomic Workbench/PPTX publication
```

### 6.2 재사용 경계

재사용한다.

- 기존 asset gateway와 owner-scoped `reference_snapshot_ids`
- 문서 분해 job의 취소·진행률·idempotency 패턴
- `authoredStyle` 선택 상태
- published company standard의 `masterPin`
- deterministic materializer 이후의 Authored Scene schema, Workbench CAS, semantic dual output, PPTX export
- 기존 `source_locked`의 image occurrence 및 source provenance 계산 방식

재사용하지 않는다.

- `source_locked`의 byte-exact restoration
- PDF 전체 페이지 raster 복원
- 별도 immutable download artifact를 생성 성공으로 보는 경로
- plain `additional_context`만으로 콘텐츠 보존을 주장하는 경로
- 기존 `author_planned_slide` → `author_slide_html` 생성형 HTML authoring 경로
- content payload를 바꿀 수 있는 vision re-author와 생성형 fallback

### 6.3 모델 책임

```text
SourceContentExtractor
  원본 값을 추출하고 봉인한다. 생성·요약하지 않는다.

ContentAllocator
  sourceBlockId를 template writable slot에 매핑한다.
  LLM을 쓰더라도 원본은 untrusted data로만 취급하고 출력은 sourceBlockId, layoutRole, slotId, sourceRange enum/값으로 제한한다.
  모델 출력에 원본 문구나 데이터 값이 포함돼도 사용하지 않는다.

SourceContentSceneMaterializer
  선택 style/master composition과 placement plan으로 Scene을 결정적으로 만든다.
  원본 문자열, 표 셀, 차트 값과 이미지 asset은 manifest에서만 읽는다.
  author_deck, author_slide_html, vision re-author를 호출하지 않는다.

SceneContentCoverageVerifier
  inventory, manifest, placement fragment와 staged Scene을 비교한다.

ExportOOXMLAndRenderReadbackVerifier
  후보 PPTX의 OOXML 값·관계와 render geometry를 검증한다.
  두 verifier 중 하나라도 실패하면 staged 결과를 폐기하고 publication을 막는다.
```

## 7. 데이터 계약

### 7.1 `SourceContentManifest/v1`

```json
{
  "contractVersion": "source-content-manifest/v1",
  "manifestId": "scm_<sha256>",
  "ownerId": "server-owned",
  "referenceSnapshotId": "uuid",
  "sourceType": "pptx",
  "sourceDigest": "sha256:...",
  "objectInventory": {
    "discoveredObjectCount": 81,
    "classifiedObjectCount": 81,
    "contentBearingObjectCount": 67,
    "unsupportedContentObjectIds": [],
    "unclassifiedObjectIds": [],
    "externalRelationshipCount": 0
  },
  "units": [
    {
      "sourceUnitId": "source-slide:1",
      "sourceIndex": 0,
      "blocks": [
        {
          "sourceBlockId": "source-slide:1:text:3",
          "kind": "text",
          "semanticRole": "body",
          "order": 3,
          "payload": { "text": "원본 문구", "runs": ["원본 문구"] },
          "comparisonProjection": { "unicodeForm": "NFC", "lineEnding": "LF" },
          "contentDigest": "sha256:...",
          "nativeCandidate": true,
          "fallbackAssetId": null
        }
      ]
    }
  ],
  "counts": {
    "textBlocks": 1,
    "tableCells": 0,
    "chartValues": 0,
    "imageOccurrences": 0
  },
  "manifestDigest": "sha256:..."
}
```

`ownerId`는 서버 내부 권한 필드이며 브라우저가 제출하거나 수정하지 않는다.

### 7.2 `SourceContentPlacementPlan/v1`

```json
{
  "contractVersion": "source-content-placement-plan/v1",
  "manifestPin": {
    "manifestId": "scm_<sha256>",
    "digest": "sha256:..."
  },
  "compositionPin": {
    "authoredStyle": {
      "id": "osb-smart-banking",
      "revision": 3,
      "digest": "sha256:..."
    },
    "companyStandard": {
      "masterId": "master:ibiz-proposal-blue",
      "revision": 2,
      "digest": "sha256:..."
    },
    "mergePolicyVersion": "master-slots-authored-body/v3"
  },
  "slides": [
    {
      "outputIndex": 0,
      "layoutRole": "title-content",
      "placements": [
        {
          "sourceBlockId": "source-slide:1:text:3",
          "slotId": "body.primary",
          "renderMode": "native",
          "sourceRange": {
            "kind": "text-run-range",
            "start": 0,
            "endExclusive": 1
          },
          "derivedDuplicate": false
        }
      ]
    }
  ],
  "planDigest": "sha256:..."
}
```

placement plan은 원본 텍스트를 복사해 갖지 않고 `sourceBlockId`와 source range만 참조한다. atomic block은 정확히 한 placement를 가지며, 분할 가능한 block은 gap·overlap 없는 fragment partition을 가진다. Scene materializer는 manifest에서 봉인된 payload를 읽는다.

### 7.3 생성 요청 확장

기존 `GeneratePresentationRequest`의 `template="authored"`, `strategy="authored"`, `authored_style`, `masterPin`, `reference_snapshot_ids`를 유지하고 다음 필드를 추가한다.

```json
{
  "sourceMode": "source_content_restyle",
  "sourceContentManifestPin": {
    "manifestId": "scm_<sha256>",
    "digest": "sha256:..."
  },
  "contentPolicy": {
    "text": "verbatim",
    "preserveAllTableCells": true,
    "preserveAllChartValues": true,
    "preserveAllImageOccurrences": true,
    "preserveSourceOrder": true,
    "allowSlideExpansion": true,
    "allowSummarization": false,
    "overflowPolicy": "add_slides"
  },
  "authored_style": "osb-smart-banking",
  "masterPin": {
    "masterId": "master:ibiz-proposal-blue",
    "revision": 2,
    "digest": "sha256:..."
  }
}
```

`sourceMode`는 `Presentation.generation_mode`에 저장하지 않는다. 서버 admission 이후 immutable generation payload와 presentation metadata의 별도 `sourceContent` projection에 기록한다.

### 7.4 idempotency

다음 digest를 생성 idempotency identity에 포함한다.

- source manifest digest
- authored style digest
- master digest 또는 explicit clear
- merge policy version
- content policy digest
- generation engine version

같은 원본이라도 템플릿이나 회사 표준이 다르면 별도 생성 작업이어야 한다.

## 8. 콘텐츠 커버리지 검증

### 8.1 검증 영수증

```json
{
  "contractVersion": "source-content-coverage-receipt/v1",
  "manifestDigest": "sha256:...",
  "planDigest": "sha256:...",
  "sceneDigest": "sha256:...",
  "candidateArtifactDigest": "sha256:...",
  "exportReadbackDigest": "sha256:...",
  "discoveredObjectCoverage": "81/81",
  "unclassifiedObjectCount": 0,
  "unsupportedContentObjectCount": 0,
  "textBlockCoverage": "24/24",
  "tableCellCoverage": "31/31",
  "chartValueCoverage": "12/12",
  "imageOccurrenceCoverage": "8/8",
  "orderViolations": 0,
  "fragmentGapCount": 0,
  "fragmentOverlapCount": 0,
  "contentDigestMismatches": 0,
  "exportRelationshipMismatches": 0,
  "renderClippingCount": 0,
  "compositionApplication": {
    "styleTokenDigestMatched": true,
    "masterGeometryDigestMatched": true
  },
  "unplacedBlockIds": [],
  "editState": "pristine",
  "status": "passed"
}
```

### 8.2 성공 조건

아래 조건을 모두 만족해야 publication과 download를 허용한다.

- inventory에서 발견한 모든 개체가 분류되고 unclassified object가 0임
- atomic `sourceBlockId`는 정확히 한 번 placement됨
- 분할 block의 source range 합집합이 원본 전체와 같고 gap·overlap이 0임
- text/table/chart payload digest mismatch 0
- image occurrence 누락 0
- source order violation 0
- staged Scene과 최종 PPTX readback 모두에서 overflow와 clipping 0
- 후보 PPTX의 text/table/chart/media relationship readback mismatch 0
- style pin과 master pin이 preview, Scene, checkpoint, export에서 동일
- 선택 style token과 master geometry가 Scene과 최종 PPTX에 실제 적용됨

실패 시 `source_content_coverage_failed`로 종료하고 누락 block 목록을 사용자에게 이해 가능한 원본 페이지/요소 위치로 변환해 보여준다.

### 8.3 Workbench 편집 이후 상태

100% coverage receipt는 최초 생성 revision과 후보 PPTX digest에만 유효하다. Workbench에서 사용자가 텍스트·표·차트·이미지 occurrence를 변경하면 기존 receipt를 덮어쓰지 않고 `stale`로 전환하며 presentation metadata의 `sourceCoverageStatus`를 `modified_by_user`로 표시한다.

- 최초 atomic publication은 반드시 `pristine` + `passed` receipt를 요구한다.
- 사용자 편집 후 export는 허용하되 UI와 export provenance에서 더 이상 `원본 콘텐츠 100% 유지`를 주장하지 않는다.
- 사용자가 보존 재검증을 요청하면 현재 revision으로 새 receipt를 만들고, 의도적 삭제가 있으면 `modified_by_user` 상태를 유지한다.
- receipt에는 presentation revision과 artifact digest를 함께 봉인해 다른 revision에 재사용하지 못하게 한다.

## 9. overflow와 fallback

우선순위는 다음과 같다.

1. 같은 템플릿 내에서 더 큰 capacity layout 선택
2. 하나의 원본 단위를 여러 슬라이드로 분할
3. 이미지 크롭과 text reflow 조정
4. 복잡한 단일 개체만 raster fallback
5. 그래도 배치할 수 없으면 실패

금지한다.

- 본문 삭제
- 자동 요약
- 숫자와 단위 생략
- 읽을 수 없는 최소 폰트로 축소
- 전체 페이지 screenshot으로 템플릿을 가리는 처리
- fallback 발생을 성공 로그에서 숨기는 처리

## 10. 기존 `source_locked` 처리

현재 기능은 다음처럼 격리한다.

- Upload 메인 생성 화면에서 제거
- 필요하면 `도구 > 원본 파일 복원`으로 이동
- 사용자 명칭은 `원본 PPTX/PDF 복원 다운로드`
- AI 저작 스타일이나 회사 표준을 적용하지 않는다는 점을 명시
- 기존 owner scope, retention, cancel, immutable artifact 경계는 보존

새 `source_content_restyle`은 `source_locked` API, job store, artifact store를 호출하지 않는다.

## 11. 오류 계약

| 코드 | 의미 | 사용자 동작 |
|---|---|---|
| `source_content_manifest_invalid` | 원본 manifest 생성 또는 검증 실패 | 다른 파일 선택 |
| `source_content_security_rejected` | active content, 외부 관계 또는 자원 상한 때문에 안전하게 분석할 수 없음 | 원본 정리 후 다시 선택 |
| `source_content_unsupported_object` | 보존 가능한 native/raster 표현이 없는 content-bearing object 발견 | 원본 위치와 지원 범위 확인 |
| `source_content_template_required` | AI 저작 스타일을 해석하지 못함 | 템플릿 다시 선택 |
| `source_content_master_stale` | 선택 회사 표준 revision이 published head가 아님 | 회사 표준 다시 선택 |
| `source_content_unplaceable` | 보존 정책 안에서 배치 불가 | 원본 위치와 원인 확인 |
| `source_content_coverage_failed` | 결과 readback에서 콘텐츠 누락 발견 | 생성 실패 처리, 누락 목록 확인 |
| `source_content_export_readback_failed` | 후보 PPTX에서 값·relationship·geometry 불일치 발견 | 생성 실패 처리 |
| `source_content_composition_not_applied` | pin은 같지만 style/master가 결과에 적용되지 않음 | 생성 실패 처리 |
| `source_content_overflow` | 슬라이드 추가 후에도 clipping 발생 | 생성 실패 처리 |

오류 발생 시 adaptive 생성이나 byte-copy 복원으로 조용히 fallback하지 않는다.

## 12. 코드 변경 지도

### 12.1 Frontend

- `servers/nextjs/store/slices/presentationGenUpload.ts`
  - `sourceLockedGenerationMode`를 UI에서 제거하고 `sourceContentMode` 추가
  - `authoredStyle`, `companyStandard`와 독립 상태로 유지
  - 첫 attachment에서 모드를 잠그는 latch 제거, generation admission 시 snapshot 고정
- `servers/nextjs/app/(presentation-generator)/upload/components/UploadPage.tsx`
  - 원본 콘텐츠 모드에서도 TemplateSelection과 회사 표준 UI 유지
  - 선택 요약과 분석 요약 표시
- `servers/nextjs/app/(presentation-generator)/upload/components/upload-page/useUploadPageState.ts`
  - `handleSourceLockedGeneration` 우회 제거
  - reference snapshot → manifest admission → 기존 authored async generation으로 연결
- `servers/nextjs/app/(presentation-generator)/services/api/source-content/`
  - analyze/status/contract client 신규
- `servers/nextjs/app/(presentation-generator)/services/api/presentation-generation.ts`
  - `sourceMode`, manifest pin, content policy를 authored async body에 추가

### 12.2 Backend

- `servers/fastapi/services/source_content/`
  - security admission, object inventory, extractor, manifest store, allocator, deterministic Scene materializer 신규
  - Scene coverage와 export OOXML/render readback verifier 신규
- `servers/fastapi/models/generate_presentation_request.py`
  - source-content request contract 추가
- `servers/fastapi/api/v1/ppt/endpoints/presentation_generate.py`
  - owner scope manifest admission과 composition pin 결합
- Authored generation/Scene compiler
  - `source_content_restyle`은 `author_planned_slide`, `author_slide_html`, vision re-author를 우회
  - placement plan을 manifest exact payload로 deterministic materialize
  - staged Scene과 후보 PPTX receipt가 모두 통과해야 atomic publication
- DB
  - immutable source-content manifest, generation binding, revision-scoped coverage receipt를 별도 테이블로 추가
  - manifest/reference snapshot claim, retention, cancel, retry와 orphan cleanup 경계를 generation job과 함께 봉인
  - legacy `generation_mode` 의미는 변경하지 않음

## 13. 구현 단계

### 단계 1 — 계약과 분석

- UI 명칭과 상태 계약 교체
- PPTX만 1차 지원하고 나머지 포맷은 capability flag로 닫음
- source security admission과 package object inventory
- `SourceContentManifest/v1` 모델과 digest
- PPTX extractor
- manifest owner scope와 idempotent analyze

완료 기준: 실파일에서 discovered/classified object와 text/table/chart/image occurrence count 및 digest가 반복 실행 시 동일하고 unclassified object가 0이다.

### 단계 2 — 템플릿 composition과 배치

- authored style와 company standard pin 결합
- capacity-aware layout 선택
- source block → slot placement plan
- source range 기반 fragment partition
- overflow slide expansion

완료 기준: 같은 manifest와 composition pin이 같은 placement plan digest를 만든다.

### 단계 3 — Scene/PPTX 생성

- 생성형 HTML/vision re-author를 호출하지 않는 deterministic Scene materializer
- placement plan을 manifest exact payload로 Authored Scene에 materialize
- native candidate와 block-level raster fallback
- staged Workbench document와 격리 후보 PPTX export 연결

완료 기준: 결과 deck이 선택한 템플릿 토큰과 master geometry를 실제 사용하며 원본 콘텐츠가 편집 가능한 범위에서 native 객체다.

### 단계 4 — fail-closed 검증과 atomic publication

- Scene coverage receipt와 후보 PPTX OOXML/render readback receipt
- clipping/overflow gate
- Workbench 편집 시 receipt stale/modified_by_user 전환
- 두 receipt 통과 후 atomic publication
- 기존 source_locked UI 제거 또는 도구 화면 이동
- 실제 브라우저 Upload → generate → Workbench → export 검증

완료 기준: Scene 또는 후보 PPTX에서 콘텐츠 한 항목을 의도적으로 누락한 fixture가 publication 전에 실패한다.

### 단계 5 — 추가 포맷 활성화

- PDF extractor/inventory/readback 경계를 완성한 뒤 PDF capability flag 활성화
- DOCX extractor/inventory/readback 경계를 완성한 뒤 DOCX capability flag 활성화
- legacy PPT 격리 변환과 변환 전후 readback이 통과한 뒤 마지막으로 활성화

완료 기준: 각 포맷의 실파일 경계가 독립적으로 통과하며 미지원 object가 조용히 누락되지 않는다.

## 14. 최소 검증 계획

각 변경 경계마다 가장 좁은 검증 하나만 실행한다.

1. UI 경계: component test에서 원본 콘텐츠 모드 활성화 후 템플릿·회사 표준 UI가 계속 보이고 요청에 pin이 포함되는지 확인
2. security/inventory 경계: external relationship 또는 unsupported content object fixture가 네트워크 접근 없이 fail closed 하는지 확인
3. manifest 경계: extractor fixture 한 개로 discovered/classified count와 block/value/occurrence digest 결정성 확인
4. allocator 경계: overflow fixture가 내용 삭제 없이 추가 슬라이드를 만들고 fragment gap·overlap이 0인지 확인
5. materializer 경계: source-content 모드에서 `author_slide_html`과 vision re-author가 호출되지 않고 manifest 문자열이 그대로 Scene에 들어가는지 확인
6. Scene verifier 경계: block 누락 fixture가 `source_content_coverage_failed`인지 확인
7. export verifier 경계: 후보 PPTX의 text 또는 media relationship을 하나 제거한 fixture가 publication 전에 실패하는지 확인
8. edit lifecycle 경계: Workbench 편집 후 기존 receipt가 `stale`, 상태가 `modified_by_user`로 바뀌는지 확인
9. 제품 경로: 실제 PPTX 한 개와 선택 템플릿 한 개로 Upload → deterministic Scene → candidate export → atomic publication을 실행하고 콘텐츠 coverage 100%, 실제 style token/master geometry 적용, 최종 artifact readback을 확인

PDF와 DOCX의 실파일 검증은 포맷 extractor 단계가 완성된 뒤 각각 별도 경계로 수행한다. fixture PASS만으로 전체 제품 경로 성공을 주장하지 않는다.

## 15. 설계 결정 요약

- 기능명은 `원본 콘텐츠 유지`다.
- 생성 모드는 `source_content_restyle`이다.
- 원본 content payload는 immutable manifest가 소유한다.
- manifest 분모는 extractor가 추출한 항목이 아니라 원본 object inventory가 소유한다.
- AI 저작 스타일과 회사 표준은 immutable composition pin으로 함께 적용한다.
- LLM은 원문을 다시 쓰지 않고 ID 기반 분류와 layout 선택만 수행하며 결과 payload는 deterministic materializer가 manifest에서 읽는다.
- overflow는 슬라이드 추가로 해결한다.
- Scene coverage와 최종 PPTX readback coverage가 모두 100%가 아니면 결과를 게시하지 않는다.
- 최초 receipt는 생성 revision에만 유효하고 사용자 편집 후 `modified_by_user`로 전환한다.
- 1차 지원 포맷은 PPTX이며 PDF, DOCX, legacy PPT는 포맷별 검증 뒤 활성화한다.
- 기존 byte-copy `source_locked`는 별도 복원 도구로 격리한다.

## 16. 구현 전 확인할 사항

다음은 제품 계약이 아니라 구현 착수 전에 현재 코드와 fixture로 확인할 기술 사항이다.

- 현행 PPTX extractor가 text/table/chart relationship을 어느 수준까지 읽을 수 있는지
- DOCX asset gateway snapshot이 원본 relationship과 reading order를 보존하는지
- Authored Scene native table/chart 후보가 source payload digest readback을 제공하는지
- PDF region 단위 raster fallback을 위한 안정적인 crop 좌표계가 있는지
- 대용량 manifest 저장과 generation job payload 크기 상한

이 확인 결과가 기능 의미를 바꾸지는 않는다. 지원할 수 없는 포맷이나 개체는 명시적으로 실패하거나 block-level fallback을 사용하며, 내용 누락이나 byte-copy로 조용히 대체하지 않는다.
