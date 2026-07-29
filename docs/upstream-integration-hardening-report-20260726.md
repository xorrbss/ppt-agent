# 업스트림 통합 진행상황 및 하드닝 리포트

평가일: 2026-07-26
대상 브랜치: `codex/upstream-integration-hardening`
기준 커밋: `a9356713720ac47ea7a56969e980fa8ee09febe9`

## 종합 판정

현재 점수는 **93/100**이다. 최초 분석 점수 84점에서 9점 상승했다.

코드 통합과 로컬 회귀 검증은 GO 수준이다. 다만 실제 관리형 PostgreSQL 카나리, 새 export-runtime 릴리스, 플랫폼 서명·배포 자격 증명이 확보되기 전까지 운영 전면 확대는 조건부 NO-GO다.

| 평가 영역 | 점수 | 판정 |
|---|---:|---|
| 업스트림 호환성 | 20/20 | 462개 계약 검증 통과 |
| 기능 완성도 | 18/20 | 보수적 runtime 분류 완료, 의미 기반 Phase B 잔여 |
| 테스트·CI | 19/20 | 경고 오류 모드와 실제 API 시각 검증 통과 |
| 보안·의존성 | 14/15 | production audit 0, export-runtime의 0.34.4 ABI 제약 존재 |
| 운영 안전성 | 14/15 | fail-closed·롤백·큐 관측 구현, 실 PostgreSQL 증거 미확보 |
| 유지보수성 | 8/10 | 대형 파일 축소, 일부 파일은 여전히 500줄 초과 |

## 반영 완료

- 업스트림 호환성 게이트: 426 → 462 checks
- FastAPI: 927 passed, 5 skipped, 경고 0
- Next.js: ESLint 216 warnings → 0, TypeScript 오류 4개 → 0
- 상시 CI에 독립 TypeScript 검사 추가
- 실제 `/pdf-maker` 및 `presentation-export v0.4.2` 경로 시각 충실도 12/12 통과
- root, Next.js, Electron production dependency audit 0건
- Template V2 runtime 가져오기 보수적 분류, 기본 콘텐츠, 복구, legacy/malformed 호환 처리
- production/staging SQLite 시작 차단 및 스키마 변경 전 안전 가드
- 플래그 OFF 상태 TTL 정리·drain 관측과 롤백 상태 CLI
- 큐 상태 우선순위와 `review_required` degraded 판정
- Studio 1,189 → 652줄, ingestion 1,474 → 869줄로 축소
- 반복 API 렌더 루프, 잘못된 Python alias, lifespan 가드 순서, Sharp ABI 회귀 수정

## 현재 문제와 개선점

### P0: 운영 카나리 증거 부족

관리형 PostgreSQL에서 실제 시작, readiness, 작업 lease, 정리, 롤백을 실행한 증거가 없다. 로컬 SQLite 검증만으로 운영 준비 완료를 선언하면 안 된다.

### P1: PPTX 의미 분류의 보수성

현재 분류기는 title/subtitle/body/placeholder와 picture/image/photo 같은 강한 이름만 편집 가능 슬롯으로 승격한다. 일반 도형·텍스트·표·차트·벡터는 안전하게 decorative로 남는다. OOXML `p:ph`, master/layout 관계, 지역화된 이름과 사용자 변경 이름을 결합한 Phase B가 필요하다.

### P1: export-runtime Sharp 제약

앱과 LiteParse는 Sharp 0.35.3을 사용해 production audit 0을 유지한다. 하지만 `presentation-export v0.4.2`는 Sharp 0.34.4 JS ABI를 번들링하므로 런타임 내부에 0.34.4 네이티브 모듈을 격리했다. 동작은 검증됐지만, 최종 해소는 Sharp 0.35 이상으로 재빌드된 업스트림 export-runtime 교체다.

### P2: 개발 도구 transitive 취약점

Electron 전체 audit에는 electron-builder 계열의 transitive `minimatch`/`brace-expansion` 경고가 남는다. `npm audit fix --force`가 제안하는 다운그레이드는 빌드 호환성을 해치므로 적용하지 않았다. 안전한 상위 릴리스 추적이 필요하다.

### P2: 남은 대형 파일

분리 후에도 `TemplateV2Studio.tsx` 652줄, `template_v2_pptx_ingestion_service.py` 869줄이다. 이벤트 조정·상태 전이·포맷별 분석을 더 작은 모듈로 분리할 수 있다.

## 남은 작업

1. 관리형 PostgreSQL 카나리와 플래그 OFF 롤백 리허설
2. OOXML·master/layout·vision/manual review를 결합한 의미 기반 슬롯 분류
3. Sharp 0.35+ 기반 `presentation-export` 업스트림 릴리스 채택
4. Electron 개발 의존성 보안 경고의 안전한 업그레이드
5. 남은 500줄 초과 파일 분리
6. Windows 서명, AppX identity, R2 자격 증명, 서명 산출물 검증
7. Linux/macOS 실제 패키징 및 시각 회귀 검증

## 검증 명령

```text
node scripts/verify-upstream-compatibility.mjs
cd servers/fastapi
uv run pytest -q --no-header -p no:cacheprovider -W error
cd ../nextjs
npm run lint
npx tsc --noEmit -p tsconfig.json
npm run test:ci-node
npm run build
npm run test:template-v2-export-fidelity
cd ../../electron
npm run typecheck
npm run test:package-preflight
node sync_export_runtime.js --check-only
```
