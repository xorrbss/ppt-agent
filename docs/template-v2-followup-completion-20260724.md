# Template V2 후속 권장 작업 완료 보고서

- 작성일: 2026-07-24
- 구현 워크트리: `C:\project\PPT-agent\pptx-template-studio`
- 구현 브랜치: `feat/pptx-template-studio`
- 기준 upstream: `presenton/presenton main` @ `57b194b234b42c8b28f8a507a30322de200e3e83`
- 기준 앱/export: `0.9.2-beta` / `presentation-export v0.4.2`
- 커밋·push: 수행하지 않음

## 1. 결과 요약

Template V2를 기존 authored HTML/adaptive rendering/authored-hybrid export와 분리된 전략으로 추가했고, 기본 OFF 및 allowlist 기반의 점진 도입 경계를 유지했다. 이어서 권장 순서에 따라 다음 항목을 실제 코드와 테스트로 완료했다.

1. Phase 1 model/schema/generation, adapter, migration, `/api/v1` CRUD
2. 브라우저 통합과 기능 플래그 ON/OFF 검증
3. Konva Studio MVP와 revision 기반 저장 충돌 방지
4. private PPTX 구조화 ingestion 기반
5. durable import claim/lease/retry/recovery
6. 실제 `template-v2-general` production renderer와 `presentation-export v0.4.2` export fidelity 경계
7. authored-hybrid snapshot 일관성과 exporter process timeout
8. PPTX 원본 보존 TTL, cleanup lease/CAS, 감사 정보

전체 FastAPI 회귀는 `638 passed, 1 skipped`, Next/Node 회귀는 `155 passed`, Konva Cypress는 `2 passed`, production build는 성공했다. LibreOffice/Poppler가 없어 시각 픽셀 비교 1건만 명시적으로 skip됐으며, 같은 테스트 안의 구조 검증과 실제 PPTX 생성은 통과했다.

## 2. 구현 전후 점수

| 평가 항목 | 구현 전 독립 감사 | 구현 후 독립 재감사 |
|---|---:|---:|
| 완성도 | 69/100 | 91/100 |
| 안전성 | 72/100 | 96/100 |
| 테스트 신뢰도 | 65/100 | 95/100 |

구현 전 감사에서 P0는 없었고, P1 4건과 P2 6건이 확인됐다. 최종 독립 재감사 결과 P0와 P1은 0건이며, 아래 10건 중 9건은 폐쇄되고 rich text multiline/overflow 항목 1건은 데이터 무손실 fallback을 둔 P2 한계로 남았다.

- P1: 비내구성 BackgroundTasks 의존
- P1: retry와 완료 처리 경쟁으로 success 덮어쓰기
- P1: 저장 중 undo/redo 및 후속 편집 유실
- P1: export fidelity 테스트가 실제 production 경계를 통과하지 않음
- P2: `expected_revision` 선택 입력
- P2: authored overlay/backplate가 서로 다른 revision을 사용할 가능성
- P2: exporter process timeout 부재
- P2: Canvas의 rich text run style 미지원
- P2: private PPTX 원본 보존·삭제 정책 부재
- P2: resize 때 viewport reset

출시 판정은 다음과 같다.

- 기능 플래그 기본 OFF인 Phase 1 통합과 후속 개발: **GO**
- Template V2 광범위 활성화: **NO-GO**
- 이유: 전체 schema element 가운데 `text-list/table/vector/chart/infographic/flex/grid`는 Studio/export renderer가 아직 지원하지 않으며, multiline/overflow rich text는 단일 스타일 fallback이다.

## 3. 핵심 구조

### 3.1 전략 경계

`servers/fastapi/templates/v2/strategies.py`에 다음 계약을 추가했다.

- `GenerationStrategy`: `template-v2`, `authored-html`
- `EditorCapability`: `template-v2`, `authored-html`
- `ExportStrategy`: `template-v2-general`, `authored-hybrid`
- generation/editor/export adapter protocol과 정확한 registry key 검증
- 잘못되거나 충돌하는 presentation identity는 fail-closed

Next export route에도 동일한 분기 경계를 추가해 Template V2는 오직 general exporter로, 명시적 authored-hybrid만 hybrid exporter로 전달된다.

### 3.2 API

모든 신규 backend endpoint는 기존 `/api/v1/ppt` router 아래에만 추가했다.

