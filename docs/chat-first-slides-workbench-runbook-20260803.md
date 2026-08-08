# Chat-first Slides Workbench 운영 Runbook

- 기준일: 2026-08-03
- 대상: Chat-first Slides Workbench v1
- 기준 설계: `chat-first-slides-workbench-design-20260802.md`
- 설계 재평가: `chat-first-slides-workbench-design-review-v2-20260802.md`
- 원칙: 오른쪽 채팅 타임라인이 생성·수정·진행·결과·오류·재시도·취소·승인의 영속 control plane이다.

이 문서는 현재 구현된 계약과 배포·복구 절차를 설명한다. 설계 문서와 코드가 충돌하면 ADR-001~005와 실제 마이그레이션·상태 머신을 우선 확인한다. 데이터 손실을 유발하는 schema downgrade나 workspace 기록 삭제는 일반적인 롤백 절차가 아니다.

## 1. 운영 불변조건

다음 조건이 깨지면 기능을 활성화하지 않는다.

1. 최초 생성 프롬프트, 첨부 파일 snapshot, 생성 설정을 생성 실행보다 먼저 첫 사용자 메시지로 영속화한다.
2. 같은 bootstrap/command/action idempotency key는 같은 결과를 반환하며 side effect를 중복 실행하지 않는다.
3. top-level timeline 순서는 message/operation anchor의 `position`으로만 결정한다.
4. operation event는 해당 카드 projection만 갱신한다. message pagination cursor와 event high-watermark는 별개다.
5. `presentation.revision`은 최종 publication commit token이다. checkpoint/progress는 revision을 올리지 않는다.
6. runner와 outbox의 DB row가 source of truth다. 프로세스 내부 wake-up은 polling 지연만 줄인다.
7. stale lease/attempt/fencing token으로는 publication이나 terminal finalize를 할 수 없다.
8. 공개 공유 응답에는 prompt, 첨부, conversation, operation, 내부 revision·오류 추적 정보가 포함되지 않는다.
9. 지원하지 않는 DB, 잘못된 runtime profile, 필수 malware scanner 부재는 시작 단계에서 fail-close한다.

## 2. Runtime profile과 기능 플래그

### 2.1 지원 프로파일

| 실제 DB dialect | `CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE` | 지원 여부 |
|---|---|---|
| SQLite | `sqlite-local` | 지원 |
| PostgreSQL | `postgresql-cluster` | 지원 |
| MySQL/MariaDB | 해당 없음 | Chat-first v1 차단 |
| 알 수 없는 dialect | 해당 없음 | 차단 |

기능이 켜진 상태에서는 runtime profile이 필수이며 실제 DB dialect와 일치해야 한다. 불일치는 조용히 축소 실행하지 않고 다음 안정 오류 코드로 거부한다.

- `chat_first_runtime_unsupported`
- `chat_first_runtime_profile_required`
- `chat_first_runtime_profile_invalid`
- `chat_first_runtime_profile_mismatch`

SQLite는 `BEGIN IMMEDIATE` 기반의 local adapter를 사용한다. PostgreSQL은 row lock/skip-locked 기반 cluster adapter를 사용한다. core schema, command, event, receipt, publication 계약은 dialect-neutral하게 유지한다.

### 2.2 플래그

| 계층 | 환경변수 | 기본값/허용값 | 의미 |
|---|---|---|---|
| Backend | `CHAT_FIRST_WORKSPACE_ENABLED` | 기본 `false`; true: `1,true,on,yes` | command ingress, runner, outbox, asset maintenance 활성화 |
| Frontend | `NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED` | 기본 off; on: `1,true,on` | build-time Workspace Chat bundle gate. 값 변경에는 frontend rebuild/redeploy가 필요 |
| Kill switch | `CHAT_FIRST_WORKSPACE_KILL_SWITCH` | 기본 off | cohort보다 우선해 새 workspace write를 즉시 차단 |
| Cohort | `CHAT_FIRST_WORKSPACE_ROLLOUT_PERCENT` | 0~100 | owner + presentation 안정 bucket의 write 허용 비율 |
| Cohort salt | `CHAT_FIRST_WORKSPACE_ROLLOUT_SALT` | 배포별 비밀 아닌 안정 문자열 | rollout bucket의 배포별 안정성 |
| Shadow | `CHAT_FIRST_WORKSPACE_SHADOW_ENABLED` | 기본 off | parity 관측 활성화 |
| Worker | `WORKSPACE_OPERATION_WORKER_MODE` | `embedded` 또는 `external` | claim/heartbeat/recovery 실행 프로세스 선택 |
| Migration | `MIGRATE_DATABASE_ON_STARTUP` | 배포 정책; `true`이면 startup upgrade | Alembic head 적용 |
| Legacy task timezone | `PPT_AGENT_ASYNC_TASK_LEGACY_TIMEZONE` | populated PostgreSQL e7 upgrade/downgrade 때 필수 | legacy `datetime.now()` wall clock의 PostgreSQL timezone 이름 |

백엔드와 프런트엔드 플래그는 함께 관리한다. 프런트만 켜거나 backend capability 확인 없이 command UI를 노출하지 않는다. `NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED` 자체는 build-time 값이지만, 해당 bundle이 배포된 뒤에는 backend feature-off, kill switch 또는 cohort 제외라는 authoritative runtime 판정이 SharePopover를 포함한 legacy UI를 rebuild 없이 복원한다. legacy write는 이 명시적인 rollback 판정에서만 허용하며 runtime/profile 불건전이나 command capability 오류는 blocked로 처리한다. 지원 dialect의 읽기 전용 timeline/history/artifact download는 command write와 독립해 유지한다. partial rollout에서 presentation scope가 없으면 `chat_first_presentation_scope_required`로 fail-close한다.

### 2.3 시작 순서와 fail-close

권장 시작 순서:

1. DB backup과 migration dry-run 결과를 확인한다.
2. runtime profile과 실제 DB dialect 일치를 검증한다.
3. asset malware scanner 정책을 검증한다.
4. `MIGRATE_DATABASE_ON_STARTUP=true`인 경우 Alembic head를 적용한다.
5. operation handler를 등록한다.
6. embedded 모드면 lifespan에서 runner를 시작하고, external 모드면 `python -m services.workspace_operation_worker`를 별도 기동한다.
7. cleanup journal maintenance는 chat-first flag와 무관하게 app lifecycle에서 시작한다. write가 활성화된 경우에만 outbox dispatcher와 workspace asset maintenance를 추가로 시작한다.
8. backend capability/health를 확인한 뒤 frontend 플래그를 연다.

현재 lifespan과 external worker는 runtime/scanner 안전성 검사를 filesystem/schema mutation이나 실행보다 먼저 수행한다. 검증 실패 시 migration, worker, outbox를 시작하지 않는다. account deletion은 chat-first rollback 중에도 가능하므로 cleanup journal sweeper는 `CHAT_FIRST_WORKSPACE_ENABLED` 조건 밖에서 기동하며 정상 app shutdown 때만 중단한다.

## 3. 확정 ADR-001~005

### ADR-001 — Dialect-neutral core와 runtime adapter

- SQLite local과 PostgreSQL cluster만 Chat-first v1의 지원 프로파일이다.
- position 예약, claim/lease/fencing, outbox dequeue는 dialect adapter로 분리한다.
- MySQL/MariaDB는 동일한 migration·동시성·복구 parity가 검증되기 전 명시적으로 차단한다.
- API capability와 worker startup은 동일한 runtime 판정을 사용한다.

### ADR-002 — 공통 operation runner/outbox

- 기존 durable presentation generation job의 claim/lease/heartbeat/recovery/attempt fencing을 일반 `WorkspaceOperationRunner` 계약으로 재사용한다.
- 별도의 병렬 job 시스템을 만들지 않는다. 기존 generation job은 호환 projection으로 workspace operation과 연결한다.
- operation/event/outbox는 DB transaction 경계에서 기록한다.
- outbox는 at-least-once 전달이며 consumer idempotency가 필수다.
- 프로세스 종료 시 진행 중 claim을 안전하게 놓아 실패 횟수를 불필요하게 차감하지 않는다.

### ADR-003 — Anchor 기반 timeline projection

