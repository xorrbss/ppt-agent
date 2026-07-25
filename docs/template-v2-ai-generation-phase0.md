# Template V2 AI 생성 통합 Phase 0 결정서

- 상태: **확정(Phase 0 기준)**
- 대상 브랜치: `codex/template-v2-generation-convergence`
- 적용 범위: 기존 `/api/v1` 생성 경로에 Template V2 생성을 추가하기 전의 제품·API·운영 계약
- 기준 원칙: 기존 adaptive, authored HTML, `korean-biz`, 한국어 UI, Windows 내보내기 계약을 깨지 않는 additive 통합

## 1. 목적

이 문서는 Template V2의 구조화된 레이아웃을 AI 프레젠테이션 생성 흐름에서 사용할 때 필요한 제품 결정을 먼저 고정한다. 구현 편의를 위해 기존 전략을 재해석하거나 서로 암묵적으로 변환하지 않는다.

Phase 0의 결과물은 생성기 자체가 아니라 다음 구현 단계가 따라야 하는 단일 계약이다.

- 어떤 생성 경로가 새로 추가되는가
- 기존 생성 경로와 무엇을 공유하고 무엇을 공유하지 않는가
- 요청에서 새 경로를 어떻게 명시하는가
- 템플릿 revision과 생성 provenance를 어떻게 고정하는가
- 어떤 상황에서 실패하고 어떤 상황에서도 자동 강등하지 않는가
- 출시, 검증, 중단 및 롤백의 기준은 무엇인가

## 2. 범위와 비범위

### 2.1 Phase 0 범위

- Template V2 생성의 제품 전략과 API discriminator
- Stage A/hybrid 실행 경계
- template revision 고정과 provenance 저장 계약
- deterministic OOXML compiler 및 선택적 LLM vision 경계
- default-OFF, exact allowlist, fail-closed 정책
- 기존 생성 모드, 한국어 UI, Windows 실행 계약의 회귀 방지
- 테스트, canary, 롤백 및 수용 조건

### 2.2 Phase 0 비범위

- `/api/v2` 신설
- 기존 adaptive 또는 authored presentation의 자동 변환
- `korean-biz` 제거 또는 Template V2로의 즉시 이관
- authored HTML을 `slide.ui`로 자동 변환
- authored-hybrid exporter를 Template V2 compiler로 재사용
- 멀티테넌시, 조직·워크스페이스 RBAC, 공유 권한 모델 신설
- LLM vision을 기본 경로로 활성화
- 기존 presentation 데이터의 일괄 migration
- 전체 Konva editor의 통합 또는 upstream 대규모 병합

## 3. 규범 용어

이 문서의 **MUST**, **MUST NOT**, **SHOULD**, **MAY**는 구현 및 운영 수용 조건을 뜻한다.

| 용어 | 정의 |
| --- | --- |
| Template V2 | 엄격한 구조화 schema와 `slide.ui`를 사용하는 일반 편집·내보내기 경로 |
| adaptive | 기존 의미 기반 자동 구성 생성 전략 |
| authored HTML | AI가 HTML/PNG 결과를 생성하는 기존 authored 전략 |
| authored-hybrid | authored HTML의 기존 편집 가능 요소와 fidelity를 보존하는 전용 내보내기 전략 |
| Stage A/hybrid | AI 의미 생성과 deterministic Template V2 compilation을 조합하는 신규 생성 계획 |
| compiler | 검증된 구조화 콘텐츠와 고정된 Template V2 snapshot을 OOXML로 변환하는 구성 요소 |
| LLM vision | 명시적으로 선택할 때만 레이아웃 매핑 또는 QA 후보를 제안하는 외부/선택적 provider |
| revision | 생성 요청이 사용해야 하는 불변 템플릿 버전 번호 |
| provenance | 입력, 템플릿, 생성기, compiler 및 선택적 provider의 재현·감사 메타데이터 |

`hybrid`라는 단어는 두 곳에서 다른 의미로 사용될 수 있으므로 다음을 강제한다.

