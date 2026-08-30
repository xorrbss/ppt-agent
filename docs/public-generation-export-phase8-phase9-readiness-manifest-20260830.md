# Public Generation/Export Phase 8-9 Readiness Manifest

작성일: 2026-08-30
기준 계약: `docs/public-generation-export-target-design-handoff-20260830.md`
판정 범위: Phase 8 cleanup inventory와 Phase 9 RC 실행 계획
판정: **Phase 8 inventory 작성 완료, Phase 9 PASS 아님**

## 1. 조사 경계와 현재 상태

- 실제 checkout은 `C:\project\PPT-agent\ppt-agent`다.
- 브랜치/HEAD: `fix/authored-release-score-20260829` / `3a9690152aaad1ad7d14a0fcdb046ba2c196a74b`.
- 기존 dirty/untracked와 다른 세션 소유 변경은 보존했다. `.spec-memory`는 Status/Compile만 읽었고 수정하지 않았다.
- 이 문서는 코드 삭제, legacy cutover, cache/evidence 삭제, commit/push/merge/deploy를 수행하지 않는다.
- 이번 확인은 repository-wide 실행이 아니라 이름·정의·직접 호출·import/export를 좁게 확인하는 reference census다. 이는 runtime access log나 실제 product-path release evidence가 아니다.

## 2. Phase 8 cleanup inventory

### 2.1 기존 네 가지 “바로 제거” 후보의 재판정

| 후보 | 현재 call-graph/reference 증거 | 재판정 | 다음 gate |
|---|---|---|---|
| `_chat_first_generation_enabled` | `presentation_generate.py`에서 admission 중 호출. 동일 이름 helper가 `presentation_generation_staging.py`에도 존재하며 runtime guard를 수행. `test_chat_first_runtime_admission.py`와 `test_chat_first_generation_integration.py`가 직접 참조. | **dead 아님; 보존** | 두 helper의 canonicalization은 별도 변경 경계에서 contract test 후 진행 |
| 동일 의미 alias 2개 | 현재 census에서 위 helper 이름의 두 구현은 확인했지만, 두 구현을 단순 alias로 합칠 근거는 없음. staging 경로의 별도 guard와 호출 경계를 먼저 입증해야 함. | **확정 dead 아님; 보류** | caller/adapter migration과 좁은 admission check |
| stale Ruff 설정 | `servers/fastapi/pyproject.toml`의 `[tool.ruff]`, rule selection, file-specific ignores가 현행 설정으로 존재. 일부 파일의 `# ruff: noqa`도 legacy compatibility와 연결될 수 있음. | **확정 dead 아님; 보류** | 실제 Ruff version/config load와 파일별 zero-use 증거. 이 세션에서는 broad lint를 실행하지 않음 |
| `PATH_ONLY` alias (`PATH_ONLY_EXPORT_COMPATIBILITY`) | `servers/nextjs/lib/presentation-export-strategy.ts`에서 export된 상수 정의와 removal condition은 확인되지만, 현재 저장소의 직접 import/call은 확인되지 않음. | **제거 후보; 아직 확정 dead 아님** | supported exporter 전체 import census, runtime access log 0, rollback rehearsal, revision/digest-bound result 전환 |

### 2.2 통합 후 제거 후보

| 대상 | 현재 상태 | 제거 조건 |
|---|---|---|
| `PublicExportIdentity` helper 분산 | `PublicExportIdentity` 모델이 alembic/database/artifact store와 여러 focused test에 연결됨. 현재 삭제 대상이 아님. | canonical builder 도입, 모든 caller 전환, identity digest/fence 회귀 check |
| legacy Next export path | legacy result field와 preview/download compatibility가 존재하며 실제 migration contract에 포함됨. | v2 cutover, access log 0, product-path smoke, rollback rehearsal |
| `claim_next_public_operation` | `public_operation_admission.py`에 구현 및 `__all__` 공개가 확인되나 직접 저장소 call은 census에서 확인되지 않음. | runner claim contract와 adapter migration 증거, unresolved import 0, rollback |
| vendor mirror | 실제 runtime access와 build dependency를 아직 확인하지 않음. | runtime access log 0, build dependency 0, packaged/export smoke, rollback |

