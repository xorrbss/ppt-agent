# SlideSpec-first 품질 점수표

- 기준 고정일: 2026-08-04
- 적용 범위: `C:\project\PPT-agent\ppt-agent`
- 원칙: 항목별 코드·테스트·실행 증거가 없으면 점수를 부여하지 않는다.

## 고정 평가 기준

### 검사기 100점

| 항목 | 배점 |
|---|---:|
| schema·정규화·참조 검증 | 10 |
| 혼합 제약 feasibility와 최소 unsat core | 20 |
| geometry/group/transform/containment/overlap/order 검사 | 20 |
| browser 기반 readability·contrast·clipping·occlusion 검사 | 20 |
| hard/soft 정책·unknown fail-closed·설명 가능한 오류 | 10 |
| 결정성·성능·관측성 | 10 |
| 단위/속성/통합/실문서 회귀 테스트 | 10 |

### 자동 수정기 100점

| 항목 | 배점 |
|---|---:|
| 제약 기반 deterministic plan/solver | 20 |
| HTML projection/operation coverage와 round-trip 검증 | 20 |
| 원본 비악화·mutation envelope·hard/soft 보존 | 20 |
| 후보 비교 불확실성·Pareto/minimal-change 선택 | 15 |
| 트랜잭션 publish·rollback·멱등성 | 10 |
| 실패 이유/수정 근거/관측성 | 5 |
| 단위/속성/통합/실문서 회귀 테스트 | 10 |

## 구현 전 감사 점수

| 대상 | 점수 | 주요 감점 근거 |
|---|---:|---|
| 검사기 | 64/100 | 혼합 제약의 결정적 feasibility/MUS, 실제 브라우저 glyph·line-box·합성 대비·변환 quad·occlusion, deck 일관성, 실문서 corpus가 부족했다. |
| 자동 수정기 | 66/100 | HTML projection이 move/resize/font-size 중심이었고, group 변환·round-trip·VQA 불확실성/Pareto·실문서 검증이 부족했다. |

## 최종 검사기 점수: 100/100

| 항목 | 점수 | 구현·테스트 증거 |
|---|---:|---|
| schema·정규화·참조 검증 | 10/10 | `models/slidespec_v1.py`, `utils/slidespec_constraints.py`; versioned schema, 참조, 지원 연산, mutation envelope를 정규화하고 hard unknown을 통과시키지 않는다. |
| 혼합 제약 feasibility와 최소 unsat core | 20/20 | `utils/slidespec_solver.py`; cardinality와 constraint id 사전순으로 결정적인 cardinality-minimum core를 찾는다. 4096회 기본 oracle 예산이 소진되면 이미 증명한 inclusion-minimal core를 `inclusion-minimal-fallback`으로 반환하며 `core_search_complete=false`를 명시한다. feasibility unknown은 core로 주장하지 않는다. |
| geometry/group/transform/containment/overlap/order 검사 | 20/20 | transformed polygon, containment/nonOverlap/order, group translation·scale·rotation, connector geometry와 mutation envelope를 동일 계약에서 검사한다. |
| browser 기반 readability·contrast·clipping·occlusion 검사 | 20/20 | `utils/slide_capture.py`, `utils/authored_dom_quality.py`; Chrome paired-frame 픽셀 glyph 대비, adaptive 40×40 연속 occlusion 영역, Range fragments, `getBoxQuads`/DOMMatrix, filter/backdrop/blend/mask/clip 정보를 실제 렌더에서 수집한다. 측정 불능 hard 항목은 null/unknown으로 fail closed 한다. |
| hard/soft 정책·unknown fail-closed·설명 가능한 오류 | 10/10 | constraint id·phase·reason·metric과 projection rejection을 구조화하고, hard failure/measurement unavailable/search-limited는 원본 유지로 종료한다. |
| 결정성·성능·관측성 | 10/10 | 안정 정렬, best-first nonOverlap lattice, projection norm 사전 계산, feasibility/optimality 분리를 구현했다. solver 시간·branch·unknown, 후보 funnel, rollback, kill switch를 canonical metrics와 dashboard에 노출한다. |
| 단위/속성/통합/실문서 회귀 테스트 | 10/10 | 다국어 표, SVG 차트, 조직도, 복잡 이미지 focal/crop을 포함한 비식별 production-shaped 7종 corpus를 baseline→repair→round-trip→fresh Chrome post-render로 검증한다. |

