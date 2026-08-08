# AI Authored Template Contract 설계

상태: 구현 전 설계 확정안
대상: 현재 AI authored HTML 생성 방식에 official Template V2의 유용한 개념만 선택 적용
API 기준: 기존 `/api/v1` 유지
핵심 원칙: 기존 authored HTML/PNG, semantic outline, authored-hybrid export를 보호한다.

## 1. 결정 요약

이번 작업은 official Template V2를 authored 모드에 그대로 이식하지 않는다. 대신 다음 개념만
`Authored Template Contract`라는 별도 계층으로 도입한다.

- 역할별 typed slot과 기계 검증 가능한 콘텐츠 제한
- 위치나 배열 순서에 의존하지 않는 stable slide/binding ID
- 사용자 콘텐츠인 `editable` 요소와 템플릿 장식인 `decorative` 요소의 분리
- 콘텐츠를 유지한 채 표현만 다시 만드는 hydration/retemplate 경계
- 선택한 슬라이드나 binding만 다시 생성하는 부분 리템플릿 기반
- 안전한 요소만 V2 UI 요소로 승격하는 향후 `편집 가능한 복사본` 경로

초기 단계에서 authored HTML과 PNG는 계속 렌더 결과의 원본이며, 저장된 semantic outline과
contract binding이 콘텐츠의 원본이다. official V2의 `slide.ui`를 authored 저장 형식이나
authored-hybrid export 입력으로 직접 사용하지 않는다.

## 2. 용어와 식별자

| 용어 | 의미 |
| --- | --- |
| Authored Style | 현재 YAML의 색상, 폰트, brief, role별 reference image |
| Authored Template Contract | role별 slot, 타입, 필수 여부, 콘텐츠 제한을 정의하는 새 기계 계약 |
| Binding | semantic content의 한 조각과 contract slot의 안정적인 연결 |
| Render Artifact | AI가 만든 self-contained HTML과 그 PNG |
| Editable V2 Copy | 안전하게 변환 가능한 요소만 `slide.ui`로 옮긴 별도 프레젠테이션 |

식별자는 아래처럼 서로 직교하게 유지한다.

- presentation mode: `template | adaptive | authored`
- authored contract schema: `presenton.authored-template-contract/v1`
- authored binding schema: `presenton.authored-template-binding/v1`
- authored-hybrid export schema: 기존 `presenton.authored-hybrid/v1`
- official-compatible V2 UI schema: 별도 discriminator를 사용하고 위 값들과 공유하지 않는다.

현재 내부 Template V2 PoC의 `version="v2-standard"`는 official V2와 구조가 다르므로 통합 전에
`presenton.internal-title-body-poc/v1`처럼 명시적인 실험 식별자로 변경해야 한다. 이 변경 전에는
PoC 데이터와 official V2 데이터를 같은 저장·변환 경로에 넣지 않는다.

## 3. 목표와 비목표

### 목표

1. 같은 semantic content로 스타일을 바꿔도 제목, 본문, 수치, 표 데이터가 보존되게 한다.
2. 역할별 길이와 밀도 제한을 생성 전에 검증한다.
3. AI 생성 HTML 안에서 콘텐츠 요소와 장식 요소를 식별할 수 있게 한다.
4. 전체 덱 재생성 없이 슬라이드 단위 재생성을 가능하게 한다.
5. 기존 authored-hybrid의 편집 가능 요소, 최소 9pt, 직선 보정, text-fit 보호를 유지한다.
6. 기능 플래그, allowlist, kill switch로 한 스타일부터 안전하게 검증한다.

### 비목표

- 공개 API `/api/v2` 신설
- 기존 authored 프레젠테이션의 일괄 마이그레이션
- authored HTML을 official V2 `slide.ui`로 즉시 대체
- authored-hybrid exporter에 V2 UI 노드를 직접 입력
- 현재 자동 저장과 Undo/Redo의 교체
- 모든 CSS/SVG/차트 요소를 편집 가능한 PowerPoint 도형으로 강제 변환