### 2.3 무조건 보존하는 경계

- legacy export 및 helper는 migration gate 전까지 보존한다.
- `source-content`와 `source-locked`는 계약이 다르므로 중복으로 삭제하지 않는다.
- `presentation-export/index.js`와 `presentation-export/index.cjs` mirror는 readiness/checksum 계약을 바꾸기 전까지 보존한다.
- `cache/output/evidence` 약 16.7 GiB 후보는 이 manifest의 코드 cleanup과 분리한다. PID/listener ownership, 보존 manifest, checksum, quarantine/TTL 없이는 삭제하지 않는다.

### 2.4 Phase 8 exit gate

Phase 8은 다음 네 증거가 대상별로 모두 있을 때만 완료로 올린다.

1. zero-call 또는 adapter migration 증거(정적 census만으로 runtime zero를 주장하지 않음)
2. 제품 경로 smoke에서 unresolved import/route 0
3. 기존 사용자 산출물·evidence 보존 및 checksum chain
4. rollback 시 legacy LKG와 기존 download/read path가 유지되는 rehearsal

현재 판정은 `inventory_ready`, `removal_authorized=false`다.

## 3. Phase 9 RC 실행 가능한 evidence manifest

각 실행은 다음 identity를 갖는 append-only receipt로 남긴다.

```text
rcRunId = <buildDigest>/<environmentDigest>/<scenarioId>/<attempt>
scenarioId = <deckSize>-<concurrency>-<temperature>-<faultProfile>
```

필수 receipt 필드:

```text
buildDigest, environmentDigest, scenarioId, operationIds,
ownerTenantBindings, queue/admission timestamps, phase timings,
provider/authoring/validation/render/package/upload/publish timings,
status transition history, artifact/ref/digest/fence, error reason,
RSS/CPU/disk, node identity, fault injection receipt, cleanup receipt
```

### 3.1 부하 matrix

| 차원 | 값 | 실행 단위 | PASS 주장에 필요한 증거 |
|---|---|---|---|
| deck | 5, 10, 20 slides | 각 크기별 동일 fixture와 실제 API product path 분리 | request → worker → artifact publication 전체 receipt |
| concurrency | C1, C2, C5, C10 | 각 deck size × cold/warm 조합 | admission/quota/lease/fence와 per-operation 결과 |
| temperature | cold, warm | cold는 worker/browser/cache 초기화 조건, warm은 사전 준비 조건을 receipt에 고정 | 두 조건의 환경 digest와 동일 scenario schema |
| stress | 30 slides | 별도 stress scenario | deadline, RSS, queue fairness, artifact isolation |
| cross-node | 3개 이상 replica | 동일 idempotency replay, lease expiry, stale completion 포함 | node별 operation event와 fence rejection/commit evidence |

실행 순서는 각 조합의 독립 run으로 한다. fixture/isolated check PASS는 product-path release readiness를 대체하지 않는다.

### 3.2 failure injection matrix

| fault | 주입 지점 | 기대 상태/검증 |
|---|---|---|
| provider timeout | provider gateway call | bounded retry, `provider_unavailable` 또는 `deadline_exceeded`, partial result 미공개 |
| worker crash | leased work item 실행 중 | lease expiry 후 재실행, stale attempt가 pointer/publication을 갱신하지 않음 |
| S3 지연 | upload/readback/commit | deadline 및 artifact commit 정책 준수, provisional artifact 오공개 금지 |
| DB failover | admission/claim/finish 경계 | fail closed 또는 명시적 retry, duplicate receipt와 fence 보존 |
| cancel race | running/packaging/publishing 경계 | terminal state 단조성, 취소 후 결과 공개 금지 또는 계약된 committed 결과만 허용 |
| malicious payload | payload, archive, owner boundary | validation rejection, path traversal/oversize 차단, raw secret/session 미노출 |
| cross-owner replay | 다른 owner/tenant의 operation/artifact id 사용 | `404/403` 계약, artifact bytes와 event 모두 격리 |