- top-level은 user/assistant message와 operation anchor의 `position`으로 정렬한다.
- event `sequence`/cursor는 기존 카드의 progress/result/error projection만 갱신한다.
- 과거 message pagination cursor와 event high-watermark를 분리한다.
- cursor가 보존 범위 밖이면 부분 replay를 고집하지 않고 timeline snapshot부터 rebase/backfill한다.
- 작업이 늦게 완료돼도 anchor 위치는 이동하거나 중복 생성되지 않는다.

### ADR-004 — Revision publication commit

- revision은 사용자에게 공개된 덱 snapshot의 commit token이다.
- 중간 checkpoint와 progress는 revision을 증가시키지 않는다.
- 최종 publish에서 expected revision과 fencing token을 함께 검사하고 revision을 정확히 한 번 증가시킨다.
- 외부 파일은 operation-scoped immutable 경로에 먼저 쓰고 SHA-256/크기를 검증한다.
- DB transaction은 pointer, 새 revision, operation result, event, outbox를 함께 commit한다.
- commit 실패나 fencing 상실 시 기존 published pointer는 유지한다. 미참조 파일은 삭제하거나 `cleanup_pending` 보상 대상으로 기록한다.
- DB commit 결과가 모호하면 DB를 다시 읽어 pointer 소유권을 확인하고, 이미 commit된 live artifact는 삭제하지 않는다.

### ADR-005 — Asset lifecycle

허용 상태는 다음 여덟 개뿐이다.

`uploaded`, `scanning`, `processing`, `ready`, `failed`, `quarantined`, `expired`, `deleted`

| Asset 상태 | Bootstrap | Operation 동작 | 사용자 조치 |
|---|---|---|---|
| `ready` | 허용, 첫 메시지/anchor 영속화 | 즉시 실행 가능 | 없음 |
| `uploaded`, `scanning`, `processing` | 허용, 먼저 영속화 | `waiting_for_assets`, 상태 event 기록 후 재개 | 대기 또는 취소 |
| `failed` | 기록은 보존 | 실행 차단 | retry/re-upload |
| `quarantined` | 기록은 보존 | 원본 접근 차단 | 안전 안내, 재첨부 |
| `expired`, `deleted` | attachment snapshot 보존 | 실행 차단 | 재첨부 |

처리 제한 시간을 넘기면 `asset_processing_timeout`으로 전환해 무한 대기하지 않는다. 같은 bootstrap idempotency key 재전송은 첫 메시지나 operation을 새로 만들지 않는다.

## 4. Persistence와 migration

Alembic migrations:

- `servers/fastapi/alembic/versions/d6f8a1c3e5b7_add_chat_first_workspace_persistence.py`
- `servers/fastapi/alembic/versions/e7a9c2d4f6b8_make_generation_task_timestamps_utc.py`
- `servers/fastapi/alembic/versions/f8b0d3e5a7c9_add_durable_deletion_and_attachment_snapshots.py`
- `servers/fastapi/alembic/versions/fa1b2c3d4e5f_add_account_deletion_fence.py` (현재 head)

추가되는 주요 구조:

- `presentations.revision`
- `presentation_generation_jobs.workspace_operation_id`
- `workspace_conversations`
- `workspace_messages`
- `workspace_assets`
- `workspace_message_attachments`
- `workspace_operations`
- `workspace_operation_events`
- `workspace_outbox`
- `workspace_action_receipts`
- `workspace_artifacts`
- `presentations.deleted_at`, `presentations.deletion_operation_id`
- `workspace_message_attachments.asset_id_snapshot`, `asset_sha256`, `asset_failure_code`, `asset_failure_detail`
- `workspace_cleanup_journal`
- `user.deletion_state`, `user.deletion_generation`, `user.deletion_requested_at`

주요 제약은 conversation/presentation owner scope, `(conversation_id, position)`, asset storage key, command idempotency, retry lineage, `(operation_id, sequence)`, outbox idempotency, action receipt idempotency와 상태 check constraint다.

`e7a9c2d4f6b8`은 PostgreSQL의 기존 `async_presentation_generation_tasks.created_at`/`updated_at`을 `TIMESTAMP WITH TIME ZONE`으로 교정한다. 과거 모델은 `datetime.now()`의 naive wall clock을 기록했으므로 populated table에서는 `PPT_AGENT_ASYNC_TASK_LEGACY_TIMEZONE`(예: `UTC`, `Asia/Seoul`)을 반드시 명시한다. 값이 없거나 PostgreSQL에 없는 timezone이거나 Alembic `-x async_task_legacy_timezone=...`와 충돌하면 migration은 fail-close한다. upgrade는 그 timezone의 wall clock을 UTC instant로 보존하고, downgrade는 같은 timezone의 wall clock으로 되돌리므로 설정을 rollback 기간에도 보존해야 한다. 빈 table은 `UTC`를 사용해 안전하게 진행하며 SQLite는 물리 schema 변경이 없는 명시적 no-op이다. 애플리케이션 모델은 두 dialect 모두 aware UTC를 사용한다. 이미 aware인 schema는 재적용하지 않고, 두 timestamp column의 timezone type이 섞여 있으면 자동 복구하지 않는다.

`f8b0d3e5a7c9`은 deletion tombstone, immutable attachment snapshot과 durable cleanup journal을 추가한다. live `asset_id`는 nullable + `ON DELETE SET NULL`이고 `(message_id, asset_id_snapshot)`이 attachment identity가 된다. snapshot column을 추가하기 전에 null/missing live asset을 preflight하고, dangling attachment를 발견하면 이전 Alembic revision과 schema를 유지한 채 upgrade를 중단한다. `test_migrations.py::test_durable_deletion_upgrade_rejects_dangling_attachment_asset`는 FK를 끈 fixture로 이 무부분변경 경계를 검증한다. asset row가 이미 제거된 뒤에는 이전 schema가 history를 표현할 수 없으므로 downgrade도 fail-close한다. cleanup journal의 snapshot ID에는 FK를 두지 않아 presentation/account cascade 뒤에도 cleanup intent가 남는다.

`fa1b2c3d4e5f`는 계정 삭제 admission/publication fence를 추가한다. 삭제 시작 시 사용자를 비활성화하고 `deletion_state=deleting`, 단조 증가하는 `deletion_generation`, 요청 시각을 한 transaction에서 기록한다. 이후 생성·publication 경로는 이 fence를 재검사하므로 account cleanup snapshot이 확정된 뒤 새 외부 파일이 생기지 않는다. `deleting` marker가 남아 있으면 downgrade는 fail-close한다.

### Migration 절차

1. 기능 플래그를 끈 상태로 DB backup을 확보한다.
2. 현재 head와 대상 migration을 확인한다. PostgreSQL task row가 있으면 과거 writer의 단일 timezone 관례를 확인하고 `PPT_AGENT_ASYNC_TASK_LEGACY_TIMEZONE`을 설정한다. 여러 timezone 관례가 섞였거나 DST fall-back 중복 시각·spring-forward 존재 불가 시각이 포함되었을 가능성이 있으면 자동 이관하지 말고 원본 writer 로그를 근거로 row별 backfill 계획을 세운다.
3. staging에서 upgrade 및 앱의 기존 presentation 조회·생성 회귀를 검증한다.
4. PostgreSQL은 테스트 전용 DB에서 non-UTC legacy fixture를 포함한 upgrade/downgrade/upgrade, exact instant 보존, 설정 누락 fail-close, claim/CAS/fencing을 검증한다.
5. f8 backfill 뒤 attachment snapshot null 0, `ON DELETE SET NULL`, cleanup journal index/check constraint를 확인한다.
6. fa1 뒤 user deletion state/generation/marker check와 index를 확인하고 account delete 중 publication fence가 닫히는지 검증한다.
7. production upgrade 후 schema/index/constraint와 sole head `fa1b2c3d4e5f`를 확인한다.
8. backend 플래그를 제한된 cohort에서 켜고 runner/outbox/asset/cleanup maintenance 상태를 확인한다.

Workspace persistence와 f8/fa1 구조는 additive이며 UTC timestamp migration은 두 기존 열의 타입을 제자리 교정한다. 운영 롤백은 우선 kill switch/플래그 off이며, 일반 장애 대응에서 downgrade하거나 workspace 테이블·cleanup journal을 삭제하지 않는다.

## 5. Runner와 outbox 운영

### 5.1 Operation runner 기본값

