# Template V2 변경 리뷰 및 커밋 분리 계획

작성일: 2026-07-24
대상: `feat/pptx-template-studio`
기준 HEAD: `78bf5f102224f8dfd80d12cd9c64767936ad567e`

## 결론

현재 변경은 하나의 커밋으로 합치기에는 범위가 너무 크다. Backend core,
PPTX ingestion, local sidecar, general export, Konva Studio, export hardening,
upstream compatibility, 문서의 8개 커밋으로 나누는 것이 가장 안전하다.

이 문서는 staging과 commit을 실행하지 않는다. 실제 commit은 별도 승인을
받은 뒤 아래 순서로 수행하고 push는 계속 보류한다.

## 커밋 전 반드시 정리할 생성 파일

다음 파일은 기능 변경으로 커밋하면 안 된다.

- `servers/nextjs/next-env.d.ts`
  - 격리 E2E의 임시 dist 경로가 자동 기록되어 있다.
  - HEAD의 `.next-build/dev/types/routes.d.ts` 참조로 복구한다.
- `servers/nextjs/tsconfig.tsbuildinfo`
  - TypeScript incremental build 산출물이다.
  - HEAD 상태로 복구하고 staging에서 제외한다.
- `servers/nextjs/tsconfig.json`
  - `.next-template-v2-fidelity-*` 임시 경로 6개만 제거한다.
  - 영구 설정 변경은 현재 없다.
- `servers/nextjs/pnpm-lock.yaml`
- `servers/nextjs/pnpm-workspace.yaml`
  - 기준 워크트리에도 별도로 존재하는 미추적 파일이다.
  - 이 브랜치의 공식 dependency 변경은 `package.json`과
    `package-lock.json`으로만 기록한다.

격리 export E2E는 후속 정리에서 `next-env.d.ts`와 `tsconfig.json` 원본을
저장하고 `finally`에서 복구하도록 보강해야 한다. 그래야 테스트 재실행이
tracked source를 dirty하게 만들지 않는다.

## 권장 커밋 순서

### 1. `feat(template-v2): add core schema generation and v1 CRUD`

포함 범위:

- `servers/fastapi/templates/v2/` 중 core model, schema, generation,
  persistence, policy, strategies, wire codec
- `servers/fastapi/models/sql/template_v2.py`
- `servers/fastapi/services/template_v2_service.py`
- `servers/fastapi/template_v2_schema_contract.py`의 core 계약
- `servers/fastapi/api/v1/ppt/endpoints/structured_templates.py`
- `servers/fastapi/alembic/versions/a4b5c6d7e8f9_add_template_v2_phase_one.py`
- presentation/slide identity와 native UI 변경
- core schema/generation/strategy/wire/API 테스트 및 fixture

공유 파일은 이 커밋에서 core 관련 hunk만 stage한다:

- `servers/fastapi/api/v1/ppt/router.py`
- `servers/fastapi/migrations.py`
- `servers/fastapi/tests/unit/test_migrations.py`
- `servers/fastapi/template_v2_schema_contract.py`

검증:

```powershell
cd servers/fastapi
.\.venv\Scripts\python.exe -m pytest `
  tests/unit/test_templates_v2_elements.py `
  tests/unit/test_templates_v2_schema.py `
  tests/unit/test_templates_v2_generation.py `
  tests/unit/test_template_v2_strategies.py `
  tests/unit/test_template_v2_policy.py `
  tests/unit/test_template_v2_wire_codec.py `
  tests/unit/test_structured_templates_api.py -q -p no:cacheprovider
```

### 2. `feat(template-v2): add durable PPTX import pipeline`

포함 범위:

- migrations `b5c6d7e8f9a0`부터 `e8f9a0b1c2d3`까지
- `models/sql/template_v2_pptx_import.py`
- `services/template_v2_pptx_*`
- `templates/v2/pptx/`
- `api/v1/ppt/endpoints/structured_template_imports.py`
- dispatcher lifecycle과 retention
- PPTX import, relationship graph, durability, retention 테스트

공유 파일:

- `servers/fastapi/api/lifespan.py`
- `servers/fastapi/api/v1/ppt/router.py`의 imports router hunk
- `servers/fastapi/migrations.py`
- `servers/fastapi/tests/unit/test_migrations.py`

검증:

```powershell
cd servers/fastapi
.\.venv\Scripts\python.exe -m pytest `
  tests/unit/test_template_v2_pptx_import.py `
  tests/unit/test_template_v2_pptx_relationship_graph.py `
  tests/unit/test_template_v2_pptx_durability.py `
  tests/unit/test_template_v2_pptx_retention.py -q -p no:cacheprovider
```

### 3. `refactor(template-v2): isolate local state in sidecar`

포함 범위:

- migration `f9a0b1c2d3e4`
- `models/sql/template_v2_local_state.py`
- sidecar ownership, orphan detection, future drop-readiness 계약
- `test_template_v2_local_state_migration.py`
- migration translation ledger의 sidecar 정책

기존 `template_v2.presentation_id` cascade 제거는 포함하지 않는다. 대체
삭제 경로가 설치되기 전에는 canonical row orphan을 만들 수 있기 때문이다.

검증:

```powershell
cd servers/fastapi
.\.venv\Scripts\python.exe -m pytest `
  tests/unit/test_template_v2_local_state_migration.py `
  tests/unit/test_migrations.py -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m alembic heads