## 4. 현재 경계

현재 authored 리템플릿은 저장된 `PresentationOutlineModel`을 원본으로 새 프레젠테이션을 만들며,
기존 `html_content`를 생성 입력으로 사용하지 않는다. 이 원본 보존 규칙은 그대로 유지한다.

현재 데이터에는 다음 한계가 있다.

- `SlideOutlineModel`은 `content`만 가지므로 슬라이드의 안정적인 의미 ID가 없다.
- style YAML의 archetype과 밀도 규칙은 prose `brief`라서 기계적으로 검증되지 않는다.
- 생성 HTML의 어떤 노드가 사용자 콘텐츠인지 장식인지 명시되어 있지 않다.
- 리템플릿은 전 슬라이드를 다시 authoring하므로 비용과 문구 drift가 발생할 수 있다.

구현 전에 고정해야 할 P0 사실:

- version restore는 DB slide UUID를 다시 만들 수 있으므로 이를 stable logical ID로 쓰면 안 된다.
- `SlideModel.get_new_slide()` 기반 경로는 현재 `html_content`를 복사하지 않으므로 contract 덱의
  edit/derive/duplicate 동작을 별도 테스트와 명시적 복제로 보호해야 한다.
- web export 진입점은 요청의 `pptxMode`를 먼저 보므로 persisted capability 검증을 앞단에 둬야 한다.
- backend와 frontend의 legacy authored 판별 기준이 달라 `layout is None` 단독 backfill은 위험하다.

## 5. 목표 아키텍처

```text
저장된 semantic manuscript
        |
        v
role별 Authored Template Contract
        |
        v
검증된 slide/binding plan
        |
        v
AI authored HTML + binding markers
        |
        +--> DOM/콘텐츠 보존 검증
        |
        +--> optional Vision QA
        |
        v
기존 HTML/PNG 저장
        |
        +--> fidelity export
        +--> 기존 authored-hybrid export
        +--> 향후 별도 Editable V2 Copy
```

`semantic manuscript + binding plan`이 콘텐츠 원본이고 HTML/PNG는 파생 렌더 결과다. Vision QA는
레이아웃을 수정할 수 있지만 binding의 정규화된 값은 명시적 승인 없이 바꾸면 안 된다.

## 6. Contract 모델

style YAML에 선택적 `contract` 블록을 추가한다. contract가 없는 style은 현재 파이프라인으로
동작하므로 기존 style과의 하위 호환성을 유지한다.

```yaml
contract:
  schema: presenton.authored-template-contract/v1
  archetypes:
    cover:
      slots:
        - id: headline
          kind: text
          required: true
          editable: true
          constraints:
            max_chars: 80
            max_lines: 3
        - id: subtitle
          kind: text
          required: false
          editable: true
          constraints:
            max_chars: 140
            max_lines: 3
      decorations:
        - id: editorial-rule
          kind: shape
          editable: false
    data:
      slots:
        - id: takeaway
          kind: text
          required: true
          editable: true
          constraints:
            max_chars: 90
            max_lines: 2
        - id: key-figures
          kind: text-list
          required: true
          editable: true
          constraints:
            max_items: 4
            max_chars_per_item: 60
```

초기 지원 `kind`는 `text`, `text-list`, `image`로 제한한다. `table`, `chart`, `infographic`,
중첩 group/container와 V2 flex/grid 문법은 실제 authored 사례와 보존 규칙을 확보한 뒤 추가한다.

검증 규칙은 Pydantic `extra="forbid"` 상당의 fail-closed 방식으로 구현한다.

- 알 수 없는 schema, archetype, slot kind, 필드는 거부한다.
- slot ID는 archetype 안에서 중복될 수 없다.
- `required=true` slot은 빈 값이 될 수 없다.
- 목록, 표, 차트의 최대 크기는 authoring 전에 검증한다.
- 장식은 semantic binding을 가질 수 없다.
- 계약이 prose brief보다 우선하며 서로 충돌하면 style 로드를 실패시킨다.

