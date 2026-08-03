# Chat-first Slides Workbench 수정 설계 재평가 리포트

- 평가일: 2026-08-02
- 평가 대상: `docs/chat-first-slides-workbench-design-20260802.md`
- 비교 기준: 1차 평가 `78/100`
- 평가 방법: 수정 설계의 제품·UX·데이터·동시성·복구 계약을 현재 FastAPI/Next.js 및 지원 DB 구조와 재대조
- 판정: **핵심 설계 승인 / 개발 조건부 GO**
- 종합 점수: **89/100**

> 문서 상태 안내(2026-08-03 구현 동기화): §1~§8의 점수, P0/P1 우선순위, GO/보류 판정과 미체크 항목은 2026-08-02 설계 착수 전의 역사적 심사 기록이다. 현재 열린 blocker 목록이나 release 통과 증거로 읽지 않는다. 현재 source-tree 계약 매핑은 §9를 기준으로 하며, 실제 release candidate의 통과 수치와 commit SHA는 CI·릴리스 증거에 별도로 기록한다.

## 1. 최종 판단 — 2026-08-02 역사적 평가

수정 설계는 이전 평가의 핵심 Blocker를 대부분 정확하게 반영했다. 특히 다음 결정은 더 이상 다시 설계할 필요가 없다.

- 최초 생성 프롬프트, 첨부 파일, 생성 설정을 오른쪽 채팅의 첫 사용자 메시지로 영구 보존
- 업로드 이후 `staging presentation`을 기준으로 conversation, 첫 메시지, attachment, 생성 operation을 원자적으로 bootstrap
- 일회성 팝업 대신 영속 operation card로 생성·수정·검토·내보내기·복구 상태 표시
- 안정적인 asset ID, conversation 실체, 메시지 position allocator, 덱 authoritative revision 도입
- DB event를 원본으로 하는 재접속 가능한 timeline과 cursor 만료 시 전체 재동기화
- idempotency, operation version CAS, 취소·승인·재시도·되돌리기, worker lease와 복구 정책
- authored/adaptive/template-v2 capability를 서버가 권위 있게 선언
- 공개 공유 DTO에서 prompt, 첨부, 대화, operation 정보를 분리
- feature flag, shadow mode, 롤백, 수용 테스트 게이트

따라서 제품 방향과 핵심 도메인 모델은 승인할 수 있다. 다만 **현재 문서 그대로 전체 구현에 들어가는 것은 권장하지 않는다.** 기본 SQLite 실행 환경과 설계의 PostgreSQL·다중 서버 전제가 아직 하나의 지원 정책으로 정리되지 않았고, 범용 operation runner와 통합 timeline 투영 규칙도 구현자가 해석해야 하는 부분이 남아 있다.

권장 판정은 다음과 같다.

| 범위 | 판정 | 조건 |
|---|---|---|
| Sprint 0: 계약 fixture, mutation inventory, DB/runner spike | **즉시 시작 가능** | 아래 ADR 5개를 산출물로 고정 |
| P0-A 모델·마이그레이션 구현 | **조건부 시작 가능** | DB 지원 프로파일과 position allocator 방식을 먼저 확정 |
| P0-B outbox·worker·durable stream | **아직 착수 보류** | runner 소유권·claim·fencing·wakeup 계약 확정 후 시작 |
| P1-A 읽기 전용 오른쪽 timeline | **병렬 prototype 가능** | anchor 기반 timeline projection fixture를 먼저 고정 |
| 기존 팝업 제거와 전체 사용자 활성화 | **아직 불가** | 문서 21장의 수용 게이트와 본 리포트 Gate A~E 통과 필요 |

한 문장으로 정리하면 **“설계 방향은 맞고 개발을 시작해도 되지만, 구현 방식의 5개 결정을 먼저 잠근 뒤 단계적으로 진행해야 한다.”**

## 2. 재점수표 — 2026-08-02 역사적 평가

1차 평가와 같은 가중치를 사용했다.

| 평가 항목 | 가중치 | 1차 | 재평가 | 가중 점수 | 판단 |
|---|---:|---:|---:|---:|---|
| 제품 비전과 사용자 맥락 연속성 | 15 | 93 | 96 | 14.40 | 최초 프롬프트·파일·생성 정보의 첫 메시지 보존이 bootstrap과 수용 기준까지 연결됨 |
| 정보 구조와 카드 UX | 15 | 88 | 94 | 14.10 | 카드 상태, 팝업 예외, 패널 폭, 선택 대상, 직접 편집 집계가 구체화됨 |
| Command/Event/Action 계약 | 15 | 76 | 91 | 13.65 | schema version, 상태 전이, action CAS, idempotency, stable target ID가 보강됨 |
| 영속성·재접속·타임라인 | 15 | 70 | 86 | 12.90 | snapshot high watermark와 DB replay가 보강됐으나 메시지·이벤트 투영 규칙이 한 단계 더 필요 |
| 현재 코드와의 구현 적합성 | 15 | 62 | 74 | 11.10 | 기존 durable generation 기반을 재사용할 수 있으나 DB dialect와 mutation 경로 통합 비용이 큼 |
| 동시성·복구·되돌리기 | 10 | 72 | 86 | 8.60 | authoritative revision, lease, 취소 상태가 좋아졌으나 외부 파일/side effect의 commit 경계가 남음 |
| 보안·개인정보·접근성 | 5 | 78 | 91 | 4.55 | 공유 경계, 안전 렌더링, asset 권한·격리·만료가 명확해짐 |
| 테스트·단계 전환·관측성 | 10 | 82 | 94 | 9.40 | shadow mode, parity, 장애·경쟁 조건 E2E, 롤백 게이트가 충분히 구체적임 |
| **합계** | **100** | **78** |  | **88.70 → 89** | **핵심 설계 승인 / 개발 조건부 GO** |