## 최종 자동 수정기 점수: 100/100

| 항목 | 점수 | 구현·테스트 증거 |
|---|---:|---|
| 제약 기반 deterministic plan/solver | 20/20 | 혼합 제약 해를 안정 정렬된 RepairPlan으로 만들고, bounded feasible witness와 minimum-change optimality를 분리한다. `optimality_proven=false`이면 자동 적용하지 않는다. |
| HTML projection/operation coverage와 round-trip 검증 | 20/20 | bounds/font/gap/padding/flex/grid/tracks/line-height/text-fit/image-fit에 더해 connector 안전 재배선, Chrome glyph 기반 shrink-to-fit, crop/focal point, 증명된 static translation을 지원한다. 각 연산은 mutation envelope, Chrome telemetry, 허용 속성 round-trip을 모두 요구한다. |
| 원본 비악화·mutation envelope·hard/soft 보존 | 20/20 | baseline/candidate hard 위반, soft cost, DOM 품질, canonical digest, envelope, VQA non-regression을 acceptance gate에서 검증한다. 증명 불가 후보는 구조화된 fail-closed rejection이다. |
| 후보 비교 불확실성·Pareto/minimal-change 선택 | 15/15 | VQA confidence interval 중첩을 동등성으로 처리하고 hard feasibility, soft cost, 실현 mutation cost, quality, plan id 순으로 결정적인 Pareto/minimal-change 선택을 한다. |
| 트랜잭션 publish·rollback·멱등성 | 10/10 | CAS publish, stale revision 거부, active-pointer read-back, 원본 rollback, 재실행 결정성/멱등성을 보존한다. |
| 실패 이유/수정 근거/관측성 | 5/5 | solver/core/projection/round-trip/rejection/rollback/kill-switch 증거를 API 결과에 합산하고 UI에 노출한다. |
| 단위/속성/통합/실문서 회귀 테스트 | 10/10 | 단위·속성·통합·실제 Chrome golden과 전체 백엔드/프런트 회귀가 모두 통과했다. |

## 재현 벤치마크

`scripts/slidespec_solver_benchmark.py --cases 48 --seed 20260804`:

| 지표 | 결과 |
|---|---:|
| duration p50 / p95 / p99 | 1.879 / 111.361 / 116.776 ms |
| branch p50 / p95 / p99 | 1 / 2 / 2 |
| total branches | 56 |
| feasible / unknown | 100% / 0% |
| multi-branch / bounded-optimality | 16.6667% / 50% |

고정 seed 성능 회귀는 p50 `<50 ms`, p95 `<400 ms`, p99 `<700 ms`, branch p95/p99 `<=4`, unknown `=0`을 강제한다.

## 최종 회귀 증거

- FastAPI 전체: **2332 passed, 16 skipped**, 270.90초.
- SlideSpec top-level 단위·통합: **105 passed**, 23.83초.
- 실제 Chrome golden/browser/page11: **15 passed**, 17.31초.
- Next.js production build/TypeScript: 통과, `/dashboard` 포함 31 routes.
- ESLint: 통과.
- Authored quality component Cypress: **6 passed**.
- chat Cypress: **5 passed**.
- Node/Chrome CI: **277 passed**.
- `git diff --check`: 오류 없음. 표시된 메시지는 기존 Windows line-ending 변환 예고뿐이다.

## 활성화·복구·운영 증거