## 7. Stable ID와 Binding 모델

위치 기반 `slide-0`, `title-0`은 삽입·삭제·복제 때 의미가 바뀌므로 사용하지 않는다.

```json
{
  "schema": "presenton.authored-template-binding/v1",
  "slide_id": "sl_01J...",
  "archetype": "data",
  "contract_hash": "sha256:...",
  "source_hash": "sha256:...",
  "bindings": [
    {
      "id": "bd_01J...",
      "slot_id": "takeaway",
      "kind": "text",
      "editable": true,
      "value": "영업이익률은 전년 대비 4.2%p 개선됐다.",
      "value_hash": "sha256:..."
    }
  ]
}
```

- 새 authored 생성 시 각 semantic slide에 opaque stable ID를 한 번 부여한다.
- 이 ID는 DB `SlideModel.id`가 아니다. 현재 version restore와 일부 edit/derive 경로는 DB slide
  UUID를 다시 만들 수 있으므로 `logical_slide_id`를 contract metadata 안에 별도로 보존한다.
- slot 값이 수정되더라도 binding ID는 유지한다.
- 슬라이드 순서 변경은 `index`만 바꾸며 slide/binding ID를 재발급하지 않는다.
- 덱 복제는 새 presentation/slide/binding ID를 만들고 `source_*_id`로 계보만 남긴다.
- 동일 semantic slide를 style-only 리템플릿해 새 덱을 만들 때는 새 logical slide/binding ID를
  발급하고 결정적인 old-to-new mapping과 `source_*_id` 계보를 남긴다. stable의 범위는 동일 덱의
  save/reopen/edit/Undo/Redo이며 fork 사이의 ID 공유가 아니다.
- `source_hash`는 정규화된 semantic source 전체, `value_hash`는 slot 단위 보존 검증에 쓴다.

`SlideOutlineModel` 자체를 즉시 변경하면 기존 생성 경로에 파급이 크다. 1차 구현에서는
`SlideModel.properties["authored_template"]`에 binding envelope와 template ID/revision을
저장한다. 모든 슬라이드에 presentation 단위 provenance를 중복 기록하고 덱 조회 시 일관성을
검증한다. `theme`은 authored style, `deck_plan`은 adaptive composition이라는 현재 의미를
유지하므로 contract metadata를 넣지 않는다. 기존 버전 스냅샷이 `properties`, `html_content`,
speaker note를 함께 보존하는지 통합 테스트로 먼저 확인한다.

PoC가 안정화되기 전에는 새 DB column과 Alembic migration을 추가하지 않는다. 조회·검색·인덱싱
요구가 확인되면 `contract_version`과 `contract_state`를 additive nullable column으로 승격한다.
이때도 기존 행은 명확한 `mode=authored`와 유효한 slide HTML 등 여러 신호가 일치할 때만
backfill하며, `layout is None` 하나만으로 authored라고 분류하지 않는다.

## 8. HTML marker 계약

AI authoring 결과의 사용자 콘텐츠 노드는 다음 속성을 포함한다.

```html
<h1
  data-presenton-binding-id="bd_01J..."
  data-presenton-slot-id="headline"
  data-presenton-editable="true"
>
  분기 성장을 만든 세 가지 신호
</h1>
```

장식 노드는 다음처럼 semantic binding과 분리한다.

```html
<div
  data-presenton-decoration-id="editorial-rule"
  data-presenton-editable="false"
></div>
```

생성 모델의 속성 준수를 신뢰하지 않고 HTML 파서로 검증한다.

- 문서 안 binding ID는 중복될 수 없다.
- 모든 필수 binding이 정확히 한 번 존재해야 한다.
- HTML 텍스트를 정규화한 값은 저장된 binding 값과 일치해야 한다.
- decorative 노드는 binding ID를 가질 수 없다.
- ID와 slot은 서버가 만든 값만 허용하고 사용자 입력을 CSS selector로 직접 사용하지 않는다.
- 검증 실패 시 해당 HTML을 성공 결과로 저장하지 않는다.
- HTML fingerprint와 marker 검증 상태를 `valid | degraded`로 기록한다.