| 환경변수 | 기본값 |
|---|---:|
| `WORKSPACE_OPERATION_LEASE_SECONDS` | 300초 |
| `WORKSPACE_OPERATION_HEARTBEAT_SECONDS` | 30초 |
| `WORKSPACE_OPERATION_POLL_SECONDS` | 1초 |
| `WORKSPACE_OPERATION_MAX_CONCURRENCY` | 2 |

Operation별 최대 시도 횟수는 row의 `max_attempts`로 관리하며 기본값은 3이다. retryable 실패는 기본 2초, 최대 300초의 exponential backoff와 안정적인 jitter를 사용한다. non-retryable 실패는 `failed`, retryable 실패가 최대 시도를 소진하면 `dead_letter`다.

Terminal state는 `succeeded`, `failed`, `cancelled`, `dead_letter`다. DB의 비종료 상태는 `queued`, `waiting_for_assets`, `blocked`, `running`, `approval_required`, `awaiting_approval`, `cancelling`이다. Claim과 retry schedule은 lease/attempt/`next_attempt_at` 및 event로 표현하며 별도 DB 상태를 추가하지 않는다.

Crash recovery는 handler registry의 `recovery_policy`를 반드시 따른다. `automatic`으로 등록된 handler만 만료 lease를 새 claim으로 되돌려 재실행할 수 있다. 내장 `presentation.generate`는 별도의 durable generation job을 재개하는 경로이므로 automatic으로 취급한다. `manual` handler와 registry에 없는 operation kind는 추측으로 replay하지 않는다. 이 경우 runner는 operation을 `failed`로 끝내고 non-retryable `operation_recovery_required`와 `manual_recovery_required` 사유를 event/card에 남기며 `next_attempt_at`을 설정하지 않는다. 취소 의도가 남은 `cancelling`/`cancel_requested` operation은 재실행 대신 `cancelled`로 종결한다.

### 5.2 Outbox 기본값과 의미

| 환경변수 | 기본값 |
|---|---:|
| `WORKSPACE_OUTBOX_LEASE_SECONDS` | 60초 |
| `WORKSPACE_OUTBOX_HEARTBEAT_SECONDS` | 15초 |
| `WORKSPACE_OUTBOX_POLL_SECONDS` | 1초 |
| `WORKSPACE_OUTBOX_MAX_ATTEMPTS` | 8 |
| `WORKSPACE_OUTBOX_MAX_CONCURRENCY` | 4 |

Outbox 상태는 `pending`, `delivering`, `delivered`, `failed`, `dead_letter`다. 재전달은 정상 동작이므로 topic consumer는 idempotency key로 중복을 흡수해야 한다. `workspace.chat.memory`도 durable outbox를 통해 전달된다.

### 5.3 정상 확인 항목

- lease 만료 전 heartbeat가 갱신되는가
- `queued` row의 `next_attempt_at`과 최신 `retry_scheduled` event가 poll+backoff 범위를 과도하게 넘지 않는가
- outbox `pending`/`failed` age와 `dead_letter` 증가가 없는가
- operation event lag가 지속적으로 증가하지 않는가
- 같은 operation/action idempotency key의 receipt가 하나뿐인가
- runner 재시작 뒤 expired claim이 복구되는가

## 6. Bootstrap, command, action

Bootstrap transaction은 staging presentation, conversation, 최초 user message, attachment rows, generation operation, operation anchor, outbox를 durable하게 만든다. commit 전에 실행을 시작하지 않는다.

최초 메시지는 다음을 복원 가능하게 보존한다.

- 사용자가 입력한 원문 prompt
- 정규화된 파일명, MIME, 크기, hash, 당시 처리 상태를 포함한 attachment snapshot
- template, language, slide count, aspect ratio 등 generation settings

명령은 `Command → Operation → Event → Action` 경로로 실행한다. 기능 플래그가 켜진 상태에서 legacy/direct mutation이 공통 command 경로를 우회하면 `chat_first_command_required`로 차단한다.

Action은 approve, cancel, retry를 포함하며 receipt와 operation version CAS로 보호한다. duplicate receipt는 기존 응답을 반환하고 side effect를 재실행하지 않는다.

## 7. 승인과 destructive action

현재 destructive approval 대상에는 `slide.delete`와 `presentation.delete`가 포함된다.

- 대기 상태: `awaiting_approval`
- event: `approval_required`
- TTL: `WORKSPACE_DESTRUCTIVE_APPROVAL_TTL_SECONDS`
- 기본 TTL: 86,400초(24시간)
- 허용 범위: 최소 60초, 최대 7일
- 만료 응답: HTTP 409, `approval_expired`

승인 시 owner/edit 권한, operation version, 현재 revision을 다시 검증한다. 만료된 카드는 실행할 수 없으며 destructive retry는 새 operation과 새 승인 창을 만든다. 확인 dialog 대신 채팅 operation card/action을 사용하되 해당 기능의 Gate D 검증이 끝나기 전 legacy UI를 제거하지 않는다.

`presentation.delete`는 승인 외에도 operation lease/fencing과 owner를 다시 검사한다. 같은 transaction에서 presentation을 tombstone/redact하고 삭제 대상 snapshot을 `workspace_cleanup_journal`에 남긴 뒤, 실제 immutable 파일/계정 소유 tree 삭제는 commit 이후 maintenance에 맡긴다. conversation, message, operation/event와 attachment snapshot은 감사·history read를 위해 보존한다. generation/export publication과 일반 operation finalizer는 commit 직전에 lifecycle을 다시 검사한다. tombstone과 경쟁한 늦은 성공은 `presentation_deleted`로 실패하고 staged immutable artifact를 보상 삭제 또는 durable cleanup으로 수렴시키며 presentation revision이나 `operation.result` event를 되살리지 않는다.

## 8. Publication과 authored/template/adaptive 경계

공통 publication service는 presentation revision을 권위 있는 commit token으로 사용한다. authored checkpoint, Template V2 revision, adaptive/template 저장 형식은 각 모드의 저장 경계를 유지하되 최종 사용자 공개 시점은 공통 expected revision/fencing 계약을 통과해야 한다.

Generation job은 기존 authored checkpoint의 fencing을 보존하기 위한 compatibility projection이다. intermediate checkpoint는 재개 지점일 뿐 published revision이 아니며 revision을 증가시키지 않는다. 최종 publish가 성공할 때만 revision을 한 번 증가시킨다.

외부 artifact와 image는 operation/attempt/fencing token 범위의 staging journal에 먼저 기록한다. SHA-256과 크기를 검증한 뒤 hash-derived immutable 경로로 승격하고, slide 및 중첩 `ImageAsset` pointer를 그 immutable 경로로 치환한다. 최종 DB unit of work가 artifact pointer, 새 revision, operation result, terminal event, outbox row를 함께 commit해야만 사용자에게 공개된다.

Publication 장애 시 확인 순서:

1. operation ID, attempt number, fencing token outcome을 확인한다.
2. DB의 current revision과 artifact pointer를 다시 읽는다.
3. immutable 파일의 SHA-256/크기와 DB row를 비교한다.
4. DB pointer가 commit됐으면 파일을 live artifact로 간주해 삭제하지 않는다.
5. pointer가 없고 파일만 남았으면 operation/attempt/fence 소유권을 확인한 뒤 안전 삭제 또는 `cleanup_pending` 보상 기록을 확인한다.
6. commit 결과가 모호하면 DB의 전체 publication ownership tuple을 먼저 조정한다. 이미 live pointer가 된 artifact는 삭제하지 않는다.
7. stale attempt라면 새 publish를 허용하지 않고 명시적 conflict/fence loss로 종료한다.
8. 보상 삭제 자체가 실패하면 독립 transaction의 `cleanup_pending` row를 남긴다. maintenance가 lease/backoff/dead-letter 정책으로 재시도한다.

## 9. Asset scanner와 maintenance

| 환경변수 | 기본/의미 |
|---|---|
| `WORKSPACE_ASSET_MALWARE_SCAN_MODE` | `required` 또는 `disabled` |
| `WORKSPACE_ASSET_MALWARE_SCANNER` | scanner 실행 파일; required일 때 미지정 시 `clamscan` |
| `TEMPLATE_V2_DEPLOYMENT_TIER` | `local`, `development`, `test`, `staging`, `production` |
| `WORKSPACE_ASSET_ORPHAN_TTL_SECONDS` | 기본 86,400초 |
| `WORKSPACE_ASSET_PROCESSING_TIMEOUT_SECONDS` | 기본 1,800초 |
| `WORKSPACE_ASSET_MAINTENANCE_INTERVAL_SECONDS` | 기본 60초 |