- **Stage A/hybrid**는 “AI 의미 생성 + deterministic Template V2 compiler” 조합이다.
- **authored-hybrid**는 기존 authored HTML 전용 export strategy이다.
- Template V2 생성은 authored-hybrid executor를 호출하거나 그 capability를 상속하면 안 된다.

## 4. 확정 제품 결정

| 번호 | 결정 | Phase 0 확정 내용 | 보호 장치 |
| --- | --- | --- | --- |
| D1 | 도입 방식 | **Stage A/hybrid로 단계적 도입** | API는 `strategy="template_v2"`로 명시하고 내부 generation profile을 별도 기록 |
| D2 | 기존 전략 | **adaptive와 authored를 독립 유지** | 암묵적 업그레이드·fallback·executor 공유 금지 |
| D3 | `korean-biz` | **현재 경로 유지, 이관은 후속 작업** | 동등성 증거와 별도 승인 전 제거·재지정 금지 |
| D4 | 플랫폼 계약 | **한국어 UI와 Windows 계약 유지** | 한국어 E2E, Windows 실제 export/fidelity gate 필수 |
| D5 | 인증 범위 | **현재 인증 범위 유지** | Phase 0에서 tenant/RBAC/소유권 모델을 임의 확장하지 않음 |
| D6 | 출시 정책 | **기본 OFF + exact allowlist** | backend가 권위자이며 wildcard와 UI 단독 활성화 금지 |
| D7 | compiler | **deterministic OOXML 우선, LLM vision 선택적** | provider가 validator를 우회하거나 무기록 fallback할 수 없음 |

### 4.1 D1 — Stage A/hybrid

Stage A의 기본 실행 흐름은 다음과 같다.

```text
검증된 생성 요청
  -> 기존 의미/아웃라인 생성 인터페이스
  -> 고정된 Template V2 snapshot에 대한 slot·layout binding
  -> strict schema 및 capability 검증
  -> deterministic OOXML compilation
  -> 원자적 저장
  -> 기존 Template V2 editor/export
```

여기서 AI는 의미 콘텐츠와 제한된 binding 후보를 생성할 수 있지만, 최종 layout tree와 OOXML은 deterministic compiler가 만든다. 생성된 `slide.ui`는 schema와 capability 검증을 통과한 경우에만 저장한다.

`generation_profile`의 첫 값은 `staged-a-hybrid-v1`로 고정한다. 외부 API의 전략 discriminator는 `strategy="template_v2"`이며, `hybrid`를 공개 strategy 값으로 추가하지 않는다. 이렇게 해야 authored-hybrid와의 오해와 잘못된 dispatch를 방지할 수 있다.

### 4.2 D2 — adaptive와 authored의 독립 유지

- discriminator가 없는 기존 요청은 지금과 동일하게 해석해야 한다.
- adaptive는 Template V2의 fallback이 아니다.
- authored HTML은 Template V2의 source format 또는 fallback이 아니다.
- Template V2는 `GenerationStrategy.TEMPLATE_V2`,
  `EditorCapability.TEMPLATE_V2`,
  `ExportStrategy.TEMPLATE_V2_GENERAL`의 조합만 사용할 수 있다.
- authored HTML은 기존 `AUTHORED_HTML`과 `AUTHORED_HYBRID` 조합을 유지한다.
- 저장된 정체성과 요청 strategy가 불일치하면 dispatch 전에 실패한다.
- 신규 코드가 `layout is None`, `html_content` 존재 여부 같은 휴리스틱만으로 모드를 추론하면 안 된다.

### 4.3 D3 — `korean-biz` 유지와 후속 이관

`korean-biz`는 현재 선택·생성·편집·내보내기 계약을 그대로 유지한다. Phase 0 또는 Stage A 구현에서 이를 Template V2의 별칭으로 바꾸거나 제거하지 않는다.

향후 이관은 별도 ADR과 release gate가 필요한 독립 작업이다. 최소한 다음 증거가 있어야 한다.

- 대표 한국어 문서 golden corpus에서 레이아웃 및 폰트 동등성
- 기존 요청과 저장 데이터의 backward compatibility
- Windows 실제 PowerPoint/LibreOffice 또는 승인된 renderer 기반 fidelity 비교
- 사용자가 명시적으로 선택하거나 되돌릴 수 있는 migration 방식
- 실패 시 기존 `korean-biz`로 데이터 손실 없이 복귀하는 절차

