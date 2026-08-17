# Authored-only migration 운영 런북

문서 기준일: 2026-08-13  
적용 범위: `legacy`, `adaptive`, `smart`, `template_v2`, `authored`, `mixed`, `invalid` presentation의 authored-only 전환  
목표: 신규 작성 경로는 authored-only로 제한하면서 기존 presentation의 호환성, 소유자 격리, 원본 불변성 및 즉시 rollback 가능성을 보장한다.

이 문서는 운영 절차와 승인 gate를 정의한다. 이 문서의 명령 예시는 **조회, 검증, dry-run 및 증거 수집 전용**이다. 실제 배포, 외부 provider 호출, 고객 데이터 반출, 운영 DB 파괴, archive 물리 삭제, 기존 PowerPoint 프로세스 attach/종료를 허가하지 않는다. 물리 삭제는 이 런북의 readiness 판정과 별도의 변경 승인 이후에만 수행한다.

## 1. 불변 조건

다음 조건은 단계와 관계없이 항상 성립해야 한다.

- canonical mode resolver가 모든 row를 정확히 하나의 mode로 분류한다. 총 row 수는 `legacy + adaptive + smart + template_v2 + authored + mixed + invalid` 합계와 같아야 한다.
- 신규 presentation/generation/import 요청으로 non-authored mode를 만들 수 없다. 클라이언트가 숨긴 기능도 서버 admission이 다시 차단한다.
- frozen compatibility runtime은 기존 non-authored presentation에 대해 `open`, `preview`, `export`, `download`, `delete`, `upgrade-fork`만 허용한다. 그 밖의 mutation은 안정적인 오류 코드와 함께 거부한다.
- upgrade-fork는 원본 presentation과 source/assets/original PPTX를 수정하지 않고 새 authored presentation ID를 만든다.
- source, runtime, artifact digest와 signed archive manifest를 검증할 수 있어야 한다. owner가 다른 artifact, manifest, fork 또는 export는 존재 여부도 노출하지 않는다.
- `mixed`와 `invalid`는 자동 변환, 자동 publish, 일반 canary 대상에 포함하지 않는다. 격리 후 사람이 판정한다.
- rollout 비율을 낮추거나 kill switch를 켜도 기존 read/export와 이미 생성된 archive는 유지한다.
- 어떤 단계에서도 census row, original PPTX, checkpoint, archive 또는 audit receipt를 덮어쓰거나 삭제하지 않는다.

## 2. 역할과 승인

| 역할 | 책임 | 단계 승격 승인 |
|---|---|---|
| Incident commander | 단계 시작/중단, kill/rollback 선언, 타임라인 유지 | 필수 |
| Migration operator | census, inventory, flag 변경안, drain, evidence 수집 | 필수 |
| Service owner | admission/frozen runtime/export 상태 확인 | 필수 |
| Data owner | archive completeness, digest/signature, owner isolation 확인 | 25% 이상 필수 |
| Security reviewer | 서명 키 참조, owner isolation, quarantine 검토 | 25% 이상 필수 |
| Release manager | rolling deploy 호환성, 두 release 무회귀 기록 | 50% 이상 필수 |

한 사람이 operator와 incident commander를 동시에 맡지 않는다. 승인자는 evidence bundle을 직접 확인하고 체크리스트에 이름, 시각, release ID를 남긴다.

## 3. Evidence bundle 규격

모든 실행은 immutable `run_id`를 사용한다. 예: `authored-only-20260813T090000Z-r1`. evidence는 고객 원문을 포함하지 않고 ID, count, digest, status, latency, 오류 코드만 포함한다.

필수 파일:

| 파일 | 필수 내용 |
|---|---|
| `run-metadata.json` | run ID, UTC 시각, git SHA, build/release ID, schema revision, operator, 대상 환경 |
| `census.json` | resolver version/digest, mode별 count, total/reconciled count, unresolved count |
| `inventory.json` | presentation별 owner-scoped source/assets/original PPTX/active job/checkpoint/archive checksum 존재 여부와 digest |
| `archive-verification.json` | manifest signature 결과, source/runtime/artifact digest 결과, owner isolation probe 결과 |
| `admission-probes.json` | 각 legacy create/mutation 거부와 authored create 허용 결과 |
| `compatibility-probes.json` | open/preview/export/download/delete/upgrade-fork 결과와 forbidden mutation 결과 |
| `quarantine.json` | mixed/invalid 개수, reason code, reviewer 상태; 고객 payload 제외 |
| `drain.json` | active/queued/leased/retrying job count의 시작값과 종료값, 강제 취소 0건 증거 |
| `slo.json` | window, sample size, 성공률, p95/p99, 오류율, hard defect count |
| `rollback-rehearsal.json` | 시작/완료 시각, 목표/실제 단계, 복구 시간, digest/owner 검증 결과 |
| `approval.md` | 단계별 체크리스트, 승인/기각, 서명자와 UTC 시각 |

Evidence bundle 자체에도 SHA-256 manifest를 생성하고 CI 또는 승인된 내부 서명기로 서명한다. 서명 private key, credential, customer source는 bundle에 넣지 않는다. 운영 증거의 저장 위치와 retention은 조직의 내부 정책을 따르며 외부 위치로 업로드하지 않는다.

## 4. 사전 census와 inventory

운영 증거는 식별자와 고정 source revision 없이 생성할 수 없다. 다음 명령은
조회 전용이며 `run_metadata`에 release/build/git/Alembic revision, 공개 flag 값과
rollout salt의 SHA-256만 기록한다. salt 원문과 고객 콘텐츠는 metadata에 남기지
않는다.

```powershell
uv run --directory servers/fastapi python scripts/authored_only_census.py `
  --database-url $env:DATABASE_URL `
  --run-id authored-only-20260813T090000Z-r1 `
  --release-id release-2026.08.13 `
  --build-id build-42 `
  --git-commit b387a34735482d4f6dfbf2b955fbf19fd0d2315e `
  --output evidence/census.json --pretty
```

`evidence_sha256`는 report 본문을 canonical JSON으로 다시 계산해 검증한다.

canary SLO 집계는 원문 콘텐츠가 없는 strict JSON으로 저장한 뒤 다음 read-only
판정기를 실행한다. exit 0은 `advance|complete`, exit 2는 `hold|kill|rollback`이며
판정기는 flag나 배포 상태를 직접 변경하지 않는다.

```powershell
uv run --directory servers/fastapi python scripts/authored_only_canary.py `
  --input evidence/canary-window.json `
  --output evidence/canary-decision.json --pretty
```

### 4.1 안전한 실행 원칙

1. 운영 DB는 read-only credential 또는 transaction read-only session으로 조회한다.
2. census CLI는 canonical resolver와 동일한 코드 경로를 사용해야 하며 별도 SQL 휴리스틱으로 mode를 재구현하지 않는다.
3. 먼저 fixture DB에서 실행해 100% reconciliation을 확인하고, staging snapshot, production read replica 순으로 진행한다.
4. report 생성 중 digest 불일치, row 누락 또는 resolver exception이 하나라도 있으면 rollout을 시작하지 않는다.
5. archive 작성은 별도 승인된 절차이며 census 단계에서는 archive 상태와 checksum만 inventory한다.

### 4.2 필수 census 판정

- `input_rows == classified_rows == sum(mode_counts)`
- `unresolved_rows == 0`; resolver가 판단할 수 없는 row는 `invalid`로 명시적으로 분류되어야 하며 누락하면 안 된다.
- 모든 presentation은 owner ID와 결합된 stable identifier로 보고한다. 이메일, 이름, prompt, slide text는 보고하지 않는다.
- 각 mode에 대해 source, assets, original PPTX, active job, checkpoint, archive manifest의 `present/missing/not_applicable` 상태를 기록한다.
- source/runtime/artifact SHA-256 digest는 byte source와 versioned canonicalization 규칙을 명시한다. 서로 다른 canonicalizer의 digest를 직접 비교하지 않는다.
- dangling asset, owner mismatch, invalid checksum, unsigned manifest는 count와 reason code로 별도 집계한다.