### 3.3 24시간 soak

- 시작 전/종료 후 queue, lease, worker, browser descendant, temporary sandbox, provisional artifact, cleanup journal snapshot을 남긴다.
- 고정된 혼합 workload는 5/10/20-slide와 C1/C2/C5/C10을 순환하며 cold/warm을 별도 표기한다.
- 매 시간 operation success/failure, p95/p99 latency, RSS, disk, queue age, retry, stale fence rejection, artifact leak, cleanup dead-letter를 checkpoint한다.
- soak 완료만으로 production activation을 승인하지 않는다. development acceptance와 production activation은 별도 gate다.

### 3.4 Export/readback evidence

- Fidelity raster와 명시적 Editable Hybrid를 분리한다.
- PPTX/PDF 생성 성공, quality status, release readiness를 별도 필드로 기록한다.
- PowerPoint와 LibreOffice 결과는 renderer별 receipt와 artifact digest로 분리한다.
- 100 MiB 이상 download는 streaming/RSS evidence를 별도 남긴다.
- cross-renderer/desktop 확인이 없으면 `releaseReadiness=blocked` 또는 `unverified`로 남긴다.

## 4. 현재 실행 판정

| 영역 | 판정 | 근거 |
|---|---|---|
| Phase 8 inventory | `ready-for-gate` | 후보별 정적 definition/reference census 작성 |
| Phase 8 deletion | `blocked-by-missing-gates` | access log, adapter migration, rollback, product smoke 미완료 |
| Phase 9 matrix | `planned-executable` | 조합·fault·receipt schema 정의 |
| Phase 9 product-path RC | `not-verified` | 실제 202 API → worker → external artifact evidence 미완료 |
| production activation/cutover | `not-authorized` | handoff가 별도 승인으로 분리하며 사용자 승인 없음 |

## 5. 후보 Spec Memory handoff

`.spec-memory`는 이 세션에서 수정하지 않았다. 조정 세션이 통합할 후보만 남긴다.

- `public-generation-export.cleanup-requires-call-census-and-rollback`: cleanup은 zero-call/adapter migration, unresolved import/route 0, 보존 checksum, rollback rehearsal 이후에만 허용.
- `public-generation-export.rc-readiness-separates-product-evidence`: fixture/isolated/dispatch PASS는 실제 API → worker → external artifact 및 production activation 증거와 분리.

두 항목 모두 이 문서의 계약 정리 후보이며, 현재 세션에서 accepted로 기록하지 않았다.

## 6. Composition Gate 이후 RC harness (append-only)

`scripts/start-public-v2-rc.ps1`와
`servers/fastapi/tests/integration/test_public_generation_export_live_product_path.py`는
실제 제품 경로만 검증하도록 추가되었다. 이 harness는 PostgreSQL·MinIO·FastAPI
8010·Next 3010·proxy 5110 및 generation/export controller의 ownership을 먼저
확인하고, isolated app-data의 로그인 cookie를 노출하지 않은 상태에서 다음을 수행한다.

```text
POST v2 generation -> terminal poll -> DB observed source revision
-> POST v2 fidelity export -> terminal poll
-> fenced publication/committed artifact DB assertion
-> authorized 303 -> signed MinIO Range + streaming digest assertion
```

외부 provider가 실제로 준비되지 않았으면 generation/export 모드는 preflight에서
`blocked`로 끝나며 fake provider/transport를 주입하지 않는다. 이미 실제 생성된
presentation이 있는 경우에만 `export-only` mode를 별도 실행할 수 있다. Docker
sandbox image, PostgreSQL image, MinIO image는 local immutable `@sha256` image를
요구하고, listener ownership이 다르거나 dispatcher/S3/quality authority가 없으면
PASS를 주장하지 않는다.