이 증거와 제품 승인 전에는 `korean-biz`를 deprecated로 표시하지 않는다.

### 4.4 D4 — 한국어 UI와 Windows 계약

- 신규 UI의 기본 사용자 문구, 오류, 접근성 label, 진행 상태는 한국어를 제공해야 한다.
- 기존 한국어 문구와 선택 흐름을 영어 전용으로 회귀시키면 안 된다.
- 템플릿 이름, 사용자 입력, 파일 이름, 노트, 폰트 family의 Unicode/한글 round-trip을 보장해야 한다.
- Windows는 first-class runtime/export 대상이다.
- 경로 separator, `.exe` converter 탐색, 임시 파일 잠금, 폰트 대체, ZIP/OOXML 처리에서 Windows 전용 테스트를 유지한다.
- Linux-only 성공을 Windows 출시 근거로 간주하지 않는다.

### 4.5 D5 — 현재 인증 범위 유지

Phase 0은 현재 애플리케이션의 session authentication과 기존 접근 제어 범위를 그대로 사용한다. 신규 Template V2 생성 엔드포인트만 인증을 우회해서는 안 되며, 반대로 이 작업에서 조직, tenant, workspace, RBAC 또는 공유 링크 권한 모델을 새로 정의하지 않는다.

멀티사용자·멀티테넌트 기능은 별도 threat model, 데이터 소유권 migration, row-level authorization, 감사 및 삭제 정책을 포함한 후속 ADR로 다룬다. 그 전까지 현재 인증 범위를 넘어서는 격리를 주장하지 않는다.

### 4.6 D6 — default-OFF + exact allowlist

신규 생성 admission은 서버에서 아래 두 조건을 모두 만족할 때만 허용한다.

1. `ENABLE_TEMPLATE_V2=true`
2. 요청한 `template_v2_id`가 `TEMPLATE_V2_TEMPLATE_ALLOWLIST`의 정확한 항목과 일치

정책은 fail-closed여야 한다.

- 환경 변수가 없거나 boolean parsing에 실패하면 OFF로 간주한다.
- 비어 있는 allowlist는 전체 차단을 뜻한다.
- `*`, prefix, glob, regex, 부분 일치, 대소문자 보정은 지원하지 않는다.
- frontend feature flag는 UI 노출만 제어하며 backend admission을 대신하지 못한다.
- create, retry, confirm, regenerate 등 새 결과를 만드는 모든 mutation이 같은 정책을 확인한다.
- flag OFF 후에도 기존에 저장된 Template V2 결과의 읽기와 안전한 export는 유지한다.

### 4.7 D7 — compiler 우선순위

이번 vertical slice의 compiler mode는 `deterministic-native-ui`이다. 기존 native UI
생성 경로를 고정된 Template V2 snapshot에 연결하는
`presenton-template-v2-generation-adapter`를 사용한다. 전용 deterministic OOXML
compiler는 golden fixture와 함께 후속 단계에서 도입한다.

- 동일한 정규화 입력, template snapshot, compiler version이면 동일한 구조적 OOXML 결과를 만들어야 한다.
- 시간, 임의 UUID, ZIP member ordering 등 비결정적 값은 정규화하거나 provenance에서 분리한다.
- compiler는 허용된 element type과 capability만 처리한다.
- 지원되지 않는 schema, element, relationship 또는 media는 명시적 오류로 종료한다.

`llm-vision`은 선택적 보조 모드다.

- 요청자가 명시하고 server policy가 허용한 경우에만 호출한다.
- slot/layout 후보 제안 또는 QA 결과를 낼 수 있지만 최종 schema validator와 deterministic compiler를 우회할 수 없다.
- provider, model, prompt version, 입력 artifact digest, 응답 digest, timeout 및 결과 상태를 provenance에 기록한다.
- provider 실패를 adaptive, authored 또는 다른 템플릿으로 자동 전환하지 않는다.
- 외부 전송 범위, 보존 정책, 비용 한도, timeout 및 credential 격리는 별도 운영 설정으로 승인되어야 한다.