staging/production tier에서는 malware scan을 필수로 한다. scanner가 필요한데 사용할 수 없으면 startup을 중단한다.

Asset admission은 비동기 처리 경계다. `POST /assets`는 owner 범위의 임시 `upload.part`로 stream하면서 MIME/확장자/크기를 검증하고 SHA-256을 계산한 뒤, hash-derived immutable 이름으로 atomic rename하고 `uploaded` row를 commit하면 응답한다. 요청 thread/background task에서 malware scan이나 후속 처리를 시작하지 않는다.

Maintenance는 DB에서 `uploaded`/`scanning`/`processing` row를 전역 탐색한 뒤 각 asset의 owner context로 다시 진입한다. optimistic version/CAS로 한 worker만 `uploaded → scanning → processing → ready`를 진행하므로 중복 discovery는 무해하다. scanner 실패는 `failed`, malware 발견은 `quarantined`, 처리 시간 초과는 `failed`, orphan 만료는 `expired → deleted`로 기록한다. startup 시 한 번 reconciliation한 뒤 interval loop에서 processing resume, timeout, orphan 정리, publication `cleanup_pending` sweep를 반복하며, startup pass 실패 row도 다음 interval에서 다시 발견한다.

Bootstrap transaction과 첫 메시지 영속화는 첨부가 `uploaded`/`scanning`/`processing`이어도 허용하지만 실제 generation 실행은 모든 첨부가 `ready`일 때만 시작한다. 처리 중이면 operation card를 `waiting_for_assets`로 유지하고, 치명 상태면 `blocked`와 안전한 오류 코드를 표시하며, timeout은 retryable `asset_processing_timeout`으로 새 retry action을 허용한다. 격리 자산은 worker가 읽지 않고 외부에는 내부 scanner 출력 대신 `workspace_asset_quarantined` 같은 안전 오류 코드만 반환한다. 원본 상태가 변하거나 삭제돼도 첫 메시지의 attachment snapshot과 생성 설정 표시는 유지한다.

## 10. Timeline, reconnect, 읽기 전용 history

클라이언트는 timeline snapshot을 먼저 읽고 `event_high_watermark` 다음 event를 구독한다. 과거 message pagination은 현재 event cursor를 되돌리지 않는다. 서버는 snapshot 시작 시 conversation event floor를 고정한 뒤 message/operation anchor를 한 SQL statement에서 잘라 읽으므로, 동시 writer가 anchor를 추가해도 응답 anchor와 high-watermark 사이에 replay 불가능한 틈이 생기지 않는다.

Reconnect 절차:

1. 마지막 event cursor로 replay를 요청한다.
2. 중복 event는 operation ID/sequence로 제거한다.
3. event는 기존 anchor card에만 반영한다.
4. cursor expiry가 반환되면 timeline snapshot을 다시 받고 projection을 rebase한다.
5. 네트워크가 끊겨도 서버 operation은 계속 실행한다. 취소는 명시적 action으로만 요청한다.

기능 플래그 off 롤백 후에도 저장된 first message, attachment snapshot, operation history는 삭제하지 않는다. UI는 legacy 진입점을 사용하되 read-only history 접근을 유지한다.

SSE는 query `after` 또는 `Last-Event-ID`를 cursor로 사용하고 access/cursor를 streaming header 전에 검사한다. durable DB를 source of truth로 polling하며 event ID에 cursor를 기록한다. 응답은 `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`를 사용하고 idle 구간에는 heartbeat를 보낸다. stream 중 오류는 `schema_version=1`의 `stream_error`와 안전한 `code`, `safe_message`, `retryable`만 전송한다.

## 11. 장애 복구 Playbook

### 11.1 Worker crash 또는 lease 만료

1. operation state, lease owner/expiry, attempt/fencing token을 확인한다.
2. 정상 heartbeat 범위 내 lease는 강제 탈취하지 않는다.
3. 취소 의도가 있으면 재실행하지 않고 `cancelled`로 terminalize한다.
4. handler registry에서 operation kind와 `recovery_policy`를 확인한다.
5. 등록된 `automatic` handler만 dialect adapter의 claim/CAS와 retry policy로 `queued` 복구한다. 최대 시도 소진 시 `dead_letter` 카드와 안전한 retry action을 제공한다.
6. `manual` 또는 미등록 handler는 자동 requeue하지 않는다. `failed` + non-retryable `operation_recovery_required`(`manual_recovery_required`)로 끝내고 운영자가 외부 상태를 조정한 뒤 새 command/retry lineage를 시작한다.
7. 외부 side effect가 모호하면 artifact hash/job ID/DB pointer 및 publication ownership tuple을 먼저 조정한다. 비멱등 side effect를 추측으로 반복하지 않는다.

### 11.2 Operation `dead_letter`

1. 안전한 `error_code`, attempt/retry/recovery count를 확인한다.
2. 원인이 runtime/profile, asset, revision, 외부 dependency인지 분류한다.
3. 원인 제거 전 동일 command의 수동 반복 실행을 금지한다.
4. 사용자가 retry action을 누르면 새 operation을 만들고 `retry_of_operation_id`를 보존한다.
5. destructive command는 새 승인 만료 시각을 발급한다.

### 11.3 Outbox backlog 또는 `dead_letter`

1. dispatcher heartbeat와 DB 연결을 확인한다.
2. `pending`/`failed` row의 next attempt, lease expiry, attempt count를 확인한다.
3. consumer 반영 여부를 idempotency key로 조정한다.
4. 이미 반영된 side effect는 delivered 상태만 복구하고 재실행하지 않는다.
5. 미반영이고 retry-safe한 row만 재전달한다.
6. `dead_letter` 증가는 경보 대상으로 유지하고 원인·조정 결과를 audit에 남긴다.

### 11.4 Asset processing 정체·격리·만료

- `uploaded/scanning/processing`이 timeout을 넘으면 maintenance가 `asset_processing_timeout`으로 종료한다.
- scanner 양성이면 `quarantined`로 두고 원본 접근을 금지한다. 격리를 임의 해제하지 않는다.
- `expired`/`deleted`는 attachment snapshot을 지우지 않고 재첨부 action을 제공한다.
- transient processor 실패만 retry하고 hash/owner를 다시 검증한다.

### 11.5 Stale revision과 두 탭 충돌

- CAS 실패를 성공으로 처리하지 않는다.
- `REVISION_CONFLICT`/안전 오류 코드와 최신 revision을 카드에 반영한다.
- 자동 재적용이 안전한 명령만 최신 revision에서 재계획한다.
- 그 외에는 사용자가 변경 내용을 검토하고 새 command로 재시도한다.

### 11.6 Stream 장애와 cursor expiry

- stream wake-up은 source of truth가 아니다. DB polling/replay로 보완한다.
- event lag가 증가하면 DB event 저장과 dispatcher/stream 전달을 분리해 조사한다.
- 만료 cursor는 full timeline rebase로 복구한다. 과거 cursor를 억지로 이어 붙이지 않는다.

## 12. 보안, 개인정보, 공개 공유

- 모든 private workspace API는 owner와 presentation access를 다시 확인한다.
- 다른 소유자의 리소스 존재를 숨겨야 할 때는 enumeration 방지를 위해 404를 사용한다.
- prompt, 파일명, event detail은 화면 표시 전에 escape/정규화한다.
- operation/event/outbox payload에는 원본 파일 내용, 저장 경로, API key, lease token, stack trace, raw exception을 넣지 않는다.
- 공개 공유는 명시적 allowlist DTO와 recursive denylist로 fail-close한다.
- 공개 공유에 prompt, attachment, conversation, operation, action receipt, internal artifact path, revision history, error trace를 포함하지 않는다.
- artifact 다운로드 권한은 요청 시 재검증하며 영속 URL 대신 만료 가능한 URL을 발급한다.
- LLM context는 화면 timeline 전체가 아니다. 제한된 메시지, 서버 요약, 선택 attachment chunk, 짧은 operation 결과를 provenance와 함께 조립한다.

## 13. Telemetry와 audit

관측 이벤트는 metadata-only closed schema를 사용한다.