- `GET /api/v1/ppt/structured-templates`
- `POST /api/v1/ppt/structured-templates`
- `GET /api/v1/ppt/structured-templates/{template_id}`
- `PATCH /api/v1/ppt/structured-templates/{template_id}`
- `DELETE /api/v1/ppt/structured-templates/{template_id}`
- `POST /api/v1/ppt/structured-templates/imports`
- `GET /api/v1/ppt/structured-templates/imports/{import_id}`
- `POST /api/v1/ppt/structured-templates/imports/{import_id}/retry`

쓰기와 import는 `ENABLE_TEMPLATE_V2=true`와 `TEMPLATE_V2_TEMPLATE_ALLOWLIST`를 모두 요구한다. 기본값은 OFF다. 이미 저장된 Template V2를 읽는 동작은 kill switch로 손상시키지 않는다.

PATCH는 필수 `expected_revision >= 1`을 사용하고 단일 SQL compare-and-swap으로 revision을 증가시킨다. 충돌 시 `409 template_v2_revision_conflict`와 현재 revision을 반환한다.

### 3.3 migration 계보

실제 기존 head `f3a4b5c6d7e8` 뒤에 additive migration을 연결했다.

```text
c8… -> d1… -> e2… -> f3a4b5c6d7e8
      -> a4b5c6d7e8f9  Template V2 Phase 1
      -> b5c6d7e8f9a0  private PPTX imports
      -> c6d7e8f9a0b1  template revision
      -> d7e8f9a0b1c2  import attempt/lease
      -> e8f9a0b1c2d3  private source retention
```

최종 `alembic heads`는 `e8f9a0b1c2d3 (head)` 하나다. 데이터 손실 가능성이 있는 downgrade는 안전 검사를 통과하지 않으면 거부한다.

### 3.4 Konva Studio MVP

전체 upstream 49K LOC editor를 이식하지 않고 현재 schema/API에 맞는 수직 슬라이스를 구현했다.

- 1280×720 논리 캔버스
- recursive container/text/shape rendering
- slide/layout/component navigation
- selection과 Transformer 기반 이동·크기 변경
- zoom/pan과 resize 후 논리 중심 보존
- rectangle 추가
- undo/redo 및 저장 checkpoint
- 한 번에 하나의 save, snapshot token, 느린 응답의 후속 편집 덮어쓰기 차단
- 409 발생 시 로컬 변경 유지 및 명시적 reload
- run별 font family/size/color/bold/italic/underline와 필드 단위 상속
- multiline/overflow는 원본 데이터를 변경하지 않는 보수적 단일 텍스트 fallback

### 3.5 PPTX 구조화 ingestion과 원본 보존

private streaming upload에 크기·SHA-256·확장자·MIME·ZIP/XML·경로 검증을 적용했다. OOXML을 결정적으로 파싱해 Template V2 draft와 manual review manifest를 만든다.

비동기 작업은 DB의 queued/processing/terminal 상태, attempt token/number, heartbeat, lease를 사용한다. 앱 시작 시 queued와 stalled processing을 복구하고, owner token을 가진 worker만 success/failure를 확정할 수 있다. failed retry도 compare-and-swap으로 한 번만 queued 상태를 획득한다.

원본 보존 정책은 다음과 같다.

- 기본 TTL 7일
- `TEMPLATE_V2_PPTX_SOURCE_TTL_DAYS`는 1~90일만 허용
- processing/queued/recent retryable failed 원본은 삭제하지 않음
- cleanup claim token과 5분 lease
- 삭제 직전 private storage path 재검증
- missing/IO/path 실패 결과를 best-effort로 manifest와 DB에 감사 기록
- startup 및 dispatcher 주기 cleanup
- API에 retention expiry, cleanup attempt, deletion timestamp 노출

### 3.6 export 경계