## 5. 생성 API 계약

### 5.1 additive discriminator

기존 `template` 문자열에 새로운 의미를 더 얹지 않는다. 다음 필드를 `/api/v1` request model에 additive하게 도입한다.

```json
{
  "strategy": "template_v2",
  "template_v2_id": "quarterly-review",
  "template_v2_revision": 12
}
```

규범적 타입은 다음과 같다.

```text
strategy:
  "legacy" | "adaptive" | "authored" | "template_v2" | null

template_v2_id: string | null
template_v2_revision: positive integer | null
```

`generation_profile="staged-a-hybrid-v1"`과 `compiler_mode="deterministic-native-ui"`는 이번 vertical slice의 공개 request 필드가 아니라 서버가 정규화하여 provenance에 저장하는 내부 값이다. LLM vision 선택권을 공개 API로 확장할 때는 별도 계약과 정책 검증을 추가한다.

### 5.2 backward compatibility

- `strategy`가 없으면 기존 `template` 해석을 그대로 사용한다.
- 기존 `template="adaptive"`, authored sentinel, built-in template 및 `custom-*` 요청의 의미와 응답을 바꾸지 않는다.
- 기존 클라이언트에 신규 필드를 필수로 요구하지 않는다.
- discriminator를 명시한 신규 요청에서는 strategy와 충돌하는 legacy `template` 값을 허용하지 않는다.
- `template` 값만 보고 Template V2로 자동 승격하지 않는다.

### 5.3 검증 행렬

| 요청 | 결과 |
| --- | --- |
| discriminator 없음 + 기존 유효 요청 | 기존 경로로 처리 |
| `strategy="template_v2"` + template ID + revision + 정책 허용 | 고정 snapshot으로 처리 |
| `strategy="template_v2"` + template ID 누락 | `400 template_v2_id_required` |
| `strategy="template_v2"` + revision 누락 | `400 template_v2_revision_required` |
| 현재 revision과 요청 revision 불일치 | `409 template_v2_revision_conflict` |
| flag OFF | `403 template_v2_creation_disabled` |
| allowlist 미설정 | `403 template_v2_allowlist_required` |
| allowlist 불일치 | `403 template_v2_template_not_allowed` |
| template 없음 | `404 template_v2_template_not_found` |
| revision snapshot 없음 | `409 template_v2_snapshot_not_found` |
| `strategy="template_v2"` + authored sentinel | `400 generation_strategy_conflict` |
| `strategy="template_v2"` + legacy `custom-*` ID | `400 generation_strategy_conflict` |
| `strategy="template_v2"` + 파일 또는 web search source | `400 template_v2_source_mode_not_supported` |
| 알 수 없는 strategy | request schema validation 오류 |
| 생성된 layout이 schema/capability 위반 | `422 template_v2_generation_invalid` |

현재 vertical slice는 FastAPI 표준 envelope
`{"detail":"<stable_code>"}`를 사용한다. 성공한 생성의 request/job correlation ID는
provenance에 저장한다. 오류에도 correlation ID를 제공하는 구조화 envelope는 후속 API
hardening 범위다. provider 응답 본문, prompt 원문, 파일 경로, credential 또는 문서
내용은 오류·로그에 포함하지 않는다.

### 5.4 요청 수락 시점

서버는 job을 만들기 전에 다음 순서로 검증한다.

1. 인증과 현재 범위의 접근 권한
2. request schema 및 strategy 조합
3. backend flag와 exact allowlist
4. template 존재 여부와 예상 revision
5. generation profile과 compiler/provider availability
6. immutable snapshot digest 생성
7. job ID와 provenance seed를 원자적으로 저장

1~6 중 하나라도 실패하면 job, presentation 또는 slide를 부분 생성하지 않는다.

## 6. Revision과 provenance

### 6.1 revision 고정

- 요청은 `template_v2_id`와 `template_v2_revision`을 함께 제공해야 한다.
- 서버는 수락 시점에 해당 revision의 immutable snapshot과 digest를 고정한다.
- job 실행 중 현재 template revision이 바뀌더라도 고정 snapshot을 계속 사용한다.
- 요청 revision이 수락 시점의 현재 revision과 다르면 generation 시작 전에 `409`로 종료한다.
- 동일 job의 retry는 최초 snapshot과 provenance seed를 재사용한다.
- 최신 revision으로 바꾸려면 새 요청과 새 idempotency/job identity가 필요하다.

