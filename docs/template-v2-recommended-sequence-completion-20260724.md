# Template V2 권장 순서 통합 완료 보고서

- 작성일: 2026-07-24
- 구현 워크트리: `C:\project\PPT-agent\pptx-template-studio`
- 구현 브랜치: `feat/pptx-template-studio`
- 기준 커밋: `78bf5f102224f8dfd80d12cd9c64767936ad567e`
- upstream 검증 기준: `presenton/presenton main` / `57b194b234b42c8b28f8a507a30322de200e3e83`
- 커밋·push·외부 배포: 수행하지 않음

## 1. 결론

요청된 권장 순서를 따라 다음 범위를 실제 코드와 테스트로 완료했다.

1. 기존 Phase 1 변경 안정화 및 diff 감사
2. Template V2 일반 렌더러의 11개 요소와 flex/grid 의미 보존
3. Windows/Linux export fidelity 시각 회귀 CI
4. Konva Studio P0/MVP 편집 명령과 UI 연결
5. PPTX OOXML 구조화 V0 파이프라인
6. 기본 OFF 기능 플래그와 명시적 allowlist canary 정책
7. FastAPI·Node·Cypress·빌드·타입·lint·migration·export sync 통합 검증

Template V2는 기존 authored HTML/adaptive rendering/authored-hybrid export를 대체하지 않는다. 새 경로는 명시적 전략 선택과 기본 OFF 플래그 뒤에 추가되었고, `/api/v1`만 사용한다. `presentation-export`는 v0.4.2를 유지하며 Windows용 OS-aware 동기화도 보존했다.

현재 판정은 **Editor 내부 allowlist canary GO**, **export-only 내부 canary GO**이다. 광범위 활성화는 Linux/Windows 시각 회귀 CI의 실제 green 결과 전까지 NO-GO이다.

## 2. 점수와 독립 감사

초기 통합 감사 점수는 77/100이었다. P0는 없었고 다음 세 P1이 확인되었다.

- renderer에서 일부 chart/image/container 의미가 손실될 수 있음
- 잠긴 조상의 자식 편집과 undo/redo 과정에서 session lock이 우회·소실될 수 있음
- export fidelity workflow의 변경 감지 경로가 충분히 넓지 않음

세 항목을 수정한 첫 재감사에서 Konva UI가 exact path만 잠그는 추가 P1이 발견되었다. 저장 reducer는 조상 잠금 때문에 변경을 거부하지만 Konva node가 화면상 이동된 채 남을 수 있는 문제였다. Canvas에 ancestor/subtree 양방향 `pathsOverlap`을 적용하고, 잠금 충돌 node를 non-draggable 처리하며 Transformer에서 제거했다. Studio toolbar와 text inspector에도 같은 `lockConflict`를 적용하고 Cypress 양방향 잠금 시나리오를 추가했다.

최종 독립 재감사 결과:

- 점수: **95/100**
- P0: 0
- P1: 0
- P2: 4
- Editor 내부 allowlist canary: **GO**
- Export-only 내부 canary: **GO**
- Broad rollout: **NO-GO**

잔여 P2는 Konva canvas 속성 자체의 직접 assertion 부재, 로컬 visual tool 부재, 미push로 인한 양 OS Actions 미실행, lint 경고와 dirty/untracked release provenance이다. 깨끗한 commit/push와 Linux/Windows required visual CI green을 확보하면 추가 기능 수정 없이 broad rollout GO로 전환할 수 있다는 감사 판정이다.

## 3. 변경 파일과 변경 근거

### 3.1 Backend model, schema, generation contract

- `servers/fastapi/models/sql/template_v2.py`
  - Template V2 template/revision/layout 저장 모델을 기존 SQLAlchemy 구조에 맞게 추가했다.
- `servers/fastapi/models/sql/template_v2_pptx_import.py`
  - PPTX import 시도, lease, 상태, source retention을 기존 presentation 데이터와 분리해 표현했다.
- `servers/fastapi/template_v2_schema_contract.py`
  - Template V2 element discriminator와 layout contract를 한 곳에서 검증하고, 지원하지 않는 구조를 fail-closed 처리한다.
- `servers/fastapi/templates/v2/`
  - 기본 OFF 상태에서도 독립 검증할 수 있는 V2 template/generation 자산을 둔다.
- `servers/fastapi/services/template_v2_poc.py`
  - 현재 generation 흐름을 깨지 않고 Template V2 계약을 선택적으로 생성하는 경계를 추가했다.
