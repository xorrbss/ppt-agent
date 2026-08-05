# SlideSpec 실문서 자동 수정 품질·속도 개선 분석

- 작성일: 2026-08-05 (Asia/Seoul)
- 기준 실행: `task-5ca0dcf19aa27600ddf87b28f921e79dbea78520ce489af65dfad45dbe882459`
- 대상 프레젠테이션: `15f91f1d-66d7-491d-abbb-c02481358b96`
- 대상 규모: 90장, 문제 감지 23장
- 선행 문서: `docs/slidespec-production-repair-priority-report-20260805.md`

## 1. 요약 판정

선행 리포트대로 구현하면 가장 먼저 개선되는 것은 검사 점수가 아니라 **실제 수정 성공률**이다.

현재 실문서 자동 수정 성과는 품질 27/100, 속도 22/100, 종합 26/100으로 평가한다. 코드·회귀 범위를 평가한 기존 점수표의 검사기/자동 수정기 100/100과 모순되지 않는다. 기존 점수는 기능과 안전장치의 존재를 평가한 정적 점수이고, 여기의 점수는 이번 90장 문서에서 실제로 고친 결과를 평가한 운영 점수다.

선행 리포트의 1차 release gate인 후보 커버리지 90% 이상, 실제 적용 18장 이상을 달성하면 다음 수준을 예상한다.

| 구분 | 현재 실측 | 1차 목표 예상 | 최종 목표 예상 |
|---|---:|---:|---:|
| 후보가 있는 문제 슬라이드 | 5/23, 21.7% | 21/23 이상, 91.3% 이상 | 23/23, 100% |
| 적용·post-verify 완료 | 0/23, 0% | 18/23 이상, 78.3% 이상 | 23/23, 100% |
| 남은 문제 | 23장 | 5장 이하 | 0장 |
| 실문서 품질 점수 | 27/100 | 약 87/100 | 기계 점수 100, 신뢰도 보정 95/100 |
| 90장 총 처리 시간 | 806.8초 | 240~360초 | 150~210초 |
| 속도 개선 | 기준 | 55~70% 단축, 2.2~3.4배 | 74~81% 단축, 3.8~5.4배 |
| 속도 점수 | 22/100 | 50~75/100 | 86~100/100 |
| 품질 70%+속도 30% 종합 | 26/100 | 76~83/100 | 92~97/100 |

1차 및 최종 수치는 아직 실측 결과가 아니라 현재 단계별 실행 시간과 release gate를 이용한 추정치다. 동일 90장 fixture의 baseline→repair→round-trip→post-render 재실행을 통과하기 전에는 달성값으로 보고하면 안 된다.

## 2. 품질 점수 산정

실제 자동 수정의 가치를 반영하기 위해 다음 100점 기준을 사용한다.

| 항목 | 배점 | 현재 | 산정 근거 |
|---|---:|---:|---|
| 검사 경로와 문제 대상화 | 15 | 15 | 기존 90장을 모두 검사했고 23장을 문제 대상으로 만들었다. 단, 정답 corpus가 없어 탐지 정확도는 별도 검증이 필요하다. |
| 수정 후보 슬라이드 커버리지 | 20 | 4.3 | `20 × 5/23` |
| 실제 채택·적용률 | 35 | 0 | `35 × 0/23` |
| 적용 후 비악화·post-render 검증 | 20 | 5 | 적용 성공은 없지만 fail-closed로 원본을 보존한 안전성 기본점수만 반영했다. |
| 실패 원인의 실행 가능성 | 10 | 3 | 18장이 후보 없이 종료됐고 다수가 generic `required_gate_failed`로만 요약됐다. |
| 합계 | 100 | **27.3** | 반올림 27점 |

### 1차 목표 달성 시