### 4.3 시작 gate

- [ ] fixture DB의 전체 row가 100% reconciliation되었다.
- [ ] production 대상 row 수와 census 합계가 일치한다.
- [ ] resolver version과 digest가 evidence에 기록되었다.
- [ ] source/assets/original PPTX/job/checkpoint/archive inventory가 누락 없이 생성되었다.
- [ ] owner mismatch가 0건이다.
- [ ] `mixed`/`invalid`가 quarantine queue에 연결되었고 일반 rollout 대상에서 제외되었다.
- [ ] archive checksum 또는 signature 실패 건은 canary 대상에서 제외되었다.

## 5. Deterministic owner bucketing

Rollout 단위는 presentation이 아니라 **owner**다. 같은 owner의 모든 요청이 같은 단계에 들어가야 교차-mode 혼합과 지원 혼선을 막을 수 있다.

표준 bucketing:

```text
key       = rollout_salt_version + ":" + stable_owner_id
digest    = HMAC-SHA256(internal_rollout_secret, UTF8(key))
bucket_bp = unsigned_big_endian(digest[0:8]) mod 10000
selected  = bucket_bp < rollout_percent * 100
```

- `stable_owner_id`는 인증 시스템의 immutable tenant/user subject를 사용한다. 이메일, 표시 이름, DB row 순번, presentation ID, Python/JavaScript 기본 `hash()`는 금지한다.
- salt version과 secret reference는 단계 전체에서 고정한다. secret 자체를 로그나 evidence에 기록하지 않는다.
- 비율 확대는 단조롭다. 1% owner 집합은 5% 집합에 포함되고, 5%는 25%에 포함된다.
- 내부 단계는 percent 계산 전에 승인된 internal owner allowlist와 테스트 tenant만 선택한다. internal owner가 customer owner와 연결된 경우 제외한다.
- 명시적 denylist, quarantine, compliance hold, kill switch가 bucketing보다 우선한다.
- owner가 병합/분리되면 identity migration receipt를 남기고 다음 rollout 단계 전까지 기존 bucket 결정을 고정한다.
- API, worker, UI capability manifest는 동일한 server 결정 결과를 사용한다. UI가 자체적으로 bucket을 다시 계산하지 않는다.

단계 시작 전에 서로 다른 runtime 두 개에서 같은 고정 fixture owner 100개가 동일 bucket을 내는 golden test를 실행한다.

## 6. Canary 단계

순서는 `internal → 1% → 5% → 25% → 50% → 100%`이며 건너뛰지 않는다. 각 단계는 새 owner에게 authored-only admission을 적용하지만 기존 non-authored deck은 frozen runtime으로 계속 읽고 내보낼 수 있다.

| 단계 | 대상 | 최소 관측 | 추가 승격 조건 |
|---|---:|---|---|
| internal | 승인된 내부 owner | 24시간 및 대표 fixture 전체 | 수동 QA, owner isolation, kill/rollback rehearsal 통과 |
| 1% | deterministic owner 1% | 24시간 및 충분한 read/export 표본 | hard defect 0, admission bypass 0 |
| 5% | owner 5% | 48시간 | archive/digest 오류 0, quarantine 누락 0 |
| 25% | owner 25% | 72시간 | security/data owner 승인, peak traffic window 포함 |
| 50% | owner 50% | 7일 | 이전 release와 rolling compatibility 통과 |
| 100% | 모든 eligible owner | 7일 | SLO 연속 충족, rollback 경로 유지, 삭제 gate와 분리 |

`최소 관측`은 시간만 채우면 통과하는 조건이 아니다. 대표 mode/owner/export 형식 표본이 없으면 기간을 연장한다. 100% rollout은 compatibility runtime이나 archive를 삭제한다는 뜻이 아니다.

