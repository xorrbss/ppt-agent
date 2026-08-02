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
| Frontend | `NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED` | 기본 off; on: `1,true,on` | 오른쪽 Workspace Chat UI 활성화 |
| Migration | `MIGRATE_DATABASE_ON_STARTUP` | 배포 정책; `true`이면 startup upgrade | Alembic head 적용 |
| Legacy task timezone | `PPT_AGENT_ASYNC_TASK_LEGACY_TIMEZONE` | populated PostgreSQL e7 upgrade/downgrade 때 필수 | legacy `datetime.now()` wall clock의 PostgreSQL timezone 이름 |

백엔드와 프런트엔드 플래그는 함께 관리한다. 프런트만 켜거나 backend capability 확인 없이 command UI를 노출하지 않는다. 백엔드 플래그가 꺼지면 workspace command는 비활성화되지만 이미 저장된 읽기 전용 history는 보존·조회할 수 있다.

### 2.3 시작 순서와 fail-close

권장 시작 순서:

1. DB backup과 migration dry-run 결과를 확인한다.
2. runtime profile과 실제 DB dialect 일치를 검증한다.
3. asset malware scanner 정책을 검증한다.
4. `MIGRATE_DATABASE_ON_STARTUP=true`인 경우 Alembic head를 적용한다.
5. operation handler를 등록한다.
6. outbox dispatcher, operation runner, asset maintenance를 시작한다.
7. backend capability/health를 확인한 뒤 frontend 플래그를 연다.

현재 lifespan은 runtime/scanner 안전성 검사를 filesystem/schema mutation보다 먼저 실행한다. 검증 실패 시 migration, worker, outbox를 시작하지 않는다.

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
- `servers/fastapi/alembic/versions/e7a9c2d4f6b8_make_generation_task_timestamps_utc.py` (현재 head)

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

주요 제약은 conversation/presentation owner scope, `(conversation_id, position)`, asset storage key, command idempotency, retry lineage, `(operation_id, sequence)`, outbox idempotency, action receipt idempotency와 상태 check constraint다.

`e7a9c2d4f6b8`은 PostgreSQL의 기존 `async_presentation_generation_tasks.created_at`/`updated_at`을 `TIMESTAMP WITH TIME ZONE`으로 교정한다. 과거 모델은 `datetime.now()`의 naive wall clock을 기록했으므로 populated table에서는 `PPT_AGENT_ASYNC_TASK_LEGACY_TIMEZONE`(예: `UTC`, `Asia/Seoul`)을 반드시 명시한다. 값이 없거나 PostgreSQL에 없는 timezone이거나 Alembic `-x async_task_legacy_timezone=...`와 충돌하면 migration은 fail-close한다. upgrade는 그 timezone의 wall clock을 UTC instant로 보존하고, downgrade는 같은 timezone의 wall clock으로 되돌리므로 설정을 rollback 기간에도 보존해야 한다. 빈 table은 `UTC`를 사용해 안전하게 진행하며 SQLite는 물리 schema 변경이 없는 명시적 no-op이다. 애플리케이션 모델은 두 dialect 모두 aware UTC를 사용한다. 이미 aware인 schema는 재적용하지 않고, 두 timestamp column의 timezone type이 섞여 있으면 자동 복구하지 않는다.

### Migration 절차

1. 기능 플래그를 끈 상태로 DB backup을 확보한다.
2. 현재 head와 대상 migration을 확인한다. PostgreSQL task row가 있으면 과거 writer의 단일 timezone 관례를 확인하고 `PPT_AGENT_ASYNC_TASK_LEGACY_TIMEZONE`을 설정한다. 여러 timezone 관례가 섞였거나 DST fall-back 중복 시각·spring-forward 존재 불가 시각이 포함되었을 가능성이 있으면 자동 이관하지 말고 원본 writer 로그를 근거로 row별 backfill 계획을 세운다.
3. staging에서 upgrade 및 앱의 기존 presentation 조회·생성 회귀를 검증한다.
4. PostgreSQL은 테스트 전용 DB에서 non-UTC legacy fixture를 포함한 upgrade/downgrade/upgrade, exact instant 보존, 설정 누락 fail-close, claim/CAS/fencing을 검증한다.
5. production upgrade 후 schema/index/constraint를 확인한다.
6. backend 플래그를 제한된 인스턴스에서 켜고 runner/outbox/asset maintenance 상태를 확인한다.

Workspace persistence migration은 additive이며 UTC timestamp migration은 두 기존 열의 타입을 제자리 교정한다. 운영 롤백은 우선 플래그 off이며, 일반 장애 대응에서 downgrade하거나 workspace 테이블을 삭제하지 않는다.

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