### 6.2 필수 provenance envelope

presentation 또는 별도 generation record에 다음 정보를 저장한다.

```json
{
  "schema": "presenton.template-v2-generation-provenance/v1",
  "request_strategy": "template_v2",
  "generation_strategy": "template-v2",
  "generation_profile": "staged-a-hybrid-v1",
  "editor_capability": "template-v2",
  "export_strategy": "template-v2-general",
  "template_v2_id": "quarterly-review",
  "template_v2_revision": 12,
  "template_snapshot_sha256": "sha256:...",
  "source_content_sha256": "sha256:...",
  "compiler_mode": "deterministic-native-ui",
  "compiler_name": "presenton-template-v2-generation-adapter",
  "compiler_version": "1",
  "schema_version": "presenton.template-v2-generation-schema/v1",
  "upstream_baseline_sha": "...",
  "request_id": "...",
  "job_id": "...",
  "created_at": "...",
  "vision": null
}
```

`llm-vision`을 사용한 경우 `vision`에는 최소한 다음을 추가한다.

- provider와 model identifier
- prompt/template version
- 입력 artifact digest와 정규화된 응답 digest
- timeout·retry policy version
- 시작/종료 상태 및 bounded latency/token/cost 수치
- validator 결과

provenance에는 credential, bearer/session token, private storage path, signed URL, raw provider 오류, 불필요한 원문 콘텐츠를 저장하지 않는다. digest 생성 방식과 canonicalization version도 문서화하고 버전 관리한다.

## 7. Fail-closed 및 원자성

다음 경우 Template V2 생성은 실패해야 하며 adaptive, authored HTML, `korean-biz`, 다른 revision 또는 다른 compiler로 조용히 전환하면 안 된다.

- 알 수 없거나 충돌하는 discriminator
- flag 비활성 또는 allowlist 불일치
- template/revision 부재, stale revision 또는 snapshot digest 불일치
- generation profile, schema, element, capability 또는 export strategy 불일치
- validator 실패, canvas bounds 위반, 필수 slot 누락
- compiler가 지원하지 않는 OOXML 관계·미디어·기능
- 명시적으로 선택한 vision provider의 timeout, 정책 위반 또는 응답 검증 실패
- 필수 provenance를 완성할 수 없는 상태

저장 경계는 원자적이어야 한다.

- 성공 시 presentation, slides, strategy identity, pinned revision 및 provenance가 함께 commit된다.
- 실패 시 외부에서 완성본처럼 조회되는 부분 deck을 남기지 않는다.
- 재시도는 idempotency key와 pinned snapshot을 사용하며 중복 deck을 만들지 않는다.
- 동시 수정은 expected revision/CAS로 제어하고 충돌을 명시적으로 반환한다.
- cleanup 실패가 원래의 안전한 generation 실패를 성공으로 바꾸면 안 된다.

## 8. 구현 단계

### Phase 0 — 계약 고정

- 본 문서 승인
- API request/response 및 error code fixture 합의
- provenance schema와 canonical digest 규칙 합의
- feature policy, canary, rollback owner 지정

### Stage A1 — deterministic canary

- `strategy="template_v2"` discriminator와 request validation 추가
- 단일 `staged-a-hybrid-v1` profile만 지원
- deterministic OOXML compiler만 활성화
- 내부 템플릿 소수와 내부 사용자 allowlist로 제한
- 기존 editor/export와 연결하되 strategy identity를 검증

### Stage A2 — 제한적 확장

- 한국어 대표 corpus와 Windows fidelity gate 통과 후 allowlist 확대
- 운영 지표와 managed PostgreSQL canary 증거 확보
- rollback rehearsal 완료

### Stage B — 선택적 vision 평가

- 별도 provider security/privacy/cost 승인 후에만 opt-in
- deterministic 결과와 비교 가능한 shadow 또는 제한 canary부터 시작
- 품질 향상 근거가 없으면 제품 기본값으로 승격하지 않음

