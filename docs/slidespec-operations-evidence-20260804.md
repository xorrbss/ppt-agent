# SlideSpec 고도화 운영·회귀 증거

- 실행일: 2026-08-04
- workspace: `C:\project\PPT-agent\ppt-agent`
- 기준 commit: `880d630b73ce7b1a9debd9159023c7ac104d665b`

## 병렬 담당과 통합 결과

| 담당 | 모델/수준 | 배타적 소유 범위 | 결과 |
|---|---|---|---|
| solver_core_perf | gpt-5.6-sol high | `utils/slidespec_solver.py`, solver/perf tests, benchmark script, solver evidence doc | cardinality-minimum core, bounded inclusion-minimal fallback, best-first large nonOverlap, 고정 seed 임계값 |
| chrome_pixel_metrics | gpt-5.6-sol high | `utils/slide_capture.py`, `utils/authored_dom_quality.py`, browser metrics integration test | paired pixel glyph contrast, adaptive continuous occlusion, Range/quad/filter/mask 측정과 hard fail-closed |
| safe_projection_repair | gpt-5.6-sol high | `services/slidespec_repair.py`, `services/slidespec_repair_pipeline.py`, safe projection tests | connector/glyph/image/static 연산의 mutation-envelope·measurement·round-trip 증명과 구조화 rejection |
| corpus_observability | gpt-5.6-terra high | golden fixture/test, review metrics merge/test, Next API normalization, dashboard component/test | 7종 corpus, canonical solver/candidate/rollback/kill-switch metrics와 UI |
| root 통합 | primary | schema/importer/executor/core/patch engine/Chrome connector probe, ACL, service runtime, full regression/docs | 단일 solve 재사용, native connector round-trip, static proof, 정확한 ACL 검증, 전체 회귀·production 활성화 |

각 에이전트는 지정 범위 밖 파일을 수정하지 않았고 커밋을 만들지 않았다. root가 공유 경계를 검토하고 전체 회귀를 실행했다.

## 핵심 변경 파일

### Solver와 constraint

- `servers/fastapi/utils/slidespec_solver.py`
- `servers/fastapi/utils/slidespec_constraints.py`
- `servers/fastapi/utils/slidespec_core.py`
- `servers/fastapi/utils/slidespec_deck_constraints.py`
- `servers/fastapi/models/slidespec_v1.py`
- `servers/fastapi/scripts/slidespec_solver_benchmark.py`
- `servers/fastapi/tests/unit/test_slidespec_solver.py`
- `servers/fastapi/tests/unit/test_slidespec_perf_harness.py`

### Chrome 측정과 projection/repair

- `servers/fastapi/utils/slide_capture.py`
- `servers/fastapi/utils/authored_dom_quality.py`
- `servers/fastapi/utils/authored_patch_engine.py`
- `servers/fastapi/services/slidespec_authored_importer.py`
- `servers/fastapi/services/slidespec_authored_executor.py`
- `servers/fastapi/services/slidespec_repair.py`
- `servers/fastapi/services/slidespec_repair_pipeline.py`
- `servers/fastapi/tests/unit/test_slidespec_safe_projection_repair.py`
- `servers/fastapi/tests/integration/test_slidespec_browser_metrics.py`
- `servers/fastapi/tests/integration/test_slidespec_page11_browser_canary.py`

### Corpus와 관측성/UI

- `servers/fastapi/tests/fixtures/slidespec_golden_corpus.json`
- `servers/fastapi/tests/integration/test_slidespec_golden_corpus.py`
- `servers/fastapi/services/authored_quality_review_service.py`
- `servers/nextjs/app/(presentation-generator)/services/api/authored.ts`
- `servers/nextjs/app/(presentation-generator)/presentation/components/AuthoredQualityReview.tsx`
- `servers/nextjs/app/(presentation-generator)/presentation/components/AuthoredQualityReview.cy.tsx`

### 운영·영속성

- `servers/fastapi/services/slidespec_artifact_store.py`
- `servers/fastapi/models/sql/slidespec_artifact_bundle.py`
- `servers/fastapi/models/sql/slidespec_publication_attempt.py`
- `servers/fastapi/alembic/versions/fb2c4d6e8a0b_add_slidespec_artifact_bundles.py`
- `servers/fastapi/alembic/versions/fc3d5e7f9a1b_add_slidespec_publication_attempts.py`
- `servers/fastapi/utils/user_config_store.py`
- `servers/fastapi/tests/unit/test_user_config_store.py`
- `scripts/presenton-local-proxy.mjs`
- `Dockerfile`, `docker-compose.yml`

그 밖의 tracked 변경은 authored acceptance/results, workspace operation, Electron env typing, rollout 기본값과 해당 회귀 테스트다. 전체 정확한 목록은 `git status --short`가 authoritative하다.

## Solver benchmark

실행:

```powershell
cd C:\project\PPT-agent\ppt-agent\servers\fastapi
.\.venv\Scripts\python.exe scripts\slidespec_solver_benchmark.py --cases 48 --seed 20260804
```

| 지표 | 결과 |
|---|---:|
| duration p50 / p95 / p99 | 1.879 / 111.361 / 116.776 ms |
| branch p50 / p95 / p99 | 1 / 2 / 2 |
| total branches | 56 |
| feasible ratio | 1.0 |
| unknown ratio | 0.0 |
| multi-branch ratio | 0.166667 |
| bounded-optimality ratio | 0.5 |

성능 테스트 임계값은 p50 50ms, p95 400ms, p99 700ms, branch p95/p99 4, unknown 0이다.

## 테스트 결과

