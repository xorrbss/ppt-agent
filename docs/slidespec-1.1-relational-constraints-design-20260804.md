# SlideSpec 1.1 관계형 제약 설계

## 1. 목적

고품질 검수가 한 결함을 없애는 과정에서 원래 정상인 정렬, 간격, 그룹 구조, 가독성을 훼손하지 못하게 한다. 제약은 후보 생성의 참고 문구가 아니라 파싱 가능한 SlideSpec 데이터이며, 후보 렌더링 전후에 같은 규칙으로 강제한다.

## 2. 모델 경계

SlideSpec 1.1은 두 종류의 제약을 분리한다.

- `layout.*.constraints`: 단일 노드의 변경 허용 범위. 잠금, 허용 연산, 최대 이동·크기 변경, 글꼴 축소 한도를 담당한다.
- `constraints`: 여러 레이아웃 노드 사이의 관계. `group`, `anchor`, `alignment`, `gap`, `containment`, `nonOverlap`, `order`, `readability`를 닫힌(discriminated) union으로 표현한다.

관계형 제약은 `constraintId`, `kind`, `strength`, `priority`, `confidence`, `source`를 공통 필드로 갖는다. `hard`는 하나라도 위반하거나 필요한 관측값이 없으면 후보를 거절하고, `soft`는 후보 순위의 위반 비용으로 사용한다. hard 제약이 soft 제약보다 항상 우선한다.

## 3. 제약 의미

| kind | 의미 | 사전 검증 | 렌더 후 검증 |
| --- | --- | --- | --- |
| `group` | 구성원의 이동 원자성과 내부 순서 보존 | 동일 이동 벡터 확인 | 실제 위치 관계 확인 |
| `anchor` | 대상 edge를 컨테이너 edge/offset에 고정 | 정규화 bounds | 실제 픽셀 bounds |
| `alignment` | 2개 이상 대상의 edge/center 정렬 | 정규화 bounds | 실제 픽셀 bounds |
| `gap` | 두 대상 사이의 최소·최대·선호 간격 | 정규화 bounds | 실제 픽셀 bounds |
| `containment` | 대상이 컨테이너와 inset 안에 존재 | 정규화 bounds | 실제 픽셀 bounds |
| `nonOverlap` | 대상 집합이 clearance를 두고 겹치지 않음 | 정규화 bounds | 실제 픽셀 bounds |
| `order` | reading/x/y/z 순서를 유지 | content/bounds | DOM bounds/z-index |
| `readability` | 글자 크기, 행간, 표시, 잘림, 대비 하한 | 가능한 항목만 | 실제 computed style/scroll/visibility |

`safeArea`는 canvas 대상 `containment`, `minSize/maxSize/aspectRatio`는 단일 노드의 절대 크기 한도와 변경량 한도, `textFit/noTruncation/visibility/contrast`는 `readability`로 표현한다. 이미지 crop과 connector는 기존 typed operation과 mutation envelope를 유지한다. authored HTML에서 결정적으로 관측할 수 없는 crop/connector 관계는 임의 추론하지 않고 잠가 두며, 전용 렌더 관측기가 추가된 뒤 품질 규칙으로 확장한다.

## 4. 실행 파이프라인

```text
authored HTML + browser telemetry
  -> conservative constraint inference
  -> sealed SlideSpec 1.1 artifact
  -> defect detection
  -> constraint-aware deterministic/LLM candidates
  -> schema + transition + pre-render geometry validation
  -> browser render
  -> DOM quality + relational invariant validation
  -> OCR + VQA non-regression gate
  -> visual quality gain + soft-constraint cost + repair cost ranking
  -> CAS publish + readback
```

검증 순서는 비용이 싼 것부터 실행한다. schema/reference/cycle 검증, repair transition, 정규화 geometry 검증에서 실패한 후보는 브라우저를 호출하지 않는다. 렌더 후 hard invariant 실패 후보는 OCR/VQA를 호출하지 않는다.

## 5. HTML 추론 정책

추론기는 현재 화면에서 확실히 관측되는 관계만 생성한다.

- 모든 편집 대상에는 canvas containment와 원본 글꼴 크기 이하로 악화되지 않는 readability를 생성한다.
- 명시적 absolute/fixed/sticky edge는 anchor 후보가 된다.
- 같은 DOM 부모를 가진 형제 중 오차가 작은 2개 이상의 edge는 alignment 후보가 된다.
- 같은 행/열에 있고 서로 겹치지 않는 형제 순서는 order와 nonOverlap 후보가 된다.
- 3개 이상의 일정한 연속 간격은 gap 후보가 된다.
- 2개 이상의 편집 자식을 가진 DOM 부모는 group 후보가 된다.
- `data-allow-overlap` 또는 의도적 crop 표시는 nonOverlap 추론에서 제외한다.

추론값은 baseline이 통과하도록 tolerance를 포함한다. 모호한 관계는 만들지 않는 것이 잘못된 hard 제약을 만드는 것보다 우선한다.

## 6. 충돌과 실패 정책

1. hard 대 hard 충돌 또는 hard cycle은 spec 자체를 거절한다.
2. hard 대 soft 충돌은 hard를 만족시키고 soft 위반 비용을 기록한다.
3. soft 대 soft 충돌은 시각 품질 비회귀를 먼저 만족한 후보 안에서 `priority * confidence * normalized violation` 합이 작은 후보를 우선한다.
4. hard 검증에 필요한 렌더 관측값이 없으면 inconclusive가 아니라 후보 거절로 fail closed 한다.
5. 통과 후보가 없으면 원본 artifact와 HTML을 유지한다.
6. publish 이후 hash/readback 불일치는 기존 CAS rollback 경로를 사용한다.

## 7. 호환성과 활성화

- `$schema` URI는 `urn:ppt-agent:slidespec:1.1`을 유지한다.
- 새 필드는 기본 빈 tuple이므로 기존 1.1 artifact는 읽을 수 있다.
- 1.0 spec에는 관계형 제약을 허용하지 않으며 digest 계산 시 1.1 기본 필드를 제외한다.
- authored importer를 `authored-html-import/2`로 올린다.
- 기존 active bundle이 v1 importer이면 현재 live HTML을 byte-identical baseline으로 다시 import하고 v2 bundle을 CAS 활성화한다. 이 마이그레이션 자체는 화면을 바꾸지 않는다.

## 8. 완료 기준

- 8개 제약의 schema/reference/conflict 테스트
- 각 hard 제약을 깨는 repair가 렌더 전에 거절되는 테스트
- 브라우저 실제 geometry가 spec 예상과 다르면 OCR/VQA 전에 거절되는 테스트
- inferred baseline은 전부 invariant 통과
- 제약을 만족하는 실제 overflow 수정은 시각 품질 비회귀 gate를 통과 가능
- 회귀 시 어떤 active HTML도 원본보다 나빠지지 않고 원본이 유지됨