초기에는 marker가 없는 현재 fallback HTML을 contract 성공 결과로 취급하지 않는다. contract
경로는 제한 횟수만 재시도하고, shadow 모드에서는 현재 authored 생성으로 안전하게 회귀한다.
strict 모드에서는 콘텐츠 보존 실패를 사용자에게 명확히 알린다.
Vision QA처럼 저장된 HTML을 다시 쓰는 경로는 수정 직후 fingerprint와 marker를 전수 검증한다.
검증 실패 시 시각 결과를 보존할 수는 있지만 상태를 `degraded`로 내리고 contract 편집과 변환을
차단한다.

## 9. 생성과 리템플릿 흐름

### 9.1 신규 생성

1. style과 role별 contract를 resolve한다.
2. semantic outline을 role/archetype별 slot 값으로 compile한다.
3. 길이, 개수, 타입 제한과 stable ID를 검증한다.
4. contract와 binding plan을 prompt에 넣어 HTML을 author한다.
5. HTML marker와 콘텐츠 보존을 검증한다.
6. illustration을 채우고 다시 marker/콘텐츠 보존을 확인한다.
7. PNG를 렌더한다.
8. Vision QA가 켜졌다면 수정 전후 binding hash를 비교한다.
9. 기존 `PresentationModel`, `SlideModel`, HTML/PNG 형태로 저장한다.

### 9.2 스타일 전용 리템플릿

- 원본 HTML이 아니라 저장된 binding 값과 semantic outline을 사용한다.
- slot compile을 다시 하지 않고 선택한 style contract에 hydrate한다.
- 원본 덱은 수정하지 않고 새 덱을 만든다.
- 변환 전후 `source_hash`와 각 `value_hash`가 같아야 성공한다.
- 대상 style이 필수 slot을 더 요구하면 자동으로 내용을 발명하지 않고 `incompatible_contract`
  오류와 누락 slot 목록을 반환한다.

### 9.3 부분 리템플릿

1차 부분 작업의 단위는 슬라이드다.

- `all`: 현재와 같은 전체 새 덱
- `selected_slides`: 선택 slide ID만 다시 authoring
- `style_only`: 모든 binding 값을 재사용해 표현만 변경

binding 하나만 직접 수정하는 기능은 slide 단위 검증이 안정화된 뒤 추가한다. 선택하지 않은
슬라이드의 HTML/PNG를 새 덱으로 복사할 때는 asset 경로와 계보를 새 presentation에 맞게 안전하게
복제하며 원본 파일을 덮어쓰지 않는다.

요청은 index가 아니라 `logical_slide_id`, `expected_revision`, `idempotency_key`를 사용한다.
새 덱에서는 presentation/slide/binding ID와 asset locator가 달라지므로 비대상 슬라이드의
보존 조건을 다음 세 층으로 나눈다.

- `semantic_digest`: 정규화된 binding 값, slot 순서, semantic outline이 원본과 정확히 같다.
- `render_digest`: HTML/PNG byte 또는 정규화된 렌더 결과가 원본과 정확히 같고 render 호출
  횟수가 0이다.
- `identity/provenance`: 새 ID와 asset locator는 명시적인 old-to-new mapping 안에서만 달라진다.

따라서 새 덱의 full canonical payload hash 전체가 같아야 한다고 요구하지 않는다. 향후
in-place 적용에서만 logical slide/binding ID 동일성을 요구한다. 생성 실패, 취소, revision
충돌이 발생하면 새 덱이나 live deck에 부분 저장을 남기지 않는다. in-place 적용은 이 계약이
검증된 뒤 snapshot + compare-and-swap을 갖춘 고급 기능으로만 추가한다.