각 단계 절차:

1. 직전 단계 evidence와 승인자를 확인한다.
2. 새 `run_id`로 census/inventory를 다시 생성하고 이전 결과와 diff한다.
3. kill switch가 동작하고 rollback target이 사용 가능한지 read-only probe한다.
4. target percent와 salt version을 변경안으로 검토한다. 실제 flag 반영은 정식 변경 절차에서 별도 승인한다.
5. admission probe로 authored create 성공, non-authored create 거부, frozen mutation 거부를 확인한다.
6. owner bucket 분포와 단조 포함 관계를 확인한다.
7. SLO와 hard defect를 관측하고 promotion checklist를 작성한다.
8. incident commander, service owner와 단계별 필수 승인자가 서명한 뒤에만 다음 단계로 이동한다.

## 7. SLO, kill 조건, rollback

### 7.1 승격 SLO

| 지표 | 승격 기준 | 분모/주의점 |
|---|---:|---|
| 기존 deck `open + preview + export` 성공률 | 각각 및 합산 99.9% 이상 | 사용자 취소, 명시적 unsupported는 별도 reason으로 분리 |
| 기존 deck read + export 성공률 | 99.9% 이상 | physical deletion readiness에도 동일 기준 적용 |
| authored publish 성공률 | 99.0% 이상 | canary 7일 기준; transient retry 후 최종 상태 |
| PPTX export 성공률 | 99.5% 이상 | authored/frozen을 mode별로도 분리 |
| PowerPoint repair/corrupt package | 0건 | 1건도 hard kill |
| owner isolation 위반 | 0건 | 존재 여부 노출 포함, 1건도 hard kill |
| original/source mutation 또는 digest drift | 0건 | 1건도 hard kill |
| legacy create 또는 forbidden mutation 성공 | 0건 | 1건도 hard kill |
| master furniture drift/CJK golden clipping | 0건 | 대표 golden corpus |
| rollback 복구 시간 | 30분 이내 | 탐지부터 검증 완료까지 |

표본 수, window, retry policy와 reason exclusion을 `slo.json`에 고정한다. 작은 표본에서 100%가 나와도 자동 승격하지 않는다.

### 7.2 즉시 kill 조건

다음 중 하나면 새 authored-only admission/upgrade-fork 유입을 즉시 중단하고 현재 단계를 보존한다.

- owner isolation, archive signature, source/original immutability 위반
- corrupt PPTX, 원본 overwrite, irreversible data loss 가능성
- 신규 non-authored create 성공 또는 frozen runtime forbidden mutation 성공
- `mixed`/`invalid`가 quarantine을 우회해 자동 publish됨
- bucket 불일치로 동일 owner가 서로 다른 경로를 사용함
- SLO가 두 연속 5분 window에서 기준 미달이거나 error budget이 단계 budget을 소진함
- active job이 새 runtime/old runtime 사이에서 인계되지 않거나 checkpoint digest가 달라짐
- rollback probe 또는 archive restore verification 실패

보안/무결성 hard kill은 window를 기다리지 않는다. 단순 latency 악화는 먼저 새 유입을 멈추고 in-flight job을 안전하게 drain한다.

### 7.3 Rollback 순서

1. incident commander가 incident ID, 최초 증상, 대상 release/percent를 기록한다.
2. global kill switch로 **새 generation과 upgrade-fork admission만** 거부한다. 기존 open/export까지 끄지 않는다.
3. rollout percent를 마지막 known-good 단계로 낮춘다. salt version은 바꾸지 않는다.
4. 새 job lease를 중단하고 in-flight job을 drain한다. 임의 cancel, checkpoint 삭제, queue purge는 금지한다.
5. rolling deploy 호환 matrix에서 검증된 이전 app release로 되돌릴 변경안을 실행한다. schema downgrade는 하지 않는다.
6. original presentation ID와 archive manifest를 기준으로 read/preview/export를 검증한다.
7. canary에서 생성된 authored fork는 삭제하거나 원본에 덮어쓰지 않는다. 접근을 중단하고 상태를 보존한다.
8. digest/signature/owner isolation/admission probe를 다시 실행한다.
9. 30분 이내 서비스 복구 여부와 미복구 항목을 기록하고, 원인 분석 전 재승격하지 않는다.

