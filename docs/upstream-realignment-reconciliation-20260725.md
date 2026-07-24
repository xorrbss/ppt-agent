# Upstream realignment — 문서 정합성 정리 (2026-07-25)

세션별 완료 문서를 교차 검토했을 때 표면적으로 상충해 보이는 서술 몇 가지가 있었다.
이 문서는 **역사 기록을 고쳐 쓰지 않고**, 코드로 검증한 사실을 근거로 그 서술들이 실제로
무엇을 뜻하는지 정리한다. 각 항목은 재검증 가능한 명령/파일을 함께 남긴다.

## 1. "11종 렌더러" vs "4종 편집" — 모순이 아니라 두 렌더 경로

두 개의 서로 다른 렌더 경로가 있으며, 각 문서는 그중 하나를 가리킨다. 아래 근거는
discriminator 문자열 grep이 아니라 **구현 본문·테스트·커밋 타임라인**으로 검증했다
(2026-07-25 재검증).

- **General 렌더러** (`servers/nextjs/lib/template-v2-general-renderer.mjs`): 생성된 Template
  V2 덱을 렌더/`presentation-export`로 내보내는 경로. **11종 모두 실제 구현이다.**
  - 511–521행의 11개 분기가 각각 실질 구현 함수로 위임한다: renderText(114행),
    renderContainer(141), renderImage(177), renderTextList(217), renderTable(252),
    renderVector(267), renderPlannedChart(298–462), renderInfographic(463), renderFlex(482),
    renderGrid(491), renderGroup(502).
  - 코드 내 `throw`는 전부 입력 데이터 검증이며 타입 차단이 아니다.
    `template_v2_renderer_unsupported_element`(522행)는 11종 밖의 미지 타입에만 발동한다.
  - 상위 plan 레이어의 허용 목록(`template-v2-render-plan.mjs:3-15`,
    `TEMPLATE_V2_PLAN_ELEMENT_TYPES`)도 동일한 11종이고, 그 레이어의 "unsupported"는 하위
    속성 수준(특정 chart 타입, image clip path, stroke dash 등)이다.
  - 전용 테스트 "renderer covers every strict Template V2 element discriminator"가 11종을
    fixture로 행사한다(`template-v2-general-renderer.test.mjs`, 로컬 5/5 통과 확인).
- **Studio(Konva) 편집 캔버스**
  (`app/template-v2-studio/[templateId]/TemplateV2CanvasElement.tsx`, 원래 문서 작성 시점에는
  `TemplateV2Canvas.tsx`에 있었으나 Phase 2A에서 element 렌더링 모듈로 분리됨): 처음에는
  `text/container/image/group` **4종만** 인터랙티브 편집을 지원했다. **Phase 2A 증분 1**에서
  `text-list/table/infographic` 3종을, **증분 2**에서 `vector/chart/flex/grid` 4종을
  추가해 현재 **11종 전부 캔버스에 렌더링**된다. 증분 2는 export render plan을 요소 단위로
  재사용(`lib/template-v2-studio-plan.ts`)해 vector 지오메트리·차트 데이터·flex/grid 자식
  배치를 export와 동일한 계산으로 그린다. 편집 능력은 차등: 10종은 선택·이동·크기조정·회전
  가능(flex/grid 자식은 배치가 플랜 소산이므로 비인터랙티브), vector는 지오메트리가
  position/size가 아닌 points에 있어 커밋 경로에 point 변환이 생기기 전까지 선택만 가능.
  내부 콘텐츠 편집(인라인 텍스트 등)은 이후 워크스트림. 즉 "4종"은 Phase 2A 이전의
  스냅샷이다.

**타임라인이 "시점 차이" 가설을 배제한다.** 렌더러 파일은 최초 커밋 `50300fed`
(2026-07-24 21:00)부터 11종으로 태어났고 이후 수정 이력이 없다. "11종"과 "4/11"을 각각
주장한 두 문서는 그 3분 뒤 `7c89416d`(21:03)로 **함께** 커밋됐다. 즉 "렌더러가 4종이었다가
나중에 확장됐다"는 해석은 성립하지 않으며, 두 문서는 같은 트리 상태의 서로 다른 레이어를
서술한 것이다.