## 10. API 설계

공개 경로는 계속 `/api/v1`이다. 기존 요청은 변경 없이 동작하고 필드는 모두 additive로 추가한다.

```json
POST /api/v1/ppt/presentation/{id}/retemplate
{
  "authored_style": "signal",
  "vision_qa": false,
  "strategy": "full",
  "slide_ids": [],
  "strict_content_preservation": true,
  "expected_revision": "rev_01J...",
  "idempotency_key": "..."
}
```

- `strategy` 기본값은 기존 의미와 같은 `full`
- `selected_slides`일 때만 `slide_ids` 허용
- contract 미지원 style의 `full`은 기존 동작 유지
- contract 지원 style의 `style_only`는 binding metadata가 있는 source만 허용
- task 결과에 새 presentation ID와 content preservation report를 추가

동시성 및 재시도 규범:

- 요청 수락 시 source revision과 semantic/render digest를 고정하고 완료 직전에 CAS로 다시
  확인한다.
- `expected_revision`이 없거나 현재 revision과 다르면 `409`를 반환하고 DB row와 asset을
  변경하지 않는다.
- 같은 key와 같은 정규화 payload는 같은 task/result를 반환한다.
- 같은 key와 다른 payload는 `409`를 반환한다.
- 동시에 들어온 중복 요청은 실제 author/render 작업을 한 번만 실행한다.
- 취소나 실패 후에는 presentation/slide row, asset, 임시 idempotency result가 부분적으로
  남지 않으며 같은 요청을 안전하게 재시도할 수 있다.

서버 권위 경계:

- `SlideModel.properties["authored_template"]` envelope의 schema, logical/binding ID,
  provenance, source/value hash는 server-owned 필드다.
- 현재 autosave의 전체 slide replacement 입력을 그대로 신뢰하지 않는다. update endpoint는
  이 envelope를 저장된 값과 서버 계산 결과로 merge하거나, 누락·변조·stale 입력을 `4xx`로
  거절한다.
- 클라이언트는 허용된 binding value edit만 별도 명령으로 제출하고 hash와 provenance를 직접
  정하지 않는다.
- 이 경계를 구현하기 전에는 contract metadata가 포함된 덱의 일반 autosave를 production에
  활성화하지 않는다.

향후 별도 API:

```text
POST /api/v1/ppt/presentation/{id}/editable-copy
```

이 API는 원본을 수정하지 않고 새 V2 UI 프레젠테이션을 만든다. 같은 작업에서 authored HTML
저장 형식을 바꾸지 않는다.

## 11. 프론트 UX

1. 기존 `AuthoredTemplateChanger`의 기본 동작은 `새 프레젠테이션으로 전체 변경`을 유지한다.
2. contract 호환성이 확인된 경우에만 `내용 고정`과 `선택 슬라이드만` 옵션을 노출한다.
3. 처리 완료 후 아래 보존 리포트를 보여준다.
   - 보존된 binding 수
   - 다시 생성된 슬라이드 수
   - contract 제약으로 조정이 필요한 항목
   - fallback 발생 여부
4. strict content preservation 실패 시 새 덱을 성공 화면으로 열지 않는다.
5. 향후 `편집 가능한 복사본 만들기`는 authored 원본 편집으로 표현하지 않는다.

읽기 전용 editability preflight를 먼저 제공해 슬라이드별 editable/decorative/unsupported
비율과 잠금 사유를 보여준다. `편집 가능한 복사본`은 preflight 결과를 확인한 사용자의 명시적인
요청으로만 시작한다.

직접 binding 편집 UI를 추가할 때 장식 요소는 선택·편집 대상에서 제외하고, editable coverage를
슬라이드별로 표시한다. authored view-only 화면에서 현재 backend가 거부하는 공용 편집/추가
액션도 capability에 따라 숨긴다.

## 12. Editable V2 Copy

이 단계는 contract 기반 부분 리템플릿이 안정화된 뒤 수행한다.

