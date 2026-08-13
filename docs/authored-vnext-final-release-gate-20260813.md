# authored-only vNext 최종 통합·Release Gate — 2026-08-13

## 판정

**NO-GO — canary 포함 promotion 금지.**

현재 worktree는 독립 모듈과 집중 테스트는 다수 통과했지만, 공개 제품 경로가 authored generation/export/OOXML import와 연결되지 않았고 renderer 동적 보안 게이트가 `INCONCLUSIVE` (`promotion_allowed=false`)이다. 또한 master 요소의 조용한 export 유실, 계정 삭제 시 authored-vNext 데이터/산출물 정리 부재, PowerPoint repair-dialog 증거 부재가 남아 있다. 사용자가 정한 금지 조건에 직접 해당하므로 조건부 출시도 허용하지 않는다.

초기 및 최종 HEAD는 `b387a34735482d4f6dfbf2b955fbf19fd0d2315e` (`main`)이며 기존 변경을 reset/checkout/clean/delete하지 않았다. 최종 상태는 tracked 수정 43개, untracked 471개(상태 항목 117개)이다.

## 이번 최종 통합에서 추가한 안전 보강

- SQL export lease claim의 CAS 조건에 cancel/attempt/기존 lease 만료값을 포함해 heartbeat 뒤 stale claimant가 새 lease를 탈취하지 못하게 했다: `servers/fastapi/services/authored_vnext/integration/sql_repository.py`.
- rollback 대상 receipt를 owner/deck scope로 조회하고, 대상 bytes의 경로·길이·digest를 pointer CAS 전에 reopen 검증한다. receipt의 deck digest도 대상 revision에서 읽는다: `servers/fastapi/api/v1/ppt/endpoints/authored_vnext/export/router.py`.
- 손상된 rollback 대상이 LKG pointer를 바꾸지 못하는 HTTP 통합 회귀 테스트를 추가했다: `servers/fastapi/tests/integration/test_authored_vnext_export_integration.py`.
- production renderer command를 절대 Python executable + 검토된 `contracts/authored-renderer/invoke.py` 조합으로 고정하여 임의 `cmd.exe` 실행을 거부한다: `servers/fastapi/utils/renderer_isolation.py`.
- renderer gate가 확정되지 않은 동안 authored-only admission 기본값을 off/internal로 되돌려 legacy/blank/import 경로를 기본 차단하지 않게 했다: `servers/fastapi/services/compatibility_migration/admission.py`.
- OOXML import 시 `_rels/.rels`, 정확히 하나의 root `officeDocument` 관계, 모든 package part의 ContentType을 필수화했다. root 관계를 제거한 corrupt PPTX가 publish되는 재현을 회귀 테스트로 막았다: `servers/nextjs/lib/ooxml-vnext/package-graph.ts`.
- workspace export의 신규 public error code 3개를 중앙 allowlist에 등록했다: `servers/fastapi/services/workspace_error_registry.py`.
- Next production typecheck에서 드러난 presentation version과 Babel/PostCSS patch-level type 경계를 보정했다.

## 출시 차단 사항

### P0 — 실제 제품 흐름 단절

- FastAPI 공개 router는 authored-vNext Master/Export만 mount한다. generation/resume/body-only regenerate/rollback pipeline은 `InMemoryGenerationRepository` 전용이며 공개 API와 SQL repository에 연결되지 않았다.
- `/authored-vnext` UI는 로컬 outline을 만든 뒤 Master `createDeck`만 호출한다. 편집/undo/redo는 local reducer+outbox, body-only regenerate는 boolean conflict, export는 notice/queued 표시뿐이다.
- Workbench Master layer는 published master contract가 아니라 하드코딩된 header/footer/page number를 사용한다.
- 50~100장 virtual rail은 parent의 `startIndex=0` 고정으로 다음 window를 렌더하지 못하고, reorder는 실제 mutation 없이 성공 announce를 낸다.
- OOXML import/targeted edit/corporate master CRUD는 테스트·CLI에만 있고 라이브 API 호출점이 없다. imported corporate master는 `A compiled master is required`로 실제 materialization에 실패한다.

### P0 — renderer promotion gate 미통과