- `servers/fastapi/services/template_v2_rollout.py`
  - 기능 플래그, 명시적 template allowlist, 전략 선택을 중앙화했다.
- `servers/fastapi/models/presentation_with_slides.py`
  - 기존 presentation 응답과 호환되는 선택적 Template V2 metadata를 추가했다.

근거: upstream 구현을 wholesale cherry-pick하지 않고 현재 로컬 모델과 serialization 관례에 맞춰 선택 이식했다. 기존 authored presentation은 자동 변환하지 않는다.

### 3.2 Migration

- `servers/fastapi/alembic/versions/a4b5c6d7e8f9_add_template_v2_phase_one.py`
- `servers/fastapi/alembic/versions/b5c6d7e8f9a0_add_template_v2_pptx_imports.py`
- `servers/fastapi/alembic/versions/c6d7e8f9a0b1_add_template_v2_revision.py`
- `servers/fastapi/alembic/versions/d7e8f9a0b1c2_add_template_v2_import_attempt_lease.py`
- `servers/fastapi/alembic/versions/e8f9a0b1c2d3_add_template_v2_source_retention.py`
- `servers/fastapi/alembic/env.py`
- `servers/fastapi/migrations.py`
- `servers/fastapi/services/database.py`

근거: 실제 로컬 계보 `c8d -> d1f -> e2f -> f3a` 뒤에 additive migration만 연결했다. 최종 단일 head는 `e8f9a0b1c2d3`이다. Windows SQLite 경로 보정과 기존 migration bootstrap을 보존했다.

### 3.3 `/api/v1` CRUD와 import API

- `servers/fastapi/api/v1/ppt/endpoints/structured_templates.py`
- `servers/fastapi/api/v1/ppt/endpoints/structured_template_imports.py`
- `servers/fastapi/api/v1/ppt/router.py`
- `servers/fastapi/api/v1/ppt/endpoints/presentation.py`
- `servers/fastapi/api/lifespan.py`

근거: Template V2 core CRUD, revision, PPTX import 상태를 모두 `/api/v1` 아래에 추가했다. 새 `/api/v2`는 만들지 않았다. lifespan에는 만료 lease/source cleanup을 현재 서비스 시작 방식에 맞게 연결했다.

### 3.4 PPTX 구조화 V0

- `servers/fastapi/services/template_v2_pptx_ingestion_service.py`
- `servers/fastapi/services/template_v2_pptx_storage.py`
- `servers/fastapi/services/template_v2_pptx_retention_service.py`

근거: PPTX ZIP package와 OOXML 관계 그래프를 bounded, allowlisted 방식으로 읽고 slide/layout/master/theme/media 증거를 조립한다. external relationship은 역참조하거나 보존하지 않으며 traversal, cycle, 누락 관계, 크기·개수 상한을 검사한다. OLE와 embedded workbook은 실행·해석하지 않는다.

### 3.5 Generation/editor/export 전략 경계

- `servers/nextjs/lib/presentation-export-strategy.ts`
- `servers/nextjs/lib/presentation-export-strategy.test.ts`
- `servers/nextjs/lib/presentation-export-boundary.ts`
- `servers/nextjs/lib/export-presentation-route.ts`
- `servers/nextjs/app/api/export-presentation/route.ts`

근거: 다음 경계를 코드에서 명시적으로 선택하도록 했다.

- `GenerationStrategy`: `template-v2` / `authored-html`
- `EditorCapability`: `template-v2` / `authored-html`
- `ExportStrategy`: `template-v2-general` / `authored-hybrid`

기본 경로는 기존 authored-hybrid이며, Template V2는 플래그와 template allowlist가 모두 충족되어야 선택된다.

### 3.6 일반 renderer와 export

- `servers/nextjs/lib/template-v2-render-plan.mjs`
- `servers/nextjs/lib/template-v2-render-plan.d.ts`
- `servers/nextjs/lib/template-v2-general-renderer.mjs`
- `servers/nextjs/lib/template-v2-general-renderer.d.ts`
- `servers/nextjs/app/(export)/pdf-maker/TemplateV2GeneralSlide.tsx`
- `servers/nextjs/app/(export)/pdf-maker/PdfMakerPage.tsx`
- `servers/nextjs/app/(export)/pdf-maker/page.tsx`
- `servers/nextjs/lib/run-bundled-presentation-export.ts`
- `servers/nextjs/scripts`가 아닌 루트 `scripts/sync-presentation-export.cjs`