```

### 4. `feat(template-v2): add general render and export strategy boundary`

포함 범위:

- `lib/template-v2-render-plan.*`
- `lib/template-v2-general-renderer.*`
- upstream render compatibility fixture와 테스트
- `lib/presentation-export-strategy.*`
- `lib/presentation-export-boundary.ts`
- `lib/export-presentation-route.ts`
- `app/(export)/pdf-maker/TemplateV2GeneralSlide.tsx`
- pdf-maker와 export API route의 Template V2 general 경계

검증:

```powershell
cd servers/nextjs
node --test `
  lib/presentation-export-strategy.test.ts `
  lib/template-v2-render-plan.test.mjs `
  lib/template-v2-general-renderer.test.mjs `
  lib/template-v2-upstream-render-compat.test.mjs `
  app/api/export-presentation/route.test.ts
npx tsc --noEmit --pretty false
```

### 5. `feat(template-v2): add guarded Konva Studio MVP`

포함 범위:

- `app/template-v2-studio/[templateId]/`
- `lib/template-v2-konva.ts`
- `lib/template-v2-studio*.ts`
- `lib/template-v2-upstream-compat.*`
- `package.json`, `package-lock.json`의 exact Konva dependencies
- Studio unit 및 Cypress component 테스트

`pnpm-lock.yaml`, `pnpm-workspace.yaml`, `next-env.d.ts`,
`tsconfig.tsbuildinfo`는 포함하지 않는다.

검증:

```powershell
cd servers/nextjs
node --test `
  lib/template-v2-upstream-compat.test.ts `
  lib/template-v2-studio.test.ts `
  lib/template-v2-studio-ui.test.ts
npx cypress run --component --browser electron `
  --spec "app/template-v2-studio/[templateId]/TemplateV2Studio.cy.tsx"
npx tsc --noEmit --pretty false
```

### 6. `fix(export): harden snapshot integrity and isolated fidelity E2E`

포함 범위:

- `lib/presentation-snapshot-integrity.*`
- `lib/export-process-supervisor.*`
- `lib/export-render-url.*`
- `lib/run-bundled-presentation-export*`
- authored-hybrid의 snapshot hash 전달과 보안 테스트
- `lib/export-fidelity/`
- `next.config.mjs`의 테스트용 dist override
- `scripts/sync-presentation-export.cjs`의 Windows Sharp 실행 보강
- `.github/workflows/template-v2-export-fidelity.yml`

커밋 전에 격리 E2E가 tracked TypeScript 설정을 복구하도록 먼저 보강한다.

검증:

```powershell
cd servers/nextjs
node --test --test-concurrency=1 `
  lib/presentation-snapshot-integrity.test.mjs `
  lib/export-process-supervisor.test.mjs `
  lib/export-render-url.test.mjs `
  lib/authored-hybrid/security.test.mjs
node --test lib/export-fidelity/template-v2-general-export-fidelity.e2e.test.mjs
```

### 7. `chore(compat): pin upstream Template V2 contracts`

포함 범위:

- `compatibility/`
- `scripts/verify-upstream-compatibility.mjs`
- `scripts/verify-upstream-compatibility.test.mjs`
- `.github/workflows/upstream-compatibility.yml`
- `api/v1/ppt/endpoints/template_v2_compat.py`
- legacy template handler/router의 `{template_id}` OpenAPI 정렬
- upstream contract 테스트

검증:

```powershell
node scripts/verify-upstream-compatibility.mjs
cd servers/fastapi
.\.venv\Scripts\python.exe -m pytest `
  tests/unit/test_template_v2_compat_facade.py `
  tests/unit/test_upstream_template_v2_contracts.py -q -p no:cacheprovider
```

### 8. `docs(template-v2): record rollout and compatibility decisions`

포함 범위:

- `docs/template-v2-canary-runbook.md`
- Phase 1, follow-up, recommended sequence 완료 보고서
- 이 커밋 분리 계획
- canary 검사 스크립트

문서의 literal repository path가 실제 파일과 일치하는지 다시 검사한다.

## 부분 staging이 필요한 파일

다음 파일은 여러 커밋의 변경을 함께 담고 있으므로 파일 전체를 한 번에
stage하면 안 된다.

| 파일 | 분리 대상 |
| --- | --- |
| `servers/fastapi/api/v1/ppt/router.py` | core CRUD / import / compatibility |
| `servers/fastapi/migrations.py` | a4 core / b5-e8 import / f9 sidecar |
| `servers/fastapi/tests/unit/test_migrations.py` | 동일 migration 3단계 |
| `servers/fastapi/template_v2_schema_contract.py` | core / sidecar readiness |
| `servers/nextjs/package.json` | fidelity test script / Konva dependencies |
| `servers/nextjs/app/api/export-presentation/route.ts` | strategy / hardening |

각 커밋은 `git add -p -- <파일>`로 hunk를 선택하고 아래 검사를 통과해야 한다.

```powershell
git diff --cached --check
git diff --cached --stat
git status --short
```

## 최종 통합 검증

8개 커밋을 모두 만든 뒤에만 다음 전체 검증을 수행한다.

```powershell
cd servers/fastapi
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider

cd ..\nextjs
npx tsc --noEmit --pretty false
npm run lint

cd ..\..
node scripts/verify-upstream-compatibility.mjs
git diff --check
```

예상 기준:

- FastAPI: 731 passed, 1 skipped
- 프로젝트 Node tests: 196 passed
- Cypress Studio component: 6 passed
- upstream compatibility: 261 checks
- visual-only E2E: LibreOffice/Poppler가 없으면 1 skipped

## 현재 실행 상태

- staging: 수행하지 않음
- commit: 수행하지 않음
- push: 수행하지 않음
- 다른 워크트리 파일: 변경하지 않음