- static contract와 Windows Electron PID probe는 통과했으나 Docker/OCI가 없어 network-none, non-root, read-only root, tmpfs, cgroup을 실제 관찰하지 못했다.
- 저장 및 재실행 결과는 `INCONCLUSIVE`, `promotion_allowed=false`, exit 2다: `docs/experiments/authored-vnext/05-renderer-isolation-implementation/results/product-local-canary-gate/gate-result.json`.
- production compose는 required worker command/attestation sidecar와 연결되지 않아 기본 배포가 fail-closed한다.

### P0/P1 — 데이터 수명주기·계약

- authored-vNext 10개 SQL table은 FK가 없고 owner_id가 nullable이다. account deletion service에 authored rows, export artifacts, 후발 publication을 정리/fence하는 경로가 없다.
- generated `contracts/authored_vnext/export` 공개 HTTP schema와 실제 FastAPI export models의 필드, schemaVersion, receipt가 크게 다르다. 현재 drift test는 worker envelope/generated freshness만 검사하고 실제 API conformance를 증명하지 않는다.
- selection 후 insert하는 candidate/quality/render idempotency 일부는 multi-process unique race에서 raw integrity error 가능성이 남아 있다.
- 실제 PostgreSQL multi-process CAS와 online backup/restore는 미검증이다. SQLite fresh upgrade 및 cold byte-copy restore만 확인했다.

### P1 — OOXML/PowerPoint 무결성

- Python→worker bridge가 필수 `logo`, `pageNumber`와 `fontManifest.files`를 조용히 버린 채 성공 receipt를 허용한다: `servers/fastapi/services/authored_vnext/integration/export_payload.py`.
- 현재 제품 4장 artifact는 Open XML SDK 3.5.1 오류 0, LibreOffice 26.2.5.2 4→4 read-back을 통과했다. 그러나 실제 PowerPoint COM open/save/reopen과 visible repair dialog=0 증거는 없다.
- external OOXML relationship은 inventory만 하고 그대로 저장된다. PowerPoint가 열 때 외부 HTTP/UNC 연결을 시도할 수 있으므로 quarantine/warning/removal 정책이 필요하다.
- synthetic 90장과 로컬 existing 90장 no-op은 통과했지만 existing fixture가 `.gitignore` 대상이어서 clean checkout 재현성이 없다.

### P1 — provider privacy/BYOK

- offline 90×3(270장), usage/cost receipt, budget/circuit 정책의 독립 테스트는 통과했다.
- `ProviderEgressPolicy`, `ProviderCredentialVault`, `ProviderRuntimeControl`은 실제 provider SDK 호출 경로에 연결되지 않았다. custom URL/key는 FastAPI SDK config로 직접 전달되고 Electron user config에 raw JSON으로 저장된다.
- 실제 KMS/OS vault, allowlist/DNS pin/redirect denial, actual outbound budget enforcement가 없다.

### P1 — release/supply chain

- GenOffice exact commit, Apache hash, SPDX 22-package closure, `ee` fence, PPTist/AGPL exclusion은 통과했다. 하지만 제품 전체 release NOTICE/SBOM은 없고 Electron builder가 참조하는 `electron/LICENSE`/`electron/NOTICE`도 없다. 번들 폰트 OFL 전문도 동봉되지 않았다.
- Electron package preflight는 통과했으나 bundled Next/FastAPI resource가 2026-08-13 소스보다 오래됐다. preflight는 source revision/digest를 검사하지 않아 이번 변경의 package smoke 증거가 아니다.
- historical `e4` migration이 live ORM table metadata를 import하여 미래 모델 변경이 과거 migration 결과를 바꿀 수 있다.

## 검증 결과

### 통과

- FastAPI 최종 집중(renderer/admission/SQL/export/public error): 67 pass. 별도 admission 및 public error 회귀 묶음 64 pass.
- Authored Master/deck/export generated contract freshness: PASS.
- Fresh SQLite `alembic upgrade head`: `d4f8a0b2c6e1`; authored table 10개. cold backup→restore copy도 head와 10개 table을 재확인했다.
- OOXML core/adapter/security 최종 집중: 19/19 pass. synthetic/existing 90장 no-op 포함.
- Next `tsc --noEmit` 및 최종 production build(35 static pages): PASS. ESLint: 0 errors, 기존 warning 4개.
- Next full Node: 507 pass, 1 skip, 1 Windows Chrome cleanup failure. 실패한 SVG test 단독 재실행은 PASS하여 flaky cleanup으로 분류했다.
- Electron typecheck/lint: PASS. release workflow 4 pass, package-preflight 25 pass, production dependency vulnerability 0.
- root/Next production `npm audit`: 각 0 vulnerability. Electron production 0, dev/build high 3.
- provider focused 92 pass, offline 90×3 270장 pass. Legacy/census focused 87 + 25 pass.
- `git diff --check`, presentation-export v0.4.3, provider/settings/template registries: PASS.