보조 지표:

- 제품 경험 설계: **95/100**
- 기술 설계 완성도: **88/100**
- 2026-08-02 코드 기준 구현 준비도: **74/100**
- 아래 ADR 반영 후 예상 설계 점수: **93/100**
- 1차 대비 개선: **+11점**

## 3. 이전 Blocker 반영 평가 — 역사적 기록

| 1차 지적 | 수정 결과 | 상태 |
|---|---|---|
| conversation 실체와 최초 생성 원자성 부재 | staging presentation 기반 `POST /workspace/bootstrap`과 `workspace_conversations` 정의 | **해결** |
| 임시 파일 경로와 최초 첨부 복원 불가 | `workspace_assets`, `workspace_message_attachments` snapshot metadata, provenance·만료·격리 정의 | **해결** |
| 덱 전체 authoritative revision 부재 | `presentations.revision`, expected revision CAS, 모든 mutation의 공용 revision service 정의 | **설계상 해결** |
| timeline snapshot/stream 사이 이벤트 유실 | snapshot high watermark와 `after_cursor` replay 정의 | **해결** |
| 여러 서버의 SSE 이벤트 전달 불명확 | DB event source of truth, wake-up은 힌트, heartbeat DB 재조회 정의 | **설계상 해결** |
| `max(position)+1` 경쟁 조건 | conversation row의 `next_position` 예약과 unique constraint 정의 | **해결, dialect 구현 결정 필요** |
| schema/state/action 계약 부족 | 당시 제안한 versioned schema, strict registry, cancelling/expired, action idempotency와 CAS 정의 | **당시 설계상 해결** |
| stable slide/element ID와 capability 부재 | ID 기반 target과 서버 authoritative capability 정의 | **해결** |
| 공유·보존·롤아웃 정책 부족 | public DTO 분리, 보존·압축, feature flag·shadow·rollback 정의 | **해결** |

## 4. 2026-08-02 당시 남은 구현 전 결정 — 역사적 P0 ADR

아래 항목은 설계 방향을 뒤집는 문제가 아니라, 구현자가 서로 다른 방식으로 해석하면 장애가 생기는 경계다. 코드 작성 전에 ADR로 확정해야 한다.

### ADR-001. DB 및 배포 프로파일

- 심각도: **P0 Decision Blocker**
- 영향: migration 호환성, 메시지 순번 경쟁, job claim, 다중 서버 wake-up

현재 애플리케이션은 `DATABASE_URL`이 없으면 SQLite를 기본으로 사용하고 PostgreSQL과 MySQL URL도 허용한다.

- `servers/fastapi/utils/db_utils.py:59-71`

반면 수정 설계는 conversation row에 `SELECT ... FOR UPDATE`를 사용하고, 다중 서버 wake-up 예시로 PostgreSQL `LISTEN/NOTIFY`를 제시한다.

- `docs/chat-first-slides-workbench-design-20260802.md:576-577`
- `docs/chat-first-slides-workbench-design-20260802.md:609-615`

SQLite에는 같은 방식의 row lock이 없다. 기존 durable generation 코드는 이미 이 차이를 PostgreSQL advisory transaction lock과 SQLite `BEGIN IMMEDIATE`로 분기한다.

- `servers/fastapi/services/presentation_generation_job_service.py:73-99`

권장 결정:

| 항목 | Embedded/Local 프로파일 | Managed/Cluster 프로파일 |
|---|---|---|
| DB | SQLite | PostgreSQL |
| API/worker 규모 | 단일 노드, 복수 프로세스는 제한적으로 검증 | 다중 API·worker |
| position 예약 | `BEGIN IMMEDIATE` 또는 원자적 counter update abstraction | row lock 또는 원자적 `UPDATE ... RETURNING` |
| job wake-up | 짧은 DB polling | PostgreSQL notify 또는 Redis, DB polling fallback |
| event 원본 | DB row | DB row |
| SSE replay 계약 | 동일 | 동일 |