근거: 11개 discriminator, flex/grid frame, vector/infographic, text-list/table/chart/image/container 의미를 render plan과 renderer로 분리했다. image fit/flip/opacity/focus/cropScale/safe clip, text/container stroke·shadow·alignment, chart axis/title/grid/legend/source/data label과 positive/negative stack을 보존한다. SVG에 묻혀 편집 불가능해지던 chart label은 위치 지정 HTML text로 내보낸다. 손실 없이 표현할 수 없는 의미는 fail-closed한다.

Windows의 `unzip` 의존 문제를 피하는 기존 OS-aware export sync 구현과 `presentation-export` v0.4.2는 유지했다.

### 3.7 authored-hybrid 안전 불변 조건

- `servers/nextjs/lib/authored-hybrid/export.ts`
- `servers/nextjs/lib/export-render-url.ts`
- `servers/nextjs/lib/export-render-url.test.mjs`
- `servers/nextjs/lib/authored-hybrid/security.test.mjs`
- `servers/nextjs/lib/export-process-supervisor.ts`
- `servers/nextjs/lib/presentation-snapshot-integrity.ts`

근거: editable element, 최소 9pt, 가로·세로선 직선 보정, 텍스트 잘림 방지, secret-free render URL, 제한된 인증 전달, 경로 정규화/traversal 차단, format 제한, snapshot 무결성, 중복 실행 방지를 유지·검증했다.

### 3.8 Konva Studio P0/MVP

- `servers/nextjs/app/template-v2-studio/[templateId]/page.tsx`
- `servers/nextjs/app/template-v2-studio/[templateId]/TemplateV2StudioLoader.tsx`
- `servers/nextjs/app/template-v2-studio/[templateId]/TemplateV2Canvas.tsx`
- `servers/nextjs/lib/template-v2-konva.ts`
- `servers/nextjs/lib/template-v2-studio.ts`
- `servers/nextjs/lib/template-v2-studio-commands.ts`
- `servers/nextjs/lib/template-v2-studio-ui.ts`

근거: full 49K LOC editor 이식 대신 현재 contract에 맞는 bounded P0/MVP를 구현했다. modifier 동일 부모 multi-select, multi-node Transformer의 atomic geometry batch, reorder, rotated AABB 기반 group/ungroup, session-only path lock, 40개 undo/redo history, UI command facade를 포함한다.

초기 감사에서 발견된 ancestor lock 우회와 history lock 소실은 effective ancestor/subtree lock 판정, 구조 변경 path remap, layout history와 분리된 session lock history로 수정했다. 후속 재감사에서 발견된 UI exact-path 잠금도 Canvas와 toolbar 양쪽의 양방향 path overlap 판정으로 닫았다.

Konva editor가 최초 Phase 1에서 제외된 이유는 원 요청의 “이번 Phase 1에서 하지 않을 것”에 49K LOC 전체 이식과 autosave/Undo·Redo 전체 이식이 명시되어 있었기 때문이다. 기존 authored-hybrid/export 경계를 먼저 고정하지 않은 상태에서 전체 editor를 동시에 들이면 회귀 원인과 데이터 계약을 분리하기 어렵다. 이번 후속 순서에서는 그 경계를 유지한 채 P0/MVP를 실제 구현했다.

### 3.9 Visual regression CI

- `.github/workflows/template-v2-export-fidelity.yml`
- `servers/nextjs/lib/export-fidelity/`

근거: Linux/Windows matrix에서 실제 export를 만들고, Linux에는 LibreOffice/Poppler/fonts를 설치하며 Windows에는 도구 discovery를 둔다. `REQUIRE_TEMPLATE_V2_VISUAL=1`이면 도구나 비교 결과가 없을 때 성공으로 오인하지 않고 실패한다. workflow paths에는 renderer, render plan, schema/model/service/API/migration/fixture 변경을 포함한다.

### 3.10 Canary

- `servers/fastapi/scripts/check_template_v2_canary.py`
- `docs/template-v2-canary-runbook.md`

근거: `ENABLE_TEMPLATE_V2=true`와 명시적 `TEMPLATE_V2_TEMPLATE_ALLOWLIST`가 함께 있어야 준비 상태가 된다. wildcard, 빈 항목, 중복, control character, 과도한 크기는 invalid로 fail-closed한다. 배포나 원격 설정 변경은 수행하지 않았다.

