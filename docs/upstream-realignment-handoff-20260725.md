# 업스트림 통합 하드닝 인수인계

최종 갱신: 2026-07-26

## 작업 기준

- 작업 폴더: `C:\project\PPT-agent\pptx-template-studio`
- 브랜치: `codex/upstream-integration-hardening`
- 기준 커밋: `a9356713720ac47ea7a56969e980fa8ee09febe9`
- 업스트림 호환성 기준: `57b194b234b42c8b28f8a507a30322de200e3e83`
- 사용자가 명시하기 전에는 commit, push, PR, merge를 수행하지 않는다.

## 완료된 하드닝

- 업스트림 호환성 검증을 426개에서 462개로 확대
- FastAPI 전체 테스트를 경고 오류 모드에서 통과하도록 deprecation 및 리소스 누수 제거
- Next.js ESLint 경고 216개와 독립 TypeScript 오류 4개 제거
- TypeScript 검사를 상시 CI 게이트에 추가
- Template V2 런타임 가져오기 보수적 분류기와 복구·호환 테스트 추가
- 운영 큐 상태·정리·롤백 점검 서비스 및 CLI 추가
- production/staging의 SQLite 구성을 시작 전에 fail-closed 처리
- Template V2 Studio와 PPTX ingestion god-file 분리
- `presentation-export v0.4.2`의 Sharp 0.34.4 ABI를 앱 Sharp 0.35.3과 격리하고 런타임 로컬 버전을 검증

## 최종 검증 결과

- `node scripts/verify-upstream-compatibility.mjs`: 462 checks 통과
- `uv run pytest -q --no-header -p no:cacheprovider -W error`: 927 passed, 5 skipped, warnings 0
- Next.js lint/typecheck/build: 통과
- Next.js CI Node 테스트: adaptive 19, URL/data 9, authored hybrid 83, theme 1, Template V2 118 통과
- Template V2 실제 API 시각 충실도: 12/12 통과
- export-runtime 동기화 계약: 16/16 통과
- Electron 패키징 preflight: 23/23 통과
- root, Next.js, Electron production audit: 0건

## 다음 작업

1. 실제 관리형 PostgreSQL에서 카나리와 플래그 OFF 롤백 리허설을 실행한다.
2. OOXML placeholder 메타데이터, 지역화된 이름, 사용자 변경 이름을 포함하는 Phase B 의미 분류를 구현한다.
3. `presentation-export`가 Sharp 0.35 이상으로 재빌드된 업스트림 릴리스로 교체한다.
4. Electron 개발 도구 체인의 transitive 보안 경고는 안전한 상위 릴리스가 나온 뒤 갱신한다.
5. 500줄을 넘는 남은 Studio/ingestion 파일을 추가 분리한다.
6. Windows 서명 인증서, AppX identity, R2 자격 증명과 서명 산출물로 배포 게이트를 검증한다.

자세한 점수와 근거는 `docs/upstream-integration-hardening-report-20260726.md`를 참조한다.