- native `SlideLayout` JSON을 읽는 `template-v2-general-renderer.mjs`
- 실제 `/pdf-maker`의 `TemplateV2GeneralSlide.tsx`가 persisted `slide.ui.components[].elements[]`를 React DOM으로 렌더
- persisted presentation identity를 production strategy boundary에서 판정해 `template-v2-general`만 전용 렌더러로 전달
- authored/adaptive/legacy는 기존 `SlideScale`/V1 경로를 유지
- 실제 Next `/api/export-presentation` → `/pdf-maker` → bundled `presentation-export v0.4.2`로 PPTX 생성
- 미지원 element는 조용히 누락하지 않고 export를 fail-closed
- fixture의 제목/본문/도형/텍스트 fit을 PPTX slide XML에서 확인
- authored-hybrid는 기존 편집 가능 요소, 최소 9pt, 직선 보정, 텍스트 잘림 방지 로직 유지
- authored overlay와 backplate가 동일 raw presentation body의 SHA-256 snapshot을 사용
- hash 불일치는 live fallback 없이 fail-closed
- render URL에는 `source_sha256`만 포함하며 cookie/session/payload를 넣지 않음
- exporter 기본 deadline 600초, grace 5초
- POSIX process group TERM→KILL, Windows `taskkill /T`→`/F /T`
- 임시 작업공간 정리 재시도

## 4. 변경 파일과 근거

### Backend model/schema/generation

- `servers/fastapi/templates/v2/**`: element/layout/schema/generation/persistence/policy/strategy 계약
- `servers/fastapi/template_v2_schema_contract.py`: 기존 presentation model과 공유하는 V2 schema 검증 경계
- `servers/fastapi/models/sql/template_v2.py`: structured template persistence와 revision
- `servers/fastapi/models/sql/presentation.py`, `slide.py`, `presentation_with_slides.py`: V2 identity와 native UI를 additive하게 보존
- `servers/fastapi/services/template_v2_poc.py`, `template_v2_rollout.py`: 기본 OFF/allowlist/telemetry 호환

### API와 DB

- `servers/fastapi/api/v1/ppt/endpoints/structured_templates.py`: CRUD와 revision CAS
- `servers/fastapi/api/v1/ppt/endpoints/structured_template_imports.py`: private PPTX import 조회·재시도
- `servers/fastapi/api/v1/ppt/router.py`: `/api/v1/ppt` 하위 router 등록
- `servers/fastapi/alembic/versions/a4…e8*.py`: Phase 1부터 retention까지 단일 additive 계보
- `servers/fastapi/alembic/env.py`, `migrations.py`, `services/database.py`: SQLite/legacy stamp와 새 계보 검증

### PPTX ingestion/durability/retention

- `servers/fastapi/models/sql/template_v2_pptx_import.py`
- `servers/fastapi/services/template_v2_pptx_storage.py`
- `servers/fastapi/services/template_v2_pptx_ingestion_service.py`
- `servers/fastapi/services/template_v2_pptx_retention_service.py`
- `servers/fastapi/templates/v2/pptx/**`
- `servers/fastapi/api/lifespan.py`

### Konva Studio

- `servers/nextjs/app/template-v2-studio/[templateId]/**`
- `servers/nextjs/lib/template-v2-konva.ts`
- `servers/nextjs/lib/template-v2-studio.ts`
- `servers/nextjs/package.json`, `package-lock.json`

### Export

- `servers/nextjs/app/(export)/pdf-maker/PdfMakerPage.tsx`
- `servers/nextjs/app/(export)/pdf-maker/TemplateV2GeneralSlide.tsx`
- `servers/nextjs/app/api/export-presentation/route.ts`
- `servers/nextjs/lib/export-presentation-route.ts`
- `servers/nextjs/lib/presentation-export-boundary.ts`
- `servers/nextjs/lib/presentation-export-strategy.ts`
- `servers/nextjs/lib/template-v2-general-renderer.mjs`
- `servers/nextjs/lib/export-fidelity/**`
- `servers/nextjs/app/(export)/pdf-maker/page.tsx`
- `servers/nextjs/lib/authored-hybrid/export.ts`
- `servers/nextjs/lib/presentation-snapshot-integrity.ts`
- `servers/nextjs/lib/export-process-supervisor.ts`
- `servers/nextjs/lib/run-bundled-presentation-export.ts`
- `servers/nextjs/lib/export-render-url.ts`
- `scripts/sync-presentation-export.cjs`: 기존 Windows OS-aware 동기화와 검증 유지·보강

### 테스트