| 범위 | 결과 |
|---|---|
| FastAPI 전체 | 2332 passed, 16 skipped, 270.90s |
| SlideSpec top-level 단위·통합 | 105 passed, 23.83s |
| Chrome golden/browser/page11 | 15 passed, 17.31s |
| userConfig ACL 단위 | 10 passed |
| Next lint | 통과 |
| Next production build/TypeScript | 통과, 31 routes |
| chat Cypress | 5 passed |
| authored quality Cypress | 6 passed |
| Node/Chrome CI | 277 passed |
| diff whitespace | `git diff --check` 오류 없음 |

FastAPI 전체 실행의 6개 warning은 Starlette/httpx, websockets, SQLModel의 기존 deprecation 계열이며 test failure나 ACL 경고가 아니다.

## 서비스 활성화

| 포트 | PID | 실행 방식 | HTTP 증거 |
|---:|---:|---|---|
| 3000 | 38204 | Next 16.2.11 standalone production `.next-build/standalone/servers/nextjs/server.js` | `/dashboard` 200, 47,144 bytes |
| 5000 | 10852 | `scripts/presenton-local-proxy.mjs` | `/dashboard` 200, `/api/v1/auth/status` 200 |
| 8000 | 47904 | FastAPI `server.py --port 8000 --reload false` | direct `/api/v1/auth/status` 200 |

standalone의 `.next-build/static`과 `public`은 proxy startup에서 동기화된다. direct `/openapi.json`은 인증 없이 401이며 보호 정책이 유지된다.

실행 환경은 `SLIDESPEC_REPAIR_CANARY=100`,
`SLIDESPEC_REPAIR_APPLY=true`, `SLIDESPEC_REPAIR_ALL_SLIDES=true`,
shadow false, kill switch false다. 고정 UUID의 첫 슬라이드 정책 결과는
`slidespec_active`/eligible/apply true다. 동일 입력의 kill switch true
결과는 `legacy`, `system.kill_switch_enabled`이다.

## Windows userConfig ACL

`app_data/userConfig.json`은 Administrators 소유이지만 DACL inheritance가
차단되어 있고 current user와 SYSTEM만 필요한 권한을 가진다. 이 소유권에서는
`icacls /inheritance:r` 재적용이 Access denied가 될 수 있다. 구현은
pywin32 또는 PowerShell `Get-Acl`로 exact DACL을 읽어 다음을 요구한다.

- protected DACL
- current user와 SYSTEM 외 ACE 없음
- inherited/unresolved/Everyone ACE 없음
- 현재 사용자의 read/write와 SYSTEM full control

따라서 재적용 실패 후에도 이미 private인 파일은 안전하게 수용하고, 약한 ACL은
fail closed 한다. fingerprint cache는 dev/inode/ctime/size 변경 시 무효화된다.
production 재기동 로그에는 기존 ACL 경고가 없다.

## Migration rollback rehearsal

live DB를 건드리지 않고
`C:\Users\ibiz\AppData\Local\Temp\presenton-slidespec-migration-a4e4f440560b4853bfc2e83c822bb0c4`
아래 임시 SQLite DB에서 수행했다.

1. empty → head `fc3d5e7f9a1b`: artifact bundle/publication attempt table과 candidate constraint 확인.
2. head → `fb2c4d6e8a0b`: publication attempt와 신규 constraint 제거, artifact bundle 유지 확인.
3. `fb2c4d6e8a0b` → head: 두 table과 constraint 복원 확인.

upgrade/downgrade/re-upgrade가 모두 성공했다.

## 작업트리·커밋 판정

최초 완료 시점의 `git status --porcelain`은 tracked modified 39개,
untracked 42개, staged 0개였다. 작업 시작 시 이미 tracked 변경과 SlideSpec
핵심 파일 대부분이 dirty/untracked였기 때문에 최초에는 attribution이 명확한
proxy 변경만 커밋했다. 이후 2026-08-05 사용자가 현재 완료된 변경 전체를
명시적으로 커밋·병합·푸시하도록 지시했고, 그 범위 안에서 런타임 로그를
제외한 구현·테스트·문서를 다음 논리 단위로 분리했다.

- `88ebbeed` `fix: sync public assets for local standalone`
- `339aa29b` `feat(slidespec): add deterministic constraint solver`
- `dd8a8cc9` `feat(slidespec): harden Chrome repair pipeline`
- `2326001c` `feat(slidespec): persist artifacts and activate rollout`
- `39fd14db` `feat(slidespec): expose repair metrics and controls`

각 커밋 전 `git diff --cached --check`를 통과시켰다. 기능 브랜치는
`codex/slidespec-quality-hardening-20260805`이며, 기준 `main`에 no-ff로
병합한 뒤 `origin/main`에 푸시한다. reset, checkout을 이용한 변경 폐기,
broad delete, stash는 수행하지 않았다. `.local-runtime-logs/`는 현재 서비스가
사용하는 로컬 실행 증거이므로 커밋 대상에서 제외했다.

## 남은 지원 경계

- core oracle 예산 초과 시 cardinality-minimum을 주장하지 않고 증명된 inclusion-minimal fallback을 반환한다.
- 측정 불능 external asset/animation/canvas/SVG path는 hard fail closed다.
- continuous occlusion은 adaptive 40×40 component 추정이며 vector analytic area는 아니다.
- imported connector의 arbitrary topology, flow 영향이 큰 static translation, 증명되지 않은 crop/shrink는 적용하지 않는다.
- corpus는 고객 개인정보를 포함하지 않는 7종 production-shaped fixture이며 장기 운영 분포는 지속적으로 누적해야 한다.
