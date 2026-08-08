# Template V2 운영 카나리 리허설 프롬프트

최종 갱신: 2026-07-26

이 문서는 실제 관리형 PostgreSQL 환경에서 Template V2의 활성화, 상태 확인, 롤백을 재현하기 위한 실행 체크리스트다. 로컬 SQLite 통과 결과를 운영 카나리 성공으로 간주하지 않는다.

## 사전 조건

- 배포 티어: `TEMPLATE_V2_DEPLOYMENT_TIER=production`
- 기능 플래그: `ENABLE_TEMPLATE_V2=true`
- 허용 목록: 실제 카나리 템플릿 ID만 `TEMPLATE_V2_TEMPLATE_ALLOWLIST`에 지정
- 관리형 PostgreSQL 연결 문자열과 파일 저장소를 카나리 인스턴스에 설정
- 기존 authored/adaptive 경로의 기준 요청·응답과 복구 담당자를 확보

## 실행 순서

1. 카나리 인스턴스 시작 전 `uv run python scripts/check_template_v2_canary.py`를 실행해 DB 연결, 정확한 Alembic head, 비공개 저장소, 운영 상태의 누락을 숨기지 않고 실패하는지 확인한다. Template V2는 공개 `/health` 엔드포인트를 추가하지 않는다.
2. 동일 명령의 `template_v2_canary_ready`와 exit 0을 증거로 보관한다.
3. `uv run python scripts/check_template_v2_operations.py`를 실행해 큐의 `stale`, `failed`, `review_required`, `cleanup_due` 수치를 기록한다.
4. 허용된 템플릿과 허용되지 않은 템플릿으로 `/api/v1/ppt/structured-templates` 목록·읽기·쓰기·가져오기 경계를 검증한다.
5. 실제 PPTX를 가져와 분석, 검토, 저장, 재개, 내보내기까지 수행한다.
6. 동일 인스턴스에서 기존 authored/adaptive 생성 경로를 재검증한다.
7. `template_v2_rollout`, `template_v2_pptx_queue` 로그와 작업 상태를 저장한다.

## 롤백 리허설

1. `ENABLE_TEMPLATE_V2=false`로 전환하고 인스턴스를 재시작한다.
2. 신규 discovery/write/import/dispatcher가 차단되는지 확인한다.
3. 플래그가 꺼진 상태에서도 만료 작업 정리와 drain 관측이 계속되는지 확인한다.
4. 기존 Template V2 프레젠테이션의 읽기·내보내기와 기존 authored/adaptive 경로가 유지되는지 확인한다.
5. 데이터 삭제나 암묵적 스키마 변경이 없음을 확인한다.

## GO 판정 기준

- 관리형 PostgreSQL에서 시작·readiness·마이그레이션 안전 가드가 모두 통과
- 허용 목록이 fail-closed로 동작
- 큐 우선순위가 `stale > failed > review_required > cleanup_due`로 보고됨
- 실제 PPTX 왕복과 기존 경로 회귀 테스트 통과
- 플래그 OFF 롤백 후 데이터 손실 없음

하나라도 증거가 없으면 운영 확대는 NO-GO다.