- 백엔드 기본 정책과 현재 로컬 런타임: canary 100%, apply=true, all-slides=true, shadow=false, kill switch=false.
- 고정 presentation/slide 입력은 `route=slidespec_active`, `canary_eligible=true`, `apply_enabled=true`, `kill_switch_active=false`다.
- 동일 입력에 `SLIDESPEC_KILL_SWITCH=true`를 주입하면 `route=legacy`, `reason_code=system.kill_switch_enabled`로 즉시 전환된다.
- 3000은 Next standalone production, 5000은 repository local proxy, 8000은 FastAPI다. frontend/proxy `/dashboard`와 direct/proxy `/api/v1/auth/status`가 HTTP 200이다. 보호된 direct `/openapi.json`은 인증 없이 의도대로 HTTP 401이다.
- Windows `userConfig.json`은 protected DACL에서 현재 사용자와 SYSTEM만 허용되는지 정확히 검증한다. 관리자 소유로 `icacls` 재적용이 거부되어도 동일한 private ACL이면 안전하게 수용하고, Everyone/상속/예상 밖 ACE는 fail closed 한다. 재기동 로그에 ACL 경고가 없다.
- 별도 임시 DB에서 empty→head→직전 revision→head upgrade/downgrade/re-upgrade를 완료했고 live DB는 변경하지 않았다.

## 지원 경계

- cardinality core 예산이 소진되면 최소 cardinality라고 과장하지 않고 검증된 inclusion-minimal fallback과 incomplete 상태를 반환한다.
- 복잡한 외부 이미지/폰트, 애니메이션, canvas/SVG path처럼 deterministic Chrome 측정을 보장하지 못하는 입력은 hard 항목에서 fail closed 한다.
- connector/glyph/crop/static 이동은 안전 증명과 round-trip 계약을 만족하는 범위만 적용한다. 임의의 imported connector topology나 flow 영향이 큰 static 요소는 원본을 유지한다.
- corpus는 실제 고객 원문이 아니라 비식별 production-shaped fixture다. 고객 데이터 반입 없이 회귀 가능하도록 의도한 경계다.

세부 실행·서비스·migration·작업트리 증거는 `docs/slidespec-operations-evidence-20260804.md`에 기록한다.

## 2026-08-05 실문서 운영 품질 재평가

이 절은 위의 production-shaped corpus 점수와 별개로, presentation
`15f91f1d-66d7-491d-abbb-c02481358b96`의 실제 90장에 publish한 결과를 평가한다.

| 신뢰도 항목 | 배점 | 실측 | 점수 |
|---|---:|---|---:|
| 23건 사람 라벨과 결과 완결성 | 25 | true positive 5건, detector false positive/intentional overlap 18건을 fixture에 고정했고 23/23 상태가 결정적이다. | 25 |
| 실제 수정 lifecycle | 25 | 5/5가 후보 생성, acceptance, CAS publish, active read-back, fresh Chrome post-render를 통과했고 target constraint delta가 모두 0이 됐다. | 25 |
| 원본·정상 슬라이드 비악화 | 20 | authored source 변경은 9·19·26·47·72번뿐이다. 정상 67장은 67/67, false-positive 18장은 18/18 byte-identical이고 hard/soft regression은 0이다. | 20 |
| 동일 viewport 시각 검증 | 20 | 1280×720 before/after 5쌍을 직접 대조해 겹침·잘림 해소와 계층·가독성·프레임 보존을 확인했다. 독립된 외부 사람 sign-off는 없으므로 2점을 유보한다. | 18 |
| 재현성·운영 검증 | 10 | 23장 10회에서 230/230 상태가 동일했고, 90장 10회 dry-run에서 source unchanged, remaining 0, solver unknown 0, LLM 0이었다. | 10 |
| 합계 | 100 | 실문서 품질 신뢰도 | **98/100** |

실제 적용 재생은 `release_passed=true`, verified true positive 5, corrected false positive
18, unresolved 0이다. 적용 뒤 90장 전체 dry-run도 `issue_slide_count=0`과
`remaining_issue_count=0`을 반환했다.

최종 전체 회귀는 FastAPI **2437 passed, 16 skipped**(exit 0), Next Node/Chrome
CI **277 passed**, 관련 Cypress **8 passed**, ESLint와 Next production build/TypeScript
통과다. 90장 cold 1회와 warm 9회의 deck wall time은 cold **113.734초**,
warm p50/p95/p99 **11.291/12.472/12.725초**로 운영 목표를 충족했다.