- 후보 21장: `20 × 21/23 = 18.3점`
- 실제 적용 18장: `35 × 18/23 = 27.4점`
- 18장 모두 post-verify: `5 + 15 × 18/23 = 16.7점`
- 문제 대상화 15점, 구조화된 실패 원인 10점
- 합계: **87.4점**, 반올림 87점

즉, 1차 계획은 실문서 품질 점수를 약 60점 높이고 실제 수정률을 0%에서 최소 78.3%까지 끌어올리는 계획이다.

### 최종 목표 달성 시

23장 모두 후보 생성·적용·post-verify가 끝나면 이 산식의 기계 점수는 100점이다. 다만 아래 두 증거가 없으므로 운영 신뢰도 점수는 우선 95점으로 제한하는 것이 안전하다.

1. 사람이 정답을 표시한 실문서에서 23건이 실제 결함인지 확인한 precision/recall 증거
2. 자동 수정 전후를 동일 viewport에서 사람이 표본 확인한 시각 품질 증거

## 3. 속도 개선 분석

현재 실측 총 시간은 806.812초다.

| 단계 | 실측 | 전체 비중 | 판정 |
|---|---:|---:|---|
| LLM proposal | 618.111초 | 76.6% | 23회 호출, 후보 0개로 현재 가장 큰 낭비 |
| LLM 이외 전체 | 188.701초 | 23.4% | 검사·DOM·schema·Chrome 등 |
| solver | 1.488초 | 0.2% 미만 | 이번 실문서 지연의 병목이 아님 |
| 후보 Chrome render | 6.991초 / 5개 | 후보당 약 1.4초 | 후보 수 확대 시 증가할 비용 |

### 1차 속도 예상: 4~6분

projection 불가와 mutation envelope 불가를 LLM 전에 판정하고 후보 0개 응답을 negative cache하면 최대 618.1초를 제거할 수 있다. LLM 비용을 단순 제거한 이론값은 188.7초, 약 3분 9초다.

그러나 후보 커버리지를 5장에서 최소 21장으로 확대하면 Chrome render, DOM 검사, acceptance, post-render 비용이 증가한다. 현재 후보당 render 약 1.4초를 기준으로 다중 대안과 브라우저 경합까지 포함하면 240~360초가 현실적인 1차 범위다.

- 806.8→360초: 55.4% 단축, 2.24배
- 806.8→240초: 70.3% 단축, 3.36배

### 최종 속도 예상: 2분 30초~3분 30초

baseline telemetry digest cache, 변경 슬라이드만 재검사, 브라우저 context 재사용, bounded Chrome 병렬 검증까지 적용하면 150~210초를 목표로 할 수 있다.

- 806.8→210초: 74.0% 단축, 3.84배
- 806.8→150초: 81.4% 단축, 5.38배

속도 점수는 90장 전체를 180초 안에 처리하는 것을 100점으로 두고 `min(100, 180/실행초×100)`으로 계산했다. 따라서 현재는 22점이고, 240~360초는 50~75점, 150~210초는 86~100점이다.

## 4. 선행 리포트 외에 추가해야 할 개선

### P0. 실제 수정 품질을 보증하는 항목

1. **결함 해소 delta gate**
   - 비악화만 확인하지 말고 후보가 목표로 한 원래 결함이 실제로 사라졌는지를 동일 element/constraint ID로 증명해야 한다.
   - `target_issue_resolved=true`, before/after metric, 허용 오차를 acceptance 결과에 남긴다.

2. **23장 human-labeled fixture**
   - 각 결함을 `실제 결함`, `허용 가능한 디자인`, `자동 수정 가능`, `사용자 판단 필요`로 분류한다.
   - false positive를 고친 것처럼 만드는 위험을 막고 품질 점수의 95점 상한을 해제하는 핵심 증거다.

3. **슬라이드별 repairability 계약**
   - `auto_repairable`, `preview_required`, `unsupported`를 요소와 연산 단위로 반환한다.
   - 자동 수정이 안전하지 않은 슬라이드는 단순 실패가 아니라 사용자 승인형 preview 후보로 분리한다.

