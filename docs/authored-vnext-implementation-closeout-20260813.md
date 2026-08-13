# Authored-only vNext 구현 종료 및 Release Gate — 2026-08-13

## 판정

**구현 vertical slice는 성립했지만 release/canary는 NO-GO다.**

공개 authored 생성, SQL operation, Master 고정, Workbench 편집·재생성·rollback,
durable export, native PowerPoint master/layout 생성까지 한 흐름으로 연결했다. 그러나
renderer 동적 보안 gate, FastAPI 전체 회귀, i18n, file-size, 전체 PostgreSQL fresh
migration, package freshness, provider 보안 기본값 및 공개 OOXML import가 아직
fail-closed 또는 실패 상태다. feature flag 기본값은 계속 OFF다.

## 이번 세션에서 구현한 내용

### 공개 생성과 Workbench

- SQL-backed generation operation, owner scope, idempotency hash,
  lease/heartbeat/fencing, account-deletion fence를 추가했다.
- master pin → 10장 generate/resume → detached body regeneration candidate → explicit
  publish → reorder/rollback의 공개 API와 client를 연결했다.
- active deck/slide/head publication은 revision/digest CAS를 사용한다.
- Workbench가 published `DeckMasterContract` scene, virtual rail, typed body edit,
  conflict/rebase/discard, server reorder/rollback, candidate review와 durable export를
  실제 호출한다. In-memory repository는 dev/test 구현으로만 남겼다.
- 공개 OOXML import inspect/quarantine/edit/save/fork 계약은 아직 없으므로 임의의
  암묵적 import를 추가하지 않고 차단 상태로 유지했다.

### PPTX/OOXML export

- Python↔TypeScript worker v1 request/success/master/error schema를 생성 코드로
  통합했다.
- `deck-document/v1`과 `deck-generation-snapshot/v1`을 discriminator로만 해석한다.
- logo, page number, visibility, asset identity, font file manifest를 worker까지
  전달하고 미바인딩 logo bytes와 무음 손실을 거부한다.
- native master/layout/theme, editable page-number field, semantic read-back,
  furniture duplicate 및 package drift 검증을 추가했다.
- CJK rich run, fallback, emoji, combining/NFC, long-title promotion gate를 추가했다.
- HTTP/mailto hyperlink는 warning-preserve, external resource/UNC/file/template link와
  알 수 없는 relationship은 quarantine한다.

### 데이터·BYOK/provider

- Authored 10개 테이블의 owner UUID NOT NULL/User CASCADE FK와 canonical owner scope를
  추가했다. generation/export cancellation과 late-publication fence를 계정 삭제에
  연결했다.
- provider credential reference CRUD/rotation/revocation, Windows DPAPI vault와 AWS
  Secrets Manager backend interface를 연결했다.
- authored author/VQA/image SDK 직전에 admission, estimated budget, RPM, queue,
  circuit breaker, cancellation을 적용했다.
- custom LLM 및 OpenAI-compatible image URL에 allowlist, DNS/public-IP,
  private/metadata address와 redirect 차단을 적용했다.

### Renderer 격리

- 최소 renderer image, hash-locked dependencies, Chromium sandbox, non-root,
  read-only root, network-none, dropped capabilities와 no-new-privileges 계약을
  launcher/compose에 연결했다.
- source tree, renderer contract, image, deployment aggregate digest를 image label과
  runtime inspect에서 대조한다.
- Windows Electron renderer PID의 12개 webPreferences/IPC 격리 control을 검사한다.
- WSL2 실측에서 필요한 user namespace/AppArmor가 없어 Chromium이 시작되지 않았고,
  제품은 `renderer-isolation-failed`로 실행을 거부하고 컨테이너를 정리했다. 격리
  수준을 낮춘 fallback은 없다.

### 공급망·운영 증거

- Next/FastAPI/Electron source tree SHA-256과 resource provenance manifest를 생성하고
  package preflight에서 stale bundle을 거부한다.
- root/Next/Electron production lock과 FastAPI uv lock을 합친 deterministic SPDX 2.3
  SBOM을 생성했다. 현재 1,066 packages다.
- NOTICE를 Python, root Node, Next, Electron 설치 집합에서 재생성하고 GenOffice
  NOTICE와 번들 폰트 SIL OFL 1.1 전문·inventory를 배포 리소스에 추가했다.
