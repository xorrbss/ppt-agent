# Template V2 Phase 1 구현·검증 보고서

작성일: 2026-07-24
구현 브랜치: `feat/pptx-template-studio`
upstream 기준: `presenton/presenton` `57b194b234b42c8b28f8a507a30322de200e3e83`

## 결론과 점수

Phase 1 범위의 구현 품질은 **91/100**, 전체 Template V2 제품 완성도는 **72/100**으로 평가한다.

| 평가 항목 | 점수 | 판단 |
| --- | ---: | --- |
| 계약·전략 경계 | 19/20 | Template V2와 authored HTML의 생성·편집·내보내기 경계를 fail-closed 방식으로 분리했다. |
| DB·migration | 19/20 | 기존 `f3a4b5c6d7e8` 뒤에 additive revision을 추가했고 SQLite와 PostgreSQL 왕복을 검증했다. |
| API·보안 | 18/20 | `/api/v1` CRUD, 기능 플래그, 인증 경계, 무결성 오류 분류와 안전한 export proxy를 구현했다. |
| export·회귀 안정성 | 20/20 | v0.4.2 general/authored-hybrid E2E와 기존 불변 조건 회귀를 통과했다. |
| 편집기 준비도 | 8/12 | 최소 Konva 수직 슬라이스는 동작하지만 첫 layout/component와 제한된 요소만 지원한다. |
| 운영·의존성 | 7/8 | Windows exporter/Sharp/Electron 검증을 통과했으나 기존 npm 의존성 취약점과 lint 경고가 남아 있다. |

91점은 이번 Phase 1 계약과 안전성의 품질 점수다. 72점은 전체 제품 기준 점수이며, 완전한 Konva Studio와 PPTX vision 구조화 파이프라인이 아직 범위 밖이므로 더 낮다.

## 구현 내용과 근거

### 핵심 model/schema/generation

- `servers/fastapi/templates/v2/models/`: 위치·크기·텍스트·이미지·컨테이너·flex·group·table·chart·infographic 및 layout 모델을 현재 Pydantic 구조에 맞게 추가했다.
- `servers/fastapi/templates/v2/schema.py`, `generation.py`: 구조화 템플릿의 JSON schema와 생성 계약을 추가했다.
- `servers/fastapi/template_v2_schema_contract.py`: DB/API/migration에서 공유하는 버전·검증 계약을 정의했다. PostgreSQL의 `FOR UPDATE`와 충돌하던 streaming cursor 옵션은 제거했다.
- `servers/fastapi/templates/v2/policy.py`: `ENABLE_TEMPLATE_V2`가 정확히 `true`인 경우에만 쓰기 기능이 열리도록 기본 OFF 정책을 추가했다.
- `servers/fastapi/templates/v2/strategies.py`: 다음 경계를 명시했다.
  - `GenerationStrategy`: `template-v2`, `authored-html`
  - `EditorCapability`: `template-v2`, `authored-html`
  - `ExportStrategy`: `template-v2-general`, `authored-hybrid`
- `servers/fastapi/tests/fixtures/template_v2/strategy-parity.json`: Python과 TypeScript가 동일한 전략 판정 사례를 사용하도록 고정했다.

### DB와 migration

- `servers/fastapi/models/sql/template_v2.py`: 구조화 템플릿 저장 모델을 추가했다.
- `servers/fastapi/models/sql/presentation.py`, `slide.py`: 기존 presentation/slide에 additive Template V2 identity와 native UI payload를 추가했다.
- `servers/fastapi/alembic/versions/a4b5c6d7e8f9_add_template_v2_phase_one.py`:
  - 실제 계보를 재확인하고 `down_revision = "f3a4b5c6d7e8"`로 추가했다.
  - migration 실행이 애플리케이션 런타임 코드에 의존하지 않도록 계약을 revision 안에 동결했다.
  - downgrade 전에 Template V2 데이터, native UI payload, 비-legacy schema version을 모두 검사하여 데이터 손실을 거부한다.
- `servers/fastapi/alembic/env.py`, `migrations.py`, `services/database.py`: SQLite와 PostgreSQL에서 동일한 additive schema가 적용되도록 연결했다.

### `/api/v1` CRUD