- `schema_version=1`
- `pipeline=workspace_operation`
- event name: `accepted`, `claimed`, `running`, `retry_scheduled`, `cancel_requested`, `cancelled`, `recovered`, `succeeded`, `failed`, `dead_letter`, `blocked`, `lease_lost`, `released`, `runner_fault`
- 식별/상태: UUID operation/presentation/conversation ID, allowlisted command type, result status, stable error code
- 수치: duration/event lag, attempt/retry/recovery count, expected/result revision, fencing outcome, revision conflict

알 수 없는 command/error는 `other`로 축약한다. prompt, message 본문, 파일명·경로, lease token, secret, raw exception은 telemetry에 넣지 않는다. 오류 카드의 추적 ID와 서버 audit event를 연결하되 사용자 데이터는 기록하지 않는다.

권장 경보:

- 오래된 `queued`, `retry_scheduled`, `cancelling`, `waiting_for_assets`
- lease expiry/recovery/lease loss 급증
- operation 또는 outbox `dead_letter` 증가
- cursor/event lag 증가와 rebase 비율 급증
- revision conflict/fence loss 급증
- asset quarantine/processing timeout/orphan cleanup 급증

구현된 관측 endpoint:

- `GET /metrics/workspace`: session 인증 밖의 aggregate-only Prometheus endpoint다. API 서비스/ingress에서 Prometheus network만 허용해야 한다.
- `GET /api/v1/ppt/workspace/observability/metrics`: 인증된 운영 진단용 Prometheus text다. allowlisted operation admission/outcome, cleanup journal, outbox, rollout decision, event lag histogram, shadow parity/silent loss, dead-letter, lease-lost, revision-conflict를 노출한다.
- `GET /api/v1/ppt/workspace/observability/slo`: shadow parity, operation error/dead-letter/lease-loss/revision-conflict, cleanup/outbox dead-letter, event lag threshold를 평가한다. 분모는 transition 관측 횟수가 아니라 새로 durable하게 저장된 non-duplicate operation admission 또는 실제 claim 횟수다. 승인·취소·in-place retry 같은 기존 operation action은 admission 분모에 포함하지 않고, terminal retry가 replacement operation을 새로 만들 때만 한 번 포함한다.
- alert는 API/worker scrape absent·down, shadow silent loss/divergence, telemetry sink failure, event-lag SLO breach/stall을 구분한다. lag-stall의 활동 분모인 `event_delivered`는 `workspace.operation.event` 전달에만 기록되므로 chat-memory outbox 트래픽이 false positive를 만들지 않는다.

metrics exporter는 workflow를 실패시키지 않는 in-process 저장소다. multi-process/external-worker 배포에서는 API와 각 worker를 모두 scrape하고 외부에서 합산해야 하며, 한 process의 값만 배포 전체 SLO로 해석하지 않는다. external worker는 `WORKSPACE_OPERATION_METRICS_PORT`를 명시하면 `/metrics`와 `/healthz`만 제공하는 전용 listener를 시작한다. 기본 bind는 `127.0.0.1`이며 container/service discovery가 필요할 때만 `WORKSPACE_OPERATION_METRICS_HOST`를 명시한다. 포트를 설정하지 않으면 listener는 열리지 않고, 잘못된 host/port는 관측 불능 배포를 피하기 위해 worker startup을 실패시킨다. API와 모든 worker scrape target 예시는 `servers/fastapi/ops/prometheus/chat-first-workspace-scrape.example.yml`, alert rule은 같은 디렉터리의 `chat-first-workspace-alerts.yml`에 둔다.

## 14. Gate A~E 실제 구현 매핑

| Gate | 구현 매핑 | 통과 근거/남은 활성화 조건 |
|---|---|---|
| Gate A — 계약/ADR | ADR-001~005, runtime/error code, canonical versioned command/event/action fixture, public share denylist | `servers/fastapi/tests/unit/test_workspace_contract_fixtures.py`, `servers/nextjs/tests/workspace-contract.test.mjs`; release candidate에서는 §16 명령을 별도 실행 |
| Gate B — Persistence/runner | workspace persistence + UTC timestamp migration, revision/linkage, bootstrap idempotency, receipt, runner/outbox, registry recovery/fencing, async asset state machine | workspace persistence/runner/asset tests와 PostgreSQL opt-in suite; 실제 PostgreSQL 실행 여부는 CI·릴리스 증거에서 확인 |
| Gate C — 읽기 전용 timeline | first message/attachment/settings projection, anchor ordering, operation card, history reload, cursor rebase | frontend projection/component tests와 intercepted route E2E, 별도 FastAPI+SQLite live harness |
| Gate D — Command 전환 | generation/edit/header mutation gate, approve/cancel/retry card, stale revision UI, popup별 점진 gate | generation admission/API marker/인접 UI 회귀; 기능별 parity 확인 후에만 legacy popup 제거 |
| Gate E — 복구/E2E/관측성 | vertical API→DB→runner→timeline/reconnect/retry, snapshot/HWM 동시 writer 회귀, worker/outbox recovery, duplicate receipt, publication fault, public-share fail-close, telemetry allowlist | production lifecycle·publication fault·public share·observability tests; 실제 배포 topology 검증은 별도 release evidence |

Route E2E suite는 script가 임시 포트에서 직접 띄운 실제 Next route/browser shell에서 실행하지만 해당 route API 경계는 `cy.intercept` fixture다. 따라서 이 suite만으로 실제 FastAPI, DB, worker, SSE, LLM 연결을 검증했다고 표현하지 않는다.

같은 `npm run test:workspace-e2e` 실행은 route suite 뒤 `WorkspaceChatPanelLiveBackend.cy.tsx`와 전용 FastAPI+SQLite harness도 기동한다. 이 live component 경계는 실제 FastAPI HTTP router, SQLite persistence, timeline/event stream, operation runner를 통과한다. 다만 edit handler는 결정론적 test handler이고 PostgreSQL, 실제 LLM, production malware scanner/object store는 포함하지 않으므로 배포 환경 E2E 증거로 확대 해석하지 않는다.

asset failure 시나리오는 메시지의 immutable `uploaded` snapshot을 먼저 확인하고 test-only endpoint로 live asset을 `failed`로 CAS 전이한 뒤 current failure·safe code·generation blocked를 검증한다. `[live-backfill]` marker만 진행 gate를 사용하며 각 spec의 `afterEach` release가 잔류 gate를 해제한다. marker가 없는 성공 명령은 즉시 완료되어 harness가 모든 edit를 인위적으로 멈추지 않는지도 함께 확인한다.

## 15. 점진 Rollout과 Rollback

### Rollout

1. backend write off 상태로 migration과 read-only history를 배포한다. frontend bundle gate를 켜 배포할 경우에도 backend capability가 write를 열기 전에는 command UI를 노출하지 않는다.
2. SQLite local/PostgreSQL cluster 각각의 runtime fail-close와 health를 확인한다.
3. `WORKSPACE_OPERATION_WORKER_MODE`를 선택한다. external 모드에서는 API와 worker를 별도 process/service로 기동하고 둘의 runtime/scanner 판정이 같은지 확인한다.
4. kill switch가 off인지, rollout salt가 고정됐는지, percent와 shadow 설정이 의도한 값인지 확인한다.
5. backend write 플래그를 켜되 낮은 presentation cohort부터 시작하고 runner/outbox/asset/cleanup 및 rollout 지표를 관찰한다.
6. build-time frontend bundle을 내부 사용자/테스트 덱에 배포하고 first-message/history read와 backend authoritative blocked/legacy 판정을 확인한다.
7. generation, edit, approval, deletion 등 기능별 command 경로를 순차 전환한다.
8. duplicate, reconnect/rebase, worker recovery, revision/publication, cleanup parity를 확인한 뒤 해당 legacy popup만 제거한다.
9. shadow parity/silent loss, error/dead-letter/lease-loss/revision-conflict ratio와 event lag SLO를 만족하면 cohort percent를 단계적으로 높인다.
10. production lifecycle, Windows/a11y/required CI와 live browser+backend release 검증 뒤 기본 활성화 범위를 넓힌다.

cohort는 owner + presentation ID의 salted SHA-256 bucket으로 안정적으로 계산한다. partial rollout에서 presentation scope 없는 write는 허용하지 않는다. 배포된 frontend bundle은 backend의 explicit feature-off, kill switch, cohort 제외 판정에서만 legacy write로 전환한다. runtime/profile/capability 오류는 blocked이며 legacy write fallback 조건이 아니다. `NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED` 자체를 바꾸는 rollback은 rebuild/redeploy가 필요하다.