### 실패 또는 미확정

- FastAPI 전체: 최초 3,313개 중 3,235 pass / 17 skip / 61 fail. admission 기본값 및 error registry 회귀를 수정한 뒤 해당 64개는 pass. 남은 last-failed 재실행은 14 pass / 25 fail / 5 deselected이다. 남은 실패는 Chrome/SlideSpec quality, chat transaction, migration D2 등 현재 dirty worktree 회귀이며 baseline HEAD 별도 실행 증거가 없어 기존 실패로 축소 분류하지 않았다.
- Next lint wrapper: i18n hardcoding gate에서 authored-vNext 신규 한국어 literal 196개 이상으로 FAIL. ESLint 자체는 error 0.
- Next build: 최초 missing `PresentationData.version`, 이어 Babel/PostCSS 중복 patch 타입을 발견해 수정했다. 최종 production build는 PASS했다.
- root CI: file-size ratchet에서 신규/증가 oversized file 다수로 FAIL. downstream 독립 root test는 46/47 pass, upstream byte-parity policy stale 1 fail.
- Ruff 전체: 신규 renderer test의 ASYNC109 5건으로 FAIL.
- renderer dynamic security gate: INCONCLUSIVE / promotion disallowed.
- Windows PowerPoint matrix, actual PostgreSQL cluster CAS, dynamic backup under write load, full product E2E는 미확정.

## 비용·승인 필요 항목

- 실제 유료 provider staging, 고객 데이터 전송, 배포는 실행하지 않았다.
- 유료 provider staging은 별도 비용 승인과 synthetic/non-customer prompt corpus가 있어야 한다.
- Windows PowerPoint visible repair witness는 고객 파일이 아닌 disposable corpus로 실행하되, 기존 PowerPoint 세션 종료/자동화 허용에 대한 운영 승인 또는 격리 runner가 필요하다.
- Docker/OCI 보안 gate는 격리된 Linux runner가 필요하지만 유료 provider 호출은 필요하지 않다.

## 커밋 정책

커밋 후보는 제품 코드, contracts/schema, migrations, focused tests, executable runbook, exact-pin/license policy, 최소 SBOM과 작은 synthetic fixture다. `node_modules`, `.next-build`, `app_data`, Electron `resources/build/dist`, 반복 생성 PPTX/PNG, LibreOffice 중간 렌더, SARIF/NDJSON 복제본, 로컬 PID/경로가 담긴 evidence는 기본 제외한다.

현재 untracked에는 PPTX 9, PNG 10, SARIF 6, NDJSON 11개가 있다. 어떤 것도 삭제하지 않았다. `docs/experiments` 전체를 일괄 stage하지 말고 source-of-truth harness + 작은 fixture + final receipt + hash manifest만 allowlist해야 한다.

## 다음 조치(우선순위, 5개 이하)

1. generation/regenerate/edit/export/OOXML import를 SQL-backed 공개 API와 Workbench에 실제 연결하고 shared schema로 API conformance를 강제한다.
2. renderer OCI gate를 `PASS`, `promotion_allowed=true`로 만들고 production compose/attestation을 같은 launcher digest에 묶는다.
3. logo/pageNumber/font files export를 구현하거나 명시적으로 reject하고, PowerPoint 90장/corporate/unsupported COM read-back 및 repair-dialog=0을 확보한다.
4. authored-vNext account deletion/FK/owner non-null/late-publication fence와 실제 provider vault·egress·budget enforcement를 제품 호출 경로에 연결한다.
5. 전체 테스트·i18n·file ratchet·Ruff·NOTICE/SBOM·fresh package provenance를 모두 green으로 만든 뒤에만 release gate를 재실행한다.

Rollback은 현재 feature flag default-off/internal 및 renderer/export fail-closed를 유지하고, legacy physical delete나 schema downgrade를 수행하지 않는다. 장애 시 신규 authored admission을 닫고 active artifact LKG pointer를 유지하며 app version만 rollback한다.