### 3.11 Tests와 package metadata

- `servers/fastapi/tests/unit/test_templates_v2_*.py`
- `servers/fastapi/tests/unit/test_structured_templates_api.py`
- `servers/fastapi/tests/unit/test_presentation_template_v2_ui.py`
- `servers/fastapi/tests/unit/test_template_v2_policy.py`
- `servers/fastapi/tests/unit/test_template_v2_pptx_*.py`
- `servers/fastapi/tests/unit/test_migrations.py`
- `servers/nextjs/lib/*.test.ts`
- `servers/nextjs/lib/*.test.mjs`
- `servers/nextjs/app/api/export-presentation/route.test.ts`
- `servers/nextjs/package.json`
- `servers/nextjs/package-lock.json`

근거: upstream에서 참고 대상으로 제시된 schema/elements/generation/API/UI/default/async 관점을 현재 구조에 맞춰 선택 이식하고, renderer semantics, export boundary, Studio command, lock/history, PPTX relationship security, canary를 회귀 테스트로 고정했다.

`servers/nextjs/pnpm-lock.yaml`과 `servers/nextjs/pnpm-workspace.yaml`은 구현 워크트리와 기준 워크트리에 모두 존재하는 기존 미추적 파일로 보존했으며, 이번 완료 작업의 산출물로 간주하지 않는다.

## 4. Upstream 선택 이식과 의도적 제외

선택 이식:

- Template V2 element/schema/generation의 핵심 계약
- template CRUD/revision/import 상태의 데이터 모델 아이디어
- 일반 export에 필요한 render 의미와 UI route의 최소 골격
- upstream 테스트가 검증하는 schema/elements/generation/API/UI 관점

현재 로컬 구조에 맞게 재작성:

- `/api/v1` router와 현재 SQLAlchemy/Alembic 계보
- authored-hybrid와 공존하는 export dispatcher
- Windows/Linux 모두 동작하는 export sync/visual verification
- 로컬 auth, path, SQLite, packaged Electron/Sharp 불변 조건

의도적 제외:

- merge-base 없는 전체 merge 또는 wholesale cherry-pick
- 49K LOC Konva editor 전체와 autosave/Undo·Redo 전체 이식
- PPTX vision/OCR/external model 전체 파이프라인
- 기존 presentation의 자동 Template V2 변환
- `/api/v2`
- upstream 일반 export로 authored-hybrid 교체
- `presentation-export` v0.4.2 변경
- 외부 배포, 서명, R2 upload

## 5. 실행한 검증과 정확한 결과

### FastAPI

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

- 690 passed
- 1 skipped
- 0 failed
- 60 warnings
- 50.09초

```powershell
.\.venv\Scripts\alembic.exe heads
```

- `e8f9a0b1c2d3 (head)` 한 개

### Next/Node

전체 Node test를 실제 export 서버 충돌을 피하도록 직렬 실행:

```powershell
$tests = Get-ChildItem -Recurse -File -Include *.test.ts,*.test.mjs
node --experimental-strip-types --test --test-concurrency=1 $tests
```

- 193 tests
- 192 passed
- 0 failed
- 1 skipped
- 112752.5526ms

유일한 skip은 로컬 Windows 환경에 LibreOffice/Poppler가 없어 pixel visual comparison을 수행하지 못한 항목이다. `REQUIRE_TEMPLATE_V2_VISUAL=1`인 CI에서는 이 조건을 skip하지 않고 fail-closed한다.

첫 병렬 실행에서는 192개 중 2개가 실패했다.

- Chrome cleanup `EBUSY`: 단독 실행 2/2 통과하여 병렬 자원 경합으로 분류
- 실제 PPTX XML에서 `Quarter` 누락: renderer 회귀로 분류해 chart text overlay로 수정

수정 후 실제 API `/api/export-presentation -> /pdf-maker -> presentation-export v0.4.2` E2E와 위 전체 직렬 회귀가 통과했다.

```powershell
npm run build
```

- 성공
- TypeScript 검사 성공
- 28개 static page 생성
- `/template-v2-studio/[templateId]` route 포함

```powershell
npm run lint
```

- 0 errors
- 216 warnings

집중 renderer ESLint는 0 errors, 0 warnings이다.

```powershell
npm run test:presentation-export-sync
```

- 16/16 passed
- v0.4.2와 Windows converter 확인