`korean-biz` 이관과 multi-user authorization 확대는 Stage B에도 자동 포함되지 않는 별도 작업이다.

## 9. 테스트 및 출시 게이트

### 9.1 계약 및 단위 테스트

- discriminator 전체 조합의 table-driven 테스트
- discriminator 미지정 legacy request의 회귀 fixture
- strategy/editor/export registry의 exact match 및 fail-closed 테스트
- flag OFF, 잘못된 boolean, 빈 allowlist, wildcard, 부분 일치의 음성 테스트
- revision pin, stale revision, concurrent CAS, retry idempotency 테스트
- provenance 필수 필드, digest canonicalization, secret/content 비노출 테스트
- deterministic compiler golden fixture와 재실행 동일성 테스트
- unsupported schema/element/media 및 bounds 오류 테스트
- 명시한 vision failure가 다른 경로로 강등되지 않는 테스트

### 9.2 통합 및 회귀 테스트

- Template V2 generation이 authored-hybrid executor를 호출하지 않는 테스트
- adaptive, authored HTML, built-in/custom template, `korean-biz` snapshot 회귀
- 생성 → 저장 → 재열기 → 편집 → general export 왕복
- 한국어 title/body/list/filename/font round-trip 및 한국어 오류 UI E2E
- feature OFF 전환 후 신규 mutation 차단과 기존 deck read/export 검증
- SQLite 개발 검증과 PostgreSQL exact-head migration/integration
- managed PostgreSQL canary에서 create/retry/conflict/rollback 검증

### 9.3 플랫폼 및 공급망 게이트

- Linux와 Windows에서 동일 fixture의 구조 검사
- Windows 실제 converter 실행 및 render fidelity 비교
- line/shape/text 최소 크기, font substitution, clipping, overflow 기준
- `presentation-export` 고정 버전과 bundled native dependency/ABI 검증
- FastAPI 전체 suite, Next typecheck/lint/build, Cypress, dependency audit
- upstream compatibility manifest와 pinned upstream SHA 검증

### 9.4 운영 지표

canary는 최소한 다음을 strategy/profile/template revision별로 집계한다.

- admission 거절률과 사유
- generation 성공률, validation 실패율, compiler 실패율
- revision conflict 및 idempotent retry 비율
- p50/p95 latency와 작업당 bounded cost
- Windows/Linux export 실패율과 fidelity regression
- rollback 후 신규 mutation이 0인지 여부

문서 내용, prompt 원문, 파일명, 사용자 식별 정보는 telemetry label로 사용하지 않는다.

## 10. 롤아웃과 롤백

### 10.1 롤아웃

1. production flag OFF 상태로 코드와 migration 배포
2. migration exact-head와 read/export 회귀 확인
3. 내부 템플릿 ID만 allowlist에 추가
4. 내부 사용자 canary에서 deterministic mode만 실행
5. 한국어·Windows·PostgreSQL 증거와 지표 검토
6. 승인된 template ID를 한 항목씩 allowlist에 추가

allowlist 확대는 기능 자체의 기본값을 ON으로 바꾸는 행위가 아니다. template별로 독립 승인하고 감사 기록을 남긴다.

### 10.2 즉시 롤백

첫 대응은 `ENABLE_TEMPLATE_V2=false`이다.

- 신규 create, retry, confirm, regenerate admission을 중단한다.
- adaptive, authored HTML, `korean-biz`는 계속 선택·실행 가능해야 한다.
- 기존 Template V2 deck을 삭제하거나 다른 mode로 rewrite하지 않는다.
- 기존 deck read/export는 가능한 범위에서 유지한다.
- 수락된 in-flight job은 pinned snapshot으로 원자적으로 완료하거나 정책에 따라 원자적으로 abort한다. 중간 결과를 게시하지 않는다.
- DB downgrade는 운영 kill switch가 아니며 데이터 손실 가능성이 있으면 수행하지 않는다.

### 10.3 자동 중단 또는 수동 롤백 기준

정확한 수치는 canary 운영 문서에서 환경별로 정하되, 아래 사건은 즉시 확대 중단 대상이다.