## 8. Malformed/mixed quarantine

`mixed`는 복수 canonical mode marker 또는 서로 다른 runtime artifact가 공존하는 경우, `invalid`는 resolver contract 위반, 필수 source 부재, checksum/signature 오류 등으로 분류한다.

- quarantine entry는 presentation ID, owner-scoped opaque ID, resolver version, reason code, 발견 시각, source/archive digest만 담는다.
- 원문 content, prompt, asset URL, credential을 quarantine log에 복제하지 않는다.
- 일반 open은 안전한 immutable artifact가 검증된 경우에만 허용하고, preview/export는 검증된 frozen artifact로 제한한다.
- mutation과 upgrade-fork는 reviewer가 source authority와 owner를 확인하기 전까지 거부한다.
- 자동으로 mode marker를 수정하거나 newest artifact를 authority로 선택하지 않는다.
- 판정 결과는 `confirmed_mode`, `source_authority`, `reviewer`, `evidence_digest`를 포함한 append-only receipt로 남긴다.
- quarantine 해제 후에도 original ID는 그대로 두고, 변환은 새 authored presentation ID로 fork한다.

Quarantine reason code의 개수 합은 census의 `mixed + invalid`와 일치해야 한다.

## 9. Active job drain

Drain은 process 종료나 queue 삭제가 아니다.

1. non-authored 신규 admission이 차단되었는지 확인한다.
2. old/new worker 모두가 읽을 수 있는 checkpoint schema와 job state를 matrix test로 확인한다.
3. old worker의 신규 lease 획득을 중단하되 기존 lease는 정상 완료 또는 versioned checkpoint 저장까지 허용한다.
4. `queued`, `leased`, `running`, `retrying`, `finalizing`을 mode/runtime/release별로 집계한다.
5. stuck threshold를 넘은 job은 자동 재작성하지 말고 incident queue로 격리한다.
6. `active_non_authored_jobs == 0`, `unowned_leases == 0`, `checkpoint_digest_mismatch == 0`을 확인한다.
7. 완료 job의 publish target이 원본 non-authored ID를 수정하지 않았음을 확인한다.

Drain 완료 조건:

- [ ] 신규 non-authored job 유입 0건
- [ ] active/leased/retrying non-authored job 0건
- [ ] checkpoint 없는 강제 종료 0건
- [ ] queue purge 0건
- [ ] owner mismatch와 duplicate publish 0건
- [ ] 시작/종료 count와 terminal state count가 reconciliation됨

## 10. Rolling deploy compatibility matrix

Schema와 wire contract는 expand/contract 방식으로 배포한다. additive schema를 먼저 배포하고, 모든 app release가 새/옛 데이터를 읽는 것을 확인한 뒤 writer를 전환한다. rollback 기간에는 column/table/enum 값을 삭제하거나 좁히지 않는다.

| Reader/API | Worker | DB/schema | 필수 검증 |
|---|---|---|---|
| N-1 | N-1 | expanded N | 기존 open/preview/export, 신규 field 무시, 안정 오류 |
| N | N-1 | expanded N | N이 만든 job을 N-1이 잘못 lease하지 않음, version guard |
| N-1 | N | expanded N | 기존 API/client 응답 호환, capability field additive |
| N | N | expanded N | authored admission, frozen runtime, upgrade-fork, archive receipt |
| rollback N-1 | mixed N/N-1 | expanded N | schema downgrade 없이 known-good read/export |

각 조합에서 다음 fixture를 모두 사용한다.