- `servers/fastapi/tests/unit/test_templates_v2_*.py`
- `servers/fastapi/tests/unit/test_structured_templates_api.py`
- `servers/fastapi/tests/unit/test_presentation_template_v2_ui.py`
- `servers/fastapi/tests/unit/test_template_v2_strategies.py`
- `servers/fastapi/tests/unit/test_template_v2_pptx_*.py`
- `servers/fastapi/tests/unit/test_migrations.py`
- `servers/nextjs/app/template-v2-studio/[templateId]/TemplateV2Studio.cy.tsx`
- `servers/nextjs/lib/template-v2-studio.test.ts`
- `servers/nextjs/lib/presentation-export-strategy.test.ts`
- `servers/nextjs/lib/export-fidelity/**`
- `servers/nextjs/lib/export-process-supervisor.test.mjs`
- `servers/nextjs/lib/presentation-snapshot-integrity.test.mjs`
- `servers/nextjs/lib/run-bundled-presentation-export.e2e.test.mjs`

`next-env.d.ts`와 `tsconfig.tsbuildinfo`는 Next/TypeScript 검증 과정의 생성 변경이다. 작업 시작 전에 존재한 다른 워크트리 변경과 미추적 파일은 되돌리거나 덮어쓰지 않았다.

## 5. upstream 선택 이식과 제외

선택 이식하거나 현재 구조에 재구현한 항목:

- V2 element/layout/schema/generation 계약
- structured template persistence와 API 테스트 관점
- Template V2 UI/native UI 보존 관점
- default template/async task 회귀 관점
- Konva의 논리 캔버스·선택·변환·viewport 핵심 동작

의도적으로 제외한 항목:

- merge-base 없는 upstream 전체 merge
- wholesale cherry-pick
- `/api/v2`
- upstream 49K LOC Konva editor 전체
- PPTX vision/AI 구조화 전체 파이프라인
- 기존 presentation 자동 V2 변환
- V2 기본 활성화
- autosave와 전체 Undo/Redo 체계
- authored-hybrid를 upstream general export로 교체
- `presentation-export` 버전 변경
- upstream Windows `unzip ENOENT` sync 구현
- 외부 배포, 서명, R2 upload

Konva editor를 최초 Phase 1에서 제외한 이유는 사용자 지정 범위가 “49K LOC 전체 이식 금지”였고 model/API/adapter/migration 안정화가 선행돼야 했기 때문이다. 후속 권장 순서에서는 전체 포팅 대신 검증 가능한 MVP 수직 슬라이스를 구현했다.

## 6. 실행한 검증과 정확한 결과

| 명령/검증 | 결과 |
|---|---|
| `python -m pytest -q` | 638 passed, 1 skipped, 60 warnings, 최종 재실행 30.04s |
| PPTX/API/durability/retention/migration 5개 파일 | 89 passed, 18.10s |
| `python -m compileall -q ...` | exit 0 |
| `python -m alembic heads` | `e8f9a0b1c2d3 (head)` |
| Next 전체 `node --experimental-strip-types --test ...` | 155 passed, 0 failed, 최종 재실행 21.02s |
| `npm run test:template-v2-export-fidelity` | 7 tests: 6 passed, 0 failed, 1 visual-only skipped, 50.2s |
| export strategy/route/snapshot/supervisor 집중 회귀 | 44 passed, 0 failed |
| `node --test lib/run-bundled-presentation-export.e2e.test.mjs` | 1 passed, 11.02s |
| `npm run test:presentation-export-sync` | 16 passed, 0 failed |
| `npm run check:presentation-export` | OK, v0.4.2와 Windows converter 확인 |
| `npx tsc --noEmit --pretty false` | exit 0 |
| focused `npx eslint ...` | exit 0 |
| `npm run build` | Next production build 성공, 28/28 static page generation |
| Konva Cypress component | 2 passed, 0 failed |
| `git diff --check` | exit 0; 기존 LF→CRLF 경고만 존재 |

수동 브라우저 검증:

- 기능 ON에서 GET/PATCH, 요소 추가, 저장, revision 증가 확인
- 지원하지 않는 image/assets round-trip 보존
- 외부 revision 증가 후 stale save가 409를 받고 로컬 편집을 보존하는지 확인
- 기능 OFF에서 Studio route가 not-found UI를 반환하는지 확인
- 검증용 격리 서버와 브라우저 탭 종료

## 7. 보존한 로컬 불변 조건