- `servers/fastapi/api/v1/ppt/endpoints/structured_templates.py`: `/api/v1/ppt/structured-templates` create/list/get/update/delete를 추가했다.
- `servers/fastapi/api/v1/ppt/router.py`: 기존 v1 router에만 등록했다. API v2는 만들지 않았다.
- draft의 `layouts = NULL`은 읽을 수 있지만, non-null layouts는 항상 검증한다.
- create/update/delete의 무결성 오류는 rollback 후 duplicate 409, source missing 404, 기타 충돌 409로 안정적으로 분류한다.
- 손상된 저장 데이터는 임의 보정하지 않고 409로 fail-closed 처리한다.

### export 경계

- `servers/nextjs/lib/presentation-export-strategy.ts`, `presentation-export-boundary.ts`: persisted identity를 기준으로 general과 authored-hybrid 실행기를 분리했다.
- `servers/nextjs/app/api/export-presentation/route.ts`, `lib/export-presentation-route.ts`: malformed JSON, 크기 제한, timeout, 상태 매핑, 내부 응답 본문 비노출, presentation ID 인코딩을 보강했다.
- Template V2는 general exporter만 사용하고 authored HTML은 명시적 hybrid일 때만 기존 authored-hybrid를 사용한다.
- `scripts/sync-presentation-export.cjs`: Windows에서 `npm.cmd`/`unzip`에 기대지 않고 Node와 npm CLI를 사용하는 OS-aware 동기화를 유지했다.
- `presentation-export` v0.4.2와 packaged Sharp를 그대로 사용했다.

### 최소 Konva Studio

- `servers/nextjs/app/template-v2-studio/[templateId]/`: 동적 route와 client-only Konva canvas를 추가했다.
- `servers/nextjs/lib/template-v2-studio.ts`: immutable reducer와 손실 없는 부분 갱신을 구현했다.
- `konva 10.3.0`, `react-konva 19.2.4`를 exact dependency로 추가했다.
- `NEXT_PUBLIC_TEMPLATE_V2_STUDIO_ENABLED === "true"`일 때만 route가 열린다.
- 첫 layout/component 렌더링, component drag, text 편집, rectangle 추가, 명시적 PATCH 저장을 지원한다.
- 미지원 요소는 placeholder로 보여 주되 payload는 그대로 보존한다.
- autosave, Undo/Redo, resize/rotate, 다중 layout 편집은 의도적으로 포함하지 않았다.

## upstream 선택 이식과 의도적 제외

선택 이식:

- Template V2 element/layout model과 schema/generation 개념
- core structured-template API와 관련 테스트의 계약
- default template/UI identity 검증 관점
- general export를 사용하는 Template V2 전략

현재 로컬 구조에 맞게 다시 작성하거나 강화:

- 모든 endpoint를 `/api/v1` 아래에 배치
- 로컬 Alembic 계보와 데이터 손실 방지 downgrade
- authored/adaptive/authored-hybrid identity를 보존하는 전략 판정
- Windows OS-aware exporter sync와 packaged Sharp 검증
- backend body와 내부 URL을 노출하지 않는 bounded export proxy

의도적으로 제외:

- upstream 전체 merge 및 wholesale cherry-pick
- 49K LOC 규모 Konva editor 전체
- PPTX vision 구조화 파이프라인 전체
- 기존 presentation/template 자동 변환
- Template V2 기본 활성화와 API v2
- autosave/Undo·Redo 전체, 외부 배포·서명·R2 업로드
- authored-hybrid를 upstream general export로 교체하는 변경

전체 Konva editor를 제외한 이유는 최초 Phase 1 범위에서 명시적으로 제외됐고, 한 번에 이식하면 authored/adaptive 경계와 export 불변 조건을 검증하기 어렵기 때문이다. 대신 이번 작업에서는 schema/API를 실제 편집 UI까지 연결하는 최소 수직 슬라이스를 추가해 Phase 2의 기술 위험을 줄였다.

## 검증 결과

### FastAPI

```text
.\.venv\Scripts\python.exe -m pytest tests -q
617 passed, 1 skipped, 60 warnings in 23.28s
```

upstream Windows 기준선의 7개 알려진 실패는 재현되지 않았고 신규 회귀도 없었다.

```text
.\.venv\Scripts\python.exe -m pytest tests/unit/test_migrations.py -q
46 passed

Template V2 신규 unit/API/migration 묶음
138 passed, 6 warnings

tests/unit/test_structured_templates_api.py 최종 좁은 검증
22 passed
```