**단, 원 문서 표현 하나는 부정확하다.** `template-v2-followup-completion-20260724.md` 290행의
"실제 **Studio/export renderer**는 4종만 지원" 중 "export renderer" 부분은 사실과 다르다 —
그 시점에 export 렌더러(General)는 이미 11종 구현+테스트 완료 상태였다. 그 문장에서 살릴 수
있는 취지는 (a) Studio 편집이 4종이라는 것, (b) E2E golden test로 **실증된** export 범위가
당시 제목/본문/도형/텍스트 수준으로 좁았다는 것이며, "광범위 활성화 전 타입별 golden test
필요"라는 권고 자체는 여전히 유효하다.

## 2. 마이그레이션 계보 — rebase가 아니라 단일 additive 체인

세션마다 서로 다른 head(`a4b5c6d7e8f9`, `e8f9a0b1c2d3`, `1b2c3d4e5f6a` 등)를 언급해 ID 체계가
바뀐 것처럼 보이지만, 실제로는 **하나의 연속된 additive 체인**이고 각 문서는 그 시점의 head를
적었을 뿐이다. rebase나 rename은 없었다.

- 현재 실체: 로컬 마이그레이션 17개, 단일 head `1b2c3d4e5f6a`.
- 과거 문서가 언급한 `a4b5c6d7e8f9`(Phase 1), `e8f9a0b1c2d3`(followup)는 지금도 최종 체인의
  **중간 노드**로 그대로 존재한다. 즉 각 세션은 앞 체인에 리비전을 덧붙였을 뿐이다.
- 권위 있는 출처는 산문 스냅샷이 아니라 `compatibility/migration-translation-ledger.json`이며,
  `scripts/verify-upstream-compatibility.mjs`가 실제 alembic 파일 헤더와 대조해 단일 head·전체
  조상 체인 일치를 강제한다(현재 통과).

## 3. "Vision" 명칭 — 실제로는 결정론적 OOXML 분석

`template-v2-phase2-structured-vision-mvp-20260725.md`의 "structured/Vision MVP"는 이름과 달리
**OCR·컴퓨터비전·LLM 추론을 수행하지 않는다.** provider-neutral 한 결정론적 OOXML 정적 분석
(`deterministic-ooxml-static`)과 반복 블록 제안 + 명시적 사용자 확정 흐름만 제공한다. 문서 본문도
"production OCR/CV/rendered-preview fidelity를 주장하지 않는다"고 명시한다. 실제 vision(렌더 기반
지오메트리, OCR/분류, confidence)은 별도 프로덕션 단계로 이월된 상태다. 이후 문서/이슈에서는
capability를 "deterministic OOXML analysis"로 부르는 것이 정확하다.

## 4. 점수 스케일 — 비교 전 범위(scope) 확인 필요

세션별 점수는 스케일과 대상이 달라 단순 비교하면 안 된다. 원 문서의 값을 범위와 함께 남긴다.

| 세션/문서 | 값 | 대상(scope) |
| --- | --- | --- |
| Phase 1 완료 | 72/100 · 91/100 | 제품 전체 · Phase 1 계약 |
| Followup 완료 | 91 / 96 / 95 | 완성도 / 안전성 / 테스트 신뢰도(각 100점) |
| Recommended sequence | 95/100 | 해당 세션 범위 |
| Konva 세션 5 | 91.2/100 | 제한된 MVP readiness |
| Phase 2 vision | 9.1/10 | 제한된 MVP 품질 |

"제품 완성도"를 하나의 정규화된 수치로 연속 추적한 지표는 아직 없다. 세션 간 진척은 위 범위를
분리해 읽어야 한다.

## 5. 세션 1–2 감사 문서 위치

기준선 감사(세션 1–2)는 이 브랜치가 아니라 `upstream-realignment` 워크트리의
`docs/upstream-realignment-20260724.md`에 있다(브랜치 `integration/upstream-realignment-20260724`).
이 브랜치(`feat/pptx-template-studio` 계열) docs에는 포함되지 않는다.
