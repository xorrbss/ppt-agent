# Authored vNext durable export 운영 런북

## 적용 범위

이 경로는 `DeckMasterContract v1`과 `SlideSpec v2`로 봉인된 master/deck을
Next.js 내부 OOXML worker에서 PPTX로 materialize하고, FastAPI가 검증된
artifact receipt를 SQL publication pointer에 원자적으로 게시하는 경로다.
기존 generation pipeline과 authored-hybrid exporter를 대체하지 않는다.

## 필수 설정

- `AUTHORED_VNEXT_MASTER_ENABLED=true`
- `AUTHORED_VNEXT_EXPORT_ENABLED=true`
- `MIGRATE_DATABASE_ON_STARTUP=true`
- `NEXT_INTERNAL_URL`: FastAPI에서 접근할 Next.js 내부 주소
- `AUTHORED_VNEXT_OOXML_WORKER_TOKEN`: FastAPI와 Next.js에 동일하게 주입하는
  최소 32자의 전용 secret. 브라우저나 공개 응답에 노출하면 안 된다.
- `APP_DATA_DIRECTORY`: content-addressed PPTX artifact root의 상위 디렉터리

production에서는 SQL repository만 허용된다. 파일 repository는
`AUTHORED_VNEXT_REPOSITORY_MODE=file`과 명시적인 dev/test 환경이 함께 설정된
경우에만 로컬 fixture로 사용할 수 있고, export publication의 권위 저장소가
될 수 없다.

## 상태와 복구

1. job 생성은 owner scope와 idempotency key로 중복 제거한다. 같은 key에 다른
   request digest를 보내면 충돌로 종료한다.
2. worker는 claim 때 증가한 operation fence를 받는다. lease를 잃은 이전
   worker의 완료 응답은 게시하지 않는다.
3. worker 응답은 base64를 전부 decode한 뒤 artifact digest/길이를 확인한다.
   content-addressed 임시 파일을 fsync하고 다시 읽어 동일성을 확인한 후에만
   receipt를 만든다.
4. receipt insert, job 성공, active artifact pointer CAS는 한 DB transaction이다.
   stale revision/hash, 취소, timeout, digest mismatch, worker 실패에서는 기존
   active pointer가 그대로 유지된다.
5. retry/resume은 새 fence를 획득한다. `maxAttempts`를 넘기지 않으며 source
   master/deck/spec/asset/font/package digest를 매번 다시 확인한다.
6. rollback은 과거 immutable receipt를 새 publication revision으로 다시
   가리킨다. artifact bytes를 복사하거나 이전 receipt를 수정하지 않는다.

## 장애 확인

- `authored_vnext_worker_timeout`, `*_unavailable`: Next.js 상태와 내부 URL을
  확인한 뒤 retry한다. active artifact는 정상이라면 변하지 않는다.
- `*_digest_mismatch`: 재시도 전에 master/deck/font/asset source가 요청 이후
  변경되었는지 확인한다. mismatch를 무시하거나 hash를 재기록하지 않는다.
- `*_fence_conflict`, `*_publication_conflict`: 다른 process가 먼저 완료한
  정상적인 CAS 경쟁일 수 있다. 현재 job과 active receipt를 다시 조회한다.
- download read-back 실패: artifact storage의 손상으로 취급하고 배포를
  중단한다. DB pointer를 임의로 새 파일에 맞추지 말고 검증된 receipt로
  rollback한다.

## 검증 명령

전체 suite 대신 변경 경계의 focused test를 실행한다.

```powershell
cd servers/fastapi
uv run pytest tests/integration/test_authored_vnext_export_integration.py
uv run pytest tests/unit/test_authored_vnext_sql_repository.py

cd ../nextjs
node --test app/api/internal/ooxml-vnext/*.test.mjs
```

마이그레이션 검증은 임시 DB에서 upgrade 후 downgrade하고, 기존 운영 DB나
`app_data` 복사본을 테스트 대상으로 사용하지 않는다.