## 2026-08-05 scoped evidence 결함 재평가

새 presentation `50907f60-ea13-404c-90da-57f821404d74`의 5번 슬라이드는
이전 task에서 `issue0/clean`으로 잘못 완료됐지만, 수정 뒤 historic exact report가
`confirmed/incomplete/unresolved/clean = 1/1/1/0`으로 재생된다. fresh sensor v4는
footer 샘플을 확보해 `1/0/1/0`이며, 3개 nested raw finding은 증거를 보존한 채
물리 이슈 1개로 deduplicate된다.

자동수정 후보 9개는 모두 검증 거부됐다. 물리 owner 이동에 필요한 73.65625px보다
가용 공간 37.359375px(상위 section은 59.359375px)이 작고 footer 경계 y=690을
침범하므로 slide 5의 apply/publish/post-verify는 실행하지 않았다. 이는 generic
unsupported가 아니라 qid/constraint/operation/stage/metric과
`repair.geometry.ancestor.physical_owner_no_safe_translation`으로 기록된다.

실제 Chrome 20장 검수 task는 실행 당시 confirmed 21, incomplete 77,
unresolved 20, clean 8을 표시했다. 별개의 안전 후보인 6번 슬라이드 1건만
apply/CAS publish/post-verify에 성공했고 rollback은 0이다. 최종 live 재검수는
그 해결을 반영해 confirmed/incomplete/unresolved/clean `20/77/20/9`이며,
5번은 detected·remaining으로 남고 clean으로 표시되지 않는다.

| 신뢰도 항목 | 배점 | 실측 | 점수 |
|---|---:|---|---:|
| proven issue 보존·physical dedupe | 25 | unrelated unknown이 overlap proof를 지우지 않고 exact 1/1/1/0 재현 | 25 |
| scoped fail-closed·mutation safety | 25 | dependency cone, capacity, containment 근거로 slide 5 publish 차단 | 25 |
| fresh Chrome·round-trip·UI lifecycle | 20 | baseline, 후보 9개, fresh post-render, 실제 20장 task와 상세 blocker UI 확인 | 20 |
| 기존 90장 corpus 보존 | 20 | human-labeled release pass: corrected 18, verified 5, pending 0, source 90/90 unchanged | 20 |
| 독립 외부 human sign-off | 10 | 동일 viewport의 운영자 검증은 완료, 독립 외부 sign-off는 미수행 | 8 |
| 합계 | 100 | 파이프라인 품질 신뢰도(문서 자체의 무결함 점수가 아님) | **98/100** |

최종 live 90장 dry-run은 cold **266.461초**, warm p50/p95/p99
**79.604/111.930/114.803초**로 warm p95 210초 운영 SLO 안이다. cold에는
실패한 model call 7회와 OCR/VQA 67.537초가 포함되어 숨기지 않았다. 새 20장은
cold **61.520초**, warm **18.048/20.892/21.144초**이고, 5번 집중 replay는
cold **27.836초**, warm **8.144/8.338/8.356초**다. 원시 sensor finding과
human-reviewed disposition은 서로 다른 지표로 보존하며, 원시 unknown을 clean이나
confirmed defect로 임의 변환하지 않는다.

최종 human-labeled 23건 replay는 `release_passed=true`, corrected 18,
executor-verified 5, pending 0, source 90/90 unchanged를 다시 확인했다. 전체
FastAPI는 **2468 passed, 16 skipped**(exit 0), 관련 Cypress는 **9 passed**,
Node/Chrome CI는 **277 passed**이며 ESLint와 Next production build도 통과했다.

slide 6 publish 뒤 presentation-version 복구 흔적으로 20개 active pointer의
bundle row가 없는 상태를 발견했다. SQLite online backup 뒤 표준 CAS re-import로
20/20 bundle을 복구했고 HTML/content hash는 전부 동일했다. historic fixture의
원래 bundle ID는 보존하며, 현재 live slide 5 bundle은
`e73d7e98-a7c1-4528-a9e6-e2b86fa1576c`이다.