### Rollback

1. `CHAT_FIRST_WORKSPACE_KILL_SWITCH`를 켜 새 write를 가장 먼저 차단한다.
2. backend capability를 다시 조회해 SharePopover를 포함한 legacy UI/write 복원과 timeline read 유지를 확인한다. `CHAT_FIRST_WORKSPACE_ENABLED`를 끄는 경우에도 같은 판정을 확인한다.
3. frontend build-time gate도 내릴 필요가 있으면 `NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED=false`로 rebuild/redeploy한다. runtime 즉시 복구 수단으로 이 값만 바꾸지 않는다.
4. embedded/external worker, outbox와 workspace asset maintenance의 진행 상태를 확인하고 안전한 종료 또는 drain 정책을 적용한다. cleanup journal sweeper는 app이 살아 있는 동안 계속 둔다.
5. 진행 중 operation, cleanup journal과 외부 side effect를 조정한다. 강제 재실행하지 않는다.
6. workspace DB row, first message, attachment snapshot, receipt, artifact metadata와 cleanup intent는 보존한다.
7. f8/fa1 schema downgrade나 데이터 삭제 없이 원인을 수정한 뒤 낮은 cohort부터 재개한다.

kill switch/플래그 off가 이미 시작되거나 commit된 외부 side effect를 되돌리는 것은 아니다. publication pointer/revision, outbox delivery와 cleanup journal fencing을 반드시 조정한다. cleanup journal sweeper는 chat-first flag와 독립적으로 재시도·복구를 계속한다. 지원 dialect의 timeline/history read도 rollback 중에 유지한다.

## 16. 검증 명령

### Backend focused suite

아래 명령은 release candidate에서 재현할 검증 범위다. 이 문서는 특정 commit의 실행 성공이나 수치를 증명하지 않으며, 결과는 CI·릴리스 증거에 commit SHA와 함께 기록한다. focused group은 서로 test file이 겹칠 수 있으므로 합산하지 않는다.

```powershell
cd C:\project\PPT-agent\ppt-agent\servers\fastapi
uv run pytest -q tests/unit
```

focused evidence group의 재현 명령은 다음과 같다.

```powershell
# Gate E runner + vertical focused suite
uv run pytest -q tests/unit/test_workspace_operation_runner.py tests/integration/test_workspace_gate_e_vertical_flow.py

# External image/immutable publication 및 compensation
uv run pytest -q tests/unit/test_workspace_artifact_publication_faults.py tests/unit/test_workspace_quality_export_operations.py tests/unit/test_adaptive_template_publication_isolation.py tests/integration/test_authored_workspace_sqlite_publication.py tests/unit/test_asset_ownership_security.py::test_browser_asset_paths_are_owner_scoped_and_traversal_safe

# Public share allowlist/fail-close
uv run pytest -q tests/unit/test_presentation_share.py tests/integration/test_workspace_public_share.py tests/unit/test_session_auth_middleware.py::test_public_share_link_is_exempt

# Asset admission/lifecycle/owner boundary
uv run pytest -q tests/unit/test_workspace_asset_lifecycle.py tests/unit/test_asset_ownership_security.py tests/unit/test_lifespan.py::test_managed_chat_first_scanner_guard_precedes_schema_mutation tests/unit/test_lifespan.py::test_chat_first_workers_start_and_stop_in_dependency_order
```

### PostgreSQL real integration

주의: 아래 테스트는 `public` schema를 초기화하므로 이름이 `test` 또는 `tests`로 끝나는 전용 DB에서만 실행한다.

```powershell
cd C:\project\PPT-agent\ppt-agent\servers\fastapi
$env:PPT_AGENT_POSTGRES_TEST_URL='postgresql+psycopg://.../ppt_agent_test'
$env:PPT_AGENT_REQUIRE_POSTGRES_INTEGRATION='1'
uv run pytest -q tests/integration/test_postgresql_workspace.py tests/integration/test_postgresql_template_v2_migrations.py tests/integration/test_postgresql_template_v2_canary_rollback.py tests/integration/test_postgresql_async_task_read_facade.py tests/integration/test_postgresql_authored_cluster_quota.py
```

이 opt-in suite는 workspace migration/schema 제약, 동시 command/action idempotency, claim/CAS/fencing, template migration/canary rollback, owner-scoped async read facade, generation-task UTC timestamp 보존·복구와 authored quota/FK 경계를 대상으로 한다. 현재 문서는 이 명령이 실제 PostgreSQL에서 실행됐다고 주장하지 않는다. 실행 결과가 있더라도 PostgreSQL adapter 통합 증거이며 전체 애플리케이션 배포 topology의 E2E 증거는 아니다.

### Frontend

```powershell
cd C:\project\PPT-agent\ppt-agent\servers\nextjs
npm run test:ci-node
npx tsc --noEmit
npm run lint
npm run build
npm run test:workspace-cypress
npx cypress run --component --spec "app/(presentation-generator)/outline/hooks/usePresentationGeneration.cy.tsx"
npx cypress run --component --spec "app/(presentation-generator)/presentation/components/PresentationHeader.cy.tsx"
npx cypress run --component --spec "app/(presentation-generator)/presentation/components/SlideContent.cy.tsx"
npx cypress run --component --spec "app/(presentation-generator)/presentation/components/SidePanel.cy.tsx,app/(presentation-generator)/presentation/components/SharePopover.cy.tsx"
npm run test:workspace-e2e
```

`npm run test:workspace-e2e`는 사용자가 Next나 FastAPI를 미리 실행할 필요가 없다. script가 loopback의 빈 임시 포트를 예약하고 run별 `.next-workspace-e2e` distDir로 `NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED=true`인 Next dev app을 직접 시작한다. 가장 큰 `/presentation` route의 cold Turbopack compile이 끝날 때까지 제한 시간 안에서 먼저 워밍업한 뒤 route spec(인터셉트 API)을 실행한다. 이어서 별도 loopback 포트에서 FastAPI+SQLite runner harness를 시작하고 `WorkspaceChatPanelLiveBackend.cy.tsx` component spec(실제 HTTP/persistence/event stream/runner)을 실행한다. 종료 시 child process tree와 검증된 run directory를 정리하고 `tsconfig.json`/`next-env.d.ts`를 원래 bytes로 복원한다. live harness의 edit handler는 결정론적이며 PostgreSQL/실제 LLM/production object store는 전제로 하지 않는다.

`npm run test:workspace-e2e`와 별개로 실제 FastAPI+SQLite runner component 경계만 두 terminal에서 재현하려면 아래 명령을 사용한다. 이 harness는 실제 HTTP/persistence/event stream/runner를 사용하지만 edit handler는 결정론적이며 PostgreSQL/LLM/production object store는 사용하지 않는다.

```powershell
# Terminal 1
cd C:\project\PPT-agent\ppt-agent\servers\fastapi
uv run python -m tests.support.workspace_browser_live_harness --port 8765

# Terminal 2
cd C:\project\PPT-agent\ppt-agent\servers\nextjs
npx cypress run --component --spec "cypress/component/WorkspaceChatPanelLiveBackend.cy.tsx"
```

### Gate 1~8 remediation focused commands

아래 command는 aggregate를 대신하지 않으며 file group 사이에 중복이 있을 수 있다. 실행 결과와 commit SHA는 이 문서가 아니라 CI·릴리스 증거에 기록한다.