- authored-only census에 run/release/build/git/Alembic/flag/salt-hash metadata와
  canonical evidence digest를 추가했다.
- content-free canary evidence를 `advance|hold|kill|rollback|complete`로 판정하는
  read-only CLI를 추가했다.

## 검증 결과

### 통과

- Next 전체 Node CI: **461 passed**.
- Next TypeScript: `npx tsc --noEmit` PASS.
- 공개 generation 집중: **19 passed**.
- Legacy census/canary/frozen runtime: **66 passed**.
- OOXML/worker/compliance/Office tooling: **68 passed**; FastAPI contract 7 + vertical 1
  PASS.
- Data/provider: focused **114 passed**, migration 추가 1 PASS.
- Renderer: FastAPI 40, gate unit 10, Electron isolation 7 PASS.
- Electron release audit 3, package preflight tests 29, build config 2 PASS.
- Python 전체 Ruff, `git diff --check`, SBOM freshness, NOTICE freshness PASS.
- SQLite full fresh migration과 populated backfill PASS.
- Disposable PostgreSQL에서 authored migration chain, 두 프로세스 publish CAS
  (`published`/`conflict`), `pg_dump`/restore와 row count를 확인했다.

### 실패 또는 미검증

1. FastAPI 전체 `pytest -q`는 실패했다. `--last-failed` 재실행에서 **26 failed**:
   renderer allowlist와 기존 SlideSpec browser/repair, chat transaction 및 migration
   회귀가 포함된다.
2. Renderer WSL2 dynamic gate는 **53 PASS / 13 FAIL**, `promotion_allowed=false`다.
   승인된 Linux release runner에서 user namespace/AppArmor가 활성화된 상태로 다시
   실행해야 한다.
3. i18n gate는 전체 251건, authored-vNext UI 소유 범위 172건 literal이 남았다.
4. 500-line ratchet은 다수 기존 성장 파일과 신규 파일 때문에 실패한다. 핵심 신규
   잔여는 FlowClient, MasterSetup, OOXML adapter, SQL repository, provider runtime,
   renderer isolation/chrome/security다. baseline은 상향하지 않았다.
5. 실제 package preflight는 기존 resource에 `.source-provenance.json`이 없어서 의도한
   대로 실패했다. Next/FastAPI/Electron resource를 새로 빌드해야 한다.
6. 전체 PostgreSQL fresh upgrade는 기존 `d1a4c7e9b2f6`의 Asset Gateway state 검사에서
   중단된다. authored 전용 chain은 별도로 통과했다.
7. 공개 OOXML import inspect/quarantine/edit/save/fork API가 없다.
8. PowerPoint GUI repair-dialog=0 witness와 실제 LibreOffice GUI matrix는 실행하지
   않았다.
9. provider는 모든 공식 SDK outbound에 공통 egress pinning이 적용된 상태가 아니다.
   budget은 process-local estimated reservation이고 actual cost reconciliation,
   Electron raw settings migration, 실제 AWS KMS 호출이 남았다. secure credential
   강제 기본값도 아직 호환 모드다.
10. upstream byte-parity manifest는 이번 대규모 working-tree 변경을 아직 승인된
    classification으로 재생성하지 않아 stale이다.

## 다음 실행 순서

1. FastAPI 26개 회귀를 renderer policy 변경/기존 fixture 계약으로 분류해 모두 green으로
   만든다. 보안 allowlist 확대는 최소 필요 속성·태그 단위 characterization test와 함께
   수행한다.
2. authored-vNext UI 172개 literal을 locale catalog로 이동하고 FlowClient/MasterSetup을
   500줄 미만 책임 단위로 분리한다.
3. 공개 OOXML import lifecycle을 strict generated schema와 owner/CAS/quarantine 경계로
   구현한다.
4. 승인된 Linux runner에서 renderer gate PASS를 확보하고 Windows PowerPoint 실기
   witness를 수집한다.
5. 전체 PostgreSQL fresh migration, package rebuild/provenance preflight, durable
   provider budget/credential migration을 통과한 뒤 최종 release gate를 다시 실행한다.

물리적 legacy 삭제, feature flag ON, 배포, 실제 provider 호출, 고객 데이터 사용은 이번
세션에서 수행하지 않았다.