MySQL을 Chat-first v1에서 지원하려면 같은 migration·claim·cursor 경쟁 테스트를 추가한다. 그렇지 않으면 feature capability에서 명시적으로 비활성화해야 한다. **SQL 문법을 문서의 계약으로 삼지 말고 `PositionAllocator`, `JobClaimer`, `EventCursorStore`의 dialect adapter 계약으로 표현하는 것이 최선이다.**

### ADR-002. 범용 operation runner와 outbox 소유권

- 심각도: **P0 Decision Blocker**
- 영향: 재시작 후 작업 유실, 중복 side effect, 취소·retry·lease 일관성

수정 설계는 transactional outbox와 worker lease를 요구하지만 다음 세부가 아직 없다.

- outbox row를 누가, 어떤 조건으로 claim하는가
- delivery attempt와 operation execution attempt를 같은 번호로 볼 것인가
- claim fencing token을 handler와 artifact publication까지 어떻게 전달하는가
- 재시도 지연, 최대 시도, `dead_letter`와 `operation_recovery_required`/`manual_recovery_required` 투영 조건
- wake-up이 없어도 polling으로 작업이 실행되는 최대 지연
- 프로세스 종료 시 lease·in-process task의 종료 순서

현재 코드에는 DB job row, attempt token, lease, heartbeat, 시작 시 복구, polling dispatcher가 이미 존재한다.

- `servers/fastapi/services/presentation_generation_job_service.py:173-273`
- `servers/fastapi/services/presentation_generation_job_service.py:276-305`
- `servers/fastapi/services/presentation_generation_job_service.py:380-438`
- `servers/fastapi/services/presentation_generation_job_service.py:540-592`

생성 endpoint는 여전히 FastAPI `BackgroundTasks`로 launch를 깨우지만 실행 상태 자체는 durable job row에 보존된다.

- `servers/fastapi/api/v1/ppt/endpoints/presentation_generate.py:1397-1518`

권장 결정은 새 큐 체계를 병렬로 만드는 것이 아니라, 기존 `PresentationGenerationJob`의 검증된 claim/lease/recovery 패턴을 `WorkspaceOperationRunner`로 일반화하는 것이다. outbox는 operation 생성 트랜잭션에서 쓰고, dispatcher는 outbox를 claim한 뒤 동일한 runner를 깨운다. wake-up은 지연 최적화일 뿐 실행 보장의 원본이 아니어야 한다.

### ADR-003. 메시지와 operation event의 통합 timeline 투영

- 심각도: **P0 Decision Blocker**
- 영향: 새로고침 후 순서 변경, 카드 중복, 실시간 완료 카드의 이동, pagination 오류

수정 설계에는 두 개의 순서 축이 있다.

- conversation 메시지의 `position`
- operation event의 전역 단조 `cursor`와 operation join 기반 owner/presentation 필터(당시 초안의 presentation별 cursor 표현은 현재 schema에 없음)

현재 구현은 operation row의 `anchor_position`이 timeline의 고정 위치를 차지하고, event는 그 카드의 상태만 갱신한다. 별도 assistant message row는 만들지 않으며 이 규칙은 canonical API fixture와 decoder가 함께 검증한다.

권장 projection 계약:

1. top-level timeline 순서는 message `position`과 operation `anchor_position`으로 합성한 예약 위치 순서로 정한다.
2. operation 시작 시 `WorkspaceOperation.anchor_position`이 position 하나를 예약하며 assistant message row는 생성하지 않는다.
3. operation event는 새로운 top-level 행을 만들지 않고 `operation_id` 카드 projection만 변경한다.
4. 작업이 나중에 완료돼도 anchor 위치는 이동하지 않는다.
5. progress detail은 카드 내부 `sequence`로 정렬한다.
6. 사용자 직접 편집을 top-level 기록으로 보여주려면 계약에 따라 user message 또는 operation anchor 위치를 예약한다.
7. `event_high_watermark`는 카드 최신 상태의 replay 경계이며 message pagination cursor를 대신하지 않는다.

이 모델을 선택하지 않고 메시지와 모든 event를 같은 레벨에 섞으려면 별도의 전역 `timeline_sequence`가 필요하다. 제품 UX에는 anchor 모델이 더 단순하고 작업 카드의 정체성과도 잘 맞는다.

### ADR-004. authoritative revision의 실제 commit 경계

- 심각도: **P0 Decision Blocker**
- 영향: DB revision은 증가했지만 슬라이드 파일은 이전 상태인 부분 성공, stale artifact 공개

설계의 CAS 예시는 `presentations.content`와 `revision`을 같은 SQL update로 변경한다.

- `docs/chat-first-slides-workbench-design-20260802.md:789-808`

현재 프레젠테이션 row에는 `content`뿐 아니라 `deck_plan`, `theme`, `mode`, `version`, `lifecycle_status`가 있고, Template V2 revision과 authored checkpoint 파일은 별도 저장 경계를 가진다.