PostgreSQL 15 임시 cluster에서 기존 DB의 `head -> f3a4b5c6d7e8 -> head` 왕복과 빈 DB의 전체 `upgrade head`를 모두 통과했다. 임시 DB와 서버는 종료했다.

### Next.js와 exporter

```text
node --experimental-strip-types --test <repository test files>
136 tests, 136 passed, 0 failed

npm run build
success; /template-v2-studio/[templateId] dynamic route 포함

npx tsc --noEmit --pretty false
exit 0

npm run lint
0 errors, 216 warnings
```

변경 파일만 대상으로 한 ESLint는 경고 없이 통과했다. 전체 216개 경고는 현재 로컬 트리의 기존 hook/img/unused 경고를 포함하며 이번 변경의 오류는 아니다.

```text
npm run check:presentation-export
passed

npm run test:presentation-export-sync
16 passed

v0.4.2 authored-hybrid representative E2E
passed

v0.4.2 Template V2 general export E2E
passed
```

### Electron/Windows packaging 경계

```text
npm run typecheck
passed

package preflight tests
23 passed

build-config tests
1 passed

standalone-copy tests
11 passed
```

Windows exporter binary, packaged Sharp, standalone copy 계약을 확인했다.

## 남아 있는 문제와 개선점

1. Studio는 첫 layout/component만 다루며 zoom, resize, rotate, 다중 선택, 다중 layout/component 탐색이 없다.
2. 여러 style run을 가진 text를 편집하면 문자열이 첫 run으로 합쳐지므로 스타일 구간 의미를 보존하는 편집 모델이 필요하다.
3. Studio GET/PATCH는 reducer와 API를 각각 검증했지만 브라우저 컴포넌트 수준의 mocked 통합 테스트가 아직 없다.
4. public 환경 변수 기반 UI flag는 build-time 값이므로 활성화 절차에 backend `ENABLE_TEMPLATE_V2`와 사용자 allowlist를 함께 명시해야 한다.
5. npm 설치가 보고한 기존 dependency 취약점은 기능 작업과 분리해 lockfile 영향과 호환성을 검토한 뒤 갱신해야 한다. 자동 `audit fix`는 수행하지 않았다.
6. 전체 lint는 오류 0이지만 로컬 기준 216개 경고가 남아 있다. upstream 104 경고와 직접 비교하려면 동일 SHA·동일 dependency 환경의 별도 기준 실행이 필요하다.
7. Python Ruff가 환경에 설치되어 있지 않아 실행하지 못했다. pytest와 TypeScript/ESLint/build 검증은 완료했다.
8. PostgreSQL 테스트 서버는 중지했으나 도구의 삭제 정책이 임시 cluster 디렉터리 제거를 차단했다. 제품 워크트리 밖의 중지된 테스트 산출물이다.

## Phase 2 권장 순서

1. Studio에 layout/component navigator, zoom/pan, selection/transformer, resize/rotate와 좌표 정규화를 추가한다.
2. text run 단위 편집과 이미지/shape/group/flex/table/chart renderer를 순차 추가한다.
3. explicit save에 optimistic concurrency token을 추가한 뒤 autosave와 Undo/Redo를 도입한다.
4. 브라우저 GET/PATCH 실패·충돌·미지원 요소 round-trip 통합 테스트를 추가한다.
5. PPTX vision ingestion을 별도 비동기 pipeline으로 구현한다.
6. vision 결과를 Template V2 schema에 매핑하고 confidence/unsupported fallback을 보존한다.
7. representative PPTX corpus로 geometry, font 9pt, line correction, text clipping, editable export parity를 자동 비교한다.
8. 기능 플래그와 사용자 allowlist로 제한 배포한 뒤 telemetry 기준으로 점진 확대한다.

## 최종 Git 상태

- `C:\project\PPT-agent\ppt-agent`
  - branch: `main`
  - 기존 modified 2개, untracked 10개가 그대로 남아 있다.
- `C:\project\PPT-agent\pptx-template-studio`
  - branch: `feat/pptx-template-studio`
  - Phase 1 구현 파일이 modified/untracked 상태다. 커밋과 push는 하지 않았다.
- `C:\project\PPT-agent\upstream-realignment`
  - branch: `integration/upstream-realignment-20260724`
  - `docs/upstream-realignment-20260724.md`가 untracked 상태로 보존돼 있다.

기존 사용자 변경, 미추적 파일, 생성 파일은 되돌리거나 덮어쓰지 않았다.