4. **UI before/after 증거**
   - 수정 0건인데 검수 완료로 보이는 문제를 막기 위해 감지, 후보, 거절, 적용, 남은 문제를 분리한다.
   - 적용된 슬라이드는 before/after 이미지와 변경 속성, 되돌리기를 제공한다.

### P1. 속도와 비용을 함께 줄이는 항목

1. **SlideSpec proposal negative cache**
   - 일반 critique/browser cache는 이미 있지만 현재 SlideSpec proposal 단계에는 동일 digest의 후보 0개 결과를 재사용하는 경로가 보이지 않는다.
   - `spec digest + DOM evidence digest + projection capability version + model version`을 키로 사용한다.

2. **증분 재검수**
   - 전체 90장을 매번 다시 검사하지 말고 source/content/font/browser-version digest가 바뀐 슬라이드와 인접 deck-consistency 영향 범위만 재검사한다.
   - 동일 문서 재실행에서 가장 큰 추가 단축 수단이다.

3. **Chrome 검증의 제한 병렬화**
   - 현재 후보 루프는 정렬된 후보를 순차 시도한다. 브라우저 메모리 한도를 둔 2~4개 worker로 독립 슬라이드를 병렬화한다.
   - 같은 슬라이드의 후보는 결정적 순서를 보존하고 최초 확정 후 나머지를 취소한다.

4. **Chrome 전 Pareto pruning**
   - 동일 결함을 해결하면서 이동량, mutation cost, 예상 soft cost가 모두 열등한 후보는 render 전에 제거한다.
   - 후보 커버리지 확대로 늘어나는 Chrome 비용을 상쇄한다.

5. **단계별 timeout budget**
   - 슬라이드와 전체 deck에 deterministic/Chrome/LLM 예산을 분리한다.
   - 예산 소진은 `unknown`이나 generic 실패가 아니라 어느 단계가 얼마를 사용했는지 반환한다.

### P1. 운영 신뢰도를 높이는 항목

1. **실문서 전체 파이프라인 p50/p95/p99**
   - 현재 재현 benchmark는 solver 중심이고 이번 806.8초는 단일 실행이다.
   - 10회 이상 warm/cold 재생으로 deck total, proposal, render, acceptance, postverify의 p50/p95/p99를 별도로 고정한다.

2. **수정 성공 SLO**
   - 후보 커버리지 ≥90%, 적용률 ≥78%, post-verify 성공률 100%, hard regression 0, 90장 p95 ≤360초를 첫 SLO로 둔다.
   - 안정화 후 적용률 ≥90%, p95 ≤210초로 상향한다.

3. **부분 진행 저장과 재개**
   - 슬라이드별 검증 결과를 source digest에 묶어 checkpoint한다.
   - 작업 취소·프로세스 재시작·CAS 충돌 시 검증이 끝난 불변 결과는 재사용하되 문서 반영은 기존 CAS/postverify 계약을 유지한다.

4. **비용 관측성**
   - 호출 수뿐 아니라 모델별 token, 비용, timeout, empty/invalid patch, cache hit를 대시보드에 표시한다.

## 5. 권장 구현 순서

1. 23장 human-labeled fixture와 결함 해소 delta gate
2. 후보 없는 18장의 원인 구조화와 일반 geometry 다중 후보
3. projection preflight, LLM short-circuit, SlideSpec negative cache
4. static translation/containment와 round-trip 확장
5. 후보 9건의 readability/soft-regression/round-trip 실패 해결
6. UI의 검사·후보·거절·적용·잔여 문제 분리와 before/after
7. 증분 재검수, Chrome Pareto pruning, bounded 병렬화
8. 동일 90장 10회 benchmark와 전체 회귀

이 순서의 핵심은 안전 gate를 완화하지 않고, 먼저 실제 결함이 맞는지 고정한 뒤 후보 생성과 projection 정확도를 높이는 것이다.