- `servers/fastapi/models/sql/presentation.py:36-96`
- `servers/fastapi/services/template_v2_revision_service.py:14-69`
- `servers/fastapi/services/authored_checkpoint_service.py:226-272`

권장 결정:

- authoritative revision은 **사용자에게 보이는 덱 스냅샷의 publication commit token**으로 정의한다.
- 중간 생성 checkpoint는 덱 revision을 올리지 않는다.
- 최종 publication에서 presentation row, operation 결과, revision event, outbox를 하나의 DB transaction으로 commit한다.
- 외부 파일·object storage는 먼저 immutable key에 쓰고 hash를 검증한 뒤 DB pointer를 publication한다.
- worker attempt token을 checkpoint와 publication에 fencing token으로 사용한다.
- DB commit 후 cleanup 실패는 데이터 commit 실패로 되돌리지 않고 보상 작업으로 처리한다.
- 모든 기존 mutation endpoint 목록과 실제 저장 필드를 표로 만들고, revision service 우회가 0개일 때 feature flag를 연다.

### ADR-005. asset readiness와 bootstrap 실패 정책

- 심각도: **P0 Decision Blocker**
- 영향: 최초 메시지 유실, 검사 중 파일의 생성 시작, 격리 파일 접근, 무한 대기 카드

현재 문서는 비동기 추출 시 bootstrap이 준비를 기다리거나 processing 카드를 보일 수 있다고 두 가지 선택을 남겨 둔다.

- `docs/chat-first-slides-workbench-design-20260802.md:492`

권장 상태 계약:

- `uploaded/scanning/processing/ready`: bootstrap을 허용하고 첫 메시지와 생성 operation을 즉시 만든다.
- `ready`: 생성 handler가 바로 실행한다.
- `uploaded/scanning/processing`: operation은 `waiting_for_assets` 단계에 머물며 파일별 상태를 카드에 표시한다.
- `failed/quarantined/expired/deleted`: worker가 원본을 읽지 않고 operation을 `blocked` 또는 `failed`로 전환한다. 카드에서 해당 파일 제거·재첨부·재처리를 제공한다.
- 처리 제한 시간을 넘기면 `ASSET_PROCESSING_TIMEOUT`으로 종료하고 무한 polling하지 않는다.
- 동일 bootstrap idempotency key의 재전송은 같은 첫 메시지와 operation을 반환한다.

이 방식은 파일 처리 실패가 있어도 사용자가 처음 입력한 프롬프트와 파일 카드가 채팅에서 사라지지 않는다는 제품 요구를 보존한다.

## 5. “당시 설계가 최선인가?”에 대한 역사적 평가

### 제품·UX 관점

현재 방향은 요구사항에 거의 최선이다. ChatGPT/Genspark Slides와 유사하게 최초 요청부터 결과·수정·재시도·내보내기까지 오른쪽 대화가 작업의 기억이 되고, 중앙 캔버스는 결과와 직접 조작에 집중한다. 모든 팝업을 없애지 않고 OS picker와 정밀 편집 popover를 예외로 둔 것도 현실적이다.

### 시스템 설계 관점

아직 절대적인 최선은 아니다. 이유는 기능이 부족해서가 아니라 **단일 사용자·SQLite 데스크톱과 다중 서버 PostgreSQL 운영을 같은 구현 방식으로 설명하기 때문**이다. 현재 문서를 core contract와 deployment profile로 나누면 복잡도를 크게 낮출 수 있다.

최선의 형태는 다음 세 층이다.

```text
Product Contract
  최초 메시지 + operation card + command/action + revision + replay
        ↓
Domain Services
  BootstrapService / RevisionService / TimelineProjection / OperationRunner
        ↓
Runtime Adapters
  SQLite Local                         PostgreSQL Cluster
  immediate transaction + polling     row/advisory lock + notify/Redis + polling
```

핵심 계약은 두 프로파일에서 동일하고, lock·claim·wake-up만 adapter로 달라져야 한다. 이것이 현재 저장소의 self-hosted·Electron 성격과 향후 서버 확장성을 함께 만족하는 가장 좋은 절충안이다.

## 6. 권장 구현 순서 — 역사적 계획

### Gate 0. ADR과 fixture

1. ADR-001~005 승인
2. command/event/action JSON fixture와 timeline projection fixture 작성
3. 모든 presentation mutation endpoint·service inventory 작성
4. SQLite/PostgreSQL의 position·job claim 경쟁 spike
5. 최초 prompt + 파일 2개의 bootstrap API contract test 작성

### Gate 1. P0-A 데이터 기반

1. conversation, asset, attachment, operation/event/artifact, outbox additive migration
2. 기존 conversation backfill dry-run과 중복 position 정리
3. dialect별 position allocator와 unique constraint
4. authoritative revision service를 읽기/쓰기 경로에 shadow 적용
5. 기존 UI는 그대로 유지

### Gate 2. P0-B 실행 기반