현재 destructive approval 대상에는 `slide.delete`가 포함된다.

- 대기 상태: `awaiting_approval`
- event: `approval_required`
- TTL: `WORKSPACE_DESTRUCTIVE_APPROVAL_TTL_SECONDS`
- 기본 TTL: 86,400초(24시간)
- 허용 범위: 최소 60초, 최대 7일
- 만료 응답: HTTP 409, `approval_expired`

승인 시 owner/edit 권한, operation version, 현재 revision을 다시 검증한다. 만료된 카드는 실행할 수 없으며 destructive retry는 새 operation과 새 승인 창을 만든다. 확인 dialog 대신 채팅 operation card/action을 사용하되 해당 기능의 Gate D 검증이 끝나기 전 legacy UI를 제거하지 않는다.

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

클라이언트는 timeline snapshot을 먼저 읽고 `high_watermark_cursor` 다음 event를 구독한다. 과거 message pagination은 현재 event cursor를 되돌리지 않는다. 서버는 snapshot 시작 시 conversation event floor를 고정한 뒤 message/operation anchor를 한 SQL statement에서 잘라 읽으므로, 동시 writer가 anchor를 추가해도 응답 anchor와 high-watermark 사이에 replay 불가능한 틈이 생기지 않는다.

Reconnect 절차:

1. 마지막 event cursor로 replay를 요청한다.
2. 중복 event는 operation ID/sequence로 제거한다.
3. event는 기존 anchor card에만 반영한다.
4. cursor expiry가 반환되면 timeline snapshot을 다시 받고 projection을 rebase한다.
5. 네트워크가 끊겨도 서버 operation은 계속 실행한다. 취소는 명시적 action으로만 요청한다.

기능 플래그 off 롤백 후에도 저장된 first message, attachment snapshot, operation history는 삭제하지 않는다. UI는 legacy 진입점을 사용하되 read-only history 접근을 유지한다.

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

## 14. Gate A~E 실제 구현 매핑

| Gate | 구현 매핑 | 통과 근거/남은 활성화 조건 |
|---|---|---|
| Gate A — 계약/ADR | ADR-001~005, runtime/error code, versioned command/event/action fixture, public share denylist | Backend unit **1,938 passed / 1 skipped**, Frontend Node **277 passed** |
| Gate B — Persistence/runner | workspace persistence + UTC timestamp migration, 9개 workspace table, revision/linkage, bootstrap idempotency, receipt, runner/outbox, registry recovery/fencing, async asset state machine | SQLite integration **8 passed**, PostgreSQL real DB aggregate **9 passed**(workspace 1 + migration/canary/facade/quota 8), asset **26 passed** |
| Gate C — 읽기 전용 timeline | first message/attachment/settings projection, anchor ordering, operation card, history reload, cursor rebase | Workspace component **13 passed**, route E2E **8 passed**(인터셉트 API) + live FastAPI/SQLite component **3 passed** |
| Gate D — Command 전환 | generation/edit/header mutation gate, approve/cancel/retry card, stale revision UI, popup별 점진 gate | generation admission **24 passed**, API marker/gate **25 passed**, 인접 UI 회귀 **47 passed**; 기능별 parity 확인 후에만 legacy popup 제거 |
| Gate E — 복구/E2E/관측성 | vertical API→DB→runner→timeline/reconnect/retry, snapshot/HWM 동시 writer 회귀, worker/outbox recovery, duplicate receipt, publication fault, public-share fail-close, telemetry allowlist | 전체 backend unit **1,938 passed / 1 skipped**, runner/recovery/publication focused **324 passed**, external image/publication **35 passed**, public share **14 passed**; 실제 배포 종단 검증은 별도 release evidence로 유지 |

Route E2E 8개는 script가 임시 포트에서 직접 띄운 실제 Next route/browser shell에서 실행하지만 해당 route API 경계는 `cy.intercept` fixture다. 따라서 이 8개만으로 실제 FastAPI, DB, worker, SSE, LLM 연결을 검증했다고 표현하지 않는다.

같은 `npm run test:workspace-e2e` 실행은 route suite 뒤 `WorkspaceChatPanelLiveBackend.cy.tsx`와 전용 FastAPI+SQLite harness도 기동한다. 이 live component 경계 3개는 실제 FastAPI HTTP router, SQLite persistence, timeline/event stream, operation runner를 통과한다. 다만 edit handler는 결정론적 test handler이고 PostgreSQL, 실제 LLM, production malware scanner/object store는 포함하지 않으므로 배포 환경 E2E 증거로 확대 해석하지 않는다.

## 15. 점진 Rollout과 Rollback

### Rollout