- `legacy`, `adaptive`, `smart`, `template_v2`, `authored`
- `mixed`, `invalid` quarantine
- archive 있음/없음, original PPTX 있음/없음, checkpoint 있음/없음
- owner A 정상 접근, owner B 교차 접근 거부
- active job의 old→new 및 new→old handoff/version rejection

Pass 조건은 create/mutate 차단, open/preview/export/download, upgrade-fork의 새 ID/원본 digest 불변, rollback read/export, 안정 오류 코드가 release 조합마다 동일한 것이다. N-1이 이해하지 못하는 job/runtime을 소비하는 것은 실패다.

## 11. Rollback rehearsal

실제 고객 데이터나 배포를 사용하지 않고 production-equivalent fixture와 내부 owner로 rehearsal한다.

1. known-good release ID, schema revision, flag snapshot, census/archive digest를 기록한다.
2. internal bucket에 authored create와 기존 mode별 open/export fixture를 만든다.
3. 의도적으로 test-only SLO probe를 실패시켜 경보와 incident 선언 시간을 기록한다.
4. kill switch, percent 복원, lease stop, drain 순서를 실행한다.
5. N-1 app + expanded N schema 조합에서 frozen read/export를 검증한다.
6. authored upgrade-fork를 비활성화한 뒤 original ID/source/PPTX digest가 변하지 않았는지 확인한다.
7. archive manifest signature와 owner isolation negative probe를 재실행한다.
8. 탐지부터 검증 완료까지 30분 이내인지 확인한다.
9. 생성된 fixture를 삭제하지 말고 retention 정책에 따른 격리 상태로 남겨 evidence를 보존한다.

Rehearsal은 internal 시작 전, 25% 전, 100% 전, release N 변경 시 반복한다.

## 12. Physical deletion readiness

이 절은 **판정만** 정의하며 물리 삭제를 수행하지 않는다. 아래 조건을 하나라도 충족하지 못하면 compatibility code, DB column/table, source, archive, original PPTX를 유지한다.

| Gate | 필수 판정 | 증거 |
|---|---|---|
| 참조 0 | runtime, API, worker, UI, queue, checkpoint, export, audit 및 retained fork가 삭제 대상에 참조 0건 | static scan + DB/reference census + runtime access log |
| 두 release 무회귀 | 연속된 두 production release에서 non-authored 신규 참조/생성/회귀 0건 | release별 signed census와 compatibility test |
| archive 100% | eligible presentation 전부 signed archive manifest와 source/runtime/artifact digest 검증 성공 | archive-verification report; missing/invalid 0건 |
| read/export 99.9% | retention window 동안 기존 deck read와 export가 각각/합산 99.9% 이상 | mode/release별 SLO report |
| active job 0 | non-authored active/leased/retrying/finalizing job과 incompatible checkpoint 0건 | drain report |
| quarantine 0 unresolved | mixed/invalid가 reviewer 판정 또는 보존 예외로 모두 종결 | append-only quarantine receipts |
| rollback rehearsal | 30분 이내 복구 및 owner/digest 검증 | rehearsal report |
| retention/hold | retention SLA 경과, legal/compliance hold 없음 | 승인 기록 |

`참조 0`은 코드 문자열 검색만 의미하지 않는다. DB foreign key/JSON refs, object store locator, signed manifest, queued payload, checkpoints, audit/outbox, download URL과 지원 도구까지 포함한다. archive 100%의 분모와 제외 사유는 census total에서 추적 가능해야 하며, 단순히 archive job 성공률로 대체할 수 없다.

삭제 readiness 승인 후에도 실제 deletion plan은 별도 문서, 별도 승인, dry-run target manifest, 복구 시나리오를 요구한다. 이 런북 실행자는 deletion command를 실행하지 않는다.

### 12.1 Census deletion manifest와 blocker 규칙