## 6. 완료 판정

다음 증거가 모두 있어야 예상치가 실제 개선으로 전환됐다고 판정한다.

- 동일 90장 입력에서 후보 커버리지, applied, remaining, post-verify 실측
- 23장 각각의 before/after constraint metric과 Chrome 캡처
- 정상 67장 hard/soft 비악화
- 동일 입력 2회차에서 멱등성 및 negative/incremental cache hit
- 10회 실행 total/stage p50/p95/p99와 cold/warm 구분
- FastAPI 전체, SlideSpec, 실제 Chrome, Next build/lint/test 통과
- CAS conflict, kill switch, rollback 재검증

이 기준 전에는 “약 87점, 4~6분”은 계획상 예상치일 뿐 완료 실적이 아니다.

## 7. 최종 실측 결과 (2026-08-05)

위 절의 수치는 구현 전 분석이다. 아래 결과가 같은 presentation의 최종 운영 증거이며
예상치를 대체한다.

### 품질 결과

- 사람 라벨 23건: true positive 5건(9, 19, 26, 47, 72), corrected false
  positive/intentional overlap 18건.
- true positive 5/5: candidate, acceptance, 실제 apply, active publish, fresh Chrome
  post-render, target issue delta gate 통과.
- `remaining_issue_count=0`; generic unsupported나 required-gate-only 잔여 없음.
- authored source 변경 위치는 정확히 9, 19, 26, 47, 72번이다. 정상 67장은
  67/67, false-positive 18장은 18/18 byte-identical이다.
- 동일 1280×720 before/after 5쌍을 직접 검토했고 새 hard/soft regression은 0이다.
- 독립 외부 sign-off 2점을 유보한 실문서 품질 신뢰도는 **98/100**이다.

### 23건 evidence replay 10회

| 구분 | p50 | p95 | p99 | 표본 |
|---|---:|---:|---:|---:|
| cold deck total | 57.151초 | 57.151초 | 57.151초 | 1 |
| warm deck total | 57.147초 | 87.917초 | 94.786초 | 9 |

230개 lifecycle 판정은 corrected false positive 180개와 canonical executor verified
50개로 모두 동일했다. 이 경로는 각 회차마다 23장의 baseline/post-render를 독립 Chrome
프로세스로 다시 캡처하는 보수적 evidence replay다.

### 90장 전체 파이프라인 10회

| 구분 | p50 | p95 | p99 | 표본 |
|---|---:|---:|---:|---:|
| cold deck wall | 113.734초 | 113.734초 | 113.734초 | 1 |
| warm deck wall | 11.291초 | 12.472초 | 12.725초 | 9 |

cold는 inspection miss 90, render request 90, Chrome process launch 1, session reuse
89, solver 3.284초/branch 90/unknown 0이었다. warm은 inspection cache hit 810,
추가 render와 Chrome launch 0이었다. 두 phase 모두 LLM call/candidate 0,
remaining 0, source unchanged다. 따라서 cold p95 360초와 warm p95 210초 SLO를
충족한다.

### 검증과 운영

- FastAPI 전체: 2437 passed, 16 skipped, exit 0, 324.63초.
- Next Node/Chrome CI: exit 0, 최종 serial 집계 277/277.
- AuthoredQualityReview Cypress: 8/8. ESLint와 Next production build/TypeScript 통과.
- migration rehearsal: `fc3d5e7f9a1b → fb2c4d6e8a0b → fc3d5e7f9a1b`.
- 활성 서비스: 3000 PID 36160, 5000 PID 44300, 8000 PID 39912. frontend/proxy
  `/dashboard`, direct/proxy `/api/v1/auth/status` HTTP 200.
- 기본 rollout은 `slidespec_active`, publisher allowed이고, kill switch는 즉시
  `legacy/system.kill_switch_enabled`로 전환한다.