1. 플래그 off 상태로 migration과 read-only history를 배포한다.
2. SQLite local/PostgreSQL cluster 각각의 runtime fail-close와 health를 확인한다.
3. 내부 배포 인스턴스에서 backend 플래그를 켜고 runner/outbox/asset 지표를 관찰한다.
4. frontend 플래그를 내부 사용자/테스트 덱부터 켠다.
5. first-message/history read path를 확인한다.
6. generation, edit, approval 등 기능별 command 경로를 순차 전환한다.
7. 각 기능의 duplicate, reconnect, recovery, revision/publication parity를 확인한 뒤 해당 legacy popup만 제거한다.
8. live browser+backend release 검증과 관측 기준을 만족한 후 기본 활성화 범위를 넓힌다.

코드에 사용자 allowlist가 내장됐다고 가정하지 않는다. 점진 rollout은 배포 환경, 인스턴스, 계정/테넌트 feature management 등 운영 제어면에서 수행한다.

### Rollback

1. `NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED`를 끄고 legacy UI 진입점을 복구한다.
2. `CHAT_FIRST_WORKSPACE_ENABLED`를 끄고 새 workspace command ingress와 runner/outbox/asset maintenance startup을 중단한다.
3. 진행 중 operation의 DB 상태와 외부 side effect를 조정한다. 강제 재실행하지 않는다.
4. workspace DB row, first message, receipt, artifact metadata는 보존한다.
5. schema downgrade나 데이터 삭제 없이 원인을 수정한 뒤 제한된 rollout부터 재개한다.

플래그 off가 이미 시작된 외부 side effect를 되돌리는 것은 아니다. publication pointer/revision과 outbox delivery 여부를 반드시 조정한다.

## 16. 검증 명령

### Backend focused suite

전체 unit aggregate는 아래 명령으로 최종 재실행했다. focused 결과는 서로 test file이 겹칠 수 있으므로 aggregate 수치에 합산하지 않는다.

```powershell
cd C:\project\PPT-agent\ppt-agent\servers\fastapi
uv run pytest -q tests/unit
```

확정된 focused evidence group의 재현 명령은 다음과 같다. 그룹 사이에는 중복 test file이 있을 수 있으므로 서로 합산하지 않는다.

```powershell
# Gate E runner + vertical focused suite (최종 targeted aggregate는 §17에서 별도 기록)
uv run pytest -q tests/unit/test_workspace_operation_runner.py tests/integration/test_workspace_gate_e_vertical_flow.py

# External image/immutable publication 및 compensation = 35
uv run pytest -q tests/unit/test_workspace_artifact_publication_faults.py tests/unit/test_workspace_quality_export_operations.py tests/unit/test_adaptive_template_publication_isolation.py tests/integration/test_authored_workspace_sqlite_publication.py tests/unit/test_asset_ownership_security.py::test_browser_asset_paths_are_owner_scoped_and_traversal_safe

# Public share allowlist/fail-close = 14
uv run pytest -q tests/unit/test_presentation_share.py tests/integration/test_workspace_public_share.py tests/unit/test_session_auth_middleware.py::test_public_share_link_is_exempt

# Asset admission/lifecycle/owner boundary = 26
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

실제 PostgreSQL aggregate **9 passed** 중 workspace 1건은 workspace migration, schema 제약, 동시 command/action idempotency, claim/CAS/fencing 계약을 포함한다. 나머지 8건은 template migration/canary rollback/owner-scoped async read facade, generation-task UTC timestamp 업·다운·재업그레이드 exact-instant 보존, 이미-aware/unversioned/orphan revision의 idempotent 복구와 mixed-type fail-close, 다중 프로세스 authored quota/FK fixture를 검증한다. 이는 실제 PostgreSQL adapter 통합 증거이지 전체 애플리케이션의 배포 환경 E2E 증거는 아니다.

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
uv run python tests/support/workspace_browser_live_harness.py --port 8765

# Terminal 2
cd C:\project\PPT-agent\ppt-agent\servers\nextjs
npx cypress run --component --spec "cypress/component/WorkspaceChatPanelLiveBackend.cy.tsx"
```

## 17. 현재 확인 결과 — 총괄 최종 갱신 영역

> 갱신 규칙: commit 전 통합 검증이면 `미커밋 작업 트리 검증`으로 명시하고, 최종 병합 후에는 총괄이 동일 commit에서 재실행한 결과, 시각, commit SHA를 이 절에 기록한다. 부분 spec, intercepted API, 실DB 반복 실행을 서로 합산해 하나의 E2E 수치로 표현하지 않는다.

- 확인 기준일: 2026-08-03
- 최종 통합 commit SHA: **미커밋 작업 트리 검증**
- 최종 통합 재실행 시각: **2026-08-03 07:47 KST 종료**