1. 기존 durable generation runner 패턴의 범용화
2. outbox claim·delivery·dead-letter·fencing
3. event append + operation state transaction
4. timeline snapshot/high watermark/SSE replay
5. process restart, lost wake-up, duplicate action, lease expiry 테스트

### Gate 3. P1-A 수직 슬라이스

1. 오른쪽 `WorkspaceChatPanel` 읽기 전용 timeline
2. 최초 프롬프트·첨부·접힌 생성 정보 복원
3. 기존 `quality.review` 하나만 operation adapter로 연결
4. shadow parity 99.9%와 silent loss 0 검증
5. 그 다음 export, slide edit, generation 순으로 확대

### Gate 4. UI 전환

1. 기능별 direct API 호출 제거
2. 채팅 카드 행동으로 점진 이동
3. legacy modal 제거는 마지막에 수행
4. backend runtime feature-off/kill/cohort 판정에서는 같은 저장 데이터로 legacy UI/write가 복귀하고 read-only history/download는 유지되는지 확인하며, build-time frontend flag 변경에는 rebuild/redeploy가 필요함을 확인

## 7. 개발 착수 전 최종 체크리스트 — 역사적 사전 조건

### Gate A — DB

- [ ] SQLite와 PostgreSQL 지원 범위를 문서와 capability에 명시했다.
- [ ] 두 dialect에서 position 중복 0, idempotency 중복 side effect 0을 검증했다.
- [ ] 지원하지 않는 DB는 startup 또는 feature capability에서 명확히 차단한다.

### Gate B — Runner

- [ ] outbox row, claim 조건, fencing token, retry, dead-letter 계약이 있다.
- [ ] API 프로세스 재시작과 worker 강제 종료 후 작업 결과가 명시적으로 복구된다.
- [ ] wake-up 신호를 유실해도 polling으로 처리된다.

### Gate C — Timeline

- [ ] message position과 event cursor의 역할이 fixture로 고정돼 있다.
- [ ] 작업 완료 시 카드 위치가 이동하거나 중복 생성되지 않는다.
- [ ] snapshot과 stream 경계에서 누락 0, 중복 표시 0이다.

### Gate D — Revision

- [ ] template/adaptive/authored/Template V2의 publication commit 경계가 정의돼 있다.
- [ ] 모든 mutation 경로가 revision service를 통한다.
- [ ] 외부 파일 저장 실패와 DB commit 실패의 보상 정책이 있다.

### Gate E — Asset

- [ ] 모든 asset 상태에 대한 bootstrap 허용·대기·차단 행렬이 있다.
- [ ] 격리·만료 파일을 worker가 읽지 않는다.
- [ ] 파일 실패 후에도 최초 프롬프트와 파일 상태가 첫 메시지에 남는다.

## 8. 최종 결론 — 2026-08-02 역사적 평가

현재 수정 설계는 **89점이며, 제품 방향과 핵심 도메인 설계는 승인**할 수 있다. 1차 평가의 78점 설계보다 실제 운영 실패를 훨씬 잘 다루며, 최초 생성 정보가 오른쪽 채팅 안에 남는 ChatGPT형 경험도 정확하게 반영했다.

다만 “이 문서 그대로 모든 개발을 시작해도 되는가”에 대한 답은 **아니다**. Sprint 0과 P0-A의 준비 작업은 바로 시작하되, P0-B와 UI 전환 전에 DB 프로파일, operation runner/outbox, timeline projection, revision publication, asset readiness의 5개 ADR을 확정해야 한다.

이 다섯 결정을 반영하면 예상 점수는 **93/100**이며, 그 시점부터는 재설계보다 구현과 검증에 집중하는 것이 맞다.

## 9. Gate 1~8 remediation addendum — 구현 재검토 (2026-08-03)

§8의 89점과 93점 예상치는 당시 설계 심사의 역사적 판단으로 보존한다. 아래 내용은 점수를 다시 산정하는 대신, 확정된 ADR-001~005를 현재 source tree와 대조해 Gate 1~8의 계약과 검증 경계를 기록한 addendum다. authored/template/adaptive의 서로 다른 저장 경계도 기존 평가대로 유지한다. 이 절은 특정 release candidate의 통과 수치나 commit SHA를 주장하지 않는다.