```powershell
node --test lib/template-v2-general-renderer.test.mjs lib/template-v2-render-plan.test.mjs
```

- 18/18 passed

Studio 집중 테스트는 최종 28/28 통과했고, 조상 잠금과 잠긴 자손 포함 부모를 검증하는 Cypress component는 4/4 통과했다.

### Canary readiness

```powershell
$env:ENABLE_TEMPLATE_V2='true'
$env:TEMPLATE_V2_TEMPLATE_ALLOWLIST='template-alpha'
python scripts/check_template_v2_canary.py
```

결과:

```json
{"allowlisted_template_count":1,"code":"template_v2_canary_ready","configuration_valid":true,"feature_enabled":true,"ready":true}
```

### Repository hygiene

```powershell
git diff --check
```

- exit 0
- 내용 오류 없음
- Windows checkout의 LF→CRLF 경고만 출력

의존성 복구 과정에서 `npm install`은 30개 vulnerability(16 moderate, 14 high)를 보고했다. 자동 `audit fix`는 호환성 위험 때문에 수행하지 않았다.

## 6. 남아 있는 회귀 위험

1. Full Konva editor가 아니다. table/chart/asset/text-run 세부 편집, 전체 autosave journal, 협업 충돌 UI는 후속 범위다.
2. Cypress는 조상·자손 잠금에 따른 toolbar 결과를 검증하지만 Konva node의 `draggable=false`와 Transformer detach 속성 자체를 직접 assertion하지 않는다.
3. PPTX 구조화는 OOXML evidence V0이다. 이미지 기반 chart/diagram 해석, OCR, vision model, confidence/fallback pipeline은 아직 없다.
4. 로컬 Windows에서 pixel visual diff는 도구 부재로 skip되었다. workflow는 작성했지만 commit/push하지 않았으므로 원격 Linux/Windows CI 실행 결과는 아직 없다.
5. 실사용 복합 PPTX corpus에서 exotic relationship, SmartArt, embedded object의 기대 fallback을 더 검증해야 한다.
6. canary는 준비 상태만 검증했다. 외부 배포와 telemetry 관측은 수행하지 않았다.
7. 전체 lint는 오류가 없지만 기존 경고를 포함해 216개가 남아 있다.
8. dirty/untracked 상태라 release provenance 확정은 commit 전까지 보류된다.
9. npm audit의 30개 vulnerability는 별도 의존성 검토가 필요하다.

## 7. 다음 권장 순서

1. 별도 승인 후 commit/push하여 Linux/Windows export fidelity workflow를 실제 실행하고 두 OS green을 확인한다.
2. 대표 실사용 PPTX corpus를 추가해 OOXML V0의 구조 증거와 actual export 결과를 비교한다.
3. OCR/vision을 비동기 stage로 추가하되 confidence, timeout, resource cap, authored fallback을 명시한다.
4. Konva Studio에 table/chart/asset/text-run 세부 편집과 autosave journal을 추가한다.
5. 단일 또는 소수 template allowlist canary를 운영하고 export 성공률, fallback률, fidelity diff, 편집 실패율을 관측한 뒤 allowlist를 단계적으로 확대한다.

## 8. 최종 Git 상태

### 기준 워크트리

- 경로: `C:\project\PPT-agent\ppt-agent`
- 브랜치: `main`
- HEAD: `78bf5f102224f8dfd80d12cd9c64767936ad567e`
- 상태: modified 2개, untracked 10개
- 기존 modified/generated log/runtime/untracked 파일이 남아 있으며 되돌리거나 삭제하지 않았다.

### 구현 워크트리

- 경로: `C:\project\PPT-agent\pptx-template-studio`
- 브랜치: `feat/pptx-template-studio`
- HEAD: `78bf5f102224f8dfd80d12cd9c64767936ad567e`
- 상태: modified 28개, untracked 59개
- Template V2 구현 변경과 테스트·문서·workflow가 modified/untracked 상태로 남아 있다.

### Upstream 검증 워크트리

- 경로: `C:\project\PPT-agent\upstream-realignment`
- 브랜치: `integration/upstream-realignment-20260724`
- HEAD: `57b194b234b42c8b28f8a507a30322de200e3e83`
- 상태: modified 0개, untracked 1개
- 검증 보고서 `docs/upstream-realignment-20260724.md`만 미추적 상태이며 보존했다.

커밋과 push는 요청대로 수행하지 않았다.