| 검증 | 현재 결과 | 범위/주의 |
|---|---:|---|
| Backend unit 최종 aggregate 재실행 | **1,938 passed / 1 skipped** | `tests/unit`; 기존 Starlette deprecation warning 1건, 143.37초 |
| SQLite integration | **8 passed** | Gate E vertical flow, authored publication, public-share integration |
| PostgreSQL real DB aggregate | **9 passed** | workspace 1 + migration/canary/facade/quota 8; UTC timestamp exact-instant/idempotent recovery, mixed-type fail-close, concurrency/claim/CAS/fencing 포함; 배포 E2E 아님 |
| Gate E runner/recovery/publication focused | **324 passed** | targeted 실행; 전체 unit과 중복될 수 있으며 browser/실제 LLM/object store 아님 |
| External image/immutable publication | **35 passed** | hash publication, fencing, ambiguous commit reconciliation, compensation/cleanup |
| Public share | **14 passed** | explicit allowlist, private field 제거, auth fail-close |
| Asset | **26 passed** | async admission, lifecycle/CAS, owner boundary, wait/block/retry |
| Frontend Node suite | **277 passed** | `npm run test:ci-node` auto-discovered test set |
| Frontend TypeScript | **passed** | `npx tsc --noEmit` |
| Frontend ESLint | **passed** | `npm run lint` |
| Next production build | **passed (31 routes/pages)** | `npm run build` |
| WorkspaceChatPanel component | **13 passed** | Cypress component, fixture API |
| Generation admission component | **24 passed** | bootstrap/legacy admission과 first-message handoff |
| API marker/gate component | **25 passed** | durable projection marker의 marked/unmarked/ambiguous 응답 처리 |
| 인접 presentation/upload/outline UI | **47 passed** | header/version/slide/side/share/upload/outline 회귀 |
| Workspace route E2E | **8 passed** | 자체 기동한 isolated-port Next route/browser shell + intercepted API; live FastAPI/SSE/LLM 아님 |
| Workspace live FastAPI/SQLite component | **3 passed** | 같은 script가 기동한 real HTTP/router/persistence/event stream/runner 경계; 결정론적 edit handler, PostgreSQL/LLM/object store 아님 |

### 전체 기본 활성화 전 남은 release evidence

- 실제 Next browser와 FastAPI/PostgreSQL/worker를 같은 배포 topology로 연결한 live 종단 smoke/E2E
- 실제 SSE disconnect/reconnect와 worker process crash를 포함한 배포 환경 복구 훈련
- 실제 LLM 호출과 production object store를 사용한 generation/publication 검증
- production과 동일한 malware scanner/object storage 설정의 asset quarantine/publication 보상 검증
- 기능별 parity와 오류율을 관찰한 뒤 legacy popup 제거 승인

이 항목은 현재 자동 테스트의 실패를 뜻하지 않는다. isolated Next route E2E는 API를 intercept하고, FastAPI+SQLite runner component/vertical 검증은 실제 HTTP·DB·runner를 사용하더라도 결정론적 handler와 로컬 파일 경계에 머문다. PostgreSQL aggregate **9 passed**는 adapter·migration·동시성 계약의 증거일 뿐 live Next/worker가 결합된 하나의 종단 수치가 아니다. 어느 결과도 실제 PostgreSQL 배포 topology, 실제 LLM, production object store/scanner를 함께 검증한 것으로 표현하지 않으며, 전체 사용자 기본 활성화에 필요한 운영 증거는 별도로 유지한다.

## 18. 최종 운영 체크리스트

### 활성화 전

- [ ] DB backup 및 migration 결과 확인
- [ ] runtime profile/dialect 일치 확인
- [ ] staging/production malware scanner 확인
- [ ] backend capability와 frontend flag 일치 확인
- [ ] runner/outbox lease와 polling 지표 확인
- [ ] first prompt/file/settings 새로고침 복원 확인
- [ ] duplicate bootstrap/command/action side effect 0 확인
- [ ] 두 탭 revision conflict가 명시적으로 표시되는지 확인
- [ ] public share denylist 회귀 확인
- [ ] rollback 플래그와 legacy UI 복귀 확인

### 장애 종료 후

- [ ] operation/outbox terminal state 조정 완료
- [ ] artifact pointer/hash와 `cleanup_pending` 확인
- [ ] asset quarantine/timeout 원인 확인
- [ ] first message/history가 보존됐는지 확인
- [ ] telemetry에 민감 정보가 없는지 확인
- [ ] 재현 명령, error code, operation ID, 조치 결과를 운영 기록에 남김