| Remediation gate | 구현으로 해소된 핵심 위험 | 활성화 전 확인할 잔여 위험 |
|---|---|---|
| Gate 1 — SSE/EOF | malformed·unknown frame에서 cursor 미진행, EOF backoff, request/fatal 오류 분리, monotonic high-watermark | 배포 proxy buffering/timeout과 실제 reconnect lag |
| Gate 2 — Delete/cleanup | 승인된 `presentation.delete`; tombstone/redaction·cleanup snapshot·terminal operation/result projection·`operation.result` event의 단일 commit; generation/export late-publication fence와 staged artifact 보상; deleted-tombstone legacy guard; account admission marker·2-pass snapshot·cleanup generation resume; 활성 lease가 없는 retryable dead-letter 관리자 CAS redrive | backlog/`dead_letter`, root rotation·계정 cascade·lost-response replay·관리자 redrive 훈련과 감사 로그 확인 |
| Gate 3 — Rollback/capability | frontend build-time bundle gate와 backend runtime 제어 분리, backend feature-off/kill/cohort 판정의 legacy 복원, timeline read와 command write 분리, read-only 다운로드·두 탭 갱신 | build-time flag 변경의 rebuild/redeploy 확인과 runtime 불건전 시 legacy write 우회 금지 |
| Gate 4 — Snapshot/recovery | f8 immutable attachment snapshot, live `SET NULL`, 당시/현재 상태와 재첨부·retry UX, schema mutation 전 dangling attachment preflight와 무부분변경 회귀; fa1 account deletion fence | backup·staging upgrade와 실제 오래된 DB 표본 점검 |
| Gate 5 — Required CI | workspace E2E 독립 PR job, FastAPI job의 lifecycle required step, local standard runner, timeout/log artifact/cleanup 진단, Windows retained Job Object supervisor와 PID 비신호 stale recovery | Bitbucket/GitHub 저장소 merge-check가 해당 job을 실제 필수 상태 검사로 지정했는지, 실제 Windows recovery run, pytest 전역 temp의 무경고 정리 또는 잠금 process 원인 진단 확인 |
| Gate 6 — API contract | canonical v1 JSON fixture를 backend Pydantic·생성 OpenAPI와 frontend decoder가 함께 검증; versioned receipt/duplicate, 중앙 error/schema registry, server `allowed_actions`, 안전 card projection | 외부 소비자의 flat-field 호환 기간과 unknown-version telemetry |
| Gate 7 — Lifecycle | Alembic-upgraded blank SQLite, 실제 ASGI, 별도 external worker 종료·lease expiry recovery와 실제 SSE disconnect/backfill | PostgreSQL·실제 LLM·production object store/scanner를 결합한 smoke는 별도 release evidence |
| Gate 8 — Rollout/operations | salted cohort·kill switch·shadow, low-cardinality SLO/alerts, Windows PR preflight·link-free staging fallback, axe/ARIA/viewport | 모든 프로세스 metrics 집계와 수동 `workflow_dispatch` Windows package/RC smoke |

### 9.1 Gate 1 — SSE 무손실 판정

malformed/unknown-version frame은 적용하거나 cursor를 전진시키지 않고 reconnect/backfill/rebase한다. 정상 EOF에도 jittered exponential backoff와 상태 전이가 적용되어 busy loop를 만들지 않는다. request/action 오류는 fatal timeline 오류와 분리하고 late/lower sequence·version event는 projection을 되돌리지 못한다. pagination 중 event high-watermark도 단조 증가한다.

SSE endpoint는 access와 cursor를 header 전 검증하고 query `after` 또는 `Last-Event-ID` 이후의 durable event를 DB에서 읽는다. heartbeat와 buffering 방지 header를 사용하며 cursor expiry 시 클라이언트가 snapshot으로 rebase한다.

### 9.2 Gate 2 — 삭제와 durable cleanup 판정

삭제 operation은 승인·revision·lease·fencing을 재검사한 뒤 DB transaction에서 tombstone/redaction, cleanup intent, terminal operation 상태·result/revision/version과 `operation.result` event를 함께 commit한다. 과거 구현에서 tombstone만 commit되고 operation이 `running`으로 남은 split-state도 같은 operation의 삭제 effect를 감지해 terminal result/event로 수렴한다. conversation, message, operation/event history와 attachment snapshot은 보존한다. 삭제된 row는 live presentation guard가 legacy read/write·stream 경로에서 `404`로 숨기며, Chat-first write가 활성화된 legacy mutation은 DB 접근 전에 `409 CHAT_FIRST_COMMAND_REQUIRED`로 막힌다. feature-off/kill-switch 뒤 legacy 경로가 복원돼도 tombstone을 재활성화하거나 다시 삭제하지 않는다. generation/export publication과 일반 operation의 finalizer도 lifecycle을 다시 fence한다. tombstone과 경쟁한 늦은 성공은 `presentation_deleted`로 실패하고 staged immutable artifact를 보상 삭제 또는 durable cleanup으로 수렴시키며 revision이나 `operation.result` event를 되살리지 않는다. 파일 삭제는 commit 뒤 journal worker가 retry하며, missing target은 멱등 성공, traversal/symlink/root 이탈은 비재시도 안전 오류다.