```powershell
cd C:\project\PPT-agent\ppt-agent\servers\fastapi

# Gate 1: SSE cursor/backoff/rebase와 monotonic projection
uv run pytest -q tests/unit/test_workspace_api.py tests/unit/test_workspace_service.py

# Gate 2: approved deletion, tombstone guard, atomic terminal result,
# publication late-success fence/compensation, account snapshot/dead-letter redrive와 durable journal
uv run pytest -q tests/unit/test_presentation_deletion_operation.py tests/unit/test_deleted_presentation_tombstone.py tests/unit/test_account_deletion_service.py tests/unit/test_account_deletion_migration.py tests/unit/test_workspace_artifact_publication_faults.py tests/unit/test_workspace_operation_runner.py tests/unit/test_workspace_service.py

# Gate 3: read/write capability, legacy rollback와 two-tab policy
uv run pytest -q tests/unit/test_chat_first_rollout_legacy_write.py tests/unit/test_workspace_api.py

# Gate 4: f8 immutable attachment snapshot, dangling-asset preflight와 lifecycle/recovery
uv run pytest -q tests/unit/test_migrations.py tests/unit/test_workspace_asset_lifecycle.py tests/unit/test_workspace_persistence.py tests/unit/test_workspace_service.py

# Gate 6: receipt/error/schema registry, allowed_actions와 authored 경계
uv run pytest -q tests/unit/test_workspace_public_error_contract.py tests/unit/test_workspace_error_projection.py tests/unit/test_workspace_contract_fixtures.py tests/unit/test_workspace_authored_template_operation_handler.py

# Gate 7: production ASGI + Alembic blank DB + external worker lifecycle
uv run pytest -q --basetemp=.pytest-gate7 tests/integration/test_workspace_production_lifecycle_gate.py

# Gate 8: rollout metrics/SLO, API+external-worker exporter와 worker ownership
uv run pytest -q tests/unit/test_workspace_gate8_observability.py tests/unit/test_workspace_metrics_exporter.py tests/unit/test_workspace_operation_worker.py
```

```powershell
cd C:\project\PPT-agent\ppt-agent\servers\nextjs
# Gate 1, 3, 4, 6 frontend projection/state unit suite
npm run test:ci-node

# Gate 3 two-context rollback, Gate 4 UX, Gate 5 required browser job, Gate 8 axe/ARIA
npm run test:workspace-e2e
```

```powershell
cd C:\project\PPT-agent\ppt-agent
# Gate 5 workflow/runner contract, Gate 8 packaging contract
node --test scripts/required-ci-contract.test.mjs
node --test electron/tests/standalone-copy.test.cjs
npm run test:local -- --dry-run
```

`npm run test:local` 기본 profile은 portable CI contract, 격리 FastAPI suite, Next Node/lint/build와 chat-first browser E2E를 실행한다. `--with-postgres`, `--with-cypress`, `--with-electron`, `--with-fidelity`, `--with-fastapi-binary`, `--with-g4`는 명시적으로 요청한 capability gate다. `--all`은 하나라도 사용할 수 없으면 실패하며 unavailable gate를 성공으로 간주하지 않는다. PostgreSQL URL은 이름이 `test` 또는 `tests`로 끝나는 폐기 가능한 DB만 사용한다.