- strategy 혼선 또는 silent fallback 발견
- provenance/revision 누락 또는 잘못된 snapshot 사용
- 부분 deck 게시, 중복 deck 생성 또는 권한 우회
- 지속적인 schema/compiler 실패율 급증
- 승인 threshold를 넘는 Windows fidelity 회귀
- export service/native dependency 장애
- 외부 provider로의 승인되지 않은 데이터 전송

롤백 rehearsal은 flag OFF 후 신규 생성 차단, 기존 데이터 read/export, queue/in-flight 처리, 재활성화 절차까지 증거로 남겨야 한다.

## 11. Phase 0 수용 조건

다음 항목이 모두 충족되어야 Phase 0을 완료로 판정한다.

- [ ] D1~D7이 제품·API·운영 owner에게 승인되었다.
- [ ] 공개 discriminator와 `staged-a-hybrid-v1` 내부 profile의 의미가 분리되었다.
- [ ] discriminator 미지정 요청이 기존 동작을 유지하는 contract fixture가 있다.
- [ ] Template V2와 authored/adaptive/`korean-biz` 사이의 암묵적 변환과 fallback이 없다.
- [ ] backend default-OFF 및 exact allowlist 음성 테스트가 있다.
- [ ] 요청 수락 시 template revision과 immutable snapshot digest를 고정한다.
- [ ] 필수 provenance가 없으면 저장을 완료하지 않는다.
- [ ] deterministic OOXML이 기본이며 golden fixture 재현성을 통과한다.
- [ ] 선택적 LLM vision이 validator를 우회하지 않고 완전한 provenance를 남긴다.
- [ ] 한국어 UI·콘텐츠 round-trip E2E가 통과한다.
- [ ] Windows 실제 export와 fidelity gate가 통과한다.
- [ ] adaptive, authored HTML, authored-hybrid, `korean-biz` 회귀 suite가 통과한다.
- [ ] SQLite와 PostgreSQL exact-head 검증 및 managed PostgreSQL canary 증거가 있다.
- [ ] flag-OFF rollback rehearsal과 기존 deck read/export 증거가 있다.
- [ ] 로그·telemetry·오류 응답에 credential, private path 또는 원문 콘텐츠가 노출되지 않는다.

위 체크리스트는 구현 코드가 있다는 이유만으로 충족된 것으로 간주하지 않는다. CI 결과, canary 기록 또는 승인된 운영 증거가 필요하다.

## 12. 후속 작업

Phase 0 이후에도 다음은 별도 결정과 검증이 필요하다.

1. API 모델·dispatcher·persistence의 실제 Stage A 구현
2. deterministic compiler의 지원 element/capability 확대
3. managed PostgreSQL canary 및 production flag-OFF rollback 증적
4. 한국어 corpus와 Windows fidelity threshold 확정
5. LLM vision provider의 privacy, egress, credential, 비용, 품질 평가
6. `korean-biz` 이관 ADR와 opt-in migration 도구
7. multi-user/tenant authorization ADR와 데이터 소유권 migration
8. 장기 provenance 보존·삭제 및 재현성 정책

## 13. 관련 설계와 코드 경계

- `docs/authored-template-contract-design.md`
- `docs/template-v2-recommended-sequence-completion-20260724.md`
- `docs/template-v2-phase1-completion-20260724.md`
- `docs/template-v2-phase2-structured-vision-mvp-20260725.md`
- `docs/template-v2-canary-runbook.md`
- `docs/adaptive-backlog.md`
- `docs/compose-ux-design.md`
- `docs/windows-v0.4.2-release-readiness.md`
- `servers/fastapi/templates/v2/generation.py`
- `servers/fastapi/templates/v2/strategies.py`
- `servers/fastapi/templates/v2/policy.py`
- `servers/fastapi/api/v1/ppt/endpoints/presentation_generate.py`

이 문서와 구현이 충돌할 경우 Phase 0 범위에서는 더 보수적인 fail-closed 해석을 우선한다. 범위를 넓히거나 기존 사용자 계약을 변경해야 한다면 코드에서 임의로 해결하지 말고 별도 ADR과 승인으로 문서를 먼저 갱신한다.