- text, image, 단순 shape/line, 안전한 table/chart만 V2 UI 요소로 변환
- 복잡한 CSS, filter, SVG illustration, unsupported chart는 raster backplate에 유지
- promoted 요소는 backplate에서 제거해 중복 렌더를 막는다.
- 변환 결과에 `editable_coverage`와 raster-only 이유 코드를 저장한다.
- 새 덱의 mode/format은 authored 원본과 구분한다.
- authored-hybrid DOM 추출 결과를 V2 source of truth로 역사용하지 않는다.
- 현재 safe-element 판정기는 Next.js/TypeScript에 있으므로 Python에 별도 classifier를 포팅하지
  않는다. Next 내부 worker가 fingerprinted report와 V2 payload를 만들고 FastAPI는 계약 검증과
  clone/persist를 담당해 판정 drift를 막는다.

이 경로는 완전 편집 가능성을 약속하지 않는다. 대신 fidelity를 유지하면서 안전한 범위만
편집 가능하게 만드는 별도 복사본이다.

## 13. Export capability matrix

| 저장 형식 | 화면 렌더 | PPTX export | 허용 사항 |
| --- | --- | --- | --- |
| authored HTML/PNG | authored viewer | fidelity 또는 기존 authored-hybrid | 현재 9pt, axis-line, text-fit, raster fallback 유지 |
| template/adaptive V1 | 기존 React editor | 기존 presentation-export | 현재 경로 유지 |
| official-compatible V2 UI | V2 direct editor | V2 전용 renderer/export | authored-hybrid에 입력 금지 |
| Editable V2 Copy | V2 direct editor | V2 전용 renderer/export | raster backplate + 안전하게 승격한 요소 |

라우팅은 `mode` 단독 추론 대신 명시적인 capability resolver가 결정한다. 알 수 없는 format은
가장 가까운 exporter로 추측하지 않고 export를 중단한다.

resolver는 DB에 저장된 identity를 신뢰하고 클라이언트의 mode/version은 신뢰하지 않는다.
`pptxMode`는 preference일 뿐이다. `authored + slide.ui`, `V2 + html_content`, marker와 payload가
불일치하는 mixed row는 편집/생성을 차단하고 read-only recovery로 격리한다. 유효한 authored
HTML이 남아 있는 경우에만 명시적인 fidelity recovery export를 허용한다. 정상 authored-hybrid
처리 중 개별 요소/슬라이드 추출 실패에 대한 기존 fidelity fallback은 유지하고 effective mode와
fallback reason을 기록한다.

## 14. Feature flag와 rollout

새 플래그는 기존 `ENABLE_TEMPLATE_V2_POC`와 공유하지 않는다.

```text
ENABLE_AUTHORED_TEMPLATE_CONTRACTS=false
AUTHORED_TEMPLATE_CONTRACT_STYLE_ALLOWLIST=signal
AUTHORED_TEMPLATE_CONTRACT_STRICT=false
```

- kill switch는 신규 contract 생성만 중단하고 기존 contract 덱 조회/export는 허용한다.
- style allowlist가 비어 있으면 기능을 노출하지 않는다.
- 사용자 allowlist가 필요하면 style allowlist와 별도 정책으로 추가한다.
- telemetry에는 사용자 텍스트, HTML, prompt를 기록하지 않는다.

단계:

0. 내부 Template V2 PoC의 `v2-standard` 식별자 충돌 제거
1. `signal` 한 style의 contract loader/validator와 shadow compile
2. contract HTML marker 및 콘텐츠 보존 검증, UI 노출 없음
3. 내부 allowlist에 `style_only` 새 덱 생성
4. 선택 슬라이드 부분 리템플릿
5. `편집 가능한 복사본` 실험
6. 신규 사용자/신규 템플릿부터 점진 확대

각 단계의 kill switch는 데이터 삭제나 rollback migration 없이 작동해야 한다.

## 15. Failure policy