Windows runner는 `npm.cmd`/`npx.cmd`/`uv.exe` 등을 선택하고 run별 격리 temp root를 사용한다. child는 retained supervisor가 `CreateProcessW(CREATE_SUSPENDED)`로 만든 뒤 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` Job Object에 할당하고서만 resume한다. 정상 cleanup은 Node가 보유한 supervisor `ChildProcess` handle을 종료하고 exit를 기다리며 기록된 PID, PPID 탐색 또는 `taskkill`로 process tree를 신호하지 않는다. lock metadata의 `processIdentity.birthId`는 `Get-Process -Id ... .StartTime.ToUniversalTime().ToFileTimeUtc()`의 exact FILETIME decimal string이고 `executable`은 `.Path`다. supervisor는 retained parent HANDLE에서 `GetProcessTimes`/`QueryFullProcessImageName`으로 동일 identity를 재검증한다. stale recovery에서 정확한 supervisor가 살아 있거나 identity/legacy metadata를 검증할 수 없으면 project restore·lock release를 fail-close한다. supervisor가 사라졌거나 PID가 다른 process로 재사용된 경우에만 어떤 PID도 종료하지 않은 채 비파괴 recovery를 진행한다. cleanup은 `EPERM`/`EACCES`/`EBUSY`를 retry하고 실패 시 lock/run diagnostics를 보존한다. 실제 supervisor create/exit와 retained-handle termination probe는 통과했지만 전체 stale recovery/RC는 후보 빌드에서 별도 실행한다. 직접 broad temp/root 삭제로 우회하지 않는다.

직접 실행하는 Windows pytest focused suite는 pytest 전역 numbered-temp의 `pytest-current` 링크를 공유하지 않도록 run마다 고유한 `--basetemp`를 지정한다(예: `$pytestBase = Join-Path $env:TEMP ("presenton-gate-" + [Guid]::NewGuid().ToString("N")); uv run pytest --basetemp=$pytestBase ...`). `$env:TEMP`가 없거나 쓸 수 없는 host에서는 먼저 존재하고 쓰기 가능한 좁은 parent를 선택해 확인하며, pytest가 없는 중간 parent를 대신 만들 것이라고 가정하지 않는다. 종료 코드가 0이어도 atexit에서 `PermissionError: [WinError 5]`가 나오면 clean cleanup 증거로 보지 않는다. 실제 Windows에서 전체 traceback, 사용한 base temp, ACL과 잠금 process/handle 진단을 보존한 뒤 고유 경로로 재실행해야 한다. 잠금 주체와 정리 결과가 release artifact에 남기 전에는 이 residual을 닫지 않는다. 이는 workspace runner가 소유한 lock/run-directory 정리와 별도인 pytest host-tooling 경계다.

## 17. commit 전 source-tree 검증 범위

이 절은 현재 source tree에 존재하는 검증 경계와 재현 위치를 설명한다. 특정 commit SHA, 실행 시각, pass count 또는 release candidate 통과를 주장하지 않는다. 후보 빌드에서는 §16 명령을 같은 SHA에서 실행하고 원본 CI 로그·artifact·환경 정보를 릴리스 증거로 보존한다. 서로 겹치는 focused suite, intercepted browser spec과 live harness 결과를 하나의 E2E 수치로 합산하지 않는다.

| 경계 | source-tree 증거 | 해석 제한 |
|---|---|---|
| Backend contract/unit | `servers/fastapi/tests/unit/test_workspace_contract_fixtures.py`와 Gate별 focused tests | 실행 성공·수치는 CI 결과에서 확인 |
| Migration/SQLite lifecycle | `servers/fastapi/tests/integration/test_workspace_production_lifecycle_gate.py`와 Alembic-upgraded blank SQLite harness | 실제 PostgreSQL 또는 배포 DB 증거가 아님 |
| Browser/live component | `npm run test:workspace-e2e`, `WorkspaceChatPanelLiveBackend.cy.tsx`, `uv run python -m tests.support.workspace_browser_live_harness --port 8765` | route spec의 API는 intercepted이고 live handler는 결정론적 |
| Shared API contract | `contracts/chat-first-workspace/v1/*.json`, backend Pydantic/OpenAPI test, frontend decoder test | 외부 소비자의 배포·호환 완료를 뜻하지 않음 |
| CI/Windows/a11y | required-CI contract, workspace E2E workflow job, Windows preflight/link-free staging, axe/ARIA route checks | repository merge-check 설정과 전체 Windows package/RC 실행은 별도 확인 |

### 전체 기본 활성화 전 필요한 외부 release evidence

- 실제 Next browser, FastAPI, PostgreSQL과 worker를 같은 배포 topology로 연결한 live 종단 smoke/E2E
- 배포 환경의 SSE disconnect/reconnect와 worker process crash 복구 훈련
- 실제 LLM 호출과 production object store를 사용한 generation/publication 검증
- production과 동일한 malware scanner/object storage 설정의 asset quarantine/publication 보상 검증
- Windows standalone 전체 package/release-candidate smoke
- 기능별 parity와 오류율을 관찰한 뒤 legacy popup 제거 승인

위 항목은 source-tree harness의 실패를 뜻하지 않는다. 다만 현재 문서는 실제 PostgreSQL, LLM, production object store/scanner 또는 Windows full RC를 실행했다고 주장하지 않는다.

## 18. 최종 운영 체크리스트

### 활성화 전

- [ ] DB backup 및 migration 결과 확인
- [ ] Alembic sole head가 `fa1b2c3d4e5f`이고 attachment snapshot null 0, account deletion fence 제약이 유효한지 확인
- [ ] runtime profile/dialect 일치 확인
- [ ] staging/production malware scanner 확인
- [ ] capability의 read/write 분리와 frontend blocked/legacy 판정 확인
- [ ] kill switch, rollout percent/salt, shadow와 대상 cohort 확인
- [ ] embedded/external worker mode 및 process ownership 확인
- [ ] runner/outbox lease와 polling 지표 확인
- [ ] cleanup journal backlog/`dead_letter`와 fencing 확인
- [ ] SSE proxy buffering, `Last-Event-ID` reconnect와 cursor-expiry rebase 확인
- [ ] first prompt/file/settings 새로고침 복원 확인
- [ ] duplicate bootstrap/command/action side effect 0 확인
- [ ] 두 탭 revision conflict가 명시적으로 표시되는지 확인
- [ ] public share denylist 회귀 확인
- [ ] shadow parity/silent loss와 error/lag SLO 확인
- [ ] required CI, Windows cleanup(고유 pytest `--basetemp`, atexit `WinError 5` 포함), axe/ARIA/viewport gate 확인
- [ ] rollback 플래그와 legacy UI 복귀 확인

### 장애 종료 후

- [ ] operation/outbox terminal state 조정 완료
- [ ] external worker claim/lease와 cleanup journal 상태 조정 완료
- [ ] artifact pointer/hash와 `cleanup_pending` 확인
- [ ] asset quarantine/timeout 원인 확인
- [ ] first message/history가 보존됐는지 확인
- [ ] telemetry에 민감 정보가 없는지 확인
- [ ] 재현 명령, error code, operation ID, 조치 결과를 운영 기록에 남김

## 19. Gate 1~8 remediation addendum — 운영 매핑 (2026-08-03)

이 표는 §14의 Gate A~E 구현 순서를 대체하지 않는다. Gate 1~8은 추가 remediation의 배포 책임과 source-tree 증거 위치를 묶는다. 특정 release candidate의 최종 수치나 commit SHA는 CI·릴리스 증거에서 관리한다.

| Gate | 운영 계약 | 구현/증거 위치 |
|---|---|---|
| 1 — SSE/EOF | malformed/unknown 무손실 reconnect, EOF backoff, request/fatal 분리, monotonic cursor/sequence/HWM | workspace event API, `workspaceStream`/timeline projection와 race tests |
| 2 — Delete/cleanup | 승인·revision·lease/fence 재검사, tombstone/redaction + cleanup snapshot + terminal result/revision + `operation.result` event 단일 commit, generation/export late-publication fence·보상, deleted-row legacy guard, account deletion 2-pass snapshot/resume, 활성 lease가 없는 retryable dead-letter 관리자 CAS redrive | deletion/access/account/publication services, cleanup journal service, deletion/tombstone/account/publication focused tests |
| 3 — Rollback/capability | build-time frontend gate와 backend runtime 제어 분리, backend feature-off/kill/cohort의 legacy 복원, timeline read와 command write 분리, read-only download, two-tab 동기화 | capability/runtime profile, rollout hook, SharePopover와 two-context E2E |
| 4 — Snapshot/recovery | attachment immutable snapshot, live asset `SET NULL`, 당시/현재 상태와 안전 복구 UX, schema mutation 전 dangling attachment preflight | Alembic `f8b0d3e5a7c9` + current head `fa1b2c3d4e5f`, migration 무부분변경 regression과 persistence/asset/UI tests |
| 5 — Required CI | workspace E2E 독립 PR job, FastAPI job의 lifecycle step, 표준 runner, timeout/log artifact/cleanup 진단, Windows retained Job Object supervisor와 PID 비신호 stale recovery | GitHub/Bitbucket workflow, required-CI contract, `test-local.mjs`; merge-check 필수 지정, 실제 Windows recovery/RC run, pytest 전역 temp 무경고 정리 또는 잠금 process 진단은 별도 확인 |
| 6 — API contract | canonical v1 fixture의 backend Pydantic exact round-trip·생성 OpenAPI response 검증·frontend decoder 검증, versioned receipt/duplicate, 중앙 error/schema registry, `allowed_actions`, 안전 operation card | `contracts/chat-first-workspace/v1/*.json`, backend/frontend contract tests |
| 7 — Production lifecycle | Alembic-upgraded blank SQLite, 실제 ASGI와 별도 external worker 종료, marker lease-owner PID 기록, lease-expiry recovery, 실제 SSE disconnect/backfill | `uv run python -m tests.support.workspace_browser_live_harness --port 8765`, production lifecycle integration; marker PID의 OS birth identity 및 실제 PostgreSQL/LLM/object store/scanner는 별도 증거 |
| 8 — Rollout/release | stable cohort/kill switch/shadow, low-cardinality SLO/alerts, API+worker scrape 경계, Windows PR preflight/link-free staging fallback, axe/ARIA | rollout/metrics/exporter services, Prometheus scrape/rules, packaging/route tests; full Windows package/RC는 `workflow_dispatch` 증거 필요 |

### 19.1 Cleanup와 계정 삭제

presentation/account cascade 전에 journal row가 asset/artifact와 계정 소유 tree의 owner/presentation/operation ID, storage namespace/key, immutable metadata와 root snapshot을 보존해야 한다. 계정 tree는 구성된 app-data의 `images/users`, `exports/users`, `uploads/users`, `pptx-to-html/users`, `pptx-to-json/users`와 temp root 아래의 정확한 `<owner UUID>` child만 허용한다. root가 재설정돼도 snapshot 당시 root를 검증해 처리하며 symlink, traversal, root 자체 삭제는 거부한다.

SQLite claim은 atomic update/returning 경계, PostgreSQL claim은 skip-locked 경계를 사용한다. cleanup token과 fencing을 잃은 worker는 terminalize할 수 없다. 파일이 이미 없거나 일부만 남은 경우는 멱등 성공으로 수렴하고 OS 잠금·일시 오류만 backoff 후 재시도한다.

삭제된 presentation tombstone은 legacy read/write·stream route에서도 live presentation으로 취급하지 않는다. Chat-first write가 켜진 legacy mutation은 `409 CHAT_FIRST_COMMAND_REQUIRED`, feature-off/kill-switch로 legacy route가 복원된 뒤 tombstone에 접근하면 `404`가 기대값이다. tombstone을 수동으로 되살리거나 legacy DELETE로 다시 제거하지 말고 `test_deleted_presentation_tombstone.py` 회귀를 같은 candidate에서 실행한다.

관리자 계정 DELETE는 admission marker를 먼저 별도 commit해 token을 무효화하고, 동일 계정의 중복 요청은 같은 cleanup generation으로 resume한다. marker lock 뒤 cleanup target을 한 번 snapshot하고 flush한 다음 다시 rescan하고서 restricted dependent와 user cascade를 commit한다. 최종 `204` 응답만 유실된 replay는 account-owned cleanup tombstone이 있을 때만 멱등 성공하며 임의의 존재하지 않는 UUID는 `404`로 유지한다. cleanup row가 `dead_letter`이면 자동으로 retry budget을 늘리지 않는다. 활성 `cleanup_token`/lease가 없고 `last_error.retryable=true`인 row만 관리자 권한으로 `POST /api/v1/admin/workspace-cleanup/dead-letters/{journal_id}/redrive`를 호출한다. 성공 시 `retry_wait`, 증가한 fencing/redrive count, requester/time 감사값을 확인한다. 두 관리자 동시 redrive는 하나만 성공해야 하며 non-retryable 또는 retryability가 없는 legacy row의 `409`는 강제로 우회하지 않는다.

### 19.2 활성화/중단 의사결정

활성화는 다음 순서로 승인한다.

1. f8/fa1 migration, runtime/scanner와 read capability를 write off 상태에서 검증한다.
2. external 또는 embedded worker ownership을 하나로 확정하고 production lifecycle test를 실행한다.
3. shadow와 낮은 cohort에서 command/delete/SSE/cleanup parity를 관찰한다.
4. Gate 1~8 focused command, required CI와 OS별 gate를 같은 candidate에서 실행한다.
5. SLO와 accessibility 증거를 확인한 뒤 기능별 legacy popup을 제거하고 cohort를 확대한다.

중단 시 kill switch로 새 write를 먼저 닫고 backend capability refresh로 legacy UI/write 복원을 확인한다. build-time frontend flag까지 내리려면 별도 rebuild/redeploy한다. runtime/profile 불건전은 legacy write 우회 조건이 아니다. worker/outbox는 무조건 강제 종료하지 말고 lease와 이미 commit된 side effect를 조정한다. cleanup journal sweeper는 chat-first flag와 독립된 app lifecycle로 계속 실행해 account/presentation deletion intent를 복구하며 정상 app shutdown 때만 중단한다. durable read는 유지하고, f8/fa1 downgrade·journal 삭제·history 삭제는 일반 rollback에서 수행하지 않는다.