이 harness의 실제 실행 결과는 아직 기록되지 않았다. 순서는 항상 **composition
integration -> focused live product E2E -> Phase 9 RC -> Phase 8 cleanup audit**이며,
RC 전 cleanup/deletion은 허용되지 않는다. harness가 만든 isolated evidence root는
자동 삭제하지 않는다.

## 7. 2026-08-30 integration/RC 실행 기록 (append-only)

### 7.1 확인된 실제 외부 seam

| 경계 | 실제 결과 | 한계 |
|---|---|---|
| 제품 worker image | `public-worker-runtime` Linux image를 최종 코드로 재봉인해 로컬 immutable image ID `sha256:c90152497f44073a0f91d09359de16f11f8f92df33ada252c880d9683fa570ce`로 빌드. source revision `3a9690152aaad1ad7d14a0fcdb046ba2c196a74b`, source-tree digest `b0be626bf2b37dc0c54e29cae2ca79f75de00bb2` 라벨 확인 | registry push는 수행하지 않음. 로컬 RC launcher는 worker에 한해 immutable local image ID를 허용하고 PostgreSQL/MinIO는 `name@sha256`를 계속 강제 |
| Docker isolation | 동일 제품 image에서 non-root/read-only/network-none/cap-drop/no-new-privileges/PID·memory·CPU/tmpfs 제한으로 render/export/runtime import 성공. `probe_docker_sandbox`가 `docker-linux-runc` 강제 capability 확인 | 실제 POST v2 operation payload를 이 image에 전달한 full product E2E는 미실행 |
| MinIO artifact | 실제 MinIO에서 두 client 간 100 MiB streaming upload/head, 64 KiB Range, signed URL Range 206, provisional orphan sweep 성공 | API RSS와 결합된 100 MiB download evidence는 아님 |
| PostgreSQL + MinIO resume | 제품 schema 생성 후 node A checkpoint 저장/identity claim, node B exact checkpoint restore 및 동일 singleflight `in_flight` join 성공 | 다중 실제 API/worker replica 부하 run은 아님 |
| owned 개발 stack readiness | export-only launcher PID `32476` 아래 8010/3010/5110 listener ancestry 확인 및 각 HTTP 200. 생성용 master catalog seed는 export-only profile에서 실행하지 않도록 교정 | provider generation이 비활성인 개발 readiness이며 public v2 product E2E PASS가 아님 |
| focused code gates | trusted transport/artifact/identity/quality/v2 orchestrator 23 PASS, workspace quality/export operation 29 PASS, `git diff --check` exit 0 | full suite/build/lint/E2E를 의미하지 않음 |

### 7.2 아직 release evidence가 아닌 항목

- 실제 provider-ready 선언과 immutable PostgreSQL/MinIO image 환경이 없어
  `POST /api/v2` generation부터 8010/3010/5110 owned stack, export child,
  S3 publication, authorized download까지의 harness는 실행하지 않았다.
- 동일 export identity 100회는 DB singleflight focused seam만 확인했으며 실제 HTTP
  계산 1회 evidence가 아니다.
- 5/10/20 slides × C1/C2/C5/C10 × cold/warm, provider/API/worker/DB/S3 전체
  fault matrix, PowerPoint/LibreOffice readback, 24시간 soak는 미실행이다.
- public v2 Editable Hybrid는 422 명시적 비지원이며 silent Fidelity downgrade가 없다.
  PowerPoint/LibreOffice/native readback evidence가 없으므로 Hybrid와 전체 release
  readiness는 계속 blocked다.

### 7.3 cleanup 재감사

통합 코드와 위 focused RC seam 확인 뒤 call census를 다시 수행했다. 제거는 하지 않았다.
`_chat_first_generation_enabled` 두 경계, Ruff, legacy export, source-content,
source-locked는 실제 call/route/CI 계약 때문에 `preserve`다. `PATH_ONLY`와
`claim_next_public_operation`은 직접 caller가 확인되지 않았지만 access-log,
migration, rollback gate가 없어 `access-log gate`/`migrate then remove`로 유지한다.
16.7 GiB cache/evidence와 다른 작업의 dirty/untracked는 건드리지 않았다.