관리자 계정 삭제는 별도 commit의 admission marker로 token을 먼저 무효화하고, 중복 DELETE가 같은 generation을 resume한다. marker lock 뒤 owner asset/artifact tree를 먼저 snapshot·flush하고 재scan한 다음 cascade해 marker 직전 publication도 durable intent에 포함한다. 최종 `204`만 유실된 replay는 account-owned cleanup tombstone이 있을 때만 멱등 성공하고 임의의 존재하지 않는 UUID는 `404`다. sweeper는 chat-first flag 밖의 app lifecycle에서 실행되어 flag-off 중에도 lease/fencing/retry로 cleanup을 계속한다. `dead_letter`이고 활성 cleanup token/lease가 없으며 `last_error.retryable=true`인 row만 관리자 전용 `POST /api/v1/admin/workspace-cleanup/dead-letters/{journal_id}/redrive`가 단일 CAS로 한 번만 `retry_wait`에 되돌리고 retry budget·fence·redrive requester/time을 갱신한다. non-retryable 또는 retryability가 없는 legacy row는 redrive하지 않는다.

### 9.3 Gate 3 — rollback과 capability 판정

`NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED`는 build-time bundle gate이므로 그 값 자체를 바꾸려면 frontend rebuild/redeploy가 필요하다. 해당 bundle이 배포된 뒤에는 backend의 authoritative feature-off, kill switch 또는 cohort 제외 판정이 SharePopover를 포함한 legacy UI를 즉시 복원하고 focus/pageshow/`BroadcastChannel`으로 두 탭에 전파한다. 지원 dialect의 durable timeline/history와 artifact download read는 command write capability와 독립해 계속 가능하다. legacy write는 이 명시적인 backend rollback 판정에서만 복원하며, runtime/profile 불건전이나 command capability 오류는 legacy write로 우회하지 않고 blocked로 처리한다.

### 9.4 Gate 4 — attachment 보존 판정

f8 migration은 attachment의 immutable ID/filename/MIME/size/hash/state/failure snapshot과 live asset `ON DELETE SET NULL`을 추가한다. timeline은 snapshot을 역사적 source로 사용하고 live 상태를 별도 표시하므로 ready/failed/quarantined/expired/deleted 전환이나 원본 삭제가 과거 메시지를 바꾸지 않는다. snapshot column을 추가하기 전 dangling attachment를 preflight하며, live asset이 없으면 이전 Alembic revision/schema를 유지한 채 upgrade를 거부한다. FK를 끈 SQLite dangling fixture 회귀가 이 무부분변경 경계를 검증하고 정보 손실 downgrade도 fail-close한다.

### 9.5 Gate 5 — required CI 판정

