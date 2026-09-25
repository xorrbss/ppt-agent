# Compiled Spec Memory 선택 범위 정리 — 2026-09-12

## 범위와 결론

현재 대화에 주입된 15개 결정을 대상으로 원장·선택 구조를 확인하고, 충돌 징후가 있는 항목의 직접 연결 코드와 Claude 기록을 읽었다. 전체 제품 계약 재검증이나 Claude 작업 전량 이관은 아니다. 기준 HEAD는 `3bfd48da2`이며 기존 미커밋 변경을 보존한다.

원장에는 최신 키 105개가 모두 accepted로 존재했다. 기존 선택은 15개, 3153/3200자였다. 과거 P0/P1 구현 작업의 경로·scope·명시 키 6개와 항상 선택되는 core 4개가 유지되어, 최신 관련 결정도 예산 때문에 누락됐다. Claude 기록 `public-export-hybrid-decision.md`는 같은 결정이 이미 원장에 기록됐음을 명시한다. 따라서 Claude 작업이 전혀 저장되지 않았다는 가정은 맞지 않는다.

## 확인한 차이

| 기존 주입 항목 | 현재 근거 | 판정 및 처리 |
| --- | --- | --- |
| `authored.provider-attempt-budget-per-operation-attempt@1` | 원장 @2는 콘텐츠 ordinal 3개와 별도 physical replay를 정의. `authored_provider_attempt_ledger.py:69`도 `{1, 2, 3}` 허용 | 주입 스냅샷이 오래됨. 기존 원장 수정 불필요. 다음 해당 작업은 @2 선택 필요 |
| `authored.author-provider-call-budget@2` | `authored_batch_policy.py`는 프로필 기반 동시성과 과부하 후 회복을 사용. `authored_provider_rate_profile.py`는 기본 1장/호출, token/API별 동시성을 구분 | 고정 3장 이하·최대 2호출·직렬 강등 계약을 전 경로에 적용하면 부정확. 현재 작업에서 강제 선택 해제. 의도된 새 계약 확정 전 accepted 덮어쓰기 금지 |
| `authored.quality-composition-first@3` | 원장 본문은 16px/12px 고정 바닥을 명시하지만 `authored_composition_policy.py:1476`은 본문에 `capacity.min_font_px` 사용. 별도 `authored.verbatim-flagged-publish@2`는 제한된 flagged 발행 허용 | 프로필·경로별 차이 추가 확인 필요. 전체 hard gate나 숫자 바닥을 보편 규칙으로 재확정하지 않음 |
| `public-v2.fidelity-only-browser-export@1` | `PublicV2OperationsClient.tsx:288`은 `fidelity-raster`, 360행은 editable 선택 비활성화 | 브라우저 범위에서는 현재 코드와 일치. `public-export.hybrid-in-isolated-lane@1`의 워커 허용과 모순이라고 단정하지 않음. 둘 다 보존 |
| `source-content.template-restyle-contract@7` | 원문 43쪽/96개 이미지의 특정 문서 계약이 core로 항상 선택됨 | 계약 폐기 아님. 문서별 작업에서 선택해야 하므로 전역 강제 선택 해제 |

나머지 10개(`source-locked.owner-scoped-durable-generation`, `presentation.company-standard-optional`, `public-v2.browser-operation-lifecycle`, `public-export.runtime.no-docker`, `public-db.controller-authority`, `public-export.fidelity-performance-contract`, `authored.generation-stage-timing-durable`, `authored.gate-critique-fits-the-repair-contract`, `authored.layout-alignment-repair`, `authored.offcanvas-width-is-repaired-deterministically`)는 이번 조사에서 폐기 근거가 확인되지 않았다. 원장 보존은 최신 제품 동작 재검증 완료를 뜻하지 않는다. 다음 관련 작업에서 직접 근거를 확인하고 선택한다.

## 적용 방식

- 기존 제품 결정·리비전·사용자 미커밋 기록은 삭제하거나 rejected/candidate로 일괄 변경하지 않는다.
- 현재 activeTask를 기록 정합성 정리로 바꾸고 과거 구현 작업의 paths/scopes/includeKeys를 해제한다.
- 기존 제품 core 4개를 작업별 선택 대상으로 전환한다. 기록 보존·작업별 선택에 관한 사용자 승인 workflow만 core로 둔다.
- 이 문서를 readFirst로 연결한다. 다음 제품 작업 시작 시 `SetTask`로 실제 paths/scopes/includeKeys를 지정하고 `PlanBinding`으로 선택 리비전을 확인한다. 현재 정리용 binding을 제품 작업의 완전한 요구사항으로 사용하지 않는다.
- 새로 발견한 구현 차이만으로 사용자 요구사항을 변경하지 않는다. 특히 배치·품질 계약 갱신은 별도 근거 확인 대상이다.

이미 대화에 주입된 개발자 컨텍스트는 디스크 수정으로 소급 제거되지 않는다. 이후 주입에 적용되는 선택만 수정한다.

## 검증 범위

유지보수 도구의 Audit/PlanBinding으로 구조와 선택 이유를 조사했다. 변경 후 검증은 `csm.ps1 -Action Validate` 한 번으로 제한한다. 제품 코드는 수정하거나 실행하지 않는다. Audit의 missingEvidence에는 실제 누락 파일 외에도 pytest `::test_name`, 문장형 근거가 포함되어 있어 전부 계약 무효로 해석하면 안 된다.