| 실패 | 동작 |
| --- | --- |
| style contract parse 실패 | 해당 style contract 기능만 비활성화, 기존 style 생성은 유지 |
| slot compile 실패 | 생성 전 중단, 오류 코드와 slot만 반환 |
| HTML marker 누락/중복 | 제한 재시도 후 strict는 실패, shadow는 기존 authored 경로로 fallback |
| 콘텐츠 hash 불일치 | 결과 저장/공개 금지, 해당 슬라이드 재시도 |
| Vision QA가 binding 변경 | 수정본 폐기하고 QA 이전 HTML/PNG 유지 |
| 부분 복제 asset 실패 | 새 덱 생성을 실패 처리, 원본 유지 |
| exporter/format 불일치 | fidelity로 몰래 전환하지 않고 명시적 오류 |

일반 리템플릿의 기존 fallback과 contract 보존 보장은 구분한다. 사용자에게 `내용 고정`을
약속한 strict 요청은 보존 검증 실패 후 현재 자유 authoring으로 조용히 fallback하면 안 된다.

## 16. 관측 지표

콘텐츠를 기록하지 않고 다음만 수집한다.

- contract schema, style ID/hash, archetype
- compile/validation violation code와 개수
- HTML marker 누락/중복 개수
- content hash mismatch 개수
- retry/fallback 비율
- 슬라이드별 author/render/QA 시간
- 부분 리템플릿 대상/재사용 슬라이드 수
- editable copy의 promoted/raster-only 요소 수와 coverage
- exporter capability route와 실패 코드

## 17. 테스트 전략과 승인 조건

### 단위 테스트

- contract exact-key, enum, 중복 ID, 필수 slot, 크기 제한
- stable ID의 순서 변경 불변성, 복제 시 재발급
- canonical text/hash 정규화
- HTML marker 누락, 중복, 장식 오염, 콘텐츠 불일치
- feature flag, allowlist, kill switch, strict/shadow 정책
- capability resolver의 모든 format/export 조합
- Python/TypeScript가 공유 fixture를 같은 canonical JSON/hash로 해석
- Unicode CJK, emoji, RTL, NFC/NFD 정규화

### 서비스 통합 테스트

- generate → save → reopen에서 binding envelope 동일
- duplicate에서 내용은 같고 ID는 새로 발급
- `A 저장 → B autosave → reload → Undo`가 정확히 A, `Redo`가 정확히 B를 복원
- 첫 autosave와 60초 throttle 구간을 각각 포함해 snapshot source가 incoming state가 아닌
  변경 전 DB state인지 검증
- version restore와 edit/derive에서 logical ID, binding ID, HTML, properties, note,
  asset ref, semantic/render digest의 기대값을 경로별로 exact match
- full/style-only/selected-slides 리템플릿의 원본 불변성
- 새 덱의 비대상 슬라이드는 semantic/render digest 동일, render 호출 0회,
  identity/provenance 변경은 old-to-new mapping과 정확히 일치
- Vision QA 전후 binding 보존
- 오류 후 presentation/slide/asset의 부분 저장이 남지 않음
- missing/stale revision은 `409`이며 DB/asset 변경 0건
- same-key/same-payload는 같은 task/result, same-key/different-payload는 `409`
- concurrent duplicate는 실제 생성 1회, cancel/failure 후 partial row/file 0개
- autosave 입력에서 server-owned schema/ID/hash의 누락·변조·stale contract를 거절하거나
  서버 값으로 안전하게 복원

### Export 회귀

- authored fidelity와 hybrid가 contract marker 유무와 무관하게 동작
- 최소 9pt 유지
- 가로/세로선과 connector 직선 보정 유지
- 긴 텍스트의 text-fit과 잘림 방지 유지
- unsupported 요소가 raster fallback에서 사라지거나 중복되지 않음
- presentation-export v0.4.2 경로와 authored-hybrid 경로가 섞이지 않음
- V2 fixture가 authored-hybrid runner를 한 번도 호출하지 않음
- export cache key가 contract revision과 render fingerprint를 포함