workspace browser E2E는 관련 PR workflow의 독립 job과 기본 local runner에 포함되고, production lifecycle test는 FastAPI required job의 별도 step으로 실행된다. 저장소 merge-check 설정에서도 이 job들을 필수 상태 검사로 지정해야 Gate가 실제로 닫힌다. runner는 timeout, Next/FastAPI/Cypress 로그 artifact, 격리 port/temp/DB와 cleanup 진단을 제공한다. Windows child는 retained supervisor가 `CreateProcessW(CREATE_SUSPENDED)`로 만들고 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` Job Object에 할당한 뒤 resume한다. live cleanup은 보유한 supervisor `ChildProcess` handle만 종료하고 exit를 기다리며 PID/PPID 탐색이나 `taskkill`을 사용하지 않는다. runner metadata는 `Get-Process -Id ... .StartTime.ToUniversalTime().ToFileTimeUtc()`의 exact FILETIME decimal birth identity와 executable path를 보존하고, supervisor는 retained parent HANDLE의 `GetProcessTimes`/`QueryFullProcessImageName` 값으로 이를 재검증한다. 정확한 supervisor가 살아 있거나 identity·legacy metadata가 검증 불가능하면 fail-close하고, supervisor가 사라졌거나 PID가 다른 process로 재사용된 경우에만 어떤 PID도 신호하지 않은 채 project recovery를 진행한다. 실제 supervisor create/exit와 retained-handle termination probe는 통과했지만 전체 stale recovery와 candidate RC를 대신하지 않는다. locked-handle 오류는 재시도 후 원인을 보존하고, 선택 capability는 `NOT RUN`으로 명시해 성공으로 계산하지 않는다. runner 경계와 pytest의 전역 numbered-temp/`pytest-current` atexit 정리도 구분한다. 후자에서 `PermissionError`/`WinError 5`가 관찰되면 exit code 0만으로 해소됐다고 판정하지 않고 실제 Windows의 고유 `--basetemp` 재실행과 잠금 process·host ACL/handle 진단을 별도 증거로 요구한다.

live FastAPI+SQLite component 시나리오는 attachment의 immutable `uploaded` snapshot을 확인한 뒤 test-only CAS로 current asset을 `failed`로 만들고 safe failure·generation blocked 투영을 검증한다. 결정론적 backfill gate는 marker opt-in이며 각 spec 종료 시 release해 후속 실행을 격리하고, 일반 성공 경로는 gate 없이 즉시 완료된다.

### 9.6 Gate 6 — API 계약 판정

operation kind는 backend의 단일 immutable catalog가 command/bootstrap admission, destructive 분류, projection과 metrics label을 함께 정의한다. unknown kind는 persistence 전에 `unsupported_operation_type`으로 거부하며, 과거에 저장된 unknown row는 action 없이 non-retryable safe card로만 읽힌다.

`contracts/chat-first-workspace/v1/*.json`이 단일 canonical fixture다. backend는 public Pydantic model의 exact round-trip과 생성 OpenAPI의 response `$ref`/schema를 이 fixture로 검증하고, frontend Node suite는 같은 파일을 실제 decoder에 통과시킨다. command/action 응답은 versioned envelope의 receipt/operation/duplicate/status/timeline을 제공하고 기존 flat field를 호환 기간 동안 유지한다. 중앙 error registry가 안전 envelope와 OpenAPI 응답을 함께 생성하며 strict schema/operation registry와 unknown fallback이 raw 외부 오류를 차단한다. 서버 `allowed_actions`가 card action을 결정하고 actor/시간/대상/revision/activity/undo 사유를 투영하며 Promise/history 실패와 raw key humanization을 검증한다.

### 9.7 보존되는 모드 경계

authored `template.apply`는 기존 generation/checkpoint adapter를 사용하면서 원본 덱 timeline을 operation source로 유지하고 결과에 새 presentation 링크를 돌려준다. 이는 authored/template/adaptive 저장 구조를 하나로 합쳤다는 뜻이 아니다. 각 모드의 checkpoint·revision·canonical template 규칙은 유지하고, 최종 publish만 expected revision, attempt/fencing, immutable artifact 검증을 공유한다.

### 9.8 Gate 7 — 프로세스 경계 판정

`WORKSPACE_OPERATION_WORKER_MODE=external`은 API와 worker ownership을 분리한다. production lifecycle integration test는 Alembic-upgraded blank SQLite, 실제 ASGI app, 별도 external worker의 종료와 marker lease-owner PID 기록, lease expiry 뒤 recovery, duplicate idempotency, stale revision과 실제 SSE disconnect/backfill을 묶어 검증한다. 이 테스트는 marker PID와 OS birth identity의 동일성을 별도로 증명하는 Windows process-supervision 증거가 아니다. `uv run python -m tests.support.workspace_browser_live_harness --port 8765`는 같은 production app lifespan과 blank SQLite를 사용하되 embedded durable runner와 결정론적 edit handler로 browser HTTP/history 경계를 재현한다. partial external write는 별도 결정론적 fault test로 검증한다. 이 경계는 실제 PostgreSQL, LLM, production object store/scanner 또는 배포 topology의 종단 증거가 아니다.

### 9.9 Gate 8 — 출시 제어 판정

rollout은 `CHAT_FIRST_WORKSPACE_KILL_SWITCH`, percent, salt, shadow 변수를 사용하며 owner + presentation 기준의 안정 bucket으로 결정된다. partial rollout에서 presentation scope가 없으면 fail-close한다. capability는 주기적 polling뿐 아니라 focus/visibility/online/pageshow와 `BroadcastChannel`로 갱신된다.

metrics는 durable non-duplicate operation admission과 operation/cleanup/outbox의 allowlisted outcome, rollout/event lag/shadow parity와 silent loss/dead-letter/lease-lost/revision-conflict만 low-cardinality label로 기록한다. API/worker absent·down, shadow silent loss/divergence, telemetry sink failure와 event-lag breach/stall alert를 제공하며 lag-stall의 활동 분모는 `workspace.operation.event`의 `event_delivered` outcome으로 한정한다. SLO endpoint는 환경별 threshold를 평가하지만 exporter가 in-process이므로 multi-process 배포에서는 API와 각 worker를 모두 scrape하고 외부 집계해야 한다.

Windows PR gate는 symlink 권한 사전 점검과 link-free staging/copy fallback, 격리 output, locked-handle cleanup retry를 자동 검사한다. 전체 standalone package/release-candidate smoke는 `workflow_dispatch` 경로이므로 실제 RC 실행 증거로 과장하지 않는다. route gate는 axe와 destructive approval의 bounded assertive announcement, 단일 polite status, tab/disclosure/progress ARIA와 기준 viewport를 검사한다.

### 9.10 최종 승인 기준

설계 승인은 유지하되 전체 기본 활성화 승인은 별도다. 같은 release candidate에서 Gate 1~8 command를 재현하고, runtime/capability, cleanup/outbox backlog, SSE lag/rebase, shadow parity/silent loss, lease/revision conflict SLO를 확인해야 한다. rollback rehearsal은 backend kill switch/feature/cohort 판정으로 즉시 legacy write를 복원하고, build-time frontend flag를 변경한다면 rebuild/redeploy까지 확인해야 한다. f8/fa1 downgrade나 workspace history 삭제는 포함하지 않는다. `workspace_cleanup_journal` sweeper는 chat-first flag 조건 밖의 app lifecycle에서 계속 실행되므로, flag-off 중에도 presentation/account deletion의 durable cleanup을 재시도·복구해야 한다.