`authored_only_census.py`의 `deletion_readiness`는 삭제 대상 후보를 판정하기 위한
읽기 전용 manifest이다. 각 `presentation_id`에는 `eligible`과 `blockers`를 기록하며,
다음 blocker 중 하나라도 있으면 해당 행은 삭제 후보가 아니다.

- `non_authored_compatibility_required`: 기존 legacy/adaptive/smart/template_v2/mixed/invalid 호환 대상
- `archive_manifest_missing` 또는 `archive_digest_unverified`: signed archive와 digest 검증 미완료
- `active_job_drain_incomplete`: queued/running/retrying/finalizing 등 작업 잔존
- `checkpoint_retention_present`: checkpoint가 남아 복구 가능성을 보존해야 함
- `owner_isolation_failed`: 소유자 격리 검증 실패
- `inventory_checksum_unverified`: source/artifact/original PPTX/asset checksum 불일치·누락·읽기 실패

`deletion_readiness.status`는 이 manifest가 비어 있지 않거나 승인되지 않은 동안
`blocked`이며, `physical_deletion_performed`는 항상 `false`여야 한다. `eligible` 행이
있더라도 별도 deletion change request, dry-run manifest, 복구 rehearsal, retention/legal
hold 확인과 승인 서명이 완료되기 전에는 삭제하지 않는다. census의 출력 자체가 삭제
승인이나 삭제 명령으로 해석되어서는 안 된다.

## 13. 단계별 evidence checklist

### 매 단계 공통

- [ ] run metadata에 git/build/schema/flag/salt version이 기록됨
- [ ] canonical census가 100% reconciliation되고 이전 단계와 diff 검토됨
- [ ] inventory와 archive signature/digest 검증 완료
- [ ] owner bucket golden test와 분포/단조성 검증 완료
- [ ] authored create 성공, non-authored create와 forbidden mutation 거부
- [ ] frozen open/preview/export/download와 upgrade-fork 성공
- [ ] upgrade-fork가 새 presentation ID를 사용하고 original digest 불변
- [ ] cross-owner open/export/fork/download가 안정적으로 거부됨
- [ ] mixed/invalid quarantine 합계가 census와 일치
- [ ] active job drain 또는 compatibility 확인 완료
- [ ] SLO window/sample/error exclusion 기록 완료
- [ ] hard defect 0건, kill/rollback target 정상
- [ ] 필요한 역할의 승인 서명 완료

### 100% 승격 전

- [ ] internal→1→5→25→50 단계 evidence가 모두 보존됨
- [ ] 50%에서 7일 연속 SLO 충족
- [ ] rolling deploy matrix 전체 통과
- [ ] rollback rehearsal 30분 이내 통과
- [ ] 기존 compatibility runtime의 read/export flag가 admission flag와 독립적임
- [ ] 100%가 physical deletion 승인이 아님을 변경 기록에 명시

### Physical deletion readiness 판정 전

- [ ] 참조 0
- [ ] 연속 두 release 무회귀
- [ ] archive 100% 및 signature/digest 성공 100%
- [ ] read와 export 각각/합산 99.9% 이상
- [ ] active job/checkpoint blocker 0
- [ ] unresolved quarantine 0
- [ ] retention/hold 승인
- [ ] 별도 deletion change request가 존재하며 이 런북에서는 실행하지 않음

## 14. 실패 보고 형식

실패 보고는 다음을 구분한다.

- **확인 사실:** 실행한 probe, timestamp, release, count, 오류 코드, digest 결과
- **실행 결과:** pass/fail, kill 여부, rollback 단계, 복구 시간
- **추론:** 가능한 원인과 이를 뒷받침하는 evidence; 확정 원인처럼 쓰지 않음
- **미검증:** 표본 부족, 접근 불가, fixture 부재, 관측 window 미충족
- **기존 실패/신규 실패:** rollout 전 baseline에도 있던 실패와 이번 release에서 새로 발생한 실패

보고서에는 고객 원문이나 secret을 넣지 않는다. blocker가 남으면 현재 단계에서 멈추고 percent를 확대하지 않는다.
