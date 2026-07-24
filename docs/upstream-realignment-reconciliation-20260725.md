# Upstream realignment — 문서 정합성 정리 (2026-07-25)

세션별 완료 문서를 교차 검토했을 때 표면적으로 상충해 보이는 서술 몇 가지가 있었다.
이 문서는 **역사 기록을 고쳐 쓰지 않고**, 코드로 검증한 사실을 근거로 그 서술들이 실제로
무엇을 뜻하는지 정리한다. 각 항목은 재검증 가능한 명령/파일을 함께 남긴다.

## 1. "11종 렌더러" vs "4종 편집" — 모순이 아니라 두 렌더 경로

두 개의 서로 다른 렌더 경로가 있으며, 각 문서는 그중 하나를 가리킨다.

- **General 렌더러** (`servers/nextjs/lib/template-v2-general-renderer.mjs`): 생성된 Template
  V2 덱을 렌더/`presentation-export`로 내보내는 경로. **11종 discriminator를 모두 처리**한다.
  검증: `grep -oE 'element\.type === "[^"]+"' servers/nextjs/lib/template-v2-general-renderer.mjs`
  → text, container, image, text-list, table, vector, chart, infographic, flex, grid, group.
  이는 `compatibility/upstream-compatibility.json`의 `templateV2Renderer.discriminators`와
  일치하며 verifier가 매 실행마다 강제한다.
- **Studio(Konva) MVP 편집/내보내기 경로**: `text/container/image/group` **4종만** 편집·왕복
  지원하고 나머지 7종(`text-list/table/vector/chart/infographic/flex/grid`)은 조용히 누락하지
  않고 fail-closed 한다. 근거: `docs/template-v2-followup-completion-20260724.md` 290행.

따라서 `template-v2-recommended-sequence-completion-20260724.md`의 "11종 처리"는 General 렌더러
경로를, `template-v2-followup-completion-20260724.md`의 "4/11"은 Studio 편집 경로를 말한다.
두 진술은 서로 다른 레이어를 서술한 것이며 모두 참이다.

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