- authored-hybrid의 편집 가능 요소
- 최소 글꼴 9pt
- 가로·세로선 직선 보정
- 텍스트 잘림 방지
- secret-free render URL과 제한된 인증 전달
- 경로 정규화와 traversal 차단
- 업로드 파일 형식과 크기 제한
- 기존 로그인 throttling 및 공개 share token 예외
- Windows SQLite 경로 보정
- packaged Sharp와 Electron Windows 검증
- Windows OS-aware export sync
- `presentation-export v0.4.2`

## 8. 남은 회귀 위험

1. schema의 element 11종 가운데 실제 Studio/export renderer는 `text/container/image/group` 4종만 지원한다. `text-list/table/vector/chart/infographic/flex/grid`는 명시적으로 차단되므로 광범위 활성화 전에 renderer와 golden test가 필요하다.
2. rich text는 단일 라인과 폭 안의 run별 스타일을 보존하지만 multiline/overflow는 데이터 무손실 단일 스타일 fallback이다.
3. LibreOffice/Poppler가 없어 자동 pixel comparison 1건은 실행되지 않았다. 실제 Next route 기반 PPTX 생성과 XML 구조 검증은 통과했다.
4. Konva는 MVP다. multi-select, group 편집, asset 편집, 전체 autosave/Undo·Redo는 아직 없다.
5. PPTX ingestion은 안전한 deterministic OOXML 기반이다. theme/master/SmartArt/chart/복합 SVG와 vision 기반 의미 추론은 제한적이다.
6. worker 상태는 DB에 내구화됐지만 dispatcher 자체는 애플리케이션 프로세스 안에 있다. 다중 노드 운영에는 외부 queue/worker와 lease telemetry가 더 적합하다.
7. 파일 삭제 성공 후 DB 감사 확정 전에 DB 장애가 발생하는 희귀 구간은 다음 cleanup에서 missing으로 복구되지만, 별도 tombstone/reconciliation을 추가하면 운영 가시성이 더 좋아진다.
8. 기본 OFF이므로 실제 사용자 트래픽 기반 fidelity/latency/cleanup 지표는 아직 없다.
9. 기존 SQLAlchemy/Pydantic warning 60건은 테스트 실패가 아니지만 장기적으로 정리할 대상이다.

## 9. 다음 권장 작업

### Phase 2A: Konva Studio 확장

1. multi-select/group/lock/z-order
2. table/chart/image/SVG 편집 adapter
3. inline rich-text editor와 text-fit 시각 경고
4. command 기반 autosave/Undo·Redo와 server revision journal
5. keyboard/accessibility와 대형 deck 성능 측정

### Phase 2B: PPTX vision 구조화

1. theme/master/layout 관계 해석
2. chart/SmartArt/table/media 관계 그래프
3. render 기반 geometry 보정과 OCR/vision 의미 분류
4. confidence score와 manual review diff UI
5. 재현 가능한 PPTX corpus 및 structural/pixel golden

### Phase 2C: 운영 준비

1. 외부 durable queue/worker
2. lease/cleanup/export timeout 지표와 경보
3. Windows/Linux/macOS visual CI 이미지
4. allowlist canary → read-only 확대 → write 확대
5. rollback rehearsal와 DB/file reconciliation

## 10. 최종 워크트리 상태

어떤 워크트리에서도 commit 또는 push를 수행하지 않았다. 기존 사용자 변경과 미추적 파일은 되돌리거나 삭제하지 않았다.

- `C:\project\PPT-agent\ppt-agent`
  - branch: `main` (`origin/main` 대비 ahead 15)
  - tracked modified 2, untracked 10
  - 주요 기존 상태: `next-env.d.ts`, `tsconfig.tsbuildinfo`, 로그, `.test-runtime`, `AGENTS.md`, pnpm 파일
- `C:\project\PPT-agent\pptx-template-studio`
  - branch: `feat/pptx-template-studio`
  - tracked modified 28, untracked 43
  - 이 보고서의 Backend/API/migration/Konva/export/test 변경이 포함됨
- `C:\project\PPT-agent\upstream-realignment`
  - branch: `integration/upstream-realignment-20260724`
  - tracked modified 0, untracked 1
  - 미추적 파일: `docs/upstream-realignment-20260724.md`

최종 `git diff --check`는 exit 0이다. 출력된 메시지는 기존 LF→CRLF 변환 경고뿐이다.