### 브라우저 E2E

- contract 미지원 style은 기존 UX와 동일
- 지원 style은 capability 확인 뒤 옵션 노출
- 원본 덱이 바뀌지 않고 새 덱으로 이동
- strict 보존 실패는 성공으로 표시되지 않음
- 선택 슬라이드만 새 결과로 변경
- save → reopen → duplicate → Undo/Redo → fidelity/hybrid export 전체 lifecycle 통과

### CI 연결 규범

- workflow에 실제 등록되어 실행된 테스트만 acceptance 충족으로 계산한다.
- PR gate는 shared schema/service/route matrix와 1개 lifecycle smoke를 실행한다.
- G4/release gate는 실서버 browser lifecycle, 실제 PPTX/PDF inspection, 1/10/30/100 slide
  성능·메모리·임시 파일 회수 테스트를 실행한다.
- 현재 `.github/workflows/test-all.yml`에 빠진 authored-hybrid/export-performance runner와
  version-history Cypress coverage를 먼저 등록한 뒤 이를 release gate로 승격한다.
- 테스트 파일 존재나 로컬 수동 통과만으로 rollout 단계를 올리지 않는다.

### 단계별 승인 기준

- Phase 1: loader/validator 단위 테스트 100%, production output 변화 없음
- Phase 2: 내부 fixture에서 binding/content hash mismatch 0건
- Phase 3: 20개 이상 실제 내부 덱에서 semantic content mismatch 0건,
  위 exact lifecycle gate, visual QA 및 hybrid export 기준선 통과
- Phase 4: 부분 리템플릿이 전체 리템플릿보다 authoring 호출과 지연을 실제로 절감
- Phase 5: editable copy가 fidelity 기준을 통과하고 coverage/reason을 정확히 보고

## 18. 구현 단위와 파일 후보

1. contract 모델/loader
   - `servers/fastapi/models/authored_template_contract.py`
   - `servers/fastapi/utils/authored_styles.py`
   - `servers/fastapi/tests/unit/test_authored_template_contract.py`
2. binding compile/검증
   - `servers/fastapi/services/authored_template_contract_service.py`
   - `servers/fastapi/tests/unit/test_authored_template_contract_service.py`
3. authoring marker 연동
   - `servers/fastapi/utils/llm_calls/author_slide.py`
   - `servers/fastapi/utils/llm_calls/author_deck.py`
   - `servers/fastapi/services/authored_presentation_service.py`
4. 리템플릿 API와 보존 report
   - `servers/fastapi/models/presentation_from_template.py`
   - `servers/fastapi/api/v1/ppt/endpoints/presentation.py`
   - 관련 endpoint/service 통합 테스트
5. frontend capability/UX
   - `servers/nextjs/app/(presentation-generator)/presentation/components/AuthoredTemplateChanger.tsx`
   - `servers/nextjs/app/(presentation-generator)/services/api/presentation-generation.ts`
   - Cypress component/E2E 테스트
6. export capability resolver와 회귀 suite
   - 기존 authored-hybrid 코드는 동작 변경 없이 routing guard부터 추가
7. Editable V2 Copy
   - 앞 단계 승인 후 별도 설계/구현 작업으로 분리

## 19. 첫 구현 스프린트

첫 스프린트는 production 생성 결과를 바꾸지 않는 아래 범위로 제한한다.

1. 내부 Template V2 PoC schema marker 충돌 제거
2. `AuthoredTemplateContract` Pydantic 모델과 loader 추가
3. `signal.yaml`용 cover/content/data 세 archetype contract 추가
4. semantic outline을 binding plan으로 shadow compile
5. content-free validation telemetry와 단위 테스트 추가

HTML marker, 부분 리템플릿, API/UI 변경은 shadow compile의 실제 실패 유형과 제약 적합성을
확인한 다음 스프린트에서 시작한다.
